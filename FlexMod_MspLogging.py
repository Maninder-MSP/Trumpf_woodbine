# FlexMod_MspLogging.py, Local (to file) and Remote (to server) logging facility
# Now delegated to an external application that we can update / replace on the fly

from pymodbus.client.sync import ModbusTcpClient as mb_tcp_client
from threading import Thread, Event
from FlexDB import FlexTinyDB
from enum import Enum
import time

import os
import sys
from pathlib import Path
from multiprocessing import Process, Queue
import random

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
        #super(Module, self).__init__()

        # General Module Data
        self.author = "Sophie Coates"
        self.uid = uid
        self.icon = "/static/images/Logging.png"
        self.name = "MSP Logging"
        self.module_type = ModTypes.LOGGING.value
        self.manufacturer = "MSP"
        self.model = ""
        self.options = ""
        self.version = "-"
        self.serial = "-"
        self.website = "/Mod/FlexMod_MspLogging"

        # Run state
        self.con_state = False
        self.state = State.IDLE
        self.set_state_text(self.state)

        # Non-Volatile Data (Loaded from DB)
        self.dbData = db.fetch_from_db(__name__ + "_(" + str(self.uid) + ")")
        if self.dbData is None:
            self.dbData = dict()
            self.dbData["logging_data_store"] = str("C:/Users/End User/Documents/Flex3/logs")                   # Default Directory
            self.dbData["logging_data_server"] = str("wss://bess-be.com")                                                          # Remote Server Domain

        # Volatile Data
        self.tcp_client = None
        self.tcp_timeout = 0
        self.enabled = True
        self.enabled_echo = False
        self.override = False
        self.inputs = [self.uid, False]
        self.outputs = [self.uid, self.enabled, [0]*25]
        self.heartbeat = 0
        self.heartbeat_echo = 0
        
        self.logging_process = None
        self.logging_process_inQ = None 
        self.logging_process_outQ = None
        self.control_system_name = ""
        self.logging_heartbeat = 0
        self.logging_local_state = 0
        self.logging_local_interval = 1                                                             # Log every few seconds
        self.logging_local_duration = 900                                                          # Save Log file every hour (3600 seconds)
        self.logging_local_max_files = 8760                                                         # Save 1 Year of data (8760 hours). Oldest file is deleted.
        self.logging_local_interval_state = 0
        self.logging_local_interval_counter = 0
        self.logging_local_duration_counter = 0
        self.logging_local_buffer_count = 0                                                         # Number of logs awaiting save to file
        self.logging_local_file_count = 0                                                           # Number of files currently stored
        self.logging_local_trigger = 0
        self.logging_local_start = 0

        self.logging_remote_interval = 1                                                            # Log every few seconds
        self.logging_remote_interval_state = 0
        self.logging_remote_interval_counter = 0
        self.logging_remote_length = 5                                                              # Number of logs to normally send in one JSON packet.
        self.logging_remote_length_counter = 0
        self.logging_remote_trigger = 0
        self.logging_remote_buffer_count = 0
        self.logging_remote_log_packet = dict()

        self.log_fault = False

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

        print("Starting " + self.name + " with UID " + str(self.uid))
        self.countdown = 10
        
        # Track Interval usage (GIL interference)
        self.start_time = time.time()
        self.interval_count = 0
        self.max_delay = 0
        self.last_time = 0

    def twos_comp_to_int(self, twoC):
        # calculate int from two's compliment input
        return twoC if not twoC & 0x8000 else -(0xFFFF - twoC + 1)

    def twos_comp_to_int32(self, twoC):
        # calculate int from two's compliment input
        return twoC if not twoC & 0x80000000 else -(0xFFFFFFFF - twoC + 1)

    def int_to_twos_comp(self, num):
        return num if num >= 0 else (0xFFFF + num + 1)

    def int_to_twos_comp(self, num):
        return num if num >= 0 else (0xFFFFFFFF + num + 1)
    
    def log_to_server(self, data, project):
        try:
            self.socket.send_data(data)
            print("Sending log data to server as " + project)
            self.update_alarms(Alarms.SERVER_ERROR.value, False)
        except:
            print("Communication fault when sending log data to server")
            self.update_alarms(Alarms.SERVER_ERROR.value, True)
    
    def process(self):
        global loop_time
        #print("(23) Logging Heartbeat: " + str(self.heartbeat) + ", loop time: " + str(loop_time) + " Seconds")
        
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
            
        
        s = time.time()

        self.heartbeat += 1
        if self.heartbeat >= 0xFFFF:
            self.heartbeat = 0

        self.logging_heartbeat = self.heartbeat

        if self.enabled:  # The controller can start and stop the process
            self.enabled_echo = True
            
            # We check if the logging application exists.
            try:
                if os.path.isfile(r"C:\Users\End User\Documents\Flex3\FlexLogger.py"):
                    # Check if the logging module has been loaded, and load if necessary
                    if "FlexLogger" not in sys.modules:
                        
                        # Create a queue for passing data to the remote process
                        self.logging_process_inQ = Queue(maxsize=1)
                        self.logging_process_outQ = Queue(maxsize=1)
                        
                        # Import the new process code
                        flex_logger = __import__("FlexLogger")
                        
                        # Give it an ID (this is used in Flex3 but not strictly necessary for external processes
                        process_id = random.randint(1, 65535)
                        
                        # Create and start the process
                        self.logging_process = Process(target=flex_logger.driver, args=([self.logging_process_inQ, self.logging_process_outQ], process_id))
                        self.logging_process.start()
                    else:
                        try:    
                            # Synchronise the process
                            tx_msg = [SYNC] 
                            self.logging_process_outQ.put(tx_msg)
                        except Exception as e:
                            print("Error when sending SYNC to Flex Logger: " + str(e))
                        
                        try:
                            # Update static info and user configuration
                            config = [0] * 20
                            
                            module = 1  # Controller
                            dev = 0     # There's only one ever installed
                            system_name = ""
                            for char in range(16):
                                # We have to pull apart characters and form the 32-char string name.
                                system_name += chr((self.inputs[module][dev][2][25 + char] >> 8) & 0xFF)
                                system_name += chr((self.inputs[module][dev][2][25 + char]) & 0xFF)
                            
                            self.control_system_name = system_name
                            
                            system_type = self.inputs[module][dev][2][42]
                            
                            latitude = self.twos_comp_to_int32((self.inputs[module][dev][2][44] << 16) + self.inputs[module][dev][2][45]) / 1000000
                            longitude = self.twos_comp_to_int32((self.inputs[module][dev][2][47] << 16) + self.inputs[module][dev][2][48]) / 1000000
                            
                            # Pass on the user HMI settings
                            config[0] = self.dbData["logging_data_store"]
                            config[1] = self.dbData["logging_data_server"]
                            config[2] = self.logging_local_interval
                            config[3] = self.logging_local_duration
                            config[4] = self.logging_local_max_files
                            config[5] = self.logging_remote_interval
                            config[6] = self.logging_remote_length
                            config[7] = self.override
                            config[8] = "" # spare
                            config[9] = "" # spare
                            
                            # Pass on system parameters that won't be accessible over SCADA
                            config[10] = [system_name]
                            config[11] = [system_type]
                            config[12] = [latitude]
                            config[13] = [longitude]
                            config[14] = ""
                            config[15] = ""
                            config[16] = ""
                            config[17] = ""
                            config[18] = ""
                            config[19] = ""
                            
                            tx_msg = [SET_INPUTS, config]                                           
                            self.logging_process_outQ.put(tx_msg)
                            rx_msg = self.logging_process_inQ.get()                    
                            
                            if isinstance(rx_msg, list):
                                if rx_msg[0] == SET_INPUTS_ACK:
                                    # No return data at present for this command
                                    pass  
                            
                        except Exception as e:
                            print("Error when sending SET INPUTS to Flex Logger: " + str(e))
                            
                        try:
                            # Retrieve and update logging statistics
                            tx_msg = [GET_OUTPUTS]
                            self.logging_process_outQ.put(tx_msg)
                            rx_msg = self.logging_process_inQ.get()
                            
                            if isinstance(rx_msg, list):
                                if rx_msg[0] == GET_OUTPUTS_ACK:
                                    self.logging_local_buffer_count = rx_msg[1][0]
                                    self.logging_local_file_count = rx_msg[1][1]
                                    self.logging_remote_buffer_count = rx_msg[1][2]
                                    
                        except Exception as e:
                            print("Error when sending GET OUTPUTS to Flex Logger: " + str(e))
                        
                else:
                    # print("FlexLogger Process is not running")
                    if "FlexLogger" in sys.modules:
                        self.logging_process.terminate()
                        del sys.modules["FlexLogger"]
                        
            except Exception as e:
                print("Logging: " + str(e))


            if len(self.actions) == 0:
                self.actions.append(0)  # Dummy

            # Modify self.outputs
            self.outputs[2][0] = self.logging_heartbeat
            self.outputs[2][1] = self.logging_local_state
            self.outputs[2][2] = 0
            self.outputs[2][3] = self.logging_local_interval
            self.outputs[2][4] = self.logging_local_duration
            self.outputs[2][5] = self.logging_local_trigger
            self.outputs[2][6] = 0
            self.outputs[2][7] = 0
            self.outputs[2][8] = self.logging_local_buffer_count                                                               # Remember to clear the buffer when it is written to file
            self.outputs[2][9] = self.logging_local_file_count
            self.outputs[2][10] = self.logging_local_max_files
            self.outputs[2][11] = 0
            self.outputs[2][12] = 0
            self.outputs[2][13] = self.logging_remote_interval
            self.outputs[2][14] = self.logging_remote_length
            self.outputs[2][15] = self.logging_remote_trigger
            self.outputs[2][16] = 0
            self.outputs[2][17] = 0
            self.outputs[2][18] = self.logging_remote_buffer_count + 1
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
        if inputs[0] == self.uid:
            self.inputs = inputs[2:]  # Save all module data from the controller onwards

            #self.enabled = inputs[1]
        return [SET_INPUTS_ACK, self.inputs]   

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

                # No buttons to process yet - TODO: Further work required to typecheck input
                if "logging_data_store" in form:
                    self.dbData[control] = str(form[control].replace("\\", "/"))            # Platform-friendly slashes
                    isButton = False

                if "logging_data_server" in form:
                    self.dbData[control] = str(form[control])
                    isButton = False

                if "logging_interval_local" in form:
                    self.logging_local_interval = int(form[control])
                    self.dbData[control] = form[control]
                    isButton = False

                if "logging_duration_local" in form:
                    self.logging_local_duration = int(form[control])
                    self.dbData[control] = form[control]
                    isButton = False

                if "logging_max_files_local" in form:
                    self.logging_local_max_files = int(form[control])
                    self.dbData[control] = form[control]
                    isButton = False

                if "logging_interval_remote" in form:
                    self.logging_remote_interval = int(form[control])
                    self.dbData[control] = form[control]
                    isButton = False

                if "logging_length_remote" in form:
                    self.logging_remote_length = int(form[control])
                    self.dbData[control] = form[control]
                    isButton = False

                if "logging_override" in form:
                    self.actions.insert(0, Actions.CTRL_OVERRIDE_CHANGE.value)
                    self.override = not self.override

                else:
                    isButton = False

                    if "logging_data_store" == control:
                        self.actions.insert(0, Actions.LOCAL_STORE_CHANGE.value)

                    if "logging_data_server" == control:
                        self.actions.insert(0, Actions.REMOTE_STORE_CHANGE.value)

                    self.dbData[control] = form[control]

            if isButton is False:  # Button press states are to be acted upon only, not stored
                self.save_to_db()
            
            return [SET_PAGE_ACK]

        elif page == (self.website + "_(" + str(self.uid) + ")/data"):  # It was a json data fetch quest (POST)

            mod_data = dict()

            # Controller Information
            mod_data["logging_name"] = self.name
            mod_data["logging_man"] = self.manufacturer
            mod_data["logging_fwver"] = self.version
            mod_data["logging_serno"] = self.serial
            mod_data["logging_constate"] = str(self.con_state).capitalize()
            mod_data["logging_enablestate"] = self.enabled
            mod_data["logging_data"] = self.outputs[2]

            mod_data["logging_override"] = self.override

            mod_data.update(self.dbData)                                                        # I'm appending the dict here so we don't save unnecessary
            
            return [SET_PAGE_ACK, mod_data]  # data to the database.
        else:
            return [SET_PAGE_ACK, ('OK', 200)]                                                                           # Return the data to be jsonified

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
            print("Logging: " + str(e))
          
        try:
            if tx_msg is not None:
                queue[0].put(tx_msg, block=True)
                
        except Exception as e:
            print("Logging: " + str(e))
            
 
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

    SCADA = 22      # Add option to select Protocol within module?
    LOGGING = 23
    CLIENT = 24
    UNDEFINED = 25
    
    
    def kill(self):
        self.stop.set()


# Enums # TODO
class Warnings(Enum):
    NONE = 0                                                                                        # "Warning: Analogue IO - No Warning present"


class Alarms(Enum):
    NONE = 0                                                                                        # "Alarm: Analogue IO - No Alarm present"
    STORAGE_ERROR = 1
    SERVER_ERROR = 2


class Faults(Enum):
    NONE = 0                                                                                        # Fault: Analogue IO - No Fault present
    CONFIG = 1                                                                                      # Fault: Analogue IO - Configuration
    LOSS_OF_COMMS = 2                                                                               # Fault: Analogue IO - Loss of Comms
    IO_TIMEOUT = 3


class Actions(Enum):
    NONE = 0
    IP_ADDRESS_CHANGE = 1
    CTRL_OVERRIDE_CHANGE = 2
    LOCAL_STORE_CHANGE = 3
    REMOTE_STORE_CHANGE = 4


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
