#!/usr/bin/env python3

"""
   Decode Sure Pet Packet

   Copyright (c) 2021, Peter Lambrechtsen (peter@crypt.nz)

   This program is free software; you can redistribute it and/or modify
   it under the terms of the GNU General Public License as published by
   the Free Software Foundation; either version 3 of the License, or
   (at your option) any later version.

   This program is distributed in the hope that it will be useful,
   but WITHOUT ANY WARRANTY; without even the implied warranty of
   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
   GNU General Public License for more details.

   You should have received a copy of the GNU General Public License
   along with this program; if not, write to the Free Software Foundation,
   Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301  USA
"""

import binascii, struct, time, sys, sqlite3, json

from datetime import datetime
from operator import xor
from pathlib import Path
from enum import IntEnum

#Debugging mesages
PrintFrame = False #Print the before and after xor frame
PrintFrameDbg = False #Print the frame headers
Print126Frame = False #Debug the 2A / 126 feeder frame
Print127Frame = False #Debug the 2D / 127 feeder frame
Print132Frame = False #Debug the 3C / 132 hub and door frame
Print2Frame = False   #Debug the 2 frame

class SureEnum(IntEnum):
    """Sure base enum."""
    def __str__(self) -> str:
        return self.name.title()

    @classmethod
    def has_value(self, value):
        return value in self._value2member_map_ 

class EntityType(SureEnum):
    """Sure Entity Types."""
    PET           = 0   # artificial ID, not used by the Sure Petcare API
    HUB           = 1   # Hub
    REPEATER      = 2   # Repeater
    PET_FLAP      = 3   # Pet Door Connect
    FEEDER        = 4   # Microchip Pet Feeder Connect
    PROGRAMMER    = 5   # Programmer
    FEEDER_LITE   = 7   # Feeder Lite
    CAT_FLAP      = 6   # Cat Flap Connect
    FELAQUA       = 8   # Felaqua Connect
    DEVICES       = 13  # artificial ID, Pet Flap + Cat Flap + Feeder = 3 + 6 + 4 = 13  ¯\_(ツ)_/¯

class FeederState(SureEnum): # Feeder states
    Animal_Open   = 0   # Animal Open Feeder
    Animal_Closed = 1   # Animal Closed Feeder
    Manual_Open   = 4   # Manually Opened Feeder
    Manual_Closed = 5   # Manually Closed Feeder
    Zero_Both     = 6   # Zero Feeder both scales
    Zero_Left     = 7   # Zero Feeder left scale
    Zero_Right    = 8   # Zero Feeder right scale

class FeederCloseDelay(SureEnum): # Feeder Close Delay speed
    Fast        = 0     # Fast delay
    Normal      = 4000  # Normal delay
    Slow        = 20000 # Slow delay

class FeederBowls(SureEnum): # Feeder Close Delay speed
    Single        = 1   # Fast close delay
    Double        = 2   # Normal delay

class LockState(SureEnum): # Lock State IDs.
    UNLOCKED        = 0
    LOCKED_IN       = 1
    LOCKED_OUT      = 2
    LOCKED_ALL      = 3
    CURFEW          = 4
    CURFEW_LOCKED   = -1
    CURFEW_UNLOCKED = -2
    CURFEW_UNKNOWN  = -3

class LockedOutState(SureEnum): # Locked Out State for preventing animals coming in
    NORMAL          = 2  # Allow pets in
    LOCKED_IN       = 3  # Keep pets out

class PetDirection(SureEnum): #
    Out_61          = 0x61
    Out_62          = 0x62

class CurfewState(SureEnum): # Sure Petcare API State IDs.
    DISABLED        = 1
    ENABLED         = 2
    STATE3          = 3



#Feeder static values
pDCurfewState={1:"Disabled",2:"Enabled"}

#Import xor key from pethubpacket.xorkey and make sure it is sane.
xorfile=Path('pethubpacket.xorkey').read_text()
if len(xorfile) > 20 and len(xorfile) % 2 == 0:
    xorkey=bytearray.fromhex(xorfile)
else:
    sys.exit("Corrupted pethubpacket.xorkey file, make sure the length is an even set of bytes")

#Load PetHubLocal database
conn=sqlite3.connect('pethublocal.db')
curs=conn.cursor()
conn.row_factory = sqlite3.Row


