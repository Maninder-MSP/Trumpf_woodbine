# FlexMod_CumminsFuelCell.py

# Description
# Cummins - Fuel Cell Module

# Versions
# 3.5.24.10.16 - SC - Known good starting point, uses thread.is_alive to prevent module stalling.

from pymodbus.client.sync import ModbusTcpClient as mb_tcp_client
from threading import Thread, Event
from FlexMod import BaseModule, ModTypes
from FlexDB import FlexTinyDB
from enum import Enum
from pymodbus.constants import Endian
from pymodbus.payload import BinaryPayloadDecoder
import copy
from datetime import datetime
import time
import logging
import sys

# Setup logging
def setup_logging():
    logger = logging.getLogger('fc_logger')
    logger.propagate = False
    handler = logging.FileHandler('fc_specific.log')
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    return logger

fc_logger = setup_logging()

'''
# Log a message
def log_to_file(message, log_level='info'):
    log_funcs = {
        'info': fc_logger.info,
        'warning': fc_logger.warning,
        'error': fc_logger.error
    }
    timestamp = datetime.now().strftime("%d-%m-%Y %H:%M:%S")
    log_func = log_funcs.get(log_level, fc_logger.info)
    log_func(f"{message} - {timestamp}")
'''

# Database
db = FlexTinyDB()

loop_time = 0


# Queued commands
SYNC = 0                                                                                            # Allows the Flex code to syncronise module polling loops using a single thread (TBD)
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

'''
class Interval(Thread):
    def __init__(self, event, process, interval):
        Thread.__init__(self)
        self.stopped = event
        self.target = process
        self.interval = interval

    def run(self):
        while not self.stopped.wait(self.interval):
            self.target()
'''


