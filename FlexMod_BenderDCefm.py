# FlexMod_BenderDCefm.py 

# Description
# DC EFM Module

# Versions
# 3.5.24.10.16 - SC - Known good starting point, uses thread.is_alive to prevent module stalling.

import sys
from pymodbus.client.sync import ModbusTcpClient as mb_tcp_client
from threading import Thread, Event
from FlexDB import FlexTinyDB
from enum import Enum
import sunspec.core.client as sunspec_client
from pymodbus.client.sync import ModbusTcpClient as modbus_client
from pymodbus.constants import Endian
from pymodbus.payload import BinaryPayloadBuilder, BinaryPayloadDecoder

import copy
from datetime import datetime
import time

# Database
db = FlexTinyDB()

USE_SUNSPEC = False

loop_time = 0


# Queued commands
SYNC = 0                                                                                            # Allows the Flex code to synchronise module polling loops using a single thread
SYNC_ACK = 1
GET_INFO = 2
GET_INFO_ACK = 3
GET_STATUS = 4
GET_STATUS_ACK = 5 
GET_PAGE = 6
GET_PAGE_ACK = 7
SET_PAGE = 8
SET_PAGE_ACK = 9
GET_OUTPUTS = 10
GET_OUTPUTS_ACK = 11
SET_INPUTS = 12
SET_INPUTS_ACK = 13
ERROR = 100