def feederchiptohex(chip):
    chiphex = ""
    if len(chip) == 10:
        #HDX Chip - Chip type seems to be always 03 and needs a 00 to pad it to the right length.
        chiphex = chip+'0003'
    else:
        #FDX-B Chip - Append 01 for chip type.
        chipsplit=chip.split(".")
        chipbin=format(int(chipsplit[0]),'b').zfill(10)+format(int(chipsplit[1]),'b').zfill(38)
        chipint=int(chipbin,2)
        chiphex=hex(int.from_bytes(chipint.to_bytes(6,'little'),'big'))[2:]
        chiphex = chiphex+'01'
        #print("Feeder Chip to Hex : " + chip + " " + chiphex)
    return chiphex

def feederhextochip(chiphex):
    chipbytes = bytes.fromhex(chiphex)
    chip = ""
    if len(chiphex) == 10:
        chip=chiphex
    elif chipbytes[5] == 0:
        #HDX
        chip=chiphex[:10]
    else:
        #FDX-B
        chipval = "{0:48b}".format(int.from_bytes(chipbytes[:6], byteorder='little'))
        chip=str(int(chipval[:10],2)) + "." + str(int(chipval[10:],2)).zfill(12)
        #print("Feeder Hex to Chip : " + chiphex + " " + chip)
    return chip

def doorchiptohex(chip):
    chipsplit=chip.split(".")
    chipbin=format(int(chipsplit[0]),'b').zfill(10)+format(int(chipsplit[1]),'b').zfill(38)
    #print(chipbin)
    chiphex=hex(int(chipbin[::-1],2))[2:]
    #print("Door   Chip to Hex : " + chip + " " + chiphex)
    return chiphex

def doorhextochip(chiphex):
    chipbin = "{0:48b}".format(int.from_bytes(bytes.fromhex(chiphex), byteorder='big'))[::-1]
    #print(chipbin)
    chip=str(int(chipbin[:10],2)) + "." + str(int(chipbin[10:],2)).zfill(12)
    #print("Door   Hex to Chip : " + chiphex + " " + chip)
    return chip

#Conversion of byte arrays into integers
def b2ih(b2ihvalue):
    #Divide int by 100 to give two decimal places for weights
    return str(int(b2is(b2ihvalue))/100)

def b2iu(b2ivalue):
    #Take little endian hex byte array and convert it into a int then into a string.
    return str(int.from_bytes(b2ivalue, byteorder='little', signed=False))

def b2is(b2ivalue):
    #Take little endian hex byte array and convert it into a int then into a string.
    return str(int.from_bytes(b2ivalue, byteorder='little', signed=True))

def b2ib(b2ivalue):
    #Take little endian hex byte array and convert it into a int then into a string.
    return str(int.from_bytes(b2ivalue, byteorder='big', signed=True))

def macconv(macaddress):
    #Take little endian hex byte array and convert it into a int then into a string.
    return str(int.from_bytes(b2ivalue, byteorder='big', signed=True))

def tohex(ba):
    return ''.join(format(x, '02x') for x in ba)

#Convert a int to hex
def hb(hexbyte):
    return format(hexbyte,'02x')
    
def petmovement(mac_address, deviceindex):
    curs.execute('select tag from tagmap where mac_address=(?) and deviceindex=(?)', (mac_address, deviceindex))
    tagval = curs.fetchone()
    if (tagval):
        tag=str(tagval[0])
        curs.execute('select name from pets where tag=(?)', ([tag]))
        petval = curs.fetchone()
        if (petval):
            petname=petval[0]
        else:
            petname="Unknown"
    else:
        petname="Unknown"
    return petname

#126 Frames aka 0x7E (126) or 0x2A (Wire value with TBC XOR) can have multiple messages, so need to loop until you get to the end
def parsemultiframe(device, payload):
    response = []
    operation = []
    while len(payload) > 2:
        subframelength=payload[0]+1
        currentframe=payload[1:subframelength]
        frameresponse = parseframe(device, currentframe)
        response.append(frameresponse)
        #Append the operation to a separate array we attach at the end.
        op=frameresponse['OP']
        operation.append(op)
        #Remove the parsed payload and loop again
        del payload[0:subframelength]
    response.append({"OP":operation})
    return response

