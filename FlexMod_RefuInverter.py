# FlexMod_RefuInverter.py, Controls and monitors a REFU 50/80kW Inverter

# Description

# Versions
# 3.5.24.10.16 - SC - Known good starting point, uses thread.is_alive to prevent module stalling. 

from pymodbus.client.sync import ModbusTcpClient as mb_tcp_client
from threading import Thread, Event
from FlexMod import BaseModule, ModTypes
from FlexDB import FlexTinyDB
from enum import Enum
from pymodbus.client.sync import ModbusTcpClient as modbus_client
from pymodbus.constants import Endian
from pymodbus.payload import BinaryPayloadDecoder
import sys
import time

from datetime import datetime

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
        self.interval_offset = interval
        self.interval_count = 0

    def run(self):
        global loop_time
        ms_offset = 0

        while not self.stopped.wait(self.interval_offset):

            self.interval_count += 1

            #time_now = datetime.now().microsecond / 1000
            #if time_now > ms_offset:
            #    self.interval_offset = self.interval - ((time_now - ms_offset) / 1000)

            #print("(3) Inverter cycles: " + str(self.interval_count) + " at msOffset: " + str(int(time_now)) + " loop time: " + str(loop_time) + " Seconds")
            #self.target()
            
            self.interval_count += 1
            time_now = datetime.now().microsecond / 1000
            print("(3) Inverter cycles: " + str(self.interval_count) + " at msOffset: " + str(int(time_now)) + " loop time: " + str(loop_time) + " Seconds")
            #self.interval_offset = self.interval - ((datetime.now().microsecond+10) / 1000000) #(time_now / 1000)
            self.target()
