# Accuvim II - AC Meter Module

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
SYNC = 0  # Allows the Flex code to synchronise module polling loops using a single thread
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
        # super(Module, self).__init__()

        # General Module Data
        self.author = "Sophie Coates"
        self.uid = uid
        self.icon = "/static/images/ACmeter.png"
        self.name = "Acuvim II"
        self.module_type = ModTypes.AC_METER.value
        self.manufacturer = "Accuenergy"
        self.model = ""
        self.options = ""
        self.version = "-"
        self.serial = "-"
        self.website = "/Mod/FlexMod_AccuACMeter"

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
        self.outputs = [self.uid, self.enabled, [0] * 25]
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
        # for Trumpf inverter:
        self.ac_meter_total_apparent_power_L1 = 0
        self.ac_meter_total_apparent_power_L2 = 0
        self.ac_meter_total_apparent_power_L3 = 0

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
        self.starttime = time.time()
        self.interval_count = 0
        self.max_delay = 0
        self.last_time = 0

    def process(self):
        global loop_time
        # print("(4) AC Meter Heartbeat: " + str(self.heartbeat) + ", loop time: " + str(loop_time) + " Seconds")

        # Calculate successful polls over an hour
        if time.time() - self.starttime < 3600:  # Record for an hour after starting

            # Calculate delay between polls
            time_now = time.time()
            delay = time_now - self.last_time
            # print(delay)
            if self.last_time > 0:
                if self.last_time > 0:
                    if delay > self.max_delay:
                        self.max_delay = delay
            self.last_time = time_now

            self.interval_count += 1
            # print("AC Meter " + str(self.uid) + ": " + str(self.interval_count))
        else:
            # print("AC Meter " + str(self.uid) + ": " + str(self.interval_count) + " events in 1 hour. Maximum gap between events was " + str(self.max_delay) + " seconds.")
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
        else:  # Main process loop

            # Common data
            try:
                rr = self.tcp_client.read_holding_registers(50000, 65, unit=1)

                if rr.isError():
                    self.tcp_timeout += 1
                    return
                else:
                    self.tcp_timeout = 0
                    self.manufacturer = (
                        (BinaryPayloadDecoder.fromRegisters(rr.registers[4:20], byteorder=Endian.Big)).decode_string(
                            16)).decode("utf-8")
                    self.name = (
                        (BinaryPayloadDecoder.fromRegisters(rr.registers[20:36], byteorder=Endian.Big)).decode_string(
                            16)).decode("utf-8")
                    self.version = (
                        (BinaryPayloadDecoder.fromRegisters(rr.registers[44:52], byteorder=Endian.Big)).decode_string(
                            16)).decode("utf-8")
                    self.serial = (
                        (BinaryPayloadDecoder.fromRegisters(rr.registers[52:68], byteorder=Endian.Big)).decode_string(
                            16)).decode("utf-8")

            except Exception as e:
                print("AC Meter: " + str(e))
                self.tcp_timeout += 1
                return

            # Basic Metering
            if USE_SUNSPEC:
                try:
                    self.tcp_client.ac_meter.read()
                    self.tcp_timeout = 0

                    self.ac_meter_average_voltage = self.tcp_client.ac_meter.PhV
                    self.ac_meter_average_line_voltage = self.tcp_client.ac_meter.PPV
                    self.ac_meter_average_current = self.tcp_client.ac_meter.A
                    self.ac_meter_frequency = self.tcp_client.ac_meter.Hz

                    self.ac_meter_power_factor = self.tcp_client.ac_meter.PF
                    self.ac_meter_total_active_power = self.tcp_client.ac_meter.W
                    # self.ac_meter_total_apparent_power = self.tcp_client.ac_meter.VA

                    self.ac_meter_import_active_energy = self.tcp_client.ac_meter.TotWhImp
                    self.ac_meter_export_active_energy = self.tcp_client.ac_meter.TotWhExp
                except Exception as e:
                    print("AC Meter: " + str(e))
                    self.tcp_timeout += 1
                    return

            else:
                try:
                    # Phase Currents
                    rr = self.tcp_client.read_holding_registers(12306, 10, unit=1)

                    if rr.isError():

                        self.tcp_timeout += 1
                        return
                    else:

                        self.tcp_timeout = 0

                        phase_a_current = (BinaryPayloadDecoder.fromRegisters(rr.registers[0:2],
                                                                              byteorder=Endian.Big)).decode_32bit_float()
                        phase_b_current = (BinaryPayloadDecoder.fromRegisters(rr.registers[2:4],
                                                                              byteorder=Endian.Big)).decode_32bit_float()
                        phase_c_current = (BinaryPayloadDecoder.fromRegisters(rr.registers[4:6],
                                                                              byteorder=Endian.Big)).decode_32bit_float()
                        self.pA = self.ac_meter_total_current = (
                                                                            phase_a_current + phase_b_current + phase_c_current) / 3

                        # Current is always reported positive but that screws up our animation polarity. Active power is polarised though!
                        if self.ac_meter_total_active_power < 0:
                            self.pA = -self.pA
                except Exception as e:
                    print("AC Meter: " + str(e))
                    self.tcp_timeout += 1
                    return

                try:
                    # Power
                    rr = self.tcp_client.read_holding_registers(16384, 72, unit=1)

                    if rr.isError():
                        self.tcp_timeout += 1
                        return
                    else:
                        self.tcp_timeout = 0
                        # self.ac_meter_average_voltage = (BinaryPayloadDecoder.fromRegisters(rr.registers[8:10], byteorder=Endian.Big)).decode_32bit_float()
                        self.ac_meter_average_line_voltage = (BinaryPayloadDecoder.fromRegisters(rr.registers[16:18],
                                                                                                 byteorder=Endian.Big)).decode_32bit_float()
                        self.ac_meter_average_current = (BinaryPayloadDecoder.fromRegisters(rr.registers[24:26],
                                                                                            byteorder=Endian.Big)).decode_32bit_float()
                        self.ac_meter_frequency = (BinaryPayloadDecoder.fromRegisters(rr.registers[0:2],
                                                                                      byteorder=Endian.Big)).decode_32bit_float()

                        self.ac_meter_power_factor = (BinaryPayloadDecoder.fromRegisters(rr.registers[58:60],
                                                                                         byteorder=Endian.Big)).decode_32bit_float()
                        self.ac_meter_phase_a_power = (BinaryPayloadDecoder.fromRegisters(rr.registers[28:30],
                                                                                          byteorder=Endian.Big)).decode_32bit_float() / 1000
                        self.ac_meter_phase_b_power = (BinaryPayloadDecoder.fromRegisters(rr.registers[30:32],
                                                                                          byteorder=Endian.Big)).decode_32bit_float() / 1000
                        self.ac_meter_phase_c_power = (BinaryPayloadDecoder.fromRegisters(rr.registers[32:34],
                                                                                          byteorder=Endian.Big)).decode_32bit_float() / 1000
                        self.ac_meter_total_active_power = (BinaryPayloadDecoder.fromRegisters(rr.registers[34:36],
                                                                                               byteorder=Endian.Big)).decode_32bit_float() / 1000
                        # for Trumpf inverter:
                        self.ac_meter_total_apparent_power_L1 = (BinaryPayloadDecoder.fromRegisters(rr.registers[44:46],
                                                                                                    byteorder=Endian.Big)).decode_32bit_float() / 1000
                        self.ac_meter_total_apparent_power_L2 = (BinaryPayloadDecoder.fromRegisters(rr.registers[46:48],
                                                                                                    byteorder=Endian.Big)).decode_32bit_float() / 1000
                        self.ac_meter_total_apparent_power_L3 = (BinaryPayloadDecoder.fromRegisters(rr.registers[48:50],
                                                                                                    byteorder=Endian.Big)).decode_32bit_float() / 1000
                        self.ac_meter_total_apparent_power = (BinaryPayloadDecoder.fromRegisters(rr.registers[50:52],
                                                                                                 byteorder=Endian.Big)).decode_32bit_float() / 1000

                        # Update the HMI Icon info
                        # A point to note here. For every module we build, we are expecting imported power to be negative (flowing in), and exported power to
                        # be positive (flowing out). The Acuvim AC meters do it the other way, so here we're looking at Acuvim's polarity and changing one
                        # parameter's sign to ensure that when power is reported or displayed, it is the polarity we expect.

                        # This indicates whether we're reporting the power as total power (normal operation) or applying the median across all phases (REFU)
                        power_rep = "ac_meter_power_rep"
                        if power_rep in self.dbData:
                            if self.dbData[power_rep] == "Median":
                                self.ac_meter_total_active_power = (statistics.median(
                                    [self.ac_meter_phase_a_power, self.ac_meter_phase_b_power,
                                     self.ac_meter_phase_c_power])) * 3

                        self.ac_meter_total_active_power = self.ac_meter_total_active_power
                        self.ac_meter_total_apparent_power = self.ac_meter_total_apparent_power

                        # Average Phase Voltage
                        self.pV = self.ac_meter_average_voltage = self.ac_meter_average_line_voltage / 1.7320508076  # LV / root 3 gives us the Phase Voltage

                except Exception as e:
                    print("AC Meter: " + str(e))
                    self.tcp_timeout += 1
                    return

                try:
                    # Energy
                    rr = self.tcp_client.read_holding_registers(16456, 18, unit=1)

                    if rr.isError():
                        self.tcp_timeout += 1
                        return
                    else:
                        self.tcp_timeout = 0
                        self.ac_meter_import_active_energy = ((rr.registers[0] << 16) | (rr.registers[1])) / 10
                        self.ac_meter_export_active_energy = ((rr.registers[2] << 16) | (rr.registers[3])) / 10

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

                import_energy = int(self.ac_meter_import_active_energy * 10)
                export_energy = int(self.ac_meter_export_active_energy * 10)

                # Modify self.outputs
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
                self.outputs[2][13] = self.ac_meter_total_apparent_power
                self.outputs[2][14] = 0
                self.outputs[2][15] = self.ac_meter_total_current
                self.outputs[2][16] = self.ac_meter_total_apparent_power_L1
                self.outputs[2][17] = self.ac_meter_total_apparent_power_L2
                self.outputs[2][18] = self.ac_meter_total_apparent_power_L3
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

        # outputs = copy.deepcopy(self.outputs)
        # return outputs
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
        return [GET_INFO_ACK, self.uid, self.module_type, self.icon, self.name, self.manufacturer, self.model,
                self.options, self.version, self.website]

    def get_status(self):
        return [GET_STATUS_ACK, self.uid, self.heartbeat, self.priV, self.priA, self.secV, self.secA, self.terV,
                self.terA, self.state, self.warnings, self.alarms, self.faults, self.actions, self.icon]

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
    NONE = 0  # "Warning: Analogue IO - No Warning present"


class Alarms(Enum):
    NONE = 0  # "Alarm: Analogue IO - No Alarm present"


class Faults(Enum):
    NONE = 0
    CONFIG = 1
    LOSS_OF_COMMS = 2


class Actions(Enum):  # Track the manual interactions with the html page
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
