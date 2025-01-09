# Kore - Battery Module
# TODO: Cycles...ugh
# TODO: Finish single stack alerts...later
# TODO: Include echo code in the override mechanism...maybe
# TODO: Alerts on temperature breaches. If possible
import sys
from pymodbus.client.sync import ModbusTcpClient as mb_tcp_client
from threading import Thread, Event
from FlexDB import FlexTinyDB
from enum import Enum
from pymodbus.constants import Endian
from pymodbus.payload import BinaryPayloadDecoder

import time
import logging

import copy
from datetime import datetime
import os
import inspect

# Database
db = FlexTinyDB()

USE_KORE_SOC = True                                                                                # Setting True uses the SoC reported by Kore. False uses the OCV Lookup table

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



#Setup logger
def setup_logging(in_development=True):
    logger = logging.getLogger('kore_logger')
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
        print(f"Error setting up kore logger: {e}")

    return logger

def log_message(logger, message, log_level='info'):
    try:
        # Change this prefix if you want to use it for a different module
        prefix = "kore: "
        frame = inspect.stack()[1]
        python_line = frame.lineno
        full_message = f"{prefix}{message} line: {python_line}"

        log_method = getattr(logger, log_level.lower(), logger.info)
        log_method(full_message)
    
    except Exception as e:
        print(f"Error logging kore message: {e}")

# If you want to log to file put in_development as True, otherwise False for production
in_development = True  # Set to False for production
kore_logger = setup_logging(in_development)

# Example of using the logger
# If logger in_development = True it will print to file - if False it will print to CLI
# log_message(kore_logger, "System started", log_level='info')
# log_message(kore_logger, "Potential issue detected", log_level='warning')
# log_message(kore_logger, "Critical error occurred", log_level='error')

# class Interval(Thread):
#     def __init__(self, event, process, interval):
#         try:
#             Thread.__init__(self)
#             self.stopped = event
#             self.target = process
#             self.interval = interval
#         except Exception as e:
#             log_message(kore_logger, f"Error initialising Interval thread in kore: {e}", log_level='error')

#     def run(self):
#         while not self.stopped.wait(self.interval):
#             try:
#                 self.target()
#             except Exception as e:
#                 log_message(kore_logger, f"Error in running Interval thread in kore: {e}", log_level='error')


