# Example Module.py, Launches child and initialises the base class
import sys
from datetime import datetime
from enum import Enum
import random
from threading import Thread, Event
import time

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

        # Module Data
        self.uid = uid                                                                              # A unique identifier passed from the HMI via a mangled URL
        self.icon = "/static/images/mspLogo.png"
        self.name = "MSP Logo"
        self.module_type = ModTypes.UNDEFINED.value
        self.manufacturer = "MSP"
        self.model = ""
        self.options = ""
        self.version = "-"
        self.serial = random.randint(1, 65535)
        self.website = "/Mod/FlexMod_MspLogo"                                            # This is the template name itself
        self.address = 0
        self.inputs = []
        self.outputs = []
        self.heartbeat = 0
        self.heartbeat_echo = 0
        
        # Run state
        self.con_state = False
        self.state = ""
        self.set_state_text(State.NONE)
        
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

        print("Starting " + self.name + " with UID " + str(self.uid))

    def process(self):
        global loop_time
        #print("(25)MSP Logo Heartbeat: " + str(self.heartbeat) + ", loop time: " + str(loop_time) + " Seconds")
        
        s = time.time()
        
        self.heartbeat += 1
        if self.heartbeat >= 0xFFFF:
            self.heartbeat = 0
            
        loop_time = time.time() - s

    def set_inputs(self, inputs):
        for module in inputs:
            if module[0] == self.serial:
                self.inputs = module[1:]
        return [SET_INPUTS_ACK]

    def get_outputs(self):
        outputs = [self.serial]
        return [GET_OUTPUTS_ACK, self.outputs]

    def set_page(self, page, form):                                                                       # Respond to GET/POST requests
        
        if page == self.website + "_(" + str(self.uid) + ")":
            self.dbData[control] = form[control]
        elif page == (self.website + "_(" + str(self.uid) + ")/data"):
            mod_data = dict()   # No data
            return [SET_PAGE_ACK, mod_data]
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
    
    def kill(self):
        pass


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
            print("MSP LOGO: " + str(e))
          
        try:
            if tx_msg is not None:
                queue[0].put(tx_msg, block=True)
                
        except Exception as e:
            print("MSP LOGO: " + str(e))
            
 
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
    CLEAR = 0
    WARN1 = 1
    WARN2 = 2
    WARN3 = 3
    WARN4 = 4
    WARN5 = 5


class Alarms(Enum):
    CLEAR = 0
    ALARM1 = 1
    ALARM2 = 2
    ALARM3 = 3
    ALARM4 = 4
    ALARM5 = 5


class Faults(Enum):
    CLEAR = 0
    FAULT1 = 1
    FAULT2 = 2
    FAULT3 = 3
    FAULT4 = 4
    FAULT5 = 5


class Actions(Enum):
    CLEAR = 0
    ACTION1 = 1
    ACTION2 = 2
    ACTION3 = 3
    ACTION4 = 4
    ACTION5 = 5


class State(Enum):
    RESERVED = "Reserved"
    IDLE = "Idle"
    CONFIG = "Configuration"
    CONNECTING = "Connecting"
    CONNECTED = "Connected"
    ACTIVE = "Active"
    WARNING = "Warning"
    ALARM = "Alarm"
    NONE = ""

if __name__ == '__main__':                                                                          # The module must be able to run solo for testing purposes
    pass