class Module():
    # Class-level lists and dictionaries for registers
    # These are the registers from the Cummins Modbus Map documents
    SystemMode = ['Unknown', 'Off', 'Standby', 'Run']
    SystemState = [
        'Unknown', 'H2_Valve_Open', 'Grid_Tie_Charge', 'Grid_Tie_Run',
        'Cooling_Start', 'Fuel_Cell_Start', 'Fuel_Cell_Running', 'Fuel_Cell_Stop',
        'H2_Valve_Close', 'Cooling_Stop', 'Stopped'
    ]

    # Alarm Word labels
    alarm_labels_1 = [
        "Ambient Temp Low", "Ambient Temp High", 
        "Coolant Inlet Pressure Low", "Coolant Inlet Pressure High",
        "Coolant Outlet Pressure Low", "Coolant Outlet Pressure High",
        "Coolant Inlet Temperature Low", "Coolant Outlet Temperature Low",
        "Coolant Outlet Temperature High", "Coolant Outlet Temperature Warning",
        "Coolant Outlet Temperature Derate", "Coolant Flow Low",
        "Coolant Flow High", "Loss of Coolant Flow",
        "Coolant Pump Fail To Stop", "Coolant Pump Overload"
    ]

    alarm_labels_2 = [ 
        "Coolant Inlet Flow Sensor Fail", "Coolant Inlet Pressure Sensor Fail",
        "Coolant Outlet Pressure Sensor Fail", "Coolant Inlet Temperature Sensor Fail",
        "Coolant Outlet Temperature Sensor Fail", "Overtemp and Insufficient Cooling Capacity",
        "H2 Flow Low", "H2 Flow High", "H2 Pressure Low", "H2 Pressure High",
        "H2 Meter Failure Device or Comms", "VFD Comm Failure", "VFD Drive Failure", 
        "Vent Fan Flow Switch Failure", "DI Water Tank Contact Failure", "DI Water Tank Fail To Fill"          
    ]

    alarm_labels_3 = [
        "Process Room Flame Alarm", "Process Room Flame Sensor Fail",
        "Process Room H2 Gas Warning", "Process Room H2 Gas Alarm",
        "Process Room H2 Gas Sensor Fail", "Process Room Smoke Alarm",
        "Cooling Room Smoke Alarm", "Station Power UPS Alarm",
        "Station Power From Batteries", "Emergency Stop",
        "Invalid System Configuration", "Reserved", "Reserved", "Reserved", "Reserved", "Reserved"
    ]

    rack_alarm_state_1_labels = [
        "FCPMs Alarm", "FCPMs Comm Lost", "H2 Level High Alarm", "Drain Pump or Sensor Failure",
        "Low Coolant Level", "External Coolant Temp High", "External Coolant Temp Low",
        "External Coolant Temp Sensor Fail", "H2 Inlet Pressure Low", "H2 Inlet Pressure High",
        "Ambient Temp High/Low", "3 Way Valve Failure", "Derate Active", "Reserved", "Reserved", "Reserved"
    ]

    rack_alarm_fault_state_2_labels = [
        "Reserved", "Reserved", "Reserved", "Reserved", "Reserved", "Reserved", "Reserved", "Reserved",
        "Current Limit Exceeded", "Reserved", "Reserved", "Reserved", "Reserved", "Reserved", "Reserved", "Reserved"
    ]

    rack_fault_state_labels = [
        "Precharge Relay Failure", "Negative Load Contactor Fail", "Positive Load Contactor Fail",
        "H2 Inlet Pressure Sensor Failure", "H2 Inlet Pressure High", "System Fail (Restart Attempts)",
        "FCPMs Fail (Restart Attempts)", "Host Communication Lost", "Voltage Mismatch", "Precharge Timeout 1",
        "Precharge Timeout 2", "Cathode Actuator Failed", "Bus Voltage Sensor Failed", "H2 Level Sensor Failed",
        "Low Power Limit", "Fail To Start"
    ]
    rack_e_stop_fault_state_labels = [
        "Pressure Switch", "HWSS", "Drain Level Low", "Remote Estop", "Local Estop", "H2 Level Sensor", "Smoke Detected",
        "Reserved", "Reserved", "Reserved", "Reserved", "Reserved", "Reserved", "Reserved", "Reserved", "Reserved"
    ]

    # Control and status words
    control_word_labels = [
        "Run Command", "Reset Command", "Reserved", "Reserved", "Reserved", "Reserved", "Reserved", "Reserved",
        "Reserved", "Reserved", "Reserved", "Reserved", "Reserved", "Reserved", "Reserved",
        "Fieldbus Control Enable"
    ]
    status_word_labels = [
        "Run Command Echo", "Reset Command Echo",
        "Reserved", "Reserved", "Reserved", "Reserved", "Reserved", "Reserved",
        "Ready To Start", "Reserved", "Reserved", "Summary Alarm",
        "System Operation Inhibited", "Control Param OOR",
        "Non-Critical Shutdown", "Critical Shutdown"
    ]
    
    # Define the encoding types for each register - Note: The registers are offset by -400001
    register_names_1 = {
        "DC Converter Amperage": {"register": 13999, "type": "int16", "scaling_factor": 0.1, "access": "R/W"},
    }
    
    register_names_2 = {
        "Off Mode Indicator": {"register": 14009, "type": "int16", "scaling_factor": None, "access": "R/W"},
        "Off Mode Command": {"register": 14010, "type": "int16", "scaling_factor": None, "access": "R/W"},
        "Standby Mode": {"register": 14011, "type": "int16", "scaling_factor": None, "access": "R/W"},
    }
    register_names_3 = {
        "Local Control Command": {"register": 14015, "type": "int16", "scaling_factor": None, "access": "R/W"},
        "Local Control Indicator": {"register": 14016, "type": "int16", "scaling_factor": None, "access": "R/W"},
        "Remote Control Command": {"register": 14017, "type": "int16", "scaling_factor": None, "access": "R/W"},
    }
    register_names_4 = {
        "Status_Word": {"register": 14499, "type": "uint16", "scaling_factor": None, "access": "R"},
        "Control_Word": {"register": 14500, "type": "uint16", "scaling_factor": None, "access": "R/W"},
        "kW_Setpoint": {"register": 14501, "type": "int16", "scaling_factor": 1, "access": "R/W"},
        "Heartbeat": {"register": 14502, "type": "uint16", "scaling_factor": None, "access": "R"},
        "System_Mode": {"register": 14503, "type": "uint16", "scaling_factor": None, "access": "R"},
        "System_State": {"register": 14504, "type": "uint16", "scaling_factor": None, "access": "R"},
        "Nominal_kW": {"register": 14505, "type": "int16", "scaling_factor": 1, "access": "R"},
        "Ambient_Air_Temp": {"register": 14506, "type": "int16", "scaling_factor": 0.1, "access": "R"},
        "Ambient_Air_Temp_Setpoint": {"register": 14507, "type": "int16", "scaling_factor": 0.1, "access": "R"},
        "H2_Pressure": {"register": 14508, "type": "int16", "scaling_factor": 0.1, "access": "R"},
        "H2_Flow": {"register": 14509, "type": "int16", "scaling_factor": 0.1, "access": "R"},
        "Coolant_Inlet_Temp": {"register": 14510, "type": "int16", "scaling_factor": 0.1, "access": "R"},
        "Coolant_Outlet_Temp": {"register": 14511, "type": "int16", "scaling_factor": 0.1, "access": "R"},
        "Coolant_Inlet_Pressure": {"register": 14512, "type": "int16", "scaling_factor": 0.1, "access": "R"},
        "Coolant_Outlet_Pressure": {"register": 14513, "type": "int16", "scaling_factor": 0.1, "access": "R"},
        "Coolant_Inlet_Flow": {"register": 14514, "type": "int16", "scaling_factor": 0.1, "access": "R"},
        "Reserved_1": {"register": 14515, "type": "int16", "scaling_factor": 1, "access": "R"},
        "Reserved_2": {"register": 14516, "type": "int16", "scaling_factor": 1, "access": "R"},
        "Reserved_3": {"register": 14517, "type": "int16", "scaling_factor": 1, "access": "R"},
        "Reserved_4": {"register": 14518, "type": "int16", "scaling_factor": 1, "access": "R"},
        "Reserved_5": {"register": 14519, "type": "int16", "scaling_factor": 1, "access": "R"},
        "Reserved_6": {"register": 14520, "type": "int16", "scaling_factor": 1, "access": "R"},
        "Alarm_Word_1": {"register": 14521, "type": "uint16", "scaling_factor": 1, "access": "R"},
        "Alarm_Word_2": {"register": 14522, "type": "uint16", "scaling_factor": 1, "access": "R"},
        "Alarm_Word_3": {"register": 14523, "type": "uint16", "scaling_factor": 1, "access": "R"},
    }   

    rack_register_offsets = {
        "Rack State": {"register": 14099, "type": "uint16", "scaling_factor": None, "access": "R"},
        "Alarm State 1": {"register": 14100, "type": "uint16", "scaling_factor": None, "access": "R"},
        "Alarm Fault State 2": {"register": 14101, "type": "uint16", "scaling_factor": None, "access": "R"},
        "Fault State": {"register": 14102, "type": "uint16", "scaling_factor": None, "access": "R"},
        "Estop Fault State": {"register": 14103, "type": "uint16", "scaling_factor": None, "access": "R"},
        "Nominal kW Size": {"register": 14104, "type": "uint16", "scaling_factor": None, "access": "R"},
        "Rack Coolant Temperature": {"register": 14105, "type": "int16", "scaling_factor": 0.1, "access": "R"},
        "Rack DC Voltage": {"register": 14106, "type": "int16", "scaling_factor": 1, "access": "R"},
        "Rack DC Current": {"register": 14107, "type": "int16", "scaling_factor": 0.1, "access": "R"},
        "Rack Total kW": {"register": 14108, "type": "int16", "scaling_factor": 0.1, "access": "R"},
        "Rack Current Draw Available (CDA)": {"register": 14109, "type": "int16", "scaling_factor": 0.1, "access": "R"},
        "Status Word": {"register": 14110, "type": "uint16", "scaling_factor": None, "access": "R"},
        "Control Word": {"register": 14111, "type": "uint16", "scaling_factor": None, "access": "R/W"},
    }
    
    def __init__(self, uid, queue):
        super(Module, self).__init__()

        # General Module Data
        self.author = "Gareth Reece"
        self.uid = uid
        self.icon = "/static/images/FuelCell.png"
        self.name = "Cummins Fuel Cell"
        self.module_type = ModTypes.FUEL_CELL.value
        self.module_version = "3.5.24.10.16"
        self.manufacturer = "Cummins"
        self.model = ""
        self.options = ""
        self.version = "-"
        self.serial = "-"
        self.website = "/Mod/FlexMod_CumminsFuelCell"

        # Run state
        self.con_state = False
        self.state = State.IDLE
        self.set_state_text(self.state)

        # Non-Volatile Data (Loaded from DB)
        self.dbData = db.fetch_from_db(__name__ + "_(" + str(self.uid) + ")")
        if self.dbData is None:
            self.dbData = dict()
            self.dbData["fuel_cell_ipaddr_local"] = "0.0.0.0"

        # Volatile Data
        # change self.tcp_client to None if the device is running on a BESS
        # change self.enabled and self.enabled_echo to False if the device is running on a BESS

        self.tcp_client = None # change this to 0 if the device is running locally otherwise leave as None
        self.tcp_timeout = 0
        ############################
        self.enabled = False # change this to True if the device is running locally
        ############################
        self.enabled_echo = False # change this to True if the device is running locally
        self.override = False
        self.inputs = [self.uid, False, [False * 14]]  # All reserved
        self.outputs = [self.uid, self.enabled, [0] * 25]  # 7 Analogue inputs and 7 reserved
        self.fuel_cell_heartbeat = 0
        self.heartbeat = 0
        self.heartbeat_echo = 0

        # Events
        self.warnings = Warnings.NONE.value
        self.alarms = Alarms.NONE.value
        self.faults = Faults.NONE.value
        self.actions = []
        
        # HMI data, from which power is derived.
        self.priV = 0                                                                                 # Primary units of AC Voltage and Current
        self.priA = 0
        self.secV = 0                                                                                 # Secondary, for DC units
        self.secA = 0
        self.terV = 0                                                                                 # Tertiary, if a device has a third port, like a PD Hydra
        self.terA = 0

        ##### Parsed Data #####
        self.parsed_status_word = None
        self.parsed_control_word = None
        self.parsed_alarm_1 = None
        self.parsed_alarm_2 = None
        self.parsed_alarm_3 = None
        self.parsed_rack_alarm_state_1 = None
        self.parsed_rack_alarm_fault_state_2 = None
        self.parsed_rack_fault_state = None
        self.parsed_rack_e_stop_fault_state = None
        self.parsed_system_mode = None
        self.parsed_system_state = None

        ##### Rack Data #####
        self.all_racks_data = []
        self.nominal_kw = 0
        self.kW_setpoint = 0
        self.status_word = 0
        self.control_word = 0
        self.ambient_air_temp = 0
        self.ambient_air_temp_setpoint = 0
        self.h2_pressure = 0
        self.h2_flow = 0
        self.coolant_inlet_temp = 0
        self.coolant_outlet_temp = 0
        self.coolant_inlet_pressure = 0
        self.coolant_outlet_pressure = 0
        self.coolant_inlet_flow = 0
        self.fuel_cell_heartbeat_device = 0
        self.rack_dc_voltage = 0
        self.rack_dc_current = 0
        self.rack_total_kw= 0
        self.rack_current_draw_available = 0
        self.rack_alarm_state_1 = 0
        self.rack_alarm_fault_state_2 = 0
        self.rack_fault_state = 0
        self.rack_estop_fault_state = 0

        ##### System Status #####
        self.any_alarms = 0
        self.standby_mode_indicator = None
        self.standby_mode_indicator_bit = None
        self.remote_control_indicator = None
        self.remote_control_indicator_bit = None
        self.standby_mode_indicator_check = 0
        self.remote_control_indicator_check = 0

        self.dc_converter_amperage = None # simulated amperage
        self.check_14500_15_is_one = 0

        self.device_values = {}
        self.rack_data = {}

        # Start interval timer
        #self.stop = Event()
        #self.interval = 1  # Interval timeout in Seconds
        #self.thread = Interval(self.stop, self.__process, self.interval)
        #self.thread.start()

        # Loop Counter and Time
        self.loop_times = []
        self.counter = 0

        print("Starting " + self.name + " with UID " + str(self.uid) + " on version " + str(self.module_version))


        ###### TESTING COMMANDS ######
        self.fuel_cell_on_off_output = 0
        self.old_fuel_cell_on_off_output = 0
        self.fuel_cell_rack_on_off_output = 0
        self.old_fuel_cell_rack_on_off_output = 0
        self.clear_faults_output = 0
        self.old_clear_faults_output = 0
        # self.current_device_setpoint = 0
        self.setpoint_output = 0
        self.old_setpoint_output = 0
        self.simulated_amps_output = 0
        self.old_simulated_amps_output = 0
        self.read_fuel_cell_on_off_bit = 0
        self.read_fuel_cell_rack_on_off_bit = 0
        self.read_fuel_cell_clear_faults_bit = 0

        self.enabled_counter = 0
        self.somethings_gone_wrong_enabling_counter = 0

        self.control_setpoint = 0
        self.get_setpoint = 0
        self.count_this = 0

        self.device_setpoint = 0
        self.scada_setpoint = 0
        self.override_setpoint = 0
        self.old_override_setpoint = 0
        self.change_setpoint = 0
        self.first_override_check = True

        self.override_setpoint_value = 0
        self.old_override_setpoint_value = 0

        self.old_device_setpoint = 0
        self.old_override_setpoint = 0
        self.old_scada_setpoint = 0

        self.real_setpoint = 0
    
        ##############################

    def process(self):
        global loop_time
        #print("(14)  F.Cell Heartbeat: " + str(self.heartbeat) + ", loop time: " + str(loop_time) + " Seconds")
        
        s = time.time()
        
        # start loop time to check for efficiency
        start_time = time.time()

        self.heartbeat += 1
        if self.heartbeat >= 0xFFFF:
            self.heartbeat = 0
        self.fuel_cell_heartbeat = self.heartbeat

        if self.tcp_timeout >= 5:
            self.tcp_timeout = 0
            self.tcp_client = None

        if self.tcp_client is None:

            self.con_state = False
            if "fuel_cell_ipaddr_local" in self.dbData:
                if self.dbData["fuel_cell_ipaddr_local"] != "0.0.0.0":
                    try:
                        #log_to_file("Inside try block for establishing TCP client. - 400", "info") 
                        self.tcp_client = mb_tcp_client(self.dbData["fuel_cell_ipaddr_local"], port=502, timeout=1)

                        if self.tcp_client.connect() is True:
                            #log_to_file("TCP client successfully connected. - 401", "info")
                            self.update_faults(Faults.LOSS_OF_COMMS.value, False)  # Clear Fault
                            self.set_state_text(State.CONNECTED)
                            self.con_state = True
                        else:
                            #log_to_file("TCP client failed to connect. - 402", "warning")
                            self.update_faults(Faults.LOSS_OF_COMMS.value, True)  # Raise Fault
                            self.set_state_text(State.CONNECTING)
                            self.tcp_client = None

                    except Exception as e:
                        print("Fuel Cell: " + str(e))
                        #log_to_file("Exception occurred while establishing TCP client. - 403", "error")
                        self.update_faults(Faults.LOSS_OF_COMMS.value, True)
                        self.set_state_text(State.CONNECTING)
                else:
                    self.update_faults(Faults.LOSS_OF_COMMS.value, True)  # Raise Fault
                    self.set_state_text(State.CONFIG)
            else:
                #log_to_file("IP address is not in the database. - 201", "warning")
                self.update_faults(Faults.LOSS_OF_COMMS.value, True) # Raise Fault
                self.set_state_text(State.CONFIG)
        else:
            self.con_state = True

            ###### REGISTER READS AND PARAMETERS HERE ######
            # # HMI Header parameters
            ## CAN'T FIND ANY HEADER PARAMETERS IN THE MODBUS MAP DOCUMENT FOR FUEL CELL ##

            # Set the 15th bit of register 14500 to 1 without affecting other bits every loop
            # This is to keep the fuel cell running
            self.set_14500_15_to_one(self.tcp_client)       
            
            # Attempt to read rack data
            try:
                # added a timout of 0.25 seconds to the read_rack_data function
                success = self.read_rack_data(self.rack_register_offsets)
                
                if not success:
                    #log_to_file("Error in reading rack data, operation aborted - 705", "error")
                    self.tcp_timeout += 1
                    return
            except Exception as e:
                print("Fuel Cell: " + str(e))
                #log_to_file(f"Unexpected error reading rack registers: {str(e)} - 703", "error")
                self.tcp_timeout += 1
                return
        
            try:
                # Fetch all required register blocks and handle errors.
                # Clear previous device values
                self.device_values.clear()
                register_blocks = [
                    (13999, 13999, self.register_names_1),
                    (14009, 14011, self.register_names_2),
                    (14015, 14017, self.register_names_3),
                    (14499, 14523, self.register_names_4),
                ]

                for start, end, names in register_blocks:
                    if not self.fetch_registers(start, end, names):
                        #log_to_file(f"Error reading registers {start}-{end}, operation aborted - 810.", "error")
                        self.tcp_timeout += 1
                        return False
            except Exception as e:
                print("Fuel Cell: " + str(e))
                #log_to_file(f"Unexpected error in main process loop - 811: {str(e)}", "error")
                self.tcp_timeout += 1

            #### ANY CALCULATIONS REQUIRED HERE ####
            def parse_bitfield(word, labels):
                flags = {label: bool(word & (1 << i)) for i, label in enumerate(labels)}
                return {k: v for k, v in flags.items() if v}
            def parse_enum(value, labels):
                try:
                    return labels[value]
                except IndexError as e:
                    print("Fuel Cell: " + str(e))
            def is_bit_set(value, position):
                if isinstance(value, str):
                    value = int(value, 2)
                return bool(value & (1 << position))
            
            try:
                # if first run set the dc converter amperage to 0
                if self.first_override_check == True:
                    self.set_dc_converter_amperage(self.tcp_client, 0)
                     # also set the device setpoint to 0 as well
                    self.set_kW_setpoint(self.tcp_client, 0)
                    self.first_override_check = False

                self.scada_setpoint = float(self.get_setpoint)  # Read SCADA setpoint from the outputs
                # moved this check to set inputs function so that it only changes if it has been changed in the input
                # self.override_setpoint = float(self.override_setpoint_value)
                self.device_setpoint = float(self.device_values["kW_Setpoint"])

            # Monitor SCADA Setpoint
                if self.scada_setpoint != self.old_scada_setpoint:
                    # print ("========== I'm in the SCADA Setpoint ==========")
                    self.real_setpoint = self.scada_setpoint
                    self.old_scada_setpoint = self.real_setpoint
                    self.device_setpoint = self.real_setpoint
                    self.old_device_setpoint = self.real_setpoint
                    self.override_setpoint = self.real_setpoint
                    self.old_override_setpoint = self.real_setpoint
                    self.set_kW_setpoint(self.tcp_client, self.real_setpoint)

                # Monitor Device Setpoint

                elif self.device_setpoint != self.old_device_setpoint:
                    # print ("========== I'm in the Device Setpoint ==========")
                    self.real_setpoint = self.device_setpoint
                    self.old_device_setpoint = self.real_setpoint
                    self.override_setpoint = self.real_setpoint
                    self.old_override_setpoint = self.real_setpoint
                    self.set_kW_setpoint(self.tcp_client, self.real_setpoint)

                # Monitor Override Setpoint
                elif self.override_setpoint != self.old_override_setpoint:
                    # print ("========== I'm in the Override Setpoint ==========")
                    self.real_setpoint = self.override_setpoint
                    self.old_override_setpoint = self.real_setpoint
                    self.device_setpoint = self.real_setpoint
                    self.old_device_setpoint = self.real_setpoint
                    self.set_kW_setpoint(self.tcp_client, self.real_setpoint)

                # Update the real setpoint in the outputs
                self.outputs[2][12] = self.real_setpoint           

            except Exception as e:
                print("Fuel Cell: " + str(e))
                pass
                # print(f"Error in comparing device setpoint to control setpoint - 405: {e}")
                #log_to_file(f"Error in comparing device setpoint to control setpoint - 406: {e}", "error")
                

            try: 
                # BitField variables
                status_word_bit = self.device_values["Status_Word"]
                control_word_bit = self.device_values["Control_Word"]
                alarm_word_1_bit = self.device_values["Alarm_Word_1"]
                alarm_word_2_bit = self.device_values["Alarm_Word_2"]
                alarm_word_3_bit = self.device_values["Alarm_Word_3"]
                rack_alarm_state_1_bit = self.rack_alarm_state_1
                rack_alarm_fault_state_2_bit = self.rack_alarm_fault_state_2
                rack_fault_state_bit = self.rack_fault_state
                rack_estop_fault_state_bit = self.rack_estop_fault_state

                # If any alarms is not 0 then set self.any_alarms to 1
                # This is used as a flag to indicate if there are any alarms and to run parse_bitfield to get info
                # If no alarms then save time and don't run parse_bitfield and is more efficient
                if (alarm_word_1_bit != 0 or
                    alarm_word_2_bit != 0 or
                    alarm_word_3_bit != 0 or
                    rack_alarm_state_1_bit != 0 or
                    rack_alarm_fault_state_2_bit != 0 or
                    rack_fault_state_bit != 0 or
                    rack_estop_fault_state_bit != 0):
                    self.any_alarms = 1
                else:
                    self.any_alarms = 0
                
                # Using parse_bitfield for BitField variables
                self.parsed_status_word = parse_bitfield(status_word_bit, self.status_word_labels)
                self.parsed_control_word = parse_bitfield(control_word_bit, self.control_word_labels)

                if self.any_alarms == 1:
                    self.parsed_alarm_1 = parse_bitfield(alarm_word_1_bit, self.alarm_labels_1)
                    self.parsed_alarm_2 = parse_bitfield(alarm_word_2_bit, self.alarm_labels_2)
                    self.parsed_alarm_3 = parse_bitfield(alarm_word_3_bit, self.alarm_labels_3)
                    self.parsed_rack_alarm_state_1 = parse_bitfield(rack_alarm_state_1_bit, self.rack_alarm_state_1_labels)
                    self.parsed_rack_alarm_fault_state_2 = parse_bitfield(rack_alarm_fault_state_2_bit, self.rack_alarm_fault_state_2_labels)
                    self.parsed_rack_fault_state = parse_bitfield(rack_fault_state_bit, self.rack_fault_state_labels)
                    self.parsed_rack_e_stop_fault_state = parse_bitfield(rack_estop_fault_state_bit, self.rack_e_stop_fault_state_labels)
                else:
                    self.parsed_alarm_1 = None
                    self.parsed_alarm_2 = None
                    self.parsed_alarm_3 = None
                    self.parsed_rack_alarm_state_1 = None
                    self.parsed_rack_alarm_fault_state_2 = None
                    self.parsed_rack_fault_state = None
                    self.parsed_rack_e_stop_fault_state = None
                                    
                # Enum variables
                system_mode_enum = self.device_values["System_Mode"]
                system_state_enum = self.device_values["System_State"]

                # Using parse_enum for Enum variables
                self.parsed_system_mode = parse_enum(system_mode_enum, self.SystemMode)
                self.parsed_system_state = parse_enum(system_state_enum, self.SystemState)


                self.nominal_kw = self.device_values["Nominal_kW"]
                # self.kW_setpoint = self.device_values["kW_Setpoint"]
                self.status_word = self.device_values["Status_Word"]
                self.control_word = self.device_values["Control_Word"]
                self.ambient_air_temp = self.device_values["Ambient_Air_Temp"]
                self.ambient_air_temp_setpoint = self.device_values["Ambient_Air_Temp_Setpoint"]
                self.h2_pressure = self.device_values["H2_Pressure"]
                self.h2_flow = self.device_values["H2_Flow"]
                self.coolant_inlet_temp = self.device_values["Coolant_Inlet_Temp"]
                self.coolant_outlet_temp = self.device_values["Coolant_Outlet_Temp"]
                self.coolant_inlet_pressure = self.device_values["Coolant_Inlet_Pressure"]
                self.coolant_outlet_pressure = self.device_values["Coolant_Outlet_Pressure"]
                self.coolant_inlet_flow = self.device_values["Coolant_Inlet_Flow"]
                self.fuel_cell_heartbeat_device = self.device_values["Heartbeat"]
                self.dc_converter_amperage = self.device_values["DC Converter Amperage"]

                self.standby_mode_indicator_check = self.device_values["Standby Mode"]
                self.standby_mode_indicator_bit = '{:016b}'.format(self.standby_mode_indicator_check)
                self.standby_mode_indicator = is_bit_set(self.standby_mode_indicator_bit, 0)

                self.remote_control_indicator_check = self.device_values["Remote Control Command"]
                self.remote_control_indicator_bit = '{:016b}'.format(self.remote_control_indicator_check)
                self.remote_control_indicator = is_bit_set(self.remote_control_indicator_bit, 0)

            except Exception as e:
                print("Fuel Cell: " + str(e))
                #log_to_file(f"Error calculating values, operation aborted - 804. Exception: {e}", "error")
                self.tcp_timeout += 1
                return

            ###############################################################
            #### Need to turn the fuel cell on / off if self.enabled is True / False ####
            # Turned this into a function so that it can be called on other occasions if needed
            self.switch_on() 

            # Check to see if the rack CDA is different than the simulated amps
            # If so set the dc converted amperage to the rack CDA to follow it
            # print(f"rack_current_draw_available: {self.rack_current_draw_available}  simulated_amps_output: {self.simulated_amps_output}")  
            if self.rack_current_draw_available != self.simulated_amps_output:
                self.set_dc_converter_amperage(self.tcp_client, self.rack_current_draw_available)
                # self.simulated_amps_output = self.rack_current_draw_available
            if self.rack_current_draw_available == 0:
                self.set_dc_converter_amperage(self.tcp_client, 0)

            # Put code in to Override Check fuel cell on/off
            # print(f"fuel_cell_on_off_output: {self.fuel_cell_on_off_output}  old_fuel_cell_on_off_output: {self.old_fuel_cell_on_off_output}")
            if self.fuel_cell_on_off_output != self.old_fuel_cell_on_off_output:
                self.set_fuel_cell_on_off(self.tcp_client, self.fuel_cell_on_off_output)
                self.old_fuel_cell_on_off_output = self.fuel_cell_on_off_output


            # Put code in to Override Check fuel cell rack on/off
            # print(f"fuel_cell_rack_on_off_output: {self.fuel_cell_rack_on_off_output}  old_fuel_cell_rack_on_off_output: {self.old_fuel_cell_rack_on_off_output}")
            if self.fuel_cell_rack_on_off_output != self.old_fuel_cell_rack_on_off_output:
                self.set_fuel_cell_rack_on_off(self.tcp_client, self.fuel_cell_rack_on_off_output)
                self.old_fuel_cell_rack_on_off_output = self.fuel_cell_rack_on_off_output


            # Put code in to Override Check clear faults
            # print(f"clear_faults_output: {self.clear_faults_output}  old_clear_faults_output: {self.old_clear_faults_output}")
            if self.clear_faults_output != self.old_clear_faults_output:
                self.set_clear_faults(self.tcp_client, self.clear_faults_output)
                self.old_clear_faults_output = self.clear_faults_output

            self.read_fuel_cell_on_off_bit = self.read_register_bit(self.tcp_client, 14500, 0)
            self.read_fuel_cell_rack_on_off_bit = self.read_register_bit(self.tcp_client, 14111, 1)
            self.read_fuel_cell_clear_faults_bit = self.read_register_bit(self.tcp_client, 14500, 1)

            ############################################################

        # SC 02/10/24 - I've outdented(?) this so we can transmit the heartbeat even if we're not currently talking to a device.
        if len(self.actions) == 0:
            self.actions.append(0)  # Dummy

        try:
            self.outputs[2][0] = self.fuel_cell_heartbeat
            self.outputs[2][1] = 0 #self.device_values["System_State"] # fuel cell state
            self.outputs[2][2] = 0 #self.device_values["System_Mode"] # fuel cell control
            self.outputs[2][3] = 0 #self.any_alarms # fuel cell faults
            self.outputs[2][4] = 0 # fuel cell open circuit voltage ??
            self.outputs[2][5] = 0 #self.rack_current_draw_available / 10 # fuel cell max allowable current scale factor 0.1
            self.outputs[2][6] = 0 #self.nominal_kw # fuel cell max allowable power no scale factor
            self.outputs[2][7] = 0 # fuel cell obligated current
            self.outputs[2][8] = 0 #self.rack_dc_voltage # fuel cell dc bus voltage no scale factor
            self.outputs[2][9] = 0 #self.rack_dc_current / 10 # fuel cell dc bus current scale factor 0.1
            self.outputs[2][10] = 0 #self.rack_total_kw / 10 # fuel cell dc bus power scale factor 0.1
            # self.outputs[2][11] is set by the SCADA and can't be modified here.
            # self.outputs[2][11] = float(self.kW_setpoint) # user setpoint - kW_setpoint for now # old code for show
            self.outputs[2][12] = 0 #float(self.real_setpoint) # real setpoint - read from the device
            self.outputs[2][13] = 0 # Reserved
            self.outputs[2][14] = 0 # V0 Voltage ?
            self.outputs[2][15] = 0 # A0 Current ?
            self.outputs[2][16] = 0 # V1 Voltage ?
            self.outputs[2][17] = 0 # A1 Current ?
            self.outputs[2][18] = 0 # Reserved
            self.outputs[2][19] = 0 # Reserved
            self.outputs[2][20] = self.warnings
            self.outputs[2][21] = self.alarms
            self.outputs[2][22] = self.faults
            self.outputs[2][23] = self.actions[0]
        except Exception as e:
            print("Fuel Cell: " + str(e))
            self.tcp_timeout += 1
            #log_to_file("Error reading FC Solar registers, operation aborted - 900", "error")
            return

        # HMI Icon Status

        if self.any_alarms > 0:
            self.check_warnings()
            self.check_alarms()
            self.check_faults()
        else:
            self.warnings = Warnings.NONE.value
            self.alarms = Alarms.NONE.value
            self.faults = Faults.NONE.value
        
        
        if self.faults != Faults.NONE.value:
            self.set_state_text(State.FAULT)
        elif self.alarms != Alarms.NONE.value:
            self.set_state_text(State.ALARM)
        elif self.warnings != Warnings.NONE.value:
            self.set_state_text(State.WARNING)
        else:
            if self.enabled_echo:
                self.set_state_text(State.ACTIVE)
            else:
                self.set_state_text(State.CONNECTED)

        
        # Loop time calculations for efficiency checking and monitoring performance
        end_time = time.time()
        current_loop_time = round(end_time - start_time, 2)
        self.loop_times.append(current_loop_time)
        # print(f"Fuel Cell Loop Time: {current_loop_time:.2f}")
        self.counter += 1
        if self.counter == 1000:
            loop_time_average = round(sum(self.loop_times) / len(self.loop_times), 2)
            #log_to_file(f"Average loop over 1000 counts: {loop_time_average:.2f}")
            self.counter = 0
            self.loop_times.clear()

        loop_time = time.time() - s

    # Set the 15th bit of register 14500 to 1 without affecting other bits
    def set_14500_15_to_one(self, client):
        address = 14500
        bit_index = 15
        try:
            # Read the current value from the register
            result = client.read_holding_registers(address, 1)
            if result.isError():
                #log_to_file(f"Failed to read address {address} - 733", "error")
                self.check_14500_15_is_one = 0
                return False
            
            # Set the 15th bit of the current register value to 1
            new_value = result.registers[0] | (1 << bit_index)

            # Write the updated value back to the register
            if not client.write_register(address, new_value):
                #log_to_file(f"Failed to write to address {address} - 740", "error")
                self.check_14500_15_is_one = 0
                return False

            # If the write was successful, no need to log every time
            self.check_14500_15_is_one = 1
            return True
        except Exception as e:
            print("Fuel Cell: " + str(e))
            #log_to_file(f"An exception occurred in set_14500_15_to_one - 751: {str(e)}", "error")
            self.check_14500_15_is_one = 0
            return False


    def set_bit(self, value, bit_index):
        # Set the bit at `bit_index` to 1.
        return value | (1 << bit_index)

    def fetch_registers(self, first_register, last_register, register_dict):
        # Function that fetches register values from a specified range and maps them based on register_dict.
        # Returns 'True' if registers are fetched successfully, 'False' otherwise.
        try:
            num_registers = last_register - first_register + 1
            raw_data_response = self.tcp_client.read_holding_registers(first_register, num_registers, unit = 1, timeout = 0.25)

            if raw_data_response is None or raw_data_response.isError():
                #log_to_file(f"Timeout or error over 0.25 seconds while reading registers {first_register}-{last_register} - 800")
                return False

            for name, info in register_dict.items():
                register, data_type = info['register'], info['type']
                if first_register <= register <= last_register:
                    relative_offset = register - first_register
                    register_data = raw_data_response.registers[relative_offset:relative_offset + 1]
                    decoded_value = self.decode_register(register_data, data_type)
                    if decoded_value is not None:
                        self.device_values[name] = decoded_value

            return True

        except Exception as e:
            print("Fuel Cell: " + str(e))
            #log_to_file(f"Unexpected error reading registers {first_register}-{last_register}: {e} - 806")
            return False

    # This version does not loop and has a timeout.
    def read_rack_data(self, rack_register_offsets):
        try:
            # Define the range of registers to fetch
            first_register = 14099
            last_register = 14111
            num_registers = last_register - first_register + 1

            # Fetch all registers in the range
            raw_data_response = self.tcp_client.read_holding_registers(first_register, num_registers, unit=1, timeout=0.25)

            if raw_data_response is None or raw_data_response.isError():
                #log_to_file("Timeout or error over 0.25 seconds while reading rack data - 706", "error")
                return False

            # Extract the actual register values
            register_values = raw_data_response.registers

            # Map the decoded values to their names
            for name, info in rack_register_offsets.items():
                offset = info['register']
                data_type = info['type']
                relative_offset = offset - first_register  # Adjust for zero-based index

                # Slice the register_values to get the specific registers
                register_data = register_values[relative_offset:relative_offset + 1]  # Assuming 1 register for each data_type

                # Decode the register data
                decoded_value = self.decode_register(register_data, data_type)

                if decoded_value is not None:
                    self.rack_data[name] = decoded_value

            # Store the rack data and individual attributes
            self.all_racks_data = [self.rack_data]
            self.rack_dc_voltage = self.rack_data.get("Rack DC Voltage", 0)
            self.rack_dc_current = self.rack_data.get("Rack DC Current", 0)
            self.rack_total_kw = self.rack_data.get("Rack Total kW", 0)
            self.rack_current_draw_available = self.rack_data.get("Rack Current Draw Available (CDA)", 0)
            self.rack_alarm_state_1 = self.rack_data.get("Alarm State 1", 0)
            self.rack_alarm_fault_state_2 = self.rack_data.get("Alarm Fault State 2", 0)
            self.rack_fault_state = self.rack_data.get("Fault State", 0)
            self.rack_estop_fault_state = self.rack_data.get("Estop Fault State", 0)


            return True

        except Exception as e:
            print("Fuel Cell: " + str(e))
            #log_to_file(f"Unexpected error reading rack data: {str(e)} - 704", "error")
            return False

    # Function to process each register depending on type to give actual value
    def decode_register(self, register_data, data_type):
        try:
            # Decode based on data_type
            value = None
            if data_type == "uint16":
                value = BinaryPayloadDecoder.fromRegisters(register_data, byteorder=Endian.Big).decode_16bit_uint()
            elif data_type == "int16":
                value = BinaryPayloadDecoder.fromRegisters(register_data, byteorder=Endian.Big).decode_16bit_int()
            elif data_type == "acc32":
                value = BinaryPayloadDecoder.fromRegisters(register_data, byteorder=Endian.Big).decode_32bit_uint()

            return value

        except Exception as e:
            print("Fuel Cell: " + str(e))
            #log_to_file(f"Unexpected error decoding register data: {str(e)}", "error")
            return None

    def set_kW_setpoint(self, client, setpoint_output):
        print("Inside set_kW_setpoint function")
        try:
            print("Attempting changing setpoint")
            # Check if setpoint_output is None or not a number
            if setpoint_output is None or not isinstance(setpoint_output, (int, float)):
                print("setpoint_output is None or not a number, setting to current kW_setpoint")
                setpoint_output = self.kW_setpoint
            # Check if setpoint_output is within valid range
            elif 0 <= setpoint_output <= self.nominal_kw:
                self.kW_setpoint = setpoint_output
                client.write_register(14501, int(setpoint_output))  # Assuming register requires integer
                print(f"setpoint_output set to {setpoint_output}")
            else:
                # Handle out of range values
                print(f"setpoint_output {setpoint_output} is out of valid range, setting to current kW_setpoint")
                setpoint_output = self.kW_setpoint
        except Exception as e:
            print("Fuel Cell: " + str(e))
            setpoint_output = self.kW_setpoint  # Resetting to a known good value on error

        return self.kW_setpoint  # Returning the actual set value
    
    # Shouldn't need this on the real Fuel Cell
    def set_dc_converter_amperage(self, client, simulated_amps_output):
        print("Inside set_dc_converter_amperage function")
        try:
            # Check if simulated_amps_output is a valid number and greater than 0
            if isinstance(simulated_amps_output, (int, float)) and simulated_amps_output >= 0:
                # Only update if the value is different from the current dc_amperage
                print("Updating DC Converter Amperage")
                self.dc_amperage = simulated_amps_output
                scaled_output = int(simulated_amps_output)  # Apply scale factor
                client.write_register(13999, scaled_output)  # Adjusted register number
                print(f"DC amperage set to {simulated_amps_output}, scaled value {scaled_output} written to register")
            else:
                # If invalid, set simulated_amps_output to the current dc_amperage and log an error
                print(f"Invalid simulated amps value: {simulated_amps_output}. Setting to current DC amperage")
                simulated_amps_output = self.dc_amperage
                #log_to_file(f"Invalid simulated amps value: {simulated_amps_output}", "error")
        except Exception as e:
            print("Fuel Cell: " + str(e))
            simulated_amps_output = self.dc_amperage  # Resetting to a known good value on error
            #log_to_file(f"Error in setting DC Converter Amperage: {e}", "error")

        return self.simulated_amps_output  # Returning the actual set value

    
    def bit_on_off(self, value, bit_index, on):
        # Sets or clears the bit at bit_index in value.
        if on:
            return value | (1 << bit_index)
        else:
            return value & ~(1 << bit_index)

    def set_fuel_cell_on_off(self, client, fuel_cell_on_off_output):
        print("Inside set_fuel_cell_on_off function 14500: 0")
        try:
            response = client.read_holding_registers(14500, 1)
            if response.isError():
                raise ValueError(f"Error reading register: {response}")
            current_value = response.registers[0]
            new_value = self.bit_on_off(current_value, 0, fuel_cell_on_off_output)
            client.write_register(14500, new_value)
            print(f"Fuel cell on/off set to {fuel_cell_on_off_output}")
        except Exception as e:
            print("Fuel Cell: " + str(e))

    def set_fuel_cell_rack_on_off(self, client, fuel_cell_rack_on_off_output):
        print("Inside set_fuel_cell_rack_on_off function 14111: 1")
        try:
            response = client.read_holding_registers(14111, 1)
            if response.isError():
                raise ValueError(f"Error reading register: {response}")
            current_value = response.registers[0]
            new_value = self.bit_on_off(current_value, 1, fuel_cell_rack_on_off_output)
            client.write_register(14111, new_value)
            print(f"Fuel cell rack on/off set to {fuel_cell_rack_on_off_output}")
        except Exception as e:
            print("Fuel Cell: " + str(e))

    def set_clear_faults(self, client, clear_faults_output):
        print("Inside set_clear_faults function 14500: 1")
        try:
            response = client.read_holding_registers(14500, 1)
            if response.isError():
                raise ValueError(f"Error reading register: {response}")
            current_value = response.registers[0]
            new_value = self.bit_on_off(current_value, 1, clear_faults_output)
            client.write_register(14500, new_value)
            print(f"Clear faults set to {clear_faults_output}")
        except Exception as e:
            print("Fuel Cell: " + str(e))

    def read_register_bit(self, client, register, bit_position):
        # General function that reads a specific bit from a given Modbus register.
        try:
            response = client.read_holding_registers(register, 1)
            if response.isError():
                raise ValueError(f"Error reading register {register}: {response}")

            register_value = response.registers[0]
            bit_value = (register_value >> bit_position) & 1
            return bit_value

        except Exception as e:
            print("Fuel Cell: " + str(e))
            # You can decide how to handle this error - either re-throw, return a default value, etc.
            return None