class Module():
    def __init__(self, uid, queue):
        try:
            # super(Module, self).__init__()

            # General Module Data
            self.author = "Sophie Coates"
            self.uid = uid  # A unique identifier passed from the HMI via a mangled URL
            self.icon = "/static/images/Battery.png"
            self.name = "Kore Battery"
            self.module_type = ModTypes.BATTERY.value
            self.manufacturer = "KORE"
            self.fw_ver = 0
            self.model = ""
            self.options = ""
            self.version = 0
            self.serial = ""  # This can be replaced with the device serial number later
            self.website = "/Mod/FlexMod_KoreBattery"  # This is the template name itself

            # Run state
            self.con_state = False
            self.state = "Idle"
            self.set_state_text(State.IDLE)

            # Non-Volatile Data (Loaded from DB)
            self.dbData = db.fetch_from_db(__name__ + "_(" + str(self.uid) + ")")
            if self.dbData is None:
                self.dbData = dict()
                self.dbData["battery_ipaddr_local"] = "0.0.0.0"

            # Volatile Data
            self.tcp_client = None
            self.tcp_timeout = 0
            self.inputs = [self.uid, False, [False * 14]]  # All reserved
            self.enabled = False
            self.enabled_echo = False
            self.outputs = [self.uid, self.enabled, [0]*25]
            self.heartbeat = 0
            self.process_timeout = 5
            self.process_timeout_counter = 0
            self.process_retries = 0
            self.alert_timeout = 5
            # TODO: This should reflect the amount of battery racks!!
            self.warning_timeout_counter = [0] * 20
            self.alarm_timeout_counter = [0] * 20
            self.fault_timeout_counter = [0] * 20
            self.heartbeat = 0
            self.heartbeat_echo = 0

            # HMI Volatiles
            self.override = False
            self.contactor_state = False

            # Device Volatiles
            self.battery_bank_quantity = 1
            self.battery_rack_quantity = 0
            self.battery_rack_online_quantity = 0
            self.battery_bank_heartbeat = 0
            self.battery_bank_cont_states = 0
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
            self.battery_bank_module_count = 0

            self.battery_bank_rmtctrl = 0
            self.battery_min_enabled_racks = 0
            self.battery_bank_length = 0
            self.number_of_strings = 0
            self.reconnect_counter = 0


            self.battery_last_soc = 0
            self.battery_import_kw = 0                                                                  # Cycle counting
            self.battery_export_kw = 0

            # Flag to check if common data has been read
            self.common_data_count = 0
            self.start_time = 0
            self.end_time = 0
            self.loop_times = []
            self.counter = 0

            self.common_data_counter = 0

            self.cal_interval = 0
            # self.battery_prescaler = 0  # Divide the interval count because Kore is a pain and we've only successfully run it at 2sec intervals

            # Tables
            self.soc_t = {"T": [-30,    -25,    -20,    -15,    -10,    -5,     0,      5,      10,     15,     20,     25,     30,     35,     40,     45,     50,     55],
                        100: [4.254,  4.235,  4.260,  4.258,  4.258,  4.264,  4.266,  4.267,  4.261,  4.265,  4.261,  4.265,  4.258,  4.260,  4.253,  4.258,  4.257,  4.248],
                        95:  [4.192,	4.151,  4.201,  4.202,  4.201,	4.209,	4.209,	4.209,	4.203,	4.205,	4.202,	4.199,	4.195,	4.194,	4.191,	4.194,	4.194,	4.187],
                        90:  [4.158,  4.107,	4.167,	4.169,	4.166,	4.170,	4.171,	4.170,	4.161,	4.164,	4.160,	4.151,	4.148,	4.145,	4.142,	4.144,	4.145,	4.136],
                        85:  [4.125,	4.067,	4.135,	4.136,	4.129,	4.130,	4.131,	4.128,	4.117,	4.118,	4.114,	4.101,	4.095,	4.092,	4.089,	4.089,	4.089,	4.082],
                        80:  [4.094,	4.031,	4.101,	4.102,	4.089,	4.089,	4.088,	4.083,	4.070,	4.069,	4.063,	4.047,	4.040,	4.037,	4.033,	4.033,	4.032,	4.024],
                        75:  [4.063,	3.996,	4.065,	4.066,	4.048,	4.046,	4.044,	4.036,	4.021,	4.020,	4.013,	3.993,	3.986,	3.983,	3.977,	3.978,	3.977,	3.968],
                        70:  [4.028,	3.960,	4.026,	4.025,	4.004,	4.002,	3.997,	3.987,	3.973,	3.970,	3.962,	3.941,	3.931,	3.930,	3.923,	3.923,	3.921,	3.913],
                        65:  [3.994,	3.925,	3.986,	3.985,	3.960,	3.958,	3.952,	3.941,	3.927,	3.922,	3.913,	3.890,	3.879,	3.878,	3.870,	3.870,	3.867,	3.859],
                        60:  [3.961,	3.892,	3.945,	3.945,	3.918,	3.914,	3.910,	3.897,	3.881,	3.877,	3.866,	3.840,	3.829,	3.829,	3.817,	3.818,	3.813,	3.804],
                        55:  [3.927,	3.856,	3.904,	3.902,	3.873,	3.869,	3.865,	3.849,	3.835,	3.829,	3.815,	3.787,	3.772,	3.775,	3.760,	3.761,	3.755,	3.748],
                        50:  [3.893,	3.820,	3.864,	3.858,	3.825,	3.821,	3.817,	3.798,	3.785,	3.776,	3.758,	3.731,	3.715,	3.721,	3.706,	3.709,	3.704,	3.702],
                        45:  [3.861,	3.787,	3.823,	3.816,	3.781,	3.772,	3.770,	3.747,	3.733,	3.724,	3.710,	3.691,	3.683,	3.688,	3.676,	3.681,	3.677,	3.674],
                        40:  [3.827,	3.752,	3.785,	3.774,	3.742,	3.730,	3.726,	3.706,	3.695,	3.688,	3.679,	3.665,	3.659,	3.664,	3.655,	3.659,	3.656,	3.654],
                        35:  [3.794,	3.719,	3.750,	3.738,	3.703,	3.697,	3.693,	3.677,	3.670,	3.664,	3.657,	3.647,	3.640,	3.646,	3.637,	3.640,	3.637,	3.637],
                        30:  [3.764,	3.690,	3.719,	3.709,	3.679,	3.671,	3.670,	3.658,	3.651,	3.648,	3.641,	3.631,	3.626,	3.632,	3.620,	3.624,	3.619,	3.617],
                        25:  [3.735,	3.662,	3.692,	3.683,	3.658,	3.652,	3.652,	3.642,	3.636,	3.632,	3.626,	3.615,	3.607,	3.614,	3.595,	3.597,	3.586,	3.583],
                        20:  [3.707,	3.635,	3.669,	3.661,	3.639,	3.635,	3.635,	3.626,	3.622,	3.616,	3.610,	3.595,	3.575,	3.584,	3.562,	3.562,	3.553,	3.552],
                        15:  [3.684,	3.613,	3.650,	3.644,	3.626,	3.620,	3.623,	3.613,	3.608,	3.603,	3.590,	3.563,	3.545,	3.553,	3.529,	3.532,	3.520,	3.517],
                        10:  [3.662,	3.591,	3.632,	3.628,	3.611,	3.606,	3.610,	3.598,	3.591,	3.579,	3.557,	3.532,	3.507,	3.518,	3.487,	3.489,	3.477,	3.474],
                        5:   [3.640,	3.570,	3.616,	3.612,	3.595,	3.595,	3.594,	3.575,	3.564,	3.547,	3.524,	3.491,	3.466,	3.475,	3.453,	3.452,	3.440,	3.437],
                        0:   [3.635,	3.595,	3.606,	3.599,	3.580,	3.589,	3.581,	3.554,	3.534,	3.516,	3.478,	3.461,	3.448,	3.444,	3.355,	3.314,	3.237,	3.213]}

            # Events
            self.warnings = 0
            self.alarms = 0
            self.faults = 0
            self.actions = []

            self.acc_evt1 = 0
            self.evt1 = 0
            self.evtvnd1 = 0

            # Calcultae SoC drift and recalibration
            self.battery_soc_drift = False
            self.recalibration_in_progress = False
            self.recalibraction_counter = 0
            self.current_rack_id = 1 
            self.recalibration_counter = 0
            self.development_counter = 0
            

            self.acc_evtvnd1 = 0
            self.warning_map = {
                2: Warnings.OVERTEMP.value,
                4: Warnings.UNDERTEMP.value,
                6: Warnings.OVERCHARGE.value,
                8: Warnings.OVERDISCHARGE.value,
                10: Warnings.OVERVOLT.value,
                12: Warnings.UNDERVOLT.value,
                14: Warnings.SOC_LOW.value,
                16: Warnings.SOC_HIGH.value,
                19: Warnings.TEMP_DIFF.value,
                24: Warnings.CURRENT_DIFF.value,
                26: Warnings.OTHER.value,
                29: Warnings.CONFIG.value,
                23: Warnings.INSULATION.value  # From evtvnd1
            }

            self.alarm_map = {
                1: Alarms.OVERTEMP.value,
                3: Alarms.UNDERTEMP.value,
                5: Alarms.OVERCHARGE.value,
                7: Alarms.OVERDISCHARGE.value,
                9: Alarms.OVERVOLT.value,
                11: Alarms.UNDERVOLT.value,
                13: Alarms.SOC_LOW.value,
                15: Alarms.SOC_HIGH.value,
                18: Alarms.TEMP_DIFF.value,
                25: Alarms.OTHER.value,
                28: Alarms.CONFIG.value
            }

            self.fault_map = {
                9: Faults.OVERTEMP.value,
                10: Faults.UNDERTEMP.value,
                11: Faults.TEMP_DIFF.value,
                12: Faults.OVERVOLT.value,
                13: Faults.UNDERVOLT.value,
                14: Faults.OVERCHARGE.value,
                15: Faults.OVERDISCHARGE.value,
                17: Faults.RBMS_COMMS.value,
                24: Faults.INSULATION.value
            }

            # HMI data, from which power is derived.
            self.priV = 0  # Primary units of AC Voltage and Current
            self.priA = 0
            self.secV = 0  # Secondary, for DC units
            self.secA = 0
            self.terV = 0  # Tertiary, if a device has a third port, like a PD Hydra
            self.terA = 0

            # Start interval timer
            # self.stop = Event()
            # self.interval = 1  # Interval timeout in Seconds
            # self.thread = Interval(self.stop, self.__process, self.interval)
            # self.thread.start()

            print("Starting " + self.name + " with UID " + str(self.uid))

            # Track Interval usage (GIL interference)
            self.start_time = time.time()
            self.interval_count = 0
            self.max_delay = 0
            self.last_time = 0

        except Exception as e:
            log_message(kore_logger, f"Error initialising kore module: {e}", log_level='error')

    def process(self):

        try:
            global loop_time
            #print("(2)  Battery Heartbeat: " + str(self.heartbeat) + ", loop time: " + str(loop_time) + " Seconds")
            
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
                #print("Kore Battery " + str(self.uid) + ": " + str(self.interval_count))
            else:
                #print("Kore Battery " + str(self.uid) + ": " + str(self.interval_count) + " events in 1 hour. Maximum gap between events was " + str(self.max_delay) + " seconds.")
                pass
            
            s = time.time()
        except Exception as e:
            log_message(kore_logger, f"Error in getting time in kore module: {e}", log_level='error')

        # Heartbeat
        try:
            try:
                self.heartbeat += 1
                if self.heartbeat >= 0xFFFF:
                    self.heartbeat = 0
                self.battery_bank_heartbeat = self.heartbeat                                        # Overwritten by the device if it is running
            except Exception as e:
                log_message(kore_logger, f"Error in heartbeat in kore module: {e}", log_level='error')
            # # The battery complains if we poll it every second over Sunspec, so this just divides it down.
            # self.battery_prescaler += 1
            # if self.battery_prescaler < 2:
            #     return
            # self.battery_prescaler = 0
            #     # Check for TCP connection
            if self.tcp_timeout >= 5:
                self.tcp_client = None
                self.tcp_timeout = 0     # Without this we'll never escape the Nonoe state after the first error encountered.

            if self.tcp_client is None:
                self.con_state = False
                try:
                    if "battery_ipaddr_local" in self.dbData:
                        if self.dbData["battery_ipaddr_local"] != "0.0.0.0":
                            try:
                                self.tcp_client = mb_tcp_client(self.dbData["battery_ipaddr_local"], port=502)

                                if self.tcp_client.connect() is True:
                                    self.update_faults(Faults.LOSS_OF_COMMS.value, False)  # Clear Fault
                                    self.set_state_text(State.CONNECTED)
                                    self.con_state = True
                                else:
                                    self.update_faults(Faults.LOSS_OF_COMMS.value, True)  # Raise Fault
                                    self.set_state_text(State.CONNECTING)
                                    self.tcp_client = None

                            except Exception as e:
                                log_message(kore_logger,f"Error in comms TCP Client in Kore Battery: {e}", log_level='warning')
                                self.update_faults(Faults.LOSS_OF_COMMS.value, True)
                                self.set_state_text(State.CONNECTING)
                        else:
                            self.update_faults(Faults.LOSS_OF_COMMS.value, True)  # Raise Fault
                            self.set_state_text(State.CONFIG)
                    else:
                        self.update_faults(Faults.LOSS_OF_COMMS.value, True)
                        self.set_state_text(State.CONFIG)
                except Exception as e:
                    log_message(kore_logger,f"Error in establishing TCP Client in Kore Battery: {e}", log_level='warning')
                    self.update_faults(Faults.LOSS_OF_COMMS.value, True)  # Raise Fault
                    self.set_state_text(State.CONNECTING)
            else:
                self.con_state = True
                
                # General Monitoring. Requires the E-Stop interlocks / Rack Power enabled for live data before closing contactors.

            # Common data
                try:
                    if self.common_data_counter == 2:
                        rr = self.tcp_client.read_holding_registers(40004, 66, unit=1)
                        
                        if rr.isError():
                            log_message(kore_logger, f"Error fetching common data in Kore: {rr}", log_level='error')
                            self.tcp_timeout += 1
                            return
                        elif len(rr.registers) != 66:
                            log_message(kore_logger, f"Invalid common data quantity: {rr}", log_level='error')
                            self.tcp_timeout += 1
                            return
                        else:
                            self.tcp_timeout = 0

                            # Manufacturer
                            self.manufacturer = self.decode_str(rr.registers, 0, 16)

                            # Model
                            self.model = self.decode_str(rr.registers, 16, 16)

                            # Options
                            self.options = self.decode_str(rr.registers, 32, 8)

                            # Version
                            self.version = self.decode_str(rr.registers, 40, 8)

                            # Serial Number
                            self.serial = self.decode_str(rr.registers, 48, 16)

                            # Device Address
                            self.device_address = self.decode_u16(rr.registers, 64)

                    self.common_data_counter += 1
                    if self.common_data_counter >= 5000:
                        self.common_data_counter = 0
                except Exception as e:
                    log_message(kore_logger, f"Error in reading common data in Kore Battery: {e}", log_level='error')
                    self.tcp_timeout += 1
                    return

            # Lithium Ion Bank
                try:
                    rr = self.tcp_client.read_holding_registers(40168, 1, unit=1, timeout=0.25)
                    if rr.isError():
                        log_message(kore_logger, f"Error reading rack quantity registers: {rr}", log_level='error')
                        self.tcp_timeout += 1
                        return
                    elif len(rr.registers) != 1:
                        log_message(kore_logger, f"Invalid rack data quantity: {rr}", log_level='error')
                        self.tcp_timeout += 1
                        return
                    else:
                        self.tcp_timeout = 0
                        self.battery_rack_quantity = self.decode_u16(rr.registers, 0)
                        self.number_of_strings = self.battery_rack_quantity * 32
                except TimeoutError as e:
                    log_message(kore_logger, "Timeout occurred while reading rack quantity registers", log_level='error')
                    self.tcp_timeout += 1
                    return
                except Exception as e:
                    log_message(kore_logger, f"Error in reading rack quantity in Kore Battery: {e}", log_level='error')
                    self.tcp_timeout += 1
                    return
                
                try:
                    # Clears on each rotation
                    self.battery_bank_cont_states = 0
                    self.battery_rack_online_quantity = 0
                    self.battery_bank_soc = 0
                    self.battery_bank_soh = 0
                    self.battery_bank_bus_voltage = 0
                    self.battery_bank_bus_current = 0
                    self.battery_bank_bus_power = 0
                    self.battery_bank_max_chg_power = 0
                    self.battery_bank_max_dchg_power = 0
                    self.battery_bank_min_cell_voltage = 0
                    self.battery_bank_max_cell_voltage = 0
                    self.battery_bank_avg_cell_voltage = 0
                    self.battery_bank_min_cell_temperature = 0
                    self.battery_bank_max_cell_temperature = 0
                    self.battery_bank_avg_cell_temperature = 0
                    self.battery_bank_module_count = 0
                    self.battery_rack_max_soc = 0
                    self.battery_rack_min_soc = 100

                    self.offline_battery_bank_soc = 0
                    self.offline_battery_bank_soh = 0
                    self.offline_battery_bank_bus_voltage = 0
                    self.offline_battery_bank_bus_current = 0
                    self.offline_battery_bank_bus_power = 0
                    self.offline_battery_bank_max_chg_power = 0
                    self.offline_battery_bank_max_dchg_power = 0
                    self.offline_battery_bank_min_cell_voltage = 0
                    self.offline_battery_bank_max_cell_voltage = 0
                    self.offline_battery_bank_avg_cell_voltage = 0
                    self.offline_battery_bank_min_cell_temperature = 0
                    self.offline_battery_bank_max_cell_temperature = 0
                    self.offline_battery_bank_avg_cell_temperature = 0
                    self.offline_battery_bank_module_count = 0
                    self.offline_battery_rack_max_soc = 0
                    self.offline_battery_rack_min_soc = 100
                    self.evt1 = 0
                    self.evtvnd1 = 0
                    self.acc_evt1 = 0
                    self.acc_evtvnd1 = 0
                    self.acc_evtvnd2 = 0
                    self.battery_bank_cycles = 0

                    self.battery_bank_rmtctrl = 2 # set this as not 1 or 0 to avoid the remote control statements if not read from battery data
                except Exception as e:
                    log_message(kore_logger,f"Error in clearing battery bank data in Kore Battery: {e}", log_level='error')
                    return

                #############
                # Rack Data #
                #############
                # No need to get Rack Data as I can get all the variables from the Bank Data #

                # # All data references are from the Kore Modbus Map using expanding registers 
                # try:
                #     # Calculate length for Battery Bank Model
                #     self.battery_bank_length = 2 + 26 + self.number_of_strings


                #     if self.battery_rack_quantity == 1:
                #         start_address = 40196
                #         rr = self.tcp_client.read_holding_registers(start_address, 30, unit=1)
                #         if rr.isError():
                #             log_message(kore_logger, f"Single Rack - Error reading battery bank registers for rack {rack}: {rr}", log_level='error')
                #             self.tcp_timeout += 1
                #         elif len(rr.registers) != 30:
                #             log_message(kore_logger, f"Single Rack - Invalid battery bank data length for rack {rack}: {rr}", log_level='error')
                #             self.tcp_timeout += 1
                #         else:
                #             self.tcp_timeout = 0

                #     elif self.battery_rack_quantity > 1:
                #         for rack in range(self.battery_rack_quantity):
                #             start_address = 40196 + (rack * 32)  # Adjust the start address for each rack
                #             rr = self.tcp_client.read_holding_registers(start_address, 30, unit=1)
                #             if rr.isError():
                #                 log_message(kore_logger, f"Error reading battery bank registers for rack {rack}: {rr}", log_level='error')
                #                 self.tcp_timeout += 1
                #             elif len(rr.registers) != 30:
                #                 log_message(kore_logger, f"Invalid battery bank data length for rack {rack}: {rr}", log_level='error')
                #                 self.tcp_timeout += 1
                        
                #             else:
                #                 self.tcp_timeout = 0
                #                 #print(f'Battery bank data for rack {rack+1}: {rr.registers}')
                #                 # Process the battery bank data if needed
                        
                #         # Offline, so copy over the buffered data.
                #     elif self.battery_rack_online_quantity == 0:
                #         self.battery_bank_soc = self.offline_battery_bank_soc
                #         self.battery_bank_soh = self.offline_battery_bank_soh
                #         self.battery_bank_bus_voltage = self.offline_battery_bank_bus_voltage
                #         self.battery_bank_bus_current = self.offline_battery_bank_bus_current

                #         self.battery_bank_min_cell_voltage = self.offline_battery_bank_min_cell_voltage
                #         self.battery_bank_max_cell_voltage = self.offline_battery_bank_max_cell_voltage
                #         self.battery_bank_avg_cell_voltage = self.offline_battery_bank_avg_cell_voltage

                #         self.battery_bank_min_cell_temperature = self.offline_battery_bank_min_cell_temperature
                #         self.battery_bank_max_cell_temperature = self.offline_battery_bank_max_cell_temperature
                #         self.battery_bank_avg_cell_temperature = self.offline_battery_bank_avg_cell_temperature

                #         self.battery_bank_module_count = self.offline_battery_bank_module_count

                #         # Used to calculate drift between racks
                #         self.battery_rack_max_soc = self.offline_battery_rack_max_soc
                #         self.battery_rack_min_soc = self.offline_battery_rack_min_soc
                # except Exception as e:
                #     log_message(kore_logger, f"Error in reading lithium-ion battery bank data: {e}", log_level='error')
                #     self.tcp_timeout += 1
                #     return

                # Bank data

                # Lithium-Ion String Model Data
                try:
                    # Calculate starting address for the Lithium-Ion String Model
                    string_model_start_address = 40166 + ((2 + 26) + self.number_of_strings)

                    # Hardcoded scale factors based on the correct mapping
                    CellV_SF = -3  # 10^-3 or divide by 1000
                    ModTmp_SF = 0  # 10^0 or no scaling
                    Amp_SF = -1  # 10^-1 or divide by 10
                    SoH_SF = 0  # 10^0 or no scaling
                    SoC_SF = 0  # 10^0 or no scaling
                    Vol_SF = -1  # 10^-1 or divide by 10

                    # Lists to store values for averaging
                    contactor_value_list = []
                    soc_list = []
                    soc_list_online = []
                    soh_list = []
                    soh_list_online = []
                    bus_voltage_list = []
                    bus_voltage_list_online = []
                    bus_current_list = []
                    bus_current_list_online = []
                    min_cell_voltage_list = []
                    min_cell_voltage_list_online = []
                    max_cell_voltage_list = []
                    max_cell_voltage_list_online = []
                    avg_cell_voltage_list = []
                    avg_cell_voltage_list_online = []
                    min_cell_temperature_list = []
                    min_cell_temperature_list_online = []
                    max_cell_temperature_list = []
                    max_cell_temperature_list_online = []
                    avg_cell_temperature_list = []
                    avg_cell_temperature_list_online = []
                    no_of_cycles_list = []
                    no_of_cycles_list_online = []
                    module_count_list = []
                    module_count_list_online = []

                    warning_overtemp = 0
                    warning_undertemp = 0
                    warning_overcharge = 0
                    warning_overdischarge = 0
                    warning_overvolt = 0
                    warning_undervolt = 0
                    warning_soc_low = 0
                    warning_soc_high = 0
                    warning_temp_diff = 0
                    warning_current_diff = 0
                    warning_other = 0
                    warning_config = 0
                    warning_insulation = 0

                    alarm_overtemp = 0
                    alarm_undertemp = 0
                    alarm_overcharge = 0
                    alarm_overdischarge = 0
                    alarm_overvolt = 0
                    alarm_undervolt = 0
                    alarm_soc_low = 0
                    alarm_soc_high = 0
                    alarm_temp_diff = 0  
                    alarm_other = 0
                    alarm_config = 0

                    fault_overtemp = 0
                    fault_undertemp = 0
                    fault_temp_diff = 0
                    fault_overvolt = 0
                    fault_undervolt = 0
                    fault_overcharge = 0
                    fault_overdischarge = 0
                    fault_rbms_comms = 0
                    fault_insulation = 0

                    for rack in range(self.battery_rack_quantity):
                        start_address = string_model_start_address + (rack * 48)  # Adjust the start address for each rack
                        rr = self.tcp_client.read_holding_registers(start_address, 48, unit=1)
                        if rr.isError():
                            log_message(kore_logger, f"Error reading lithium-ion string model registers for rack {rack}: {rr}", log_level='error')
                            self.tcp_timeout += 1
                        elif len(rr.registers) != 48:
                            log_message(kore_logger, f"Invalid lithium-ion string model data length for rack {rack}: {rr}", log_level='error')
                            self.tcp_timeout += 1
                        else:
                            self.tcp_timeout = 0

                            data = rr.registers

                            # The register needs to be 27 not 26 like it shows on the Kore Modbus map??? (Took me a while to figure this out)
                            contactor_value = data[27]

                            # Check if CONTACTOR_0 (Positive Contactor) is closed - if so take the readings from that rack
                            if contactor_value & (1 << 0):  
                                self.battery_bank_cont_states |= (1 << rack)
                                self.battery_rack_online_quantity += 1
                                soc_list_online.append(self.decode_u16(data, 8) * (10 ** SoC_SF))
                                soh_list_online.append(self.decode_u16(data, 12) * (10 ** SoH_SF))
                                bus_voltage_list_online.append(self.decode_u16(data, 14) * (10 ** Vol_SF))
                                bus_current_list_online.append(self.decode_s16(data, 13) * (10 ** Amp_SF))
                                min_cell_voltage_list_online.append(self.decode_u16(data, 17) * (10 ** CellV_SF))
                                max_cell_voltage_list_online.append(self.decode_u16(data, 15) * (10 ** CellV_SF))
                                avg_cell_voltage_list_online.append(self.decode_u16(data, 19) * (10 ** CellV_SF))
                                min_cell_temperature_list_online.append(self.decode_s16(data, 22) * (10 ** ModTmp_SF))
                                max_cell_temperature_list_online.append(self.decode_s16(data, 20) * (10 ** ModTmp_SF))
                                avg_cell_temperature_list_online.append(self.decode_s16(data, 24) * (10 ** ModTmp_SF))
                                no_of_cycles_list_online.append(round(self.decode_u16(data, 15)))
                                module_count_list_online.append(self.decode_u16(data, 3))

                            # Always append data to lists, whether online or offline
                            contactor_value_list.append(contactor_value)
                            soc_list.append(self.decode_u16(data, 8) * (10 ** SoC_SF))
                            soh_list.append(self.decode_u16(data, 12) * (10 ** SoH_SF))
                            bus_voltage_list.append(self.decode_u16(data, 14) * (10 ** Vol_SF))
                            bus_current_list.append(self.decode_s16(data, 13) * (10 ** Amp_SF))
                            min_cell_voltage_list.append(self.decode_u16(data, 17) * (10 ** CellV_SF))
                            max_cell_voltage_list.append(self.decode_u16(data, 15) * (10 ** CellV_SF))
                            avg_cell_voltage_list.append(self.decode_u16(data, 19) * (10 ** CellV_SF))
                            min_cell_temperature_list.append(self.decode_s16(data, 22) * (10 ** ModTmp_SF))
                            max_cell_temperature_list.append(self.decode_s16(data, 20) * (10 ** ModTmp_SF))
                            avg_cell_temperature_list.append(self.decode_s16(data, 24) * (10 ** ModTmp_SF))
                            no_of_cycles_list.append(round(self.decode_u16(data, 15)))
                            module_count_list.append(self.decode_u16(data, 3))

                            try:

                                evt1 = (data[28] << 16) | data[29]
                                self.acc_evt1 |= evt1

                                evtvnd1 = (data[32] << 16) | data[33]
                                self.acc_evtvnd1 |= evtvnd1

                                evtvnd2 = (data[34] << 16) | data[35]
                                self.acc_evtvnd2 |= evtvnd2


                            # Update Warnings / Alarms / Faults

                                # WARNINGS

                                if evt1 > 0 or evtvnd1 > 0:


                                    if self.warning_timeout_counter[rack] < self.alert_timeout:
                                        self.warning_timeout_counter[rack] += 1
                                    else:
                                        if evt1 & (1 << 2):
                                            self.update_warnings(Warnings.OVERTEMP.value, True)
                                            warning_overtemp += 1
                                        if evt1 & (1 << 4):
                                            self.update_warnings(Warnings.UNDERTEMP.value, True)
                                            warning_undertemp += 1
                                        if evt1 & (1 << 6):
                                            self.update_warnings(Warnings.OVERCHARGE.value, True)
                                            warning_overcharge += 1
                                        if evt1 & (1 << 8):
                                            self.update_warnings(Warnings.OVERDISCHARGE.value, True)
                                            warning_overdischarge += 1
                                        if evt1 & (1 << 10):
                                            self.update_warnings(Warnings.OVERVOLT.value, True)
                                            warning_overvolt += 1
                                        if evt1 & (1 << 12):
                                            self.update_warnings(Warnings.UNDERVOLT.value, True)
                                            warning_undervolt += 1
                                        if evt1 & (1 << 14):
                                            self.update_warnings(Warnings.SOC_LOW.value, True)
                                            warning_soc_low += 1
                                        if evt1 & (1 << 16):
                                            self.update_warnings(Warnings.SOC_HIGH.value, True)
                                            warning_soc_high += 1
                                            # Kore Modbus register change temp diff warning from evt1 & (1 << 19) to evtvnd1 & (1 << 13) 
                                        if evtvnd1 & (1 << 13):
                                            self.update_warnings(Warnings.TEMP_DIFF.value, True)
                                            warning_temp_diff += 1
                                        if evt1 & (1 << 24):
                                            self.update_warnings(Warnings.CURRENT_DIFF.value, True)
                                            warning_current_diff += 1
                                        if evt1 & (1 << 26):
                                            self.update_warnings(Warnings.OTHER.value, True)
                                            warning_other += 1
                                        if evt1 & (1 << 29):
                                            self.update_warnings(Warnings.CONFIG.value, True)
                                            warning_config += 1
                                        if evtvnd1 & (1 << 23):
                                            self.update_warnings(Warnings.INSULATION.value, True)
                                            warning_insulation += 1
                                else:
                                    self.warning_timeout_counter[rack] = 0

                                # ALARMS

                                if evt1 > 0 or evtvnd1 > 0:  # Timeout if any active alarms
                                    if self.alarm_timeout_counter[rack] < self.alert_timeout:
                                        self.alarm_timeout_counter[rack] += 1
                                    else:
                                        if evt1 & (1 << 1):
                                            self.update_alarms(Alarms.OVERTEMP.value, True)
                                            alarm_overtemp += 1
                                        if evt1 & (1 << 3):
                                            self.update_alarms(Alarms.UNDERTEMP.value, True)
                                            alarm_undertemp += 1
                                        if evt1 & (1 << 5):
                                            self.update_alarms(Alarms.OVERCHARGE.value, True)
                                            alarm_overcharge += 1
                                        if evt1 & (1 << 7):
                                            self.update_alarms(Alarms.OVERDISCHARGE.value, True)
                                            alarm_overdischarge += 1
                                        if evt1 & (1 << 9):
                                            self.update_alarms(Alarms.OVERVOLT.value, True)
                                            alarm_overvolt += 1
                                        if evt1 & (1 << 11):
                                            self.update_alarms(Alarms.UNDERVOLT.value, True)
                                            alarm_undervolt += 1
                                        if evt1 & (1 << 13):
                                            self.update_alarms(Alarms.SOC_LOW.value, True)
                                            alarm_soc_low += 1
                                        if evt1 & (1 << 15):
                                            self.update_alarms(Alarms.SOC_HIGH.value, True)
                                            alarm_soc_high += 1
                                        # Kore Modbus register change temp diff alarm from evt1 & (1 << 18) to evtvnd1 & (1 << 29)
                                        if evtvnd1 & (1 << 29):
                                            self.update_alarms(Alarms.TEMP_DIFF.value, True)
                                            alarm_temp_diff += 1
                                        if evt1 & (1 << 25):
                                            self.update_alarms(Alarms.OTHER.value, True)
                                            alarm_other += 1
                                        if evt1 & (1 << 28):
                                            self.update_alarms(Alarms.CONFIG.value, True)
                                            alarm_config += 1
                                else:
                                    self.alarm_timeout_counter[rack] = 0

                                # FAULTS

                                if evtvnd2 > 0:
                                    if self.fault_timeout_counter[rack] < self.alert_timeout:
                                        self.fault_timeout_counter[rack] += 1

                                        if self.fault_timeout_counter[rack] >= self.alert_timeout:
                                            self.update_faults(Faults.BANK_OFFLINE.value, True)
                                    else:
                                        # Kore Modbus register change form lots of faults from evtvnd1 to evtvnd2.
                                        # if evtvnd1 & (1 << 9):
                                        if evtvnd2 & (1 << 11):
                                            self.update_faults(Faults.OVERTEMP.value, True)
                                            fault_overtemp += 1
                                        # if evtvnd1 & (1 << 10):
                                        if evtvnd2 & (1 << 12):
                                            self.update_faults(Faults.UNDERTEMP.value, True)
                                            fault_undertemp += 1
                                        # if evtvnd1 & (1 << 11):
                                        if evtvnd2 & (1 << 13):
                                            self.update_faults(Faults.TEMP_DIFF.value, True)
                                            fault_temp_diff += 1
                                        # if evtvnd1 & (1 << 12):
                                        if evtvnd2 & (1 << 0):
                                            self.update_faults(Faults.OVERVOLT.value, True)
                                            fault_overvolt += 1
                                        # if evtvnd1 & (1 << 13):
                                        if evtvnd2 & (1 << 1):
                                            self.update_faults(Faults.UNDERVOLT.value, True)
                                            fault_undervolt += 1
                                        # if evtvnd1 & (1 << 14):
                                        if evtvnd2 & (1 << 2):
                                            self.update_faults(Faults.OVERCHARGE.value, True)
                                            fault_overcharge += 1
                                        # if evtvnd1 & (1 << 15):
                                        if evtvnd2 & (1 << 3):
                                            self.update_faults(Faults.OVERDISCHARGE.value, True)
                                            fault_overdischarge += 1
                                        # if evtvnd1 & (1 << 17):
                                        if evtvnd2 & (1 << 25):
                                            self.update_faults(Faults.RBMS_COMMS.value, True)
                                            fault_rbms_comms += 1
                                        # if evtvnd1 & (1 << 24):
                                        if evtvnd2 & (1 << 17):
                                            self.update_faults(Faults.INSULATION.value, True)
                                            fault_insulation += 1
                                else:
                                    self.fault_timeout_counter[rack] = 0


                            except Exception as e:
                                log_message(kore_logger,f"Error in reading multiple rack data in Kore Battery: {e}", log_level='error')
                                self.tcp_timeout += 1
                                return


                    if warning_overtemp == 0:
                        self.update_warnings(Warnings.OVERTEMP.value, False)
                    if warning_undertemp == 0:
                        self.update_warnings(Warnings.UNDERTEMP.value, False)
                    if warning_overcharge == 0:
                        self.update_warnings(Warnings.OVERCHARGE.value, False)
                    if warning_overdischarge == 0:
                        self.update_warnings(Warnings.OVERDISCHARGE.value, False)
                    if warning_overvolt == 0:
                        self.update_warnings(Warnings.OVERVOLT.value, False)
                    if warning_undervolt == 0:
                        self.update_warnings(Warnings.UNDERVOLT.value, False)
                    if warning_soc_low == 0:
                        self.update_warnings(Warnings.SOC_LOW.value, False)
                    if warning_soc_high == 0:
                        self.update_warnings(Warnings.SOC_HIGH.value, False)
                    if warning_temp_diff == 0:
                        self.update_warnings(Warnings.TEMP_DIFF.value, False)
                    if warning_current_diff == 0:
                        self.update_warnings(Warnings.CURRENT_DIFF.value, False)
                    if warning_other == 0:
                        self.update_warnings(Warnings.OTHER.value, False)
                    if warning_config == 0:
                        self.update_warnings(Warnings.CONFIG.value, False)
                    if warning_insulation == 0:
                        self.update_warnings(Warnings.INSULATION.value, False)

                    if alarm_overtemp == 0:
                        self.update_alarms(Alarms.OVERTEMP.value, False)
                    if alarm_undertemp == 0:
                        self.update_alarms(Alarms.UNDERTEMP.value, False)
                    if alarm_overcharge == 0:
                        self.update_alarms(Alarms.OVERCHARGE.value, False)
                    if alarm_overdischarge == 0:
                        self.update_alarms(Alarms.OVERDISCHARGE.value, False)
                    if alarm_overvolt == 0:
                        self.update_alarms(Alarms.OVERVOLT.value, False)
                    if alarm_undervolt == 0:
                        self.update_alarms(Alarms.UNDERVOLT.value, False)
                    if alarm_soc_low == 0:
                        self.update_alarms(Alarms.SOC_LOW.value, False)
                    if alarm_soc_high == 0:
                        self.update_alarms(Alarms.SOC_HIGH.value, False)
                    if alarm_temp_diff == 0:
                        self.update_alarms(Alarms.TEMP_DIFF.value, False)
                    if alarm_other == 0:
                        self.update_alarms(Alarms.OTHER.value, False)
                    if alarm_config == 0:
                        self.update_alarms(Alarms.CONFIG.value, False)

                    if fault_overtemp == 0:
                        self.update_faults(Faults.OVERTEMP.value, False)
                    if fault_undertemp == 0:
                        self.update_faults(Faults.UNDERTEMP.value, False)
                    if fault_temp_diff == 0:
                        self.update_faults(Faults.TEMP_DIFF.value, False)
                    if fault_overvolt == 0:
                        self.update_faults(Faults.OVERVOLT.value, False)
                    if fault_undervolt == 0:
                        self.update_faults(Faults.UNDERVOLT.value, False)
                    if fault_overcharge == 0:
                        self.update_faults(Faults.OVERCHARGE.value, False)
                    if fault_overdischarge == 0:
                        self.update_faults(Faults.OVERDISCHARGE.value, False)
                    if fault_rbms_comms == 0:
                        self.update_faults(Faults.RBMS_COMMS.value, False)
                    if fault_insulation == 0:
                        self.update_faults(Faults.INSULATION.value, False)
                    

                    # Calculate values for the battery bank

                    try:
                        # Determine which lists to use
                        if sum(contactor_value_list) <= 0:
                            soc = soc_list
                            soh = soh_list
                            bus_voltage = bus_voltage_list
                            bus_current = bus_current_list
                            min_cell_voltage = min_cell_voltage_list
                            max_cell_voltage = max_cell_voltage_list
                            avg_cell_voltage = avg_cell_voltage_list
                            min_cell_temperature = min_cell_temperature_list
                            max_cell_temperature = max_cell_temperature_list
                            avg_cell_temperature = avg_cell_temperature_list
                            no_of_cycles = no_of_cycles_list
                            module_count = module_count_list
                        else:
                            soc = soc_list_online
                            soh = soh_list_online
                            bus_voltage = bus_voltage_list_online
                            bus_current = bus_current_list_online
                            min_cell_voltage = min_cell_voltage_list_online
                            max_cell_voltage = max_cell_voltage_list_online
                            avg_cell_voltage = avg_cell_voltage_list_online
                            min_cell_temperature = min_cell_temperature_list_online
                            max_cell_temperature = max_cell_temperature_list_online
                            avg_cell_temperature = avg_cell_temperature_list_online
                            no_of_cycles = no_of_cycles_list_online
                            module_count = module_count_list_online

                        # Calculate battery bank parameters
                        self.battery_bank_soc = sum(soc) / len(soc) if soc else 0
                        self.battery_bank_soh = sum(soh) / len(soh) if soh else 0
                        self.battery_bank_bus_voltage = sum(bus_voltage) / len(bus_voltage) if bus_voltage else 0
                        self.battery_bank_bus_current = sum(bus_current) if bus_current else 0
                        self.battery_bank_min_cell_voltage = min(min_cell_voltage) if min_cell_voltage else 0
                        self.battery_bank_max_cell_voltage = max(max_cell_voltage) if max_cell_voltage else 0
                        self.battery_bank_avg_cell_voltage = sum(avg_cell_voltage) / len(avg_cell_voltage) if avg_cell_voltage else 0
                        self.battery_bank_min_cell_temperature = min(min_cell_temperature) if min_cell_temperature else 0
                        self.battery_bank_max_cell_temperature = max(max_cell_temperature) if max_cell_temperature else 0
                        self.battery_bank_avg_cell_temperature = sum(avg_cell_temperature) / len(avg_cell_temperature) if avg_cell_temperature else 0
                        # Number of cycles register doesn't seem to give the correct info. I will calulate this from our data below.
                        # self.battery_bank_cycles = round(sum(no_of_cycles) / len(no_of_cycles)) if no_of_cycles else 0
                        self.battery_bank_module_count = sum(module_count) if module_count else 0

                        #############################
                        #  Cycle Count Calculation  #
                        #############################
                        # Calculate the number of cycles as Kore 'NCyc' register doesn't seem to give the correct info
                        # The number of cycles is calculated by the number of times the battery has been charged and discharged
                        
                        # Cycle count calculations
                        if self.battery_last_soc == 0:                                                  # Will likely be zero when we start
                            self.battery_last_soc = self.battery_bank_soc
                            print("Resetting previous soc")
                        else:
                            soc_diff = float(self.battery_last_soc) - float(self.battery_bank_soc)
                            rack_max_kw = (self.battery_bank_module_count * 6.5)

                            if soc_diff >= 0.5:     # This needs to represent the smallest shift that Kore will report
                                self.battery_export_kw += float(0.005 * rack_max_kw)                    # Module count * 6kW per module
                                self.battery_last_soc = self.battery_bank_soc

                                if "battery_export_kw" not in self.dbData:
                                    self.dbData["battery_export_kw"] = str(0)

                                #self.dbData["battery_export_kw"] = str(float(self.dbData["battery_export_kw"]) + self.battery_export_kw)
                                #self.save_to_db()

                            elif soc_diff <= -0.5:
                                self.battery_import_kw += float(0.005 * rack_max_kw)                    # Module count * 6kW per module
                                self.battery_last_soc = self.battery_bank_soc

                                if "battery_import_kw" not in self.dbData:
                                    self.dbData["battery_import_kw"] = str(0)

                                #self.dbData["battery_import_kw"] = str(float(self.dbData["battery_import_kw"]) + self.battery_import_kw)
                                #self.save_to_db()


                            if self.battery_import_kw >= rack_max_kw and self.battery_export_kw >= rack_max_kw:
                                self.battery_import_kw -= rack_max_kw
                                self.battery_export_kw -= rack_max_kw

                                # Grab the value stored in the database, add to it and resave.
                                if "battery_bank_cycles" not in self.dbData:
                                    self.dbData["battery_bank_cycles"] = str(0)

                                # And there's our cycle count since commissioning.
                                self.dbData["battery_bank_cycles"] = str(int(self.dbData["battery_bank_cycles"]) + 1)
                                self.save_to_db()

                            if "battery_bank_cycles" in self.dbData:
                                self.battery_bank_cycles = self.dbData["battery_bank_cycles"]

                        # Calculate bus power
                        self.battery_bank_bus_power = (self.battery_bank_bus_voltage * self.battery_bank_bus_current) / 1000

                        # Determine charge state
                        if self.battery_bank_bus_power == 0:
                            self.battery_bank_charge_state = 0  # "Idle"
                        elif self.battery_bank_bus_power < 0:
                            self.battery_bank_charge_state = 1  # "Charge"
                        else:
                            self.battery_bank_charge_state = 2  # "Discharge"

                        # Calculate drift between racks
                        if self.battery_rack_online_quantity > 0 and soc:
                            self.battery_rack_max_soc = max(soc)
                            self.battery_rack_min_soc = min(soc)
                            soc_drift = self.battery_rack_max_soc - self.battery_rack_min_soc
                            if soc_drift > 5:
                                self.battery_soc_drift = True
                                self.update_warnings(Warnings.SOC_DRIFT.value, True)
                            else:
                                self.battery_soc_drift = False
                                self.update_warnings(Warnings.SOC_DRIFT.value, False)
                        else:
                            self.battery_soc_drift = False
                            self.update_warnings(Warnings.SOC_DRIFT.value, False)

                    except Exception as e:
                        log_message(kore_logger, f"Error in calculating battery bank data: {e}", log_level='error')
                        self.tcp_timeout += 1
                        return

                except Exception as e:
                    log_message(kore_logger, f"Error in reading lithium-ion string model data: {e}", log_level='error')
                    self.tcp_timeout += 1

                # Main Battery Bank Data
                try:
                    rr = self.tcp_client.read_holding_registers(40102, 62, unit=1)
                    if rr.isError():
                        log_message(kore_logger, f"Error reading battery bank registers: {rr}", log_level='error')
                        self.tcp_timeout += 1
                        return
                    elif len(rr.registers) != 62:
                        log_message(kore_logger, f"Invalid battery bank data length: {rr}", log_level='error')
                        self.tcp_timeout += 1
                        return
                    else:
                        self.tcp_timeout = 0

                        # Scale Factors
                        Vol_SF = -1 # Voltage scale factor
                        CellV_SF = -3  # Cell voltage scale factor
                        AMax_SF = 0  # Max current scale factor

                        # Heartbeat
                        self.battery_bank_heartbeat = rr.registers[18]

                        # Max Charge and Discharge Current
                        self.battery_bank_max_chg_current = rr.registers[45] * (10 ** AMax_SF)
                        self.battery_bank_max_dchg_current = rr.registers[46] * (10 ** AMax_SF)

                        # Calculate Max Charge and Discharge Power
                        self.battery_bank_max_chg_power = (self.battery_bank_max_chg_current * self.battery_bank_bus_voltage)
                        self.battery_bank_max_dchg_power = (self.battery_bank_max_dchg_current * self.battery_bank_bus_voltage)

                        # Cycle Count
                        # TODO: Need to figure this out as the register doesn't work
                        # self.battery_bank_cycles = rr.registers[15]  # Which register 14 or 15 or both?

                        self.battery_bank_max_capacity = self.battery_bank_module_count * 6.5
                        if self.battery_rack_quantity == 0:
                            self.battery_bank_online_capacity = 0
                        else:
                            self.battery_bank_online_capacity = (self.battery_rack_online_quantity/self.battery_rack_quantity) * (6.5 * self.battery_bank_module_count)

                        # Update the HMI Home page Graphics with the DC Bus parameters
                        self.sV = self.battery_bank_bus_voltage
                        self.sA = self.battery_bank_bus_current

                        self.battery_bank_rmtctrl = rr.registers[17]

                        # # Warnings, Alarms, and Faults
                        # self.evt1 = (rr.registers[26] << 16) | rr.registers[27]
                        # self.evtvnd1 = (rr.registers[30] << 16) | rr.registers[31]

                        # if self.warning != 0:
                        #     self.update_warnings(Warnings.OTHER.value, True)
                        # if self.alarm != 0:
                        #     self.update_alarms(Alarms.OTHER.value, True)
                        # if self.fault != 0:
                        #     self.update_faults(Faults.OTHER.value, True)

                except ValueError as e:
                    log_message(kore_logger, f"Error in reading data from Kore Battery: {e}", log_level='error')
                    self.tcp_timeout += 1

                try:
                    if self.battery_bank_soc <= 5:
                        self.icon = "/static/images/Battery0.png"
                    elif self.battery_bank_soc <= 20:
                        self.icon = "/static/images/Battery20.png"
                    elif self.battery_bank_soc <= 40:
                        self.icon = "/static/images/Battery40.png"
                    elif self.battery_bank_soc <= 60:
                        self.icon = "/static/images/Battery60.png"
                    elif self.battery_bank_soc <= 80:
                        self.icon = "/static/images/Battery80.png"
                    elif self.battery_bank_soc <= 100:
                        self.icon = "/static/images/Battery100.png"
                except Exception as e:
                    log_message(kore_logger,f"Error in updating battery icon in Kore Battery: {e}", log_level='error')
                    return
                
                # The Controller is asking us to close our contactors
                if self.enabled:
                    if not self.enabled_echo:                                                               # Task not yet complete

                        try:
                            # Check we're in Remote Mode
                            if self.battery_bank_rmtctrl == 0: # 0 = remote, 1 = local

                                self.update_alarms(Alarms.LOCAL_MODE.value, False)

                                # Clear any faults by writing to the register (40122 is the 'AlmRst' register)
                                self.tcp_client.write_register(40122, 1, unit=1)

                                max_attempts = 5
                                attempts = 0

                                while attempts < max_attempts:
                                    response = self.tcp_client.read_holding_registers(40122, 1, unit=1)
                                    if response.isError():
                                        print("Error reading AlmRst register")
                                        log_message(kore_logger, f"Error reading AlmRst register: {response}", log_level='error')
                                        break  # Exit loop on error

                                    alarm_reset_status = response.registers[0]
                                    print(f"Alarm reset status: {alarm_reset_status}")
                                    if alarm_reset_status == 0:
                                        print("Alarm cleared")
                                        self.enabled_echo = True 
                                        break  # Exit loop if AlmRst is cleared
                                    else:
                                        print("Waiting for alarm clear on start")
                                        time.sleep(0.1)  # Adding a small delay

                                    attempts += 1  # Increment the counter

                                if attempts == max_attempts:
                                    attempts = 0
                                    print(f"Max attempts reached: {max_attempts}... continuing without clearing alarm")

                                # Close the contactors
                                response = self.tcp_client.read_holding_registers(40152, 1, unit=1) # 'SetOp' in sunspec
                                if response.isError():
                                    log_message(kore_logger, f"Error reading contactor status: {response}", log_level='error')
                                    self.tcp_timeout += 1
                                    return
                                if response.registers[0] != 1:
                                    self.tcp_client.write_register(40152, 1, unit=1)

                                # Check if the contactors are closed
                                check = self.tcp_client.read_holding_registers(40124, 1, unit=1)  # SunSpec for 'State'
                                if check.isError():
                                    log_message(kore_logger, f"Error reading contactor status: {check}", log_level='error')
                                    self.tcp_timeout += 1
                                    return
                                if check.registers[0] == 3:
                                    self.process_timeout_counter = 0
                                    self.process_retries = 0

                                    if self.battery_rack_online_quantity >= self.battery_min_enabled_racks:
                                        self.enabled_echo = True  # Brought online by the enabled_echo monitoring code.

                                else:
                                    self.process_timeout_counter += 1
                                    if self.process_timeout_counter >= self.process_timeout:
                                        self.process_timeout_counter = 0
                                        self.process_retries += 1
                                        print("Retries: " + str(self.process_retries))

                                        if self.process_retries >= 5:
                                            self.process_retries = 0
                                            print("FAULT: Could not enable battery bank")
                                            self.enabled = False

                                    self.enabled_echo = False
                            else:
                                self.update_alarms(Alarms.LOCAL_MODE.value, True)  # Code cannot bring racks back online in Local mode

                        except Exception as e:
                            log_message(kore_logger, f"Error in enabling battery bank in Kore Battery: {e}", log_level='error')
                            self.tcp_timeout += 1
                            return
                else:
                    try:
                        response = self.tcp_client.read_holding_registers(40152, 1, unit=1)  # address for 'SetOp'
                        if response.isError():
                            log_message(kore_logger, f"Error reading contactor status: {response}", log_level='error')
                            self.tcp_timeout += 1
                            return
                        if response.registers[0] != 2:
                            self.tcp_client.write_register(40152, 2, unit=1)
                        self.tcp_timeout = 0
                        self.enabled_echo = False  # Now we can tell Control to open the E-Stop Interlocks

                    except Exception as e:
                        log_message(kore_logger, f"Error in disabling battery bank in Kore Battery: {e}", log_level='error')
                        self.tcp_timeout += 1
                        return

                # Our contactors are closed, monitor the battery racks and rectify faults when possible
                if self.enabled_echo:
                
                    # Check Remote / Local state
                    try:
                        if self.battery_bank_rmtctrl == 1:
                            self.update_alarms(Alarms.LOCAL_MODE.value, True)                               # Code cannot bring racks back online in Local mode
                        else:
                            self.update_alarms(Alarms.LOCAL_MODE.value, False)
                    except Exception as e:
                        log_message(kore_logger,f"Error in checking remote/local mode in Kore Battery: {e}", log_level='error')
                        self.tcp_timeout += 1
                        return
                    
                    # Check if any racks are offline
                    if self.battery_bank_online_capacity == 0:
                        self.update_faults(Faults.BANK_OFFLINE.value, True)
                    elif 0 < self.battery_bank_online_capacity < self.battery_bank_max_capacity:
                        self.update_faults(Faults.BANK_OFFLINE.value, False)
                        self.update_alarms(Alarms.RACK_OFFLINE.value, True)
                    else:
                        self.update_faults(Faults.BANK_OFFLINE.value, False)
                        self.update_alarms(Alarms.RACK_OFFLINE.value, False)

                    # Command the battery racks to recalibrate
                    # write 86 to 40073 and
                    # write 23 to 40072 to set-up the calibration process
                    try:
                        # for development purposes. Wait for 30 loops to start recalibration
                        #################
                        # self.development_counter += 1

                        # # Reset counter and allow recalibration every 200 loops
                        # if self.development_counter >= 100:
                        #     print("Development counter reset")
                        #     self.development_counter = 0
                        #################

                        # If the SoC drift is detected, begin recalibration process and see it through
                        if self.battery_soc_drift:
                            if self.recalibration_counter == 0:
                                log_message(kore_logger, f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} SoC drift detected - recalibration in progress", log_level='info')
                                print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - SoC drift detected - recalibration in progress")
                            self.recalibration_in_progress = True

                        # If the SoC drift is fine and the recalibration progress is complete, reset the counter and rack id
                        if not self.battery_soc_drift and not self.recalibration_in_progress:
                            self.recalibration_counter = 0

                        # Begin recalibration process
                        if self.recalibration_in_progress:
                            if self.recalibration_counter == 1:
                                self.tcp_client.write_registers(40072, [23, 86], unit=1)

                                log_message(kore_logger, f"Recalibrating racks", log_level='info')
                                print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - Recalibrating racks")
                        
                            # if self.recalibration_counter == 25:
                            #     self.tcp_client.write_register(40073, 0, unit=1)
                            # if self.recalibration_counter == 26:
                            #     self.tcp_client.write_register(40072, 0, unit=1)

                            # Increment the loop counter
                            self.recalibration_counter += 1

                        # If we've gone through a recalibration and counted further to give it about an hour break (4000 loops) before trying again.
                        if self.recalibration_counter >= 4000:
                            self.recalibration_in_progress = False
                            self.recalibration_counter = 0

                    except Exception as e:
                        log_message(kore_logger, f"Error in recalibrating battery racks: {e}", log_level='error')
                        self.recalibration_in_progress = False
                        self.recalibration_counter = 0

                    # Reconnect any open racks
                    # if self.battery_bank_cont_states < self.battery_rack_quantity: 
                    # Count the number of enabled contactors in the bitfield
                    enabled_contactors = bin(self.battery_bank_cont_states).count('1')

                    # Check if the number of enabled contactors is less than the number of racks
                    if enabled_contactors < self.battery_rack_quantity:
                        # Try to reconnect any open racks
                        # Close the contactors
                        # Added a counter so it is not trying every loop and backs off a bit.
                        # This lets it wait in case it has reconnected but hasn't had chance to update.
                        # But also so it doesn't bombard the register every second if the voltages are too far out.
                        # Will only reconnect the rack if average voltage of online racks are within 5V of the offline bank voltage
                        self.reconnect_counter += 1
                        if self.reconnect_counter >= 5:
                            self.reconnect_counter = 0
                            response = self.tcp_client.read_holding_registers(40152, 1, unit=1) # 'SetOp' in sunspec
                            if response.isError():
                                log_message(kore_logger, f"Error reconnecting reading contactor status: {response}", log_level='error')
                                self.tcp_timeout += 1
                                return
                            if response.registers[0] != 1:
                                self.tcp_client.write_register(40152, 1, unit=1)

                    #########################################################
                    # TODO: Check this bit of code as it is giving a fault

                    # # Check if any racks are offline
                    # try:
                    #     if self.battery_bank_online_capacity == 0:
                    #         self.update_faults(Faults.BANK_OFFLINE.value, True)
                    #     elif 0 < self.battery_bank_online_capacity < self.battery_bank_max_capacity:
                    #         self.update_faults(Faults.BANK_OFFLINE.value, False)
                    #         self.update_alarms(Alarms.RACK_OFFLINE.value, True)
                    #     else:
                    #         self.update_faults(Faults.BANK_OFFLINE.value, False)
                    #         self.update_alarms(Alarms.RACK_OFFLINE.value, False)

                    # except Exception as e:
                    #     log_message(kore_logger,f"Error in checking offline racks in Kore Battery: {e}", log_level='error')
                    #     return
                    #########################################################

                    ###################
                    # WARNINGS ALARMS FAULTS NOW IN THE string model data loop
                    ###################

                    # # Command the battery racks to recalibrate
                    # try:
                    #     if "battery_cal_interval" in self.dbData:
                    #         if self.cal_interval < int(self.dbData["battery_cal_interval"]):
                    #             self.cal_interval += 1
                    #         else:
                    #             self.cal_interval = 0
                    #             # TODO: Send the message
                    # except Exception as e:
                    #     log_message(kore_logger,f"Error in recalibrating battery racks in Kore Battery: {e}", log_level='error')
                    #     return

                    # Clear Warnings
                    try:
                        if self.acc_evt1 == 0 and self.acc_evtvnd1 == 0:
                            self.update_warnings(Warnings.OVERTEMP.value, False)
                            self.update_warnings(Warnings.UNDERTEMP.value, False)
                            self.update_warnings(Warnings.OVERCHARGE.value, False)
                            self.update_warnings(Warnings.OVERDISCHARGE.value, False)
                            self.update_warnings(Warnings.OVERVOLT.value, False)
                            self.update_warnings(Warnings.UNDERVOLT.value, False)
                            self.update_warnings(Warnings.SOC_LOW.value, False)
                            self.update_warnings(Warnings.SOC_HIGH.value, False)
                            self.update_warnings(Warnings.TEMP_DIFF.value, False)
                            self.update_warnings(Warnings.CURRENT_DIFF.value, False)
                            self.update_warnings(Warnings.OTHER.value, False)
                            self.update_warnings(Warnings.CONFIG.value, False)
                            self.update_warnings(Warnings.INSULATION.value, False)

                        # Clear Alarms
                        if self.acc_evt1 == 0 and self.acc_evtvnd1 == 0:
                            self.update_alarms(Alarms.OVERTEMP.value, False)
                            self.update_alarms(Alarms.UNDERTEMP.value, False)
                            self.update_alarms(Alarms.OVERCHARGE.value, False)
                            self.update_alarms(Alarms.OVERDISCHARGE.value, False)
                            self.update_alarms(Alarms.OVERVOLT.value, False)
                            self.update_alarms(Alarms.UNDERVOLT.value, False)
                            self.update_alarms(Alarms.SOC_LOW.value, False)
                            self.update_alarms(Alarms.SOC_HIGH.value, False)
                            self.update_alarms(Alarms.TEMP_DIFF.value, False)
                            self.update_alarms(Alarms.OTHER.value, False)
                            self.update_alarms(Alarms.CONFIG.value, False)

                        # Clear Faults
                        if self.acc_evtvnd2 == 0:
                            self.update_faults(Faults.OVERTEMP.value, False)
                            self.update_faults(Faults.UNDERTEMP.value, False)
                            self.update_faults(Faults.TEMP_DIFF.value, False)
                            self.update_faults(Faults.OVERVOLT.value, False)
                            self.update_faults(Faults.UNDERVOLT.value, False)
                            self.update_faults(Faults.OVERCHARGE.value, False)
                            self.update_faults(Faults.OVERDISCHARGE.value, False)
                            self.update_faults(Faults.RBMS_COMMS.value, False)
                            self.update_faults(Faults.INSULATION.value, False)
                    except Exception as e:
                        log_message(kore_logger,f"Error in clearing warnings/alarms/faults in Kore Battery: {e}", log_level='error')
                        return
                else:                                                                                   # Deliberately offline so some alerts shouldn't exist
                    self.update_alarms(Alarms.RACK_OFFLINE.value, False)
                    self.update_faults(Faults.BANK_OFFLINE.value, False)

            try:
                if len(self.actions) == 0:
                    self.actions.append(0)  # Dummy

                # Modify self.outputs
                self.outputs[2][0] = self.battery_bank_heartbeat
                self.outputs[2][1] = self.battery_bank_charge_state
                self.outputs[2][2] = self.battery_bank_soc
                self.outputs[2][3] = self.battery_bank_soh
                self.outputs[2][4] = self.battery_bank_bus_voltage
                self.outputs[2][5] = self.battery_bank_bus_current
                self.outputs[2][6] = self.battery_bank_bus_power
                self.outputs[2][7] = self.battery_bank_cont_states
                self.outputs[2][8] = self.battery_bank_max_chg_power
                self.outputs[2][9] = self.battery_bank_max_dchg_power
                self.outputs[2][10] = self.battery_bank_min_cell_voltage
                self.outputs[2][11] = self.battery_bank_max_cell_voltage
                self.outputs[2][12] = self.battery_bank_avg_cell_voltage
                self.outputs[2][13] = self.battery_bank_min_cell_temperature
                self.outputs[2][14] = self.battery_bank_max_cell_temperature
                self.outputs[2][15] = self.battery_bank_avg_cell_temperature
                self.outputs[2][16] = self.battery_bank_cycles
                self.outputs[2][17] = self.battery_bank_max_capacity
                self.outputs[2][18] = self.battery_bank_online_capacity
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
                log_message(kore_logger,f"Error in updating self.outputs for Kore Battery: {e}", log_level='error')
                return

        except Exception as e:
            log_message(kore_logger, f"Error in process in kore module: {e}", log_level='error')
            self.tcp_timeout += 1
            return

        # Loop time calculations for efficiency checking and monitoring performance
        try:
            loop_time = time.time() - s
        except Exception as e:
            log_message(kore_logger, f"Error in kore loop time calculations: {e}", log_level='error')

    def decode_str(self, registers, start, length, byteorder=Endian.Big, wordorder=Endian.Big):
        decoder = BinaryPayloadDecoder.fromRegisters(
            registers[start:start+length], byteorder=byteorder, wordorder=wordorder)
        return decoder.decode_string(length * 2).decode('utf-8').strip('\x00')

    def decode_s16(self, registers, index):
        try:
            value = registers[index]
            if value > 32767:
                value -= 65536
            return value
        except Exception as e:
            log_message(kore_logger, f"Error decoding s16 at index {index}: {e}", log_level='error')


    def decode_u16(self, registers, index):
        try:
            value = registers[index]
            if 0 <= value <= 65535:
                return value
            else:
                log_message(kore_logger, f"Register value out of range: {value} at index: {index}", log_level='error')
        except Exception as e:
            log_message(kore_logger, f"Error decoding u16 at index {index}: {e}", log_level='error')

    def decode_u32(self, registers, offset):
        try:
            high_word = registers[offset]
            low_word = registers[offset + 1]
            value = (high_word << 16) | low_word
            if 0 <= value <= 0xFFFFFFFF:
                return value
            else:
                log_message(kore_logger, f"Register value out of range: {value} at offset: {offset}", log_level='error')
        except Exception as e:
            log_message(kore_logger, f"Error decoding u32 at offset {offset}: {e}", log_level='error')

    def set_inputs(self, inputs):
        try:
            for module in inputs:
                if module[0] == self.uid:
                    if len(module) > 0:
                        if self.override is False:
                            self.inputs = module

                            self.enabled = self.inputs[1]
            return [SET_INPUTS_ACK]
        except Exception as e:
            log_message(kore_logger,f"Error in setting inputs for Kore Battery {e}", log_level='error')

    def get_outputs(self):
        try:
            self.outputs[1] = self.enabled_echo

            # outputs = copy.deepcopy(self.outputs)
            # #print(outputs)
            # return outputs
            return [GET_OUTPUTS_ACK, self.outputs]
        except Exception as e:
            log_message(kore_logger,f"Error in getting outputs for Kore Battery: {e}", log_level='error')

    def set_page(self, page, form):  # Respond to GET/POST requests
        try:
            data = dict()
            if page == self.website + "_(" + str(self.uid) + ")":  # It was a page request for a numbered device (POST)

                # Save all control changes to database
                isButton = True

                for control in form:
                    if "battery_output_override" in form:
                        self.actions.insert(0, Actions.CTRL_OVERRIDE_CHANGE.value)
                        self.override = not self.override
                    elif "battery_min_online_rack_qty" in form:                                 # This prevents the inverters enabling unless n number of racks are online.
                        self.battery_min_enabled_racks = int(form[control])
                        print("Minimum racks set to " + str(self.battery_min_enabled_racks))
                    #     self.actions.insert(0, Actions.CONTACTOR_TOGGLE_CHANGE.value)
                    #     self.contactor_state = not self.contactor_state
                    #
                    # elif "battery_alarm_reset" in page[1].form:
                    #     self.actions.insert(0, Actions.ALARM_RESET_CHANGE.value)

                    # else:

                    isButton = False

                    if "battery_ipaddr_local" == control:
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
                mod_data["battery_name"] = self.name
                mod_data["battery_man"] = self.manufacturer
                mod_data["battery_fwver"] = self.version
                mod_data["battery_serno"] = self.serial
                mod_data["battery_constate"] = str(self.con_state).capitalize()
                mod_data["battery_override"] = self.override
                mod_data["battery_enablestate"] = self.enabled
                mod_data["battery_bank"] = self.outputs[2]

                mod_data.update(self.dbData)  # I'm appending the dict here so we don't save unnecessary
                
                return [SET_PAGE_ACK, mod_data]            # data to the database.

            else:

                return [SET_PAGE_ACK, ('OK', 200)]  # Return the data to be jsonified
        except Exception as e:
            log_message(kore_logger,f"Error in setting page for Kore Battery: {e}", log_level='error')
            return 'Error', 500

    def get_page(self):
        try:
            routes = [self.website + "_(" + str(self.uid) + ")/data"]  # JSON Data: FlexMod_test_v100_(<uid>)/data/
            page = [self.website + "_(" + str(self.uid) + ")", routes]  # HTML content: FlexMod_test_v100_(<uid>).html
            return [GET_PAGE_ACK, page]
        except Exception as e:
            log_message(kore_logger,f"Error in getting page for Kore Battery: {e}", log_level='error')
            return None
        
    def set_state_text(self, state):  # Update the State text on the "Home" HMI Icon
        try:
            self.state = state.value
        except Exception as e:
            log_message(kore_logger,f"Error in setting state text for Kore Battery: {e}", log_level='error')
        
    def update_warnings(self, warning, active):
        try:
            if active:
                self.warnings |= (1 << warning)
            else:
                self.warnings &= ~(1 << warning)
        except Exception as e:
            log_message(kore_logger,f"Error in updating warnings for Kore Battery: {e}", log_level='error')

    def update_alarms(self, alarm, active):
        try:
            if active:
                self.alarms |= (1 << alarm)
            else:
                self.alarms &= ~(1 << alarm)
        except Exception as e:
            log_message(kore_logger,f"Error in updating alarms for Kore Battery: {e}", log_level='error')

    def update_faults(self, fault, active):
        try:
            if active:
                self.faults |= (1 << fault)
            else:
                self.faults &= ~(1 << fault)
        except Exception as e:
            log_message(kore_logger,f"Error in updating faults for Kore Battery: {e}", log_level='error')

    def get_info(self):
        try:
            return [GET_INFO_ACK, self.uid, self.module_type, self.icon, self.name, self.manufacturer, self.model, self.options, self.version, self.website]
        except Exception as e:
            log_message(kore_logger,f"Error in getting info for Kore Battery: {e}", log_level='error')

    def get_status(self):
        try:
            return [GET_STATUS_ACK, self.uid, self.heartbeat, self.priV, self.priA, self.secV, self.secA, self.terV, self.terA, self.state, self.warnings, self.alarms, self.faults, self.actions, self.icon]
        except Exception as e:
            log_message(kore_logger,f"Error in getting status for Kore Battery: {e}", log_level='error')

    def save_to_db(self):
        # This shouldn't be triggered by buttons!
        try:
            db.save_to_db(__name__ + "_(" + str(self.uid) + ")", self.dbData)
        except Exception as e:
            print("Error saving Kore Battery to database: " + str(e))
            log_message(kore_logger,f"Unable to save record in Kore Batter, may already exist: {e}", log_level='error')  # Todo find error code from tinydb

    def set_state_text(self, state):  # Update the State text on the "Home" HMI Icon
        try:
            self.state = state.value
        except Exception as e:
            log_message(kore_logger,f"Error in setting state text for Kore Battery: {e}", log_level='error')

    def get_rack_soc(self, vRack, tRack):
        try:
            # Find the temperature range in which the cell temperature sits.
            for temp in range(len(self.soc_t["T"])-1):
                # Find the initial column in which the rack temperature lies
                if (tRack == self.soc_t["T"][temp]) or ((tRack > self.soc_t["T"][temp]) and (tRack < self.soc_t["T"][temp+1])):
                    break

            # And the offsets for the given voltage
            for soc in self.soc_t:
                if soc != "T":
                    if soc < 100:
                        if vRack == self.soc_t[soc][temp]:
                            break

                        elif (vRack > self.soc_t[soc][temp]) and (vRack < self.soc_t[soc+5][temp]):       # Temperatures are defined in 5 degree rows
                            # Calculate the voltage for each percent of the span in which our voltage lies
                            vDiv = (self.soc_t[soc+5][temp] - self.soc_t[soc][temp]) / 100
                            #print("vRack = " + str(vRack))
                            #print("vDiv = " + str(vDiv))

                            # Then the percentage comes from subtracting the lower value from the cell voltage and dividing it by the divisor
                            if vDiv > 0:
                                # This gives us a percentage of the rack voltage within the lower and upper cell limits of the raw soc range
                                soc_offset = (vRack - self.soc_t[soc][temp]) / vDiv

                                # Subdivide the SoC for the range in which our voltage sits (it's always going to be 0.05, unless someone adds more resolution to the soc rows)
                                socDiv = (soc+5 - soc) / 100

                                # Then calculate the actual soc using the lower value as a baseline
                                soc = (socDiv * soc_offset) + soc

                            break

                    elif vRack >= self.soc_t[soc][temp]:                                                # Catch the remainder, usually if the cell voltage exceeds the 100% threshold
                        break

            # Then interpolate somewhere in the middle

            # And return it
            return soc
        except Exception as e:
            log_message(kore_logger,f"Error in getting rack SoC for Kore Battery: {e}", log_level='error')

    def kill(self):
        try:
            self.stop.set()
        except Exception as e:
            log_message(kore_logger,f"Error in killing Kore Battery: {e}", log_level='error')