class Module():
    def __init__(self, uid, queue):
        #super(Module, self).__init__()

        # General Module Data
        self.author = "Sophie Coates (formatting), Maninder Grewal (Data collection)"
        self.uid = uid
        self.icon = "/static/images/DCefm.png"
        self.name = "DC Earth Fault Monitor"
        self.module_type = ModTypes.DC_EFM.value
        self.module_version = "3.5.24.10.16"
        self.manufacturer = "Bender"
        self.model = ""
        self.options = ""
        self.version = "-"
        self.serial = "-"
        self.website = "/Mod/FlexMod_BenderDCefm"

        # Run state
        self.con_state = False
        self.state = State.IDLE
        self.set_state_text(self.state)

        # Non-Volatile Data (Loaded from DB)
        self.dbData = db.fetch_from_db(__name__ + "_(" + str(self.uid) + ")")
        if self.dbData is None:
            self.dbData = dict()
            self.dbData["dc_efm_ipaddr_local"] = "0.0.0.0"

        # Volatile Data
        self.tcp_client = None
        self.tcp_timeout = 0
        self.enabled = False
        self.enabled_echo = False
        self.override = False
        self.inputs = [self.uid, False]
        self.outputs = [self.uid, self.enabled, [0]*25]
        self.heartbeat = 0
        self.heartbeat_echo = 0
        
        self.dc_efm_heartbeat = 0
        self.dc_efm_state = 0
        self.dc_efm_R_insulation_resistance = 0
        self.dc_efm_Rmin_insulation_resistance = 0
        self.dc_efm_insulation_alarm_1 = 0
        self.dc_efm_insulation_alarm_2 = 0
        self.dc_efm_alarm_status_code = 0 
        self.dc_efm_alarm_status_params = 0 

        # Events
        self.warnings = 0
        self.alarms = 0
        self.faults = 0
        self.actions = []

        # HMI data, from which power is derived.
        self.priV = 0  # Primary units of AC Voltage and Current
        self.priA = 0
        self.secV = 0  # Secondary, for DC units
        self.secA = 0
        self.terV = 0  # Tertiary, if a device has a third port, like a PD Hydra
        self.terA = 0

        print("Starting " + self.name + " with UID " + str(self.uid) + " on version " + str(self.module_version))
        
        # Track Interval usage (GIL interference)
        self.start_time = time.time()
        self.interval_count = 0
        self.max_delay = 0
        self.last_time = 0

    def process(self):
        global loop_time
        #print("(20)  DC EFM Heartbeat: " + str(self.heartbeat) + ", loop time: " + str(loop_time) + " Seconds")
        
        # Calculate successful polls over an hour
        if time.time() - self.start_time < 3600:    # Record for an hour after starting
        
            # Calculate delay between polls
            time_now = time.time()
            delay = time_now - self.last_time
            #print(delay)
            if self.last_time > 0:
                if self.last_time > 0:
                    if delay > self.max_delay:
                        self.max_delay = delay
            self.last_time = time_now
        
            self.interval_count += 1
            #print("DC EFM " + str(self.uid) + ": " + str(self.interval_count))
        else:
            #print("DC EFM " + str(self.uid) + ": " + str(self.interval_count) + " events in 1 hour. Maximum gap between events was " + str(self.max_delay) + " seconds.")
            pass
            
        s = time.time()
        
        self.heartbeat += 1
        if self.heartbeat >= 0xFFFF:
            self.heartbeat = 0

        if self.tcp_timeout >= 5:
            self.tcp_timeout = 0
            self.tcp_client = None

        if self.tcp_client is None:
            self.con_state = False
            if "dc_efm_ipaddr_local" in self.dbData:
                if self.dbData["dc_efm_ipaddr_local"] != "0.0.0.0":
                    try:
                        self.tcp_client = modbus_client(self.dbData["dc_efm_ipaddr_local"], port=502)

                        if self.tcp_client.connect() is True:
                            self.update_faults(Faults.LOSS_OF_COMMS.value, False)  # Clear Fault
                            self.set_state_text(State.CONNECTED)
                            self.con_state = True
                        else:
                            self.update_faults(Faults.LOSS_OF_COMMS.value, True)  # Raise Fault
                            self.set_state_text(State.CONNECTING)
                            self.tcp_client = None

                    except Exception as e:
                        print("DC EFM: " + str(e))
                        self.update_faults(Faults.LOSS_OF_COMMS.value, True)
                        self.set_state_text(State.CONNECTING)
                else:
                    self.update_faults(Faults.LOSS_OF_COMMS.value, True)  # Raise Fault
                    self.set_state_text(State.CONFIG)
            else:
                self.update_faults(Faults.LOSS_OF_COMMS.value, True)
                self.set_state_text(State.CONFIG)
        else:
            
            # iso685W-S-B
            try:
                rr = self.tcp_client.read_holding_registers(1296,120)

                if rr.isError():
                    self.tcp_timeout += 1
                    return
                else:
                    self.tcp_timeout = 0
                    # Device name & manufacturer
                    self.manufacturer  = ((BinaryPayloadDecoder.fromRegisters(rr.registers[0:15], byteorder=Endian.Big, wordorder=Endian.Big)).decode_string(11)).decode("utf-8") + " / " + ((BinaryPayloadDecoder.fromRegisters(rr.registers[48:96], byteorder=Endian.Big, wordorder=Endian.Big)).decode_string(20)).decode("utf-8")
                    # FW version software/software
                    self.version = (str(rr.registers[96]) + ' V' + str(rr.registers[97]) + ' / ' + str(rr.registers[104]) + ' V' + str(rr.registers[105]))
                    # Serial Number
                    self.serial = ((BinaryPayloadDecoder.fromRegisters(rr.registers[32:48], byteorder=Endian.Big, wordorder=Endian.Big)).decode_string(10)).decode("utf-8")
            except Exception as e:
                print("DC EFM: " + str(e))
                self.tcp_timeout += 1
                return
        
           
            # R Insulation resistance 
            try:
                rr = self.tcp_client.read_holding_registers(4112,3)
                if rr.isError():
                    self.tcp_timeout += 1
                    return
                else:
                    self.tcp_timeout = 0
                    self.dc_efm_R_insulation_resistance = int(((BinaryPayloadDecoder.fromRegisters(rr.registers[0:2], byteorder=Endian.Big, wordorder=Endian.Little)).decode_32bit_float())/1000)
            except Exception as e:
                print("DC EFM: " + str(e))
                self.tcp_timeout += 1
                return                      

            # Rmin. Insulation resistance 
            try:
                rr = self.tcp_client.read_holding_registers(4368,3)
                if rr.isError():
                    self.tcp_timeout += 1
                    return
                else:
                    self.tcp_timeout = 0
                    self.dc_efm_Rmin_insulation_resistance = int(((BinaryPayloadDecoder.fromRegisters(rr.registers[0:2], byteorder=Endian.Big, wordorder=Endian.Little)).decode_32bit_float())/1000)
            except Exception as e:
                print("DC EFM: " + str(e))
                self.tcp_timeout += 1
                return                                              

            # Ins. alarm 1 and Ins. alarm 2 (setpoint)
            try:
                rr = self.tcp_client.read_holding_registers(12499,5)
                if rr.isError():
                    self.tcp_timeout += 1
                    return
                else:
                    self.tcp_timeout = 0
                    self.dc_efm_insulation_alarm_1 = ((rr.registers[0] << 16) | rr.registers[1])/1000
                    self.dc_efm_insulation_alarm_2 = ((rr.registers[2] << 16) | rr.registers[3])/1000

            except Exception as e:
                print("DC EFM: " + str(e))
                self.tcp_timeout += 1
                return                                              



            # Alarm Status
            try:
                rr = self.tcp_client.read_holding_registers(8450,2)
                if rr.isError():
                    self.tcp_timeout += 1
                    return
                else:
                    self.tcp_timeout = 0
                    self.dc_efm_alarm_status_code = (rr.registers[1])

                    alarm_status = (rr.registers[1])
                    self.lst_alarm_status = []
                    offset_as = 1

                    # Alarm Status bit (alarm_status) update
                    for x in range(1, 17):
                        shift = alarm_status & offset_as
                        if shift in range(1, 32788):
                            if shift == 1: # Bit[0]:
                                if 'Ins. alarm 1' not in self.lst_alarm_status:
                                    self.lst_alarm_status.append('Ins. alarm 1')
                            elif shift == 2: # Bit[1]:
                                if 'Ins. alarm 2' not in self.lst_alarm_status:
                                    self.lst_alarm_status.append('Ins. alarm 2')
                            elif shift == 4: # Bit[2]:
                                if 'Connection fault' not in self.lst_alarm_status:
                                    self.lst_alarm_status.append('Connection fault')
                            elif shift == 8: # Bit[3]:
                                if 'DC- alarm' not in self.lst_alarm_status:
                                    self.lst_alarm_status.append('DC- alarm')
                            elif shift == 16: # Bit[4]:
                                if 'DC+ alarm' not in self.lst_alarm_status:
                                    self.lst_alarm_status.append('DC+ alarm')
                            elif shift == 32: # Bit[5]:
                                if 'Symmetrical alarm' not in self.lst_alarm_status:
                                    self.lst_alarm_status.append('Symmetrical alarm')
                            elif shift == 64: # Bit[6]:
                                if 'Device error' not in self.lst_alarm_status:
                                    self.lst_alarm_status.append('Device error')
                            elif shift == 128: # Bit[7]:
                                if 'Common alarm' not in self.lst_alarm_status:
                                    self.lst_alarm_status.append('Common alarm')
                            elif shift == 256: # Bit[8]:
                                if 'Measurement complete' not in self.lst_alarm_status:
                                    self.lst_alarm_status.append('Measurement complete')
                            elif shift == 512: # Bit[9]:
                                if 'Device inactive' not in self.lst_alarm_status:
                                    self.lst_alarm_status.append('Device inactive')
                            elif shift == 1024: # Bit[10]:
                                if 'DC offset alarm' not in self.lst_alarm_status:
                                    self.lst_alarm_status.append('DC offset alarm')
                            elif shift == 2048: # Bit[11]:
                                if 'Common alarm EDS' not in self.lst_alarm_status:
                                    self.lst_alarm_status.append('Common alarm EDS')
                            elif shift == 4096: # Bit[12]:
                                if 'PGH pulse' not in self.lst_alarm_status:
                                    self.lst_alarm_status.append('PGH pulse')
                            elif shift == 8192: # Bit[13]:
                                if 'ISOnet Measurement active' not in self.lst_alarm_status:
                                    self.lst_alarm_status.append('ISOnet Measurement active')
                            elif shift == 16384: # Bit[14]:
                                if 'PGH active' not in self.lst_alarm_status:
                                    self.lst_alarm_status.append('PGH active')
                            elif shift == 32768: # Bit[16]:
                                if 'Communication error' not in self.lst_alarm_status:
                                    self.lst_alarm_status.append('Communication error')
                            else:
                                self.lst_alarm_status.clear()
                        offset_as = offset_as << 1
                    self.dc_efm_alarm_status_params = self.lst_alarm_status
                    #print(self.dc_efm_alarm_status_params)
                    
            except Exception as e:
                print("DC EFM: " + str(e))
                self.tcp_timeout += 1
                return                                              

            # The process is autonomous, just report that we're running
            if self.enabled:  
                self.enabled_echo = True
            else:  
                self.enabled_echo = False
            
            if len(self.actions) == 0:
                self.actions.append(0)  # Dummy

            self.dc_efm_heartbeat = self.heartbeat

            # Modify self.outputs
            self.outputs[2][0] = self.dc_efm_heartbeat
            self.outputs[2][1] = self.dc_efm_state
            self.outputs[2][2] = 0
            self.outputs[2][3] = self.dc_efm_R_insulation_resistance
            self.outputs[2][4] = self.dc_efm_Rmin_insulation_resistance
            self.outputs[2][5] = self.dc_efm_insulation_alarm_1
            self.outputs[2][6] = self.dc_efm_insulation_alarm_2
            self.outputs[2][7] = self.dc_efm_alarm_status_code
            self.outputs[2][8] = 0
            self.outputs[2][9] = 0
            self.outputs[2][10] = 0
            self.outputs[2][11] = 0
            self.outputs[2][12] = 0
            self.outputs[2][13] = 0
            self.outputs[2][14] = 0
            self.outputs[2][15] = 0
            self.outputs[2][16] = 0
            self.outputs[2][17] = 0
            self.outputs[2][18] = 0
            self.outputs[2][19] = 0
            self.outputs[2][20] = self.warnings
            self.outputs[2][21] = self.alarms
            self.outputs[2][22] = self.faults
            self.outputs[2][23] = self.actions[0]

            # HMI Icon Status
            if self.faults != 0:
                self.set_state_text(State.FAULT)
            elif self.alarms != 0:
                self.set_state_text(State.ALARM)
            elif self.warnings != 0:
                self.set_state_text(State.WARNING)
            else:
                if self.enabled_echo:
                    self.set_state_text(State.ACTIVE)
                else:
                    self.set_state_text(State.CONNECTED)

        loop_time = time.time() - s

    def set_inputs(self, inputs):
        for module in inputs:
            if module[0] == self.uid:
                if len(module) > 0:
                    if self.override is False:
                        self.inputs = module

                        self.enabled = self.inputs[1]
                        
        return [SET_INPUTS_ACK]

    def get_outputs(self):
        self.outputs[1] = self.enabled_echo

        #outputs = copy.deepcopy(self.outputs)
        #return outputs
        return [GET_OUTPUTS_ACK, self.outputs]

    def set_page(self, page, form):  # Respond to GET/POST requests
        data = dict()

        if page == self.website + "_(" + str(self.uid) + ")":  # It was a page request for a numbered device (POST)

            # Save all control changes to database
            isButton = True
            for control in form:
                if "dc_efm_override" in form:
                    self.actions.insert(0, Actions.CTRL_OVERRIDE_CHANGE.value)
                    self.override = not self.override

                else:
                    isButton = False

                    if "dc_efm_ipaddr_local" == control:
                        self.actions.insert(0, Actions.IP_ADDRESS_CHANGE.value)

                    self.dbData[control] = form[control]

            if isButton is False:  # Button press states are to be acted upon only, not stored
                self.save_to_db()

            # Let's just record the last 10 user interations for now
            if len(self.actions) >= 10:
                self.actions.pop()
            
            return [SET_PAGE_ACK]

        elif page == (self.website + "_(" + str(self.uid) + ")/data"):  # It was a json data fetch quest (POST)

            mod_data = dict()

            # Controller Information
            mod_data["dc_efm_name"] = self.name
            mod_data["dc_efm_man"] = self.manufacturer
            mod_data["dc_efm_fwver"] = self.version
            mod_data["dc_efm_serno"] = self.serial
            mod_data["dc_efm_constate"] = str(self.con_state).capitalize()
            mod_data["dc_efm_override"] = self.override
            mod_data["dc_efm_enablestate"] = self.enabled
            mod_data["dc_efm_data"] = self.outputs[2]
            mod_data["dc_efm_R_insulation_resistance"] = self.dc_efm_R_insulation_resistance
            mod_data["dc_efm_Rmin_insulation_resistance"] = self.dc_efm_Rmin_insulation_resistance
            mod_data["dc_efm_insulation_alarm_1"] = self.dc_efm_insulation_alarm_1
            mod_data["dc_efm_insulation_alarm_2"] = self.dc_efm_insulation_alarm_2
            mod_data["dc_efm_alarm_status_code"] = self.dc_efm_alarm_status_code
            mod_data["dc_efm_alarm_status_params"] = self.dc_efm_alarm_status_params
            

            mod_data.update(self.dbData)                                                       # I'm appending the dict here so we don't save unnecessary
            
            return [SET_PAGE_ACK, mod_data]                                                                    # data to the database.

        else:
            return [SET_PAGE_ACK, ('OK', 200)]      # Return the data to be jsonified

    def get_page(self):
        routes = [self.website + "_(" + str(self.uid) + ")/data"]                                   # JSON Data: FlexMod_test_(<uid>)/data/
        page = [self.website + "_(" + str(self.uid) + ")", routes]                                  # HTML content: FlexMod_test_(<uid>).html
        return [GET_PAGE_ACK, page]

    def set_state_text(self, state):  # Update the State text on the "Home" HMI Icon
        self.state = state.value
        
    def update_warnings(self, warning, active):
        if active:
            self.warnings |= (1 << warning)
        else:
            self.warnings &= ~(1 << warning)

    def update_alarms(self, alarm, active):
        if active:
            self.alarms |= (1 << alarm)
        else:
            self.alarms &= ~(1 << alarm)

    def update_faults(self, fault, active):
        if active:
            self.faults |= (1 << fault)
        else:
            self.faults &= ~(1 << fault)
              
    def get_info(self):
        return [GET_INFO_ACK, self.uid, self.module_type, self.icon, self.name, self.manufacturer, self.model, self.options, self.version, self.website]
    
    def get_status(self):
        return [GET_STATUS_ACK, self.uid, self.heartbeat, self.priV, self.priA, self.secV, self.secA, self.terV, self.terA, self.state, self.warnings, self.alarms, self.faults, self.actions, self.icon]
    
    def save_to_db(self):
        # This shouldn't be triggered by buttons!
        try:
            db.save_to_db(__name__ + "_(" + str(self.uid) + ")", self.dbData)
        except:
            print("Unable to save record, may already exist?")  # Todo find error code from tinydb

    def kill(self):
        self.stop.set()

