#! /usr/bin/env python
# -*- coding: utf-8 -*-
######################################################################################
# Inspired by and most of the code from:
# Dan Noguerol
# https://bitbucket.org/whizzosoftware/indigo-irrigationcaddy-plugin
# http://www.whizzosoftware.com/forums/blog/1/entry-54-indigo-irrigation-caddy-plugin/
######################################################################################

import os
import sys
import socket
import httplib
import urllib2
import indigo
import math
import decimal
import datetime
import socket
from xml.etree import ElementTree as ET
from ghpu import GitHubPluginUpdater

class Plugin(indigo.PluginBase):

    def __init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs):
        indigo.PluginBase.__init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs)
        self.updater = GitHubPluginUpdater(self)
        
        # Timeout
        self.reqTimeout = 8
        
        # Pooling
        self.pollingInterval = 2

        # Flag buttonRequest is processing
        self.reqRunning = False

        # create empty device list
        self.deviceList = {}
    
        # install authenticating opener
        self.passman = urllib2.HTTPPasswordMgrWithDefaultRealm()
        authhandler = urllib2.HTTPBasicAuthHandler(self.passman)
        opener = urllib2.build_opener(authhandler)
        urllib2.install_opener(opener)

    def __del__(self):
        indigo.PluginBase.__del__(self)     

    ###################################################################
    # Plugin
    ###################################################################

    def deviceStartComm(self, device):
        self.debugLog(device.name + ": Starting device")
        
        if device.id not in self.deviceList:
            self.deviceList[device.id] = {'ref':device,'lastTimeSensor':datetime.datetime.now()}

            if device.pluginProps.has_key("useAuthentication") and device.pluginProps["useAuthentication"]:
                self.passman.add_password(None, u"http://" + device.pluginProps["address"], device.pluginProps["username"], device.pluginProps["password"])
            self.sensorUpdateFromRequest(device)

    def deviceStopComm(self,device):
        if device.id not in self.deviceList:
            return
        self.debugLog(device.name + ": Stoping device")
        del self.deviceList[device.id]

    def startup(self):
        self.loadPluginPrefs()
        self.debugLog(u"startup called")

        self.reqRunning = False
        socket.setdefaulttimeout(self.reqTimeout)

        #self.debugLog("Pooling Interval: " + str(self.pollingInterval))    
        #self.debugLog("Request Timeout: " + str(self.reqTimeout))  
        
        self.updater.checkForUpdate()

    def shutdown(self):
        self.debugLog(u"shutdown called")

    
    def deviceCreated(self, device):
        self.debugLog(u"Created device of type \"%s\"" % device.deviceTypeId)

    def validateDeviceConfigUi(self, valuesDict, typeId, devId):
        self.debugLog(u"validating device Prefs called")    
        ipAdr = valuesDict[u'address']
        if ipAdr.count('.') != 3:
            errorMsgDict = indigo.Dict()
            errorMsgDict[u'address'] = u"This needs to be a valid IP address."
            return (False, valuesDict, errorMsgDict)
        if self.validateAddress (ipAdr) == False:
            errorMsgDict = indigo.Dict()
            errorMsgDict[u'address'] = u"This needs to be a valid IP address."
            return (False, valuesDict, errorMsgDict)    
        if (valuesDict[u'useAuthentication']):
            if not(valuesDict[u'username']>""):
                errorMsgDict = indigo.Dict()
                errorMsgDict[u'username'] = u"Must be filled."
                return (False, valuesDict, errorMsgDict)
            if not(valuesDict[u'password']>""):
                errorMsgDict = indigo.Dict()
                errorMsgDict[u'password'] = u"Must be filled."
                return (False, valuesDict, errorMsgDict)
        return (True, valuesDict)

    def validatePrefsConfigUi(self, valuesDict):    
        self.debugLog(u"validating Prefs called")       
        return (True, valuesDict)

    def closedDeviceConfigUi(self, valuesDict, userCancelled, typeId, devId):
        if userCancelled is False:
            indigo.server.log ("Device preferences were updated.")

    def closedPrefsConfigUi ( self, valuesDict, UserCancelled):
        #   If the user saves the preferences, reload the preferences
        if UserCancelled is False:
            indigo.server.log ("Preferences were updated, reloading Preferences...")
            self.loadPluginPrefs()

    def loadPluginPrefs(self):
        # set debug option
        if 'debugEnabled' in self.pluginPrefs:
            self.debug = self.pluginPrefs['debugEnabled']
        else:
            self.debug = False
        
        self.pollingInterval = 0
        self.reqTimeout = 0
            
        #if self.pluginPrefs.has_key("pollingInterval"):
        #   self.pollingInterval = int(self.pluginPrefs["pollingInterval"])                 
        #if self.pollingInterval <= 0:
        #   self.pollingInterval = 30   

        #if self.pluginPrefs.has_key("reqTimeout"):
        #   self.reqTimeout = int(self.pluginPrefs['reqTimeout'])
        #if self.reqTimeout <= 0:
        #   self.reqTimeout = 8

        self.reqTimeout = 8

    def validateAddress (self,value):
        try:
            socket.inet_aton(value)
        except socket.error:
            return False
        return True

    ###################################################################
    # Concurrent Thread
    ###################################################################

    def runConcurrentThread(self):

        self.debugLog(u"Starting polling thread")

        try:
            while True: 
            
                if self.reqRunning == False:
                    todayNow = datetime.datetime.now()
                    for deviceId in self.deviceList:
                        if deviceId in indigo.devices:
                            pollingInterval = 0
                            state           = indigo.devices[deviceId].states["RoombaState"] #self.deviceList[deviceId]['ref'].states["RoombaState"]
                            lastTimeSensor  = self.deviceList[deviceId]['lastTimeSensor']
                            if state == "clean":
                                pollingInterval = 3
                            elif state == "stop":
                                pollingInterval = 3
                            else:
                                pollingInterval = 120
                            nextTimeSensor = lastTimeSensor + datetime.timedelta(seconds=pollingInterval)
                            
                            if nextTimeSensor <= todayNow:
                                self.debugLog("Thread. Roomba State = " + state)
                                self.debugLog("Thread. Pooling interval = " + str(pollingInterval))
                                self.deviceList[deviceId]['lastTimeSensor'] = todayNow
                                if self.reqRunning == False:
                                    self.sensorUpdateFromThread(indigo.devices[deviceId])

                self.sleep(0.5)

        except self.StopThread:
            # cleanup
            pass
        self.debugLog(u"Exited polling thread") 

    def stopConcurrentThread(self):
        self.stopThread = True
        self.debugLog(u"stopConcurrentThread called")
    
    ###################################################################
    # HTTP Request against RooWIFI. 
    ###################################################################

    def sendRequest(self, device, urlAction):
        self.reqRunning = True
        requestTrial = 0
        requestMax   = 3
        requestOK    = False

        theUrl = u"http://" + device.pluginProps["address"] + urlAction
        self.debugLog("sending " + theUrl)
        while (requestTrial < requestMax) and (requestOK == False):
            try:            
                f = urllib2.urlopen(theUrl)
                requestOK = True
            except Exception, e:
                requestTrial += 1
                lastError = str(e)

        if (requestOK == False):
            self.errorLog(device.name + ": Error: " + lastError)
            self.errorLog(device.name + " did not received the request !")  
            self.reqRunning = False 
            return False

        self.sleep(1)   
        self.sensorUpdateFromRequest(device)
        self.reqRunning = False

        return True

    ###################################################################
    # Retrieve Roomba sensor status, and pass them to Indigo device.
    ###################################################################

    def sensorUpdateFromRequest (self,device):
        memoReqRunning = self.reqRunning
        self.reqRunning = True
        retValue = self.sensorUpdate(device,True)
        self.reqRunning = memoReqRunning
        return retValue

    def sensorUpdateFromThread (self,device):
        retValue = self.sensorUpdate(device,False)
        return retValue

    def sensorUpdate(self,device,fromRequest):

        requestTrial = 0
        requestMax   = 6
        requestOK = False
        lastError = ""

        sTemperature = 0
        sCharge = 0
        sCapacity = 0
        sWeelDrop = False
        sVoltage = 0
        sButton = 0
        sChargingState = 0
        sCurrent = 0
        sState = 'none'
        sDirt  = 'No'
        sCliff  = 'No'
        sVirtualWall = 'No'
        sObstacle = 'No'
        sVol   = 0
        sVol2  = 0
        sDistance = 0
        sBatteryLevel = 0
        self.debugLog(device.name + ": Requesting status.")
        
        #theUrl = u"http://" + device.pluginProps["address"] + "/roomba.json"
        #theUrl = u"http://" + device.pluginProps["address"] + "/roomba.xml"
        theUrl = u"http://" + device.pluginProps["address"] + "/rwr.xml"

        while (requestTrial < requestMax) and (requestOK == False):
            try:
                if fromRequest == False and self.reqRunning == True:
                    return True
                if requestTrial > 0:
                    self.debugLog(device.name + ": Requesting status ... trial #" + str(requestTrial))          
                f = urllib2.urlopen(theUrl)
                requestOK = True
                if requestTrial > 0 or device.states["RoombaState"] == 'lost':
                    indigo.server.log(device.name + ": was lost, now FOUND !" ) 
                
            except Exception, e:
                if fromRequest == False and self.reqRunning == True:
                    return True
                requestTrial += 1
                lastError = str(e)
                if self.stopThread == True:
                    return False
        if self.stopThread == True:
            return False
        if fromRequest == False and self.reqRunning == True:
            return True 
        if (requestOK == False):
            self.updateDeviceState(device, "RoombaState", "lost")
            
            self.debugLog(device.name + ": Error: " + lastError)
            self.errorLog(device.name + " is LOST !")           
            return False

        theXml = f.read()
        if theXml is None:
            self.errorLog(device.name + ": nothing received.")
        else:
            tree = ET.fromstring (theXml)
            self.debugLog(device.name + ": Status received.") # +  '\r\n' + theXML)

        sCharge   = tree.find('.//r18').text
        sCapacity = tree.find('.//r19').text

        sCurrent = int (tree.find('.//r16').text)
        sButton = int (tree.find('.//r11').text)
        sDistance = int (tree.find('.//r12').text)
        sChargingState = int (tree.find('.//r14').text)


        if int(tree.find('.//r1').text) > 0:
            sObstacle = 'Yes'
        if int(tree.find('.//r2').text) > 0:
            sCliff = 'Yes'
        if int(tree.find('.//r3').text) > 0:
            sCliff = 'Yes'
        if int(tree.find('.//r4').text) > 0:
            sCliff = 'Yes'
        if int(tree.find('.//r5').text) > 0:
            sCliff = 'Yes'
        if int(tree.find('.//r6').text) > 0:
            sVirtualWall = 'Yes'        
        if int(tree.find('.//r8').text) > 0:
            sDirt = 'Yes'
        if int(tree.find('.//r9').text) > 0:
            sDirt = 'Yes'

        self.updateDeviceState(device, "Dirt", sDirt)   
        self.updateDeviceState(device, "Cliff", sCliff)
        self.updateDeviceState(device, "VirtualWall",sVirtualWall)
        self.updateDeviceState(device, "Obstacle",sObstacle)

        if int(sCapacity) >0:
            sBatteryLevel = int (100 * int(sCharge) / int(sCapacity))
        else:
            sBatteryLevel = 0

        self.updateDeviceState(device, "BatteryLevel", sBatteryLevel)
        
        sVol = int(tree.find('.//r15').text)
        sVol2 = round(decimal.Decimal (str(sVol/1000.0)),1)
        self.updateDeviceState(device, "Voltage", sVol2)
        
        self.updateDeviceState(device, "Temperature", tree.find('.//r17').text)


        if int (tree.find('.//r0').text) > 0:
            self.updateDeviceState(device, "WheelDrop", "Yes")
        else:
            self.updateDeviceState(device, "WheelDrop", "No")
        
        if sChargingState == 0:
            self.updateDeviceState(device, "ChargingState", "notcharging")
        if sChargingState == 1:
            self.updateDeviceState(device, "ChargingState", "recovery")
            sState = 'dock'
        if sChargingState == 2:
            self.updateDeviceState(device, "ChargingState", "charging")
            sState = 'dock'
        if sChargingState == 3:
            self.updateDeviceState(device, "ChargingState", "trickle")
            sState = 'dock'
        if sChargingState == 4:
            self.updateDeviceState(device, "ChargingState", "waiting")
        if sChargingState == 5:
            self.updateDeviceState(device, "ChargingState", "error")
            sState = 'problem'

        # sCurrent is positive. So, is charging
        # if sCurrent is negative then is discharging
        if sCurrent > 0: 
            sState = 'dock'

        if sState == 'none':
            if sDistance == 0:
                sState = 'stop'
            else:
                sState = 'clean'
        if device.states["RoombaState"] != sState:
            indigo.server.log(device.name + ": changed state to " + sState)

        self.updateDeviceState(device, "RoombaState", sState)
        if sState == 'clean':
            device.updateStateOnServer("onOffState", True)
        else:
            device.updateStateOnServer("onOffState", False)
        #if sState == 'clean':
        #   device.updateStateOnServer("onOffState", True)
        #if sState == 'dock':
        #   device.updateStateOnServer("onOffState", False) 
        #if sState == 'stop':
        #   device.updateStateOnServer("onOffState", False)     
        #if (sState == 'lost') or (sState == 'problem'):
        #   device.updateStateOnServer("onOffState", False) 
        return True

    def updateDeviceState(self,device,state,newValue):
        if (newValue != device.states[state]):
            device.updateStateOnServer(key=state, value=newValue)

    ###################################################################
    # Custom Action callbacks
    ###################################################################

    def buttonClean(self, pluginAction, device):        
        indigo.server.log(device.name + u": Button Clean called")
        #self.sensorUpdateFromRequest (device)
        sState = device.states["RoombaState"]
        if (sState == 'problem') or (sState == 'lost'):
            self.errorLog(device.name + u": Roomba is lost or has a problem!")
            return False
        if sState == 'clean':
            indigo.server.log(device.name + u": Device is also cleaning.")
            return True
        #self.sendRequest (device,"/rwr.cgi?exec=4")    
        if self.sendRequest (device,"/roomba.cgi?button=CLEAN") == True:
            return True
        else:
            return False
    
    def buttonDock(self, pluginAction, device):
        indigo.server.log(device.name + u": Button Dock called")
        #self.sensorUpdateFromRequest (device)
        sState = device.states["RoombaState"]
        if (sState == 'problem') or (sState == 'lost'):
            self.errorLog(device.name + u": Roomba is lost or has a problem!")
            return False        
        if sState == 'dock':
            indigo.server.log(device.name + u": Roomba is also docked.")
            return True     
        if sState == 'clean':
            #First, we must stop Roomba before changing the mission
            self.sendRequest (device,"/roomba.cgi?button=STOP")

        self.sendRequest (device,"/roomba.cgi?button=DOCK")
        #self.sendRequest (device,"/rwr.cgi?exec=6")
        return True

    def buttonStop(self, pluginAction, device):     
        indigo.server.log(device.name + u": Button Stop called")
        #self.sensorUpdateFromRequest (device)
        sState = device.states["RoombaState"]
        if (sState == 'problem') or (sState == 'lost'):
            self.errorLog(device.name + u": Roomba is lost or has a problem!")
            return False
        if sState == 'clean':
            #Clean/Stop works in toggle mode
            self.sendRequest (device,"/roomba.cgi?button=STOP")
            #self.sendRequest (device,"/rwr.cgi?exec=1")
            return True
        
        indigo.server.log(device.name + u": Device is also stopped or docked.")
        return True

    def buttonSpot(self, pluginAction, device):     
        indigo.server.log(device.name + u": Button Spot called")
        #self.sensorUpdateFromRequest (device)
        sState = device.states["RoombaState"]
        #if not(self.sensorUpdate (device)):
        #   return False
        if (sState == 'problem') or (sState == 'lost'):
            self.errorLog(device.name + u": Roomba has a problem!")
            return
        self.sendRequest (device,"/roomba.cgi?button=SPOT")
        #self.sendRequest (device,"/rwr.cgi?exec=5")

    ###################################################################
    # Relay Action callbacks
    # Trying to define a Roomba as a relay. ON-> Clean. OFF->Dock
    ###################################################################

    def actionControlDimmerRelay(self, pluginAction, device):
        ## Relay ON ##
        if pluginAction.deviceAction == indigo.kDeviceAction.TurnOn:            
            if self.buttonClean(pluginAction,device):
                indigo.server.log(u"sent \"%s\" %s" % (device.name, "on"))
            else:
                indigo.server.log(u"send \"%s\" %s failed" % (device.name, "on"), isError=True)

        ## Relay OFF ##
        elif pluginAction.deviceAction == indigo.kDeviceAction.TurnOff:
            if self.buttonDock(pluginAction,device):
                indigo.server.log(u"sent \"%s\" %s" % (device.name, "off"))
            else:
                indigo.server.log(u"send \"%s\" %s failed" % (device.name, "off"), isError=True)

        ## Relay TOGGLE ##
        elif pluginAction.deviceAction == indigo.kDeviceAction.Toggle:
            if device.onState:
                self.buttonDock(pluginAction,device)                
            else:
                self.buttonClean(pluginAction,device)               

        ## Relay Status Request ##
        elif pluginAction.deviceAction == indigo.kDeviceAction.RequestStatus:
            indigo.server.log(u"sent \"%s\" %s" % (device.name, "status request"))
            if not(self.sensorUpdate (device,True)):
                self.errorLog(u"\"%s\" %s" % (device.name, "status request failed"))
    ########################################
    # Menu Methods
    ########################################
    def toggleDebugging(self):
        if self.debug:
            indigo.server.log("Turning off debug logging")
            self.pluginPrefs["debugEnabled"] = False                
        else:
            indigo.server.log("Turning on debug logging")
            self.pluginPrefs["debugEnabled"] = True
        self.debug = not self.debug
        return
        
    def menuDeviceDiscovery(self):
        if self.discoveryWorking:
            return
        self.deviceDiscover()
        return
        
    def checkForUpdates(self):
        update = self.updater.checkForUpdate() 
        if (update != None):
            pass
        return    

    def updatePlugin(self):
        self.updater.update()