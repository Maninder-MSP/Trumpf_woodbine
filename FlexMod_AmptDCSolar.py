# AMPT - DC Solar Module
import sys
from pymodbus.client.sync import ModbusTcpClient as mb_tcp_client
from threading import Thread, Event
from FlexDB import FlexTinyDB
from enum import Enum
import copy
from datetime import datetime
import time


###############################################################
# New imports for requesting Weather data every set interval
import requests
###############################################################
# New imports for the big endian values function
from pymodbus.constants import Endian
from pymodbus.payload import BinaryPayloadDecoder
###############################################################
# Set the OpenWeather API key - need to put in a config file for production.
OPEN_WEATHER_API_KEY = '457960dbfe978ebcee8e76c2cd023772'
# Set the base URL for the OpenWeather API
BASE_WEATHER_URL = 'http://api.openweathermap.org/data/2.5/weather'
# TODO: Get the latitude and longitude of the BESS automatically
# These Lon and Lat are that of Woodbine near York and have been used as place holders.
# You need to put the new values in for the location of the BESS.
LATITUDE = '54.007449'  # Replace with the latitude of BESS location
LONGITUDE = '-1.098491'  # Replace with the longitude of BESS location
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
            },
            timeout=2  # Adding a timeout of 2 seconds
        )
        data = response.json()
        return data['weather'][0]['icon']
    except requests.exceptions.Timeout:
        # Print a timeout message and return '0'
        #log_to_file(f"Timed out while fetching weather. Please check network connectivity - 001")
        return '0'
    except Exception as e:
        print("DC Solar: " + str(e))
        # If there is another type of error, print it and return '0'
        #log_to_file(f"Error while fetching weather icon: {e} - 002")
        #log_to_file(f"Exception occurred: {str(e)} - 003")
        return '0'
###############################################################
# Function to get icon path
def get_icon_path(icon_code):
    path = WEATHER_ICON_MAP.get(icon_code, '/static/images/DCsolar.png')
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