'''

class Module():
    def __init__(self, uid, queue):
        super(Module, self).__init__()

        # General Module Data
        self.author = "Sophie Coates"
        self.uid = uid
        self.icon = "/static/images/Inverter.png"
        self.name = "Inverter"
        self.module_type = ModTypes.INVERTER.value
        self.module_version = "3.5.24.10.16"                                                        # Last update on "Flex version | Year | Month | Day"
        self.manufacturer = "REFU"
        self.model = ""
        self.options = ""
        self.version = 0
        self.serial = ""
        self.website = "/Mod/FlexMod_RefuInverter"  # This is the template name itself

        # Run state
        self.con_state = False
        self.state = "Idle"
        self.set_state_text(State.IDLE)

        # Non-Volatile Data (Loaded from DB)
        self.dbData = db.fetch_from_db(__name__ + "_(" + str(self.uid) + ")")
        if self.dbData is None:
            self.dbData = dict()
            self.dbData["inverter_ipaddr_local"] = "0.0.0.0"

        # Volatile Data
        self.tcp_client = None
        self.tcp_timeout = 0
        self.inputs = [self.uid, False]
        self.enabled = False
        self.enabled_echo = False
        self.outputs = [self.uid, self.enabled, [0]*25]
        self.heartbeat = 0
        self.heartbeat_echo = 0
        self.override = False

        self.inverter_quantity = 1                                                                  # Always 1 at module level, may be greater at site level
        self.inverter_heartbeat = 0
        self.inverter_operating_mode = 0
        self.inverter_operating_state = 0
        self.inverter_frequency = 0
        self.inverter_faults1 = 0
        self.inverter_warnings1 = 0
        self.inverter_ac_voltage = 0
        self.inverter_command_real_power = 0
        self.inverter_commanded_real_power = 0
        self.inverter_real_power = 0
        self.inverter_commanded_reactive_power = 0
        self.inverter_reactive_power = 0
        self.inverter_commanded_real_current = 0
        self.inverter_real_current = 0
        self.inverter_commanded_reactive_current = 0
        self.inverter_reactive_current = 0
        self.inverter_commanded_input_current = 0
        self.inverter_dc_voltage = 0
        self.inverter_dc_current = 0
        self.inverter_input_current = 0
        self.inverter_input_voltage = 0
        self.inverter_input_power = 0
        self.inverter_operating_mode = 0

        self.inverter_internal_temperature = 0
        self.inverter_inlet_temperature = 0

        self.alert_timeout = 5
        self.fault_clear_timeout = 15
        self.fault_clear_timeout_counter = 0
        self.warning_timeout_counter = 0
        self.alarm_timeout_counter = 0
        self.fault_timeout_counter = 0

        # Inverter registers
        self.inv_conn = 0                                                                           # Enable the inverter

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

        # Start interval timer
        #self.stop = Event()
        #self.interval = 1  # Interval timeout in Seconds
        #self.thread = Interval(self.stop, self.__process, self.interval)
        #self.thread.start()

        self.ext_hb_enabled = False

        print("Starting " + self.name + " with UID " + str(self.uid) + " on version " + str(self.module_version))

    def process(self):
        global loop_time
        #print("(3) Inverter Heartbeat: " + str(self.heartbeat) + ", loop time: " + str(loop_time) + " Seconds")
        
        s = time.time()
    
        self.heartbeat += 1
        if self.heartbeat >= 0xFFFF:
            self.heartbeat = 0
        self.inverter_heartbeat = self.heartbeat

        if self.tcp_timeout >= 5:
            self.tcp_timeout = 0
            self.tcp_client = None

        if self.tcp_client is None:
            self.con_state = False
            if "inverter_ipaddr_local" in self.dbData:
                if self.dbData["inverter_ipaddr_local"] != "0.0.0.0":

                    try:
                        self.tcp_client = modbus_client(self.dbData["inverter_ipaddr_local"], port=502)

                        if self.tcp_client.connect() is True:
                            self.update_faults(Faults.LOSS_OF_COMMS.value, False)  # Clear Fault
                            self.set_state_text(State.CONNECTED)
                            self.con_state = True
                        else:
                            self.update_faults(Faults.LOSS_OF_COMMS.value, True)  # Raise Fault
                            self.set_state_text(State.CONNECTING)
                            self.tcp_client = None

                    except Exception as e:
                        print("Inverter: " + str(e))
                        self.update_faults(Faults.LOSS_OF_COMMS.value, True)
                        self.set_state_text(State.CONNECTING)
                else:
                    self.update_faults(Faults.LOSS_OF_COMMS.value, True)  # Raise Fault
                    self.set_state_text(State.CONFIG)
            else:
                self.update_faults(Faults.LOSS_OF_COMMS.value, True)
                self.set_state_text(State.CONFIG)
        else:

            # Enable the external Heartbeat on the REFU
            # if not self.ext_hb_enabled:
            #     try:
            #         self.tcp_client.write_register(40210, 3121, unit=1)                             # Refu parameter ID for Heartbeat Activation
            #         self.tcp_client.write_register(40212, 0, unit=1)                                # Refu Parameter Index = 0
            #         self.tcp_client.write_register(40215, 60, unit=1)                               # Set the heartbeat Timeout to 60 seconds
            #
            #         self.ext_hb_enabled = True
            #     except:
            #         return
            # else:
            #     # Read back the rolling Inverter heartbeat
            #     rr = self.tcp_client.read_holding_registers(40228, 1, unit=1, timeout=0.25)
            #     print("Reading heartbeat: " + str(rr.registers[0]))
            #
            #     # Write to the Inverter heartbeat register
            #     self.tcp_client.write_register(40229, rr.registers[0], unit=1)  # Send an incremental value
            #     print("Sending heartbeat: " + str(rr.registers[0]))
            #
            #     #self.tcp_client.write_register(40210, 2564, unit=1)
            #     #self.tcp_client.write_register(40212, 0, unit=1)
            #     #rr = self.tcp_client.read_holding_registers(40215, 1, unit=1, timeout=0.25)
            #     #print(rr)
            #     #print("password level = " + str(rr.registers[0]))

            try:
                rr = self.tcp_client.read_holding_registers(40000, 101, unit=1, timeout=0.25)

                if rr.isError():
                    self.tcp_timeout += 1
                    return
                else:
                    # Manufacturer
                    manufacturer = ""
                    for x in range(16):
                        manufacturer += chr(rr.registers[x + 4] >> 8) + chr(rr.registers[x + 4] & 0xFF)
                    self.manufacturer = manufacturer

                    # Model
                    model = ""
                    for x in range(16):
                        model += chr(rr.registers[x + 20] >> 8) + chr(rr.registers[x + 20] & 0xFF)
                    self.name = model

                    # Release
                    release = ''
                    for x in range(8):
                        release += chr(rr.registers[x + 44] >> 8) + chr(rr.registers[x + 44] & 0xFF)
                    self.version = release

                    # Serial Number
                    serial = ''
                    for x in range(8):
                        serial += chr(rr.registers[x + 52] >> 8) + chr(rr.registers[x + 52] & 0xFF)
                    self.serial = serial

                    # AC Current
                    sf = self.twos_comp_to_int(rr.registers[76])
                    self.pA = self.inverter_real_current = self.twos_comp_to_int(rr.registers[72]) * pow(10, sf)

                    '''
                    # AC Current Phase 1
                    sf = self.twoCtoint(rr.registers[76])
                    self.local_vars["inv1_Current1"] = "{:.2f}".format(self.twoCtoint(rr.registers[73]) * pow(10, sf))

                    # AC Current Phase 2
                    sf = self.twoCtoint(rr.registers[76])
                    self.local_vars["inv1_Current2"] = "{:.2f}".format(self.twoCtoint(rr.registers[74]) * pow(10, sf))  # Incorrect value returned by REFU

                    # AC Current Phase 3
                    sf = self.twoCtoint(rr.registers[76])
                    self.local_vars["inv1_Current3"] = "{:.2f}".format(self.twoCtoint(rr.registers[75]) * pow(10, sf))  # Incorrect value returned by REFU
                    '''
                    # AC Phase Voltage AB
                    sf = self.twos_comp_to_int(rr.registers[83])
                    phase_ab = self.twos_comp_to_int(rr.registers[77]) * pow(10, sf)

                    # AC Phase Voltage BC
                    sf = self.twos_comp_to_int(rr.registers[83])
                    phase_bc = self.twos_comp_to_int(rr.registers[78]) * pow(10, sf)

                    # AC Phase Voltage CA
                    sf = self.twos_comp_to_int(rr.registers[83])
                    phase_ca = self.twos_comp_to_int(rr.registers[79]) * pow(10, sf)

                    # AC Voltage
                    self.pV = self.inverter_ac_voltage = ((phase_ab + phase_bc + phase_ca) / 3)

                    # AC Active Power
                    sf = self.twos_comp_to_int(rr.registers[85])
                    self.inverter_real_power = self.twos_comp_to_int(rr.registers[84]) * pow(10, sf)

                    # Frequency
                    sf = self.twos_comp_to_int(rr.registers[87])
                    self.inverter_frequency = self.twos_comp_to_int(rr.registers[86]) * pow(10, sf)

                    # AC Reactive Power
                    sf = self.twos_comp_to_int(rr.registers[91])
                    self.inverter_reactive_power = self.twos_comp_to_int(rr.registers[90]) * pow(10, sf)

                    # DC Current
                    sf = self.twos_comp_to_int(rr.registers[98])
                    self.sA = self.inverter_dc_current = self.twos_comp_to_int(rr.registers[97]) * pow(10, sf)

                    # DC Voltage
                    sf = self.twos_comp_to_int(rr.registers[100])
                    self.sV = self.inverter_dc_voltage = self.twos_comp_to_int(rr.registers[99]) * pow(10, sf)

                    self.tcp_timeout = 0

            except Exception as e:
                print("Inverter: " + str(e))
                self.tcp_timeout += 1
                return

            try:
                rr = self.tcp_client.read_holding_registers(40100, 100, unit=1, timeout=0.25)

                if rr.isError():
                    self.tcp_timeout += 1
                    return
                else:
                    # Cabinet (Internal) Temperature
                    sf = self.twos_comp_to_int(rr.registers[7])
                    self.inverter_internal_temperature = self.twos_comp_to_int(rr.registers[3]) * pow(10, sf)

                    # Heat sink (Inlet?) Temperature
                    sf = self.twos_comp_to_int(rr.registers[7])
                    self.inverter_inlet_temperature = self.twos_comp_to_int(rr.registers[4]) * pow(10, sf)

                    # State
                    self.inverter_operating_state = rr.registers[8]

                    # Faults
                    self.faults = rr.registers[10]

                    # Power Limit for the REFU
                    sf = self.twos_comp_to_int(rr.registers[26])
                    power_limit = (self.twos_comp_to_int(rr.registers[25]) * pow(10, sf) / 1000)

                    # TODO? real power echo

                    # Prevent requesting power beyond REFU power limit
                    new_power = self.inverter_command_real_power

                    if new_power < 0:                                                                     # Discharging
                        if new_power < -power_limit:
                            new_power = -power_limit
                    elif new_power > power_limit:
                        new_power = power_limit

                    # The Refu inverter is send a percentage for power, but this reflects the power we want it to produce
                    self.inverter_commanded_real_power = new_power

                    # Commanded Power (kW to % conversion for REFU)
                    pct_power = (new_power / power_limit) * 100

                    sf = 1  # TODO - pull this from 40205, WMaxLimPct_SF. Why aren't you already doing so?
                    command_power = self.int_to_twos_comp(pct_power * pow(10, sf))

                    if command_power != rr.registers[87]:                                               # Commanded power request has changed
                        try:
                            self.tcp_client.write_register(40187, int(command_power), unit=1)

                            # Enable Throttling (WMaxLim_Ena)
                            #if command_power > 0:
                            self.tcp_client.write_register(40191, 1, unit=1)
                            #else:
                            #    self.tcp_client.write_register(40191, 0, unit=1)

                            self.tcp_timeout = 0
                        except Exception as e:
                            print("Inverter: " + str(e))
                            self.tcp_timeout += 1
                            return

                    self.tcp_timeout = 0
            except Exception as e:
                print("Inverter: " + str(e))
                self.tcp_timeout += 1
                return

            try:
                rr = self.tcp_client.read_holding_registers(40200, 40, unit=1, timeout=0.25)

                if rr.isError():
                    self.tcp_timeout += 1
                    return
                else:
                    # Operational Mode
                    op_mode = rr.registers[31]

                    if op_mode != self.inverter_operating_mode:
                        try:
                            self.tcp_client.write_register(40231, self.inverter_operating_mode, unit=1, timeout=0.25)
                            self.tcp_timeout = 0
                        except Exception as e:
                            print("Inverter: " + str(e))
                            self.tcp_timeout += 1
                            return

                    self.tcp_timeout = 0
            except Exception as e:
                print("Inverter: " + str(e))
                self.tcp_timeout += 1
                return

            # Inverter control and operation
            try:
                # Keep updating the inverter control reg to keep it happy
                if self.inv_conn:

                    # TODO - Please enumerate these!
                    if self.inverter_operating_state != 5:                                              # We want to be in Throttling (Following) mode

                        self.inverter_command_real_power = 0                                            # Force zero power before starting

                        if self.inverter_operating_state == 1:                                          # Off
                            self.inverter_operating_mode = 3                                            # Enter Standby
                        elif self.inverter_operating_state == 2:                                        # Sleeping
                            pass
                        elif self.inverter_operating_state == 3:                                        # Starting
                            pass
                        elif self.inverter_operating_state == 4:                                        # MPPT
                            pass
                        elif self.inverter_operating_state == 5:                                        # Throttled
                            pass
                        elif self.inverter_operating_state == 6:                                        # Shutting Down
                            pass
                        elif self.inverter_operating_state == 7:                                        # Fault
                            pass
                        elif self.inverter_operating_state == 8:                                        # Standby
                            self.inverter_operating_mode = 4                                            # Exit Standby
                        elif self.inverter_operating_state == 9:                                        # Started
                            self.inverter_operating_mode = 1                                            # Start
                        elif self.inverter_operating_state == 0:                                        # Unknown
                            self.inverter_operating_mode = 3                                            # Enter Standby
                    else:
                        self.enabled_echo = True
                else:
                    if self.inverter_operating_state == 5:                                              # We are Throttled (Following)

                        # Disable Throttling (WMaxLim_Ena)
                        self.tcp_client.write_register(40191, 0, unit=1)
                        #
                        # # TODO Reduce power (gracefully)
                        self.inverter_command_real_power = 0

                        self.inverter_operating_mode = 3

                    else:
                        self.enabled_echo = False

                self.tcp_timeout = 0
            except Exception as e:
                print("Inverter: " + str(e))
                self.tcp_timeout += 1
                return

            pass
            '''
            # Inverter control version

                    elif self.dbData["inverter_software_hash"] == "3C625C9":

                        # Block 1
                        try:
                            rr = self.tcp_client.read_holding_registers(0, 125, unit=1, timeout=0.25)         # Max 125 bytes

                            sf = self.twos_comp_to_int(rr.registers[99])
                            self.inverter_real_power = self.twos_comp_to_int(rr.registers[98]) * pow(10, sf)

                            sf = self.twos_comp_to_int(rr.registers[101])
                            self.inverter_frequency = self.twos_comp_to_int(rr.registers[100]) * pow(10, sf)

                            sf = self.twos_comp_to_int(rr.registers[105])
                            self.inverter_reactive_power = self.twos_comp_to_int(rr.registers[104]) * pow(10, sf)

                            sf = self.twos_comp_to_int(rr.registers[112])
                            self.sA = self.inverter_dc_current = self.twos_comp_to_int(rr.registers[111]) * pow(10, sf)

                            sf = self.twos_comp_to_int(rr.registers[114])
                            self.sV = self.inverter_dc_voltage = self.twos_comp_to_int(rr.registers[113]) * pow(10, sf)

                            sf = self.twos_comp_to_int(rr.registers[121])
                            self.inverter_internal_temperature = self.twos_comp_to_int(rr.registers[117]) * pow(10, sf)

                            self.inverter_operating_state = rr.registers[123]

                            self.tcp_timeout = 0
                        except:
                            self.tcp_timeout += 1
                            return

                        # Block 2
                        try:
                            rr = self.tcp_client.read_holding_registers(200, 125, unit=1, timeout=0.25)

                            self.inverter_operating_mode = rr.registers[46] & 0x01

                            self.tcp_timeout = 0
                        except:
                            self.tcp_timeout += 1
                            return

                        # Block 3
                        try:
                            rr = self.tcp_client.read_holding_registers(3100, 125, unit=1, timeout=0.25)

                            self.inverter_faults1 = rr.registers[53]
                            self.inverter_faults2 = rr.registers[54]

                            self.inverter_warnings1 = rr.registers[55]
                            self.inverter_warnings2 = rr.registers[56]

                            sf = self.twos_comp_to_int(rr.registers[97])
                            self.pV = self.inverter_ac_voltage = self.twos_comp_to_int(rr.registers[65]) * pow(10, sf)

                            sf = self.twos_comp_to_int(rr.registers[97])
                            self.inverter_input_voltage = self.twos_comp_to_int(rr.registers[74]) * pow(10, sf)

                            sf = self.twos_comp_to_int(rr.registers[101])
                            self.inverter_inlet_temperature = self.twos_comp_to_int(rr.registers[77]) * pow(10, sf)

                            sf = self.twos_comp_to_int(rr.registers[99])
                            self.pA = self.inverter_real_current = self.twos_comp_to_int(rr.registers[85]) * pow(10, sf)

                            sf = self.twos_comp_to_int(rr.registers[99])
                            self.inverter_reactive_current = self.twos_comp_to_int(rr.registers[86]) * pow(10, sf)

                            sf = self.twos_comp_to_int(rr.registers[96])
                            self.inverter_commanded_real_power = self.twos_comp_to_int(rr.registers[115]) * pow(10, sf)

                            sf = self.twos_comp_to_int(rr.registers[96])
                            self.inverter_commanded_reactive_power = self.twos_comp_to_int(rr.registers[116]) * pow(10, sf)

                            sf = self.twos_comp_to_int(rr.registers[99])
                            self.inverter_commanded_real_current = self.twos_comp_to_int(rr.registers[117]) * pow(10, sf)

                            sf = self.twos_comp_to_int(rr.registers[99])
                            self.inverter_commanded_reactive_current = self.twos_comp_to_int(rr.registers[118]) * pow(10, sf)

                            sf = self.twos_comp_to_int(rr.registers[99])
                            self.inverter_commanded_input_current = self.twos_comp_to_int(rr.registers[119]) * pow(10, sf)

                            self.tcp_timeout = 0
                        except:
                            self.tcp_timeout += 1
                            return

                        # WARNINGS
                        if self.inverter_warnings1 > 0 or self.inverter_warnings2 > 0:
                            if self.warning_timeout_counter < self.alert_timeout:
                                self.warning_timeout_counter += 1
                            else:
                            #if self.warning_timeout_counter >= self.alert_timeout:
                                if (self.inverter_warnings2 >> 0) & 0x01:
                                    self.update_warnings(Warnings.CAN_WARNING.value, True)
                                if (self.inverter_warnings2 >> 1) & 0x01:
                                    self.update_warnings(Warnings.CAN_ERROR_PASSIVE.value, True)
                                if (self.inverter_warnings2 >> 2) & 0x01:
                                    self.update_warnings(Warnings.FILTER_OVERTEMP.value, True)
                                if (self.inverter_warnings2 >> 3) & 0x01:
                                    self.update_warnings(Warnings.FAN_CIRCUIT.value, True)
                                if (self.inverter_warnings2 >> 4) & 0x01:
                                    self.update_warnings(Warnings.EE_SAVE_IN_PROGRESS.value, True)
                                if (self.inverter_warnings2 >> 5) & 0x01:
                                    self.update_warnings(Warnings.LOCAL_NETWORK_MISMATCH.value, True)
                                if (self.inverter_warnings2 >> 6) & 0x01:
                                    self.update_warnings(Warnings.REMOTE_NETWORK_MISMATCH.value, True)
                                if (self.inverter_warnings2 >> 7) & 0x01:
                                    self.update_warnings(Warnings.CONDENSATION.value, True)
                                if (self.inverter_warnings2 >> 8) & 0x01:
                                    self.update_warnings(Warnings.I2C.value, True)
                                if (self.inverter_warnings2 >> 9) & 0x01:
                                    self.update_warnings(Warnings.EXTERNAL_INHIBIT.value, True)
                                if (self.inverter_warnings2 >> 10) & 0x01:
                                    self.update_warnings(Warnings.MAINTENANCE_REQUIRED.value, True)
                                if (self.inverter_warnings2 >> 11) & 0x01:
                                    self.update_warnings(Warnings.GROUND_FAULT.value, True)
                                if (self.inverter_warnings2 >> 12) & 0x01:
                                    self.update_warnings(Warnings.FUSE.value, True)
                                if (self.inverter_warnings2 >> 13) & 0x01:
                                    self.update_warnings(Warnings.AC_DISCONNECT.value, True)
                        else:
                            if self.warnings > 0:
                                self.update_warnings(Warnings.CAN_WARNING.value, False)
                                self.update_warnings(Warnings.CAN_ERROR_PASSIVE.value, False)
                                self.update_warnings(Warnings.FILTER_OVERTEMP.value, False)
                                self.update_warnings(Warnings.FAN_CIRCUIT.value, False)
                                self.update_warnings(Warnings.EE_SAVE_IN_PROGRESS.value, False)
                                self.update_warnings(Warnings.LOCAL_NETWORK_MISMATCH.value, False)
                                self.update_warnings(Warnings.REMOTE_NETWORK_MISMATCH.value, False)
                                self.update_warnings(Warnings.CONDENSATION.value, False)
                                self.update_warnings(Warnings.I2C.value, False)
                                self.update_warnings(Warnings.EXTERNAL_INHIBIT.value, False)
                                self.update_warnings(Warnings.MAINTENANCE_REQUIRED.value, False)
                                self.update_warnings(Warnings.GROUND_FAULT.value, False)
                                self.update_warnings(Warnings.FUSE.value, False)

                            self.warning_timeout_counter = 0

                        # FAULTS
                        # EPC inverters have no Alarms, so the most common are in "Faults" and the less common in "Alarms"...which will likely fault anyway...
                        if self.inverter_faults1 > 0 or self.inverter_faults2 > 0:

                            if self.fault_timeout_counter < self.alert_timeout:
                                self.fault_timeout_counter += 1
                            else:
                            #if self.fault_timeout_counter >= self.alert_timeout:
                                if (self.inverter_faults1 >> 0) & 0x01:
                                    self.update_alarms(Alarms.CONTROL_BOARD_VOLTAGE.value, True)
                                if (self.inverter_faults1 >> 1) & 0x01:
                                    self.update_alarms(Alarms.POWER_CHANNEL_IMBALANCE.value, True)
                                if (self.inverter_faults1 >> 2) & 0x01:
                                    self.update_alarms(Alarms.CURRENT_RISE.value, True)
                                if (self.inverter_faults1 >> 3) & 0x01:
                                    self.update_faults(Faults.COOLING_FLOW.value, True)
                                if (self.inverter_faults1 >> 4) & 0x01:
                                    self.update_alarms(Alarms.THERMAL_OVERLOAD.value, True)
                                if (self.inverter_faults1 >> 5) & 0x01:
                                    self.update_alarms(Alarms.FAN_CIRCUIT.value, True)
                                if (self.inverter_faults1 >> 6) & 0x01:
                                    self.update_faults(Faults.POR_TIMEOUT.value, True)
                                if (self.inverter_faults1 >> 7) & 0x01:
                                    self.update_faults(Faults.CONDENSATION.value, True)
                                if (self.inverter_faults1 >> 8) & 0x01:
                                    self.update_faults(Faults.I2C_COMMS.value, True)
                                if (self.inverter_faults1 >> 9) & 0x01:
                                    self.update_faults(Faults.EXTERNAL_INHIBIT.value, True)
                                if (self.inverter_faults1 >> 10) & 0x01:
                                    self.update_faults(Faults.GROUND_FAULT.value, True)
                                if (self.inverter_faults1 >> 11) & 0x01:
                                    self.update_faults(Faults.FUSE.value, True)
                                if (self.inverter_faults1 >> 12) & 0x01:
                                    self.update_alarms(Alarms.BASEPLATE_OVERTEMP.value, True)
                                if (self.inverter_faults1 >> 13) & 0x01:
                                    self.update_alarms(Alarms.AC_DISCONNECT_SWITCH.value, True)
                                if (self.inverter_faults1 >> 14) & 0x01:
                                    self.update_alarms(Alarms.INTERNAL_OVERTEMP.value, True)
                                if (self.inverter_faults1 >> 15) & 0x01:
                                    self.update_faults(Faults.UNDERTEMP.value, True)

                                if (self.inverter_faults2 >> 0) & 0x01:
                                    self.update_faults(Faults.ESTOP_SHUTDOWN.value, True)
                                if (self.inverter_faults2 >> 1) & 0x01:
                                    self.update_faults(Faults.OVERCURRENT.value, True)              # AC Overcurrent
                                if (self.inverter_faults2 >> 2) & 0x01:
                                    self.update_faults(Faults.OVERCURRENT.value, True)              # DC Overcurrent, sharing same bit in SCADA
                                if (self.inverter_faults2 >> 3) & 0x01:
                                    self.update_faults(Faults.OVERVOLTAGE.value, True)              # DC Overvoltage
                                if (self.inverter_faults2 >> 4) & 0x01:
                                    self.update_alarms(Alarms.DEVICE_OVERTEMP.value, True)
                                if (self.inverter_faults1 >> 5) & 0x01:
                                    self.update_alarms(Alarms.INVERTER_OVERTEMP.value, True)
                                if (self.inverter_faults2 >> 6) & 0x01:
                                    self.update_faults(Faults.INVALID_COMMAND_MESSAGE.value, True)  # LOSS OF MODBUS-TCP / CAN COMMS
                                if (self.inverter_faults2 >> 7) & 0x01:
                                    self.update_faults(Faults.UNDERVOLTAGE.value, True)             # DC UNDERVOLTAGE
                                if (self.inverter_faults2 >> 8) & 0x01:
                                    self.update_faults(Faults.OVERVOLTAGE.value, True)              # AC Overvoltage, sharing same bit in SCADA
                                if (self.inverter_faults2 >> 9) & 0x01:
                                    self.update_alarms(Alarms.ILLEGAL_TRANSITION.value, True)
                                if (self.inverter_faults2 >> 10) & 0x01:
                                    self.update_alarms(Alarms.BAD_EEPROM.value, True)               # Bad EE Header
                                if (self.inverter_faults2 >> 11) & 0x01:
                                    self.update_alarms(Alarms.BAD_EEPROM.value, True)               # Bad EE Section, sharing same bit in SCADA
                                if (self.inverter_faults2 >> 12) & 0x01:
                                    self.update_alarms(Alarms.COOLING_SYSTEM.value, True)
                                if (self.inverter_faults2 >> 13) & 0x01:
                                    self.update_alarms(Alarms.AC_TIMED_OVERLOAD.value, True)
                                if (self.inverter_faults2 >> 14) & 0x01:
                                    self.update_alarms(Alarms.DC_TIMED_OVERLOAD.value, True)
                                if (self.inverter_faults2 >> 15) & 0x01:
                                    self.update_alarms(Alarms.TIMED_CIRC_CURRENT.value, True)
                        else:
                            if self.faults > 0 or self.alarms > 0:
                                self.update_alarms(Alarms.CONTROL_BOARD_VOLTAGE.value, False)
                                self.update_alarms(Alarms.POWER_CHANNEL_IMBALANCE.value, False)
                                self.update_alarms(Alarms.CURRENT_RISE.value, False)
                                self.update_faults(Faults.COOLING_FLOW.value, False)
                                self.update_alarms(Alarms.THERMAL_OVERLOAD.value, False)
                                self.update_alarms(Alarms.FAN_CIRCUIT.value, False)
                                self.update_faults(Faults.POR_TIMEOUT.value, False)
                                self.update_faults(Faults.CONDENSATION.value, False)
                                self.update_faults(Faults.I2C_COMMS.value, False)
                                self.update_faults(Faults.EXTERNAL_INHIBIT.value, False)
                                self.update_faults(Faults.GROUND_FAULT.value, False)
                                self.update_faults(Faults.FUSE.value, False)
                                self.update_alarms(Alarms.BASEPLATE_OVERTEMP.value, False)
                                self.update_alarms(Alarms.AC_DISCONNECT_SWITCH.value, False)
                                self.update_alarms(Alarms.INTERNAL_OVERTEMP.value, False)
                                self.update_faults(Faults.UNDERTEMP.value, False)

                                self.update_faults(Faults.ESTOP_SHUTDOWN.value, False)
                                self.update_faults(Faults.OVERCURRENT.value, False)
                                self.update_faults(Faults.OVERVOLTAGE.value, False)
                                self.update_alarms(Alarms.DEVICE_OVERTEMP.value, False)
                                self.update_alarms(Alarms.INVERTER_OVERTEMP.value, False)
                                self.update_faults(Faults.INVALID_COMMAND_MESSAGE.value, False)
                                self.update_faults(Faults.UNDERVOLTAGE.value, False)
                                self.update_alarms(Alarms.ILLEGAL_TRANSITION.value, False)
                                self.update_alarms(Alarms.BAD_EEPROM.value, False)
                                self.update_alarms(Alarms.COOLING_SYSTEM.value, False)
                                self.update_alarms(Alarms.AC_TIMED_OVERLOAD.value, False)
                                self.update_alarms(Alarms.DC_TIMED_OVERLOAD.value, False)
                                self.update_alarms(Alarms.TIMED_CIRC_CURRENT.value, False)

                            self.fault_timeout_counter = 0

                        

                    elif self.dbData["inverter_software_hash"] == "FEA5619":
                        pass

                    elif self.dbData["inverter_software_hash"] == "FE294E5":
                        pass
            '''

        if not self.override:
            if self.enabled:                                                                            # The controller is asking us to enable the inverter
                self.inv_conn = True
            else:
                self.inv_conn = False

        if len(self.actions) == 0:
            self.actions.append(0)  # Dummy

        # The Flex3 inverter states were based on the EPC Inverter as they were fairly comprehensive, so we'll offset the REFU states to match.
        refu_operating_state = 0

        if self.inverter_operating_state == 1:  # Off
            refu_operating_state = 0    # POR
        elif self.inverter_operating_state == 2:  # Sleeping
            refu_operating_state = 0    # POR
        elif self.inverter_operating_state == 3:  # Starting
            refu_operating_state = 8    # Charging DC
        elif self.inverter_operating_state == 4:  # MPPT
            refu_operating_state = 0    # POR? We shouldn't be going into this mode
        elif self.inverter_operating_state == 5:  # Throttled
            refu_operating_state = 2    # Following
        elif self.inverter_operating_state == 6:  # Shutting Down
            refu_operating_state = 1    # Ready
        elif self.inverter_operating_state == 7:  # Fault
            refu_operating_state = 3    # Fault
        elif self.inverter_operating_state == 8:  # Standby
            refu_operating_state = 1    # Ready
        elif self.inverter_operating_state == 9:  # Started
            refu_operating_state = 11   # Transitioning
        elif self.inverter_operating_state == 0:  # Unknown
            refu_operating_state = 0    # POR

        # Modify self.outputs
        self.outputs[2][0] = self.inverter_heartbeat
        self.outputs[2][1] = refu_operating_state   # self.inverter_operating_state
        self.outputs[2][2] = self.inverter_frequency
        self.outputs[2][3] = self.inverter_ac_voltage
        self.outputs[2][4] = self.inverter_commanded_real_current
        self.outputs[2][5] = self.inverter_real_current
        self.outputs[2][6] = self.inverter_commanded_reactive_current
        self.outputs[2][7] = self.inverter_reactive_current
        self.outputs[2][8] = self.inverter_commanded_real_power
        self.outputs[2][9] = self.inverter_real_power
        self.outputs[2][10] = self.inverter_commanded_reactive_power
        self.outputs[2][11] = self.inverter_reactive_power
        self.outputs[2][12] = self.inverter_dc_voltage
        self.outputs[2][13] = self.inverter_dc_current
        self.outputs[2][14] = self.inverter_input_voltage
        self.outputs[2][15] = self.inverter_commanded_input_current
        self.outputs[2][16] = self.inverter_input_current
        self.outputs[2][17] = self.inverter_internal_temperature
        self.outputs[2][18] = self.inverter_inlet_temperature
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

                        # Real Power Command
                        input_power = self.inputs[2][8]

                        # TODO: Neat as this is, we should be commanding the power *here* to avoid the process loop time (currently 1 second),
                        #  after we've already spent <0.5 seconds in Flex.py going from SCADA -> Client -> inverter

                        if -1000 < input_power < 1000:
                            self.inverter_command_real_power = input_power

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
                if "inverter_override" in form:
                    self.actions.insert(0, Actions.CTRL_OVERRIDE_CHANGE.value)
                    self.override = not self.override

                elif "inverter_enable_button_state" in form:
                    self.actions.insert(0, Actions.INVERTER_ENABLE_CHANGE.value)
                    if not self.inv_conn:
                        self.inv_conn = True
                    else:
                        self.inv_conn = False

                elif "inverter_real_power_set" in form:
                    if self.override:
                        self.actions.insert(0, Actions.REAL_POWER_CHANGE.value)
                        input_power = int(form[control])

                        # TODO: This needs to be compared against HMI Import and export limits
                        if -1000 < input_power < 1000:
                            self.inverter_command_real_power = input_power
                else:
                    isButton = False

                    if "inverter_ipaddr_local" == control:
                        self.actions.insert(0, Actions.IP_ADDRESS_CHANGE.value)
                    elif "inverter_software_hash" == control:
                        self.actions.insert(0, Actions.SOFTWARE_HASH_CHANGE.value)

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
            mod_data["inverter_name"] = self.name
            mod_data["inverter_man"] = self.manufacturer
            mod_data["inverter_fwver"] = self.version
            mod_data["inverter_serno"] = self.serial
            mod_data["inverter_constate"] = str(self.con_state).capitalize()
            mod_data["inverter_data"] = self.outputs[2]
            mod_data["inverter_override"] = self.override
            mod_data["inverter_start"] = self.inv_conn
            mod_data["inverter_enablestate"] = self.enabled

            mod_data.update(self.dbData)                                                       # I'm appending the dict here so we don't save unnecessary
            
            return [SET_PAGE_ACK, mod_data]  # data to the database.

        else:

            return [SET_PAGE_ACK, ('OK', 200)]                                                                       # Return the data to be jsonified

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
    
    def save_to_db(self):
        # This shouldn't be triggered by buttons!
        try:
            db.save_to_db(__name__ + "_(" + str(self.uid) + ")", self.dbData)
        except:
            print("Unable to save record, may already exist?")  # Todo find error code from tinydb

    def twos_comp_to_int(self, twoC):
        return twoC if not twoC & 0x8000 else -(0xFFFF - twoC + 1)

    def int_to_twos_comp(self, num):
        return num if num >= 0 else (0xFFFF + num + 1)

    def kill(self):
        self.stop.set()


def driver(queue, uid):
    
    PROC_TIMEOUT = 5
    
    # Create and init our Module class
    flex_module = Module(uid, queue)
    
    # Create a dummy thread
    thread = Thread(target=flex_module.process)
    thread_timeout = PROC_TIMEOUT
    
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
            print("Inverter: " + str(e))
          
        try:
            if tx_msg is not None:
                queue[0].put(tx_msg, block=True)
                
        except Exception as e:
            print("Inverter: " + str(e))
            
 
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


class Warnings(Enum):
    NONE = 0
    CAN_WARNING = 1
    CAN_ERROR_PASSIVE = 2
    FILTER_OVERTEMP = 3
    FAN_CIRCUIT = 4
    EE_SAVE_IN_PROGRESS = 5
    LOCAL_NETWORK_MISMATCH = 6
    REMOTE_NETWORK_MISMATCH = 7
    CONDENSATION = 8
    I2C = 9
    EXTERNAL_INHIBIT = 10
    MAINTENANCE_REQUIRED = 11
    GROUND_FAULT = 12
    FUSE = 13
    AC_DISCONNECT = 14


class Alarms(Enum):
    DEVICE_OVERTEMP = 0
    INVERTER_OVERTEMP = 1
    FAN_CIRCUIT = 2
    ILLEGAL_TRANSITION = 3
    BAD_EEPROM = 4
    THERMAL_OVERLOAD = 5
    COOLING_SYSTEM = 6
    AC_TIMED_OVERLOAD = 7
    DC_TIMED_OVERLOAD = 8
    TIMED_CIRC_CURRENT = 9
    CONTROL_BOARD_VOLTAGE = 10
    POWER_CHANNEL_IMBALANCE = 11
    CURRENT_RISE = 12
    BASEPLATE_OVERTEMP = 13
    AC_DISCONNECT_SWITCH = 14
    INTERNAL_OVERTEMP = 15


class Faults(Enum):
    NONE = 0
    CONFIG = 1                                                                                      # "Fault: Inverter - Configuration"
    LOSS_OF_COMMS = 2                                                                               # "Fault: Inverter - Loss of Comms"
    ESTOP_SHUTDOWN = 3
    OVERCURRENT = 4
    OVERVOLTAGE = 5
    UNDERVOLTAGE = 6
    COOLING_FLOW = 7
    INVALID_COMMAND_MESSAGE = 8
    POR_TIMEOUT = 9
    CONDENSATION = 10
    I2C_COMMS = 11
    EXTERNAL_INHIBIT = 12
    GROUND_FAULT = 13
    FUSE = 14
    UNDERTEMP = 15


class Actions(Enum):
    NONE = 0
    IP_ADDRESS_CHANGE = 1
    CTRL_OVERRIDE_CHANGE = 2
    INVERTER_ENABLE_CHANGE = 3
    REAL_POWER_CHANGE = 4
    SOFTWARE_HASH_CHANGE = 5


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