#Parse Feeder Frame 
#127 Frame aka 0x7F (127) or 0x2D (Wire value with TBC XOR) are a single payload vs 126 need parsemultiframe as they have multiple messages in a single frame
def parseframe(device, value):
    frameresponse = {}
    msg = ""
    if value[0] in [0x07, 0x0b, 0x0c, 0x10, 0x11, 0x16]: #Unknown messages
        op=hb(value[0])
        frameresponse["OP"]="Msg"+op
        frameresponse["MSG"]=tohex(value[3:])
        if Print126Frame:
            print("FF-"+op+": **TODO** Msg " + tohex(value[3:]))
    elif value[0] == 0x00: #Send Acknowledge for data type
        frameresponse["OP"]="Ack"
        frameresponse["MSG"]=hb(value[8])
        if Print126Frame:
            print("FF-00:ACK-" + hb(value[8]))
    elif value[0] == 0x01: #Send query for data type
        frameresponse["OP"]="Query"
        frameresponse["MSG"]=hb(value[8])
        if Print126Frame:
            print("FF-01:QRY-" + hb(value[8]))
    elif value[0]==0x09: #Update state
        frameresponse["OP"]="UpdateState"
        submessagevalue = b2is(value[9:12])
        if value[8]==0x05: # Training mode
            msg = {"MSG":"FF-09:TRN-" + submessagevalue} #Training Mode
            frameresponse["SUBOP"]="Training"
            frameresponse["MODE"]=submessagevalue
        elif value[8]==0x0a: #Set Left Weight
            msg += "FF-09:SLW-" + submessagevalue
            frameresponse["SUBOP"]="SetLeftWeight"
            frameresponse["WEIGHT"]=submessagevalue
        elif value[8]==0x0b:
            msg += "SetRightWeight=" + submessagevalue
            frameresponse["SUBOP"]="SetRightWeight"
            frameresponse["WEIGHT"]=submessagevalue
        elif value[8]==0x0c:
            msg += "FF-09:SBC-" + FeederBowls(int(submessagevalue)).name
            frameresponse["SUBOP"]="SetBowls"
            frameresponse["BOWLS"]=FeederBowls(int(submessagevalue)).name
        elif value[8]==0x0d:
            msg += "FF-09:CLD-" + FeederCloseDelay(int(submessagevalue)).name
            frameresponse["SUBOP"]="SetCloseDelay"
            frameresponse["DELAY"]=FeederCloseDelay(int(submessagevalue)).name
        elif value[8]==0x12:
            msg += "FF-09:?12=" + tohex(value[9:12])
            frameresponse["SUBOP"]="SetTODO"
            frameresponse["MSG"]=tohex(value[9:12])
        elif value[8]==0x17:
            msg += "ZeroLeftWeight=" + submessagevalue
            frameresponse["SUBOP"]="ZeroLeftWeight"
            frameresponse["WEIGHT"]=submessagevalue
        elif value[8]==0x18:
            msg += "ZeroRightWeight=" + submessagevalue
            frameresponse["SUBOP"]="ZeroRigtWeight"
            frameresponse["WEIGHT"]=submessagevalue
        elif value[8]==0x19:
            msg += "FF-09:?19=" + tohex(value[9:12])
            frameresponse["SUBOP"]="SetTODO"
            frameresponse["MSG"]=tohex(value[9:12])
        else:
            msg += "FF-09:???=" + tohex(value[9:12])
            frameresponse["SUBOP"]="SetTODO"
            frameresponse["MSG"]=tohex(value[9:12])
        if Print126Frame:
            print(msg)
    elif value[0] == 0x18:
        frameresponse["OP"]="Feed"
        #Hard code feeder states
        if FeederState.has_value(value[15]):
            if value[15] in range(4, 8):
                tag="Manual"
                frameresponse["Animal"]="Manual"
            else:
                tag = feederhextochip(tohex(value[8:15]))
                curs.execute('select name from pets where tag=(?)', ([tag]))
                petval = curs.fetchone()
                if (petval):
                    frameresponse["Animal"]=petval[0]
                else:
                    frameresponse["Animal"]=tag
            action=FeederState(int(value[15])).name
            feederopenseconds=b2iu(value[16:17])
            scaleleftfrom=b2ih(value[19:23])
            scaleleftto=b2ih(value[23:27])
            scalerightfrom=b2ih(value[27:31])
            scalerightto=b2ih(value[31:35])
            frameresponse["FA"]=action
            frameresponse["FOS"]=feederopenseconds
            frameresponse["SLF"]=scaleleftfrom #Or if single bowl
            frameresponse["SLT"]=scaleleftto
            frameresponse["SRF"]=scalerightfrom
            frameresponse["SRT"]=scalerightto
            #Return bowl count
            curs.execute('select bowltype from feeders where mac_address=(?)', ([device]))
            bowlval = curs.fetchone()
            if (bowlval):
                frameresponse["BC"]=bowlval[0]
            else:
                frameresponse["BC"]=1
            #response.append(frameresponse)
            if Print126Frame:
                print("FF-18: Feeder door change - chip="+tag+",action="+action+',feederopenseconds='+ b2iu(value[16:17])+',scaleleftfrom='+b2ih(value[19:23])+',scaleleftto='+b2ih(value[23:27])+',scalerightfrom='+b2ih(value[27:31])+',scalerightto='+b2ih(value[31:35]))
        else:
            frameresponse["OP"]="Unknown"
            frameresponse["MSG"]=tohex(value)
    else:
        frameresponse["OP"]="Unknown"
        frameresponse["MSG"]=tohex(value)
    return frameresponse

