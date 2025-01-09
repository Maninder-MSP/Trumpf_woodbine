
# Versions
# 3.5.24.10.16 - SC - Known good starting point, uses thread.is_alive to prevent module stalling.

from pymodbus.client.sync import ModbusTcpClient as mb_tcp_client
from threading import Thread, Event
from FlexDB import FlexTinyDB
from enum import Enum
import copy
import time
import logging
import os
import inspect

# for simulating data
# import json
from tabulate import tabulate
import textwrap
#####################

import logging
import os
import inspect

# Set up logger
def setup_logging(in_development=True):
    logger = logging.getLogger('HB_logger')
    logger.propagate = False

    try:
        # Remove any existing handlers to avoid duplications
        if logger.hasHandlers():
            logger.handlers.clear()

        # Get the current Python file name and replace the extension with .log
        current_file = os.path.basename(__file__)
        log_file_name = os.path.splitext(current_file)[0] + '.log'

        if in_development:
            handler = logging.FileHandler(log_file_name)  # Log to file in development
        else:
            handler = logging.StreamHandler()  # Log to CLI in production

        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG if in_development else logging.INFO)
    
    except Exception as e:
        print(f"Error setting up logger: {e}")

    return logger

def log_message(logger, message, log_level='info'):
    try:
        # Change this prefix if you want to use it for a different module
        prefix = "HITHIUM: "
        frame = inspect.stack()[1]
        python_line = frame.lineno
        full_message = f"{prefix}{message} line: {python_line}"

        log_method = getattr(logger, log_level.lower(), logger.info)
        log_method(full_message)
    
    except Exception as e:
        print(f"Error logging message: {e}")

# If you want to log to file put in_development as True, otherwise False for production
in_development = True  # Set to False for production
HB_logger = setup_logging(in_development)

# Example of using the logger
# If logger in_development = True it will print to file - if False it will print to CLI
# log_message(HB_logger, "System started", log_level='info')
# log_message(HB_logger, "Potential issue detected", log_level='warning')
# log_message(HB_logger, "Critical error occurred", log_level='error')

# Database
db = FlexTinyDB()

# Enums
class Warnings(Enum):
    NONE = 0                                                                                        # "Warning: Battery - No Warning present"
    OVERTEMP = 1
    UNDERTEMP = 2
    OVERCHARGE = 3
    OVERDISCHARGE = 4
    OVERVOLT = 5
    UNDERVOLT = 6
    SOC_LOW = 7
    SOC_HIGH = 8
    VOLT_DIFF = 9
    TEMP_DIFF = 10
    CURRENT_DIFF = 11
    OTHER = 12
    CONFIG = 13
    INSULATION = 14
    SOC_DRIFT = 15  # The SoC Between at least two active racks is > 10%


class Alarms(Enum):
    NONE = 0                                                                                        # "Alarm: Battery - No Alarm present"
    OVERTEMP = 1
    UNDERTEMP = 2
    OVERCHARGE = 3
    OVERDISCHARGE = 4
    OVERVOLT = 5
    UNDERVOLT = 6
    SOC_LOW = 7
    SOC_HIGH = 8
    VOLT_DIFF = 9
    TEMP_DIFF = 10
    CURRENT_DIFF = 11
    OTHER = 12
    CONFIG = 13
    INSULATION = 14
    RACK_OFFLINE = 15                                                                               # At least 1 battery rack is offline.


class Faults(Enum):
    NONE = 0
    OVERTEMP = 1
    UNDERTEMP = 2
    OVERCHARGE = 3
    OVERDISCHARGE = 4
    OVERVOLT = 5
    UNDERVOLT = 6
    SOC_LOW = 7
    SOC_HIGH = 8
    VOLT_DIFF = 9
    TEMP_DIFF = 10
    LOSS_OF_COMMS = 11
    OTHER = 12
    CONFIG = 13
    INSULATION = 14
    CURRENT_DIFF = 15


