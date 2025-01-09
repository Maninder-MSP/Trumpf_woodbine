# Digital IO Module

# Description
# The Digital IO module services a dedicated dvice having digital inputs and outputs.
# This version of it supports the Advantech ADAM-6060 having 6 Inputs and 6 Outputs, and is
# controlled over Modbus-TCP.

# Versions
# 3.5.24.10.16 - SC - Known good starting point, uses thread.is_alive to prevent module stalling.

import sys
from pymodbus.client.sync import ModbusTcpClient as mb_tcp_client
from threading import Thread, Event
from FlexDB import FlexTinyDB
from enum import Enum
from datetime import datetime
import copy
import time

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
        #super(Module, self).__init__()
        
        # General Module Data
        self.author = "Sophie Coates"
        self.uid = uid                                                                              # A unique identifier passed from the HMI via a mangled URL
        self.icon = "/static/images/DigIO.png"
        self.name = "ADAM-6060"
        self.module_type = ModTypes.DIG_IO.value
        self.module_version = "3.5.24.10.16" 
        self.manufacturer = "Advantech"
        self.model = ""
        self.options = ""
        self.version = "-"
        self.serial = "-"                                                                           # This can be replaced with the device serial number later
        self.website = "/Mod/FlexMod_AdamDigIO"                                                     # This is the template name itself

        # Run state
        self.con_state = False
        self.state = "Idle"
        self.set_state_text(State.IDLE)

        # Non-Volatile Data (Loaded from DB)
        self.dbData = db.fetch_from_db(__name__ + "_(" + str(self.uid) + ")")
        if self.dbData is None:
            self.dbData = dict()
            self.dbData["digio_ipaddr_local"] = "0.0.0.0"

        # Volatile Data
        self.tcp_client = None
        self.tcp_timeout = 0
        self.enabled = False
        self.enabled_echo = False
        self.override = False
        self.inputs = [self.uid, False, [False]*12]                                               # 6 outputs and 6 reserved
        self.outputs = [self.uid, self.enabled, [0]*25]
        self.outputs_debug = [False]*6
        self.outputs_debug_count = [0, 0, 0, 0, 0, 0]
        self.heartbeat = 0
        self.heartbeat_echo = 0

        # Device Volatiles
        self.digio_quantity = 1
        self.digio_heartbeat = 0
        self.digio_input_bits = 0
        self.digio_output_bits = 0

        # Events
        self.warnings = 0
        self.alarms = 0
        self.faults = 0
        self.actions = []

        # HMI data, from which power is derived.
        self.priV = 0                                                                                 # Primary units of AC Voltage and Current
        self.priA = 0
        self.secV = 0                                                                                 # Secondary, for DC units
        self.secA = 0
        self.terV = 0                                                                                 # Tertiary, if a device has a third port, like a PD Hydra
        self.terA = 0

        print("Starting " + self.name + " with UID " + str(self.uid) + " on version " + str(self.module_version))

        # Track Interval usage (GIL interference)
        self.start_time = time.time()
        self.interval_count = 0
        self.max_delay = 0
        self.last_time = 0

    def process(self):
        global loop_time
        #print("(6)  Dig. IO Heartbeat: " + str(self.heartbeat) + ", loop time: " + str(loop_time) + " Seconds")
        
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
            #print("Dig. IO " + str(self.uid) + ": " + str(self.interval_count))
        else:
            pass#print("Dig. IO " + str(self.uid) + ": " + str(self.interval_count) + " events in 1 hour. Maximum gap between events was " + str(self.max_delay) + " seconds.")
            
        
        s = time.time()

        self.heartbeat += 1
        if self.heartbeat >= 0xFFFF:
            self.heartbeat = 0
        self.digio_heartbeat = self.heartbeat
        #print("ADAM HB: " + str(self.heartbeat))
        if self.tcp_timeout >= 5:
            self.tcp_timeout = 0
            self.tcp_client = None

        if self.tcp_client is None:
            self.con_state = False
            if "digio_ipaddr_local" in self.dbData:
                if self.dbData["digio_ipaddr_local"] != "0.0.0.0":
                    try:
                        self.tcp_client = mb_tcp_client(self.dbData["digio_ipaddr_local"], port=502, timeout=1)

                        if self.tcp_client.connect() is True:
                            self.update_faults(Faults.LOSS_OF_COMMS.value, False)               # Clear Fault
                            self.set_state_text(State.CONNECTED)
                            self.con_state = True

                            # Clear the outputs
                            clr_bits = [0, 0, 0, 0, 0, 0]
                            self.tcp_client.write_coils(16, clr_bits, unit=1)

                        else:
                            self.update_faults(Faults.LOSS_OF_COMMS.value, True)                # Raise Fault
                            self.set_state_text(State.CONNECTING)
                            self.tcp_client = None
                            return

                    except Exception as e:
                        print("ADAM DIGIO: " + str(e))
                        self.update_faults(Faults.LOSS_OF_COMMS.value, True)
                        self.set_state_text(State.CONNECTING)
                else:
                    self.update_faults(Faults.LOSS_OF_COMMS.value, True)  # Raise Fault
                    self.set_state_text(State.CONFIG)
            else:
                self.update_faults(Faults.LOSS_OF_COMMS.value, True)
                self.set_state_text(State.CONFIG)
        else:
            # Digital Outputs
            rr = []
            try:
                rr = self.tcp_client.read_coils(16, 6, unit=1)

                if rr.isError():
                    self.tcp_timeout += 1
                    return
                else:
                    self.tcp_timeout = 0

                    change_op_state = False

                    for x in range(6):  # Check if the output states match the requested state

                        if ((rr.bits[x] is True) and (((self.inputs[2][5] >> (x * 2)) & 0x03) != 0x01)) or \
                           ((rr.bits[x] is False) and (((self.inputs[2][5] >> (x * 2)) & 0x03) != 0x00)):
                            if self.outputs_debug_count[x] <= 5:
                                self.outputs_debug_count[x] += 1  # Increment fault counter

                                if ((self.inputs[2][5] >> (x * 2)) & 0x03) == 0x01:
                                    self.digio_output_bits |= (0x01 << (x * 2))
                                    rr.bits[x] = True
                                else:
                                    self.digio_output_bits &= ~(0x03 << (x * 2))
                                    rr.bits[x] = False
                                change_op_state = True
                            else:
                                self.digio_output_bits |= (0x03 << (x * 2))  # Mark output as fault
                                self.update_faults(Faults["IO_OP" + str(x) + "_FAULT"].value, True)
                                self.set_state_text(State.FAULT)
                        else:
                            self.update_faults(Faults["IO_OP" + str(x) + "_FAULT"].value, False)
                            self.outputs_debug_count[x] = 0

                    if change_op_state:
                        self.tcp_client.write_coils(16, rr.bits, unit=1)  # Update on state change only to minimise traffic

            except Exception as e:
                print("ADAM DIGIO: " + str(e))
                self.tcp_timeout += 1
                return

            # Digital Inputs
            rr = []
            try:
                rr = self.tcp_client.read_coils(0, 6, unit=1)

                if rr.isError():
                    self.tcp_timeout += 1
                    return
                else:
                    self.tcp_timeout = 0

                    self.digio_input_bits = 0
                    for x in range(6):  # Invert the input data bits so pulling to ground == True
                        if rr.bits[x] is True:
                            self.digio_input_bits = self.digio_input_bits | (1 << x)

            except Exception as e:
                print("ADAM DIGIO: " + str(e))
                self.tcp_timeout += 1
                return

            # There is little to do beyond accepting IO requests
            if self.enabled:
                self.enabled_echo = True
            else:
                self.enabled_echo = False

            if len(self.actions) == 0:
                self.actions.append(0)  # Dummy

            # Modify self.outputs
            self.outputs[2][0] = self.digio_heartbeat
            self.outputs[2][1] = 0
            self.outputs[2][2] = 6
            self.outputs[2][3] = 6
            self.outputs[2][4] = self.digio_input_bits & 0xFF
            self.outputs[2][5] = self.digio_output_bits & 0xFFFF
            self.outputs[2][6] = (self.digio_input_bits >> 8) & 0xFF
            self.outputs[2][7] = (self.digio_output_bits >> 16) & 0xFFFF
            self.outputs[2][8] = (self.digio_input_bits >> 16) & 0xFF
            self.outputs[2][9] = (self.digio_output_bits >> 24) & 0xFFFF
            self.outputs[2][10] = (self.digio_input_bits >> 24) & 0xFF
            self.outputs[2][11] = (self.digio_output_bits >> 32) & 0xFFFF
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

    def set_page(self, page, form):                                                                       # Respond to GET/POST requests

        # Add user actions to list. Format is [Action, Value]
        #for action in page[1].form:
        #    self.actions.append([action, page[1].form[action]])

        data = dict()

        if page == self.website + "_(" + str(self.uid) + ")":                                    # It was a page request for a numbered device (POST)

            # Save all control changes to database
            isButton = True
            for control in form:
                if "digio_output_0_test" in form:
                    if (self.inputs[2][5] >> 0) & 0x03 == 0x01:
                        self.inputs[2][5] &= ~(0x03 << 0)
                    elif (self.inputs[2][5] >> 0) & 0x03 == 0x00:
                        self.inputs[2][5] |= (0x01 << 0)
                    self.actions.insert(0, Actions.CTRL_OUTPUT0_CHANGE.value)
                elif "digio_output_1_test" in form:
                    if (self.inputs[2][5] >> 2) & 0x03 == 0x01:
                        self.inputs[2][5] &= ~(0x03 << 2)
                    elif (self.inputs[2][5] >> 2) & 0x03 == 0x00:
                        self.inputs[2][5] |= (0x01 << 2)
                    self.actions.insert(0, Actions.CTRL_OUTPUT1_CHANGE.value)
                elif "digio_output_2_test" in form:
                    if (self.inputs[2][5] >> 4) & 0x03 == 0x01:
                        self.inputs[2][5] &= ~(0x03 << 4)
                    elif (self.inputs[2][5] >> 4) & 0x03 == 0x00:
                        self.inputs[2][5] |= (0x01 << 4)
                    self.actions.insert(0, Actions.CTRL_OUTPUT2_CHANGE.value)
                elif "digio_output_3_test" in form:
                    if (self.inputs[2][5] >> 6) & 0x03 == 0x01:
                        self.inputs[2][5] &= ~(0x03 << 6)
                    elif (self.inputs[2][5] >> 6) & 0x03 == 0x00:
                        self.inputs[2][5] |= (0x01 << 6)
                    self.actions.insert(0, Actions.CTRL_OUTPUT3_CHANGE.value)
                elif "digio_output_4_test" in form:
                    if (self.inputs[2][5] >> 8) & 0x03 == 0x01:
                        self.inputs[2][5] &= ~(0x03 << 8)
                    elif (self.inputs[2][5] >> 8) & 0x03 == 0x00:
                        self.inputs[2][5] |= (0x01 << 8)
                    self.actions.insert(0, Actions.CTRL_OUTPUT4_CHANGE.value)
                elif "digio_output_5_test" in form:
                    if (self.inputs[2][5] >> 10) & 0x03 == 0x01:
                        self.inputs[2][5] &= ~(0x03 << 10)
                    elif (self.inputs[2][5] >> 10) & 0x03 == 0x00:
                        self.inputs[2][5] |= (0x01 << 10)
                    self.actions.insert(0, Actions.CTRL_OUTPUT5_CHANGE.value)
                elif "digio_output_override" in form:
                    self.actions.insert(0, Actions.CTRL_OVERRIDE_CHANGE.value)
                    self.override = not self.override
                else:
                    isButton = False

                    if "digio_ipaddr_local" == control:
                        self.actions.insert(0, Actions.IP_ADDRESS_CHANGE.value)

                    self.dbData[control] = form[control]                                    # Input or drop-down form states are stored here

            if isButton is False:  # Button press states are to be acted upon only, not stored
                self.save_to_db()

            # Let's just record the last 10 user interations for now
            if len(self.actions) >= 10:
                self.actions.pop()
            
            return [SET_PAGE_ACK]

        elif page == (self.website + "_(" + str(self.uid) + ")/data"):                           # It was a json data fetch quest (POST)

            mod_data = dict()

            # Clear old lists
            #self.actions = []

            # Controller Information
            mod_data["digio_name"] = self.name
            mod_data["digio_man"] = self.manufacturer
            mod_data["digio_fwver"] = self.version
            mod_data["digio_serno"] = self.serial
            mod_data["digio_constate"] = str(self.con_state).capitalize()
            mod_data["digio_data"] = self.outputs[2]
            mod_data["digio_override"] = self.override
            mod_data["digio_enablestate"] = self.enabled

            mod_data.update(self.dbData)  # I'm appending the dict here so we don't save unnecessary
            
            return [SET_PAGE_ACK, mod_data]  # data to the database.

        else:
            return [SET_PAGE_ACK, ('OK', 200)]                                                                    # Return the data to be jsonified

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
        
        try:
            db.save_to_db(__name__ + "_(" + str(self.uid) + ")", self.dbData)
        except Exception as e:
            print("ADAM DIGIO: " + str(e))
            print("Unable to save record, may already exist?")  # Todo find error code from tinydb

    def kill(self):
        self.stop.set()                                                                             # Tells the timer thread to cease operation


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
            print("ADAM DIGIO: " + str(e))
          
        try:
            if tx_msg is not None:
                queue[0].put(tx_msg, block=True)
                
        except Exception as e:
            print("ADAM DIGIO: " + str(e))
            
 
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
    
    
class Warnings(Enum):
    NONE = 0                                                                                        # "Warning: Digital IO - No Fault present"


