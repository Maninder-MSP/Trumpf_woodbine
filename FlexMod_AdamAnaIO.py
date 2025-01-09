# FlexMod_AdamAnaIO.py

# Description
# ADAM 6015 Analogue IO Module

# Versions
# 3.5.24.10.16 - SC - Known good starting point, uses thread.is_alive to prevent module stalling. 

import sys
from pymodbus.client.sync import ModbusTcpClient as mb_tcp_client
from threading import Thread, Event
from FlexDB import FlexTinyDB
from enum import Enum
import time
import copy
from datetime import datetime

# Database
db = FlexTinyDB()

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
        super(Module, self).__init__()

        # General Module Data
        self.author = "Sophie Coates"
        self.uid = uid  # A unique identifier passed from the HMI via a mangled URL
        self.icon = "/static/images/AnaIO.png"
        self.name = "ADAM-6015"
        self.module_type = ModTypes.ANA_IO.value
        self.module_version = "3.5.24.10.16"                                                        # Last update on "Flex version | Year | Month | Day"
        self.manufacturer = "ADAM"
        self.model = ""
        self.options = ""
        self.version = "-"
        self.serial = "-"  
        self.website = "/Mod/FlexMod_AdamAnaIO"  # This is the template name itself

        # Run state
        self.con_state = False
        self.state = "Idle"
        self.set_state_text(State.IDLE)

        # Non-Volatile Data (Loaded from DB)
        self.dbData = db.fetch_from_db(__name__ + "_(" + str(self.uid) + ")")
        if self.dbData is None:
            self.dbData = dict()
            self.dbData["anaio_ipaddr_local"] = "0.0.0.0"

        # Volatile Data
        self.tcp_client = None
        self.tcp_timeout = 0
        self.enabled = False
        self.enabled_echo = False
        self.override = False
        self.inputs = [self.uid, False, [False * 14]]  # All reserved
        self.outputs = [self.uid, self.enabled, [0]*25]
        self.heartbeat = 0
        self.heartbeat_echo = 0

        # Device Volatiles
        self.anaio_quantity = 1
        self.anaio_heartbeat = 0

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

    def process(self):
        global loop_time
        #print("(7)  Ana. IO Heartbeat: " + str(self.heartbeat) + ", loop time: " + str(loop_time) + " Seconds")
        
        s = time.time()

        self.heartbeat += 1
        if self.heartbeat >= 0xFFFF:
            self.heartbeat = 0
        self.anaio_heartbeat = self.heartbeat

        if self.tcp_timeout >= 5:
            self.tcp_timeout = 0
            self.tcp_client = None

        if self.tcp_client is None:
            self.con_state = False
            if "anaio_ipaddr_local" in self.dbData:
                if self.dbData["anaio_ipaddr_local"] != "0.0.0.0":
                    try:
                        self.tcp_client = mb_tcp_client(self.dbData["anaio_ipaddr_local"], port=502, timeout=1)

                        if self.tcp_client.connect() is True:
                            self.update_faults(Faults.LOSS_OF_COMMS.value, False)  # Clear Fault
                            self.set_state_text(State.CONNECTED)
                            self.con_state = True
                        else:
                            self.update_faults(Faults.LOSS_OF_COMMS.value, True)  # Raise Fault
                            self.set_state_text(State.CONNECTING)
                            self.tcp_client = None
                            return

                    except Exception as e:
                        print("ADAM ANAIO: " + str(e))
                        self.update_faults(Faults.LOSS_OF_COMMS.value, True)
                        self.set_state_text(State.CONNECTING)
                else:
                    self.update_faults(Faults.LOSS_OF_COMMS.value, True)  # Raise Fault
                    self.set_state_text(State.CONFIG)
            else:
                self.update_faults(Faults.LOSS_OF_COMMS.value, True)
                self.set_state_text(State.CONFIG)
        else:
            # Analogue Inputs
            try:
                rr = self.tcp_client.read_holding_registers(0, 7, unit=1)

                if rr.isError():
                    self.tcp_timeout += 1
                    return
                else:
                    self.tcp_timeout = 0
                    for x in range(len(rr.registers)):

                        # Do the temperature conversion here. The controller can't know how we wish to represent the data.
                        ana_range = "anaio_input_" + str(x) + "_range"
                        if ana_range in self.dbData:  # Has it been configured?

                            if self.dbData[ana_range] == "None":  # Nope!
                                rr.registers[x] = 0

                            elif self.dbData[ana_range] == ana_range + "_1":  # Option 1: PT100 -50/+150, default
                                rr.registers[x] = round(((rr.registers[x] / 65535) * 200) - 50, 2)

                            elif self.dbData[ana_range] == ana_range + "_2":  # Option 2: PT100 0/+100 (untested)
                                rr.registers[x] = round(((rr.registers[x] / 65535) * 100), 2)

                            elif self.dbData[ana_range] == ana_range + "_3":  # Option 3: PT100 0/+200 (untested)
                                rr.registers[x] = round(((rr.registers[x] / 65535) * 200), 2)

                            elif self.dbData[ana_range] == ana_range + "_4":  # Option 4: PT100 0/+400 (untested)
                                rr.registers[x] = round(((rr.registers[x] / 65535) * 400), 2)

                            elif self.dbData[ana_range] == ana_range + "_5":  # Option 5: PT100 -100/+100 (untested)
                                rr.registers[x] = round(((rr.registers[x] / 65535) * 200) - 100, 2)

                            elif self.dbData[ana_range] == ana_range + "_6":  # Option 6: PT100 -200/+200 (untested)
                                rr.registers[x] = round(((rr.registers[x] / 65535) * 400) - 200, 2)

                            # Reporting
                            if "anaio_alarm_temp_min" in self.dbData and \
                               0 < rr.registers[x] < float(self.dbData["anaio_alarm_temp_min"]):

                                self.update_faults(Faults["RTD" + str(x) + "_FAULT_LIMIT"].value, False)
                                self.update_alarms(Alarms["RTD" + str(x) + "_UPPER_ALARM"].value, False)
                                self.update_warnings(Warnings["RTD" + str(x) + "_UPPER_WARN"].value, False)
                                self.update_warnings(Warnings["RTD" + str(x) + "_LOWER_WARN"].value, False)
                                self.update_alarms(Alarms["RTD" + str(x) + "_LOWER_ALARM"].value, True)

                            elif "anaio_alarm_temp_min" in self.dbData and "anaio_warning_temp_min" in self.dbData and \
                                 float(self.dbData["anaio_alarm_temp_min"]) < rr.registers[x] < float(self.dbData["anaio_warning_temp_min"]):

                                self.update_faults(Faults["RTD" + str(x) + "_FAULT_LIMIT"].value, False)
                                self.update_alarms(Alarms["RTD" + str(x) + "_UPPER_ALARM"].value, False)
                                self.update_warnings(Warnings["RTD" + str(x) + "_UPPER_WARN"].value, False)
                                self.update_warnings(Warnings["RTD" + str(x) + "_LOWER_WARN"].value, True)
                                self.update_alarms(Alarms["RTD" + str(x) + "_LOWER_ALARM"].value, False)

                            elif "anaio_warning_temp_min" in self.dbData and "anaio_warning_temp_max" in self.dbData and \
                                 float(self.dbData["anaio_warning_temp_min"]) < rr.registers[x] < float(self.dbData["anaio_warning_temp_max"]):

                                self.update_faults(Faults["RTD" + str(x) + "_FAULT_LIMIT"].value, False)
                                self.update_alarms(Alarms["RTD" + str(x) + "_UPPER_ALARM"].value, False)
                                self.update_warnings(Warnings["RTD" + str(x) + "_UPPER_WARN"].value, False)
                                self.update_warnings(Warnings["RTD" + str(x) + "_LOWER_WARN"].value, False)
                                self.update_alarms(Alarms["RTD" + str(x) + "_LOWER_ALARM"].value, False)

                            elif "anaio_warning_temp_max" in self.dbData and "anaio_alarm_temp_max" in self.dbData and \
                                 float(self.dbData["anaio_warning_temp_max"]) < rr.registers[x] < float(self.dbData["anaio_alarm_temp_max"]):

                                self.update_faults(Faults["RTD" + str(x) + "_FAULT_LIMIT"].value, False)
                                self.update_alarms(Alarms["RTD" + str(x) + "_UPPER_ALARM"].value, False)
                                self.update_warnings(Warnings["RTD" + str(x) + "_UPPER_WARN"].value, True)
                                self.update_warnings(Warnings["RTD" + str(x) + "_LOWER_WARN"].value, False)
                                self.update_alarms(Alarms["RTD" + str(x) + "_LOWER_ALARM"].value, False)

                            elif "anaio_alarm_temp_max" in self.dbData and "anaio_fault_temp_max" in self.dbData and \
                                 float(self.dbData["anaio_alarm_temp_max"]) < rr.registers[x] < float(self.dbData["anaio_fault_temp_max"]):

                                self.update_faults(Faults["RTD" + str(x) + "_FAULT_LIMIT"].value, False)
                                self.update_alarms(Alarms["RTD" + str(x) + "_UPPER_ALARM"].value, True)
                                self.update_warnings(Warnings["RTD" + str(x) + "_UPPER_WARN"].value, False)
                                self.update_warnings(Warnings["RTD" + str(x) + "_LOWER_WARN"].value, False)
                                self.update_alarms(Alarms["RTD" + str(x) + "_LOWER_ALARM"].value, False)

                            elif "anaio_fault_temp_max" in self.dbData and \
                                 rr.registers[x] > float(self.dbData["anaio_fault_temp_max"]):

                                self.update_faults(Faults["RTD" + str(x) + "_FAULT_LIMIT"].value, True)
                                self.update_alarms(Alarms["RTD" + str(x) + "_UPPER_ALARM"].value, False)
                                self.update_warnings(Warnings["RTD" + str(x) + "_UPPER_WARN"].value, False)
                                self.update_warnings(Warnings["RTD" + str(x) + "_LOWER_WARN"].value, False)
                                self.update_alarms(Alarms["RTD" + str(x) + "_LOWER_ALARM"].value, False)
                                rr.registers[0] = 0

                    # There is little to do beyond accepting IO requests
                    if self.enabled:
                        self.enabled_echo = True
                    else:
                        self.enabled_echo = False

                    if len(self.actions) == 0:
                        self.actions.append(0)  # Dummy

                    # Modify self.outputs
                    self.outputs[2][0] = self.anaio_heartbeat
                    self.outputs[2][1] = 0
                    self.outputs[2][2] = 7
                    self.outputs[2][3] = 0
                    self.outputs[2][4] = rr.registers[0]
                    self.outputs[2][5] = rr.registers[1]
                    self.outputs[2][6] = rr.registers[2]
                    self.outputs[2][7] = rr.registers[3]
                    self.outputs[2][8] = rr.registers[4]
                    self.outputs[2][9] = rr.registers[5]
                    self.outputs[2][10] = rr.registers[6]
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

            except Exception as e:
                print("ADAM ANAIO: " + str(e))
                self.tcp_timeout += 1
                return

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

        # Add user actions to list. Format is [Action, Value]
        for action in form:
            self.actions.append([action, form[action]])

        data = dict()

        if page == self.website + "_(" + str(self.uid) + ")":  # It was a page request for a numbered device (POST)

            # Save all control changes to database
            isButton = True
            for control in form:
                if "anaio_input_override" in form:
                    self.actions.insert(0, Actions.CTRL_OVERRIDE_CHANGE.value)
                    self.override = not self.override
                elif "anaio_input_0_range" in form:
                    self.actions.insert(0, Actions.CTRL_INPUT0_RANGE_CHANGE.value)
                elif "anaio_input_1_range" in form:
                    self.actions.insert(0, Actions.CTRL_INPUT1_RANGE_CHANGE.value)
                elif "anaio_input_2_range" in form:
                    self.actions.insert(0, Actions.CTRL_INPUT2_RANGE_CHANGE.value)
                elif "anaio_input_3_range" in form:
                    self.actions.insert(0, Actions.CTRL_INPUT3_RANGE_CHANGE.value)
                elif "anaio_input_4_range" in form:
                    self.actions.insert(0, Actions.CTRL_INPUT4_RANGE_CHANGE.value)
                elif "anaio_input_5_range" in form:
                    self.actions.insert(0, Actions.CTRL_INPUT5_RANGE_CHANGE.value)
                elif "anaio_input_6_range" in form:
                    self.actions.insert(0, Actions.CTRL_INPUT6_RANGE_CHANGE.value)

                isButton = False

                if "anaio_ipaddr_local" == control:
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

            # Clear old lists
            #self.actions = []

            # Controller Information
            mod_data["anaio_name"] = self.name
            mod_data["anaio_man"] = self.manufacturer
            mod_data["anaio_fwver"] = self.version
            mod_data["anaio_serno"] = self.serial
            mod_data["anaio_constate"] = str(self.con_state).capitalize()
            mod_data["anaio_data"] = self.outputs[2]
            mod_data["anaio_override"] = self.override
            mod_data["anaio_enablestate"] = self.enabled

            mod_data.update(self.dbData)  # I'm appending the dict here so we don't save unnecessary
            
            return [SET_PAGE_ACK, mod_data]  # data to the database.

        else:
            return [SET_PAGE_ACK, ('OK', 200)]
            
    def get_page(self):
        routes = [self.website + "_(" + str(self.uid) + ")/data"]  # JSON Data: FlexMod_test_(<uid>)/data/
        page = [self.website + "_(" + str(self.uid) + ")", routes]  # HTML content: FlexMod_test_(<uid>).html
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
            print("ADAM ANAIO: " + str(e))
          
        try:
            if tx_msg is not None:
                queue[0].put(tx_msg, block=True)
                
        except Exception as e:
            print("ADAM ANAIO: " + str(e))
            
 
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