#Parse Hub Frames aka 132's sent to the pet door
def parsehubframe(operation,device,offset,value):
    response = []
    operation = []
    message=bytearray.fromhex(value)
    logmsg=""
    if offset >= 0:
        msgval = {}
        msgval['Msg']=value
        operation.append("MSG")
        response.append(msgval)
    else:
        msgval = {}
        msgval['Msg']=value
        operation.append("MSG")
        response.append(msgval)
    response.append({"OP":operation})
    return response

#Parse Pet Door Frames aka 132's sent to the pet door
def parsedoorframe(operation,device,offset,value):
    response = []
    operation = []
    msgval = {}
    message=bytearray.fromhex(value)
    if PrintFrameDbg:
        print("Operation: " + operation + " device " + device + " offset " + str(offset) + " -value- " + value)
    logmsg=""
    if offset == 33: #Battery and Time
        op="BatteryandTime"
        msgval['OP']=op
        operation.append(op)
        msgval['Battery']=int(message[1])
        msgval['Time']=int(message[2]) +":"+ int(message[3])
        response.append(msgval)
    if offset == 34: #Set local time for Pet Door 34 = HH in hex and 35 = MM
        op="SetTime"
        msgval['OP']=op
        operation.append(op)
        msgval['Time']=int(message[1]) +":"+ int(message[2])
        response.append(msgval)
        #logmsg += device + " " + operation + "-Time    : "+ int(message[1]) +":"+ int(message[2])
        #if int(message[0]) > 2:
        #    logmsg += "Addional bytes:"+tohex(message[3:])
    if offset == 36: #Lock state
        op="LockState"
        msgval['OP']=op
        operation.append(op)
        msgval['Lock']=LockState(int(message[1])).name
        response.append(msgval)
        #logmsg += device + " " + operation + "-Lockstate       : "+ pDLockState[int(message[1])]
        #if int(message[0]) > 1:
        #    logmsg += "Addional bytes:"+tohex(message[2:])
    elif offset == 40: #Keep pets out allow incoming state
        op="LockedOutState"
        msgval['OP']=op
        operation.append(op)
        msgval['LockedOut']=LockedOutState(int(message[1])).name
        response.append(msgval)
        #logmsg += device + " " + operation + "-Keep pets out   : "+pDKeepPetsOutState[int(message[1])]
        #if int(message[0]) > 1:
        #    logmsg += "Addional bytes:"+tohex(message[2:])
    elif offset == 59: #Provisioned Chip Count
        op="PrivChipCount"
        msgval['OP']=op
        operation.append(op)
        msgval['ChipCount']=message[1]
        response.append(msgval)
        #logmsg += device + " " + operation + "-Prov Chip #     : "+(message[1])
        #if int(message[0]) > 1:
        #   logmsg += "Addional bytes:"+tohex(message[2:])
    elif offset >= 91 and offset <= 308: #Provisioned chips
        op="ProvChip"
        msgval['OP']=op
        operation.append(op)
        pet=round((int(offset)-84)/7) #Calculate the pet number
        chip=doorhextochip(value[4:]) #Calculate chip Number
        msgval['PetOffset']=pet
        msgval['Chip']=chip
        response.append(msgval)
        #logmsg += device + " " + operation + "-Prov Chip ID   "+ str(pet) + " : Chip number " + chip
    elif offset == 519: #Curfew
        op="Curfew"
        msgval['OP']=op
        operation.append(op)
        msgval['CurfewState']=CurfewState(message[1]).name
        msgval['CurfewOn']=str(message[2]).zfill(2)+":"+str(message[3]).zfill(2)
        msgval['CurfewOff']=str(message[4]).zfill(2)+":"+str(message[5]).zfill(2)
        response.append(msgval)
        #logmsg += device + " " + operation + "-Curfew          : "+pDCurfewState[message[1]] + " Lock: "+str(message[2]).zfill(2)+":"+str(message[3]).zfill(2) + " Unlock: "+str(message[4]).zfill(2)+":"+str(message[5]).zfill(2)
    elif offset >= 525 and offset <= 618: #Pet movement in or out
        op="PetMovement"
        msgval['OP']=op
        operation.append(op)
        pet=round((int(offset)-522)/3)-1 #Calculate the pet number
        petname = petmovement(device, pet)
        if PetDirection.has_value(message[3]):
            direction = PetDirection(message[3]).name
        else:
            direction = "Other " + hb(message[3])
        msgval['PetOffset']=pet
        msgval['Animal']=petmovement(device, pet)
        msgval['Direction']=direction
        operation.append("PetMovement")
        response.append(msgval)
        #logmsg += device + " " + operation + "-Pet Movement    : " + petname + " went " + direction
 #       print("Pet " + str(pet) + " went " + message[3])
    elif offset == 621: #Unknown pet went outside
        op="PetMovement"
        msgval['OP']=op
        operation.append(op)
        msgval['PetOffset']="621"
        msgval['Animal']="Unknown Pet"
        msgval['Direction']="Outside"
        response.append(msgval)