# function that switches the fuel cell on and off if self.enabled is triggered  
    def switch_on(self):
        try: 
            if (self.enabled == True and self.enabled_echo == False):                  
                # clear faults twice to ensure it clears
                if self.enabled_counter == 0:
                    print("Enabling fuel cell counter = 0")
                    self.set_clear_faults(self.tcp_client, 1)
                    self.set_fuel_cell_on_off(self.tcp_client, 0)
                    self.set_fuel_cell_rack_on_off(self.tcp_client, 0)
                    # self.set_kW_setpoint(self.tcp_client, 0)
                    self.enabled_counter = 1
                elif self.enabled_counter == 1:
                    print("Enabling fuel cell counter = 1")
                    self.enabled_counter = 2
                elif self.enabled_counter == 2:
                    print("Enabling fuel cell counter = 2")
                    # When counter reaches 2 then clear faults needs turning off
                    self.set_clear_faults(self.tcp_client, 0)
                    self.enabled_counter = 3
                elif self.enabled_counter == 3:
                    print("Enabling fuel cell counter = 3")
                    self.enabled_counter = 4
                elif self.enabled_counter == 4:
                    print("Enabling fuel cell counter = 4")
                    # When counter reaches 4 then clear faults needs turning on again
                    self.set_clear_faults(self.tcp_client, 1)
                    self.set_fuel_cell_on_off(self.tcp_client, 0)
                    self.set_fuel_cell_rack_on_off(self.tcp_client, 0)
                    self.enabled_counter = 5
                elif self.enabled_counter == 5:
                    print("Enabling fuel cell counter = 5")
                    self.enabled_counter = 6
                elif self.enabled_counter == 6:
                    print("Enabling fuel cell counter = 6")
                    # When counter reaches 6 then clear faults needs turning off again
                    self.set_clear_faults(self.tcp_client, 0)
                    self.enabled_counter = 7
                elif self.enabled_counter == 7:
                    print("Enabling fuel cell counter = 7")
                    self.enabled_counter = 8
                elif self.enabled_counter == 8:
                    print("Enabling fuel cell counter = 8")
                    self.enabled_counter = 9
                    # now faults have been cleared, turn fuel cell on
                    self.set_fuel_cell_on_off(self.tcp_client, 1)
                elif (
                    self.enabled_counter == 9 and 
                    self.read_fuel_cell_on_off_bit == 1 and 
                    self.parsed_system_state == "Fuel_Cell_Running"
                    ):
                    print("Fuel cell is on and counter = 9")
                    # now fuel cell is on, turn rack on
                    self.set_fuel_cell_rack_on_off(self.tcp_client, 1)
                    self.enabled_counter = 10
                elif (
                    self.enabled_counter == 10 and 
                    self.read_fuel_cell_rack_on_off_bit == 1 and 
                    self.parsed_system_state == "Fuel_Cell_Running" and 
                    self.rack_data["Rack State"] in (7, 8)
                    ):  
                        print("Fuel cell rack is on and counter = 10 - all working and enabled")
                        # rack state 7 = running and rack state 8 = running idle                        # now rack is on, all working and enabled echo can be set to True
                        print("Fuel cell is enabled")
                        self.enabled_counter = 0
                        self.enabled_echo = True
                        self.somethings_gone_wrong_enabling_counter = 0
                else:
                    self.somethings_gone_wrong_enabling_counter += 1
                    print(f"Something is going wrong else statement - counter = {self.enabled_counter}")
                    if self.somethings_gone_wrong_enabling_counter == 12:
                        print (f"Something has gone wrong enabling the fuel cell, check the fuel cell - counter = {self.somethings_gone_wrong_enabling_counter}")
                        #log_to_file(f"Something has gone wrong enabling the fuel cell, check the fuel cell - counter = {self.somethings_gone_wrong_enabling_counter}", "error")
                        self.enabled_counter = 0
                        self.enabled_echo = False
                        self.somethings_gone_wrong_enabling_counter = 0
                        self.set_clear_faults(self.tcp_client, 0)
                        self.set_fuel_cell_on_off(self.tcp_client, 0)
                        self.set_fuel_cell_rack_on_off(self.tcp_client, 0)

            if self.enabled == False and self.enabled_echo == True:
                print ("Shutting down fuel cell - Enable state is False and enabled_echo is True")
                self.enabled_counter = 0
                self.somethings_gone_wrong_enabling_counter = 0
                self.set_clear_faults(self.tcp_client, 0)
                self.set_fuel_cell_on_off(self.tcp_client, 0)
                self.set_fuel_cell_rack_on_off(self.tcp_client, 0)
                self.enabled_echo = False
        except Exception as e:
            print("Fuel Cell: " + str(e))
            #log_to_file("Fuel cell couldn't enable - Error clearing faults, operation aborted - 805", "error")
            self.enabled_counter = 0
            self.somethings_gone_wrong_enabling_counter = 0
            self.set_clear_faults(self.tcp_client, 0)
            self.set_fuel_cell_on_off(self.tcp_client, 0)
            self.set_fuel_cell_rack_on_off(self.tcp_client, 0)
            self.tcp_timeout += 1
            self.enabled_echo = False
            return

    def set_inputs(self, inputs):
        for module in inputs:
            if module[0] == self.uid:
                if len(module) > 0:
                    if self.override is False:
                        self.inputs = module

                        #########################
                        #self.enabled = True
                        self.enabled = self.inputs[1]
                        #########################

                        # get the kW Setpoint from client
                        self.get_setpoint = self.inputs[2][11]
                        # print(f"get_setpoint at set_inputs function {self.get_setpoint}")
                        # print(self.inputs[2])
        
        return [SET_INPUTS_ACK]

    def get_outputs(self):
        self.outputs[1] = self.enabled_echo
        
        #outputs = copy.deepcopy(self.outputs)
        #return outputs
        return [GET_OUTPUTS_ACK, self.outputs]
    
    def set_page(self, page, form):
        if page == self.website + "_(" + str(self.uid) + ")":  
            isButton = True
            for control in form:
                if "fuel_cell_override" in form:
                    self.actions.insert(0, Actions.CTRL_OVERRIDE_CHANGE.value)
                    self.override = not self.override

                elif "fuel_cell_setpoint_output" == control:
                    self.old_override_setpoint_value = self.override_setpoint_value
                    self.override_setpoint_value = float(form[control])
                    # moved this setpoint check to here so it only updates when the setpoint is changed
                    if self.old_override_setpoint_value != self.override_setpoint_value:
                        self.override_setpoint = float(self.override_setpoint_value)

                elif "fuel_cell_on_off_output" == control:
                    self.fuel_cell_on_off_output = int(form[control])

                elif "fuel_cell_rack_on_off_output" == control:
                    self.fuel_cell_rack_on_off_output = int(form[control])

                elif "fuel_cell_clear_faults_output" == control:
                    self.clear_faults_output = int(form[control])

                # elif "fuel_cell_simulated_amps_output" == control:
                #     self.simulated_amps_output = float(page[1].form[control])

                else:
                    isButton = False
                    if control != "fuel_cell_ipaddr_local":
                        self.dbData[control] = form[control]

            if not isButton:  
                self.save_to_db()

            if len(self.actions) >= 10:
                self.actions.pop()
            
            return [SET_PAGE_ACK]

        elif page == (self.website + "_(" + str(self.uid) + ")/data"):  # It was a json data fetch quest (POST)

            fuel_cell_data = dict()

            # Clear old lists
            #self.actions = []

            # Controller Information
            fuel_cell_data["fuel_cell_name"] = self.name
            fuel_cell_data["fuel_cell_man"] = self.manufacturer
            fuel_cell_data["fuel_cell_fwver"] = self.fw_ver
            fuel_cell_data["fuel_cell_serno"] = self.serial
            fuel_cell_data["fuel_cell_constate"] = str(self.con_state).capitalize()
            fuel_cell_data["fuel_cell_enablestate"] = self.enabled
            fuel_cell_data["fuel_cell_data"] = self.outputs[2]
            fuel_cell_data["fuel_cell_override"] = self.override


            # fuel_cell_data["fuel_cell_rack_data"] = self.all_racks_data
            # # Add rack data 1 rack at a time
            start_index = 1  # Starting index for storing rack data in self.outputs
            for i, self.rack_data in enumerate(self.all_racks_data):
                fuel_cell_data[f"fuel_cell_rack_data{start_index + i}"] = self.rack_data

            fuel_cell_data["fuel_cell_system_mode_parsed"] = self.parsed_system_mode
            fuel_cell_data["fuel_cell_system_state_parsed"] = self.parsed_system_state
            fuel_cell_data["fuel_cell_control_word_parsed"] = self.parsed_control_word
            fuel_cell_data["fuel_cell_status_word_parsed"] = self.parsed_status_word
           
            # Only run if there is alarms 
            if self.any_alarms == 1:
                fuel_cell_data["fuel_cell_alarm_word_1_parsed"] = self.parsed_alarm_1
                fuel_cell_data["fuel_cell_alarm_word_2_parsed"] = self.parsed_alarm_2
                fuel_cell_data["fuel_cell_alarm_word_3_parsed"] = self.parsed_alarm_3
                fuel_cell_data["fuel_cell_rack_alarm_state_1_parsed"] = self.parsed_rack_alarm_state_1
                fuel_cell_data["fuel_cell_rack_alarm_fault_state_2_parsed"] = self.parsed_rack_alarm_fault_state_2
                fuel_cell_data["fuel_cell_rack_fault_state_parsed"] = self.parsed_rack_fault_state
                fuel_cell_data["fuel_cell_rack_e_stop_fault_state_parsed"] = self.parsed_rack_e_stop_fault_state

            fuel_cell_data["fuel_cell_ambient_air_temp"] = self.ambient_air_temp
            fuel_cell_data["fuel_cell_ambient_air_temp_setpoint"] = self.ambient_air_temp_setpoint
            fuel_cell_data["fuel_cell_h2_pressure"] = self.h2_pressure
            fuel_cell_data["fuel_cell_h2_flow"] = self.h2_flow
            fuel_cell_data["fuel_cell_coolant_inlet_temp"] = self.coolant_inlet_temp
            fuel_cell_data["fuel_cell_coolant_outlet_temp"] = self.coolant_outlet_temp
            fuel_cell_data["fuel_cell_coolant_inlet_pressure"] = self.coolant_inlet_pressure
            fuel_cell_data["fuel_cell_coolant_outlet_pressure"] = self.coolant_outlet_pressure
            fuel_cell_data["fuel_cell_coolant_inlet_flow"] = self.coolant_inlet_flow
            fuel_cell_data["fuel_cell_heartbeat_device"] = self.fuel_cell_heartbeat_device
            fuel_cell_data["fuel_cell_standby_mode_indicator"] = self.standby_mode_indicator
            fuel_cell_data["fuel_cell_remote_control_indicator"] = self.remote_control_indicator

            fuel_cell_data["fuel_cell_on_off_output"] = self.fuel_cell_on_off_output
            fuel_cell_data["fuel_cell_rack_on_off_output"] = self.fuel_cell_rack_on_off_output
            fuel_cell_data["fuel_cell_clear_faults_output"] = self.clear_faults_output
            fuel_cell_data["fuel_cell_setpoint_output"] = self.kW_setpoint
            fuel_cell_data["fuel_cell_simulated_amps_output"] = self.simulated_amps_output

            fuel_cell_data["fuel_cell_read_on_off_bit"] = self.read_fuel_cell_on_off_bit
            fuel_cell_data["fuel_cell_read_rack_on_off_bit"] = self.read_fuel_cell_rack_on_off_bit
            fuel_cell_data["fuel_cell_read_clear_faults_bit"] = self.read_fuel_cell_clear_faults_bit

            fuel_cell_data["fuel_cell_scada_setpoint"] = self.get_setpoint 

            fuel_cell_data.update(self.dbData)
            
            return [SET_PAGE_ACK, mod_data]  # data to the database.
            
        else:
            return [SET_PAGE_ACK, ('OK', 200)]  

    def get_page(self):
        routes = [self.website + "_(" + str(self.uid) + ")/data"]
        page = [self.website + "_(" + str(self.uid) + ")", routes]
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
            #log_to_file(f"Unable to save record, may already exist? - 1001", "warning")
            pass

    def kill(self):
        self.stop.set()



    def check_warnings(self):
        self.warnings = Warnings.NONE.value

        # Check for Other Warnings
        # None here

        # Check for Ambient Temperature warnings
        if (self.parsed_alarm_1.get("Ambient Temp Low", False) or 
            self.parsed_alarm_1.get("Ambient Temp High", False) or
            self.parsed_rack_alarm_state_1.get("Ambient Temp High/Low", False)):
            self.update_warnings(Warnings.AMBIENT_TEMP_WARN.value, True)

        # Check for Coolant Pressure warnings
        if (self.parsed_alarm_1.get("Coolant Inlet Pressure Low", False) or
            self.parsed_alarm_1.get("Coolant Inlet Pressure High", False) or
            self.parsed_alarm_1.get("Coolant Outlet Pressure Low", False) or
            self.parsed_alarm_1.get("Coolant Outlet Pressure High", False)):
            self.update_warnings(Warnings.COOLANT_PRESSURE_WARN.value, True)

        # Check for Coolant Temperature warnings
        if (self.parsed_alarm_1.get("Coolant Inlet Temperature Low", False) or
            self.parsed_alarm_1.get("Coolant Outlet Temperature Low", False) or
            self.parsed_alarm_1.get("Coolant Outlet Temperature High", False) or
            self.parsed_alarm_1.get("Coolant Outlet Temperature Warning", False) or
            self.parsed_alarm_1.get("Coolant Outlet Temperature Derate", False) or
            self.parsed_rack_alarm_state_1.get("External Coolant Temp High", False) or
            self.parsed_rack_alarm_state_1.get("External Coolant Temp Low", False)):
            self.update_warnings(Warnings.COOLANT_TEMPERATURE_WARN.value, True)

        # Check for Coolant Flow warnings
        if (self.parsed_alarm_1.get("Coolant Flow Low", False) or
            self.parsed_alarm_1.get("Coolant Flow High", False) or
            self.parsed_alarm_1.get("Loss of Coolant Flow", False) or
            self.parsed_rack_alarm_state_1.get("Low Coolant Level", False)):
            self.update_warnings(Warnings.COOLANT_FLOW_WARN.value, True)

        # Check for Coolant Pump warnings
        # None here

        # Check for H2 Flow warnings
        if (self.parsed_alarm_2.get("H2 Flow Low", False) or
            self.parsed_alarm_2.get("H2 Flow High", False)):
            self.update_warnings(Warnings.H2_FLOW_WARN.value, True)

        # Check for H2 Pressure warnings
        if (self.parsed_alarm_2.get("H2 Pressure Low", False) or
            self.parsed_alarm_2.get("H2 Pressure High", False) or
            self.parsed_rack_alarm_state_1.get("H2 Inlet Pressure Low", False) or
            self.parsed_rack_alarm_state_1.get("H2 Inlet Pressure High", False) or
            self.parsed_rack_fault_state.get("H2 Inlet Pressure High", False)):
            self.update_warnings(Warnings.H2_PRESSURE_WARN.value, True)
    
    def check_alarms(self):
        self.alarms = Alarms.NONE.value
        
        # Check for Other Alarms
        if (self.parsed_rack_alarm_state_1.get("3 Way Valve Failure", False) or
            self.parsed_alarm_2.get("Vent Fan Flow Switch Failure", False) or
            self.parsed_alarm_2.get("DI Water Tank Contact Failure", False) or
            self.parsed_alarm_2.get("DI Water Tank Fail to Fill", False) or
            self.parsed_rack_alarm_state_1.get("Derate Active", False)):
            self.update_alarms(Alarms.OTHER_ALARM.value, True)
            
        # Check for Coolant Pump alarms
        if (self.parsed_alarm_1.get("Coolant Pump Fail to Stop", False) or
            self.parsed_alarm_1.get("Coolant Pump Overload", False)):
            self.update_alarms(Alarms.COOLANT_PUMP_ALARM.value, True)

        # Check for Sensor Failure alarms
        if (self.parsed_alarm_1.get("Coolant Inlet Flow Sensor Fail", False) or
            self.parsed_alarm_1.get("Coolant Inlet Pressure Sensor Fail", False) or
            self.parsed_alarm_1.get("Coolant Inlet Temperature Sensor Fail", False) or
            self.parsed_alarm_1.get("Coolant Outlet Pressure Sensor Fail", False) or
            self.parsed_alarm_1.get("Coolant Outlet Temperature Sensor Fail", False) or
            self.parsed_rack_alarm_state_1.get("Drain Pump or Sensor Failure", False) or
            self.parsed_rack_alarm_state_1.get("External Coolant Temp Sensor Fail", False) or
            self.parsed_rack_fault_state.get("H2 Inlet Pressure Sensor Failure", False) or
            self.parsed_rack_fault_state.get("H2 Level Sensor Failed", False)):
            self.update_alarms(Alarms.SENSOR_FAILURE_ALARM.value, True)
        
        # Check for System Overtemp alarms
        if (self.parsed_alarm_1.get("Overtemp and Insufficient Cooling Capacity", False)):
            self.update_alarms(Alarms.SYSTEM_OVERTEMP_ALARM.value, True)

        # Check for System Pressure alarms
        # Can't find any for this

        # Check for H2 System alarms
        if (self.parsed_alarm_2.get("H2 Meter Failure Device or Comms", False) or
            self.parsed_rack_alarm_state_1.get("H2 Level High Alarm", False)):
            self.update_alarms(Alarms.H2_SYSTEM_ALARM.value, True)

        # Check for VFD alarms
        if (self.parsed_alarm_2.get("VFD Comm Failure", False) or
            self.parsed_alarm_2.get("VFD Drive Failure", False)):
            self.update_alarms(Alarms.VFD_ALARM.value, True)

        # Check for FCPM alarms
        if (self.parsed_rack_alarm_state_1.get("FCPMs Alarm", False) or
            self.parsed_rack_alarm_state_1.get("FCPM Comm Lost", False)):
            self.update_alarms(Alarms.FCPM_ALARM.value, True)

        # Check for Room alarms
        if (self.parsed_alarm_3.get("Process Room Flame Alarm", False) or
            self.parsed_alarm_3.get("Process Room Flame Sensor Fail", False) or
            self.parsed_alarm_3.get("Process Room H2 Gas Warning", False) or
            self.parsed_alarm_3.get("Process Room H2 Gas Alarm", False) or
            self.parsed_alarm_3.get("Process Room H2 Gas Sensor Fail", False) or
            self.parsed_alarm_3.get("Process Room Smoke Alarm", False) or
            self.parsed_alarm_3.get("Cooling Room Smoke Alarm", False)):
            self.update_alarms(Alarms.ROOM_ALARM.value, True)

        # Check for Power Issue alarms
        if (self.parsed_alarm_3.get("Station Power UPS Alarm", False) or
            self.parsed_alarm_3.get("Station Power From Batteries", False)):
            self.update_alarms(Alarms.POWER_ISSUE_ALARM.value, True)

        # Check for Safety Systems alarms

        # Check for Current Limit alarms
        if (self.parsed_rack_alarm_fault_state_2.get("Current Limit Exceeded", False)):
            self.update_alarms(Alarms.CURRENT_LIMIT_ALARM.value, True)

    def check_faults(self):
        self.faults = Faults.NONE.value

        # Check for Other Faults
        if (self.parsed_rack_fault_state.get("Cathode Actuator Failed", False) or
            self.parsed_rack_fault_state.get("Host Communication Lost", False)):
            self.update_faults(Faults.OTHER_FAULT.value, True)

        # Check for System Configuration Faults
        if (self.parsed_alarm_3.get("Invalid System Configuration", False)):
            self.update_faults(Faults.SYSTEM_CONFIG_FAULT.value, True)

        # Check for Relay Faults
        if (self.parsed_rack_fault_state.get("Precharge Relay Failure", False) or
            self.parsed_rack_fault_state.get("Precharge Timeout 1", False) or
            self.parsed_rack_fault_state.get("Precharge Timeout 2", False)):
            self.update_faults(Faults.RELAY_FAULT.value, True)

        # Check for Contactor Faults
        if (self.parsed_rack_fault_state.get("Negative Load Contactor Fail", False) or
            self.parsed_rack_fault_state.get("Positive Load Contactor Fail", False)):
            self.update_faults(Faults.CONTACTOR_FAULT.value, True)

        # Check for H2 Faults

        # Check for Power Supply Faults
        if (self.parsed_rack_fault_state.get("Low Power Limit", False) or
            self.parsed_rack_fault_state.get("Voltage Mismatch", False) or
            self.parsed_rack_fault_state.get("Bus Voltage Sensor Failed", False)):
            self.update_faults(Faults.POWER_FAULT.value, True)

        # Check for FCPM Faults
        if (self.parsed_rack_fault_state.get("FCPMs Fail (Restart Attempts)", False)):
            self.update_faults(Faults.FCPM_FAULT.value, True)

        # Check for E-Stop Faults
        # Going to put all faults in here from e stop fault state from Cummins Modbus
        if (self.parsed_rack_e_stop_fault_state.get("Pressure Switch", False) or
            self.parsed_rack_e_stop_fault_state.get("HWSS", False) or
            self.parsed_rack_e_stop_fault_state.get("Drain Level Low", False) or
            self.parsed_rack_e_stop_fault_state.get("Remote E-Stop", False) or
            self.parsed_rack_e_stop_fault_state.get("Local E-Stop", False) or
            self.parsed_rack_e_stop_fault_state.get("H2 Level Sensor", False) or
            self.parsed_rack_e_stop_fault_state.get("Smoke Detected", False)):
            self.update_faults(Faults.ESTOP_FAULT.value, True)

        # Check for Fail to Start Faults
        if (self.parsed_rack_fault_state.get("Fail to Start", False) or
            self.parsed_rack_fault_state.get("System Fail (Restart Attempts)", False)):
            self.update_faults(Faults.FAIL_TO_START.value, True)


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
            print("Fuel Cell: " + str(e))
          
        try:
            if tx_msg is not None:
                queue[0].put(tx_msg, block=True)
                
        except Exception as e:
            print("Fuel Cell: " + str(e))
            
 
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