# Enums
class Warnings(Enum):
    NONE = 0                                                                                        # "Warning: Analogue IO - No Warning present"
    RTD0_LOWER_WARN = 1                                                                             # "Warning: Analogue IO - RTD0 Temp below threshold"
    RTD1_LOWER_WARN = 2                                                                             # "Warning: Analogue IO - RTD1 Temp below threshold"
    RTD2_LOWER_WARN = 3                                                                             # "Warning: Analogue IO - RTD2 Temp below threshold"
    RTD3_LOWER_WARN = 4                                                                             # "Warning: Analogue IO - RTD3 Temp below threshold"
    RTD4_LOWER_WARN = 5                                                                             # "Warning: Analogue IO - RTD4 Temp below threshold"
    RTD5_LOWER_WARN = 6                                                                             # "Warning: Analogue IO - RTD5 Temp below threshold"
    RTD6_LOWER_WARN = 7                                                                             # "Warning: Analogue IO - RTD6 Temp below threshold"

    RTD0_UPPER_WARN = 8                                                                             # "Warning: Analogue IO - RTD0 Temp above threshold"
    RTD1_UPPER_WARN = 9                                                                             # "Warning: Analogue IO - RTD1 Temp above threshold"
    RTD2_UPPER_WARN = 10                                                                            # "Warning: Analogue IO - RTD2 Temp above threshold"
    RTD3_UPPER_WARN = 11                                                                            # "Warning: Analogue IO - RTD3 Temp above threshold"
    RTD4_UPPER_WARN = 12                                                                            # "Warning: Analogue IO - RTD4 Temp above threshold"
    RTD5_UPPER_WARN = 13                                                                            # "Warning: Analogue IO - RTD5 Temp above threshold"
    RTD6_UPPER_WARN = 14                                                                            # "Warning: Analogue IO - RTD6 Temp above threshold"