#        logmsg += device + " " + operation + "-Pet Movement    : Unknown pet went outside " + value
    else:
        op="Other"
        msgval['OP']=op
        operation.append(op)
        msgval['MSG']=value
        #print("Other ", value)
        #logmsg += device + " " + operation + "-Other offset    : " + value
        #print("other offset" + logmsg)
    response.append({"OP":operation})
    return response

def decodehubmqtt(topic,message):
    response = dict();
    msgsplit=message.split()
    topicsplit=topic.split('/')
    device=topicsplit[-1:][0]

    #Decode device name
    if device=="messages":
        response['device'] = "hub"
    else:
        curs.execute('select name from devices where mac_address=(?)', ([device]))
        devicename = curs.fetchone()
        if devicename:
            response['device'] = str(devicename[0])
        else:
            response['device'] = str(device)
    response['message'] = msgsplit
    
    response['timestamp'] = msgsplit[0]
    
    if msgsplit[1] == "1000":
        operation = "Command"
    else:
        operation = "Status"
    response['operation'] = operation

    #Device message
    if msgsplit[0] == "Hub": #Hub Offline Last Will message
        resp = []
        resp.append({"Msg":message})
        resp.append({"OP":["Boot"]})
        response['message'] = resp
    elif msgsplit[2] == "Hub": #Hub online / boot message
        resp = []
        resp.append({"Msg":" ".join(msgsplit[2:])})
        resp.append({"OP":["Boot"]})
        response['message'] = resp
    elif msgsplit[2] == "10": #Hub Uptime
        resp = []
        msgval = {}
        msgval['Uptime']=str(int(msgsplit[3]))
        msgval['TS']=msgsplit[4]+"-"+msgsplit[5]+":"+msgsplit[6]+":"+msgsplit[7]
        msgval['Reconnect']=msgsplit[9]
        resp.append({"Msg":msgval})
        resp.append({"OP":["Uptime"]})
        response['message'] = resp
    elif msgsplit[2] == "127": #Feeder frame sent/control message
        #ba = bytearray([0x7F])
        print("message 127")
        singleframe = bytearray.fromhex("".join(msgsplit[3:]))
        singleresponse = []
        singleresponse.append(parseframe(device, singleframe))
        singleresponse.append({"OP":[frameresponse['OP']]})
        response['message'] = singleresponse
    elif msgsplit[2] == "126": #Feeder frame status message
        #ba = bytearray([0x7E])
        #print("message 127")
        multiframe = bytearray.fromhex("".join(msgsplit[3:]))
        response['message'] = parsemultiframe(device,multiframe)
        #print(response['message'])
    elif msgsplit[2] == "132" and device != "messages" : #Pet Door Status
        #Status message has a counter at offset 4 we can ignore:
        if Print132Frame:
            print("132 Message : "+message)
        msgsplit[5] = hb(int(msgsplit[5])) #Convert length at offset 5 which is decimal into hex byte so we pass it as a hex string to parsedataframe
        #print("Message :", "".join(msgsplit[5:]))
        response['message'] = parsedoorframe(operation,device, int(msgsplit[4]),"".join(msgsplit[5:]))
        
    elif msgsplit[2] == "132" and device == "messages": #Hub Frame
        #Status message has a counter at offset 4 we can ignore:
        if Print132Frame:
            print("132 Message : "+message)
        msgsplit[5] = hb(int(msgsplit[5])) #Convert length at offset 5 which is decimal into hex byte so we pass it as a hex string to parsedataframe
        #print("Message :", "".join(msgsplit[5:]))
        response['message'] = parsehubframe(operation,device, int(msgsplit[4]),"".join(msgsplit[5:]))
    elif msgsplit[2] == "2": #Action message setting value to Pet Door
        #Action message doesn't have a counter
        if Print2Frame:
            print("2 Message : "+message)
        msgsplit[4] = hb(int(msgsplit[4])) #Convert length at offset 4 which is decimal into hex byte so we pass it as a hex string to parsedoorframe
        response['message'] = parsedataframe(operation,device, int(msgsplit[3]),"".join(msgsplit[4:]))
    elif msgsplit[2] == "8": #Action message setting value to Pet Door
        resp = []
        resp.append({"Msg":message})
        resp.append({"OP":["8"]})
        response['message'] = resp
    else:
        response['message'] = message
        response['OP'] = "Error"
    return response

