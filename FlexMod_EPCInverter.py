# FlexMod_EPCInverter.py, Controls and monitors an EPC Inverter

import sys
from pymodbus.client.sync import ModbusTcpClient as mb_tcp_client
from threading import Thread, Event
from FlexMod import BaseModule, ModTypes
from FlexDB import FlexTinyDB
from enum import Enum
from pymodbus.client.sync import ModbusTcpClient as modbus_client
from pymodbus.constants import Endian
from pymodbus.payload import BinaryPayloadDecoder

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
        #super(Module, self).__init__()

        # General Module Data
        self.author = "Sophie Coates"
        self.uid = uid
        self.icon = "/static/images/Inverter.png"
        self.name = "Inverter"
        self.module_type = ModTypes.INVERTER.value
        self.manufacturer = "EPC"
        self.model = ""
        self.options = ""
        self.version = "-"
        self.serial = ""
        self.website = "/Mod/FlexMod_EPCInverter"  # This is the template name itself

        # Run state
        self.con_state = False
        self.state = "Idle"
        self.set_state_text(State.IDLE)

        # Non-Volatile Data (Loaded from DB)
        self.dbData = db.fetch_from_db(__name__ + "_(" + str(self.uid) + ")")
        if self.dbData is None:
            self.dbData = dict()
            self.dbData["inverter_ipaddr_local"] = "0.0.0.0"
            self.dbData["inverter_pid_ipaddr_local"] = "0.0.0.0"

        # Volatile Data
        self.tcp_client = None
        self.tcp_timeout = 0
        self.inputs = [self.uid, False]
        self.enabled = False
        self.enabled_echo = False
        self.outputs = [self.uid, self.enabled, [0]*25]
        self.override = False
        self.pid_enable = False                                                                     # Use Shaun's PID feedback loop
        self.heartbeat = 0
        self.heartbeat_echo = 0

        self.pid_tcp_client = None
        self.pid_tcp_timeout = 0
        self.pid_outputs = [[0] * 25]

        self.inverter_quantity = 1                                                                  # Always 1 at module level, may be greater at site level
        self.inverter_heartbeat = 0
        self.inverter_operating_mode = 0
        self.inverter_operating_state = 0
        self.inverter_frequency = 0
        self.inverter_faults1 = 0
        self.inverter_faults2 = 0
        self.inverter_warnings1 = 0
        self.inverter_warnings2 = 0
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
        self.inverter_ctlsrc = 0
        self.inverter_control_mode = "None"
        self.auto_fault_clear = "Manual"
        self.manual_fault_clear = False

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

        print("Starting " + self.name + " with UID " + str(self.uid))
        
        # Track Interval usage (GIL interference)
        self.start_time = time.time()
        self.interval_count = 0
        self.max_delay = 0
        self.last_time = 0

    def process(self):
        global loop_time
        #print("(3) Inverter Heartbeat: " + str(self.heartbeat) + ", loop time: " + str(loop_time) + " Seconds")    
    
        s = time.time()
    
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
            #print("Inverter " + str(self.uid) + ": " + str(self.interval_count))
        else:
            #print("Inverter " + str(self.uid) + ": " + str(self.interval_count) + " events in 1 hour. Maximum gap between events was " + str(self.max_delay) + " seconds.")
            pass
            
        self.heartbeat += 1
        if self.heartbeat >= 0xFFFF:
            self.heartbeat = 0
        self.inverter_heartbeat = self.heartbeat

        # Standard Inverter Control
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
            # Inverter control version
            if "inverter_software_hash" in self.dbData:
                if self.version != self.dbData["inverter_software_hash"]:

                    modbus_address = 0
                    if self.dbData["inverter_software_hash"] == "None":
                        pass
                    elif self.dbData["inverter_software_hash"] == "5253F22":
                        modbus_address = 3418
                    elif self.dbData["inverter_software_hash"] == "3C625C9":
                        modbus_address = 3194
                    elif self.dbData["inverter_software_hash"] == "7E5A428":
                        modbus_address = 3194
                    elif self.dbData["inverter_software_hash"] == "77655E6":
                        modbus_address = 3194
                    elif self.dbData["inverter_software_hash"] == "BF65B78":
                        modbus_address = 3472
                    elif self.dbData["inverter_software_hash"] == "FEA5619":
                        modbus_address = 3472
                    elif self.dbData["inverter_software_hash"] == "FE294E5":
                        pass

                    if modbus_address != 0:
                        try:
                            rr = self.tcp_client.read_holding_registers(modbus_address, 2, unit=1, timeout=0.25)
                            
                            if rr.isError():
                                self.tcp_timeout += 1
                                return
                            else:
                                self.tcp_timeout = 0
                                self.version = str(hex(rr.registers[0] << 16 | rr.registers[1]))[2:].upper()

                        except Exception as e:
                            print("Inverter: " + str(e))
                            self.tcp_timeout += 1
                            return
                else:

                    if self.dbData["inverter_software_hash"] == "None":
                        pass
                    elif self.dbData["inverter_software_hash"] == "5253F22":

                        # Force Modbus mode
                        try:
                            rr = self.tcp_client.read_holding_registers(3458, 1, unit=1)    #, timeout=0.25)
                           
                            if rr.isError():
                                self.tcp_timeout += 1
                                return
                            else:
                                if len(rr.registers) >= 1:
                                    self.inverter_ctlsrc = rr.registers[0]
                                    if self.inverter_ctlsrc == 0:    # We're in CAN mode
                                        try:
                                            self.tcp_client.write_register(3458, 1, unit=1)
                                        except Exception as e:
                                            print("Inverter: " + str(e))
                                            print("Could not set CAN mode on EPC Inverter. Please check your connections")
                                self.tcp_timeout = 0
                        except Exception as e:
                            print("Inverter: " + str(e))
                            self.tcp_timeout += 1
                            return

                        # Model
                        try:
                            rr = self.tcp_client.read_holding_registers(20, 4, unit=1, timeout=0.25)
                            self.name = chr(rr.registers[0] >> 8) + chr(rr.registers[0] & 0xFF) + chr(rr.registers[1] >> 8) + chr(rr.registers[1] & 0xFF) + \
                                        chr(rr.registers[2] >> 8) + chr(rr.registers[2] & 0xFF) + chr(rr.registers[3] >> 8) + chr(rr.registers[3] & 0xFF)
                            self.tcp_timeout = 0
                        except Exception as e:
                            print("Inverter: " + str(e))
                            self.tcp_timeout += 1
                            return

                        # Serial Number
                        try:
                            rr = self.tcp_client.read_holding_registers(52, 4, unit=1, timeout=0.25)
                            self.serial = chr(rr.registers[0] >> 8) + chr(rr.registers[0] & 0xFF) + chr(rr.registers[1] >> 8) + chr(rr.registers[1] & 0xFF) + \
                                          chr(rr.registers[2] >> 8) + chr(rr.registers[2] & 0xFF) + chr(rr.registers[3] >> 8) + chr(rr.registers[3] & 0xFF)
                            self.tcp_timeout = 0
                        except Exception as e:
                            print("Inverter: " + str(e))
                            self.tcp_timeout += 1
                            return

                        # Block 1
                        try:
                            rr = self.tcp_client.read_holding_registers(0, 125, unit=1, timeout=0.25)  # Max 125 bytes

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
                        except Exception as e:
                            print("Inverter: " + str(e))
                            self.tcp_timeout += 1
                            return

                        # Block 2
                        try:
                            rr = self.tcp_client.read_holding_registers(200, 125, unit=1, timeout=0.25)

                            self.inverter_operating_mode = rr.registers[46] & 0x01

                            self.tcp_timeout = 0
                        except Exception as e:
                            print("Inverter: " + str(e))
                            self.tcp_timeout += 1
                            return

                        # Block 3
                        try:
                            rr = self.tcp_client.read_holding_registers(3350, 125, unit=1, timeout=0.25)

                            self.inverter_faults1 = rr.registers[27]
                            self.inverter_faults2 = rr.registers[28]

                            self.inverter_warnings1 = rr.registers[29]
                            self.inverter_warnings2 = rr.registers[30]

                            sf = self.twos_comp_to_int(rr.registers[72])
                            self.pV = self.inverter_ac_voltage = self.twos_comp_to_int(rr.registers[39]) * pow(10, sf)

                            sf = self.twos_comp_to_int(rr.registers[72])
                            self.inverter_input_voltage = self.twos_comp_to_int(rr.registers[48]) * pow(10, sf)

                            sf = self.twos_comp_to_int(rr.registers[76])
                            self.inverter_inlet_temperature = self.twos_comp_to_int(rr.registers[51]) * pow(10, sf)

                            sf = self.twos_comp_to_int(rr.registers[74])
                            self.pA = self.inverter_real_current = self.twos_comp_to_int(rr.registers[59]) * pow(10, sf)

                            sf = self.twos_comp_to_int(rr.registers[74])
                            self.inverter_reactive_current = self.twos_comp_to_int(rr.registers[60]) * pow(10, sf)

                            sf = self.twos_comp_to_int(rr.registers[71])
                            self.inverter_commanded_real_power = self.twos_comp_to_int(rr.registers[92]) * pow(10, sf)

                            sf = self.twos_comp_to_int(rr.registers[71])
                            self.inverter_commanded_reactive_power = self.twos_comp_to_int(rr.registers[93]) * pow(10, sf)

                            sf = self.twos_comp_to_int(rr.registers[74])
                            self.inverter_commanded_real_current = self.twos_comp_to_int(rr.registers[94]) * pow(10, sf)

                            sf = self.twos_comp_to_int(rr.registers[74])
                            self.inverter_commanded_reactive_current = self.twos_comp_to_int(rr.registers[95]) * pow(10, sf)

                            sf = self.twos_comp_to_int(rr.registers[74])
                            self.inverter_commanded_input_current = self.twos_comp_to_int(rr.registers[96]) * pow(10, sf)

                            self.tcp_timeout = 0
                        except Exception as e:
                            print("Inverter: " + str(e))
                            self.tcp_timeout += 1
                            return

                        # # WARNINGS
                        if self.inverter_warnings1 > 0 or self.inverter_warnings2 > 0:
                            if self.warning_timeout_counter < self.alert_timeout:
                                self.warning_timeout_counter += 1
                            else:
                                # if self.warning_timeout_counter >= self.alert_timeout:
                                #if (self.inverter_warnings2 >> 0) & 0x01:
                                #    self.update_warnings(Warnings.CAN_WARNING.value, True)
                                #if (self.inverter_warnings2 >> 1) & 0x01:
                                #    self.update_warnings(Warnings.CAN_ERROR_PASSIVE.value, True)
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
                                #self.update_warnings(Warnings.CAN_WARNING.value, False)
                                #self.update_warnings(Warnings.CAN_ERROR_PASSIVE.value, False)
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
                                self.update_warnings(Warnings.AC_DISCONNECT.value, False)

                            self.warning_timeout_counter = 0

                        # FAULTS
                        # EPC inverters have no Alarms, so the most common are in "Faults" and the less common in "Alarms"...which will likely fault anyway...
                        if self.inverter_faults1 > 0 or self.inverter_faults2 > 0:

                            if self.fault_timeout_counter < self.alert_timeout:
                                self.fault_timeout_counter += 1
                            else:
                                # if self.fault_timeout_counter >= self.alert_timeout:
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
                                # if (self.inverter_faults1 >> 12) & 0x01:
                                #     self.update_alarms(Alarms.BASEPLATE_OVERTEMP.value, True)
                                if (self.inverter_faults1 >> 13) & 0x01:
                                    self.update_alarms(Alarms.AC_DISCONNECT_SWITCH.value, True)
                                # if (self.inverter_faults1 >> 14) & 0x01:
                                #     self.update_alarms(Alarms.INTERNAL_OVERTEMP.value, True)
                                # if (self.inverter_faults1 >> 15) & 0x01:
                                #     self.update_faults(Faults.UNDERTEMP.value, True)

                                if (self.inverter_faults2 >> 0) & 0x01:
                                    self.update_faults(Faults.ESTOP_SHUTDOWN.value, True)
                                if (self.inverter_faults2 >> 1) & 0x01:
                                    self.update_faults(Faults.OVERCURRENT.value, True)  # AC Overcurrent
                                if (self.inverter_faults2 >> 2) & 0x01:
                                    self.update_faults(Faults.OVERCURRENT.value, True)  # DC Overcurrent, sharing same bit in SCADA
                                if (self.inverter_faults2 >> 3) & 0x01:
                                    self.update_faults(Faults.OVERVOLTAGE.value, True)  # DC Overvoltage
                                if (self.inverter_faults2 >> 4) & 0x01:
                                    self.update_alarms(Alarms.DEVICE_OVERTEMP.value, True)
                                if (self.inverter_faults1 >> 5) & 0x01:
                                    self.update_alarms(Alarms.INVERTER_OVERTEMP.value, True)
                                if (self.inverter_faults2 >> 6) & 0x01:
                                    self.update_faults(Faults.INVALID_COMMAND_MESSAGE.value, True)  # LOSS OF MODBUS-TCP / CAN COMMS
                                if (self.inverter_faults2 >> 7) & 0x01:
                                    self.update_faults(Faults.UNDERVOLTAGE.value, True)  # DC UNDERVOLTAGE
                                if (self.inverter_faults2 >> 8) & 0x01:
                                    self.update_faults(Faults.OVERVOLTAGE.value, True)  # AC Overvoltage, sharing same bit in SCADA
                                if (self.inverter_faults2 >> 9) & 0x01:
                                    self.update_alarms(Alarms.ILLEGAL_TRANSITION.value, True)
                                if (self.inverter_faults2 >> 10) & 0x01:
                                    self.update_alarms(Alarms.BAD_EEPROM.value, True)  # Bad EE Header
                                if (self.inverter_faults2 >> 11) & 0x01:
                                    self.update_alarms(Alarms.BAD_EEPROM.value, True)  # Bad EE Section, sharing same bit in SCADA
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
                                # self.update_alarms(Alarms.BASEPLATE_OVERTEMP.value, False)
                                self.update_alarms(Alarms.AC_DISCONNECT_SWITCH.value, False)
                                # self.update_alarms(Alarms.INTERNAL_OVERTEMP.value, False)
                                # self.update_faults(Faults.UNDERTEMP.value, False)

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

                        # Inverter control and operation
                        try:
                            # Keep updating the inverter control reg to keep it happy
                            if self.inv_conn:
                                self.tcp_client.write_register(246, 1, unit=1)

                                if self.inverter_operating_state == 2 or \
                                        self.inverter_operating_state == 4:
                                    # Inverter in Following or Forming mode
                                    self.enabled_echo = True
                            else:
                                self.tcp_client.write_register(246, 0, unit=1)

                                if self.inverter_operating_state != 2 and \
                                        self.inverter_operating_state != 4:

                                    # Zero the commanded power when not in Following mode
                                    self.tcp_client.write_register(3442, self.int_to_twos_comp(0), unit=1)
                                    self.enabled_echo = False

                            self.tcp_timeout = 0
                        except Exception as e:
                            print("Inverter: " + str(e))
                            self.tcp_timeout += 1
                            return

                        # Allow power control in Following mode
                        if self.inverter_operating_state == 2:
                            if self.inverter_command_real_power != int(self.inverter_commanded_real_power):
                                try:
                                    if self.faults > 0:
                                        self.tcp_client.write_register(3442, self.int_to_twos_comp(0), unit=1)
                                    else:
                                        self.tcp_client.write_register(3442, self.int_to_twos_comp(self.inverter_command_real_power * 10), unit=1)
                                    self.tcp_timeout = 0
                                except Exception as e:
                                    print("Inverter: " + str(e))
                                    self.tcp_timeout += 1
                                    return
                        else:
                            if int(self.inverter_commanded_real_power) > 0:
                                try:
                                    self.tcp_client.write_register(3442, self.int_to_twos_comp(0), unit=1)
                                    self.tcp_timeout = 0
                                except Exception as e:
                                    print("Inverter: " + str(e))
                                    self.tcp_timeout += 1
                                    return

                        # Fault rectification - Ensures the fault light isn't on when the system is idle. Simplifies startup and customerd like this.
                        # This works really well! Unfortunately right now it clears the faults before we even have chance to report them!
                        if self.inverter_operating_state == 3:  # and self.fault_timeout_counter >= self.alert_timeout:
                            if self.fault_clear_timeout_counter < self.fault_clear_timeout:
                                self.fault_clear_timeout_counter += 1
                            else:
                                self.fault_clear_timeout_counter = 0

                                # Being able to clear the fault depends entirely on the type of fault.

                                # If it's an undervoltage DC fault, we can't clear it

                                # Disable Inverter
                                try:
                                    rr = self.tcp_client.read_holding_registers(246, 1, unit=1)
                                    conn = rr.registers[0]

                                    if conn & 0x01:  # System is in an enable state after it faulted
                                        conn = 0
                                        self.tcp_client.write_register(246, conn, unit=1)  # Clear enable bit before clearing faults

                                    self.tcp_timeout = 0
                                except Exception as e:
                                    print("Inverter: " + str(e))
                                    self.tcp_timeout += 1
                                    return

                                # Clear Faults (0x01) and Warnings (0x02)
                                try:
                                    cmd = 3
                                    self.tcp_client.write_register(3441, cmd, unit=1)

                                    self.tcp_timeout = 0
                                except Exception as e:
                                    print("Inverter: " + str(e))
                                    self.tcp_timeout += 1
                                    return

                    elif self.dbData["inverter_software_hash"] == "3C625C9" or self.dbData["inverter_software_hash"] == "7E5A428" or self.dbData["inverter_software_hash"] == "77655E6":

                        # Force Modbus mode
                        try:
                            rr = self.tcp_client.read_holding_registers(3230, 1, unit=1)    #, timeout=0.25)
                           
                            if rr.isError():
                                self.tcp_timeout += 1
                                return
                            else:
                                if len(rr.registers) >= 1:
                                    self.inverter_ctlsrc = rr.registers[0]
                                    if self.inverter_ctlsrc == 0:    # We're in CAN mode
                                        try:
                                            self.tcp_client.write_register(3230, 1, unit=1)
                                        except Exception as e:
                                            print("Inverter: " + str(e))
                                            print("Could not set CAN mode on EPC Inverter. Please check your connections")
                                self.tcp_timeout = 0
                        except Exception as e:
                            print("Inverter: " + str(e))
                            self.tcp_timeout += 1
                            return

                        # Model
                        try:
                            rr = self.tcp_client.read_holding_registers(20, 4, unit=1)  #, timeout=0.25)
                            
                            if rr.isError():
                                self.tcp_timeout += 1
                                return
                            else:
                                if len(rr.registers) >= 4:
                                    self.name = chr(rr.registers[0] >> 8) + chr(rr.registers[0] & 0xFF) + chr(rr.registers[1] >> 8) + chr(rr.registers[1] & 0xFF) + \
                                                chr(rr.registers[2] >> 8) + chr(rr.registers[2] & 0xFF) + chr(rr.registers[3] >> 8) + chr(rr.registers[3] & 0xFF)
                                self.tcp_timeout = 0
                        except Exception as e:
                            print("Inverter: " + str(e))
                            self.tcp_timeout += 1
                            return

                        # Serial Number
                        try:
                            rr = self.tcp_client.read_holding_registers(52, 4, unit=1)  #, timeout=0.25)
                            
                            if rr.isError():
                                self.tcp_timeout += 1
                                return
                            else:
                                if len(rr.registers) >= 4:
                                    self.serial = chr(rr.registers[0] >> 8) + chr(rr.registers[0] & 0xFF) + chr(rr.registers[1] >> 8) + chr(
                                        rr.registers[1] & 0xFF) + \
                                                  chr(rr.registers[2] >> 8) + chr(rr.registers[2] & 0xFF) + chr(rr.registers[3] >> 8) + chr(rr.registers[3] & 0xFF)
                                self.tcp_timeout = 0
                        except Exception as e:
                            print("Inverter: " + str(e))
                            self.tcp_timeout += 1
                            return

                        # Block 1
                        try:
                            rr = self.tcp_client.read_holding_registers(0, 125, unit=1) #, timeout=0.25)         # Max 125 bytes

                            if rr.isError():
                                self.tcp_timeout += 1
                                return
                            else:
                                if len(rr.registers) >= 125:
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
                        except Exception as e:
                            print("Inverter: " + str(e))
                            self.tcp_timeout += 1
                            return

                        # Block 2
                        try:
                            rr = self.tcp_client.read_holding_registers(200, 125, unit=1)   #, timeout=0.25)

                            if rr.isError():
                                self.tcp_timeout += 1
                                return
                            else:
                                if len(rr.registers) >= 125:
                                    self.inverter_operating_mode = rr.registers[46] & 0x01

                                self.tcp_timeout = 0
                        except Exception as e:
                            print("Inverter: " + str(e))
                            self.tcp_timeout += 1
                            return

                        # Block 3
                        try:
                            rr = self.tcp_client.read_holding_registers(3100, 125, unit=1)  #, timeout=0.25)
                            
                            if rr.isError():
                                self.tcp_timeout += 1
                                return
                            else:
                                if len(rr.registers) >= 125:
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
                        except Exception as e:
                            print("Inverter: " + str(e))
                            self.tcp_timeout += 1
                            return

                        # WARNINGS
                        #if (self.inverter_warnings2 >> 0) & 0x01:
                        #    self.update_warnings(Warnings.CAN_WARNING.value, True)
                        #else:
                        #    self.update_warnings(Warnings.CAN_WARNING.value, False)
                            
                        #if (self.inverter_warnings2 >> 1) & 0x01:
                        #    self.update_warnings(Warnings.CAN_ERROR_PASSIVE.value, True)
                        #else:
                        #    self.update_warnings(Warnings.CAN_ERROR_PASSIVE.value, False)
                            
                        if (self.inverter_warnings2 >> 2) & 0x01:
                            self.update_warnings(Warnings.FILTER_OVERTEMP.value, True)
                            print("Inverter UID " + str(self.uid) + ": Filter Overtemp Warning at " + str(datetime.now()))
                        else:
                            self.update_warnings(Warnings.FILTER_OVERTEMP.value, False)
                        
                        if (self.inverter_warnings2 >> 3) & 0x01:
                            self.update_warnings(Warnings.FAN_CIRCUIT.value, True)
                            print("Inverter UID " + str(self.uid) + ": Fan Circuit Warning at " + str(datetime.now()))
                        else:
                            self.update_warnings(Warnings.FAN_CIRCUIT.value, False)
                        
                        if (self.inverter_warnings2 >> 4) & 0x01:
                            self.update_warnings(Warnings.EE_SAVE_IN_PROGRESS.value, True)
                            print("Inverter UID " + str(self.uid) + ": EE Save In Progress Warning at " + str(datetime.now()))
                        else:
                            self.update_warnings(Warnings.EE_SAVE_IN_PROGRESS.value, False)
                            
                        if (self.inverter_warnings2 >> 5) & 0x01:
                            self.update_warnings(Warnings.LOCAL_NETWORK_MISMATCH.value, True)
                            print("Inverter UID " + str(self.uid) + ": Local Network Mismatch Warning at " + str(datetime.now()))
                        else:
                            self.update_warnings(Warnings.LOCAL_NETWORK_MISMATCH.value, False)
                            
                        if (self.inverter_warnings2 >> 6) & 0x01:
                            self.update_warnings(Warnings.REMOTE_NETWORK_MISMATCH.value, True)
                            print("Inverter UID " + str(self.uid) + ": Remote Network Mismatch Warning at " + str(datetime.now()))
                        else:
                            self.update_warnings(Warnings.REMOTE_NETWORK_MISMATCH.value, False)
                            
                        if (self.inverter_warnings2 >> 7) & 0x01:
                            self.update_warnings(Warnings.CONDENSATION.value, True)
                            print("Inverter UID " + str(self.uid) + ": Condensation Warning at " + str(datetime.now()))
                        else:
                            self.update_warnings(Warnings.CONDENSATION.value, False)
                            
                        if (self.inverter_warnings2 >> 8) & 0x01:
                            self.update_warnings(Warnings.I2C.value, True)
                            print("Inverter UID " + str(self.uid) + ": I2C Warning at " + str(datetime.now()))
                        else:
                            self.update_warnings(Warnings.I2C.value, False)
                            
                        if (self.inverter_warnings2 >> 9) & 0x01:
                            self.update_warnings(Warnings.EXTERNAL_INHIBIT.value, True)
                            print("Inverter UID " + str(self.uid) + ": External Inhibit Warning at " + str(datetime.now()))
                        else:
                            self.update_warnings(Warnings.EXTERNAL_INHIBIT.value, False)
                        
                        if (self.inverter_warnings2 >> 10) & 0x01:
                            self.update_warnings(Warnings.MAINTENANCE_REQUIRED.value, True)
                            print("Inverter UID " + str(self.uid) + ": Maintenance Required Warning at " + str(datetime.now()))
                        else:
                            self.update_warnings(Warnings.MAINTENANCE_REQUIRED.value, False)
                            
                        if (self.inverter_warnings2 >> 11) & 0x01:
                            self.update_warnings(Warnings.GROUND_FAULT.value, True)
                            print("Inverter UID " + str(self.uid) + ": Ground Fault Warning at " + str(datetime.now()))
                        else:
                            self.update_warnings(Warnings.GROUND_FAULT.value, False)
                            
                        if (self.inverter_warnings2 >> 12) & 0x01:
                            self.update_warnings(Warnings.FUSE.value, True)
                            print("Inverter UID " + str(self.uid) + ": Fuse Warning at " + str(datetime.now()))
                        else:
                            self.update_warnings(Warnings.FUSE.value, False)
                            
                        if (self.inverter_warnings2 >> 13) & 0x01:
                            self.update_warnings(Warnings.AC_DISCONNECT.value, True)
                            print("Inverter UID " + str(self.uid) + ": AC Disconnect Warning at " + str(datetime.now()))
                        else:
                            self.update_warnings(Warnings.AC_DISCONNECT.value, False)
                        

                        # FAULTS
                        # EPC inverters have no Alarms, so the most common are in "Faults" and the less common in "Alarms"...which will likely fault anyway...
                        if (self.inverter_faults1 >> 0) & 0x01:
                            self.update_alarms(Alarms.CONTROL_BOARD_VOLTAGE.value, True)
                            print("Inverter UID " + str(self.uid) + ": Control Board Voltage Fault at " + str(datetime.now()))
                        else:
                            self.update_alarms(Alarms.CONTROL_BOARD_VOLTAGE.value, False)
                            
                        if (self.inverter_faults1 >> 1) & 0x01:
                            self.update_alarms(Alarms.POWER_CHANNEL_IMBALANCE.value, True)
                            print("Inverter UID " + str(self.uid) + ": Imbalance Fault at " + str(datetime.now()))
                        else:
                            self.update_alarms(Alarms.POWER_CHANNEL_IMBALANCE.value, False)
                            
                        if (self.inverter_faults1 >> 2) & 0x01:
                            self.update_alarms(Alarms.CURRENT_RISE.value, True)
                            print("Inverter UID " + str(self.uid) + ": Current Rise Fault at " + str(datetime.now()))
                        else:
                            self.update_alarms(Alarms.CURRENT_RISE.value, False)
                            
                        if (self.inverter_faults1 >> 3) & 0x01:
                            self.update_faults(Faults.COOLING_FLOW.value, True)
                            print("Inverter UID " + str(self.uid) + ": Cooling Flow Fault at " + str(datetime.now()))
                        else:
                            self.update_faults(Faults.COOLING_FLOW.value, False)
                            
                        if (self.inverter_faults1 >> 4) & 0x01:
                            self.update_alarms(Alarms.THERMAL_OVERLOAD.value, True)
                            print("Inverter UID " + str(self.uid) + ": Thermal Overload Fault at " + str(datetime.now()))
                        else:
                            self.update_alarms(Alarms.THERMAL_OVERLOAD.value, False)
                            
                        if (self.inverter_faults1 >> 5) & 0x01:
                            self.update_alarms(Alarms.FAN_CIRCUIT.value, True)
                            print("Inverter UID " + str(self.uid) + ": Fan Circuit Fault at " + str(datetime.now()))
                        else:
                            self.update_alarms(Alarms.FAN_CIRCUIT.value, False)
                            
                        if (self.inverter_faults1 >> 6) & 0x01:
                            self.update_faults(Faults.POR_TIMEOUT.value, True)
                            print("Inverter UID " + str(self.uid) + ": POR Timeout Fault at " + str(datetime.now()))
                        else:
                            self.update_faults(Faults.POR_TIMEOUT.value, False)
                            
                        if (self.inverter_faults1 >> 7) & 0x01:
                            self.update_faults(Faults.CONDENSATION.value, True)
                            print("Inverter UID " + str(self.uid) + ": Condensation Fault at " + str(datetime.now()))
                        else:
                            self.update_faults(Faults.CONDENSATION.value, False)
                            
                        if (self.inverter_faults1 >> 8) & 0x01:
                            self.update_faults(Faults.I2C_COMMS.value, True)
                            print("Inverter UID " + str(self.uid) + ": I2C Comms Fault at " + str(datetime.now()))
                        else:
                            self.update_faults(Faults.I2C_COMMS.value, False)
                            
                        if (self.inverter_faults1 >> 9) & 0x01:
                            self.update_faults(Faults.EXTERNAL_INHIBIT.value, True)
                            print("Inverter UID " + str(self.uid) + ": External Inhibit Fault at " + str(datetime.now()))
                        else:
                            self.update_faults(Faults.EXTERNAL_INHIBIT.value, False)
                            
                        if (self.inverter_faults1 >> 10) & 0x01:
                            self.update_faults(Faults.GROUND_FAULT.value, True)
                            print("Inverter UID " + str(self.uid) + ": Ground Fault at " + str(datetime.now()))
                        else:
                            self.update_faults(Faults.GROUND_FAULT.value, False)
                            
                        if (self.inverter_faults1 >> 11) & 0x01:
                            self.update_faults(Faults.FUSE.value, True)
                            print("Inverter UID " + str(self.uid) + ": Fuse Fault at " + str(datetime.now()))
                        else:
                            self.update_faults(Faults.FUSE.value, False)
                            
                        if (self.inverter_faults1 >> 12) & 0x01:
                            self.update_alarms(Alarms.BASEPLATE_OVERTEMP.value, True)
                            print("Inverter UID " + str(self.uid) + ": Baseplate Fault at " + str(datetime.now()))
                        else:
                            self.update_alarms(Alarms.BASEPLATE_OVERTEMP.value, False)
                            
                        if (self.inverter_faults1 >> 13) & 0x01:
                            self.update_alarms(Alarms.AC_DISCONNECT_SWITCH.value, True)
                            print("Inverter UID " + str(self.uid) + ": AC Disconnect Fault at " + str(datetime.now()))
                        else:
                            self.update_alarms(Alarms.AC_DISCONNECT_SWITCH.value, False)
                            
                        if (self.inverter_faults1 >> 14) & 0x01:
                            self.update_alarms(Alarms.INTERNAL_OVERTEMP.value, True)
                            print("Inverter UID " + str(self.uid) + ": Internal Overtemp Fault at " + str(datetime.now()))
                        else:
                            self.update_alarms(Alarms.INTERNAL_OVERTEMP.value, False)

                        if (self.inverter_faults1 >> 15) & 0x01:
                            self.update_faults(Faults.UNDERTEMP.value, True)
                            print("Inverter UID " + str(self.uid) + ": Undertemp Fault at " + str(datetime.now()))
                        else:
                            self.update_faults(Faults.UNDERTEMP.value, False)

                        if (self.inverter_faults2 >> 0) & 0x01:
                            self.update_faults(Faults.ESTOP_SHUTDOWN.value, True)
                            print("Inverter UID " + str(self.uid) + ": E-Stop Shutdown Fault at " + str(datetime.now()))
                        else:
                            self.update_faults(Faults.ESTOP_SHUTDOWN.value, False)
                            
                        if (self.inverter_faults2 >> 1) & 0x01:
                            self.update_faults(Faults.OVERCURRENT.value, True)              # AC Overcurrent
                            print("Inverter UID " + str(self.uid) + ": AC Overcurrent Fault at " + str(datetime.now()))
                        else:
                            self.update_faults(Faults.OVERCURRENT.value, False) 
                            
                        if (self.inverter_faults2 >> 2) & 0x01:
                            self.update_faults(Faults.OVERCURRENT.value, True)              # DC Overcurrent, sharing same bit in SCADA
                            print("Inverter UID " + str(self.uid) + ": DC Overcurrent Fault at " + str(datetime.now()))
                        else:
                            self.update_faults(Faults.OVERCURRENT.value, False)
                            
                        if (self.inverter_faults2 >> 3) & 0x01:
                            self.update_faults(Faults.OVERVOLTAGE.value, True)              # DC Overvoltage
                            print("Inverter UID " + str(self.uid) + ": DC Overvoltage Fault at " + str(datetime.now()))
                        else:
                            self.update_faults(Faults.OVERVOLTAGE.value, False)
                            
                        if (self.inverter_faults2 >> 4) & 0x01:
                            self.update_alarms(Alarms.DEVICE_OVERTEMP.value, True)
                            print("Inverter UID " + str(self.uid) + ": Device Overtemp Fault at " + str(datetime.now()))
                        else:
                            self.update_alarms(Alarms.DEVICE_OVERTEMP.value, False)
                            
                        if (self.inverter_faults1 >> 5) & 0x01:
                            self.update_alarms(Alarms.INVERTER_OVERTEMP.value, True)
                            print("Inverter UID " + str(self.uid) + ": Inverter Overtemp Fault at " + str(datetime.now()))
                        else:
                            self.update_alarms(Alarms.INVERTER_OVERTEMP.value, False)
                            
                        if (self.inverter_faults2 >> 6) & 0x01:
                            self.update_faults(Faults.INVALID_COMMAND_MESSAGE.value, True)  # LOSS OF MODBUS-TCP / CAN COMMS
                            print("Inverter UID " + str(self.uid) + ": Invalid Command Message Fault at " + str(datetime.now()))
                        else:
                            self.update_faults(Faults.INVALID_COMMAND_MESSAGE.value, False)
                            
                        if (self.inverter_faults2 >> 7) & 0x01:
                            self.update_faults(Faults.UNDERVOLTAGE.value, True)             # DC UNDERVOLTAGE
                            print("Inverter UID " + str(self.uid) + ": DC Undervoltage Fault at " + str(datetime.now()))
                        else:
                            self.update_faults(Faults.UNDERVOLTAGE.value, False)
                            
                        if (self.inverter_faults2 >> 8) & 0x01:
                            self.update_faults(Faults.OVERVOLTAGE.value, True)              # AC Overvoltage, sharing same bit in SCADA
                            print("Inverter UID " + str(self.uid) + ": AC Overvoltage Fault at " + str(datetime.now()))
                        else:
                            self.update_faults(Faults.OVERVOLTAGE.value, False)
                            
                        if (self.inverter_faults2 >> 9) & 0x01:
                            self.update_alarms(Alarms.ILLEGAL_TRANSITION.value, True)
                            print("Inverter UID " + str(self.uid) + ": DC Timed Overload Fault at " + str(datetime.now()))
                        else:
                            self.update_alarms(Alarms.ILLEGAL_TRANSITION.value, False)
                            
                        if (self.inverter_faults2 >> 10) & 0x01:
                            self.update_alarms(Alarms.BAD_EEPROM.value, True)               # Bad EE Header
                            print("Inverter UID " + str(self.uid) + ": Bad EE Header Fault at " + str(datetime.now()))
                        else:
                            self.update_alarms(Alarms.BAD_EEPROM.value, False)
                            
                        if (self.inverter_faults2 >> 11) & 0x01:
                            self.update_alarms(Alarms.BAD_EEPROM.value, True)               # Bad EE Section, sharing same bit in SCADA
                            print("Inverter UID " + str(self.uid) + ": Bad Eeprom Fault at " + str(datetime.now()))
                        else:
                            self.update_alarms(Alarms.BAD_EEPROM.value, False)
                            
                        if (self.inverter_faults2 >> 12) & 0x01:
                            self.update_alarms(Alarms.COOLING_SYSTEM.value, True)
                            print("Inverter UID " + str(self.uid) + ": Cooling System Fault at " + str(datetime.now()))
                        else:
                            self.update_alarms(Alarms.COOLING_SYSTEM.value, False)
                            
                        if (self.inverter_faults2 >> 13) & 0x01:
                            self.update_alarms(Alarms.AC_TIMED_OVERLOAD.value, True)
                            print("Inverter UID " + str(self.uid) + ": AC Timed Overload Fault at " + str(datetime.now()))
                        else:
                            self.update_alarms(Alarms.AC_TIMED_OVERLOAD.value, False)
                            
                        if (self.inverter_faults2 >> 14) & 0x01:
                            self.update_alarms(Alarms.DC_TIMED_OVERLOAD.value, True)
                            print("Inverter UID " + str(self.uid) + ": DC Timed Overload Fault at " + str(datetime.now()))
                        else:
                            self.update_alarms(Alarms.DC_TIMED_OVERLOAD.value, False)
                            
                        if (self.inverter_faults2 >> 15) & 0x01:
                            self.update_alarms(Alarms.TIMED_CIRC_CURRENT.value, True)
                            print("Inverter UID " + str(self.uid) + ": Timed Circulation Fault at " + str(datetime.now()))
                        else:
                            self.update_alarms(Alarms.TIMED_CIRC_CURRENT.value, False)
                        
                        # Inverter control and operation
                        try:
                            # Keep updating the inverter control reg to keep it happy
                            if self.inv_conn:
                                self.tcp_client.write_register(246, 1, unit=1)

                                if self.inverter_operating_state == 2 or \
                                   self.inverter_operating_state == 4:
                                    # Inverter in Following or Forming mode
                                    self.enabled_echo = True
                            else:
                                self.tcp_client.write_register(246, 0, unit=1)
                                
                                if self.inverter_operating_state != 2 and \
                                    self.inverter_operating_state != 4:

                                    # Zero the commanded power when not in Following mode
                                    self.tcp_client.write_register(3215, self.int_to_twos_comp(0), unit=1)
                                    self.enabled_echo = False

                            self.tcp_timeout = 0
                        except Exception as e:
                            print("Inverter: " + str(e))
                            self.tcp_timeout += 1
                            return

                        # Allow power control in Following mode
                        if self.inverter_operating_state == 2:
                            if self.inverter_command_real_power != int(self.inverter_commanded_real_power):
                                try:
                                    if self.faults > 0:
                                        self.tcp_client.write_register(3215, self.int_to_twos_comp(0), unit=1)
                                    else:
                                        self.tcp_client.write_register(3215, self.int_to_twos_comp(self.inverter_command_real_power*10), unit=1)
                                    self.tcp_timeout = 0
                                except Exception as e:
                                    print("Inverter: " + str(e))
                                    self.tcp_timeout += 1
                                    return
                        else:
                            if int(self.inverter_commanded_real_power) > 0:
                                try:
                                    self.tcp_client.write_register(3215, self.int_to_twos_comp(0), unit=1)
                                    self.tcp_timeout = 0
                                except Exception as e:
                                    print("Inverter: " + str(e))
                                    self.tcp_timeout += 1
                                    return

                        # Fault rectification - Ensures the fault light isn't on when the system is idle. Simplifies startup and customerd like this.
                        # This works really well! Unfortunately right now it clears the faults before we even have chance to report them!
                        if self.inverter_operating_state == 3:  # and self.fault_timeout_counter >= self.alert_timeout:
                            if self.fault_clear_timeout_counter < self.fault_clear_timeout:
                                self.fault_clear_timeout_counter += 1
                            else:
                                self.fault_clear_timeout_counter = 0

                                # Being able to clear the fault depends entirely on the type of fault.

                                # If it's an undervoltage DC fault, we can't clear it

                                # Disable Inverter
                                try:
                                    rr = self.tcp_client.read_holding_registers(246, 1, unit=1)
                                    
                                    if rr.isError():
                                        self.tcp_timeout += 1
                                        return
                                    else:
                                        if len(rr.registers) >= 1:
                                            conn = rr.registers[0]

                                            if conn & 0x01:  # System is in an enable state after it faulted
                                                conn = 0
                                                self.tcp_client.write_register(246, conn, unit=1)  # Clear enable bit before clearing faults

                                        self.tcp_timeout = 0
                                except Exception as e:
                                    print("Inverter: " + str(e))
                                    self.tcp_timeout += 1
                                    return

                                # Clear Faults (0x01) and Warnings (0x02)
                                if self.auto_fault_clear == "Auto" or self.manual_fault_clear:
                                    self.manual_fault_clear = False                                 # Reset it in either case
                                    try:
                                        cmd = 3
                                        self.tcp_client.write_register(3214, cmd, unit=1)

                                        self.tcp_timeout = 0
                                    except Exception as e:
                                        print("Inverter: " + str(e))
                                        self.tcp_timeout += 1
                                        return

                    elif self.dbData["inverter_software_hash"] == "A44AD40":
                        # Model
                        try:
                            rr = self.tcp_client.read_holding_registers(20, 4, unit=1, timeout=0.25)
                            self.name = chr(rr.registers[0] >> 8) + chr(rr.registers[0] & 0xFF) + chr(rr.registers[1] >> 8) + chr(rr.registers[1] & 0xFF) + \
                                chr(rr.registers[2] >> 8) + chr(rr.registers[2] & 0xFF) + chr(rr.registers[3] >> 8) + chr(rr.registers[3] & 0xFF)
                            self.tcp_timeout = 0
                        except Exception as e:
                            print("Inverter: " + str(e))
                            self.tcp_timeout += 1
                            return

                        # Serial Number
                        try:
                            rr = self.tcp_client.read_holding_registers(52, 4, unit=1, timeout=0.25)
                            self.serial = chr(rr.registers[0] >> 8) + chr(rr.registers[0] & 0xFF) + chr(rr.registers[1] >> 8) + chr(
                                rr.registers[1] & 0xFF) + \
                                          chr(rr.registers[2] >> 8) + chr(rr.registers[2] & 0xFF) + chr(rr.registers[3] >> 8) + chr(rr.registers[3] & 0xFF)
                            self.tcp_timeout = 0
                        except Exception as e:
                            print("Inverter: " + str(e))
                            self.tcp_timeout += 1
                            return

                        # Block 1
                        try:
                            rr = self.tcp_client.read_holding_registers(0, 125, unit=1, timeout=0.25)  # Max 125 bytes

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
                        except Exception as e:
                            print("Inverter: " + str(e))
                            self.tcp_timeout += 1
                            return

                        # Block 2
                        try:
                            rr = self.tcp_client.read_holding_registers(200, 125, unit=1, timeout=0.25)

                            self.inverter_operating_mode = rr.registers[46] & 0x01

                            self.tcp_timeout = 0
                        except Exception as e:
                            print("Inverter: " + str(e))
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
                        except Exception as e:
                            print("Inverter: " + str(e))
                            self.tcp_timeout += 1
                            return

                        # WARNINGS
                        if self.inverter_warnings1 > 0 or self.inverter_warnings2 > 0:
                            if self.warning_timeout_counter < self.alert_timeout:
                                self.warning_timeout_counter += 1
                            else:
                                # if self.warning_timeout_counter >= self.alert_timeout:
                                # if (self.inverter_warnings2 >> 0) & 0x01:
                                #     self.update_warnings(Warnings.CAN_WARNING.value, True)
                                # if (self.inverter_warnings2 >> 1) & 0x01:
                                #     self.update_warnings(Warnings.CAN_ERROR_PASSIVE.value, True)
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
                                # self.update_warnings(Warnings.CAN_WARNING.value, False)
                                # self.update_warnings(Warnings.CAN_ERROR_PASSIVE.value, False)
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
                                self.update_warnings(Warnings.AC_DISCONNECT.value, False)

                            self.warning_timeout_counter = 0

                        # FAULTS
                        # EPC inverters have no Alarms, so the most common are in "Faults" and the less common in "Alarms"...which will likely fault anyway...
                        print("inv faults: " + str(self.inverter_faults1) + " " +  str(self.inverter_faults2))
                        if self.inverter_faults1 > 0 or self.inverter_faults2 > 0:

                            if self.fault_timeout_counter < self.alert_timeout:
                                self.fault_timeout_counter += 1
                            else:
                                # if self.fault_timeout_counter >= self.alert_timeout:
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
                                    self.update_faults(Faults.OVERCURRENT.value, True)  # AC Overcurrent
                                if (self.inverter_faults2 >> 2) & 0x01:
                                    self.update_faults(Faults.OVERCURRENT.value, True)  # DC Overcurrent, sharing same bit in SCADA
                                if (self.inverter_faults2 >> 3) & 0x01:
                                    self.update_faults(Faults.OVERVOLTAGE.value, True)  # DC Overvoltage
                                if (self.inverter_faults2 >> 4) & 0x01:
                                    self.update_alarms(Alarms.DEVICE_OVERTEMP.value, True)
                                if (self.inverter_faults1 >> 5) & 0x01:
                                    self.update_alarms(Alarms.INVERTER_OVERTEMP.value, True)
                                if (self.inverter_faults2 >> 6) & 0x01:
                                    self.update_faults(Faults.INVALID_COMMAND_MESSAGE.value, True)  # LOSS OF MODBUS-TCP / CAN COMMS
                                if (self.inverter_faults2 >> 7) & 0x01:
                                    self.update_faults(Faults.UNDERVOLTAGE.value, True)  # DC UNDERVOLTAGE
                                if (self.inverter_faults2 >> 8) & 0x01:
                                    self.update_faults(Faults.OVERVOLTAGE.value, True)  # AC Overvoltage, sharing same bit in SCADA
                                if (self.inverter_faults2 >> 9) & 0x01:
                                    self.update_alarms(Alarms.ILLEGAL_TRANSITION.value, True)
                                if (self.inverter_faults2 >> 10) & 0x01:
                                    self.update_alarms(Alarms.BAD_EEPROM.value, True)  # Bad EE Header
                                if (self.inverter_faults2 >> 11) & 0x01:
                                    self.update_alarms(Alarms.BAD_EEPROM.value, True)  # Bad EE Section, sharing same bit in SCADA
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

                        # Inverter control and operation
                        try:
                            # Keep updating the inverter control reg to keep it happy
                            if self.inv_conn:
                                self.tcp_client.write_register(246, 1, unit=1)

                                if self.inverter_operating_state == 2 or \
                                        self.inverter_operating_state == 4:
                                    # Inverter in Following or Forming mode
                                    self.enabled_echo = True
                            else:
                                self.tcp_client.write_register(246, 0, unit=1)

                                if self.inverter_operating_state != 2 and \
                                        self.inverter_operating_state != 4:

                                    # Zero the commanded power when not in Following mode
                                    self.tcp_client.write_register(3215, self.int_to_twos_comp(0), unit=1)
                                    self.enabled_echo = False

                            self.tcp_timeout = 0
                        except Exception as e:
                            print("Inverter: " + str(e))
                            self.tcp_timeout += 1
                            return

                        # Allow power control in Following mode
                        if self.inverter_operating_state == 2:
                            if self.inverter_command_real_power != int(self.inverter_commanded_real_power):
                                try:
                                    if self.faults > 0:
                                        self.tcp_client.write_register(3215, self.int_to_twos_comp(0), unit=1)
                                    else:
                                        self.tcp_client.write_register(3215, self.int_to_twos_comp(self.inverter_command_real_power * 10), unit=1)
                                    self.tcp_timeout = 0
                                except Exception as e:
                                    print("Inverter: " + str(e))
                                    self.tcp_timeout += 1
                                    return
                        else:
                            if int(self.inverter_commanded_real_power) > 0:
                                try:
                                    self.tcp_client.write_register(3215, self.int_to_twos_comp(0), unit=1)
                                    self.tcp_timeout = 0
                                except Exception as e:
                                    print("Inverter: " + str(e))
                                    self.tcp_timeout += 1
                                    return

                        # Fault rectification - Ensures the fault light isn't on when the system is idle. Simplifies startup and customerd like this.
                        # This works really well! Unfortunately right now it clears the faults before we even have chance to report them!
                        if self.inverter_operating_state == 3:  # and self.fault_timeout_counter >= self.alert_timeout:
                            if self.fault_clear_timeout_counter < self.fault_clear_timeout:
                                self.fault_clear_timeout_counter += 1
                            else:
                                self.fault_clear_timeout_counter = 0

                                # Being able to clear the fault depends entirely on the type of fault.

                                # If it's an undervoltage DC fault, we can't clear it

                                # Disable Inverter
                                try:
                                    rr = self.tcp_client.read_holding_registers(246, 1, unit=1)
                                    conn = rr.registers[0]

                                    if conn & 0x01:  # System is in an enable state after it faulted
                                        conn = 0
                                        self.tcp_client.write_register(246, conn, unit=1)  # Clear enable bit before clearing faults

                                    self.tcp_timeout = 0
                                except Exception as e:
                                    print("Inverter: " + str(e))
                                    self.tcp_timeout += 1
                                    return

                                # Clear Faults (0x01) and Warnings (0x02)
                                try:
                                    cmd = 3
                                    self.tcp_client.write_register(3214, cmd, unit=1)

                                    self.tcp_timeout = 0
                                except Exception as e:
                                    print("Inverter: " + str(e))
                                    self.tcp_timeout += 1
                                    return

                    elif self.dbData["inverter_software_hash"] == "BF65B78":
                        # Model
                        try:
                            rr = self.tcp_client.read_holding_registers(20, 4, unit=1, timeout=0.25)
                            self.name = chr(rr.registers[0] >> 8) + chr(rr.registers[0] & 0xFF) + chr(rr.registers[1] >> 8) + chr(rr.registers[1] & 0xFF) + \
                                chr(rr.registers[2] >> 8) + chr(rr.registers[2] & 0xFF) + chr(rr.registers[3] >> 8) + chr(rr.registers[3] & 0xFF)
                            self.tcp_timeout = 0
                        except Exception as e:
                            print("Inverter: " + str(e))
                            self.tcp_timeout += 1
                            return

                        # Serial Number
                        try:
                            rr = self.tcp_client.read_holding_registers(52, 4, unit=1, timeout=0.25)
                            self.serial = chr(rr.registers[0] >> 8) + chr(rr.registers[0] & 0xFF) + chr(rr.registers[1] >> 8) + chr(
                                rr.registers[1] & 0xFF) + \
                                          chr(rr.registers[2] >> 8) + chr(rr.registers[2] & 0xFF) + chr(rr.registers[3] >> 8) + chr(rr.registers[3] & 0xFF)
                            self.tcp_timeout = 0
                        except Exception as e:
                            print("Inverter: " + str(e))
                            self.tcp_timeout += 1
                            return

                        # Block 1
                        try:
                            rr = self.tcp_client.read_holding_registers(0, 125, unit=1, timeout=0.25)  # Max 125 bytes

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
                        except Exception as e:
                            print("Inverter: " + str(e))
                            self.tcp_timeout += 1
                            return

                        # Block 2
                        try:
                            rr = self.tcp_client.read_holding_registers(200, 125, unit=1, timeout=0.25)

                            self.inverter_operating_mode = rr.registers[46] & 0x01

                            self.tcp_timeout = 0
                        except Exception as e:
                            print("Inverter: " + str(e))
                            self.tcp_timeout += 1
                            return

                        # Block 3
                        try:
                            rr = self.tcp_client.read_holding_registers(3400, 125, unit=1, timeout=0.25)

                            self.inverter_faults1 = rr.registers[31]
                            self.inverter_faults2 = rr.registers[32]

                            self.inverter_warnings1 = rr.registers[33]
                            self.inverter_warnings2 = rr.registers[34]

                            sf = self.twos_comp_to_int(rr.registers[76])
                            self.pV = self.inverter_ac_voltage = self.twos_comp_to_int(rr.registers[43]) * pow(10, sf)

                            sf = self.twos_comp_to_int(rr.registers[76])
                            self.inverter_input_voltage = self.twos_comp_to_int(rr.registers[52]) * pow(10, sf)

                            sf = self.twos_comp_to_int(rr.registers[80])
                            self.inverter_inlet_temperature = self.twos_comp_to_int(rr.registers[55]) * pow(10, sf)

                            sf = self.twos_comp_to_int(rr.registers[78])
                            self.pA = self.inverter_real_current = self.twos_comp_to_int(rr.registers[63]) * pow(10, sf)

                            sf = self.twos_comp_to_int(rr.registers[78])
                            self.inverter_reactive_current = self.twos_comp_to_int(rr.registers[64]) * pow(10, sf)

                            sf = self.twos_comp_to_int(rr.registers[75])
                            self.inverter_commanded_real_power = self.twos_comp_to_int(rr.registers[97]) * pow(10, sf)

                            sf = self.twos_comp_to_int(rr.registers[75])
                            self.inverter_commanded_reactive_power = self.twos_comp_to_int(rr.registers[98]) * pow(10, sf)

                            sf = self.twos_comp_to_int(rr.registers[78])
                            self.inverter_commanded_real_current = self.twos_comp_to_int(rr.registers[99]) * pow(10, sf)

                            sf = self.twos_comp_to_int(rr.registers[78])
                            self.inverter_commanded_reactive_current = self.twos_comp_to_int(rr.registers[100]) * pow(10, sf)

                            sf = self.twos_comp_to_int(rr.registers[78])
                            self.inverter_commanded_input_current = self.twos_comp_to_int(rr.registers[101]) * pow(10, sf)

                            self.tcp_timeout = 0
                        except Exception as e:
                            print("Inverter: " + str(e))
                            self.tcp_timeout += 1
                            return

                        # WARNINGS
                        if self.inverter_warnings1 > 0 or self.inverter_warnings2 > 0:
                            if self.warning_timeout_counter < self.alert_timeout:
                                self.warning_timeout_counter += 1
                            else:
                                # if self.warning_timeout_counter >= self.alert_timeout:
                                # if (self.inverter_warnings2 >> 0) & 0x01:
                                #     self.update_warnings(Warnings.CAN_WARNING.value, True)
                                # if (self.inverter_warnings2 >> 1) & 0x01:
                                #     self.update_warnings(Warnings.CAN_ERROR_PASSIVE.value, True)
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
                                # self.update_warnings(Warnings.CAN_WARNING.value, False)
                                # self.update_warnings(Warnings.CAN_ERROR_PASSIVE.value, False)
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
                                self.update_warnings(Warnings.AC_DISCONNECT.value, False)

                            self.warning_timeout_counter = 0

                        # FAULTS
                        # EPC inverters have no Alarms, so the most common are in "Faults" and the less common in "Alarms"...which will likely fault anyway...
                        if self.inverter_faults1 > 0 or self.inverter_faults2 > 0:

                            if self.fault_timeout_counter < self.alert_timeout:
                                self.fault_timeout_counter += 1
                            else:
                                # if self.fault_timeout_counter >= self.alert_timeout:
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
                                    self.update_faults(Faults.OVERCURRENT.value, True)  # AC Overcurrent
                                if (self.inverter_faults2 >> 2) & 0x01:
                                    self.update_faults(Faults.OVERCURRENT.value, True)  # DC Overcurrent, sharing same bit in SCADA
                                if (self.inverter_faults2 >> 3) & 0x01:
                                    self.update_faults(Faults.OVERVOLTAGE.value, True)  # DC Overvoltage
                                if (self.inverter_faults2 >> 4) & 0x01:
                                    self.update_alarms(Alarms.DEVICE_OVERTEMP.value, True)
                                if (self.inverter_faults1 >> 5) & 0x01:
                                    self.update_alarms(Alarms.INVERTER_OVERTEMP.value, True)
                                if (self.inverter_faults2 >> 6) & 0x01:
                                    self.update_faults(Faults.INVALID_COMMAND_MESSAGE.value, True)  # LOSS OF MODBUS-TCP / CAN COMMS
                                if (self.inverter_faults2 >> 7) & 0x01:
                                    self.update_faults(Faults.UNDERVOLTAGE.value, True)  # DC UNDERVOLTAGE
                                if (self.inverter_faults2 >> 8) & 0x01:
                                    self.update_faults(Faults.OVERVOLTAGE.value, True)  # AC Overvoltage, sharing same bit in SCADA
                                if (self.inverter_faults2 >> 9) & 0x01:
                                    self.update_alarms(Alarms.ILLEGAL_TRANSITION.value, True)
                                if (self.inverter_faults2 >> 10) & 0x01:
                                    self.update_alarms(Alarms.BAD_EEPROM.value, True)  # Bad EE Header
                                if (self.inverter_faults2 >> 11) & 0x01:
                                    self.update_alarms(Alarms.BAD_EEPROM.value, True)  # Bad EE Section, sharing same bit in SCADA
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

                        # Inverter control and operation
                        try:
                            # Keep updating the inverter control reg to keep it happy
                            if self.inv_conn:
                                self.tcp_client.write_register(246, 1, unit=1)

                                if self.inverter_operating_state == 2 or \
                                        self.inverter_operating_state == 4:
                                    # Inverter in Following or Forming mode
                                    self.enabled_echo = True
                            else:
                                self.tcp_client.write_register(246, 0, unit=1)

                                if self.inverter_operating_state != 2 and \
                                        self.inverter_operating_state != 4:

                                    # Zero the commanded power when not in Following mode
                                    self.tcp_client.write_register(3497, self.int_to_twos_comp(0), unit=1)
                                    self.enabled_echo = False

                            self.tcp_timeout = 0
                        except Exception as e:
                            print("Inverter: " + str(e))
                            self.tcp_timeout += 1
                            return

                        # Allow power control in Following mode
                        if self.inverter_operating_state == 2:
                            if self.inverter_command_real_power != int(self.inverter_commanded_real_power):
                                try:
                                    if self.faults > 0:
                                        self.tcp_client.write_register(3497, self.int_to_twos_comp(0), unit=1)
                                    else:
                                        self.tcp_client.write_register(3497, self.int_to_twos_comp(self.inverter_command_real_power * 10), unit=1)
                                    self.tcp_timeout = 0
                                except Exception as e:
                                    print("Inverter: " + str(e))
                                    self.tcp_timeout += 1
                                    return
                        else:
                            if int(self.inverter_commanded_real_power) > 0:
                                try:
                                    self.tcp_client.write_register(3497, self.int_to_twos_comp(0), unit=1)
                                    self.tcp_timeout = 0
                                except Exception as e:
                                    print("Inverter: " + str(e))
                                    self.tcp_timeout += 1
                                    return

                        # Fault rectification - Ensures the fault light isn't on when the system is idle. Simplifies startup and customerd like this.
                        # This works really well! Unfortunately right now it clears the faults before we even have chance to report them!
                        if self.inverter_operating_state == 3:  # and self.fault_timeout_counter >= self.alert_timeout:
                            if self.fault_clear_timeout_counter < self.fault_clear_timeout:
                                self.fault_clear_timeout_counter += 1
                            else:
                                self.fault_clear_timeout_counter = 0

                                # Being able to clear the fault depends entirely on the type of fault.

                                # If it's an undervoltage DC fault, we can't clear it

                                # Disable Inverter
                                try:
                                    rr = self.tcp_client.read_holding_registers(246, 1, unit=1)
                                    conn = rr.registers[0]

                                    if conn & 0x01:  # System is in an enable state after it faulted
                                        conn = 0
                                        self.tcp_client.write_register(246, conn, unit=1)  # Clear enable bit before clearing faults

                                    self.tcp_timeout = 0
                                except Exception as e:
                                    print("Inverter: " + str(e))
                                    self.tcp_timeout += 1
                                    return

                                # Clear Faults (0x01) and Warnings (0x02)
                                try:
                                    cmd = 3
                                    self.tcp_client.write_register(3496, cmd, unit=1)

                                    self.tcp_timeout = 0
                                except Exception as e:
                                    print("Inverter: " + str(e))
                                    self.tcp_timeout += 1
                                    return

                    elif self.dbData["inverter_software_hash"] == "FEA5619":
                        pass

                    elif self.dbData["inverter_software_hash"] == "FE294E5":
                        pass

        # MSP PID Controller
        if self.pid_tcp_timeout == 5:
            self.pid_tcp_client = None

        if self.pid_enable and self.pid_tcp_client is None:
            if "inverter_pid_ipaddr_local" in self.dbData:
                if self.dbData["inverter_pid_ipaddr_local"] != "0.0.0.0":
                    try:
                        self.pid_tcp_client = modbus_client(self.dbData["inverter_pid_ipaddr_local"], port=502)

                        if self.pid_tcp_client.connect() is True:
                            self.update_faults(Faults.LOSS_OF_COMMS.value, False)  # Clear Fault
                            self.set_state_text(State.CONNECTED)
                            self.con_state = True
                        else:
                            self.update_faults(Faults.LOSS_OF_COMMS.value, True)  # Raise Fault
                            self.set_state_text(State.CONNECTING)
                            self.pid_tcp_client = None
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

        elif self.pid_enable:

            # Model
            try:
                rr = self.pid_tcp_client.read_holding_registers(0, 12, unit=1, timeout=0.25)
                print(rr.registers)
                pid_opstate = rr.registers[0]
                pid_heartbeat = rr.registers[1]
                pid_heartbeat_echo = rr.registers[2]
                pid_frequency = rr.registers[3]
                pid_real_power = rr.registers[7]
                pid_inverter_state = rr.registers[8]
                pid_commanded_power = rr.registers[9]
                pid_tesc_dc_enable = rr.registers[10]

                self.pid_outputs[0][0] = pid_opstate
                self.pid_outputs[0][1] = pid_heartbeat
                self.pid_outputs[0][2] = pid_heartbeat_echo                                         # The last written heartbeat echo
                self.pid_outputs[0][3] = pid_frequency
                self.pid_outputs[0][7] = pid_real_power
                self.pid_outputs[0][8] = pid_inverter_state
                self.pid_outputs[0][9] = pid_commanded_power

                self.tcp_client.write_register(2, pid_heartbeat, unit=1)                            # Echo back the heartbeat to keep the PID controller alive

                self.pid_tcp_timeout = 0
            except Exception as e:
                print("Inverter: " + str(e))
                self.pid_tcp_timeout += 1
                return

            #self.pid_outputs = self.tcp_client.read_holding_registers(0, 12, unit=1, timeout=0.25)

            #print(self.pid_outputs)

        if not self.override:
            if self.enabled:                                                                            # The controller is asking us to enable the inverter
                self.inv_conn = True
            else:
                self.inv_conn = False

        if len(self.actions) == 0:
            self.actions.append(0)  # Dummy

        # Modify self.outputs
        self.outputs[2][0] = self.inverter_heartbeat
        self.outputs[2][1] = self.inverter_operating_state
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

                elif "inverter_pid_enable" in form:
                    # TODO: We're going to have to save/load this in the db to ensure it's still enabled after a system restart
                    self.pid_enable = not self.pid_enable

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
                    
                    # Clear Faults (0x01) and Warnings (0x02)
                    if "inverter_fault_clearing" == control:
                        self.auto_fault_clear = form[control]

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
            mod_data["inverter_fault_clear"] = self.auto_fault_clear
            mod_data["inverter_start"] = self.inv_conn
            mod_data["inverter_enablestate"] = self.enabled
            mod_data["inverter_pid_enable"] = self.pid_enable
            mod_data["inverter_pid_data"] = self.pid_outputs[0]
            mod_data["inverter_ctlsrc"] = self.inverter_ctlsrc

            mod_data.update(self.dbData)                                                       # I'm appending the dict here so we don't save unnecessary
            return [SET_PAGE_ACK, mod_data]                                                                   # data to the database.

        else:
            return [SET_PAGE_ACK, ('OK', 200)]                                                                             # Return the data to be jsonified

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

    SCADA = 22      
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