def driver(queue, uid):
    
    #print("MODULE STARTED with ID " + str(uid))
    
    # Create and init our Module class
    flex_module = Module(uid, queue)
    
    # Start the interval timer for periodic device polling
    #thread = Interval(Event(), flex_module.process, 2)  # This needs looking at. Kore is dropping its connection occasionally and we must be buffer overloading with many requests.
    #thread.start()
    
    # Process piped requests
    while True:
        rx_msg = None
        tx_msg = None
        
        try:
            rx_msg = queue[1].get()
            
            if isinstance(rx_msg, list):
                if rx_msg[0] == SYNC: 
                    if sys.getrefcount(flex_module) <= 2:
                        Thread(target=flex_module.process).start()
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
            print("EPC Inverter: " + str(e))
            log_message(kore_logger, f"Error in Kore Battery driver: {e}", log_level='error')
        try:
            if tx_msg is not None:
                queue[0].put(tx_msg, block=True)
                
        except Exception as e:
            print("EPC Inverter: " + str(e))
            log_message(kore_logger, f"Error in Kore Battery driver 2: {e}", log_level='error')
            

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
    # Reserved
    TEMP_DIFF = 10
    # Reserved
    OTHER = 12
    CONFIG = 13
    LOCAL_MODE = 14
    RACK_OFFLINE = 15                                                                               # At least 1 battery rack is offline.


class Faults(Enum):
    NONE = 0
    OVERTEMP = 1
    UNDERTEMP = 2
    OVERCHARGE = 3
    OVERDISCHARGE = 4
    OVERVOLT = 5
    UNDERVOLT = 6
    OTHER = 7
    # Reserved
    # Reserved
    TEMP_DIFF = 10
    LOSS_OF_COMMS = 11
    RBMS_COMMS = 12
    CONFIG = 13
    INSULATION = 14
    BANK_OFFLINE = 15


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


if __name__ == '__main__':  # The module must be able to run solo for testing purposes
    pass