class Alarms(Enum):
    NONE = 0                                                                                        # "Alarm: Analogue IO - No Alarm present"
    RTD0_LOWER_ALARM = 1                                                                            # "Alarm: Analogue IO - RTD0 Temp below threshold"
    RTD1_LOWER_ALARM = 2                                                                            # "Alarm: Analogue IO - RTD1 Temp below threshold"
    RTD2_LOWER_ALARM = 3                                                                            # "Alarm: Analogue IO - RTD2 Temp below threshold"
    RTD3_LOWER_ALARM = 4                                                                            # "Alarm: Analogue IO - RTD3 Temp below threshold"
    RTD4_LOWER_ALARM = 5                                                                            # "Alarm: Analogue IO - RTD4 Temp below threshold"
    RTD5_LOWER_ALARM = 6                                                                            # "Alarm: Analogue IO - RTD5 Temp below threshold"
    RTD6_LOWER_ALARM = 7                                                                            # "Alarm: Analogue IO - RTD6 Temp below threshold"

    RTD0_UPPER_ALARM = 8                                                                            # "Alarm: Analogue IO - RTD0 Temp above threshold"
    RTD1_UPPER_ALARM = 9                                                                            # "Alarm: Analogue IO - RTD1 Temp above threshold"
    RTD2_UPPER_ALARM = 10                                                                           # "Alarm: Analogue IO - RTD2 Temp above threshold"
    RTD3_UPPER_ALARM = 11                                                                           # "Alarm: Analogue IO - RTD3 Temp above threshold"
    RTD4_UPPER_ALARM = 12                                                                           # "Alarm: Analogue IO - RTD4 Temp above threshold"
    RTD5_UPPER_ALARM = 13                                                                           # "Alarm: Analogue IO - RTD5 Temp above threshold"
    RTD6_UPPER_ALARM = 14                                                                           # "Alarm: Analogue IO - RTD6 Temp above threshold"


