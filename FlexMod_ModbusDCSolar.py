# FlexMod_ModbusDCSolar.py

# Description
# Modbus Interface - DC Meter Module
# A customer wishes to send their DC solar data to the client module of our SCADA interface.
# So this is a generic DC Solar which reads our own client module data instead of a physical peripheral and presents it to the system as if it were real.
# in a very similar way to our BACnet modules.

# Versions
# 3.5.24.10.16 - SC - Known good starting point, uses thread.is_alive to prevent module stalling.

from pymodbus.client.sync import ModbusTcpClient as mb_tcp_client
from threading import Thread, Event
from FlexMod import BaseModule, ModTypes
from FlexDB import FlexTinyDB
from enum import Enum
import copy
from datetime import datetime

# New imports for the big endian values function
from pymodbus.constants import Endian
from pymodbus.payload import BinaryPayloadDecoder


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
LATITUDE = '51.61443'  # Replace with the latitude of BESS location
LONGITUDE = '-0.03950'  # Replace with the longitude of BESS location
# This is the current information (18/08/2023) That I can get from the Open Weather API for the types of icons used.
WEATHER_ICON_MAP = {
    '01d': '/static/images/DCsolar_clear.png', # Clear sky day
    '01n': '/static/images/DCsolar_clear_night.png', # Clear sky night
    '02d': '/static/images/DCsolar_few.png', # Few clouds day
    '02n': '/static/images/DCsolar_few_night.png', # Few clouds night
    '03d': '/static/images/DCsolar_scattered.png', # Scattered clouds day
    '03n': '/static/images/DCsolar_scattered_night.png', # Scattered clouds night
    '04d': '/static/images/DCsolar_broken.png', # Broken clouds day
    '04n': '/static/images/DCsolar_broken_night.png', # Broken clouds night
    '09d': '/static/images/DCsolar_showers.png', # Shower rain day
    '09n': '/static/images/DCsolar_showers_night.png', # Shower rain night
    '10d': '/static/images/DCsolar_rain.png', # Rain day
    '10n': '/static/images/DCsolar_rain_night.png', # Rain night
    '11d': '/static/images/DCsolar_thunder.png', # Thunderstorm day
    '11n': '/static/images/DCsolar_thunder_night.png', # Thunderstorm night
    '13d': '/static/images/DCsolar_snow.png', # Snow day
    '13n': '/static/images/DCsolar_snow_night.png', # Snow night
    '50d': '/static/images/DCsolar_fog.png', # Fog / Mist day
    '50n': '/static/images/DCsolar_fog_night.png', # Fog / Mist night
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
        # print(f"API Response Status: {response.status_code}")
        data = response.json()
        return data['weather'][0]['icon']
    except Exception as e:
        # If there is an error, return 0.
        print(f"Error while fetching weather icon: {e}")
        return '0'

###############################################################
# Function to get icon path
def get_icon_path(icon_code):
    path = WEATHER_ICON_MAP.get(icon_code, '/static/images/DCsolar_broken_night.png')
    # print(f"Derived icon path for {icon_code}: {path}")
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
        self.author = "Sophie Coates / Gareth Reece "
        self.uid = uid  # A unique identifier passed from the HMI via a mangled URL
        self.icon = "/static/images/DCsolar.png"
        self.name = "Modbus Bridged DC Solar"
        self.module_type = ModTypes.DC_SOLAR.value
        self.module_version = "3.5.24.10.16"                                                        # Last update on "Flex version | Year | Month | Day"
        self.manufacturer = "Multi Source Power"
        self.model = ""
        self.options = ""
        self.version = "-"
        self.serial = "-"
        self.website = "/Mod/FlexMod_ModbusDCSolar"  # This is the template name itself
        self.number_devices = 1

        # Weather inits
        self.weather_update_counter = 0
        self.first_run = True  # Flag to check if it's the first run of the module

        # Run state
        self.con_state = False
        self.state = "Idle"
        self.set_state_text(State.IDLE)

        # Non-Volatile Data (Loaded from DB)
        self.dbData = db.fetch_from_db(__name__ + "_(" + str(self.uid) + ")")
        if self.dbData is None:
            self.dbData = dict()
            self.dbData["dc_solar_ipaddr_local"] = "0.0.0.0"
            
        # Volatile Data
        self.tcp_client = None
        self.tcp_timeout = 0
        self.enabled = False
        self.enabled_echo = False
        self.override = False
        self.inputs = [self.uid, False, [False * 14]]  # All reserved
        self.outputs = [self.uid, self.enabled, [0] * 25]  
        self.heartbeat = 0
        self.heartbeat_echo = 0

        # Device Volatiles
        self.dc_solar_quantity = 1
        self.dc_solar_heartbeat = 0

        # some inits
        self.frequency = 0
        self.total_system_current = 0
        self.total_pv_current = 0
        self.total_line_voltage = 0
        self.ac_solar_avg_phase_voltage = 0
        self.total_system_power = 0
        self.total_pv_power = 0
        self.pv_energy = 0
        self.pv_energyHI = 0
        self.pv_energyLO = 0

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
        
        ### New self variables for total cumulative energy ###
        self.total_cumulative_energy = 0
        self.total_daily_energy = 0
        self.db_time_counter = 0

        print("Starting " + self.name + " with UID " + str(self.uid) + " on version " + str(self.module_version))

    def update_weather_icon(self):
        current_weather_icon = get_weather_icon()
        # print(f"Updated weather icon: {current_weather_icon}")
        # If you want to update the module's icon with the fetched weather icon, you can do:
        self.icon = get_icon_path(current_weather_icon)
        # print(self.icon)

    def twoCtoint(self, twoC):
        # calculate int from two's compliment input
        return twoC if not twoC & 0x8000 else -(0xFFFF - twoC + 1)

    def process(self):

        if self.enabled:  # The controller can start and stop the process

            self.heartbeat += 1
            if self.heartbeat >= 0xFFFF:
                self.heartbeat = 0

            if self.tcp_timeout >= 5:
                self.tcp_timeout = 0
                self.tcp_client = None

            if self.tcp_client is None:

                self.con_state = False
                if "dc_solar_ipaddr_local" in self.dbData:

                    if self.dbData["dc_solar_ipaddr_local"] != "0.0.0.0":

                        try:
                            self.tcp_client = mb_tcp_client(self.dbData["dc_solar_ipaddr_local"], port=502)     # Retrieve the data we need from our own SCADA interface, using 127.0.0.1

                            if self.tcp_client.connect() is True:
                                self.update_faults(Faults.LOSS_OF_COMMS.value, False)  # Clear Fault
                                self.set_state_text(State.CONNECTED)
                                self.con_state = True
                            else:
                                self.update_faults(Faults.LOSS_OF_COMMS.value, True)  # Raise Fault
                                self.set_state_text(State.CONNECTING)
                                self.tcp_client = None

                        except Exception as e:
                            print("Modbus DC Solar: " + str(e))
                            self.update_faults(Faults.LOSS_OF_COMMS.value, True)
                            self.set_state_text(State.CONNECTING)
                    else:
                        self.update_faults(Faults.LOSS_OF_COMMS.value, True)  # Raise Fault
                        self.set_state_text(State.CONFIG)
                else:
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

                # DC Solar Total Power
                try:
                    rr = self.tcp_client.read_holding_registers(6015, 4, unit=1)

                    if rr.isError():
                        self.tcp_timeout += 1
                        return
                    else:
                        self.tcp_timeout = 0
                        sec_voltage = self.twos_comp_to_int(rr.registers[1]) / 10
                        sec_current = self.twos_comp_to_int(rr.registers[3]) / 10
                        self.total_pv_power = (sec_voltage * sec_current) / 1000

                except Exception as e:
                    print("Modbus DC Solar: " + str(e))
                    self.tcp_timeout += 1
                    return

            # There is little to do beyond accepting requests
            if self.enabled:
                self.enabled_echo = True
            else:
                self.enabled_echo = False

            if len(self.actions) == 0:
                self.actions.append(0)  # Dummy

            self.dc_solar_heartbeat = self.heartbeat

            # Modify self.outputs
            self.outputs[2][0] = self.dc_solar_heartbeat
            self.outputs[2][1] = 0
            self.outputs[2][2] = 0
            self.outputs[2][3] = 0
            self.outputs[2][4] = 0
            self.outputs[2][5] = 0
            self.outputs[2][6] = 0
            self.outputs[2][7] = self.total_pv_power
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
            for control in form:
                if "dc_solar_override" in form:
                    self.actions.insert(0, Actions.CTRL_OVERRIDE_CHANGE.value)
                    self.override = not self.override
                else:
                    isButton = False

                    if "dc_solar_ipaddr_local" == control:
                        self.actions.insert(0, Actions.IP_ADDRESS_CHANGE.value)

                    self.dbData[control] = form[control]  # Input or drop-down form states are stored here

            if isButton is False:  # Button press states are to be acted upon only, not stored
                self.save_to_db()

            # Let's just record the last 10 user interations for now
            if len(self.actions) >= 10:
                self.actions.pop()

        elif page == (self.website + "_(" + str(self.uid) + ")/data"):  # It was a json data fetch quest (POST)

            mod_data = dict()

            # Controller Information
            mod_data["dc_solar_name"] = self.name
            mod_data["dc_solar_man"] = self.manufacturer
            mod_data["dc_solar_fwver"] = self.version
            mod_data["dc_solar_serno"] = self.serial
            mod_data["dc_solar_constate"] = str(self.con_state).capitalize()
            mod_data["dc_solar_data"] = self.outputs[2]
            mod_data["dc_solar_enablestate"] = self.enabled
            mod_data["dc_solar_number_devices"] = self.number_devices
            mod_data["dc_solar_weather_icon"] = self.icon
            mod_data["dc_solar_daily_energy"] = self.total_daily_energy 
            
            mod_data.update(self.dbData)  # I'm appending the dict here so we don't save unnecessary
            
            return [SET_PAGE_ACK, mod_data]  # data to the database.

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