class Alarms(Enum):
    NONE = 0                                                                                        # "Alarm: Digital IO - No Alarm present"


class Faults(Enum):
    NONE = 0                                                                                        # "Fault: Digital IO - No Fault present"
    CONFIG = 1                                                                                      # "Fault: Digital IO - Configuration"
    LOSS_OF_COMMS = 2                                                                               # "Fault: Digital IO - Loss of Comms"
    IO_OP0_FAULT = 3                                                                                # "Fault: Digital IO - Output 0"
    IO_OP1_FAULT = 4                                                                                # "Fault: Digital IO - Output 1"
    IO_OP2_FAULT = 5                                                                                # "Fault: Digital IO - Output 2"
    IO_OP3_FAULT = 6                                                                                # "Fault: Digital IO - Output 3"
    IO_OP4_FAULT = 7                                                                                # "Fault: Digital IO - Output 4"
    IO_OP5_FAULT = 8                                                                                # "Fault: Digital IO - Output 5"


class Actions(Enum):
    NONE = 0
    IP_ADDRESS_CHANGE = 1
    CTRL_OVERRIDE_CHANGE = 2
    CTRL_OUTPUT0_CHANGE = 3
    CTRL_OUTPUT1_CHANGE = 4
    CTRL_OUTPUT2_CHANGE = 5
    CTRL_OUTPUT3_CHANGE = 6
    CTRL_OUTPUT4_CHANGE = 7
    CTRL_OUTPUT5_CHANGE = 8


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

    
if __name__ == '__main__':                                                                          # The module must be able to run solo for testing purposes
    pass