class Faults(Enum):
    NONE = 0                                                                                        # "Fault: Analogue IO - No Fault present"
    CONFIG = 1                                                                                      # "Fault: Analogue IO - Configuration"
    LOSS_OF_COMMS = 2                                                                               # "Fault: Analogue IO - Loss of Comms"
    IO_TIMEOUT = 3
    RTD0_FAULT_LIMIT = 4                                                                            # "Fault: Analogue IO - RTD0 Temp out of range"
    RTD1_FAULT_LIMIT = 5                                                                            # "Fault: Analogue IO - RTD1 Temp out of range"
    RTD2_FAULT_LIMIT = 6                                                                            # "Fault: Analogue IO - RTD2 Temp out of range"
    RTD3_FAULT_LIMIT = 7                                                                            # "Fault: Analogue IO - RTD3 Temp out of range"
    RTD4_FAULT_LIMIT = 8                                                                            # "Fault: Analogue IO - RTD4 Temp out of range"
    RTD5_FAULT_LIMIT = 9                                                                            # "Fault: Analogue IO - RTD5 Temp out of range"
    RTD6_FAULT_LIMIT = 10                                                                           # "Fault: Analogue IO - RTD6 Temp out of range"


class Actions(Enum):
    NONE = 0
    IP_ADDRESS_CHANGE = 1
    CTRL_OVERRIDE_CHANGE = 2
    CTRL_INPUT0_RANGE_CHANGE = 3
    CTRL_INPUT1_RANGE_CHANGE = 4
    CTRL_INPUT2_RANGE_CHANGE = 5
    CTRL_INPUT3_RANGE_CHANGE = 6
    CTRL_INPUT4_RANGE_CHANGE = 7
    CTRL_INPUT5_RANGE_CHANGE = 8
    CTRL_INPUT6_RANGE_CHANGE = 9


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