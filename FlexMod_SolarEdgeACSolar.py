# SolarEdge - AC Solar Module
# We only have one DIO GPIO Controlling the On/Off state of the inverter, so there's not much to do here yet.
# Might be a good test of the hotswap code when/if they connect it.
from pymodbus.client.sync import ModbusTcpClient as mb_tcp_client
from threading import Thread, Event
from FlexMod import BaseModule, ModTypes
from FlexDB import FlexTinyDB
from enum import Enum
import copy
from datetime import datetime
import time
# New imports for the big endian values function
from pymodbus.constants import Endian
from pymodbus.payload import BinaryPayloadDecoder
###############################################################
import logging
# Better logging fuctions
def setup_logging():
    logger = logging.getLogger('ac_solar_logger')
    logger.propagate = False
    handler = logging.FileHandler('ac_solar.log')
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    return logger

ac_solar_logger = setup_logging()

# Log a message
def log_to_file(message, log_level='info'):
    log_funcs = {
        'info': ac_solar_logger.info,
        'warning': ac_solar_logger.warning,
        'error': ac_solar_logger.error
    }
    timestamp = datetime.now().strftime("%d-%m-%Y %H:%M:%S")
    log_func = log_funcs.get(log_level, ac_solar_logger.info)
    log_func(f"{message} - {timestamp}")
###############################################################
# New imports for requesting Weather data every set interval
import requests
###############################################################
# Set the OpenWeather API key - need to put in a config file for production.
OPEN_WEATHER_API_KEY = '457960dbfe978ebcee8e76c2cd023772'
# Set the base URL for the OpenWeather API
BASE_WEATHER_URL = 'http://api.openweathermap.org/data/2.5/weather'
# TODO: Get the latitude and longitude of the BESS automatically

# You need to put the new values in for the location of the BESS.
LATITUDE = '53.786259'  # Replace with the latitude of BESS location
LONGITUDE = '-2.914046'  # Replace with the longitude of BESS location
# This is the current information (18/08/2023) That I can get from the Open Weather API for the types of icons used.
WEATHER_ICON_MAP = {
    '01d': '/static/images/ACsolar_clear.png', # Clear sky day
    '01n': '/static/images/ACsolar_clear_night.png', # Clear sky night
    '02d': '/static/images/ACsolar_few.png', # Few clouds day
    '02n': '/static/images/ACsolar_few_night.png', # Few clouds night
    '03d': '/static/images/ACsolar_scattered.png', # Scattered clouds day
    '03n': '/static/images/ACsolar_scattered_night.png', # Scattered clouds night
    '04d': '/static/images/ACsolar_broken.png', # Broken clouds day
    '04n': '/static/images/ACsolar_broken_night.png', # Broken clouds night
    '09d': '/static/images/ACsolar_showers.png', # Shower rain day
    '09n': '/static/images/ACsolar_showers_night.png', # Shower rain night
    '10d': '/static/images/ACsolar_rain.png', # Rain day
    '10n': '/static/images/ACsolar_rain_night.png', # Rain night
    '11d': '/static/images/ACsolar_thunder.png', # Thunderstorm day
    '11n': '/static/images/ACsolar_thunder_night.png', # Thunderstorm night
    '13d': '/static/images/ACsolar_snow.png', # Snow day
    '13n': '/static/images/ACsolar_snow_night.png', # Snow night
    '50d': '/static/images/ACsolar_fog.png', # Fog / Mist day
    '50n': '/static/images/ACsolar_fog_night.png', # Fog / Mist night
}
###############################################################
# Function to get the current weather icon from the OpenWeather API
def get_weather_icon():
    try:
        response = requests.get(
            BASE_WEATHER_URL,
            params={
                'lat': LATITUDE,
                'lon': LONGITUDE,
                'appid': OPEN_WEATHER_API_KEY
            }
        )
        data = response.json()
        return data['weather'][0]['icon']
    except Exception as e:
        # If there is another type of error, print it and return '0'
        log_to_file(f"Error while fetching weather icon: {e} - 002", "error")
        log_to_file(f"Exception occurred: {str(e)} - 003", "error")
        return '0'

###############################################################
# Function to get icon path
def get_icon_path(icon_code):
    path = WEATHER_ICON_MAP.get(icon_code, '/static/images/ACsolar.png')
    return path
###############################################################


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


class Interval(Thread):
    def __init__(self, event, process, interval):
        Thread.__init__(self)
        self.stopped = event
        self.target = process
        self.interval = interval

    def run(self):
        while not self.stopped.wait(self.interval):
            self.target()

