# Carlo Gavazzi EM210 - AC Meter Module
# This uses a Moxa MB3170 Modbus-TCP to RS485 gateway to communicate with the EM210 AC Meter
# Versions
# 3.5.24.10.16 - SC - Known good starting point, uses thread.is_alive to prevent module stalling.

import sys
from pymodbus.client.sync import ModbusTcpClient as mb_tcp_client
from threading import Thread, Event
from FlexDB import FlexTinyDB
from enum import Enum
import sunspec.core.client as sunspec_client
from pymodbus.client.sync import ModbusTcpClient as modbus_client
from pymodbus.constants import Endian
from pymodbus.payload import BinaryPayloadDecoder
import statistics
import time
import copy
from datetime import datetime

# Database
db = FlexTinyDB()

USE_SUNSPEC = False

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
        self.author = "Gareth Reece"
        self.uid = uid
        self.icon = "/static/images/ACmeter.png"
        self.name = "EM210"
        self.module_type = ModTypes.AC_METER.value
        self.manufacturer = "Carlo Gavazzi"
        self.model = "AV"
        self.options = ""
        self.version = "-"
        self.serial = "-"
        self.website = "/Mod/FlexMod_CVEM210ACMeter"
        self.module_version = "3.5.24.10.16"


        # Run state
        self.con_state = False
        self.state = State.IDLE
        self.set_state_text(self.state)

        # Non-Volatile Data (Loaded from DB)
        self.dbData = db.fetch_from_db(__name__ + "_(" + str(self.uid) + ")")
        if self.dbData is None:
            self.dbData = dict()
            self.dbData["ac_meter_ipaddr_local"] = "0.0.0.0"

        # Volatile Data
        self.tcp_client = None
        self.tcp_timeout = 0
        self.enabled = False
        self.enabled_echo = False
        self.override = False
        self.inputs = [self.uid, False]
        self.outputs = [self.uid, self.enabled, [0]*25]
        self.heartbeat = 0
        self.heartbeat_echo = 0
        
        self.ac_meter_heartbeat = 0
        self.ac_meter_average_voltage = 0
        self.ac_meter_average_line_voltage = 0
        self.ac_meter_average_current = 0
        self.ac_meter_total_current = 0
        self.ac_meter_frequency = 0
        self.ac_meter_power_factor = 0
        self.ac_meter_total_active_power = 0
        self.ac_meter_phase_a_power = 0
        self.ac_meter_phase_b_power = 0
        self.ac_meter_phase_c_power = 0
        self.ac_meter_import_active_energy = 0
        self.ac_meter_export_active_energy = 0
        self.ac_meter_total_apparent_power = 0

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

        print("Starting " + self.name + " with UID " + str(self.uid) + " on version " + str(self.module_version))
        
        # Track Interval usage (GIL interference)
        self.starttime = time.time()
        self.interval_count = 0
        self.max_delay = 0
        self.last_time = 0

        self.common_data_counter = 0

    def process(self):
        global loop_time
        #print("(4) AC Meter Heartbeat: " + str(self.heartbeat) + ", loop time: " + str(loop_time) + " Seconds")
        
        # Calculate successful polls over an hour
        if time.time() - self.starttime < 3600:    # Record for an hour after starting
        
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
            #print("AC Meter " + str(self.uid) + ": " + str(self.interval_count))
        else:
            #print("AC Meter " + str(self.uid) + ": " + str(self.interval_count) + " events in 1 hour. Maximum gap between events was " + str(self.max_delay) + " seconds.")
            pass
        
        s = time.time()
        
        self.heartbeat += 1
        if self.heartbeat >= 0xFFFF:
            self.heartbeat = 0
        self.ac_meter_heartbeat = self.heartbeat

        if self.tcp_timeout >= 5:
            self.tcp_client = None
            self.tcp_timeout = 0

        if self.tcp_client is None:
            self.con_state = False
            if "ac_meter_ipaddr_local" in self.dbData:
                if self.dbData["ac_meter_ipaddr_local"] != "0.0.0.0":

                    try:
                        # Access via Sunspec
                        if USE_SUNSPEC:
                            self.tcp_client = sunspec_client.SunSpecClientDevice(
                                device_type=sunspec_client.TCP,
                                slave_id=1,
                                ipaddr=self.dbData["ac_meter_ipaddr_local"],
                                ipport=502)

                            if self.tcp_client is not None:
                                self.update_faults(Faults.LOSS_OF_COMMS.value, False)  # Clear Fault
                                self.set_state_text(State.CONNECTED)
                                self.con_state = True
                            else:
                                self.update_faults(Faults.LOSS_OF_COMMS.value, True)  # Raise Fault
                                self.set_state_text(State.CONNECTING)
                                self.tcp_client = None
                        # Access via Modbus-TCP
                        else:
                            self.tcp_client = modbus_client(self.dbData["ac_meter_ipaddr_local"], port=502)

                            if self.tcp_client.connect() is True:
                                self.update_faults(Faults.LOSS_OF_COMMS.value, False)  # Clear Fault
                                self.set_state_text(State.CONNECTED)
                                self.con_state = True
                            else:
                                self.update_faults(Faults.LOSS_OF_COMMS.value, True)  # Raise Fault
                                self.set_state_text(State.CONNECTING)
                                self.tcp_client = None

                    except Exception as e:
                        print("AC Meter: " + str(e))
                        self.update_faults(Faults.LOSS_OF_COMMS.value, True)
                        self.set_state_text(State.CONNECTING)
                else:
                    self.update_faults(Faults.LOSS_OF_COMMS.value, True)  # Raise Fault
                    self.set_state_text(State.CONFIG)
            else:
                self.update_faults(Faults.LOSS_OF_COMMS.value, True)
                self.set_state_text(State.CONFIG)
        else:   # Main process loop
            # Common Data
            self.common_data_counter += 1
            if self.common_data_counter >= 100:
                self.common_data_counter = 0
            if self.common_data_counter == 5:
                try:
                    # Read model name from the Meter (300012)
                    rr = self.tcp_client.read_holding_registers(11, 1, unit=1)  # Reading Model (300012)
                    if rr.isError():
                        self.tcp_timeout += 1
                        print("AC Meter: failed to read make")
                        return
                    else:
                        self.tcp_timeout = 0
                        self.name = rr.registers[0]

                    # Read Serial Number from the Meter (320481 to 320487, 7 words total)
                    rr_serial = self.tcp_client.read_holding_registers(20480, 7, unit=1)  # Reading from 5000h to 5006h (adjusted)

                    if rr_serial.isError():
                        self.tcp_timeout += 1
                        print("AC Meter: failed to read serial")
                        return
                    else:
                        self.tcp_timeout = 0
                        # Decode the serial number (each register contains 2 ASCII characters)
                        serial_decoder = BinaryPayloadDecoder.fromRegisters(rr_serial.registers, byteorder=Endian.Big)
                        try:
                            serial = serial_decoder.decode_string(14).decode("utf-8")  # 7 registers * 2 characters each = 14 characters
                            self.serial = serial
                        except Exception as e:
                            print(f"AC Meter: Failed to decode serial number: {e}")
                            self.serial = "Invalid Serial"

                except Exception as e:
                    print("AC Meter: " + str(e))
                    self.tcp_timeout += 1
                    return

            # Basic Metering
            try:
                # Phase Currents (registers 300013 correspond to A L1, A L2, and A L3)
                rr = self.tcp_client.read_holding_registers(12, 6, unit=1)

                if rr.isError():
                    self.tcp_timeout += 1
                    print("AC Meter: Failed to read phase currents")
                    return
                else:
                    self.tcp_timeout = 0

                    # Decoding as 32-bit integers, and applying scaling (Ampere*1000)
                    phase_a_current = self.twos_comp_to_int32((rr.registers[1] << 16) + rr.registers[0]) / 1000   # Ampere*1000 scaling
                    phase_b_current = self.twos_comp_to_int32((rr.registers[3] << 16) + rr.registers[2]) / 1000   # Ampere*1000 scaling
                    phase_c_current = self.twos_comp_to_int32((rr.registers[5] << 16) + rr.registers[4]) / 1000  # Ampere*1000 scaling

                    self.pA = self.ac_meter_total_current = int((phase_a_current + phase_b_current + phase_c_current) / 3)

                    # Current is always reported positive, but adjust polarity based on active power
                    if self.ac_meter_total_active_power < 0:
                        self.pA = -self.pA

            except Exception as e:
                print("AC Meter: " + str(e))
                self.tcp_timeout += 1
                return
            try:
                rr = self.tcp_client.read_holding_registers(12, 40, unit=1)  # 39 registers from 300013 to 300052 inclusive

                if rr.isError():
                    print("Failed to read power")
                    self.tcp_timeout += 1
                    return
                else:
                    self.tcp_timeout = 0

                    # Adjusting indices
                    # Calculate the average current
                    phase_a_current = self.twos_comp_to_int32((rr.registers[1] << 16) + rr.registers[0]) / 1000 
                    phase_b_current = self.twos_comp_to_int32((rr.registers[3] << 16) + rr.registers[2]) / 1000 
                    phase_c_current = self.twos_comp_to_int32((rr.registers[5] << 16) + rr.registers[4]) / 1000 
                    self.ac_meter_average_current = (phase_a_current + phase_b_current + phase_c_current) / 3

                    # Average line voltage
                    self.ac_meter_average_line_voltage = self.twos_comp_to_int32((rr.registers[27] << 16) + rr.registers[26]) / 10 # BinaryPayloadDecoder.fromRegisters(rr.registers[26:28], byteorder=Endian.Big).decode_32bit_float()

                    # Power factor
                    self.ac_meter_power_factor = BinaryPayloadDecoder.fromRegisters(rr.registers[37:38], byteorder=Endian.Big).decode_16bit_int() / 1000

                    # Phase power values
                    self.ac_meter_phase_a_power = (self.twos_comp_to_int32((rr.registers[13] << 16) + rr.registers[12]) / 10) / 1000 # /1000 for kW # BinaryPayloadDecoder.fromRegisters(rr.registers[12:14], byteorder=Endian.Big).decode_32bit_float() / 10
                    self.ac_meter_phase_b_power = (self.twos_comp_to_int32((rr.registers[15] << 16) + rr.registers[14]) / 10) / 1000 # /1000 for kW # BinaryPayloadDecoder.fromRegisters(rr.registers[14:16], byteorder=Endian.Big).decode_32bit_float() / 10
                    self.ac_meter_phase_c_power = (self.twos_comp_to_int32((rr.registers[17] << 16) + rr.registers[16]) / 10) / 1000 # /1000 for kW # BinaryPayloadDecoder.fromRegisters(rr.registers[16:18], byteorder=Endian.Big).decode_32bit_float() / 10

                    # Total active and apparent power
                    self.ac_meter_total_active_power = (self.twos_comp_to_int32((rr.registers[29] << 16) + rr.registers[28]) / 10) / 1000 # / 1000 for kW
                    #TODO: This wasn't working in the SCADA, so divided by 1000 ac_meter_total_apparent_power - Check this and match with SCADA
                    self.ac_meter_total_apparent_power = (self.twos_comp_to_int32((rr.registers[31] << 16) + rr.registers[30]) / 10) / 1000  # BinaryPayloadDecoder.fromRegisters(rr.registers[30:32], byteorder=Endian.Big).decode_32bit_float() / 10

                    # Frequency
                    self.ac_meter_frequency = (BinaryPayloadDecoder.fromRegisters(rr.registers[39:40], byteorder=Endian.Big)).decode_16bit_int()

                    # Update the HMI Icon info
                    power_rep = "ac_meter_power_rep"
                    if power_rep in self.dbData:
                        if self.dbData[power_rep] == "Median":
                            self.ac_meter_total_active_power = (statistics.median([self.ac_meter_phase_a_power, self.ac_meter_phase_b_power, self.ac_meter_phase_c_power])) * 3

                    # Set final values
                    # self.ac_meter_total_active_power = self.ac_meter_total_active_power
                    # self.ac_meter_total_apparent_power = self.ac_meter_total_apparent_power

                    # Check self.ac_meter_total_active_power for negative values by using the PF.
                    if self.ac_meter_power_factor < 0:
                        self.ac_meter_total_active_power = -self.ac_meter_total_active_power

                    # Average Phase Voltage
                    self.pV = self.ac_meter_average_voltage = self.ac_meter_average_line_voltage / 1.7320508076  # LV / sqrt(3)

            except Exception as e:
                print("AC Meter: " + str(e))
                self.tcp_timeout += 1
                return

            try:
                # Energy (import/export)
                rr = self.tcp_client.read_holding_registers(274, 6, unit=1)  # Starting at register 300275, reading 6 registers for kWh(+), kWh(-)

                if rr.isError():
                    self.tcp_timeout += 1
                    print("Failed to read energy")
                    return
                else:
                    self.tcp_timeout = 0

                    # Decode imported and exported energy as 32-bit integers (kWh * 10, so divide by 10)
                    self.ac_meter_import_active_energy = self.twos_comp_to_int32((rr.registers[1] << 16) + rr.registers[0]) / 10 # ((rr.registers[0] << 16) | rr.registers[1]) / 10  # kWh(+)
                    self.ac_meter_export_active_energy = self.twos_comp_to_int32((rr.registers[5] << 16) + rr.registers[4]) / 10 # ((rr.registers[4] << 16) | rr.registers[5]) / 10  # kWh(-)

                    # print(f"Import Active Energy: {self.ac_meter_import_active_energy} kWh, Export Active Energy: {self.ac_meter_export_active_energy} kWh")

            except Exception as e:
                print("AC Meter: " + str(e))
                self.tcp_timeout += 1
                return


                # There is little to do beyond accepting IO requests
            if self.enabled:
                self.enabled_echo = True
            else:
                self.enabled_echo = False

            if len(self.actions) == 0:
                self.actions.append(0)  # Dummy

            import_energy = int((self.ac_meter_import_active_energy * 10) / 1000) # TODO: Check this against the SCADA data and scale factor will show wrong number on HMI
            export_energy = int((self.ac_meter_export_active_energy * 10) / 1000) # Check this against the SCADA data and scale factor will show wrong number on HMI
            # Modify self.outputs
            # FROM MSP Register Map
            # Average Phase Voltage = SF1
            # Average Line Voltage = SF1
            # Average Current = SF1
            # Frequency = SF3
            # Total Power Factor = SF2
            # Total Active Power = SF1
            # Import Active Energy (HI) = SF1
            # Import Active Energy (LO) (see above)
            # Export Active Energy (HI) = SF1
            # Export Active Energy (LO) (see above)
            # Total Apparent Power = SF1
            # Total Current = SF1

            self.outputs[2][0] = self.ac_meter_heartbeat
            self.outputs[2][1] = 0
            self.outputs[2][2] = self.ac_meter_average_voltage
            self.outputs[2][3] = self.ac_meter_average_line_voltage
            self.outputs[2][4] = self.ac_meter_average_current
            self.outputs[2][5] = self.ac_meter_frequency
            self.outputs[2][6] = self.ac_meter_power_factor
            self.outputs[2][7] = self.ac_meter_total_active_power
            self.outputs[2][8] = (import_energy >> 16) & 0xFFFF
            self.outputs[2][9] = import_energy & 0xFFFF
            self.outputs[2][10] = (export_energy >> 16) & 0xFFFF
            self.outputs[2][11] = export_energy & 0xFFFF
            self.outputs[2][12] = 0
            # Turned into an integer to match the SCADA data - need to check this.
            self.outputs[2][13] = int(self.ac_meter_total_apparent_power)
            self.outputs[2][14] = 0
            self.outputs[2][15] = self.ac_meter_total_current
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

    def set_page(self, page, form):  # Respond to GET/POST requests
        data = dict()

        if page == self.website + "_(" + str(self.uid) + ")":  # It was a page request for a numbered device (POST)

            # Save all control changes to database
            isButton = True
            for control in form:
                if "ac_meter_override" in form:
                    self.actions.insert(0, Actions.CTRL_OVERRIDE_CHANGE.value)
                    self.override = not self.override
                else:
                    isButton = False

                    if "ac_meter_ipaddr_local" == control:
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

            # Controller Information
            mod_data["ac_meter_name"] = self.name
            mod_data["ac_meter_man"] = self.manufacturer
            mod_data["ac_meter_fwver"] = self.version
            mod_data["ac_meter_serno"] = self.serial
            mod_data["ac_meter_constate"] = str(self.con_state).capitalize()
            mod_data["ac_meter_override"] = self.override
            mod_data["ac_meter_enablestate"] = self.enabled
            mod_data["ac_meter_data"] = self.outputs[2]

            # These three are used primarily to mitigate the load balancing issues we've experienced on REFU systems.
            mod_data["ac_meter_phase_a_power"] = self.ac_meter_phase_a_power
            mod_data["ac_meter_phase_b_power"] = self.ac_meter_phase_b_power
            mod_data["ac_meter_phase_c_power"] = self.ac_meter_phase_c_power

            mod_data.update(self.dbData)                                                            # I'm appending the dict here so we don't save unnecessary
            
            return [SET_PAGE_ACK, mod_data]                                                         # data to the database.

        else:
            return [SET_PAGE_ACK, ('OK', 200)]                                                                        # Return the data to be jsonified

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

    def twos_comp_to_int32(self, twoC):
        # calculate int from two's compliment input
        return twoC if not twoC & 0x80000000 else -(0xFFFFFFFF - twoC + 1)

    def save_to_db(self):
        try:
            db.save_to_db(__name__ + "_(" + str(self.uid) + ")", self.dbData)
        except Exception as e:
            print("AC Meter: " + str(e))
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
            print("AC Meter: " + str(e))
        
        try:
            if tx_msg is not None:
                queue[0].put(tx_msg, block=True)
                
        except Exception as e:
            print("AC Meter: " + str(e))
            

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
    NONE = 0                                                                                        # "Warning: Analogue IO - No Warning present"


class Alarms(Enum):
    NONE = 0                                                                                        # "Alarm: Analogue IO - No Alarm present"


class Faults(Enum):
    NONE = 0
    CONFIG = 1
    LOSS_OF_COMMS = 2


class Actions(Enum):                                                                                # Track the manual interactions with the html page
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