def decodemiwi(timestamp,source,destination,framestr):
    framexor=bytearray.fromhex(framestr)
    #Convert MAC addresses into reverse byte format.
    sourcemac=''.join(list(reversed(source.split(":")))).upper()
    destinationmac=''.join(list(reversed(destination.split(":")))).upper()

    #Dexor the frame
    frame = list(map(xor, framexor, xorkey))

    if PrintFrame:
        print("Received frame at: " + timestamp + " from: " + sourcemac + " to: " + destinationmac)
        print("Packet:" + tohex(framexor))
        print("Dexor :" + tohex(frame))
    if len(frame) > 8:
        if PrintFrameDbg:
            print("Frame Type       :", hb(frame[2]))
            print("Frame Length     :", len(frame))
            print("Frame Length Val :", frame[4])
        payload=frame[6:]
        logmsg=""
        if frame[2] == 0x2a: #126 Message Feeder to Hub Message
            logmsg="2A: "
            if Print126Frame:
                print("FF-126 Request: " + tohex(payload))
            resmessage=parsemultiframe(payload)
            if len(resmessage) > 0 and Print127Frame:
                print("126 Resp :", resmessage)
        elif frame[2] == 0x2d: #127 Message Hub to Feeder control message
            logmsg="2D: "
            if Print127Frame:
                print("FF-127 Request: " + tohex(payload))
            resmessage=parseframe(payload)
            logmsg += resmessage
            if len(resmessage) > 0 and Print127Frame:
                print("Frame 2D message :", resmessage)
        elif frame[2] == 0x3c: #Pet door frame
            logmsg="132: " + tohex(payload)
            if Print132Frame:
                print("DF-132 Request: " + tohex(payload))
            print(parsedataframe('Status',source,int(b2ib(payload[0:2])),tohex(payload[2:-1])))
                
    else:
        logmsg = "Corrupt Frame " + tohex(frame)
    return "Received frame at: " + timestamp + " from: " + sourcemac + " to: " + destinationmac + " " + logmsg