def driver(queue, uid):
    
    # Create and init our Module class
    flex_module = Module(uid, queue)
    
    # Create a dummy thread
    thread = Thread(target=flex_module.process)
    
    # Process piped requests
    while True:
        rx_msg = None
        tx_msg = None
        
        try:
            rx_msg = queue[1].get()
            
            if isinstance(rx_msg, list):
                if rx_msg[0] == SYNC:    
                    
                    if not thread.is_alive():
                        thread = Thread(target=flex_module.process)
                        thread.start()
                    tx_msg = None
                    
                elif rx_msg[0] == GET_INFO: 
                    tx_msg = flex_module.get_info()
                    
                elif rx_msg[0] == GET_STATUS:  
                    tx_msg = flex_module.get_status()
                    
                elif rx_msg[0] == GET_PAGE:
                    tx_msg = flex_module.get_page()
                    
                elif rx_msg[0] == SET_PAGE:
                    tx_msg = flex_module.set_page(rx_msg[1], rx_msg[2])              
                
                elif rx_msg[0] == GET_OUTPUTS:
                    tx_msg = flex_module.get_outputs()
                   
                elif rx_msg[0] == SET_INPUTS:
                    tx_msg = flex_module.set_inputs(rx_msg[1])
                
                else:
                    print("Command Unknown: " + str(rx_msg[0]))
                    
        except Exception as e:
            print("DC EFM: " + str(e))
          
        try:
            if tx_msg is not None:
                queue[0].put(tx_msg, block=True)
                
        except Exception as e:
            print("DC EFM: " + str(e))
            
 