# Enums # TODO
class Warnings(Enum):
    NONE = 0 # No Warning Present
    OTHER_WARN = 1 # Other Warnings that don't fit into any category
    AMBIENT_TEMP_WARN = 2  # Ambient Temperature Warnings High/Low
    COOLANT_PRESSURE_WARN = 3  # Coolant Inlet/Outlet Pressure High/Low
    COOLANT_TEMPERATURE_WARN = 4  # Coolant Inlet/Outlet Temperature Low/High/Warning/Derate
    COOLANT_FLOW_WARN = 5  # Coolant Flow Low/High/Loss
    COOLANT_PUMP_WARN = 6 # Coolant Pump Warning
    H2_FLOW_WARN = 7  # H2 Flow Low/High
    H2_PRESSURE_WARN = 8  # H2 Pressure Low/High

class Alarms(Enum):
    NONE = 0 # No Alarm Present
    OTHER_ALARM = 1 # Other Alarms that don't fit into any category
    COOLANT_PUMP_ALARM = 2 # Coolant Pump Fail To Stop/Overload
    SENSOR_FAILURE_ALARM = 3  # Sensor Failures for Coolant Inlet/Outlet (Flow, Pressure, Temperature)
    SYSTEM_OVERTEMP_ALARM = 4  # Exceeding Overtemp or Insufficient Cooling Capacity
    SYSTEM_PRESSURE_ALARM = 5  # Exceeding Pressure Limits
    H2_SYSTEM_ALARM = 6  # H2 Meter Failure, Pressure High/Low, Flow High/Low
    VFD_ALARM = 7  # VFD Comm Failure/Drive Failure
    FCPM_ALARM = 8  # FCPMs Alarm/Comm Lost
    ROOM_ALARM = 9  # Process or Cooling Room Alarms (Flame, Gas, Smoke)
    POWER_ISSUE_ALARM = 10  # Station Power UPS Alarm/Batteries
    SAFETY_SYSTEMS_ALARM = 11  # Invalid System Configuration
    CURRENT_LIMIT_ALARM = 12 # CDA Exceeded

class Faults(Enum):
    NONE = 0
    CONFIG = 1
    LOSS_OF_COMMS = 2
    IO_TIMEOUT = 3
    OTHER_FAULT = 4 # Any other faults that don't fit into a category
    SYSTEM_CONFIG_FAULT = 5 # System Configuration Fault 
    RELAY_FAULT = 6 # Rack Relay Failure
    CONTACTOR_FAULT = 7 # Rack Negative / Positive Load Contactor Failure
    H2_FAULT = 8 # Combined H2 Pressure High or Sensor Failure
    POWER_FAULT = 9 # Power Supply Failure
    FCPM_FAULT = 10 # FCPM Failure even with restart event
    ESTOP_FAULT = 11 # ANY E-STOP Local / Remote  
    FAIL_TO_START = 12 # Rack / System Fail to Start

class Actions(Enum):
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
    #log_to_file(f"Main script execution started.", "info")
    ## Open the database
    #mod = Module()
    #mod.set_state(True)  # Enable the module