def buildmqtt(type,value):
#  ts = str(binascii.hexlify(struct.pack('>I', round(datetime.utcnow().timestamp()))),'ascii')
  ts = hex(round(datetime.utcnow().timestamp()))[2:]
  return ts + " 1000 "+type+" " + " ".join(value[i:i+2] for i in range(0, len(value), 2))

#Generate message
def generatemessage(msgtype,msg):
    if type=="hub":
        print("TBC")
    elif type=="feeder":
        if operation == "ackfeederstatedoor":
        #Acknowledge the 18 door state.
            msgstr = "0000ZZ00TTTTTTTT180000"
            msgstr = msgstr.replace('ZZ', format(counter,'02x'))
            msgstr = msgstr.replace('TTTTTTTT', timestamp) # Timestamp
            return msgstr
        elif operation == "ackfeederstate16":
            #Acknowledge the 16 state.
            msgstr = "0000ZZ00TTTTTTTT160000"
            msgstr = msgstr.replace('ZZ', format(counter,'02x'))
            msgstr = msgstr.replace('TTTTTTTT', timestamp) # Timestamp
            return msgstr
        elif operation == "ackfeederstate09":
            #Acknowledge the 09 settings update state.
            msgstr = "0000ZZ00TTTTTTTT090000"
            msgstr = msgstr.replace('ZZ', format(counter,'02x'))
            msgstr = msgstr.replace('TTTTTTTT', timestamp) # Timestamp
            return msgstr
        elif operation == "getcurrentstate0b":
        #Request boot message state 0b
            msgstr = "0100ZZ00TTTTTTTT0b00"
            msgstr = msgstr.replace('ZZ', format(counter,'02x'))
            msgstr = msgstr.replace('TTTTTTTT', timestamp) # Timestamp
            return msgstr
        elif operation == "getbootstate0c":
            #Request boot message state 0c
            msgstr = "0100ZZ00TTTTTTTT0c00"
            msgstr = msgstr.replace('ZZ', format(counter,'02x'))
            msgstr = msgstr.replace('TTTTTTTT', timestamp) # Timestamp
            return msgstr
        elif operation == "getbootstate10":
        #Request boot message state 10
            msgstr = "0100ZZ00TTTTTTTT1000"
            msgstr = msgstr.replace('ZZ', format(counter,'02x'))
            msgstr = msgstr.replace('TTTTTTTT', timestamp) # Timestamp
            return msgstr
        elif operation == "getprovisionedchipsstate":
            #This tells the feeder to dump all provisioned chips
            msgstr = "0100ZZ00TTTTTTTT1100ff"
            msgstr = msgstr.replace('ZZ', format(counter,'02x'))
            msgstr = msgstr.replace('TTTTTTTT', timestamp) # Timestamp
            return msgstr
        elif operation == "getbootstate17":
            #Request boot message state 17
            msgstr = "0100ZZ00TTTTTTTT170000"
            msgstr = msgstr.replace('ZZ', format(counter,'02x'))
            msgstr = msgstr.replace('TTTTTTTT', timestamp) # Timestamp
            return msgstr
        elif operation == "lidclosedelayfast":
            #Update lid close delay to fast = 0
            msgstr = "0900ZZ00TTTTTTTT0d00000000"
            msgstr = msgstr.replace('ZZ', format(counter,'02x'))
            msgstr = msgstr.replace('TTTTTTTT', timestamp) # Timestamp
            return msgstr
        elif operation == "lidclosedelaynormal":
            #Update lid close delay to normal = 4000 (0fa0) - 4 seconds(?)
            msgstr = "0900ZZ00TTTTTTTT0da00f0000"
            msgstr = msgstr.replace('ZZ', format(counter,'02x'))
            msgstr = msgstr.replace('TTTTTTTT', timestamp) # Timestamp
            return msgstr
        elif operation == "lidclosedelayslow":
            #Update lid close delay to slow = 20000 (4e20) - 20 seconds(?)
            msgstr = "0900ZZ00TTTTTTTT0d204e0000"
            msgstr = msgstr.replace('ZZ', format(counter,'02x'))
            msgstr = msgstr.replace('TTTTTTTT', timestamp) # Timestamp
            return msgstr
        elif operation == "setleftscaleweight":
            #Set left or single bowl scale weight
            msgstr = "0900ZZ00TTTTTTTT0aWWWWWWWW"
            msgstr = msgstr.replace('ZZ', format(counter,'02x'))
            msgstr = msgstr.replace('TTTTTTTT', timestamp) # Timestamp
            weight= str(binascii.hexlify(struct.pack('<I', opvalue*100)),'ascii')
            msgstr = msgstr.replace("WWWWWWWW", weight)
            return msgstr
        elif operation == "setrightscaleweight":
            #Set right scale weight
            msgstr = "0900ZZ00TTTTTTTT0bWWWWWWWW"
            msgstr = msgstr.replace('ZZ', format(counter,'02x'))
            msgstr = msgstr.replace('TTTTTTTT', timestamp) # Timestamp
            weight= str(binascii.hexlify(struct.pack('<I', opvalue*100)),'ascii')
            msgstr = msgstr.replace("WWWWWWWW", weight)
            return msgstr
        elif operation == "setbowlcount":
            #Set bowl count either 01 for one bowl or 02 for two.
            msgstr = "0900ZZ00TTTTTTTT0cWW000000"
            msgstr = msgstr.replace('ZZ', format(counter,'02x'))
            msgstr = msgstr.replace('TTTTTTTT', timestamp) # Timestamp
            msgstr = msgstr.replace("WW", format(opval,'02x'))
            return msgstr
        elif operation == "set12message":
            #Not sure what caused this but it happened around setting the scales - 12f4010000
            msgstr = "0900ZZ00TTTTTTTT12WWWWWWWW"
            msgstr = msgstr.replace('ZZ', format(counter,'02x'))
            msgstr = msgstr.replace('TTTTTTTT', timestamp) # Timestamp
            weight= str(binascii.hexlify(struct.pack('<I', opvalue)),'ascii')
            msgstr = msgstr.replace("WWWWWWWW", weight)
            return msgstr
        elif operation == "zeroleftscale":
            #Zero the left scale, first need to check feeder is open via Message 18 state 
            msgstr = "0d00ZZ00TTTTTTTT001900000003000000000101"
            msgstr = msgstr.replace('ZZ', format(counter,'02x'))
            msgstr = msgstr.replace('TTTTTTTT', timestamp) # Timestamp
            return msgstr
        elif operation == "zerorightscale":
            #Zero the right scale, first need to check feeder is open via Message 18 state 
            msgstr = "0d00ZZ00TTTTTTTT001900000003000000000102"
            msgstr = msgstr.replace('ZZ', format(counter,'02x'))
            msgstr = msgstr.replace('TTTTTTTT', timestamp) # Timestamp
            return msgstr
        elif operation == "zerobothscales":
            #Zero the both scales, first need to check feeder is open via Message 18 state 
            msgstr = "0d00ZZ002601e354001900000003000000000103"
            msgstr = msgstr.replace('ZZ', format(counter,'02x'))
            return msgstr
        else:
            print("Unknown message")
    elif type=="petdoor":
        if operation == "settime":
            #Set the time, packet 34 or 22 in hex
            msgstr = "022202HHMM"
            msgstr = msgstr.replace('HHMM', time)
            return msgstr
        if operation == "enablecurfew":
            #Set the time, packet 36 or 24 in hex
            msgstr = "02240104"
            return msgstr

    else:
        print("Unknown type")





'''
        #else:
        #    print("?? Request: " + tohex(payload))
        #    return "?? Request: " + tohex(payload)
#            devid = sourcemac + '-' + b2ib(payload[0:2])
#            pbdc = conn.cursor()
#            pbdc.execute('select name from doorpetmapping left join animals using (petid) where devicepetid=(?)', ([devid]))
#            pbd = pbdc.fetchone()
#            if (pbd):
    #            #I'm not sure if this is right or not, but there are very weird values sometimes for in / out
    #            direction=list(hb(payload[6]))
#                msg = pbd[0] + " went " + hb(payload[6])
    #            msg = pbd[0] + " went " + fDirection[int(direction[1])] + " in " + direction[0] + " seconds " + hb(payload[6])
#                logmsg += msg
#                if Print132Frame:
#                    print(msg)
#            else:
#                logmsg += tohex(payload)
    #                return pbd[0]
    #            else:
    #                pbdc.execute("INSERT OR IGNORE INTO doorpetmapping values((?), (?), (?)) ", ("0", device, int(id)))
    #                conn.commit()
    #                return "Unknown"
'''