class Module():
    def __init__(self, uid, queue):
        super(Module, self).__init__()

        # General Module Data
        self.author = "Gareth Reece"
        self.uid = uid  # A unique identifier passed from the HMI via a mangled URL
        self.icon = "/static/images/DCsolar.png"
        self.name = "DC Solar"
        self.module_type = ModTypes.DC_SOLAR.value
        self.manufacturer = "Ampt"
        self.model = ""
        self.options = ""
        self.version = "-"
        self.serial = "-"
        self.website = "/Mod/FlexMod_AmptDCSolar"  # This is the template name itself
        self.number_devices = 0
        self.weather_update_counter = 0
        self.first_run = True  # Flag to check if it's the first run of the module

        self.base_registers = {
            "OutDCA": 89,
            "OutDCV": 90,
            "In1DCV": 92,
            "In2DCV": 94,
            "DCWh": 96,
            "In1DCA": 98,
            "In2DCA": 99,
        }
        self.device_values = None
        self.num_registers = 11 # number of registers to read in base_registers
        self.start_register = 0
        self.header_counter = 0

        # Run state
        self.con_state = False
        self.state = State.IDLE
        self.set_state_text(self.state)

        # Non-Volatile Data (Loaded from DB)
        self.dbData = db.fetch_from_db(__name__ + "_(" + str(self.uid) + ")")
        if self.dbData is None:
            self.dbData = dict()
            self.dbData["dc_solar_ipaddr_local"] = "0.0.0.0"
            self.dbData["dc_solar_total_cumulative_energy"] = 0

        # Volatile Data
        # change self.tcp_client to None if the device is running on a BESS
        # change self.enabled and self.enabled_echo to False if the device is running on a BESS
        self.tcp_client = None # change this to 0 if the device is running locally otherwise leave as None
        self.tcp_timeout = 0
        self.enabled = False # change this to True if the device is running locally
        self.enabled_echo = False # change this to True if the device is running locally
        self.override = False
        self.inputs = [self.uid, False, [False * 14]]  # All reserved
        self.outputs = [self.uid, self.enabled, [0] * 25]  # 7 Analogue inputs and 7 reserved
        self.heartbeat = 0
        self.heartbeat_echo = 0
        
        # Device Volatiles
        self.dc_solar_quantity = 1
        self.dc_solar_heartbeat = 0
        self.loop_times = []
        self.counter = 0

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
        self.total_cumulative_energy = self.dbData["dc_solar_total_cumulative_energy"]
        self.total_daily_energy = 0
        self.db_time_counter = 0

        print("Starting " + self.name + " with UID " + str(self.uid))
    
    def update_weather_icon(self):
        current_weather_icon = get_weather_icon()
        self.icon = get_icon_path(current_weather_icon)
        timestamp = datetime.now().strftime("%d-%m-%Y %H:%M:%S")
        #dc_solar_logger.info(f"Current weather icon: {current_weather_icon} - {timestamp}")

    def process(self):
        global loop_time
        #print("(4) AC Meter Heartbeat: " + str(self.heartbeat) + ", loop time: " + str(loop_time) + " Seconds")
        
        s = time.time()
        
        # start timer to check for process loop efficiency
        start_time = time.time()
        
        ########## WEATHER ICON UPDATE ##########
        # If it's the first run, update the weather icon and set the flag to False
        if self.first_run:
            self.update_weather_icon()
            self.first_run = False

        self.weather_update_counter += 1
        if self.weather_update_counter >= 1000:  # just over 15 minutes between updates.
            self.update_weather_icon()
            self.weather_update_counter = 0  # Reset the counter

        self.heartbeat += 1
        if self.heartbeat >= 0xFFFF:
            self.heartbeat = 0
        self.dc_solar_heartbeat = self.heartbeat

        if self.tcp_timeout >= 5:
            self.tcp_timeout = 0
            self.tcp_client = None
        
        # establish connection to the Modbus device
        ### Updated based on other modules connection to the DC Solar device

        if self.tcp_client is None:
            # log_To_file(f"TCP client is None, attempting to connect. - 100")

            self.con_state = False
            if "dc_solar_ipaddr_local" in self.dbData:
                # log_To_file(f"IP address is in the database. - 200")
                if self.dbData["dc_solar_ipaddr_local"] != "0.0.0.0":
                    # log_To_file("IP address is not 0.0.0.0. - 300")
                    try:
                        # log_To_file("Inside try block for establishing TCP client. - 400") 
                        self.tcp_client = mb_tcp_client(self.dbData["dc_solar_ipaddr_local"], port=502, timeout=0.5) # added 0.5 second timeout

                        if self.tcp_client.connect() is True:
                            # log_To_file("TCP client successfully connected. - 401")
                            self.update_faults(Faults.LOSS_OF_COMMS.value, False)  # Clear Fault
                            self.set_state_text(State.CONNECTED)
                            self.con_state = True
                        else:
                            # log_To_file("TCP client failed to connect. - 402")
                            self.update_faults(Faults.LOSS_OF_COMMS.value, True)  # Raise Fault
                            self.set_state_text(State.CONNECTING)
                            self.tcp_client = None

                    except Exception as e:
                        print("DC Solar: " + str(e))
                        #log_to_file("Exception occurred while establishing TCP client. - 403")
                        self.update_faults(Faults.LOSS_OF_COMMS.value, True)
                        self.set_state_text(State.CONNECTING)
                else:
                    # log_To_file("IP address is 0.0.0.0. - 301")
                    self.update_faults(Faults.LOSS_OF_COMMS.value, True)  # Raise Fault
                    self.set_state_text(State.CONFIG)
            else:
                # log_To_file("IP address is not in the database. - 201")
                self.update_faults(Faults.LOSS_OF_COMMS.value, True)  # Raise Fault
                self.set_state_text(State.CONFIG)

        else:
            self.con_state = True

            # HMI Header parameters
            if self.header_counter == 0:
                try:
                    rr = self.tcp_client.read_holding_registers(0, 78, unit=1, timeout=0.5)
                    if rr.isError():
                        self.tcp_timeout += 1
                        #log_to_file(f"Error reading registers from DC Solar device. Operation aborted - 500")
                        return               
                    else:
                        self.tcp_timeout = 0
                        # Workaround to fetch the full "Communication Unit" name from the DC Solar device.
                        raw_bytes = BinaryPayloadDecoder.fromRegisters(rr.registers[20:38], byteorder=Endian.Big).decode_string(18)
                        decoded_name = raw_bytes.decode("utf-8")
                        self.name = decoded_name

                        self.manufacturer = ((BinaryPayloadDecoder.fromRegisters(rr.registers[4:20], byteorder=Endian.Big)).decode_string(16)).decode("utf-8")
                        self.version = ((BinaryPayloadDecoder.fromRegisters(rr.registers[44:52], byteorder=Endian.Big)).decode_string(16)).decode("utf-8")
                        self.serial = ((BinaryPayloadDecoder.fromRegisters(rr.registers[52:68], byteorder=Endian.Big)).decode_string(16)).decode("utf-8")
                        self.number_devices = BinaryPayloadDecoder.fromRegisters([rr.registers[77]], byteorder=Endian.Big).decode_16bit_uint()
                except ConnectionError as ce:
                    print("DC Solar: " + str(ce))
                    self.tcp_timeout += 1
                    #log_to_file(f"Connection Error: {str(ce)} - 501")
                    return
                except Exception as e:  # Catch all other exceptions
                    print("DC Solar: " + str(e))
                    self.tcp_timeout += 1
                    #log_to_file(f"Unexpected error: {str(e)} - 502")
                    return
            # Only get header parameters every 1000 loops
            self.header_counter += 1
            if self.header_counter >= 1000:
                self.header_counter = 0

            try:
                # Convert self.number_devices to an integer if it is a digit
                if str(self.number_devices).isdigit():
                    self.number_devices = int(self.number_devices)
                # Check if self.number_devices is not a number between 1 and 32
                if not (1 <= self.number_devices <= 32):
                    #log_to_file(f"Error: Invalid number of devices = {self.number_devices}. Operation aborted - 600")
                    self.tcp_timeout += 1
                    return
            except Exception as e:
                print("DC Solar: " + str(e))
                #log_to_file(f"Error reading the number of devices. Operation aborted. Exception: {e} - 601")
                self.tcp_timeout += 1  # Increment the TCP timeout counter
                return

            try:
                num_devices = int(self.number_devices)
                self.device_values = {key: [] for key in self.base_registers.keys()}  # to store values from each device
                
                for base_reg in self.base_registers.values():
                    if not isinstance(base_reg, int):
                        #log_to_file(f"Error: Non-integer base register value {base_reg} - 803")
                        return

                for i in range(num_devices):
                    registers = self.read_device_registers(i, self.base_registers, self.num_registers)
                    if registers is None:
                        #log_to_file(f"Error reading DC Solar registers, operation aborted - 801. Exception: {e}")
                        self.tcp_timeout += 1
                        return
                    
                    for measurement, base_register in self.base_registers.items():
                        try:
                            # Decode register values based on the measurement type.
                            if measurement in ["OutDCA", "In1DCA", "In2DCA"]:
                                value = self.decode_registers(registers, base_register, 'int16')
                            else:
                                value = self.decode_registers(registers, base_register, 'uint32')

                            # Check if a valid value is returned before appending.
                            if value is not None:
                                # If the measurement is "DCWh", do not divide by 1000.
                                if measurement == "DCWh":
                                    self.device_values[measurement].append(value)
                                else:
                                    self.device_values[measurement].append(value / 1000.0)

                        except Exception as e:
                            print("DC Solar: " + str(e))
                            # Log the exception with the register number and measurement.
                            #log_to_file(f"Error decoding register - 802: {base_register} for measurement {measurement}: {e}")
                            pass

                # print all self.device_values with what type of measurement it is and the appended value
                # for measurement, values in self.device_values.items():
                #     print(f"self.device_values: {measurement}: {values}")

            except Exception as e:
                print("DC Solar: " + str(e))
                #log_to_file(f"Error reading DC Solar registers, operation aborted - 800")
                self.tcp_timeout += 1
                return

            try:
            # calculate the averages across the devices
                self.db_time_counter += 1
                averages = {key: sum(values) / len(values) if values else 0 for key, values in self.device_values.items()}
                average_OutDCA = averages["OutDCA"]
                average_OutDCV = averages["OutDCV"]
                average_DCWh = averages["DCWh"]
                # Using avearges and calculate total voltage and current
                self.sV = average_bus_voltage_out = average_OutDCV
                total_current_out = average_OutDCA * num_devices
                # Negated self.sA to make the flow work correctly on the front-end graphics
                self.sA = -total_current_out
                total_power_out = average_bus_voltage_out * total_current_out # in Watts

                daily_energy = int(average_DCWh * num_devices / 1000 * 10)
                # / 1000 to get kWh * 10 due to SF1 (I know I could just / 100 but wanted to show the math reasoning)
                
                if daily_energy > self.total_daily_energy:
                    self.total_cumulative_energy = (daily_energy - self.total_daily_energy) + self.total_cumulative_energy
                    self.total_daily_energy = daily_energy
                    
                # if the time is 12:05am (anywhere between 12:05am and 12:06am) and the time counter is > 1000 loops then save to database.
                if datetime.now().hour == 0 and datetime.now().minute == 5 and self.db_time_counter > 1000:
                    # log_To_file(f"Saving total cumulative energy to database. {self.total_cumulative_energy}")
                    self.dbData["dc_solar_total_cumulative_energy"] = self.total_cumulative_energy
                    self.save_to_db()
                    self.total_daily_energy = 0
                    self.db_time_counter = 0

            except Exception as e:
                print("DC Solar: " + str(e))
                #log_to_file(f"Error calculating values - 901: {e}")
                self.tcp_timeout += 1
                return

            # There is little to do, so just echo when we're operational
            if self.enabled:
                self.enabled_echo = True
            else:
                self.enabled_echo = False

            if len(self.actions) == 0:
                self.actions.append(0)  # Dummy
            # Modify self.outputs
            self.outputs[2][0] = int(self.dc_solar_heartbeat)
            self.outputs[2][1] = 0
            self.outputs[2][2] = 0
            self.outputs[2][3] = 0
            self.outputs[2][4] = 0
            self.outputs[2][5] = average_bus_voltage_out
            self.outputs[2][6] = total_current_out
            self.outputs[2][7] = total_power_out / 1000 # in kW
            self.outputs[2][8] = 0
            self.outputs[2][9] = 0
            self.outputs[2][10] = 0
            self.outputs[2][11] = 0
            self.outputs[2][12] = 0
            self.outputs[2][13] = 0
            self.outputs[2][14] = (self.total_cumulative_energy >> 16) & 0xFFFF
            self.outputs[2][15] = self.total_cumulative_energy & 0xFFFF
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

        # Loop time calculations for efficiency checking and monitoring performance
        end_time = time.time()
        current_loop_time = round(end_time - start_time, 2)
        self.loop_times.append(current_loop_time)
        # print(f"AMPT DC Solar Loop Time: {current_loop_time:.2f}")
        self.counter += 1
        if self.counter == 1000:
            loop_time_average = round(sum(self.loop_times) / len(self.loop_times), 2)
            #log_to_file(f"Average loop over 1000 counts: {loop_time_average:.2f}")
            self.counter = 0
            self.loop_times.clear()

        loop_time = time.time() - s

    def read_device_registers(self, device_index, base_registers, num_registers):
        # Ensure num_registers is an integer
        if not isinstance(num_registers, int):
            #log_to_file(f"Error: num_registers is not an integer - 802")
            return None

        # Calculate the start register
        self.start_register = min(base_registers.values()) + (device_index * 16)
        self.num_registers = 11

        # Ensure start_register is an integer
        if not isinstance(self.start_register, int):
            #log_to_file(f"Error: start_register calculated as non-integer - 804")
            return None

        # Read a block of registers
        try:
            result = self.tcp_client.read_holding_registers(self.start_register, self.num_registers, unit=1, timeout=0.2)
            if result.isError():
                #log_to_file(f"Error reading {device_index} registers for the device (error code 700)")
                return None
            return result.registers
        except Exception as ex:  # Changed variable 'e' to 'ex'
            print("DC Solar: " + str(ex))
            #log_to_file(f"Error reading DC Solar registers, operation aborted - 811")
            return None


    def decode_registers(self, registers, base_register, data_type):
        # Ensure base_register is an integer
        if not isinstance(base_register, int):
            #log_to_file(f"Error: base_register is not an integer - 805")
            return None

        # Ensure registers is a list of integers
        if not all(isinstance(reg, int) for reg in registers):
            #log_to_file(f"Error: registers contain non-integer values - 806")
            return None

        # Calculate the register offset
        offset = base_register - min(self.base_registers.values())

        # Ensure offset is an integer
        if not isinstance(offset, int):
            #log_to_file(f"Error: offset is not an integer - 807")
            return None

        # Decode the register value based on data type
        try:
            if data_type == 'uint32':
                return BinaryPayloadDecoder.fromRegisters(registers[offset:offset+2], byteorder=Endian.Big, wordorder=Endian.Big).decode_32bit_uint()
            elif data_type == 'int16':
                return BinaryPayloadDecoder.fromRegisters(registers[offset:offset+1], byteorder=Endian.Big).decode_16bit_int()
        except Exception as ex:
            print("DC Solar: " + str(ex))
            #log_to_file(f"Error decoding registers - 808. Exception: {ex}")
            return None


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

        if page == self.website + "_(" + str(self.uid) + ")":  # It was a page request for a numbered device (POST)

            # Save all control changes to database
            isButton = True
            for control in form:
                # No buttons to process yet

                isButton = False
                self.dbData[control] = form[control]

            if isButton is False:  # Button press states are to be acted upon only, not stored
                self.save_to_db()
            
            return [SET_PAGE_ACK]

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
            return [SET_PAGE_ACK, ('OK', 200)]   # Return the data to be jsonified

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
        except Exception as e:
            print("DC Solar: " + str(e))
            #log_to_file(f"Unable to save record, may already exist? - 1001")
            pass

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
            print("DC Solar: " + str(e))
          
        try:
            if tx_msg is not None:
                queue[0].put(tx_msg, block=True)
                
        except Exception as e:
            print("DC Solar: " + str(e))
            
 
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