class Actions(Enum):                                                                                # Track the manual interactions with the html page
    NONE = 0
    IP_ADDRESS_CHANGE = 1
    CTRL_OVERRIDE_CHANGE = 2
    CONTACTOR_TOGGLE_CHANGE = 3
    ALARM_RESET_CHANGE = 4
    # WARNING_TEMP_MIN_CHANGE = 5
    # WARNING_TEMP_MAX_CHANGE = 6
    # ALARM_TEMP_MIN_CHANGE = 7
    # ALARM_TEMP_MAX_CHANGE = 8
    # FAULT_TEMP_MIN_CHANGE = 9
    # FAULT_TEMP_MAX_CHANGE = 10

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
    # Class-level lists and dictionaries for registers
    # These are the registers from the Hithium Modbus Map v1.6 excel document
    # This needs to be updated if the Hithium Modbus Map document updates

    # These are the registers on the "System Information" tab in the Excel document
    # registers 0x0000 - 0x0111
    try:
        main_system_information_registers = {
            "Connect Disconnect": {"reg_unit_id": 1, "data_low": 1, "data_hi": 2, "register": 0x0001, "type": "u16", "bitfield": False, "enum": True, "units": None, "scaling_factor": None, "access": "W", "origin": "EMS", "reg_count": 1, 
                                "labels": {1: "Connect", 2: "Disconnect"}}, # "info": "0x1 = connect, 0x2 = disconnect",
            "SBMU Heartbeat": {"reg_unit_id": 1, "data_low": 0, "data_hi": 1, "register": 0x0002, "type": "u16", "bitfield": False, "enum": True, "units": None, "scaling_factor": None, "access": "W", "origin": "EMS", "reg_count": 1,
                            "labels": {0: "tik", 1: "tok"}}, # "info": "Heartbeat 0x1:1, 0x0:0",
            "Year": {"reg_unit_id": 1, "data_low": 0, "data_hi": 9999, "register": 0x0003, "type": "u16", "bitfield": False, "enum": False, "units": None, "scaling_factor": None, "access": "W", "origin": "EMS", "reg_count": 1}, # "info": "Year 0~99"
            "Month": {"reg_unit_id": 1, "data_low": 1, "data_hi": 12, "register": 0x0004, "type": "u16", "bitfield": False, "enum": False, "units": None, "scaling_factor": None, "access": "W", "origin": "EMS", "reg_count": 1}, #  "info": "Month 1~12"
            "Day": {"reg_unit_id": 1, "data_low": 1, "data_hi": 31, "register": 0x0005, "type": "u16", "bitfield": False, "enum": False, "units": None, "scaling_factor": None, "access": "W", "origin": "EMS", "reg_count": 1}, #  "info": "Day 1~31"
            "Hour": {"reg_unit_id": 1, "data_low": 0, "data_hi": 24, "register": 0x0006, "type": "u16", "bitfield": False, "enum": False, "units": None, "scaling_factor": None, "access": "W", "origin": "EMS", "reg_count": 1}, # "info": "Hour 0~23"
            "Minute": {"reg_unit_id": 1, "data_low": 0, "data_hi": 59, "register": 0x0007, "type": "u16", "bitfield": False, "enum": False, "units": None, "scaling_factor": None, "access": "W", "origin": "EMS", "reg_count": 1}, # "info": "Minute 0~59"
            "Second": {"reg_unit_id": 1, "data_low": 0, "data_hi": 59, "register": 0x0008, "type": "u16", "bitfield": False, "enum": False, "units": None, "scaling_factor": None, "access": "W", "origin": "EMS", "reg_count": 1}, # "info": "Second 0~59"
            "Switch On": {"reg_unit_id": 1, "data_low": 0, "data_hi": 1, "register": 0x000B, "type": "u16", "bitfield": False, "enum": True, "units": None, "scaling_factor": None, "access": "W", "origin": "EMS", "reg_count": 1,
                        "labels": {1: "Start"}}, # "info": "1: Start"
            "Switch Off": {"reg_unit_id": 1, "data_low": 0, "data_hi": 1, "register": 0x000C, "type": "u16", "bitfield": False, "enum": True, "units": None, "scaling_factor": None, "access": "W", "origin": "EMS", "reg_count": 1,
                        "labels": {1: "Start"}}, # "info": "1: Start"
            "Insulation Switch": {"reg_unit_id": 1, "data_low": 1, "data_hi": 2, "register": 0x0100, "type": "u16", "bitfield": False, "enum": True, "units": None, "scaling_factor": None, "access": "W", "origin": "HMI", "reg_count": 1,
                                "labels": {1: "Enable", 2: "Disable"}}, # "info": "1: Enable, 2: Disable - insulation sampling function switch"
            "Reset PCS Alarm": {"reg_unit_id": 1, "data_low": 0, "data_hi": 1, "register": 0x0101, "type": "u16", "bitfield": False, "enum": True, "units": None, "scaling_factor": None, "access": "W", "origin": "EMS", "reg_count": 1,
                                "labels": {1: "Reset PCS control fail Alarm"}}, # "info": "1: Reset PCS control fail Alarm"
            "Min Parallel Racks": {"reg_unit_id": 1, "data_low": 0, "data_hi": 10, "register": 0x0102, "type": "u16", "bitfield": False, "enum": False, "units": None, "scaling_factor": None, "access": "W", "origin": "EMS, HMI", "reg_count": 1}, # "info": "Minimum number of parallel racks"
            # "SBMU IP Address": {"reg_unit_id": 1, "data_low": 0, "data_hi": 999, "register": 0x0103, "type": "u16", "bitfield": False, "enum": False, "units": None, "scaling_factor": None, "access": "W", "origin": "EMS, HMI", "reg_count": 4}, # "info": "SBMU local IP address"
            # "Gateway IP Address": {"reg_unit_id": 1, "data_low": 0, "data_hi": 999, "register": 0x0107, "type": "u16", "bitfield": False, "enum": False, "units": None, "scaling_factor": None, "access": "W", "origin": "EMS, HMI", "reg_count": 4}, #  "info": "Gateway IP address"
            # "Subnet Mask": {"reg_unit_id": 1, "data_low": 0, "data_hi": 999, "register": 0x010B, "type": "u16", "bitfield": False, "enum": False, "units": None, "scaling_factor": None, "access": "W", "origin": "EMS, HMI", "reg_count": 4}, # "info": "Subnet mask"
            # "Client 1 Port Number": {"reg_unit_id": 1, "data_low": 0, "data_hi": 999, "register": 0x010F, "type": "u16", "bitfield": False, "enum": False, "units": None, "scaling_factor": None, "access": "W", "origin": "EMS, HMI", "reg_count": 1}, # "info": "Client 1 port number"
            # "Client 2 Port Number": {"reg_unit_id": 1, "data_low": 0, "data_hi": 999, "register": 0x0110, "type": "u16", "bitfield": False, "enum": False, "units": None, "scaling_factor": None, "access": "W", "origin": "EMS, HMI", "reg_count": 1}, # "info": "Client 2 port number"
            # "Client 3 Port Number": {"reg_unit_id": 1, "data_low": 0, "data_hi": 999, "register": 0x0111, "type": "u16", "bitfield": False, "enum": False, "units": None, "scaling_factor": None, "access": "W", "origin": "EMS, HMI", "reg_count": 1} # "info": "Client 3 port number"
        }

        # registers 0x1001 - 0x1083
        system_information_SBMU_registers = {
            "Connection Status": {"reg_unit_id": 1, "data_low": 0, "data_hi": 3, "register": 0x1001, "type": "u16", "bitfield": False, "enum": True, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1,
                                "labels": {0: "No Process", 1: "Start Connection", 2: "Connected", 3: "Process End"}}, # "info": "0x0: No Process, 0x1: Start Connection, 0x2: Connected, 0x3: Process End"},
            "Rack Enabled Status": {"reg_unit_id": 1, "data_low": 0, "data_hi": 65535, "register": 0x1002, "type": "u16", "bitfield": True, "enum": False, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1,
                                    "labels": ["Rack1", "Rack2", "Rack3", "Rack4", "Rack5", "Rack6", "Rack7", "Rack8", "Rack9", "Rack10", "Rack11", "Rack12", "Rack13", "Rack14", "Rack15", "Rack16"]}, # 0 = disabled, 1 = enabled
            "Rack Online Status": {"reg_unit_id": 1, "data_low": 0, "data_hi": 65535, "register": 0x1005, "type": "u16", "bitfield": True, "enum": False, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1,
                                "labels": ["Rack1", "Rack2", "Rack3", "Rack4", "Rack5", "Rack6", "Rack7", "Rack8", "Rack9", "Rack10", "Rack11", "Rack12", "Rack13", "Rack14", "Rack15", "Rack16"]}, # 0 = offline, 1 = online # "info": "Bit0: Rack1 online status enabled / disabled etc."},
            "System Operation State": {"reg_unit_id": 1, "data_low": 0, "data_hi": 4, "register": 0x1006, "type": "u16", "bitfield": False, "enum": True, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1,
                                    "labels": {0: "Normal", 1: "No charge", 2: "No discharge", 3: "Standby", 4: "Stop"}}, # "info": "0: Normal, 1: No charge, 2: No discharge, 3: Standby, 4: Stop"},
            "System Charge State": {"reg_unit_id": 1, "data_low": 0, "data_hi": 2, "register": 0x1007, "type": "u16", "bitfield": False, "enum": True, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1,
                                    "labels": {0: "Idle", 1: "Discharging", 2: "Charging"}}, # "info": "0x0: Idle, 0x1: Discharging, 0x2: Charging"},
            "SBMU Alarm State": {"reg_unit_id": 1, "data_low": 0, "data_hi": 65535, "register": 0x1008, "type": "u16", "bitfield": True, "enum": False, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1,
                                "labels": ["BMS internal communication failure", "PCS communication fault alarm", "PCS control failure alarm", "EMS communication fault alarm", 
                                "Racks vol diff over large warning", "Racks current diff over large warning", "Emergency stop", "Air conditioner communication failure", 
                                "Reserved_bit8", "Reserved_bit9", "Reserved_bit10", "Reserved_bit11", "Reserved_bit12", "Reserved_bit13", "Reserved_bit14", "Reserved_bit15"]}, # 0 = normal, 1 = alarm}
            "Rack Alarm I Summary": {"reg_unit_id": 1, "data_low": 0, "data_hi": 65535, "register": 0x1009, "type": "u16", "bitfield": True, "enum": False, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1,
                                    "labels": ["RackVolHigh", "RackVolLow", "CellVolHigh", "CellVolLow", "DsgOverCurr", "ChgOverCurr", "DsgTempHigh", "DsgTempLow", "ChgTempHigh", "ChgTempLow",
                                "InsulationLow", "TerminalTempHigh", "HVBTempHigh", "CellVolDiffHigh", "CellTempDiffHigh", "SOC Low"]},
            "Rack Alarm II Summary": {"reg_unit_id": 1, "data_low": 0, "data_hi": 65535, "register": 0x100A, "type": "u16", "bitfield": True, "enum": False, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1,
                                    "labels": ["RackVolHigh", "RackVolLow", "CellVolHigh", "CellVolLow", "DsgOverCurr", "ChgOverCurr", "DsgTempHigh", "DsgTempLow", "ChgTempHigh", "ChgTempLow",
                                "InsulationLow", "TerminalTempHigh", "HVBTempHigh", "CellVolDiffHigh", "CellTempDiffHigh", "SOC Low"]},
            "Rack Alarm III Summary": {"reg_unit_id": 1, "data_low": 0, "data_hi": 65535, "register": 0x100B, "type": "u16", "bitfield": True, "enum": False, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1,
                                    "labels": ["RackVolHigh", "RackVolLow", "CellVolHigh", "CellVolLow", "DsgOverCurr", "ChgOverCurr", "DsgTempHigh", "DsgTempLow", "ChgTempHigh", "ChgTempLow",
                                "InsulationLow", "TerminalTempHigh", "HVBTempHigh", "CellVolDiffHigh", "CellTempDiffHigh", "SOC Low"]},
            "System Total Voltage": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x100C, "type": "u16", "bitfield": False, "enum": False, "units": "V", "scaling_factor": 0.1, "access": "R", "origin": "SBMU", "reg_count": 1}, # "info": "System Total Voltage is average value of Enabled Racks & Contactor is closed."},
            "System Total Current": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x100D, "type": "int16", "bitfield": False, "enum": False, "units": "A", "scaling_factor": 0.1, "access": "R", "origin": "SBMU", "reg_count": 1}, # "info": "Charge current is negative and discharge is positive."},
            "System SOC": {"reg_unit_id": 1, "data_low": 0, "data_hi": 100, "register": 0x100E, "type": "u16", "bitfield": False, "enum": False, "units": "%" , "scaling_factor": 0.1, "access": "R", "origin": "SBMU", "reg_count": 1}, # "info": "System SOC in permille (‰) 0~1000 SoC is presented 101 if all the racks are disabled."},
            "System SOH": {"reg_unit_id": 1, "data_low": 0, "data_hi": 100, "register": 0x100F, "type": "u16", "bitfield": False, "enum": False, "units": "%", "scaling_factor": 0.1, "access": "R", "origin": "SBMU", "reg_count": 1}, # "info": "Average SOH of Enabled Racks; SOH is 0 if all the battery racks are disabled."},
            "System Insulation": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x1010, "type": "u16", "bitfield": False, "enum": False, "units": "kΩ", "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1}, # "info": "System Insulation Value is a minimum value of all racks' insulation. Default program is BMS IDM is only active before contactor connection; Only for BMS self-check before connection."},
            "System Enable Charge Energy": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x1011, "type": "u16", "bitfield": False, "enum": False, "units": "kWh", "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1}, # "info": "System Enable Charge Energy sum up value of enabled racks' available charge energy. System Enable Charge Energy is presented 0 if all the racks are disabled."}, 
            "System Enable Discharge Energy": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x1012, "type": "u16", "bitfield": False, "enum": False, "units": "kWh", "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1}, # "info": "System Enable Discharge Energy sum up value of enabled racks' available discharge energy System Enable Discharge Energy is presented 0 if all the racks are disabled."},
            "System Enable Max Charge Current": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x1013, "type": "u16", "bitfield": False, "enum": False, "units": "A", "scaling_factor": 0.1, "access": "R", "origin": "SBMU", "reg_count": 1}, # "info": "Connected Rack No * Rack Limitation Current"},
            "System Enable Max Discharge Current": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x1014, "type": "u16", "bitfield": False, "enum": False, "units": "A", "scaling_factor": 0.1, "access": "R", "origin": "SBMU", "reg_count": 1}, # "info": "Connected Rack No * Rack Limitation Current"},
            "Rack Current Difference": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x1015, "type": "int16", "bitfield": False, "enum": False, "units": "A", "scaling_factor": 0.1, "access": "R", "origin": "SBMU", "reg_count": 1}, # "info": "The current diff between enabled racks' Max and Min current"},
            "Rack Voltage Difference": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x1016, "type": "u16", "bitfield": False, "enum": False, "units": "V", "scaling_factor": 0.1, "access": "R", "origin": "SBMU", "reg_count": 1}, # "info": "The rack voltage diff between enabled racks' Max and Min voltage"},
            "System Max Vol Cell Rack ID": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x1017, "type": "u16", "bitfield": False, "enum": False, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1}, # "info": "1 - Max Rack ID"},
            "System Max Vol Cell Slave ID": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x1018, "type": "u16", "bitfield": False, "enum": False, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1}, # "info": "Slave number where the Cell is located, 1 - Max Slave ID."},
            "System Max Vol Cell ID": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x1019, "type": "u16", "bitfield": False, "enum": False, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1}, # "info": "The ID of the Cell in the Cell compartment.1 - Slave Cell number."},
            "System Max Cell Voltage": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x101A, "type": "u16", "bitfield": False, "enum": False, "units": "mV", "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1}, 
            "System Min Vol Cell Rack ID": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x101B, "type": "u16", "bitfield": False, "enum": False, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1}, # "info": "1 - Max Rack ID"},
            "System Min Vol Cell Slave ID": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x101C, "type": "u16", "bitfield": False, "enum": False, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1}, # "info": "Slave number where the Cell is located, 1 - Max Slave ID."},
            "System Min Vol Cell ID": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x101D, "type": "u16", "bitfield": False, "enum": False, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1}, # "info": "The ID of the Cell in the Cell compartment.1 - Slave Cell number."},
            "System Min Cell Voltage": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x101E, "type": "u16", "bitfield": False, "enum": False, "units": "mV", "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1}, 
            "System Average Voltage": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x101F, "type": "u16", "bitfield": False, "enum": False, "units": "mV", "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1}, # "info": "All racks's average voltage"},
            "System Max Temp Cell Rack ID": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x1020, "type": "u16", "bitfield": False, "enum": False, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1}, # "info": "1 - Max Rack ID"},
            "System Max Temp Cell Slave ID": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x1021, "type": "u16", "bitfield": False, "enum": False, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1}, # "info": "Slave number where the Cell is located, 1 - Max Slave ID."},
            "System Max Temp Cell ID": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x1022, "type": "u16", "bitfield": False, "enum": False, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1}, # "info": "The ID of the Cell in the Cell compartment.1 - Slave Cell number."},
            "System Max Cell Temperature": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x1023, "type": "int16", "bitfield": False, "enum": False, "units": "℃", "scaling_factor": 0.1, "access": "R", "origin": "SBMU", "reg_count": 1}, 
            "System Min Temp Cell Rack ID": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x1024, "type": "u16", "bitfield": False, "enum": False, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1}, # "info": "1 - Max Rack ID"},
            "System Min Temp Cell Slave ID": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x1025, "type": "u16", "bitfield": False, "enum": False, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1}, # "info": "Slave number where the Cell is located, 1 - Max Slave ID."},
            "System Min Temp Cell ID": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x1026, "type": "u16", "bitfield": False, "enum": False, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1}, # "info": "The ID of the Cell in the Cell compartment.1 - Slave Cell number."},
            "System Min Cell Temperature": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x1027, "type": "int16", "bitfield": False, "enum": False, "units": "℃", "scaling_factor": 0.1, "access": "R", "origin": "SBMU", "reg_count": 1}, 
            "System Average Temperature": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x1028, "type": "int16", "bitfield": False, "enum": False, "units": "℃", "scaling_factor": 0.1, "access": "R", "origin": "SBMU", "reg_count": 1}, # "info": "All racks's average temperature"},
            "SBMU CBMU comms fault alarm": {"reg_unit_id": 1, "data_low": 0, "data_hi": 65535, "register": 0x1029, "type": "u16", "bitfield": True, "enum": False, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1,
                                            "labels": ["CBMU1", "CBMU2", "CBMU3", "CBMU4", "CBMU5", "CBMU6", "CBMU7", "CBMU8", "CBMU9", "CBMU10", "CBMU11", "CBMU12", "CBMU13", "CBMU14", "CBMU15", "CBMU16"]}, # 0 = normal, 1 = alarm
            "SBMU CBMU comms fault alarm 2": {"reg_unit_id": 1, "data_low": 0, "data_hi": 65535, "register": 0x102A, "type": "u16", "bitfield": True, "enum": False, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1,
                                            "labels": ["CBMU17", "CBMU18", "CBMU19", "CBMU20", "CBMU21", "CBMU22", "CBMU23", "CBMU24", "CBMU25", "CBMU26", "CBMU27", "CBMU28", "CBMU29", "CBMU30", "CBMU31", "CBMU32"]}, # 0 = normal, 1 = alarm
            "SBMU output dry contact status": {"reg_unit_id": 1, "data_low": 0, "data_hi": 65535, "register": 0x102B, "type": "u16", "bitfield": True, "enum": False, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1,
                                            "labels": ["DryContact1", "DryContact2", "Reserved_bit2", "Reserved_bit3", "Reserved_bit4", "Reserved_bit5", "Reserved_bit6", "Reserved_bit7", "Reserved_bit8",
                                "Reserved_bit9", "Reserved_bit10", "Reserved_bit11", "Reserved_bit12", "Reserved_bit13", "Reserved_bit14", "Reserved_bit15"]}, # 0 = closed, 1 = open
            "Rack enable status 2": {"reg_unit_id": 1, "data_low": 0, "data_hi": 65535, "register": 0x102C, "type": "u16", "bitfield": True, "enum": False, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1,
                                    "labels": ["Rack17", "Rack18", "Rack19", "Rack20", "Rack21", "Rack22", "Rack23", "Rack24", "Rack25", "Rack26", "Rack27", "Rack28", "Rack29", "Rack30", "Rack31", "Rack32"]}, # 0 = disabled, 1 = enabled
            "Rack online status 2": {"reg_unit_id": 1, "data_low": 0, "data_hi": 65535, "register": 0x102F, "type": "u16", "bitfield": True, "enum": False, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1,
                                    "labels": ["Rack17", "Rack18", "Rack19", "Rack20", "Rack21", "Rack22", "Rack23", "Rack24", "Rack25", "Rack26", "Rack27", "Rack28", "Rack29", "Rack30", "Rack31", "Rack32"]}, # 0 = offline, 1 = online
            # Changed the map here as this came in two seperate registers however they need combining for high and low values. So i replaced the reg_count with 2 and type to u32 so that it combines them in the program
            # "Accumulation charge energy MSB": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x1030, "type": "u16", "bitfield": False, "enum": False, "units": "kWh", "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1}, # "info": "Accumulation charge energy high 16 bits."},
            # "Accumulation charge energy LSB": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x1031, "type": "u16", "bitfield": False, "enum": False, "units": "kWh", "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1}, # "info": "Accumulation charge energy low 16 bits."},
            "Accumulation charge energy": {"reg_unit_id": 1, "data_low": 1, "data_hi": 99, "register": 0x1030, "type": "u32", "bitfield": False, "enum": False, "units": "kWh", "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 2, "info": "Accumulation charge energy high and low 16 bits."},
            # Same for discharge as well (see above accumulation charge energy for more info)
            # "Accumulation discharge energy MSB": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x1032, "type": "u16", "bitfield": False, "enum": False, "units": "kWh", "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1}, # "info": "Accumulation discharge energy high 16 bits."},
            # "Accumulation discharge energy LSB": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x1033, "type": "u16", "bitfield": False, "enum": False, "units": "kWh", "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1}, # "info": "Accumulation discharge energy low 16 bits."},
            "Accumulation discharge energy": {"reg_unit_id": 1, "data_low": 1, "data_hi": 99, "register": 0x1032, "type": "u32", "bitfield": False, "enum": False, "units": "kWh", "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 2, "info": "Accumulation discharge energy high 16 bits."},
            "Number of racks in service": {"reg_unit_id": 1, "data_low": 1, "data_hi": 10, "register": 0x1034, "type": "u16", "bitfield": False, "enum": False, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1}, # "info": "Number of rack closed by contactor"},
            "Number of total racks": {"reg_unit_id": 1, "data_low": 1, "data_hi": 10, "register": 0x1035, "type": "u16", "bitfield": False, "enum": False, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1}, # "info": "Total number of racks in the system"},
            "Minimum number of parallel racks": {"reg_unit_id": 1, "data_low": 1, "data_hi": 5, "register": 0x1036, "type": "u16", "bitfield": False, "enum": False, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1}, # "info": "When the number of parallel racks is lower than the minimum number of parallel racks, the system status is standby, and the PCS is requested to stop charging and discharging."},
            "Max discharge power": {"reg_unit_id": 1, "data_low": 1, "data_hi": 99, "register": 0x1037, "type": "u16", "bitfield": False, "enum": False, "units": "kW", "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1}, # "info": "Maximum allowable discharge power of the system"},
            "Max charge power": {"reg_unit_id": 1, "data_low": 1, "data_hi": 99, "register": 0x1038, "type": "u16", "bitfield": False, "enum": False, "units": "kW", "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1}, # "info": "Maximum allowable charging power of the system"},
            "BMS heartbeat": {"reg_unit_id": 1, "data_low": 0, "data_hi": 65535, "register": 0x1039, "type": "u16", "bitfield": False, "enum": False, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1}, # "info": "SBMU heartbeat indication, 0-255, accumulating at intervals of 1s"},
            "Rack fault summary": {"reg_unit_id": 1, "data_low": 0, "data_hi": 65535, "register": 0x103A, "type": "u16", "bitfield": True, "enum": False, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1,
                        "labels": ["BMU hardware", "CBMU hardware", "Fuse protector", "Contactor adhesion", "BMU communication", "SBMU communication", "Current sensor", "IMD", "Disconnector Open when Rack Enabled", "NTC",
                                "Reserved_bit10", "Emergency stop", "MSD", "Door access", "Reserved_bit14", "Reserved_bit15"]}, # 0 = normal, 1 = fault
            "Alarm": {"reg_unit_id": 1, "data_low": 0, "data_hi": 65535, "register": 0x103E, "type": "u16", "bitfield": True, "enum": False, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1,
                    "labels": ["Electric meter comms fault", "IO module comms fault", "FFS alarm", "FFS fault alarm", "Aerosol alarm", "Emergency stop detection alarm", "IO module QF1 alarm", "IO module front right door access alarm", 
                                "IO module SPD1 alarm", "IO module SPD2 alarm", "IO module front left door access alarm", "IO module rear left door access alarm", "QS D15 alarm", "IO module rear right door access alarm",
                                "Air conditioning comms failure", "UPS comms failure"]}, # 0 = normal, 1 = alarm
            # "Meter info Phase A current": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x1043, "type": "u32", "bitfield": False, "enum": False, "units": "A", "scaling_factor": 0.001, "access": "R", "origin": "SBMU", "reg_count": 2, "info": "A / 1000"},
            # "Meter info Phase B current": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x1045, "type": "u32", "bitfield": False, "enum": False, "units": "A", "scaling_factor": 0.001, "access": "R", "origin": "SBMU", "reg_count": 2, "info": "A / 1000"},
            # "Meter info Phase C current": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x1047, "type": "u32", "bitfield": False, "enum": False, "units": "A", "scaling_factor": 0.001, "access": "R", "origin": "SBMU", "reg_count": 2, "info": "A / 1000"},
            # "Meter info Phase AB voltage": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x1049, "type": "u32", "bitfield": False, "enum": False, "units": "V", "scaling_factor": 0.01, "access": "R", "origin": "SBMU", "reg_count": 2, "info": "V / 100"},
            # "Meter info Phase BC voltage": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x104B, "type": "u32", "bitfield": False, "enum": False, "units": "V", "scaling_factor": 0.01, "access": "R", "origin": "SBMU", "reg_count": 2, "info": "V / 100"},
            # "Meter info Phase AC voltage": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x104D, "type": "u32", "bitfield": False, "enum": False, "units": "V", "scaling_factor": 0.01, "access": "R", "origin": "SBMU", "reg_count": 2, "info": "V / 100"},
            # "Meter info active power": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x104F, "type": "int32", "bitfield": False, "enum": False, "units": "W", "scaling_factor": 10, "access": "R", "origin": "SBMU", "reg_count": 2, "info": "W / 0.1"},
            # "Meter info inactive power": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x1051, "type": "int32", "bitfield": False, "enum": False, "units": "var", "scaling_factor": 10, "access": "R", "origin": "SBMU", "reg_count": 2, "info": "var / 0.1"},
            # "Meter info apparent power": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x1053, "type": "u32", "bitfield": False, "enum": False, "units": "VA", "scaling_factor": 10, "access": "R", "origin": "SBMU", "reg_count": 2, "info": "VA / 0.1"},
            # "Meter info power factor": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x1055, "type": "int32", "bitfield": False, "enum": False, "units": None, "scaling_factor": -0.001, "access": "R", "origin": "SBMU", "reg_count": 2, "info": "Power factor 1000 - not sure why this is a negative scaling factor??? - Gareth"},
            # "IO module QF1 circuit breaker": {"reg_unit_id": 1, "data_low": 0, "data_hi": 1, "register": 0x1057, "type": "u16", "bitfield": False, "enum": True, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1,
            #                                 "labels": {0: "Open", 1: "Closed"}}, # "info": "1:Closed, 0:Open"},
            # "IO module front right door access control": {"reg_unit_id": 1, "data_low": 0, "data_hi": 1, "register": 0x1058, "type": "u16", "bitfield": False, "enum": True, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1,
            #                                             "labels": {0: "Open", 1: "Closed"}}, # "info": "1:Closed, 0:Open"},
            # "IO module SPD1": {"reg_unit_id": 1, "data_low": 0, "data_hi": 1, "register": 0x1059, "type": "u16", "bitfield": False, "enum": True, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1,
            #                 "labels": {0: "Open", 1: "Closed"}}, # "info": "1:Closed, 0:Open"},
            # "IO module SPD2": {"reg_unit_id": 1, "data_low": 0, "data_hi": 1, "register": 0x105A, "type": "u16", "bitfield": False, "enum": True, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1,
            #                 "labels": {0: "Open", 1: "Closed"}}, # "info": "1:Closed, 0:Open"},
            # "IO module front left door access control": {"reg_unit_id": 1, "data_low": 0, "data_hi": 1, "register": 0x105B, "type": "u16", "bitfield": False, "enum": True, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1,
            #                                             "labels": {0: "Open", 1: "Closed"}}, # "info": "1:Closed, 0:Open"},
            # "IO module back left door access control": {"reg_unit_id": 1, "data_low": 0, "data_hi": 1, "register": 0x1069, "type": "u16", "bitfield": False, "enum": True, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1,
            #                                             "labels": {0: "Open", 1: "Closed"}}, # "info": "1:Closed, 0:Open"},
            # "IO module back right door access control": {"reg_unit_id": 1, "data_low": 0, "data_hi": 1, "register": 0x106A, "type": "u16", "bitfield": False, "enum": True, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1,
            #                                             "labels": {0: "Open", 1: "Closed"}}, # "info": "1:Closed, 0:Open"},
            # "Air con software version": {"reg_unit_id": 1, "data_low": 2, "data_hi": 2, "register": 0x106B, "type": "u16", "bitfield": False, "enum": False, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1}, 
            "Air con unit running status": {"reg_unit_id": 1, "data_low": 0, "data_hi": 1, "register": 0x106C, "type": "u16", "bitfield": False, "enum": True, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1,
                                            "labels": {0: "Stop", 1: "Operating", 2: "Not selected"}}, # "info": "0:Stop, 1:Operating, 2:Not selected"},
            # "Air con internal fan status": {"reg_unit_id": 1, "data_low": 0, "data_hi": 1, "register": 0x106D, "type": "u16", "bitfield": False, "enum": True, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1,
            #                                 "labels": {0: "Stop", 1: "Operating", 2: "Not selected"}}, # "info": "0:Stop, 1:Operating, 2:Not selected"},
            # "Air con external fan status": {"reg_unit_id": 1, "data_low": 0, "data_hi": 1, "register": 0x106E, "type": "u16", "bitfield": False, "enum": True, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1,
            #                                 "labels": {0: "Stop", 1: "Operating", 2: "Not selected"}}, # "info": "0:Stop, 1:Operating, 2:Not selected"},
            # "Air con compressor status": {"reg_unit_id": 1, "data_low": 0, "data_hi": 1, "register": 0x106F, "type": "u16", "bitfield": False, "enum": True, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1,
            #                             "labels": {0: "Stop", 1: "Operating", 2: "Not selected"}}, # "info": "0:Stop, 1:Operating, 2:Not selected"},
            # "Air con heater status": {"reg_unit_id": 1, "data_low": 0, "data_hi": 1, "register": 0x1070, "type": "u16", "bitfield": False, "enum": True, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1,
            #                         "labels": {0: "Stop", 1: "Operating", 2: "Not selected"}}, # "info": "0:Stop, 1:Operating, 2:Not selected"},
            # "Air con emergency fan status": {"reg_unit_id": 1, "data_low": 0, "data_hi": 1, "register": 0x1071, "type": "u16", "bitfield": False, "enum": True, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1,
            #                                 "labels": {0: "Stop", 1: "Operating", 2: "Not selected"}}, # "info": "0:Stop, 1:Operating, 2:Not selected"},
            # "Air con evaporator Temp": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x1072, "type": "int16", "bitfield": False, "enum": False, "units": "℃", "scaling_factor": 0.1, "access": "R", "origin": "SBMU", "reg_count": 1}, 
            # "Air con outdoor Temp": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x1073, "type": "int16", "bitfield": False, "enum": False, "units": "℃", "scaling_factor": 0.1, "access": "R", "origin": "SBMU", "reg_count": 1}, 
            # "Air con condenser Temp": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x1074, "type": "int16", "bitfield": False, "enum": False, "units": "℃", "scaling_factor": 0.1, "access": "R", "origin": "SBMU", "reg_count": 1}, 
            # "Air con indoor Temp": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x1075, "type": "int16", "bitfield": False, "enum": False, "units": "℃", "scaling_factor": 0.1, "access": "R", "origin": "SBMU", "reg_count": 1}, 
            # "Air con humidity": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x1076, "type": "u16", "bitfield": False, "enum": False, "units": "%", "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1}, 
            "Air con remote control": {"reg_unit_id": 1, "data_low": 0, "data_hi": 1, "register": 0x107A, "type": "u16", "bitfield": False, "enum": True, "units": None, "scaling_factor": None, "access": "RW", "origin": "SBMU", "reg_count": 1,
                                    "labels": {0: "Close", 1: "Open"}}, # "info": "0x00 = Close, 0x01 = Open"},
            # "Air con info 1": {"reg_unit_id": 1, "data_low": 0, "data_hi": 65535, "register": 0x1077, "type": "u16", "bitfield": True, "enum": False, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1,
            #                 "labels": ["High Temp", "Low Temp", "High humidity", "Low humidity", "Coil freeze protection", "High exhaust Temp", "Evaporator Temp sensor failure", "Outdoor Temp sensor failure",
            #                     "Condenser Temp sensor failure", "Indoor Temp sensor failure", "Exhaust Temp sensor failure", "Humidity sensor failure", "Internal fan failure", "External fan failure",
            #                     "Compressor failure", "Heater failure"]}, # 0 = normal, 1 = fault}
            # "Air con info 2": {"reg_unit_id": 1, "data_low": 0, "data_hi": 65535, "register": 0x1078, "type": "u16", "bitfield": True, "enum": False, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1,
            #                 "labels": ["Emergency fan failure", "HP", "LP", "Water", "Fire", "Gating", "HP lock", "LP lock", "High exhaust Temp lock", "AC over voltage", "AC under voltage", "AC power supply failure",
            #                     "Lose phase", "Freq fault", "Anti phase", "DC over voltage"]}, # 0 = normal, 1 = fault
            # "Air con info 3": {"reg_unit_id": 1, "data_low": 0, "data_hi": 65535, "register": 0x1079, "type": "u16", "bitfield": True, "enum": False, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1,
            #                 "labels": ["DC under voltage", "Reserved_bit1", "Reserved_bit2", "Reserved_bit3", "Reserved_bit4", "Reserved_bit5", "Reserved_bit6", "Reserved_bit7", "Reserved_bit8", "Reserved_bit9",
            #                     "Reserved_bit10", "Reserved_bit11", "Reserved_bit12", "Reserved_bit13", "Reserved_bit14", "Reserved_bit15"]}, # 0 = normal, 1 = fault  # bit 1-15 reserved

            # #UPS taken out by Hithium as not supplied as part of the package... so no data for UPS - just gives 65535 for all values = Fault FFFF
            # "UPS battery voltage": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x107C, "type": "u16", "bitfield": False, "enum": False, "units": "V", "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1}, 
            # "UPS Output total active power": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x107D, "type": "u16", "bitfield": False, "enum": False, "units": "W", "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1}, 
            # "UPS Output total apparent power": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x107E, "type": "u16", "bitfield": False, "enum": False, "units": "VA", "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1}, 
            # "UPS Output frequency": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x107F, "type": "u16", "bitfield": False, "enum": False, "units": "dHz", "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1}, 
            # "UPS Battery backup time": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x1080, "type": "u16", "bitfield": False, "enum": False, "units": "Min", "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1}, 
            # "UPS Battery charging level": {"reg_unit_id": 1, "data_low": 0, "data_hi": 99, "register": 0x1082, "type": "u16", "bitfield": False, "enum": False, "units": "%", "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1}, 
            # "UPS Unit general alarm": {"reg_unit_id": 1, "data_low": 0, "data_hi": 1, "register": 0x1083, "type": "u16", "bitfield": False, "enum": True, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU", "reg_count": 1,
            #                         "labels": {0: "Normal", 1: "Alarm"}}, # "info": "0 = Normal, 1 = Alarm"},
        }

        #register 0x3200
        main_rack_control_registers = {
            # "Rack 1 On Off": {"reg_unit_id": 1, "data_low": 1, "data_hi": 2, "register": 0x3200, "type": "u16", "bitfield": False, "enum": True, "units": None, "scaling_factor": None, "access": "W", "origin": "EMS, HMI", "reg_count": 1,
            #                         "labels": {1: "Enable", 2: "Disable"}}, # "info": "1: Enable, 2: Disable"
            # "Rack 2 On Off": {"reg_unit_id": 1, "data_low": 1, "data_hi": 2, "register": 0x4200, "type": "u16", "bitfield": False, "enum": True, "units": None, "scaling_factor": None, "access": "W", "origin": "EMS, HMI", "reg_count": 1,
            #                         "labels": {1: "Enable", 2: "Disable"}}, # "info": "1: Enable, 2: Disable"
            # "Rack 3 On Off": {"reg_unit_id": 1, "data_low": 1, "data_hi": 2, "register": 0x5200, "type": "u16", "bitfield": False, "enum": True, "units": None, "scaling_factor": None, "access": "W", "origin": "EMS, HMI", "reg_count": 1,
            #                         "labels": {1: "Enable", 2: "Disable"}}, # "info": "1: Enable, 2: Disable"
            # "Rack 4 On Off": {"reg_unit_id": 1, "data_low": 1, "data_hi": 2, "register": 0x6200, "type": "u16", "bitfield": False, "enum": True, "units": None, "scaling_factor": None, "access": "W", "origin": "EMS, HMI", "reg_count": 1,
            #                         "labels": {1: "Enable", 2: "Disable"}}, # "info": "1: Enable, 2: Disable"
            # "Rack 5 On Off": {"reg_unit_id": 1, "data_low": 1, "data_hi": 2, "register": 0x7200, "type": "u16", "bitfield": False, "enum": True, "units": None, "scaling_factor": None, "access": "W", "origin": "EMS, HMI", "reg_count": 1,
            #                         "labels": {1: "Enable", 2: "Disable"}}, # "info": "1: Enable, 2: Disable"
        }

        # These are the registers on the "Rack Information" tab in the excel document
        # registers 0x2001 - 0x308C
        rack_registers = {
            # "Rack operating state": {"reg_unit_id": 2, "data_low": 0, "data_hi": 4, "register": 0x2001, "type": "u16", "bitfield": False, "enum": True, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1,
            #                         "labels": {0: "Normal", 1: "No charge", 2: "No discharge", 3: "Standby", 4: "Stop"}}, # "info": "0: Normal (can charge & discharge), 1: No Charge (can discharge), 2: No Discharge (can charge), 3: Standby (cannot charge or discharge), 4: Stop (System stop operation - open contactors breakers)"},
            # "Rack precharge phase": {"reg_unit_id": 2, "data_low": 0, "data_hi": 4, "register": 0x2002, "type": "u16", "bitfield": False, "enum": True, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1,
            #                         "labels": {0: "Disconnected", 1: "Start connection", 2: "Connecting", 3: "Connected", 4: "Connection fail"}}, # "info": "0: Disconnected (positive contactor, negative contactor, precharge contactor open), 1: Start connection (precharge contactor closed, negative contactor closed, positive contactor open), 2: Connecting (precharge contactor closed, negative contactor closed, positive contactor closed), 3: Connected (precharge contactor open, negative contactor closed, positive contactor closed), 4: Connection fail"},
            # "Rack contactor state": {"reg_unit_id": 2, "data_low": 0, "data_hi": 65535, "register": 0x2003, "type": "u16", "bitfield": True, "enum": False, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1,
            #                         "labels": ["Positive contactor state", "PreContactor state", "Negative contactor state", "Disconnector state", "Reserved_bit4", "Reserved_bit5", "Reserved_bit6", "Reserved_bit7", "Reserved_bit8",
            #                     "Reserved_bit9", "Reserved_bit10", "Reserved_bit11", "Reserved_bit12", "Reserved_bit13", "Reserved_bit14", "Reserved_bit15"]}, # 0 = open, 1 = closed
            # "Rack fault": {"reg_unit_id": 2, "data_low": 0, "data_hi": 65535, "register": 0x2004, "type": "u16", "bitfield": True, "enum": False, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1,
            #             "labels": ["BMU hardware", "CBMU hardware", "Fuse protector", "Contactor adhesion", "BMU communication", "SBMU communication", "Current sensor", "IMD", "Disconnector Open when Rack Enabled", "NTC",
            #                     "Reserved_bit10", "Emergency stop", "MSD", "Door access", "Reserved_bit14", "Reserved_bit15"]}, # 0 = normal, 1 = fault
            # "Rack alarm 1": {"reg_unit_id": 2, "data_low": 0, "data_hi": 65535, "register": 0x2005, "type": "u16", "bitfield": True, "enum": False, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1,
            #                 "labels": ["RackVolHigh", "RackVolLow", "CellVolHigh", "CellVolLow", "DsgOverCurr", "ChgOverCurr", "DsgTempHigh", "DsgTempLow", "ChgTempHigh", "ChgTempLow",
            #                     "InsulationLow", "TerminalTempHigh", "HVBTempHigh", "CellVolDiffHigh", "CellTempDiffHigh", "SOC Low"]}, # 0 = normal, 1 = alarm I
            # "Rack alarm 2": {"reg_unit_id": 2, "data_low": 0, "data_hi": 65535, "register": 0x2006, "type": "u16", "bitfield": True, "enum": False, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1,
            #                 "labels": ["RackVolHigh", "RackVolLow", "CellVolHigh", "CellVolLow", "DsgOverCurr", "ChgOverCurr", "DsgTempHigh", "DsgTempLow", "ChgTempHigh", "ChgTempLow",
            #                     "InsulationLow", "TerminalTempHigh", "HVBTempHigh", "CellVolDiffHigh", "CellTempDiffHigh", "SOC Low"]}, # 0 = normal, 1 = alarm II
            # "Rack alarm 3": {"reg_unit_id": 2, "data_low": 0, "data_hi": 65535, "register": 0x2007, "type": "u16", "bitfield": True, "enum": False, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1,
            #                 "labels": ["RackVolHigh", "RackVolLow", "CellVolHigh", "CellVolLow", "DsgOverCurr", "ChgOverCurr", "DsgTempHigh", "DsgTempLow", "ChgTempHigh", "ChgTempLow",
            #                     "InsulationLow", "TerminalTempHigh", "HVBTempHigh", "CellVolDiffHigh", "CellTempDiffHigh", "SOC Low"]}, # 0 = normal, 1 = alarm III
            # "Reason for rack step in": {"reg_unit_id": 2, "data_low": 0, "data_hi": 65535, "register": 0x2008, "type": "u16", "bitfield": True, "enum": False, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1,
            #                             "labels": ["Rack Vol Diff Large", "EMS Confirmation Overtime", "Reserved_bit2", "Reserved_bit3", "Reserved_bit4", "Reserved_bit5", "Reserved_bit6", "Reserved_bit7", "Reserved_bit8",
            #                     "Reserved_bit9", "Reserved_bit10", "Reserved_bit11", "Reserved_bit12", "Reserved_bit13", "Reserved_bit14", "Reserved_bit15"]}, # 0 = normal, 1 = warning
            # "Reason why rack did not step in": {"reg_unit_id": 2, "data_low": 0, "data_hi": 65535, "register": 0x2009, "type": "u16", "bitfield": True, "enum": False, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1,
            #                                     "labels": ["Rack Vol Diff Large", "Type 1 alarms not cleared", "Insulation alarms not cleared", "Cell voltage high alarm", "Cell voltage low alarm", "Alarm III not all cleared", "Reserved_bit6",
            #                     "Reserved_bit7", "Reserved_bit8", "Reserved_bit9", "Reserved_bit10", "Reserved_bit11", "Reserved_bit12", "Reserved_bit13", "Reserved_bit14", "Reserved_bit15"]}, # 0 = normal, 1 = warning
            # "Rack precharge total vol": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x200A, "type": "u16", "bitfield": False, "enum": False, "units": "V", "scaling_factor": 0.1, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack voltage": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x200B, "type": "u16", "bitfield": False, "enum": False, "units": "V", "scaling_factor": 0.1, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack current": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x200C, "type": "int16", "bitfield": False, "enum": False, "units": "A", "scaling_factor": 0.1, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1}, # "info": "Charge current is negative and discharge is positive."},
            # "Rack charge/discharge state": {"reg_unit_id": 2, "data_low": 0, "data_hi": 2, "register": 0x200D, "type": "u16", "bitfield": False, "enum": True, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1,
            #                                 "labels": {0: "Idle", 1: "Discharging", 2: "Charging"}}, # "info": "0x0: Idle; 0x1: Discharging; 0x2: Charging"},
            # "Rack SOC": {"reg_unit_id": 2, "data_low": 0, "data_hi": 100, "register": 0x200E, "type": "u16", "bitfield": False, "enum": False, "units": "%", "scaling_factor": 0.1, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1}, # "info": "Rack SOC in permille (‰) 0-1000"},
            # "Rack SOH": {"reg_unit_id": 2, "data_low": 0, "data_hi": 100, "register": 0x200F, "type": "u16", "bitfield": False, "enum": False, "units": "%", "scaling_factor": 0.1, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1}, # "info": "Rack SOH in permille (‰) 0-1000"},
            # "Rack insulation value": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x2010, "type": "u16", "bitfield": False, "enum": False, "units": "kΩ", "scaling_factor": 0.1, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1}, # "info": "Fixed value before contactor close; BMS IMD Disabled after contactor close."},
            # "Rack positive insulation value": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x2011, "type": "u16", "bitfield": False, "enum": False, "units": "kΩ", "scaling_factor": 0.1, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1}, # "info": "Fixed value before contactor close; BMS IMD Disabled after contactor close."},
            # "Rack negative insulation value": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x2012, "type": "u16", "bitfield": False, "enum": False, "units": "kΩ", "scaling_factor": 0.1, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1}, # "info": "Fixed value before contactor close; BMS IMD Disabled after contactor close."},
            # "Rack max charge current": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x2013, "type": "u16", "bitfield": False, "enum": False, "units": "A", "scaling_factor": 0.1, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1}, # "info": "Fixed Value depends on project, same as threshold of rack over current alarm."},
            # "Rack max discharge current": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x2014, "type": "u16", "bitfield": False, "enum": False, "units": "A", "scaling_factor": 0.1, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1}, # "info": "Fixed Value depends on project, same as threshold of rack over current alarm."},
            # "Rack max vol cell ID": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x2015, "type": "u16", "bitfield": False, "enum": False, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1}, # "info": "1#  - N# - N is cell number"},
            # "Rack max cell voltage": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x2016, "type": "u16", "bitfield": False, "enum": False, "units": "mV", "scaling_factor": None, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack min vol cell ID": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x2017, "type": "u16", "bitfield": False, "enum": False, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1}, # "info": "1#  - N# - N is cell number"},
            # "Rack min cell voltage": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x2018, "type": "u16", "bitfield": False, "enum": False, "units": "mV", "scaling_factor": None, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack max temperature cell ID": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x2019, "type": "u16", "bitfield": False, "enum": False, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1}, # "info": "1#  - N# - N is cell number"},
            # "Rack max cell temperature": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x201A, "type": "int16", "bitfield": False, "enum": False, "units": "℃", "scaling_factor": 0.1, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack min temperature cell ID": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x201B, "type": "u16", "bitfield": False, "enum": False, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1}, # "info": "1#  - N# - N is cell number"},
            # "Rack min cell temperature": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x201C, "type": "int16", "bitfield": False, "enum": False, "units": "℃", "scaling_factor": 0.1, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack average voltage": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x201D, "type": "u16", "bitfield": False, "enum": False, "units": "mV", "scaling_factor": None, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack average temperature": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x201E, "type": "int16", "bitfield": False, "enum": False, "units": "℃", "scaling_factor": 0.1, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack cell voltage cumulative sum": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x201F, "type": "u16", "bitfield": False, "enum": False, "units": "V", "scaling_factor": 0.1, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "HVB Temp1": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x2020, "type": "int16", "bitfield": False, "enum": False, "units": "℃", "scaling_factor": 0.1, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "HVB Temp2": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x2021, "type": "int16", "bitfield": False, "enum": False, "units": "℃", "scaling_factor": 0.1, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "HVB Temp3": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x2022, "type": "int16", "bitfield": False, "enum": False, "units": "℃", "scaling_factor": 0.1, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "HVB Temp4": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x2023, "type": "int16", "bitfield": False, "enum": False, "units": "℃", "scaling_factor": 0.1, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "HVB Temp5": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x2024, "type": "int16", "bitfield": False, "enum": False, "units": "℃", "scaling_factor": 0.1, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1},
            # "HVB Temp6": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x2025, "type": "int16", "bitfield": False, "enum": False, "units": "℃", "scaling_factor": 0.1, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1},
            # "HVB Temp7": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x2026, "type": "int16", "bitfield": False, "enum": False, "units": "℃", "scaling_factor": 0.1, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1},
            # "HVB Temp8": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x2027, "type": "int16", "bitfield": False, "enum": False, "units": "℃", "scaling_factor": 0.1, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1},
            # #"Pack positive pole temperature": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x2220, "type": "int16", "bitfield": False, "enum": False, "units": "℃", "scaling_factor": 0.1, "access": "R", "origin": "SBMU, CBMU", "reg_count": 64, "info": "supports a maximum of 64 Packs. 64th register is 0x225F"},
            # #"Pack negative pole temperature": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x2260, "type": "int16", "bitfield": False, "enum": False, "units": "℃", "scaling_factor": 0.1, "access": "R", "origin": "SBMU, CBMU", "reg_count": 64, "info": "supports a maximum of 64 Packs. 64th register is 0x229F"},
            # #"Rack cell voltage": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x2400, "type": "u16", "bitfield": False, "enum": False, "units": "mV", "scaling_factor": None, "access": "R", "origin": "SBMU, CBMU, MBMU", "reg_count": 512, "info": "supports a maximum of 512 individual voltages. 512th register is 0x25FF"},
            # #"Rack cell temperature": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x2600, "type": "int16", "bitfield": False, "enum": False, "units": "℃", "scaling_factor": 0.1, "access": "R", "origin": "SBMU, CBMU, MBMU", "reg_count": 512, "info": "support a maximum of 512 monomer temperatures. 512th register is 0x27FF"},
            # "TMS operating state": {"reg_unit_id": 2, "data_low": 0, "data_hi": 3, "register": 0x3077, "type": "u16", "bitfield": False, "enum": True, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1,
            #                         "labels": {0: "OFF mode", 1: "Cooling mode", 2: "Heating mode", 3: "Auto cycle mode"}}, # "info": "0: OFF mode, 1: Cooling mode, 2: Heating mode, 3: Auto cycle mode"},
            # "K1 relay state": {"reg_unit_id": 2, "data_low": 0, "data_hi": 1, "register": 0x3078, "type": "u16", "bitfield": False, "enum": True, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1,
            #                 "labels": {0: "Open", 1: "Closed"}}, # "info": "0: Open, 1: Closed"},
            # "K2 relay state": {"reg_unit_id": 2, "data_low": 0, "data_hi": 1, "register": 0x3079, "type": "u16", "bitfield": False, "enum": True, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1,
            #                 "labels": {0: "Open", 1: "Closed"}}, # "info": "0: Open, 1: Closed"},
            # "Pre-heating mode feedback": {"reg_unit_id": 2, "data_low": 0, "data_hi": 1, "register": 0x307A, "type": "u16", "bitfield": False, "enum": True, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1,
            #                             "labels": {0: "Exit preheating mode", 1: "Enter preheating mode"}}, # "info": "0: Exit preheating mode, 1: Enter preheating mode"},
            # "Outlet temperature": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x307B, "type": "int16", "bitfield": False, "enum": False, "units": "℃", "scaling_factor": None, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1}, # "info": "Offset: -40℃"},
            # "Inlet temperature": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x307C, "type": "int16", "bitfield": False, "enum": False, "units": "℃", "scaling_factor": None, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1}, # "info": "Offset: -40℃"},
            # "Ambient temperature": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x307D, "type": "int16", "bitfield": False, "enum": False, "units": "℃", "scaling_factor": None, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1}, # "info": "Offset: -40℃"},
            # "Outlet pressure": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x307E, "type": "u16", "bitfield": False, "enum": False, "units": "bar", "scaling_factor": 0.1, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1}, # "info": "Resolutions: 0.1bar"},
            # "Inlet pressure": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x307F, "type": "u16", "bitfield": False, "enum": False, "units": "bar", "scaling_factor": 0.1, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1}, # "info": "Resolutions: 0.1bar"},
            # "TMS fault code": {"reg_unit_id": 2, "data_low": 0, "data_hi": 1, "register": 0x3080, "type": "u16", "bitfield": False, "enum": True, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1,
            #                 "labels": {0: "No fault"}}, # "info": "0: No fault"},
            # "TMS fault level": {"reg_unit_id": 2, "data_low": 1, "data_hi": 2, "register": 0x3081, "type": "u16", "bitfield": False, "enum": True, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1,
            #                     "labels": {0: "No fault", 1: "Alarm I", 2: "Alarm II"}}, # "info": "1: No fault, 1: Alarm I, 2: Alarm II"},
            # "ACDC_ A voltage": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3082, "type": "u16", "bitfield": False, "enum": False, "units": "V", "scaling_factor": 1, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1}, # "info": "Resolutions: 1V/bit"},
            # "High pressure value": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3083, "type": "u16", "bitfield": False, "enum": False, "units": "kPa", "scaling_factor": 1, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1}, # "info": "Resolutions: 1KPa/bit"},
            # "Low pressure value": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3084, "type": "u16", "bitfield": False, "enum": False, "units": "kPa", "scaling_factor": 1, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1}, # "info": "Resolutions: 1KPa/bit"},
            # "Fan PWM": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3085, "type": "u16", "bitfield": False, "enum": False, "units": "%", "scaling_factor": 1, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1}, # "info": "Resolutions: 1%/bit"},
            # "Compressor voltage": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3086, "type": "u16", "bitfield": False, "enum": False, "units": "V", "scaling_factor": 1, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1}, # "info": "Resolutions: 1V/bit"},
            # "Compressor current": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3087, "type": "u16", "bitfield": False, "enum": False, "units": "A", "scaling_factor": 0.1, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1}, # "info": "Resolutions: 0.1A/bit"},
            # "Compressor revolution speed": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3088, "type": "u16", "bitfield": False, "enum": False, "units": "rpm", "scaling_factor": 100, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1}, # "info": "Resolutions: 100rpm/bit"},
            # "PTC temperature switch state": {"reg_unit_id": 2, "data_low": 0, "data_hi": 1, "register": 0x3089, "type": "u16", "bitfield": False, "enum": True, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1,
            #                                 "labels": {0: "Open", 1: "Closed"}}, # "info": "0: Open, 1: Closed"},
            # "Water pump switch": {"reg_unit_id": 2, "data_low": 0, "data_hi": 1, "register": 0x308A, "type": "u16", "bitfield": False, "enum": True, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1,
            #                     "labels": {0: "Open", 1: "Closed"}}, # "info": "0: Open, 1: Closed"},
            # "AC fault-1": {"reg_unit_id": 2, "data_low": 0, "data_hi": 1, "register": 0x308B, "type": "u16", "bitfield": False, "enum": True, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1,
            #             "labels": {0: "No fault"}}, # "info": "00:  No fault"},
            # "AC fault-2": {"reg_unit_id": 2, "data_low": 0, "data_hi": 1, "register": 0x308C, "type": "u16", "bitfield": False, "enum": True, "units": None, "scaling_factor": None, "access": "R", "origin": "SBMU, CBMU", "reg_count": 1,
            #             "labels": {0: "No fault"}}, # "info": "00:  No fault"},
        }

        # These are the registers on the "Alarm Parameters" tab in the excel document
        # registers 0x3001 - 0x3066
        rack_alarm_registers = {
            # "Rack Cell Over Voltage Alarm I": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3001, "type": "u16", "bitfield": False, "enum": False, "units": "mV", "scaling_factor": None, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Cell Over Voltage Alarm I Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3002, "type": "u16", "bitfield": False, "enum": False, "units": "mV", "scaling_factor": None, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Cell Under Voltage Alarm I": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3003, "type": "u16", "bitfield": False, "enum": False, "units": "mV", "scaling_factor": None, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Cell Under Voltage Alarm I Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3004, "type": "u16", "bitfield": False, "enum": False, "units": "mV", "scaling_factor": None, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack System Over Voltage Alarm I": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3005, "type": "u16", "bitfield": False, "enum": False, "units": "V", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack System Over Voltage Alarm I Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3006, "type": "u16", "bitfield": False, "enum": False, "units": "V", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack System Under Voltage Alarm I": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3007, "type": "u16", "bitfield": False, "enum": False, "units": "V", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack System Under Voltage Alarm I Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3008, "type": "u16", "bitfield": False, "enum": False, "units": "V", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Charge Over Current Alarm I": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3009, "type": "u16", "bitfield": False, "enum": False, "units": "A", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Charge Over Current Alarm I Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x300A, "type": "u16", "bitfield": False, "enum": False, "units": "A", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Discharge Over Current Alarm I": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x300B, "type": "u16", "bitfield": False, "enum": False, "units": "A", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Discharge Over Current Alarm I Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x300C, "type": "u16", "bitfield": False, "enum": False, "units": "A", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Charge Over Temp Alarm I": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x300D, "type": "int16", "bitfield": False, "enum": False, "units": "°C", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Charge Over Temp Alarm I Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x300E, "type": "int16", "bitfield": False, "enum": False, "units": "°C", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Charge Under Temp Alarm I": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x300F, "type": "int16", "bitfield": False, "enum": False, "units": "°C", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Charge Under Temp Alarm I Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3010, "type": "int16", "bitfield": False, "enum": False, "units": "°C", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack SOC Lower Alarm I": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3011, "type": "u16", "bitfield": False, "enum": False, "units": "%", "scaling_factor": 1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack SOC Lower Alarm I Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3012, "type": "u16", "bitfield": False, "enum": False, "units": "%", "scaling_factor": 1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Pole Over Temp Alarm I": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3013, "type": "int16", "bitfield": False, "enum": False, "units": "°C", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Pole Over Temp Alarm I Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3014, "type": "int16", "bitfield": False, "enum": False, "units": "°C", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Insulation Failure Alarm I": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3015, "type": "u16", "bitfield": False, "enum": False, "units": "Ω/V", "scaling_factor": 1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Insulation Failure Alarm I Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3016, "type": "u16", "bitfield": False, "enum": False, "units": "Ω/V", "scaling_factor": 1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Cell Vol Diff Over Big Alarm I": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3017, "type": "u16", "bitfield": False, "enum": False, "units": "mV", "scaling_factor": 1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Cell Vol Diff Over Big Alarm I Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3018, "type": "u16", "bitfield": False, "enum": False, "units": "mV", "scaling_factor": 1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Total Vol Diff Over Big Alarm I": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3019, "type": "u16", "bitfield": False, "enum": False, "units": "V", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Total Vol Diff Over Big Alarm I Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x301A, "type": "u16", "bitfield": False, "enum": False, "units": "V", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Discharge Over Temp Alarm I": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x301B, "type": "int16", "bitfield": False, "enum": False, "units": "°C", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Discharge Over Temp Alarm I Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x301C, "type": "int16", "bitfield": False, "enum": False, "units": "°C", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Discharge Under Temp Alarm I": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x301D, "type": "int16", "bitfield": False, "enum": False, "units": "°C", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Discharge Under Temp Alarm I Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x301E, "type": "int16", "bitfield": False, "enum": False, "units": "°C", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Bat Temp Diff Over Big Alarm I": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x301F, "type": "u16", "bitfield": False, "enum": False, "units": "°C", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Bat Temp Diff Over Big Alarm I Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3020, "type": "u16", "bitfield": False, "enum": False, "units": "°C", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack HVB Temp High Alarm I": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3021, "type": "u16", "bitfield": False, "enum": False, "units": "°C", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack HVB Temp High Alarm I Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3022, "type": "u16", "bitfield": False, "enum": False, "units": "°C", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Cell Over Voltage Alarm II": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3023, "type": "u16", "bitfield": False, "enum": False, "units": "mV", "scaling_factor": None, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Cell Over Voltage Alarm II Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3024, "type": "u16", "bitfield": False, "enum": False, "units": "mV", "scaling_factor": None, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Cell Under Voltage Alarm II": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3025, "type": "u16", "bitfield": False, "enum": False, "units": "mV", "scaling_factor": None, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Cell Under Voltage Alarm II Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3026, "type": "u16", "bitfield": False, "enum": False, "units": "mV", "scaling_factor": None, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack System Over Voltage Alarm II": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3027, "type": "u16", "bitfield": False, "enum": False, "units": "V", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack System Over Voltage Alarm II Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3028, "type": "u16", "bitfield": False, "enum": False, "units": "V", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack System Under Voltage Alarm II": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3029, "type": "u16", "bitfield": False, "enum": False, "units": "V", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack System Under Voltage Alarm II Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x302A, "type": "u16", "bitfield": False, "enum": False, "units": "V", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Charge Over Current Alarm II": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x302B, "type": "u16", "bitfield": False, "enum": False, "units": "A", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Charge Over Current Alarm II Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x302C, "type": "u16", "bitfield": False, "enum": False, "units": "A", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Discharge Over Current Alarm II": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x302D, "type": "u16", "bitfield": False, "enum": False, "units": "A", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Discharge Over Current Alarm II Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x302E, "type": "u16", "bitfield": False, "enum": False, "units": "A", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Charge Over Temp Alarm II": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x302F, "type": "int16", "bitfield": False, "enum": False, "units": "°C", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Charge Over Temp Alarm II Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3030, "type": "int16", "bitfield": False, "enum": False, "units": "°C", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Charge Under Temp Alarm II": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3031, "type": "int16", "bitfield": False, "enum": False, "units": "°C", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Charge Under Temp Alarm II Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3032, "type": "int16", "bitfield": False, "enum": False, "units": "°C", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack SOC Lower Alarm II": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3033, "type": "u16", "bitfield": False, "enum": False, "units": "%", "scaling_factor": 1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack SOC Lower Alarm II Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3034, "type": "u16", "bitfield": False, "enum": False, "units": "%", "scaling_factor": 1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Pole Over Temp Alarm II": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3035, "type": "int16", "bitfield": False, "enum": False, "units": "°C", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Pole Over Temp Alarm II Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3036, "type": "int16", "bitfield": False, "enum": False, "units": "°C", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Insulation Failure Alarm II": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3037, "type": "u16", "bitfield": False, "enum": False, "units": "Ω/V", "scaling_factor": 1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Insulation Failure Alarm II Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3038, "type": "u16", "bitfield": False, "enum": False, "units": "Ω/V", "scaling_factor": 1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Cell Vol Diff Over Big Alarm II": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3039, "type": "u16", "bitfield": False, "enum": False, "units": "mV", "scaling_factor": 1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Cell Vol Diff Over Big Alarm II Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x303A, "type": "u16", "bitfield": False, "enum": False, "units": "mV", "scaling_factor": 1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Total Vol Diff Over Big Alarm II": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x303B, "type": "u16", "bitfield": False, "enum": False, "units": "V", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Total Vol Diff Over Big Alarm II Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x303C, "type": "u16", "bitfield": False, "enum": False, "units": "V", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Discharge Over Temp Alarm II": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x303D, "type": "int16", "bitfield": False, "enum": False, "units": "°C", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Discharge Over Temp Alarm II Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x303E, "type": "int16", "bitfield": False, "enum": False, "units": "°C", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Discharge Under Temp Alarm II": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x303F, "type": "int16", "bitfield": False, "enum": False, "units": "°C", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Discharge Under Temp Alarm II Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3040, "type": "int16", "bitfield": False, "enum": False, "units": "°C", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Bat Temp Diff Over Big Alarm II": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3041, "type": "u16", "bitfield": False, "enum": False, "units": "°C", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Bat Temp Diff Over Big Alarm II Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3042, "type": "u16", "bitfield": False, "enum": False, "units": "°C", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack HVB Temp High Alarm II": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3043, "type": "u16", "bitfield": False, "enum": False, "units": "°C", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack HVB Temp High Alarm II Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3044, "type": "u16", "bitfield": False, "enum": False, "units": "°C", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Cell Over Voltage Alarm III": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3045, "type": "u16", "bitfield": False, "enum": False, "units": "mV", "scaling_factor": None, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Cell Over Voltage Alarm III Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3046, "type": "u16", "bitfield": False, "enum": False, "units": "mV", "scaling_factor": None, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Cell Under Voltage Alarm III": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3047, "type": "u16", "bitfield": False, "enum": False, "units": "mV", "scaling_factor": None, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Cell Under Voltage Alarm III Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3048, "type": "u16", "bitfield": False, "enum": False, "units": "mV", "scaling_factor": None, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack System Over Voltage Alarm III": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3049, "type": "u16", "bitfield": False, "enum": False, "units": "V", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack System Over Voltage Alarm III Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x304A, "type": "u16", "bitfield": False, "enum": False, "units": "V", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack System Under Voltage Alarm III": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x304B, "type": "u16", "bitfield": False, "enum": False, "units": "V", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack System Under Voltage Alarm III Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x304C, "type": "u16", "bitfield": False, "enum": False, "units": "V", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Charge Over Current Alarm III": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x304D, "type": "u16", "bitfield": False, "enum": False, "units": "A", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Charge Over Current Alarm III Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x304E, "type": "u16", "bitfield": False, "enum": False, "units": "A", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Discharge Over Current Alarm III": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x304F, "type": "u16", "bitfield": False, "enum": False, "units": "A", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Discharge Over Current Alarm III Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3050, "type": "u16", "bitfield": False, "enum": False, "units": "A", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Charge Over Temp Alarm III": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3051, "type": "int16", "bitfield": False, "enum": False, "units": "°C", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Charge Over Temp Alarm III Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3052, "type": "int16", "bitfield": False, "enum": False, "units": "°C", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Charge Under Temp Alarm III": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3053, "type": "int16", "bitfield": False, "enum": False, "units": "°C", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Charge Under Temp Alarm III Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3054, "type": "int16", "bitfield": False, "enum": False, "units": "°C", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack SOC Lower Alarm III": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3055, "type": "u16", "bitfield": False, "enum": False, "units": "%", "scaling_factor": 1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack SOC Lower Alarm III Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3056, "type": "u16", "bitfield": False, "enum": False, "units": "%", "scaling_factor": 1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Pole Over Temp Alarm III": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3057, "type": "int16", "bitfield": False, "enum": False, "units": "°C", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Pole Over Temp Alarm III Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3058, "type": "int16", "bitfield": False, "enum": False, "units": "°C", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Insulation Failure Alarm III": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3059, "type": "u16", "bitfield": False, "enum": False, "units": "Ω/V", "scaling_factor": 1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Insulation Failure Alarm III Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x305A, "type": "u16", "bitfield": False, "enum": False, "units": "Ω/V", "scaling_factor": 1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Cell Vol Diff Over Big Alarm III": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x305B, "type": "u16", "bitfield": False, "enum": False, "units": "mV", "scaling_factor": 1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Cell Vol Diff Over Big Alarm III Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x305C, "type": "u16", "bitfield": False, "enum": False, "units": "mV", "scaling_factor": 1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Total Vol Diff Over Big Alarm III": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x305D, "type": "u16", "bitfield": False, "enum": False, "units": "V", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Total Vol Diff Over Big Alarm III Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x305E, "type": "u16", "bitfield": False, "enum": False, "units": "V", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Discharge Over Temp Alarm III": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x305F, "type": "int16", "bitfield": False, "enum": False, "units": "°C", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Discharge Over Temp Alarm III Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3060, "type": "int16", "bitfield": False, "enum": False, "units": "°C", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Discharge Under Temp Alarm III": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3061, "type": "int16", "bitfield": False, "enum": False, "units": "°C", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Discharge Under Temp Alarm III Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3062, "type": "int16", "bitfield": False, "enum": False, "units": "°C", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Bat Temp Diff Over Big Alarm III": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3063, "type": "u16", "bitfield": False, "enum": False, "units": "°C", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack Bat Temp Diff Over Big Alarm III Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3064, "type": "u16", "bitfield": False, "enum": False, "units": "°C", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack HVB Temp High Alarm III": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3065, "type": "u16", "bitfield": False, "enum": False, "units": "°C", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            # "Rack HVB Temp High Alarm III Recover": {"reg_unit_id": 2, "data_low": 0, "data_hi": 99, "register": 0x3066, "type": "u16", "bitfield": False, "enum": False, "units": "°C", "scaling_factor": 0.1, "access": "RW", "origin": "SBMU, CBMU", "reg_count": 1}, 
            }
        # Warnings from the "Rack Alarm I Summary" register and linking them to the Warning Enums
        warning_types = {
            "RackVolHigh": Warnings.OVERVOLT,
            "RackVolLow": Warnings.UNDERVOLT,
            "CellVolHigh": Warnings.OVERVOLT,
            "CellVolLow": Warnings.UNDERVOLT,
            "DsgOverCurr": Warnings.OVERDISCHARGE,
            "ChgOverCurr": Warnings.OVERCHARGE,
            "DsgTempHigh": Warnings.OVERTEMP,
            "DsgTempLow": Warnings.UNDERTEMP,
            "ChgTempHigh": Warnings.OVERTEMP,
            "ChgTempLow": Warnings.UNDERTEMP,
            "InsulationLow": Warnings.INSULATION,
            "TerminalTempHigh": Warnings.OVERTEMP,
            "HVBTempHigh": Warnings.OVERTEMP,
            "CellVolDiffHigh": Warnings.VOLT_DIFF,
            "CellTempDiffHigh": Warnings.TEMP_DIFF,
            "SOC Low": Warnings.SOC_LOW
            }
        # Alarms from the "Rack Alarm II Summary" and "Alarm" registers
        alarm_types = {
            "RackVolHigh": Alarms.OVERVOLT,
            "RackVolLow": Alarms.UNDERVOLT,
            "CellVolHigh": Alarms.OVERVOLT,
            "CellVolLow": Alarms.UNDERVOLT,
            "DsgOverCurr": Alarms.OVERDISCHARGE,
            "ChgOverCurr": Alarms.OVERCHARGE,
            "DsgTempHigh": Alarms.OVERTEMP,
            "DsgTempLow": Alarms.UNDERTEMP,
            "ChgTempHigh": Alarms.OVERTEMP,
            "ChgTempLow": Alarms.UNDERTEMP,
            "InsulationLow": Alarms.INSULATION,
            "TerminalTempHigh": Alarms.OVERTEMP,
            "HVBTempHigh": Alarms.OVERTEMP,
            "CellVolDiffHigh": Alarms.VOLT_DIFF,
            "CellTempDiffHigh": Alarms.TEMP_DIFF,
            "SOC Low": Alarms.SOC_LOW,
            "Electric meter comms fault": Alarms.CONFIG,
            "IO module comms fault": Alarms.CONFIG,
            "FFS alarm": Alarms.OTHER,
            "FFS fault alarm": Alarms.OTHER,
            "Aerosol alarm": Alarms.OTHER,
            "Emergency stop detection alarm": Alarms.OTHER,
            "IO module QF1 alarm": Alarms.OTHER,
            "IO module front right door access alarm": Alarms.OTHER,
            "IO module SPD1 alarm": Alarms.OTHER,
            "IO module SPD2 alarm": Alarms.OTHER,
            "IO module front left door access alarm": Alarms.OTHER,
            "IO module rear left door access alarm": Alarms.OTHER,
            "QS D15 alarm": Alarms.OTHER,
            "IO module rear right door access alarm": Alarms.OTHER,
            "Air conditioning comms failure": Alarms.OTHER,
            "UPS comms failure": Alarms.CONFIG
            }
        # Faults from the "Rack Alarm III Summary" and "SBMU Alarm State" and "Rack fault summary" registers
        fault_types = {
            "RackVolHigh": Faults.OVERVOLT,
            "RackVolLow": Faults.UNDERVOLT,
            "CellVolHigh": Faults.OVERVOLT,
            "CellVolLow": Faults.UNDERVOLT,
            "DsgOverCurr": Faults.OVERDISCHARGE,
            "ChgOverCurr": Faults.OVERCHARGE,
            "DsgTempHigh": Faults.OVERTEMP,
            "DsgTempLow": Faults.UNDERTEMP,
            "ChgTempHigh": Faults.OVERTEMP,
            "ChgTempLow": Faults.UNDERTEMP,
            "InsulationLow": Faults.INSULATION,
            "TerminalTempHigh": Faults.OVERTEMP,
            "HVBTempHigh": Faults.OVERTEMP,
            "CellVolDiffHigh": Faults.VOLT_DIFF,
            "CellTempDiffHigh": Faults.TEMP_DIFF,
            "SOC Low": Faults.SOC_LOW,
            "BMS internal communication failure": Faults.LOSS_OF_COMMS,
            "PCS communication fault alarm": Faults.LOSS_OF_COMMS,
            "PCS control failure alarm": Faults.CONFIG,
            "EMS communication fault alarm": Faults.LOSS_OF_COMMS,
            "Racks vol diff over large warning": Faults.VOLT_DIFF,
            "Racks current diff over large warning": Faults.CURRENT_DIFF,
            "Emergency stop": Faults.OTHER,
            "Air conditioner communication failure": Faults.LOSS_OF_COMMS,
            "BMU hardware": Faults.CONFIG,
            "CBMU hardware": Faults.CONFIG,
            "Fuse protector": Faults.OTHER,
            "Contactor adhesion": Faults.OTHER,
            "BMU communication": Faults.LOSS_OF_COMMS,
            "SBMU communication": Faults.LOSS_OF_COMMS,
            "Current sensor": Faults.CONFIG,
            "IMD": Faults.OTHER,
            "Disconnector Open when Rack Enabled": Faults.CONFIG,
            "NTC": Faults.CONFIG,
            "MSD": Faults.CONFIG,
            "Door access": Faults.CONFIG
            }
    except Exception as e:
        print("HITHIUM BATTERY: " + str(e))
        log_message(HB_logger, f"Error intialising base module: {e}", log_level='error')

    def __init__(self, uid, queue):
        try:
            #super(Module, self, queue).__init__()

            # General Module Data
            self.author = "Gareth Reece"
            self.uid = uid  # A unique identifier passed from the HMI via a mangled URL
            self.icon = "/static/images/Battery.png"
            self.name = "Hithium Battery"
            self.module_type = ModTypes.BATTERY.value
            self.module_version = "3.5.24.10.12"                                                    # Last update on "Flex version | Year | Month | Day"
            self.manufacturer = "Hithium"
            self.model = ""
            self.options = ""
            self.version = "1.6"
            self.serial = "0000"  # This can be replaced with the device serial number later
            self.website = "/Mod/FlexMod_HithiumBattery"  # This is the template name itself

            # Run state
            self.con_state = False
            self.state = State.IDLE
            self.set_state_text(self.state)

            # Non-Volatile Data (Loaded from DB)
            self.dbData = db.fetch_from_db(__name__ + "_(" + str(self.uid) + ")")
            if self.dbData is None:
                self.dbData = dict()
                self.dbData["battery_ipaddr_local"] = "0.0.0.0"

            # Volatile Data
            self.tcp_client = None
            self.tcp_timeout = 0
            self.enabled = False
            self.enabled_echo = False
            self.enabled_check = False
            self.enabled_counter = 0
            self.inputs = [self.uid, False, [False * 14]]  # All reserved
            self.outputs = [self.uid, self.enabled, [0]*25]
            self.heartbeat = 0
            self.heartbeat_echo = 0
            
            self.old_BMS_heartbeat = 0
            self.heartbeat_message = 0
            # self.process_timeout = 5
            # self.process_timeout_counter = 0
            # self.process_retries = 0
            # self.alert_timeout = 5
            # self.warning_timeout_counter = [0] * 10
            # self.alarm_timeout_counter = [0] * 10
            # self.fault_timeout_counter = [0] * 10

            # HMI Volatiles
            self.override = False
            # self.contactor_state = False

            # Device Volatiles
            self.battery_bank_quantity = 1
            self.battery_rack_quantity = 0
            self.battery_rack_online_quantity = 0
            self.battery_bank_heartbeat = 0
            # self.battery_bank_cont_states = 0
            self.battery_bank_soc = 0
            self.battery_bank_soh = 0
            self.battery_bank_bus_voltage = 0
            self.battery_bank_bus_current = 0
            self.battery_bank_bus_power = 0
            self.battery_bank_max_chg_current = 0
            self.battery_bank_max_dchg_current = 0
            self.battery_bank_max_chg_power = 0
            self.battery_bank_max_dchg_power = 0
            self.battery_bank_charge_state = 0
            self.battery_bank_min_cell_voltage = 0
            self.battery_bank_max_cell_voltage = 0
            self.battery_bank_avg_cell_voltage = 0
            self.battery_bank_min_cell_temperature = 0
            self.battery_bank_max_cell_temperature = 0
            self.battery_bank_avg_cell_temperature = 0
            self.battery_bank_cycles = 0
            self.battery_bank_max_capacity = 0
            self.battery_bank_online_capacity = 0
            # self.battery_bank_module_count = 0

            # self.battery_last_soc = 0
            # self.battery_import_kw = 0
            # self.battery_export_kw = 0

            self.fetched_raw_data = {}
            self.device_values = {}
            self.device_values["BMS heartbeat"] = 0
            self.device_values_parsed = {}
            self.register_chunks = {}
            self.all_registers = [
                self.main_system_information_registers,
                self.main_rack_control_registers,
                self.system_information_SBMU_registers,
                self.rack_registers,
                self.rack_alarm_registers
            ]
            self.first_run = True
            self.accum_total_energy = 0
            self.accum_average_energy = 0
            ### IF USING SIMULATED DARA SELF.SIMULATION_MODE = TRUE ###
            # self.simulation_mode = False
            # self.file_access_lock = threading.Lock()
            self.latest_good_data = {}
            self.column_width = 50

            self.connection_counter = 0
            self.port_number = 503

            ### Added for HTML commands
            self.hithium_busbar_on_off_output = 0
            self.old_hithium_busbar_on_off_output = 0
            self.hithium_switch_on_output = 0
            self.old_hithium_switch_on_output = 0
            self.hithium_switch_off_output = 0
            self.old_hithium_switch_off_output = 0
            self.hithium_min_parallel_racks_output = 0
            self.old_hithium_min_parallel_racks_output = 0
            self.insulation_switch_output = 0
            self.old_insulation_switch_output = 0
            self.reset_PCS_output = 0
            self.old_reset_PCS_output = 0
            self.rack_1_on_off = 0
            # self.old_rack_1_on_off = 0
            # to be added later once tested with 1 rack
            # self.rack_2_on_off = 0
            # self.old_rack_2_on_off = 0
            # self.rack_3_on_off = 0
            # self.old_rack_3_on_off = 0
            # self.rack_4_on_off = 0
            # self.old_rack_4_on_off = 0
            # self.rack_5_on_off = 0
            # self.old_rack_5_on_off = 0
            self.air_con_rem_con_output = 0
            self.old_air_con_rem_con_output = 0        

            ### Added for reading HTML commands
            self.hithium_busbar_on_off_state = 0
            self.hithium_switch_on_state = 0
            self.hithium_switch_off_state = 0
            self.hithium_min_parallel_racks_state = 0
            self.insulation_switch_state = 0
            self.reset_PCS_state = 0
            self.rack_1_on_off_state = 0
            # self.rack_2_on_off_state = 0
            # self.rack_3_on_off_state = 0
            # self.rack_4_on_off_state = 0
            # self.rack_5_on_off_state = 0
            self.air_con_rem_con_state = 0


            self.year_output = 0
            self.year_state = 0
            self.old_year_output = 0
            self.month_output = 0
            self.month_state = 0
            self.old_month_output = 0
            self.day_output = 0
            self.day_state = 0
            self.old_day_output = 0
            self.hour_output = 0
            self.hour_state = 0
            self.old_hour_output = 0
            self.minute_output = 0
            self.minute_state = 0
            self.old_minute_output = 0
            self.second_output = 0
            self.second_state = 0
            self.old_second_output = 0

            self.control_operations = [
                ('hithium_busbar_on_off_output', 'old_hithium_busbar_on_off_output', "Connect Disconnect", 0, 2),
                ('hithium_switch_on_output', 'old_hithium_switch_on_output', "Switch On", 0, 2),
                ('hithium_switch_off_output', 'old_hithium_switch_off_output', "Switch Off", 0, 2),
                ('hithium_min_parallel_racks_output', 'old_hithium_min_parallel_racks_output', "Min Parallel Racks", 0, 10),
                ('insulation_switch_output', 'old_insulation_switch_output', "Insulation Switch", 0, 2),
                ('reset_PCS_output', 'old_reset_PCS_output', "Reset PCS Alarm", 0, 1),
                ('air_con_rem_con_output', 'old_air_con_rem_con_output', "Air con remote control", 0, 1),
                ('year_output', 'old_year_output', "Year", 0, 9999),
                ('month_output', 'old_month_output', "Month", 1, 12),
                ('day_output', 'old_day_output', "Day", 1, 31),
                ('hour_output', 'old_hour_output', "Hour", 0, 23),
                ('minute_output', 'old_minute_output', "Minute", 0, 59),
                ('second_output', 'old_second_output', "Second", 0, 59)
            ]

            # Loop Counter and Time
            self.loop_times = []
            self.counter = 0
            self.print_counter = 47

            # Events
            self.warnings = 0
            self.alarms = 0
            self.faults = 0
            self.actions = []

            # Initial states for comparison to see if the warnings, alarms, faults processes needs to run if there are changes
            self.old_current_warnings = set()
            self.old_current_alarms = set()
            self.old_current_faults = set()

            # HMI data, from which power is derived.
            self.priV = 0  # Primary units of AC Voltage and Current
            self.priA = 0
            self.secV = 0  # Secondary, for DC units
            self.secA = 0
            self.terV = 0  # Tertiary, if a device has a third port, like a PD Hydra
            self.terA = 0

            # Start interval timer
            #self.stop = Event()
            #self.interval = 1  # Interval timeout in Seconds
            #self.thread = Interval(self.stop, self.__process, self.interval)
            #self.thread.start()


            print("Starting " + self.name + " with UID " + str(self.uid) + " on version " + str(self.module_version))

        except Exception as e:
            print("HITHIUM BATTERY: " + str(e))
            log_message(HB_logger, f"Error in initialising __init__ variables: {e}", log_level='error')

    def process(self):
        try:

            try:
                # start loop time to check for efficiency
                start_time = time.time()
            except Exception as e:
                print("HITHIUM BATTERY: " + str(e))
                log_message(HB_logger, f"Error in processing start time: {e}", log_level="error")

            # First run will get all the registers from the Modbus dictionaries and chunk them into register groups
            # This will reduce the amount of Modbus calls required to get all the data.
            # self.register_chunks can then be used to know which registers to read from the device
            if self.first_run:
                try:
                    self.register_chunks = self.chunk_registers(self.all_registers)
                    # log_message(HB_logger, self.register_chunks, log_level="info")
                    self.first_run = False
                except Exception as e:
                    print("HITHIUM BATTERY: " + str(e))
                    log_message(HB_logger,f"Unexpected error in collecting register information - {e}", log_level="error")
                    self.first_run = True

            # Increment the heartbeat counter
            try:
                if self.device_values["BMS heartbeat"] != self.old_BMS_heartbeat:
                    self.old_BMS_heartbeat = self.device_values["BMS heartbeat"]
                    self.heartbeat += 1
                    if self.heartbeat >= 0xFFFF:
                        self.heartbeat = 0
                    self.battery_bank_heartbeat = self.heartbeat
                self.heartbeat_message = 0
            except Exception as e:
                print("HITHIUM BATTERY: " + str(e))
                self.heartbeat_message +=1
                if self.heartbeat_message > 5:
                    log_message(HB_logger,f"Unexpected error in processing heartbeat - {e}", log_level="error")
                
            ### Added simualtion mode to be false (this will be removed in production version) ###
            # if self.simulation_mode:
            #     try:
            #         # Fetch and process simulated data
            #         # self.fetched_raw_data = self.fetch_raw_data(self.register_chunks, simulation_mode=True)
            #         # self.device_values = self.sort_raw_data(self.fetched_raw_data, self.all_registers)
            #         self.enable = True
            #         self.enabled_echo = True
            #         self.set_state_text(State.CONNECTED)
            #         self.update_faults(Faults.LOSS_OF_COMMS.value, False)  # Clear Fault
            #         self.tcp_client = "Sim"
            #     except Exception as e:
            #         log_message(HB_logger,f"Error in simulation mode: {e}", log_level="error")
            

            if self.tcp_timeout == 5:
                self.tcp_timeout = 0
                self.tcp_client = None
                    
            if self.tcp_client is None:
                if self.connection_counter >= 3:
                    try:
                        # trying different port numbers between 503-510 to see if that helps regain a connection
                        if self.port_number < 502:
                            self.port_number = 502
                        if self.port_number < 510:
                            self.port_number += 1
                        else:
                            self.port_number = 503
                        log_message(HB_logger, f"Port number changed to {self.port_number}", log_level="info")
                        self.connection_counter = 0
                    except Exception as e:
                        print("HITHIUM BATTERY: " + str(e))
                        log_message(HB_logger,f"Error in changing port number: {e}", log_level="error")
                        self.port_number = 503
                        self.connection_counter = 0

                self.con_state = False
                try:
                    if "battery_ipaddr_local" in self.dbData:
                        if self.dbData["battery_ipaddr_local"] != "0.0.0.0":
                            try:
                                log_message(HB_logger,"Inside try block for establishing TCP client.", log_level="info") 
                                self.tcp_client = mb_tcp_client(self.dbData["battery_ipaddr_local"], port=self.port_number, timeout=1)

                                if self.tcp_client.connect() is True:
                                    log_message(HB_logger,"TCP client successfully connected.", log_level="info")
                                    self.update_faults(Faults.LOSS_OF_COMMS.value, False)  # Clear Fault
                                    self.set_state_text(State.CONNECTED)
                                    self.connection_counter = 0
                                    self.con_state = True
                                else:
                                    log_message(HB_logger,"TCP client failed to connect.", log_level="warning")
                                    self.update_faults(Faults.LOSS_OF_COMMS.value, True)  # Raise Fault
                                    self.set_state_text(State.CONNECTING)
                                    self.connection_counter += 1
                                    self.tcp_client = None                       

                            except Exception as e:
                                print("HITHIUM BATTERY: " + str(e))
                                log_message(HB_logger,"Exception occurred while establishing TCP client.", log_level="error")
                                self.update_faults(Faults.LOSS_OF_COMMS.value, True)
                                self.connection_counter += 1
                                self.set_state_text(State.CONNECTING)
                        else:
                            self.update_faults(Faults.LOSS_OF_COMMS.value, True)  # Raise Fault
                            self.connection_counter += 1
                            self.set_state_text(State.CONFIG)
                    else:
                        log_message(HB_logger,"IP address is not in the database.", log_level="warning")
                        self.update_faults(Faults.LOSS_OF_COMMS.value, True) # Raise Fault
                        self.connection_counter += 1
                        self.set_state_text(State.CONFIG)
                except Exception as e:
                    print("HITHIUM BATTERY: " + str(e))
                    log_message(HB_logger,f"Error in establishing TCP client: {e}", log_level="error")
                    self.update_faults(Faults.LOSS_OF_COMMS.value, True)  # Raise Fault
                    self.connection_counter += 1
                    self.set_state_text(State.CONNECTING)
                    
            else:
                self.con_state = True

                # Attempt to read all required registers defined in register chunks within a timeout
                # Save the raw data with the register name as a key in a dictionary self.fetched_raw_data
                try:
                    self.print_counter += 1
                    if self.print_counter > 5000:
                        self.print_counter = 0
                    self.fetched_raw_data = self.fetch_raw_data(self.register_chunks)
                    if self.print_counter >= 5000 and in_development == True:
                        # print("\nRaw Data:")
                        raw_data_list = sorted([(reg, value) for reg, value in self.fetched_raw_data.items()])
                        raw_data_formatted = [[self.wrap_text(reg, self.column_width), self.wrap_text(value, self.column_width)] for reg, value in raw_data_list]
                        # print(tabulate(raw_data_formatted, headers=["Register", "Value"], tablefmt="simple"))
                except Exception as e:
                    print("HITHIUM BATTERY: " + str(e))
                    log_message(HB_logger,f"Unexpected error in collecting register information - {e}", log_level="error")
                    self.tcp_timeout += 1
                # Attempt to sort all the raw data into a dictionary of device values
                try:
                    self.device_values = self.sort_raw_data(self.fetched_raw_data, self.all_registers)
                    if self.print_counter >= 5000 and in_development == True:
                        # print("\nDevice Values:")
                        device_values_formatted = [[self.wrap_text(key, self.column_width), self.wrap_text(value, self.column_width)] for key, value in self.device_values.items()]
                        # print(tabulate(device_values_formatted, headers=["Key", "Value"], tablefmt="simple"))
                except Exception as e:
                    print("HITHIUM BATTERY: " + str(e))
                    log_message(HB_logger,f"Unexpected error in processing register data - {e}", log_level="error")
                    self.tcp_timeout += 1
                try:
                    self.device_values_parsed = self.parse_device_values(self.device_values, self.all_registers)
                    if self.print_counter >= 5000 and in_development == True:
                        # print("\nDevice Values Parsed:")
                        wrapped_data = []
                        for key, value in self.device_values_parsed.items():
                            wrapped_key = self.wrap_text(key, self.column_width)
                            if isinstance(value, list):
                                wrapped_values = self.wrap_text(', '.join(value), self.column_width)  # For lists, join before wrapping
                            else:
                                wrapped_values = self.wrap_text(value, self.column_width)  # For single strings, just wrap the string
                            wrapped_data.append([wrapped_key, wrapped_values])

                        # print(tabulate(wrapped_data, headers=["Key", "Values"], tablefmt="simple"))
                        self.print_counter = 0
                except Exception as e:
                    print("HITHIUM BATTERY: " + str(e))
                    log_message(HB_logger,f"Unexpected error in parsing register data - {e}", log_level="error")
                    self.tcp_timeout += 1
                    return

                # Remove the "Reserved_bit14" from the "Rack fault summary" list as it is always on and Hithium say to ignore it. It doesn't do anything?
                try:
                    if "Reserved_bit14" in self.device_values_parsed["Rack fault summary"]:
                        self.device_values_parsed["Rack fault summary"].remove("Reserved_bit14")
                except Exception as e:
                    print("HITHIUM BATTERY: " + str(e))
                    log_message(HB_logger,"Could not remove 'Reserved_bit14' not in self.device_values_parsed[Rack fault summary]", log_level="error")
                    return

                # Run this function def staying_alive(self, device_values): to keep comms alive (try except in function)
                self.staying_alive()

                # Can't find any of these registers on the Modbus register so use general manually assigned header values from class Module(BaseModule)
                # # HMI Header parameters
                # try:
                #     self.name = 
                #     self.manufacturer =
                #     self.fw_ver =
                #     self.serial =

                # except:
                #      self.tcp_timeout += 1
                #      return

                # HMI Volatiles
                # Sort the device values into variables
                try:
                    self.battery_bank_quantity = 1 # Is this variable looking at how many BESS systems are connected?
                    self.battery_rack_quantity = self.device_values["Number of total racks"]
                    self.battery_rack_online_quantity = self.device_values["Number of racks in service"]
                    #  self.battery_bank_heartbeat = self.device_values["BMS heartbeat"]
                    # Do this below when setting the battery state
                    self.battery_bank_soc = self.device_values["System SOC"]
                    self.battery_bank_soh = self.device_values["System SOH"]
                    

                    self.battery_bank_bus_voltage = self.device_values["System Total Voltage"]
                    

                    self.battery_bank_bus_current = self.device_values["System Total Current"]

                    # self.battery_bank_bus_power = self.device_values["Meter info active power"]
                    self.battery_bank_max_chg_current = self.device_values["System Enable Max Charge Current"]
                    self.battery_bank_max_dchg_current = self.device_values["System Enable Max Discharge Current"]
                    self.battery_bank_max_chg_power = self.device_values["Max charge power"]
                    self.battery_bank_max_dchg_power = self.device_values["Max discharge power"]
                    self.battery_bank_min_cell_voltage = self.device_values["System Min Cell Voltage"]
                    self.battery_bank_max_cell_voltage = self.device_values["System Max Cell Voltage"]
                    self.battery_bank_avg_cell_voltage = self.device_values["System Average Voltage"]
                    self.battery_bank_min_cell_temperature = self.device_values["System Min Cell Temperature"]
                    self.battery_bank_max_cell_temperature = self.device_values["System Max Cell Temperature"]
                    self.battery_bank_avg_cell_temperature = self.device_values["System Average Temperature"]
                    # work out self.battery_bank_cycles later
                    # work out self.battery_bank_max_capacity later
                    # work out self.battery_bank_online_capacity later
                    # self.battery_bank_module_count = 1 # Is this variable looking at how many BESS systems are connected?
                    self.hithium_busbar_on_off_state = self.device_values["Connect Disconnect"]

                    self.hithium_switch_on_state = self.device_values["Switch On"]
                    self.hithium_switch_off_state = self.device_values["Switch Off"]

                    self.hithium_min_parallel_racks_state = self.device_values["Min Parallel Racks"]
                    self.insulation_switch_state = self.device_values["Insulation Switch"]
                    self.reset_PCS_state = self.device_values["Reset PCS Alarm"]
                    # self.rack_1_on_off_state = self.device_values["Rack 1 On Off"]
                    self.air_con_rem_con_state = self.device_values["Air con remote control"]



                    self.year_state = self.device_values["Year"]
                    self.month_state = self.device_values["Month"]
                    self.day_state = self.device_values["Day"]
                    self.hour_state = self.device_values["Hour"]
                    self.minute_state = self.device_values["Minute"]
                    self.second_state = self.device_values["Second"]
                    

                    # CALCULATIONS FOR self.outputs[2][x]  
                    # TODO: FIND OUT THE CAPACITY OF EACH SYSTEM
                    if 'System Total Voltage' in self.device_values and 'System Total Current' in self.device_values:
                        if self.device_values['System Total Voltage'] != 0 and self.device_values['System Total Current'] != 0:
                            self.battery_bank_bus_power = ((self.device_values['System Total Voltage'] / 10) * (self.device_values['System Total Current']/10)) / 1000 # Taking into account scalefactor of 0.1 for Voltage and Current
                        else:
                            self.battery_bank_bus_power = 0
                    else:
                        # Handle the case where keys don't exist
                        self.battery_bank_bus_power = 0
                        log_message(HB_logger,"Error: 'System Total Voltage' or 'System Total Current' key not found in device_values", log_level="error")


                    self.battery_bank_charge_state =  self.device_values["System Charge State"]
                    # if self.battery_bank_bus_power == 0:
                    #     self.battery_bank_charge_state = 0  # "Idle"
                    # elif self.battery_bank_bus_power < 0:
                    #     self.battery_bank_charge_state = 1  # "Charge"
                    # elif self.battery_bank_bus_power > 0:
                    #     self.battery_bank_charge_state = 2  # "Discharge"

                    self.battery_bank_max_capacity = self.device_values["System Enable Charge Energy"] + self.device_values["System Enable Discharge Energy"]# This is the max capacity of the battery bank (charge energy + discharge energy) kWh
                    self.battery_bank_online_capacity = self.device_values["System Enable Discharge Energy"] # This is the reamining capacity left in the battery bank that it can discharge (discharge energy) kWh
                    
                    # Work out the accumulated battery cycles
                    # Step 1: Add total accumulation charge and discharge energies
                    self.accum_total_energy =  self.device_values["Accumulation charge energy"] + self.device_values["Accumulation discharge energy"]
                    # Step 2: Divide the sum by 2 to get average energy
                    self.accum_average_energy = self.accum_total_energy / 2
                    # Step 3: Divide by battery bank max capacity to get the number of cycles
                    if self.battery_bank_max_capacity != 0:
                        self.battery_bank_cycles = int(self.accum_average_energy / self.battery_bank_max_capacity)

                except:
                    log_message(HB_logger,f"Unexpected error in processing register data", log_level="error")
                    self.tcp_timeout += 1
                    return
                            
                # Hithium data for SoC - SF 0.1   
                try:
                    if self.battery_bank_soc <= 50:
                        self.icon = "/static/images/Battery0.png"
                    elif self.battery_bank_soc <= 200:
                        self.icon = "/static/images/Battery20.png"
                    elif self.battery_bank_soc <= 400:
                        self.icon = "/static/images/Battery40.png"
                    elif self.battery_bank_soc <= 600:
                        self.icon = "/static/images/Battery60.png"
                    elif self.battery_bank_soc <= 800:
                        self.icon = "/static/images/Battery80.png"
                    elif self.battery_bank_soc <= 1000:
                            self.icon = "/static/images/Battery100.png"
                    ### added this as SoC was above 100 on the hithium (scale factor) ###        
                    elif self.battery_bank_soc >= 1000:
                            self.icon = "/static/images/Battery100.png"
                except:
                    log_message(HB_logger,f"Unexpected error in processing register data - {e}", log_level="error")
                    self.icon = "/static/images/Battery0.png"
                    self.tcp_timeout += 1
                    return
                
                # Check self.enabled and self.enabled_echo to allow flex control of contactors
                # 10 was too short, so I have increase it to 20
                try:
                    if self.enabled == True and self.enabled_echo == False:
                        self.general_write(self.tcp_client, "Connect Disconnect", 1, self.main_system_information_registers["Connect Disconnect"]["register"], 0, 2)
                        self.enabled_check = True
                        self.enabled_counter += 1
                        if self.enabled_counter >= 20:
                            self.enabled = False
                            self.enabled_counter = 0
                            log_message(HB_logger, "Failed to Connect Timeout - Contactors Disabled - self.enabled set back to False", log_level="warning")
                    
                    if self.enabled == True and self.enabled_check == True and self.device_values_parsed["Connection Status"] == "Connected":
                        self.enabled_echo = True
                        self.enabled_check = False
                        self.enabled_counter = 0
                        log_message(HB_logger,"Contactors enabled - Busbar Connection Status: Connected", log_level="info")
                    
                    if self.enabled == False and self.enabled_echo == True:
                        self.general_write(self.tcp_client, "Connect Disconnect", 2, self.main_system_information_registers["Connect Disconnect"]["register"], 0, 2)
                        self.enabled_check = True
                        self.enabled_counter += 1
                        if self.enabled_counter >= 10:
                            self.enabled = True
                            self.enabled_counter = 0
                            log_message(HB_logger,"Failed to Disconnect Timeout - Contactors still enabled - Trying to disconnect again - self.enabled still set as False but self.enabled_echo set to True!!", log_level="warning")

                    if self.enabled == False and self.enabled_check == True and self.device_values_parsed["Connection Status"] != "Connected":
                        self.enabled_echo = False
                        self.enabled_check = False
                        self.enabled_counter = 0
                        log_message(HB_logger,"Contactors disabled - Busbar Connection Status: Disconnected", log_level="info")
                except Exception as e:
                    print("HITHIUM BATTERY: " + str(e))
                    log_message(HB_logger,f"Unexpected error in processing contactor control from Flex controller - {e}", log_level="error")
                    self.tcp_timeout += 1
                    return
                    
                # Check if any changes for manual override process control operation from the control_operations list and write them to the device
                try:
                    for current_attr, old_attr, register_name, min_value, max_value in self.control_operations:
                        current_value = getattr(self, current_attr)
                        old_value = getattr(self, old_attr)
                        if current_value != old_value:
                            self.general_write(
                                self.tcp_client,
                                register_name,
                                current_value,
                                self.main_system_information_registers[register_name]["register"],
                                min_value,
                                max_value
                            )
                            # Update the old value attribute to the current value
                            setattr(self, old_attr, current_value)
                except Exception as e:
                    print("HITHIUM BATTERY: " + str(e))
                    log_message(HB_logger,f"Unexpected error in processing control operations - {e}", log_level="error")
                    self.tcp_timeout += 1
                    return
                
                try:
                    self.process_warnings()
                except Exception as e:
                    print("HITHIUM BATTERY: " + str(e))
                    log_message(HB_logger, f"Unexpected error in processing warnings - {e}", log_level="error")
                    self.tcp_timeout += 1
                    return

                try:
                    self.process_alarms()
                except Exception as e:
                    print("HITHIUM BATTERY: " + str(e))
                    log_message(HB_logger, f"Unexpected error in processing alarms - {e}", log_level="error")
                    self.tcp_timeout += 1
                    return

                try:
                    self.process_faults()
                except Exception as e:
                    print("HITHIUM BATTERY: " + str(e))
                    log_message(HB_logger, f"Unexpected error in processing faults - {e}", log_level="error")
                    self.tcp_timeout += 1
                    return

                try:
                    # Need to swap the charge / discharge number around as the Hithium is the opposite way around compared to the Kore
                    # Therefore change it here rather than in the SCADA
                    if self.device_values["System Charge State"] == 1:
                        self.device_values["System Charge State"] = 2
                    elif self.device_values["System Charge State"] == 2:
                        self.device_values["System Charge State"] = 1

                    if len(self.actions) == 0:
                        self.actions.append(0)  # Dummy

                    # Modify self.outputs to the correct values for the units given in the SCADA
                    self.outputs[2][0] = self.battery_bank_heartbeat
                    self.outputs[2][1] = self.device_values["System Charge State"]                  # self.battery_bank_charge_state
                    self.outputs[2][2] = self.device_values["System SOC"] / 10                      # self.battery_bank_soc
                    self.outputs[2][3] = self.device_values["System SOH"] / 10                      # self.battery_bank_soh
                    self.outputs[2][4] = self.battery_bank_bus_voltage / 10                         # self.battery_bank_bus_voltage
                    self.outputs[2][5] = self.battery_bank_bus_current / 10                         # self.battery_bank_bus_current
                    self.outputs[2][6] = self.battery_bank_bus_power                                # self.battery_bank_bus_power
                    self.outputs[2][7] = self.device_values["Rack Enabled Status"]                  # Bitfield?      # self.battery_bank_cont_states # There is another register for anything over 16 racks.
                    self.outputs[2][8] = self.device_values["Max charge power"] * 1000              # self.battery_bank_max_chg_power # This is going out in Watts (to be changed into Kw later in the SDADA module)
                    self.outputs[2][9] = self.device_values["Max discharge power"] * 1000           # self.battery_bank_max_dchg_power # This is going out in Watts (to be changed into Kw later in the SDADA module)
                    self.outputs[2][10] = self.device_values["System Min Cell Voltage"] / 1000      # self.battery_bank_min_cell_voltage
                    self.outputs[2][11] = self.device_values["System Max Cell Voltage"] / 1000      # self.battery_bank_max_cell_voltage
                    self.outputs[2][12] = self.device_values["System Average Voltage"]  / 1000      # self.battery_bank_avg_cell_voltage
                    self.outputs[2][13] = self.device_values["System Min Cell Temperature"] / 10    # self.battery_bank_min_cell_temperature
                    self.outputs[2][14] = self.device_values["System Max Cell Temperature"] / 10    # self.battery_bank_max_cell_temperature
                    self.outputs[2][15] = self.device_values["System Average Temperature"]  / 10    # self.battery_bank_avg_cell_temperature
                    self.outputs[2][16] = self.battery_bank_cycles                                  # self.battery_bank_cycles
                    self.outputs[2][17] = self.battery_bank_max_capacity                            # self.battery_bank_max_capacity
                    self.outputs[2][18] = self.battery_bank_online_capacity                         # self.battery_bank_online_capacity
                    self.outputs[2][19] = 0
                    self.outputs[2][20] = self.warnings
                    self.outputs[2][21] = self.alarms
                    self.outputs[2][22] = self.faults
                    self.outputs[2][23] = self.actions[0]
                except Exception as e:
                    print("HITHIUM BATTERY: " + str(e))
                    log_message(HB_logger,f"Error in setting outputs: {e}", log_level="error")
                    self.tcp_timeout += 1
                    return
                
                try:
                    # Update the HMI Home page Graphics with the DC Bus parameters
                    self.sV = self.battery_bank_bus_voltage / 10
                    self.sA = self.battery_bank_bus_current / 10
                except Exception as e:
                    print("HITHIUM BATTERY: " + str(e))
                    log_message(HB_logger,f"Error in setting HMI Home page Graphics with the DC Bus parameters: {e}", log_level="error")
                    self.tcp_timeout += 1
                    return

                # HMI Icon Status
                try:
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
                    print("HITHIUM BATTERY: " + str(e))
                    log_message(HB_logger,f"Error in setting state text for warning, alarms, faults: {e}", log_level="error")
                    self.tcp_timeout += 1
                    return
            

            # Loop time calculations for efficiency checking and monitoring performance
            try:
                end_time = time.time()
                current_loop_time = round(end_time - start_time, 2)
                self.loop_times.append(current_loop_time)
                self.counter += 1
                if self.counter >= 1000:
                    loop_time_average = round(sum(self.loop_times) / len(self.loop_times), 2)
                    log_message(HB_logger,f"Average loop time 1000 counts: {loop_time_average:.2f}", log_level="info")
                    self.counter = 0
                    self.loop_times.clear()
            except Exception as e:
                print("HITHIUM BATTERY: " + str(e))
                log_message(HB_logger,f"Error in loop time calculations: {e}", log_level="error")
                self.counter = 0
                self.loop_times.clear()
        except Exception as e:
            print("HITHIUM BATTERY: " + str(e))
            log_message(HB_logger,f"Error in __process method loop: {e}", log_level="error")
            # sleep for 1 second to try and give things chance to recover.
            time.sleep(1)

    def chunk_registers(self, register_dicts):
        chunks = {}
        chunk_counter = 1
        sorted_registers = sorted(
            (reg_info for reg_dict in register_dicts for reg_info in reg_dict.values()),
            key=lambda x: (x['reg_unit_id'], x['register'])
        )

        current_chunk_start = None
        current_chunk_end = None
        current_unit_id = None

        for reg in sorted_registers:
            start_reg = reg['register']
            end_reg = start_reg + reg['reg_count'] - 1  # Adjust for inclusive range
            unit_id = reg['reg_unit_id']

            if current_chunk_start is None:
                # Starting a new chunk
                current_chunk_start = start_reg
                current_chunk_end = end_reg
                current_unit_id = unit_id
            elif start_reg <= current_chunk_end + 1 and unit_id == current_unit_id:
                # Extend the current chunk if the next register is consecutive and the unit ID is the same
                current_chunk_end = max(current_chunk_end, end_reg)
            else:
                # Finalise the current chunk and start a new one
                chunk_name = f"chunk{chunk_counter}"
                chunks[chunk_name] = {
                    "start": format(current_chunk_start, '#06x'),
                    "end": format(current_chunk_end, '#06x'),
                    "unit_id": current_unit_id
                }
                chunk_counter += 1
                current_chunk_start = start_reg
                current_chunk_end = end_reg
                current_unit_id = unit_id

        # Add the last chunk
        if current_chunk_start is not None:
            chunk_name = f"chunk{chunk_counter}"
            chunks[chunk_name] = {
                "start": format(current_chunk_start, '#06x'),
                "end": format(current_chunk_end, '#06x'),
                "unit_id": current_unit_id
            }

        return chunks


    def fetch_raw_data(self, register_chunks):
        # Initialise latest_raw_data as an empty dictionary
        latest_raw_data = {}

        # Use a class attribute to store the last known good data
        if not hasattr(self, 'latest_good_data'):
            self.latest_good_data = {}

        try:
            # Lock the file access for thread safety
            # with self.file_access_lock:
            #     if self.simulation_mode:
            #         # Read data from JSON file for simulation
            #         with open("hithium_raw_data.json", 'r') as file:
            #             latest_raw_data = json.load(file)
            #         # Update and return the latest good data
            #         self.latest_good_data = latest_raw_data
            #         return self.latest_good_data

            # Iterate through each chunk
            for chunk_name, chunk_info in register_chunks.items():
                start_reg_int = int(chunk_info['start'], 16)
                end_reg_int = int(chunk_info['end'], 16)
                num_registers = end_reg_int - start_reg_int + 1
                reg_unit_id = chunk_info['unit_id']

                try:
                    # Read the registers from the device
                    response = self.tcp_client.read_holding_registers(start_reg_int, num_registers, unit=reg_unit_id, timeout=1)

                    # Check if response is valid and not an error
                    if response is None or response.isError():
                        log_message(HB_logger,f"Timeout or error over 1 seconds while reading {chunk_name}", log_level="error")
                        continue

                    # Assuming response.registers contains the list of register values
                    for offset, value in enumerate(response.registers):
                        register_address = format(start_reg_int + offset, '#06x')
                        latest_raw_data[register_address] = value

                except Exception as e:
                    print("HITHIUM BATTERY: " + str(e))
                    # Handle exceptions (e.g., communication errors)
                    log_message(HB_logger,f"Error reading {chunk_name} (Reg: {start_reg_int} to {end_reg_int}): {e}", log_level="error")

            # Update the latest_good_data on successful read
            self.latest_good_data = latest_raw_data

        except Exception as e:
            print("HITHIUM BATTERY: " + str(e))
            log_message(HB_logger,f"Error reading from device - returned last known good data instead: {e}", log_level="error")
            # Return the last known good data instead of crashing
            return self.latest_good_data

        return latest_raw_data

    def sort_raw_data(self, raw_data, register_dicts):
        device_values = {}

        for register_dict in register_dicts:
            for key, reg_info in register_dict.items():
                register_address = format(reg_info['register'], '#06x')
                reg_count = reg_info['reg_count']
                reg_type = reg_info['type']

                try:
                    # Check if the register's raw data is available
                    if register_address in raw_data:
                        if reg_count == 1:
                            # Single register
                            raw_value = raw_data[register_address]
                            if reg_type == 'int16' and (raw_value & (1 << 15)):
                                # Adjust for negative int16
                                raw_value -= 1 << 16
                            device_values[key] = raw_value
                        elif reg_count > 1:
                            # Handle multiple registers
                            if reg_type == 'u32' or reg_type == 'int32':
                                # Combining two registers to form a 32-bit integer
                                combined_value = (raw_data[register_address] << 16) | raw_data[format(reg_info['register'] + 1, '#06x')]
                                if reg_type == 'int32' and (combined_value & (1 << 31)):
                                    # Adjust for negative int32
                                    combined_value -= 1 << 32
                                device_values[key] = combined_value
                            elif reg_type == 'u16' or reg_type == 'int16':
                                # Store as a list of register values for multiple u16 or int16
                                device_values[key] = [raw_data[format(reg_info['register'] + offset, '#06x')] for offset in range(reg_count)]
                                if reg_type == 'int16':
                                    # Adjust each value for negative int16
                                    device_values[key] = [(val - (1 << 16)) if (val & (1 << 15)) else val for val in device_values[key]]
                            else:
                                # Default: list of values for other types
                                device_values[key] = [raw_data[format(reg_info['register'] + offset, '#06x')] for offset in range(reg_count)]
                    # This else statement prints too much data to the log file. Might need it in future, but will comment out for now
                    # else:
                    #     log_message(HB_logger,f"Register data not found for {key} (Reg: {register_address})", log_level="warning")
                except Exception as e:
                    print("HITHIUM BATTERY: " + str(e))
                    log_message(HB_logger,f"Error sorting data for {key} (Reg: {register_address}): {e}", log_level="error")

        return device_values

    def parse_device_values(self, device_values, all_registers):
        device_values_parsed = {}
        # Flatten all_registers into a single dictionary
        all_registers_dict = {k: v for d in all_registers for k, v in d.items()}
        for key, value in device_values.items():
            if key in all_registers_dict:
                register = all_registers_dict[key]
                if register.get('bitfield'):
                    # Assuming value is an integer and labels is a list
                    bitfield_labels = register['labels']
                    parsed_value = [bitfield_labels[i] for i in range(len(bitfield_labels)) if value & (1 << i)]
                    device_values_parsed[key] = parsed_value
                elif register.get('enum'):
                    # Assuming value is an integer and labels is a dictionary
                    enum_labels = register['labels']
                    parsed_value = enum_labels.get(value, 'Unknown')
                    device_values_parsed[key] = parsed_value
        return device_values_parsed
    
    def wrap_text(self, text, width):
        # Wraps text for each cell within the specified width.
        return '\n'.join(textwrap.wrap(str(text), width))
    
    def staying_alive(self):
        try:
            heartbeat = self.device_values.get("SBMU Heartbeat")
            if heartbeat == 0:
                self.tcp_client.write_register(0x0002, 1, unit=1)
            else:
                self.tcp_client.write_register(0x0002, 0, unit=1)
        except Exception as e:
            print("HITHIUM BATTERY: " + str(e))
            log_message(HB_logger,f"Error writing SBMU Heartbeat: {e}", log_level="error")
            
    def general_write(self, client, register_key, value, register_address, min_value, max_value):
        """
        Writes a given value to a Modbus register and confirms the operation,
        after validating that the value is an integer within a specified range.

        Parameters:
        - register_key: A descriptive name or key for the register for logging purposes.
        - client: The pymodbus client instance to use for communication.
        - value: The value to write to the register.
        - register_address: The address of the register to write to.
        - min_value: The minimum allowable value for the write operation.
        - max_value: The maximum allowable value for the write operation.
        Raises:
        - ValueError: If `value` is not an integer or is out of the specified range.
        """
        log_message(HB_logger, f"Attempting to write value {value} to register '{register_key}' (Address: {register_address})", log_level="info")

        # Validate the value is an integer
        if not isinstance(value, int):
            log_message(HB_logger, f"Error: Provided value {value} is not an integer for register '{register_key}'.", log_level="error")
            return  # Exit the function early

        # Validate the value is within range
        if not min_value <= value <= max_value:
            log_message(HB_logger, f"Error: Value {value} for '{register_key}' is out of range [{min_value}, {max_value}].", log_level="error")
            return  # Exit the function early

        try:
            # Perform the write operation
            write_response = client.write_register(register_address, value, unit=1)
            if write_response.isError():
                raise IOError(f"Write operation failed for register '{register_key}' (Address: {register_address})")

            log_message(HB_logger, f"Written value {value} at register '{register_key}' (Address: {register_address})", log_level="info")

        except Exception as e:
            print("HITHIUM BATTERY: " + str(e))
            log_message(HB_logger, f"Error during write operation for '{register_key}': {e}", log_level="error")


    # Functions for processing warnings, alarms, and faults
    def process_warnings(self):
        current_warnings = set(self.device_values_parsed.get("Rack Alarm I Summary", []))
        if current_warnings != self.old_current_warnings:
            self.process_states(current_warnings, self.warning_types, self.update_warnings)
            self.old_current_warnings = current_warnings

    def process_alarms(self):
        current_alarms = set(self.device_values_parsed.get("Rack Alarm II Summary", [])) | set(self.device_values_parsed.get("Alarm", []))
        if current_alarms != self.old_current_alarms:
            self.process_states(current_alarms, self.alarm_types, self.update_alarms)
            self.old_current_alarms = current_alarms

    def process_faults(self):
        current_faults = set(self.device_values_parsed.get("Rack Alarm III Summary", [])) | set(self.device_values_parsed.get("SBMU Alarm State", [])) | set(self.device_values_parsed.get("Rack fault summary", []))
        if current_faults != self.old_current_faults:
            self.process_states(current_faults, self.fault_types, self.update_faults)
            self.old_current_faults = current_faults

    def process_states(self, current_states, state_types, update_method):
        # Dictionary to track the active state of each enum
        state_dict = {state_enum: False for state_enum in state_types.values()}
        
        # Set the active state based on current states
        for state_key in current_states:
            if state_key in state_types:
                state_enum = state_types[state_key]
                state_dict[state_enum] = True
        
        # Update the states based on the final states
        for state_enum, is_active in state_dict.items():
            update_method(state_enum.value, is_active)

    def set_inputs(self, inputs):
        #try:
        for module in inputs:
            if module[0] == self.uid:
                if len(module) > 0:
                    if self.override is False:
                        self.inputs = module

                    self.enabled = self.inputs[1]
                    
        return [SET_INPUTS_ACK]
        #except Exception as e:
        #    log_message(HB_logger,f"Error setting inputs: {e}", log_level="error")

    def get_outputs(self):
        #try:
        self.outputs[1] = self.enabled_echo

        #outputs = copy.deepcopy(self.outputs)
        #return outputs
        return [GET_OUTPUTS_ACK, self.outputs]
        #except Exception as e:
        #log_message(HB_logger,f"Error getting outputs: {e}", log_level="error")

    def set_page(self, page, form):  # Respond to GET/POST requests
        try:
            if page == self.website + "_(" + str(self.uid) + ")":  # It was a page request for a numbered device (POST)

                controls_to_skip = ["hithium_busbar_on_off_output", "hithium_switch_on_output", 
                                    "hithium_switch_off_output", "hithium_min_parallel_racks_output", 
                                    "insulation_switch_output", "reset_PCS_output", "rack_1_on_off", 
                                    "air_con_rem_con_output", "year_output", "month_output", "day_output",
                                    "hour_output", "minute_output", "second_output"]

                # Process all controls
                for control in form:
                    value = form[control]
                    if control in controls_to_skip:
                        # Process these controls without saving to DB
                        if value.isdigit():
                            setattr(self, control, int(value))
                        else:
                            log_message(HB_logger, f"Data for {control} cannot be turned into an integer", log_level="error")
                    elif control == "battery_output_override":
                        # Handle override toggle
                        self.override = not self.override
                        self.actions.insert(0, Actions.CTRL_OVERRIDE_CHANGE.value)
                        # self.dbData[control] = self.override # Don't need to save this to the db.json
                    elif control == "battery_ipaddr_local":
                        # Process and save IP address changes
                        self.dbData[control] = value
                        self.actions.insert(0, Actions.IP_ADDRESS_CHANGE.value)
                    else:
                        # Process other controls normally
                        self.dbData[control] = value

                # Save changes to the database
                if "battery_ipaddr_local" in form or "battery_output_override" in form:
                    self.save_to_db()

                # Maintain last 10 user interactions
                if len(self.actions) > 10:
                    self.actions.pop()
                    
                return [SET_PAGE_ACK]

            elif page == (self.website + "_(" + str(self.uid) + ")/data"):  # It was a json data fetch quest (POST)

                mod_data = dict()

                # Clear old lists
                #self.actions = []

                # Controller Information
                mod_data["battery_name"] = self.name
                mod_data["battery_man"] = self.manufacturer
                mod_data["battery_fwver"] = self.version
                mod_data["battery_serno"] = self.serial
                mod_data["battery_constate"] = str(self.con_state).capitalize()
                mod_data["battery_override"] = self.override
                mod_data["battery_enablestate"] = self.enabled
                mod_data["battery_bank"] = self.outputs[2]

                mod_data["hithium_busbar_on_off_output"] = self.hithium_busbar_on_off_output
                mod_data["hithium_busbar_on_off_state"] = self.hithium_busbar_on_off_state
                mod_data["hithium_switch_on_state"] = self.hithium_switch_on_state
                mod_data["hithium_switch_off_state"] = self.hithium_switch_off_state
                mod_data["hithium_switch_on_output"] = self.hithium_switch_on_output
                mod_data["hithium_switch_off_output"] = self.hithium_switch_off_output
                mod_data["hithium_min_parallel_racks_output"] = self.hithium_min_parallel_racks_output
                mod_data["hithium_min_parallel_racks_state"] = self.hithium_min_parallel_racks_state
                mod_data["insulation_switch_output"] = self.insulation_switch_output
                mod_data["insulation_switch_state"] = self.insulation_switch_state
                mod_data["reset_PCS_output"] = self.reset_PCS_output
                mod_data["reset_PCS_state"] = self.reset_PCS_state
                mod_data["rack_1_on_off"] = self.rack_1_on_off
                mod_data["rack_1_on_off_state"] = self.rack_1_on_off_state
                mod_data["air_con_rem_con_output"] = self.air_con_rem_con_output
                mod_data["air_con_rem_con_state"] = self.air_con_rem_con_state
                mod_data["year_state"] = self.year_state
                mod_data["month_state"] = self.month_state
                mod_data["day_state"] = self.day_state
                mod_data["hour_state"] = self.hour_state
                mod_data["minute_state"] = self.minute_state
                mod_data["second_state"] = self.second_state
                mod_data["year_output"] = self.year_output
                mod_data["month_output"] = self.month_output
                mod_data["day_output"] = self.day_output
                mod_data["hour_output"] = self.hour_output
                mod_data["minute_output"] = self.minute_output
                mod_data["second_output"] = self.second_output


                # pass on parsed data
                mod_data["device_values_parsed"] = self.device_values_parsed
                
                mod_data.update(self.dbData)  # I'm appending the dict here so we don't save unnecessary
                
                return [SET_PAGE_ACK, mod_data]  # data to the database.

            else:
                return 'OK', 200  # Return the data to be jsonified
            
        except Exception as e:
            print("HITHIUM BATTERY: " + str(e))
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
        # This shouldn't be triggered by buttons!
        try:
            db.save_to_db(__name__ + "_(" + str(self.uid) + ")", self.dbData)
        except:
            log_message(HB_logger, "Unable to save record, may already exist?", log_level='warning')  # Todo find error code from tinydb

    def kill(self):
        try:
            self.stop.set()
        except Exception as e:
            print("HITHIUM BATTERY: " + str(e))
            log_message(HB_logger,f"Error killing module: {e}", log_level="error")

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
            print("HITHIUM BATTERY: " + str(e))
          
        try:
            if tx_msg is not None:
                queue[0].put(tx_msg, block=True)
                
        except Exception as e:
            print("HITHIUM BATTERY: " + str(e))
            
 
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

if __name__ == '__main__':  # The module must be able to run solo for testing purposes
    pass