class Module():
    def __init__(self, uid, queue):
        super(Module, self).__init__()

        # General Module Data
        self.author = "Gareth Reece"
        self.uid = uid  # A unique identifier passed from the HMI via a mangled URL
        self.icon = "/static/images/ACsolar.png"
        self.name = "AC Solar"
        self.type = ModTypes.AC_SOLAR.value
        self.manufacturer = "SolarEdge"
        self.model = ""
        self.options = ""
        self.version = "-"
        self.fw_ver = "-"
        self.serial = "-"
        self.website = "/Mod/FlexMod_SolarEdgeACSolar"  # This is the template name itself
        self.number_devices = 0
        self.weather_update_counter = 0
        self.first_run = True  # Flag to check if it's the first run of the module
        self.process_counter = 0  # Counter to check if the process is running
        self.ac_solar_state_text = "Unknown State"

        # Run state
        self.con_state = False
        self.state = "Idle"
        self.set_state_text(State.IDLE)

        # Non-Volatile Data (Loaded from DB)
        self.dbData = db.fetch_from_db(__name__ + "_(" + str(self.uid) + ")")
        if self.dbData is None:
            self.dbData = dict()
            self.dbData["ac_solar_ipaddr_local"] = "0.0.0.0"

        # Volatile Data
        self.tcp_client = None
        self.tcp_timeout = 0
        self.enabled = False
        self.enabled_echo = False
        self.override = False
        self.inputs = [self.uid, False, [False * 14]]  # All reserved
        self.outputs = [self.uid, self.enabled, [0] * 25]

        # Start the registers for the first device these are the variables from the SolarEdge Modbus Map document
        # Define the encoding types for each register
        self.base_registers = {
            "ACSolarState": {"register": 40107, "type": "uint16"},
            "DCVoltage": {"register": 40098, "type": "uint16"},
            "DCVoltageSF": {"register": 40099, "type": "int16"},
            "DCCurrent": {"register": 40096, "type": "uint16"},
            "DCCurrentSF": {"register": 40097, "type": "int16"},
            "DCBusPower": {"register": 40100, "type": "int16"},
            "DCBusPowerSF": {"register": 40101, "type": "int16"},
            "Temperature": {"register": 40103, "type": "int16"},
            "TemperatureSF": {"register": 40106, "type": "int16"},
            "ACFrequency": {"register": 40085, "type": "uint16"},
            "ACFrequencySF": {"register": 40086, "type": "int16"},
            "ACTotalCurrent": {"register": 40071, "type": "uint16"},
            "ACTotalCurrentSF": {"register": 40075, "type": "int16"},
            "ACLineVoltageAB": {"register": 40076, "type": "uint16"},
            "ACLineVoltageBC": {"register": 40077, "type": "uint16"},
            "ACLineVoltageCA": {"register": 40078, "type": "uint16"},
            "ACLineVoltageSF": {"register": 40082, "type": "int16"},
            "ACPower": {"register": 40083, "type": "int16"},
            "ACPowerSF": {"register": 40084, "type": "int16"},
            "ActiveEnergyLife": {"register": 40093, "type": "acc32"},
            "ActiveEnergyLifeSF": {"register": 40095, "type": "uint16"},
        }

        # Device Volatiles
        # self.ac_solar_quantity = 1
        self.ac_solar_heartbeat = 0

        # Events
        self.warnings = 0
        self.alarms = 0
        self.faults = 0
        self.actions = []

        # HMI data, from which power is derived.
        self.pV = 0  # Primary units of AC Voltage and Current
        self.pA = 0
        self.sV = 0  # Secondary, for DC units
        self.sA = 0
        self.tV = 0  # Tertiary, if a device has a third port, like a PD Hydra
        self.tA = 0

        # Start interval timer
        self.stop = Event()
        self.interval = 1  # Interval timeout in Seconds
        self.thread = Interval(self.stop, self.__process, self.interval)
        self.thread.start()

        print("Starting " + self.name + " with UID " + str(self.uid))

    def update_weather_icon(self):
        current_weather_icon = get_weather_icon()
        self.icon = get_icon_path(current_weather_icon)
        timestamp = datetime.now().strftime("%d-%m-%Y %H:%M:%S")
        ac_solar_logger.info(f"Current weather icon: {current_weather_icon} - {timestamp}")

    def twoCtoint(self, twoC):
        # calculate int from two's compliment input
        return twoC if not twoC & 0x8000 else -(0xFFFF - twoC + 1)

    def process(self):

        if self.enabled:  # The controller can start and stop the process

            self.heartbeat += 1
            if self.heartbeat >= 0xFFFF:
                self.heartbeat = 0
            self.ac_solar_heartbeat = self.heartbeat

            if self.tcp_timeout >= 5:
                self.tcp_timeout = 0
                self.tcp_client = None

            if self.tcp_client is None:
                log_to_file(f"TCP client is None, attempting to connect. - 100", "warning")

                self.con_state = False
                if "ac_solar_ipaddr_local" in self.dbData:
                    log_to_file(f"IP address is in the database. - 200", "info")
                    if self.dbData["ac_solar_ipaddr_local"] != "0.0.0.0":
                        log_to_file(" IP address is not 0.0.0.0. - 300", "info")
                        try:
                            log_to_file("Inside try block for establishing TCP client. - 400", "info") 
                            self.tcp_client = mb_tcp_client(self.dbData["ac_solar_ipaddr_local"], port=502, timeout=0.5) # added 0.5 second timeout

                            if self.tcp_client.connect() is True:
                                log_to_file("TCP client successfully connected. - 401", "info")
                                self.update_faults(Faults.LOSS_OF_COMMS.value, False)  # Clear Fault
                                self.set_state_text(State.CONNECTED)
                                self.con_state = True
                            else:
                                log_to_file("TCP client failed to connect. - 402", "warning")
                                self.update_faults(Faults.LOSS_OF_COMMS.value, True)  # Raise Fault
                                self.set_state_text(State.CONNECTING)
                                self.tcp_client = None

                        except:
                            log_to_file("Exception occurred while establishing TCP client. - 403", "error")
                            self.update_faults(Faults.LOSS_OF_COMMS.value, True)
                            self.set_state_text(State.CONNECTING)
                    else:
                        log_to_file("IP address is 0.0.0.0. - 301", "warning")
                        self.update_faults(Faults.LOSS_OF_COMMS.value, True)  # Raise Fault
                        self.set_state_text(State.CONFIG)
                else:
                    log_to_file("IP address is not in the database. - 201", "warning")
                    self.update_faults(Faults.LOSS_OF_COMMS.value, True)  # Raise Fault
                    self.set_state_text(State.CONFIG)

            else:

                ########## WEATHER ICON UPDATE ##########
                # If it's the first run, update the weather icon and set the flag to False
                if self.first_run:
                    self.update_weather_icon()
                    self.first_run = False

                self.weather_update_counter += 1
                if self.weather_update_counter >= 1000:  # just over 15 minutes between updates.
                    self.update_weather_icon()
                    self.weather_update_counter = 0  # Reset the counter

                # HMI Header parameters
                try:
                    rr = self.tcp_client.read_holding_registers(0, 78, unit=1, timeout=0.5)
                    if rr.isError():
                        self.tcp_timeout += 1
                        log_to_file(f"Error reading registers from AC Solar device. Operation aborted - 500", "error")
                        return               
                    else:
                        self.tcp_timeout = 0
                        # Fetch and decode relevant device information
                        self.name = BinaryPayloadDecoder.fromRegisters(rr.registers[40020:40043], byteorder=Endian.Big).decode_string(16).decode("utf-8")
                        self.manufacturer = ((BinaryPayloadDecoder.fromRegisters(rr.registers[40004:40019], byteorder=Endian.Big)).decode_string(16)).decode("utf-8")
                        self.fw_ver = ((BinaryPayloadDecoder.fromRegisters(rr.registers[40044:40051], byteorder=Endian.Big)).decode_string(16)).decode("utf-8")
                        self.serial = ((BinaryPayloadDecoder.fromRegisters(rr.registers[40052:40067], byteorder=Endian.Big)).decode_string(16)).decode("utf-8")
                        self.device_address = BinaryPayloadDecoder.fromRegisters([rr.registers[40068]], byteorder=Endian.Big).decode_16bit_uint()
                except ConnectionError as ce:
                    self.tcp_timeout += 1
                    log_to_file(f"Connection Error: {str(ce)} - 501", "error")
                    return
                except Exception as e:  # Catch all other exceptions
                    self.tcp_timeout += 1
                    log_to_file(f"Unexpected error: {str(e)} - 502", "error")
                    return

                # Define function to fetch and decode register based on type
                def fetch_and_decode_register(register, type):
                    try:
                        if type == "uint16" or type == "int16":
                            count = 1
                        elif type == "acc32":
                            count = 2
                        # Fetch the register
                        result = self.tcp_client.read_holding_registers(register, count, unit=1)
                        
                        if result.isError():
                            log_to_file(f"Error reading register {register} - 700", "error")
                            return None
                        
                        # Decode based on type
                        if type == "uint16":
                            return BinaryPayloadDecoder.fromRegisters(result.registers, byteorder=Endian.Big).decode_16bit_uint()
                        elif type == "int16":
                            return BinaryPayloadDecoder.fromRegisters(result.registers, byteorder=Endian.Big).decode_16bit_int()
                        elif type == "acc32":
                            return BinaryPayloadDecoder.fromRegisters(result.registers, byteorder=Endian.Big).decode_32bit_uint()
                    except Exception as e:
                        log_to_file(f"Unexpected error reading register {register}: {str(e)} - 702", "error")
                        return None
                    
                # Collect data for the device
                device_values = {}
                try:
                    for measurement, info in self.base_registers.items():
                        register = info["register"]
                        type = info["type"]
                        value = fetch_and_decode_register(register, type)
                        if value is not None:
                            device_values[measurement] = value
                except Exception as e:
                    log_to_file(f"Error reading AC Solar registers, operation aborted - 800. Exception: {e}", "error")
                    self.tcp_timeout += 1
                    return

                #### ANY CALCULATIONS REQUIRED HERE ####
                expected_length = len(self.base_registers)
                if len(device_values) == expected_length:
                    try:
                        # Can only send integers over self.output... Need to do any SF in JavaScript
                        # DCVoltage = device_values["DCVoltage"] * 10 ** device_values["DCVoltageSF"]
                        # DCCurrent = device_values["DCCurrent"] * 10 ** device_values["DCCurrentSF"]
                        # DCBusPower = device_values["DCBusPower"] * 10 ** device_values["DCBusPowerSF"]
                        # Temperature = device_values["Temperature"] * 10 ** device_values["TemperatureSF"]
                        # ACFrequency = device_values["ACFrequency"] * 10 ** device_values["ACFrequencySF"]
                        # ACTotalCurrent = device_values["ACTotalCurrent"] * 10 ** device_values["ACTotalCurrentSF"]
                        # ACLineVoltageAB = device_values["ACLineVoltageAB"] * 10 ** device_values["ACLineVoltageSF"]
                        # ACLineVoltageBC = device_values["ACLineVoltageBC"] * 10 ** device_values["ACLineVoltageSF"]
                        # ACLineVoltageCA = device_values["ACLineVoltageCA"] * 10 ** device_values["ACLineVoltageSF"]
                        # ACPower = device_values["ACPower"] * 10 ** device_values["ACPowerSF"]
                        # ActiveEnergyLife = device_values["ActiveEnergyLife"] * 10 ** device_values["ActiveEnergyLifeSF"]
                        # If the system is not balanced then Root Mean Square for the AC Phase Voltage to calculate the AC Line Voltage
                        # ACLineVoltage = ((ACLineVoltageAB ** 2 + ACLineVoltageBC ** 2 + ACLineVoltageCA ** 2) / 3) ** 0.5
                        # Otherwise, if the system is balanced use the average of AB, BC and CA to find ACLineVoltage
                        ACLineVoltage = int((device_values["ACLineVoltageAB"] + device_values["ACLineVoltageBC"] + device_values["ACLineVoltageCA"] ) / 3)
                    except:
                        log_to_file(f"Error calculating values, operation aborted - 801", "error")
                        self.tcp_timeout += 1
                        return
                else:
                    log_to_file(f"Error reading AC Solar registers, operation aborted - 802", "error")
                    self.tcp_timeout += 1
                    return

            if len(self.actions) == 0:
                self.actions.append(0)  # Dummy
            
            expected_length = len(self.base_registers)
            if len(device_values) == expected_length:
            
                # Modify self.outputs
                self.outputs[2][0] = self.ac_solar_heartbeat
                self.outputs[2][1] = self.base_registers["ACSolarState"]
                self.outputs[2][2] = 0
                self.outputs[2][3] = 0
                self.outputs[2][4] = 0
                self.outputs[2][5] = int(device_values["DCVoltage"])
                self.outputs[2][6] = int(device_values["DCCurrent"])
                self.outputs[2][7] = int(device_values["DCBusPower"])
                self.outputs[2][8] = int(device_values["Temperature"])
                self.outputs[2][9] = int(device_values["ACFrequency"])
                self.outputs[2][10] = int(device_values["ACTotalCurrent"])
                self.outputs[2][11] = int(ACLineVoltage)
                self.outputs[2][12] = int(device_values["ACPower"])
                self.outputs[2][13] = 0
                self.outputs[2][14] = int((device_values["ActiveEnergyLife"] >> 16) & 0xFFFF)
                self.outputs[2][15] = int(device_values["ActiveEnergyLife"] & 0xFFFF)
                self.outputs[2][16] = 0
                self.outputs[2][17] = 0
                self.outputs[2][18] = 0
                self.outputs[2][19] = 0
                self.outputs[2][20] = self.warnings
                self.outputs[2][21] = self.alarms
                self.outputs[2][22] = self.faults
                self.outputs[2][23] = self.actions[0]
            else:
                self.tcp_timeout += 1
                log_to_file("Error reading AC Solar registers, operation aborted - 900", "error")
                return

            ####### NEW CODE FOR THE AC SOLAR STATE WITH WARNINGS/ALARMS/FAULTS ASSIGNED #######
            # Map of the status values and text and "self determined warning/alarm/fault status" from the SolarEdge AC register
            status_map = {
                1: {'text': "Off", 'warnings': 0, 'alarms': 1, 'faults': 0},
                2: {'text': "Sleeping - auto-shutdown", 'warnings': 0, 'alarms': 1, 'faults': 0},
                3: {'text': "Grid Monitoring/wake-up", 'warnings': 0, 'alarms': 1, 'faults': 0},
                4: {'text': "Inverter ON", 'warnings': 0, 'alarms': 0, 'faults': 0},
                5: {'text': "Throttled", 'warnings': 1, 'alarms': 0, 'faults': 0},
                6: {'text': "Shutting down", 'warnings': 0, 'alarms': 1, 'faults': 0},
                7: {'text': "Fault", 'warnings': 0, 'alarms': 0, 'faults': 1},
                8: {'text': "Maintenance", 'warnings': 1, 'alarms': 0, 'faults': 0},
            }
            try:
                ac_state = self.base_registers["ACSolarState"]["register"]
                
                if ac_state in status_map:
                    map = status_map[ac_state]
                    self.warnings = map['warnings']
                    self.alarms = map['alarms']
                    self.faults = map['faults']
                    self.ac_solar_state_text = map['text']
                else:
                    self.warnings, self.alarms, self.faults = 0, 0, 1
                    self.ac_solar_state_text = "Unknown State"       
            except Exception as e:
                self.tcp_timeout += 1
                log_to_file(f"Unexpected error: {str(e)} - 600", "error")
                return

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
        # for action in page[1].form:
        #     self.actions.append([action, page[1].form[action]])

        if page == self.website + "_(" + str(self.uid) + ")":  # It was a page request for a numbered device (POST)

            # Save all control changes to database
            isButton = True
            for control in page[1].form:
                # No buttons to process yet

                isButton = False
                self.dbData[control] = page[1].form[control]

            if isButton is False:  # Button press states are to be acted upon only, not stored
                self.save_to_db()
            # Let's just record the last 10 user interations for now
            if len(self.actions) >= 10:
                self.actions.pop()
        elif page == (self.website + "_(" + str(self.uid) + ")/data"):  # It was a json data fetch quest (POST)

            ac_solar_data = dict()

            # Controller Information
            ac_solar_data["ac_solar_name"] = self.name
            ac_solar_data["ac_solar_man"] = self.manufacturer
            ac_solar_data["ac_solar_fwver"] = self.fw_ver
            ac_solar_data["ac_solar_serno"] = self.serial
            ac_solar_data["ac_solar_constate"] = str(self.con_state).capitalize()
            ac_solar_data["ac_solar_data"] = self.outputs[2]
            ac_solar_data["ac_solar_enablestate"] = self.enabled
            ac_solar_data["ac_solar_weather_icon"] = self.icon
            ac_solar_data["ac_solar_state_text"] = self.ac_solar_state_text
            ac_solar_data.update(self.dbData)  # I'm appending the dict here so we don't save unnecessary
            return [SET_PAGE_ACK, ac_solar_data]  # data to the database.
        else:
            return [SET_PAGE_ACK, ('OK', 200)]    # Return the data to be jsonified

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
            log_to_file(f"Unable to save record, may already exist? - 1001", "warning")

    def twos_comp_to_int(self, twoC):
        return twoC if not twoC & 0x8000 else -(0xFFFF - twoC + 1)

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
            print("Modbus DC Solar: " + str(e))

        try:
            if tx_msg is not None:
                queue[0].put(tx_msg, block=True)
                
        except Exception as e:
            print("Modbus DC Solar: " + str(e))
            

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
    NONE = 0  # "Warning: Analogue IO - No Warning present"


class Alarms(Enum):
    NONE = 0  # "Alarm: Analogue IO - No Alarm present"


class Faults(Enum):
    NONE = 0  # "Fault: Analogue IO - No Fault present"
    CONFIG = 1  # "Fault: Analogue IO - Configuration"
    LOSS_OF_COMMS = 2  # "Fault: Analogue IO - Loss of Comms"
    IO_TIMEOUT = 3


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