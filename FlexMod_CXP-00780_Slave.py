# FlexMod_CXP-00780_Slave.py, Client Module - HHT Lynus
# Sorry for the wording - Client means customer here, but this code is intended for the two Flex-1000s at Lynas, not the Site Controller.
import sys
from pymodbus.client.sync import ModbusTcpClient as mb_tcp_client
from threading import Thread, Event
from FlexDB import FlexTinyDB
from enum import Enum

import copy
from datetime import datetime
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
        super(Module, self).__init__()

        # General Module Data
        self.author = "Sophie Coates"
        self.uid = uid
        self.icon = "/static/images/Client.png"
        self.name = "CXP-00780 Slave"
        self.module_type = ModTypes.CLIENT.value
        self.manufacturer = "MSP"
        self.model = ""
        self.options = ""
        self.version = "-"
        self.serial = "-"
        self.website = "/Mod/FlexMod_CXP-00780_Slave"

        # Run state
        self.con_state = False
        self.state = State.IDLE
        self.set_state_text(self.state)

        # Non-Volatile Data (Loaded from DB)
        self.dbData = db.fetch_from_db(__name__ + "_(" + str(self.uid) + ")")
        if self.dbData is None:
            self.dbData = dict()

        # Volatile Data
        self.tcp_client = None
        self.tcp_timeout = 0
        self.enabled = True
        self.enabled_echo = False
        self.inputs = [self.uid, False]
        self.outputs = [self.uid, self.enabled, [0] * 25]
        self.heartbeat = 0
        self.heartbeat_echo = 0
        
        self.system_enabled = False
        self.system_status_text = "[0] System Idle"
        self.control_real_power_command = 0

        self.bank_reset_enabled = False
        self.battery_estop_interlocks_enabled = False
        self.battery_enable = False
        self.battery_enabled = False
        self.battery_soc = 0
        self.battery_dcbus_power = 0
        self.battery_max_charge_power = 0
        self.battery_max_discharge_power = 0
        self.battery_avg_cell_voltage = 0
        self.battery_max_charge_soc = 0
        self.battery_min_discharge_soc = 0
        self.battery_charging_state = 0
        self.battery_rolloff_state = 0
        self.battery_warnings = 0
        self.battery_alarms = 0
        self.battery_faults = 0

        self.ac_meter_grid_power = 0
        self.ac_meter_grid_power_kva = 0
        self.ac_meter_grid_power_echo = 0
        self.ac_meter_drift_timeout = 0

        self.client_enabled = False
        self.client_heartbeat = 0
        self.client_state = 0
        self.client_peak_shaving_limit = 0
        self.client_state_timeout = 0

        self.peak_time = False

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

        # Track Interval usage (GIL interference)
        self.start_time = time.time()
        self.interval_count = 0
        self.max_delay = 0
        self.last_time = 0

    def twos_comp_to_int(self, twoC):
        # calculate int from two's compliment input
        return twoC if not twoC & 0x8000 else -(0xFFFF - twoC + 1)

    def int_to_twos_comp(self, num):
        return num if num >= 0 else (0xFFFF + num + 1)

    def get_io(self, control):
        uid = int(str(control).split("UID ")[1].split(" :")[0])
        if "Input" in control:
            io = int(str(control).split("Input ")[1])
        elif "Output" in control:
            io = int(str(control).split("Output ")[1])
        return uid, io

    def process(self):
        global loop_time
        #print("(24)  Client Heartbeat: " + str(self.heartbeat) + ", loop time: " + str(loop_time) + " Seconds")
        print("Heartbeat: " + str(self.heartbeat) + "        ", end = '\r')

        # Calculate successful polls over an hour
        if time.time() - self.start_time < 3600:    # Record for an hour after starting
        
            # Calculate delay between polls
            time_now = time.time()
            delay = time_now - self.last_time
            
            if self.last_time > 0:
                if self.last_time > 0:
                    if delay > self.max_delay:
                        self.max_delay = delay
            self.last_time = time_now
        
            self.interval_count += 1
            #print("Client " + str(seslf.uid) + ": " + str(self.interval_count))
        else:
            #print("Client " + str(self.uid) + ": " + str(self.interval_count) + " events in 1 hour. Maximum gap between events was " + str(self.max_delay) + " seconds.")
            pass

        self.heartbeat += 1
        if self.heartbeat >= 0xFFFF:
            self.heartbeat = 0

        if len(self.inputs) < 25:
            return

        # Idle state, not enabled
        if self.client_state == 0:
            if self.system_enabled:
                self.system_status_text = "[0] System Enabled"
                self.client_state = 1
            else:
                self.enabled_echo = False
                self.system_status_text = "[0] System Idle"

        # Enable power to the Battery RMSCs. For older systems this may be 5 relays (Individual E-Stop Interlocks), on newer systems it's just one (RMSC Power).
        elif self.client_state == 1:

            if self.system_enabled:
                if self.bank_reset_enabled:     #
                    self.system_status_text = "[1] Battery RMSCs Enabled"
                    self.client_state_timeout = 0
                    self.client_state = 2
                else:
                    if self.client_state_timeout < 10:
                        self.client_state_timeout += 1
                    else:  # Sit here until fixed and the enable state is cycled
                        self.system_status_text = "[1] Could not enable Battery RMSCs"

            else:
                self.system_status_text = "[1] Disabling Battery RMSCs"  # In truth we're currently leaving them enabled
                self.client_state = 0

        # No Battery contacts to close because the KP-MCs for each Flex-1000 are being enabled by the Site Controller

        # There's no Inverter either as they're also being enabled by the site controller, "CXP-00780 Master"

        # Run the Client application, if specified
        elif self.client_state == 2:
            if self.system_enabled:

                self.enabled_echo = True

                mode_selected = False

                # Client-specific functionality
                client_tasks = "client_tasks_" + str(self.uid)
                if client_tasks in self.dbData:

                    if self.dbData[client_tasks] == "Enabled":  # Time of Use in operation
                        mode_selected = True

                    elif self.dbData[client_tasks] == "Disabled":  # Time of Use in operation
                        if mode_selected is not True:
                            mode_selected = False

                if mode_selected:
                    self.system_status_text = "[2] Client System Running"
                else:
                    self.system_status_text = "[2] Client System Idle"
                    #self.charge_from_grid(0)

            else:
                self.system_status_text = "[2] Disabling Client System"
                # TODO: Nice things and variable clearing
                self.client_state = 1

        if len(self.actions) == 0:
            self.actions.append(0)  # Dummy

        self.client_heartbeat = self.heartbeat

        # Modify self.outputs
        self.outputs[2][0] = self.client_heartbeat
        self.outputs[2][1] = self.client_state
        self.outputs[2][2] = 0
        self.outputs[2][3] = 0
        self.outputs[2][4] = 0
        self.outputs[2][5] = 0
        self.outputs[2][6] = 0
        self.outputs[2][7] = 0
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

    def set_inputs(self, inputs):
        if inputs[0] == self.uid:
            self.inputs = inputs[2:]  # Save all module data from the controller onwards

            if len(self.inputs) < 25:                                                               # Wait until all modules have loaded
                return [SET_INPUTS_ACK]

            # Controller (module type 1)
            if self.inputs[ModTypes.CONTROL.value] is not None:
                for dev in self.inputs[ModTypes.CONTROL.value]:
                    # Check Controller Enable State
                    if dev[2][3] & 0x01:                                                            # Controller "operating_state_scada", bit 0 (Enable)
                        self.system_enabled = True
                    else:
                        self.system_enabled = False

            # AC Meter (module type 4)
            if self.inputs[ModTypes.AC_METER.value] is not None:
                for dev in self.inputs[ModTypes.AC_METER.value]:
                    if dev[1]:
                        # Here is where we distinguish between grid / load and grid+load metering
                        # Although in this project we only have one grid+load meter so should I bother?
                        meter_location = "client_ac_meter_location_" + str(dev[0])
                        if meter_location in self.dbData:
                            if self.dbData[meter_location] == "Grid_and_Load":
                                self.ac_meter_grid_power = dev[2][7]
                                self.ac_meter_grid_power_kva = dev[2][13]

            # Digital IO (module type 6)
            if self.inputs[ModTypes.DIG_IO.value] is not None:
                for dev in self.inputs[ModTypes.DIG_IO.value]:
                    if dev[1]:

                        # Bank power relay (Control power to all the RMSCs
                        control_power_state = 0
                        output = "client_bank_reset_op_" + str(dev[0])
                        if output in self.dbData:
                            if self.dbData[output] != "None":
                                uid, io = self.get_io(self.dbData[output])
                                if dev[0] == uid:
                                    if dev[2][5] & (1 << io * 2):
                                        control_power_state = 0x01

                        if control_power_state == 0x01:
                            self.bank_reset_enabled = False                                         # Now inverted in hardware
                        else:
                            self.bank_reset_enabled = True

            # Client (module type 24)
            if self.inputs[ModTypes.CLIENT.value] is not None:
                for dev in self.inputs[ModTypes.CLIENT.value]:
                    if dev[1]:
                        self.client_enabled = True

                        peak_shaving_limit = "client_peak_shaving_limit_" + str(dev[0])
                        if peak_shaving_limit in self.dbData:
                            self.client_peak_shaving_limit = self.dbData[peak_shaving_limit]

                    else:
                        self.client_enabled = False
        return [SET_INPUTS_ACK]

    def get_outputs(self):

        self.outputs[1] = self.enabled_echo

        # Copy the system structure locally
        #mod_data = copy.deepcopy(self.inputs)
        mod_data = self.inputs

        if len(self.inputs) < 25:
            #return mod_data
            return [GET_OUTPUTS_ACK, mod_data]

        # Add the new client output data
        mod_data[ModTypes.CLIENT.value] = self.outputs

        # #############################################################################################################################
        # Critical Module enable                                                                                                      #
        # These must be active and remain active throughout the system's operation. Logging should always come first.                 #
        # Those below are expected to be running on every system from boot, and are not included in the start sequence                #
        # #############################################################################################################################

        # Ensure the SCADA interface is running
        if self.inputs[ModTypes.SCADA.value] is not None:
            if not mod_data[ModTypes.SCADA.value][0][1]:
                mod_data[ModTypes.SCADA.value][0][1] = True

        # Ensure the Logging interface is running
        if self.inputs[ModTypes.LOGGING.value] is not None:
            if not mod_data[ModTypes.LOGGING.value][0][1]:
                mod_data[ModTypes.LOGGING.value][0][1] = True

        # Enable DC Earth Fault Monitor(s)
        if self.inputs[ModTypes.DC_EFM.value] is not None:
            if not mod_data[ModTypes.DC_EFM.value][0][1]:
                mod_data[ModTypes.DC_EFM.value][0][1] = True

        # Ensure the Li-Ion Tamer is running
        if self.inputs[ModTypes.LI_ION.value] is not None:
            if not mod_data[ModTypes.LI_ION.value][0][1]:
                mod_data[ModTypes.LI_ION.value][0][1] = True

        # Ensure the AC Meter is running
        if self.inputs[ModTypes.AC_METER.value] is not None:
            if not mod_data[ModTypes.AC_METER.value][0][1]:
                mod_data[ModTypes.AC_METER.value][0][1] = True

        # Enable Digital IO
        if self.inputs[ModTypes.DIG_IO.value] is not None:
            if not mod_data[ModTypes.DIG_IO.value][0][1]:
                mod_data[ModTypes.DIG_IO.value][0][1] = True

        # Enable Analogue IO
        if self.inputs[ModTypes.ANA_IO.value] is not None:
            if not mod_data[ModTypes.ANA_IO.value][0][1]:
                mod_data[ModTypes.ANA_IO.value][0][1] = True

        # Enable Air Conditioning
        if self.inputs[ModTypes.AIRCON.value] is not None:
            if not mod_data[ModTypes.AIRCON.value][0][1]:                                           # NO TRIGGER (AC must always be on in normal use)
                mod_data[ModTypes.AIRCON.value][0][1] = True

        # Enable Battery Interlocks
        if self.inputs[ModTypes.DIG_IO.value] is not None:
            if not mod_data[ModTypes.DIG_IO.value][0][1]:                                           # NO TRIGGER (Interlocks must always be on in normal use)
                mod_data[ModTypes.DIG_IO.value][0][1] = True
            else:
                for dev in mod_data[ModTypes.DIG_IO.value]:
                    if dev[1]:

                        # Bank power relay (Not used on the MSP Prototype, but new models control power to all the RMSCs, not individual E-Stop interlocks)
                        output = "client_bank_reset_op_" + str(dev[0])
                        if output in self.dbData:
                            if self.dbData[output] != "None":
                                uid, io = self.get_io(self.dbData[output])
                                if dev[0] == uid:
                                    dev[2][5] &= ~(0x03 << (io * 2))                                # Inverted in hardware, RMSC Power enabled if IO is False

        # #############################################################################################################################
        # Runtime Module enable                                                                                                       #
        # During the Client start process, it enables a specific feature of each system module in a safe sequence.                    #
        # Those below are included in the start sequence (they use a trigger) and may be disabled in response to a fault              #
        # The specific order in which they're enabled is set in the process loop after the Enable Bit is set in Operating State       #
        # #############################################################################################################################

        # Return the lot
        outputs = mod_data

        return [GET_OUTPUTS_ACK, outputs]

    def set_page(self, page, form):  # Respond to GET/POST requests
        data = dict()

        if page == self.website + "_(" + str(self.uid) + ")":  # It was a page request for a numbered device (POST)

            # Save all control changes to database
            isButton = True
            for control in form:
                if form[control] != "":                                                     # Save all non-button control changes to database
                    self.dbData[control] = form[control]
                    self.save_to_db()

            if isButton is False:  # Button press states are to be acted upon only, not stored
                self.save_to_db()

        elif page == (self.website + "_(" + str(self.uid) + ")/data"):  # It was a json data fetch quest (POST)

            mod_data = dict()

            # Clear old lists
            #self.actions = []

            # Controller Information
            mod_data["client_name"] = self.name
            mod_data["client_man"] = self.manufacturer
            mod_data["client_fwver"] = self.version
            mod_data["client_serno"] = self.serial
            mod_data["client_constate"] = str(self.con_state).capitalize()
            mod_data["client_enablestate"] = self.enabled
            mod_data["client_data"] = self.outputs[2]

            # System-wide info to populate the configurator
            mod_data["client_system"] = self.inputs

            # Just a line of text to indicate the start sequence
            mod_data["client_status"] = self.system_status_text

            mod_data.update(self.dbData)  # I'm appending the dict here so we don't save unnecessary
            
            return [SET_PAGE_ACK, mod_data]  # data to the database.

        else:
            return [SET_PAGE_ACK, ('OK', 200)]  # Return the data to be jsonified

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
    
    def get_page(self):
        routes = [self.website + "_(" + str(self.uid) + ")/data"]  # JSON Data: FlexMod_test_v100_(<uid>)/data/
        page = [self.website + "_(" + str(self.uid) + ")", routes]  # HTML content: FlexMod_test_v100_(<uid>).html
        return [GET_PAGE_ACK, page]

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
            print("Client: " + str(e))
          
        try:
            if tx_msg is not None:
                queue[0].put(tx_msg, block=True)
                
        except Exception as e:
            print("Client: " + str(e))


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
    NONE = 0  # "Warning: Analogue IO - No Warning present"


class Alarms(Enum):
    NONE = 0  # "Alarm: Analogue IO - No Alarm present"


class Faults(Enum):
    NONE = 0  # Fault: Analogue IO - No Fault present
    CONFIG = 1  # Fault: Analogue IO - Configuration
    LOSS_OF_COMMS = 2  # Fault: Analogue IO - Loss of Comms
    IO_TIMEOUT = 3


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