# Enums
class ModTypes(Enum):
    BASE = 0
    CONTROL = 1
    BATTERY = 2
    INVERTER = 3
    AC_METER = 4
    DC_METER = 5
    DIG_IO = 6
    ANA_IO = 7
    MIXED_IO = 8
    SWITCH = 9
    LI_ION = 10
    DCDC = 11
    AIRCON = 12
    SENSOR = 13
    FUEL_CELL = 14
    AC_GEN = 15
    AC_WIND = 16
    AC_SOLAR = 17
    DC_SOLAR = 18
    AC_EFM = 19
    DC_EFM = 20
    EV_CHARGE = 21

    SCADA = 22      
    LOGGING = 23
    CLIENT = 24
    UNDEFINED = 25


# Enums # TODO
class Warnings(Enum):
    NONE = 0


class Alarms(Enum):
    NONE = 0


class Faults(Enum):
    NONE = 0
    CONFIG = 1
    LOSS_OF_COMMS = 2


class Actions(Enum):                                                                                # Track the manual interactions with the html page
    NONE = 0
    IP_ADDRESS_CHANGE = 1
    CTRL_OVERRIDE_CHANGE = 2


class State(Enum):
    RESERVED = "Reserved"
    IDLE = "Idle"
    CONFIG = "Configuration"
    CONNECTING = "Connecting"
    CONNECTED = "Connected"
    ACTIVE = "Active"
    WARNING = "Warning"
    ALARM = "Alarm"
    FAULT = "Fault"


if __name__ == '__main__':  # The module must be able to run solo for testing purposes
    pass
