# Intesis INMBSMID008I000 - Midea Aircon Module (using the Intesis control interface - up to 8 Indoor ACs)
from pymodbus.client.sync import ModbusTcpClient as mb_tcp_client
import sys
from threading import Thread, Event
from FlexDB import FlexTinyDB
from enum import Enum
import serial.tools.list_ports as ports
from pymodbus.client.sync import ModbusSerialClient as mbUSBClient
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
        super(Module, self).__init__()

        # General Module Data
        self.author = "Sophie Coates"
        self.uid = uid  # A unique identifier passed from the HMI via a mangled URL
        self.icon = "/static/images/Aircon.png"
        self.name = "Midea Air Conditioning"
        self.module_type = ModTypes.AIRCON.value
        self.manufacturer = "Midea (Intesis)"
        self.model = ""
        self.options = ""
        self.version = "-"
        self.serial = "-"  # This can be replaced with the device serial number later
        self.website = "/Mod/FlexMod_MideaAircon"  # This is the template name itself

        # Run state
        self.con_state = False
        self.state = "Idle"
        self.set_state_text(State.IDLE)

        # Non-Volatile Data (Loaded from DB)
        self.dbData = db.fetch_from_db(__name__ + "_(" + str(self.uid) + ")")
        if self.dbData is None:
            self.dbData = dict()
            self.dbData["aircon_comport_local"] = "COM1"

        # Volatile Data
        self.rtu_client = None
        self.rtu_timeout = 0
        self.enabled = False
        self.enabled_echo = False
        self.inputs = [self.uid, False, 0]
        # self.outputs = [self.uid, self.enabled, [[0]*10]*8]
        self.outputs = [self.uid, self.enabled, [0] * 32]
        self.heartbeat = 0
        self.heartbeat_echo = 0
        self.comports = []
        for cp in list(ports.comports()):
            self.comports.append(cp.device)
        self.next_ac = 0
        self.aircon_regs = [[0, 0, 0, 0, 0, 0, 0, 0]] * 8  # Data stored as [aircon1][aircon2][aircon3][etc...]
        self.ac_enable = True
        # self.ac_enable_echo = 1
        self.reset = 0
        self.setpoint = 0
        self.fanmode = 0
        self.fanspeed = 0
        self.ac_warnings = 0
        self.ac_alarms = 0
        self.alert_timeout = 5
        self.warning_timeout_counter = 0
        self.alarm_timeout_counter = 0
        self.fault_timeout_counter = 0
        self.override = False

        # Device Volatiles
        self.aircon_quantity = 1
        self.aircon_heartbeat = 0
        self.aircon_fanspeed = 0
        self.aircon_fanmode = 0

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
        self.enabled = False

        self.aircon_states = [0]*8
        self.aircon_alerts = 0
        self.aircon_enables = 0
        self.hvac_offset = 0                                                                        # Support for old systems configured to start at address #1

        # Track Interval usage (GIL interference)
        self.start_time = time.time()
        self.interval_count = 0
        self.max_delay = 0
        self.last_time = 0

    def process(self):
        global loop_time
        #print("(12)  Aircon Heartbeat: " + str(self.heartbeat) + ", loop time: " + str(loop_time) + " Seconds")
        
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
            #print("HVAC " + str(self.uid) + ": " + str(self.interval_count))
        else:
            #print("HVAC " + str(self.uid) + ": " + str(self.interval_count) + " events in 1 hour. Maximum gap between events was " + str(self.max_delay) + " seconds.")
            pass
        
        s = time.time()
        
        self.heartbeat += 1
        if self.heartbeat >= 0xFFFF:
            self.heartbeat = 0
        self.aircon_heartbeat = self.heartbeat

        if self.rtu_timeout >= 5:
            self.rtu_client = None
            self.tcp_timeout = 0    # Without this we'll never escape the None state after the first error encountered.

        if self.rtu_client is None:
            self.con_state = False
            if "aircon_com_port" in self.dbData:
                if self.dbData["aircon_com_port"] != "":
                    try:
                        self.rtu_client = mbUSBClient(method="rtu", port=self.dbData["aircon_com_port"], baudrate=9600)

                        if self.rtu_client.connect() is True:
                            self.update_faults(Faults.LOSS_OF_COMMS.value, False)  # Clear Fault
                            self.set_state_text(State.CONNECTED)
                            self.con_state = True

                            for x in range(1, 9):
                                self.rtu_client.write_register(1000 + (20 * x), 0, unit=1)          # Force all off to reset the logic

                        else:
                            self.update_faults(Faults.LOSS_OF_COMMS.value, True)  # Raise Fault
                            self.set_state_text(State.CONNECTING)
                            self.rtu_client.close()
                            self.rtu_client = None
                            return

                    except Exception as e:
                        print("HVAC 1: " + str(e))
                        self.update_faults(Faults.LOSS_OF_COMMS.value, True)
                        self.set_state_text(State.CONNECTING)
                else:
                    self.update_faults(Faults.LOSS_OF_COMMS.value, True)  # Raise Fault
                    self.set_state_text(State.CONFIG)
            else:
                self.update_faults(Faults.LOSS_OF_COMMS.value, True)
                self.set_state_text(State.CONFIG)
        else:

            # Retrieve data from all the Indoor Aircon Units
            if "aircon_indoor_qty" in self.dbData:

                # Offset the address range to support hardware starting at address 1, not 0
                if "aircon_address_offset" in self.dbData:
                    self.hvac_offset = int(self.dbData["aircon_address_offset"])

                # Response from the HVAC system may be slow so we need to implement a state machine to track changes and not brute force it.
                for hvac in range(self.hvac_offset, int(self.dbData["aircon_indoor_qty"]) + self.hvac_offset):
                    if self.aircon_states[hvac] == 0:                                               # Discovery
                        try:
                            rr = self.rtu_client.read_holding_registers(1000 + (20 * hvac), 10, unit=1)

                            if rr.isError():
                                self.rtu_timeout += 1
                                return
                            else:
                                self.aircon_regs[hvac] = rr.registers

                                # Accumulate errors across all internal AC units
                                if rr.registers[7] < 100:
                                    if 0 < rr.registers[7] <= 16:
                                        self.ac_alarms |= (1 << rr.registers[7] - 1)

                                # Accumulate warnings across all internal AC units
                                if rr.registers[7] > 100:
                                    rr.registers[7] -= 100
                                    if 0 < rr.registers[7] <= 16:
                                        self.ac_warnings |= (1 << rr.registers[7] - 1)

                                self.aircon_alerts |= (1 << hvac)                                       # Indicate we've updated the alert status for this device
                                self.aircon_states[hvac] = 1

                                self.rtu_timeout = 0
                        except Exception as e:
                            print("HVAC 2: " + str(e))
                            self.rtu_timeout += 1
                            return

                    if self.aircon_states[hvac] == 1:                                               # Device enable
                        try:
                            rr = self.rtu_client.read_holding_registers(1000 + (20 * hvac), 1, unit=1)
                            
                            if rr.isError():
                                self.rtu_timeout += 1
                                return
                            else:
                                if rr.registers[0] == 0 and self.ac_enable:                             # Turn all the HVACs on one-by one
                                    self.rtu_client.write_register(1000 + (20 * hvac), 1, unit=1)
                                elif rr.registers[0] == 1 and not self.ac_enable:
                                    self.rtu_client.write_register(1000 + (20 * hvac), 0, unit=1)

                                if rr.registers[0] == 1:                                                # Go "Active" if they're all enabled
                                    self.aircon_enables |= (1 << hvac)
                                else:
                                    self.aircon_enables &= ~(1 << hvac)

                                self.rtu_timeout = 0
                                self.aircon_states[hvac] = 2
                        except Exception as e:
                            print("HVAC 3: " + str(e))
                            self.aircon_states[hvac] = 0
                            self.rtu_timeout += 1
                            return

                    if self.aircon_states[hvac] == 2:                                               # Fan Mode
                        try:
                            mode = 0
                            if "aircon_fan_mode" in self.dbData:
                                if self.dbData["aircon_fan_mode"] == "[0] Auto":
                                    mode = 0
                                elif self.dbData["aircon_fan_mode"] == "[1] Heat":
                                    mode = 1
                                elif self.dbData["aircon_fan_mode"] == "[2] Dry":
                                    mode = 2
                                elif self.dbData["aircon_fan_mode"] == "[3] Fan":
                                    mode = 3
                                elif self.dbData["aircon_fan_mode"] == "[4] Cool":
                                    mode = 4

                            rr = self.rtu_client.read_holding_registers(1001 + (20 * hvac), 1, unit=1)
                            
                            if rr.isError():
                                self.rtu_timeout += 1
                                return
                            else:
                                if rr.registers[0] != mode:
                                    self.rtu_client.write_register(1001 + (20 * hvac), mode, unit=1)
                                self.rtu_timeout = 0
                                self.aircon_states[hvac] = 3
                        except Exception as e:
                            print("HVAC 4: " + str(e))
                            self.aircon_states[hvac] = 0
                            self.rtu_timeout += 1
                            return

                    if self.aircon_states[hvac] == 3:                                               # Fan Speed
                        try:
                            speed = 0
                            if "aircon_fan_speed" in self.dbData:
                                if self.dbData["aircon_fan_speed"] == "[0] Auto":
                                    speed = 0
                                elif self.dbData["aircon_fan_speed"] == "[1] SP1":
                                    speed = 1
                                elif self.dbData["aircon_fan_speed"] == "[2] SP2":
                                    speed = 2
                                elif self.dbData["aircon_fan_speed"] == "[3] SP3":
                                    speed = 3

                            rr = self.rtu_client.read_holding_registers(1002 + (20 * hvac), 1, unit=1)
                            
                            if rr.isError():
                                self.rtu_timeout += 1
                                return
                            else:
                                if rr.registers[0] != speed:
                                    self.rtu_client.write_register(1002 + (20 * hvac), speed, unit=1)
                                self.rtu_timeout = 0
                                self.aircon_states[hvac] = 4
                        except Exception as e:
                            print("HVAC 5: " + str(e))
                            self.aircon_states[hvac] = 0
                            self.rtu_timeout += 1
                            return

                    if self.aircon_states[hvac] == 4:                                               # Temperature Setpoint
                        try:
                            temp = 20   # Default
                            if "aircon_temp_setpoint" in self.dbData:
                                temp = int(self.dbData["aircon_temp_setpoint"])
                            rr = self.rtu_client.read_holding_registers(1004 + (20 * hvac), 1, unit=1)
                            
                            if rr.isError():
                                self.rtu_timeout += 1
                                return
                            else:
                                if rr.registers[0] != temp:
                                    self.rtu_client.write_register(1004 + (20 * hvac), temp, unit=1)
                                self.rtu_timeout = 0

                                if self.reset == 1:
                                    self.aircon_states[hvac] = 5
                                elif self.version == "-":
                                    self.aircon_states[hvac] = 6
                                else:
                                    self.aircon_states[hvac] = 0                                        # Bypass reset state if not required
                        except Exception as e:
                            print("HVAC 6: " + str(e))
                            self.aircon_states[hvac] = 0
                            self.rtu_timeout += 1
                            return

                    if self.aircon_states[hvac] == 5:                                               # Intesis Module reset
                        try:
                            if self.reset == 1:

                                self.rtu_client.write_register(2099, self.reset, unit=1)
                                self.reset = 0
                            self.rtu_timeout = 0
                            self.aircon_states[hvac] = 0
                        except Exception as e:
                            print("HVAC 7: " + str(e))
                            self.aircon_states[hvac] = 0
                            self.rtu_timeout += 1
                            return

                    if self.aircon_states[hvac] == 6:                                               # Firmware Version
                        try:
                            rr = self.rtu_client.read_holding_registers(2050, 1, unit=1)
                            
                            if rr.isError():
                                self.rtu_timeout += 1
                                return
                            else:
                                self.version = rr.registers[0] / 100
                                self.rtu_timeout = 0
                                self.aircon_states[hvac] = 0
                        except Exception as e:
                            print("HVAC 8: " + str(e))
                            self.aircon_states[hvac] = 0
                            self.rtu_timeout += 1
                            return

                update_alerts = 0
                update_enables = 0

                for hvac in range(self.hvac_offset, int(self.dbData["aircon_indoor_qty"]) + self.hvac_offset):
                    if self.aircon_enables & (1 << hvac):
                        update_enables = 1
                    else:
                        update_enables = 0
                        break

                if update_enables == 1:
                    self.enabled_echo = True
                else:
                    self.enabled_echo = False

                # This confirms all HVACs have updated their current warnings and alarms
                for hvac in range(self.hvac_offset, int(self.dbData["aircon_indoor_qty"]) + self.hvac_offset):
                    if self.aircon_alerts & (1 << hvac):
                        update_alerts = 1
                    else:
                        update_alerts = 0
                        break

                if update_alerts == 1:
                    self.aircon_alerts = 0
                    # Warnings
                    if self.ac_warnings > 0:
                        if self.warning_timeout_counter < self.alert_timeout:
                            self.warning_timeout_counter += 1

                        if self.warning_timeout_counter >= self.alert_timeout:

                            if self.ac_warnings & (1 << 0):
                                self.update_warnings(Warnings.VAPORIZER.value, True)
                            if self.ac_warnings & (1 << 1):
                                self.update_warnings(Warnings.THAWING.value, True)
                            if self.ac_warnings & (1 << 2):
                                self.update_warnings(Warnings.CONDENSER_HIGH_TEMP.value, True)
                            if self.ac_warnings & (1 << 3):
                                self.update_warnings(Warnings.COMPRESSOR_TEMP.value, True)
                            if self.ac_warnings & (1 << 4):
                                self.update_warnings(Warnings.EVACUATION_DUCT_TEMP.value, True)
                            if self.ac_warnings & (1 << 5):
                                self.update_warnings(Warnings.DISCHARGE_HIGH_PRESSURE.value, True)
                            if self.ac_warnings & (1 << 6):
                                self.update_warnings(Warnings.DISCHARGE_LOW_PRESSURE.value, True)
                            if self.ac_warnings & (1 << 7):
                                self.update_warnings(Warnings.CURRENT_OVER_OR_UNDER_LOAD.value, False)
                            if self.ac_warnings & (1 << 8):
                                self.update_warnings(Warnings.COMPRESSOR_CURRENT_OVERLOAD.value, True)
                            if self.ac_warnings & (1 << 15):
                                self.update_warnings(Warnings.OTHER.value, True)

                            self.ac_warnings = 0
                    else:
                        if self.warnings > 0:
                            self.update_warnings(Warnings.VAPORIZER.value, False)
                            self.update_warnings(Warnings.THAWING.value, False)
                            self.update_warnings(Warnings.CONDENSER_HIGH_TEMP.value, False)
                            self.update_warnings(Warnings.COMPRESSOR_TEMP.value, False)
                            self.update_warnings(Warnings.EVACUATION_DUCT_TEMP.value, False)
                            self.update_warnings(Warnings.DISCHARGE_HIGH_PRESSURE.value, False)
                            self.update_warnings(Warnings.DISCHARGE_LOW_PRESSURE.value, False)
                            self.update_warnings(Warnings.CURRENT_OVER_OR_UNDER_LOAD.value, False)
                            self.update_warnings(Warnings.COMPRESSOR_CURRENT_OVERLOAD.value, False)
                            self.update_warnings(Warnings.OTHER.value, False)

                        self.warning_timeout_counter = 0

                    # Alarms
                    if self.ac_alarms > 0:
                        if self.alarm_timeout_counter < self.alert_timeout:
                            self.alarm_timeout_counter += 1

                        if self.alarm_timeout_counter >= self.alert_timeout:

                            if self.ac_alarms & (1 << 0):
                                self.update_alarms(Alarms.PHASE_ERROR.value, True)
                            if self.ac_alarms & (1 << 1):
                                self.update_alarms(Alarms.COMMS_ERROR.value, True)
                            if self.ac_alarms & (1 << 2):
                                self.update_alarms(Alarms.T1_SENSOR_ERROR.value, True)
                            if self.ac_alarms & (1 << 3):
                                self.update_alarms(Alarms.T2A_SENSOR_ERROR.value, True)
                            if self.ac_alarms & (1 << 4):
                                self.update_alarms(Alarms.T2B_SENSOR_ERROR.value, True)
                            if self.ac_alarms & (1 << 5):
                                self.update_alarms(Alarms.T3_AND_T4_SENSOR_ERROR.value, True)
                            if self.ac_alarms & (1 << 6):
                                self.update_alarms(Alarms.ZERO_CROSS_ERROR.value, True)
                            if self.ac_alarms & (1 << 7):
                                self.update_alarms(Alarms.EEPROM_ERROR.value, False)
                            if self.ac_alarms & (1 << 8):
                                self.update_alarms(Alarms.FAN_SPEED_ERROR.value, True)
                            if self.ac_alarms & (1 << 9):
                                self.update_alarms(Alarms.COMMS_ERROR_PANEL.value, True)
                            if self.ac_alarms & (1 << 10):
                                self.update_alarms(Alarms.COMPRESSOR_ERROR.value, True)
                            if self.ac_alarms & (1 << 11):
                                self.update_alarms(Alarms.INVERTER_PROTECTION.value, True)
                            if self.ac_alarms & (1 << 12):
                                self.update_alarms(Alarms.COOLING_ERROR.value, True)
                            if self.ac_alarms & (1 << 13):
                                self.update_alarms(Alarms.OUTDOOR_UNIT_FAULT.value, True)
                            if self.ac_alarms & (1 << 14):
                                self.update_alarms(Alarms.WATER_LEVEL_FAULT.value, True)
                            if self.ac_alarms & (1 << 15):
                                self.update_alarms(Alarms.OTHER.value, True)

                            self.ac_alarms = 0
                    else:
                        if self.alarms > 0:
                            self.update_alarms(Alarms.PHASE_ERROR.value, False)
                            self.update_alarms(Alarms.COMMS_ERROR.value, False)
                            self.update_alarms(Alarms.T1_SENSOR_ERROR.value, False)
                            self.update_alarms(Alarms.T2A_SENSOR_ERROR.value, False)
                            self.update_alarms(Alarms.T2B_SENSOR_ERROR.value, False)
                            self.update_alarms(Alarms.T3_AND_T4_SENSOR_ERROR.value, False)
                            self.update_alarms(Alarms.ZERO_CROSS_ERROR.value, False)
                            self.update_alarms(Alarms.EEPROM_ERROR.value, False)
                            self.update_alarms(Alarms.FAN_SPEED_ERROR.value, False)
                            self.update_alarms(Alarms.COMMS_ERROR_PANEL.value, False)
                            self.update_alarms(Alarms.COMPRESSOR_ERROR.value, False)
                            self.update_alarms(Alarms.INVERTER_PROTECTION.value, False)
                            self.update_alarms(Alarms.COOLING_ERROR.value, False)
                            self.update_alarms(Alarms.OUTDOOR_UNIT_FAULT.value, False)
                            self.update_alarms(Alarms.WATER_LEVEL_FAULT.value, False)
                            self.update_alarms(Alarms.OTHER.value, False)

                        self.alarm_timeout_counter = 0

                if len(self.actions) == 0:
                    self.actions.append(0)  # Dummy

                aircon_onoff_states = 0

                for x in range(1, int(self.dbData["aircon_indoor_qty"]) + 1):
                    if len(self.aircon_regs[x - 1]) > 0:
                        if self.aircon_regs[x - 1][0] == 1:
                            aircon_onoff_states |= (1 << (x - 1))

                # Having no enabled Indoor ACs *should* be a good indication the RS485 comms are iffy. It's entirely possible a single-phase breaker is open
                if aircon_onoff_states == 0:
                    self.update_faults(Faults.LOSS_OF_485_COMMS.value, True)
                else:
                    self.update_faults(Faults.LOSS_OF_485_COMMS.value, False)

                # This is ugly but the lists are all sequentially populated and can't be guaranteed when loading
                ac1_ambient = 0
                if len(self.aircon_regs[0]) > 0:
                    ac1_ambient = self.aircon_regs[0][4]

                # We're compressing the modes and speeds into the same word for later use
                fan_mode_speed = (self.aircon_fanmode << 8) + self.aircon_fanspeed
                fan_mode_speed1 = (self.aircon_regs[0][1] << 8) + self.aircon_regs[0][2]
                fan_mode_speed2 = (self.aircon_regs[1][1] << 8) + self.aircon_regs[1][2]
                fan_mode_speed3 = (self.aircon_regs[2][1] << 8) + self.aircon_regs[2][2]
                fan_mode_speed4 = (self.aircon_regs[3][1] << 8) + self.aircon_regs[3][2]
                fan_mode_speed5 = (self.aircon_regs[4][1] << 8) + self.aircon_regs[4][2]
                fan_mode_speed6 = (self.aircon_regs[5][1] << 8) + self.aircon_regs[5][2]

                # Modify self.outputs
                self.outputs[2][0] = self.aircon_heartbeat
                self.outputs[2][1] = 0
                self.outputs[2][2] = int(self.dbData["aircon_indoor_qty"]) if "aircon_indoor_qty" in self.dbData else 0
                self.outputs[2][3] = aircon_onoff_states
                self.outputs[2][4] = int(self.dbData["aircon_temp_setpoint"]) if "aircon_temp_setpoint" in self.dbData else 0
                self.outputs[2][5] = fan_mode_speed
                self.outputs[2][6] = 0
                self.outputs[2][7] = self.aircon_regs[0][5]
                self.outputs[2][8] = fan_mode_speed1
                self.outputs[2][9] = self.aircon_regs[1][5]
                self.outputs[2][10] = fan_mode_speed2
                self.outputs[2][11] = self.aircon_regs[2][5]
                self.outputs[2][12] = fan_mode_speed3
                self.outputs[2][13] = self.aircon_regs[3][5]
                self.outputs[2][14] = fan_mode_speed4
                self.outputs[2][15] = self.aircon_regs[4][5]
                self.outputs[2][16] = fan_mode_speed5
                self.outputs[2][17] = self.aircon_regs[5][5]
                self.outputs[2][18] = fan_mode_speed6
                self.outputs[2][19] = 0
                self.outputs[2][20] = self.warnings
                self.outputs[2][21] = self.alarms
                self.outputs[2][22] = self.faults
                self.outputs[2][23] = self.actions[0]
                # The following are legacy for the pre-made HMI and are not represented in SCADA output
                self.outputs[2][24] = self.aircon_regs[0]
                self.outputs[2][25] = self.aircon_regs[1]
                self.outputs[2][26] = self.aircon_regs[2]
                self.outputs[2][27] = self.aircon_regs[3]
                self.outputs[2][28] = self.aircon_regs[4]
                self.outputs[2][29] = self.aircon_regs[5]
                self.outputs[2][30] = self.aircon_regs[6]
                self.outputs[2][31] = self.aircon_regs[7]

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

                        if "aircon_temp_setpoint" in self.dbData:
                            if 17 <= self.inputs[2][4] <= 30:
                                if self.dbData["aircon_temp_setpoint"] != self.inputs[2][4]:
                                    self.dbData["aircon_temp_setpoint"] = self.inputs[2][4]  # Set new ambient temp
                                    self.setpoint = 1  # Force update
                        else:
                            if 17 <= self.inputs[2][4] <= 30:
                                self.dbData["aircon_temp_setpoint"] = self.inputs[2][4]

                    self.enabled = self.inputs[1]
                    self.outputs[1] = self.enabled
                    
        return [SET_INPUTS_ACK]

    def get_outputs(self):
        self.outputs[1] = self.enabled_echo

        #outputs = copy.deepcopy(self.outputs)
        #return outputs
        return [GET_OUTPUTS_ACK, self.outputs]

    def set_page(self, page, form):  # Respond to GET/POST requests

        # Add user actions to list. Format is [Action, Value]
        #for action in page[1].form:
        #    self.actions.append([action, page[1].form[action]])

        data = dict()

        if page == self.website + "_(" + str(self.uid) + ")":  # It was a page request for a numbered device (POST)

            # Save all control changes to database
            isButton = True
            for control in form:

                if "aircon_toggle" in form:
                    self.actions.insert(0, Actions.ENABLE_OVERRIDE_CHANGE.value)
                    self.ac_enable = not self.ac_enable
                elif "aircon_reset" in form:
                    self.actions.insert(0, Actions.AC_RESET_CHANGE.value)
                    self.reset = 1
                elif "aircon_temp_setpoint" in form:  # Treating a dropdowns like a trigger
                    self.actions.insert(0, Actions.AC_SETPOINT_CHANGE.value)
                    self.setpoint = 1
                    self.dbData[control] = form[control]
                    isButton = False  # Config must be saved to db for page loaders
                elif "aircon_fan_mode" in form:
                    self.actions.insert(0, Actions.AC_FANMODE_CHANGE.value)
                    self.fanmode = 1
                    self.dbData[control] = form[control]
                    isButton = False
                elif "aircon_fan_speed" in form:
                    self.actions.insert(0, Actions.AC_FANSPEED_CHANGE.value)
                    self.fanspeed = 1
                    self.dbData[control] = form[control]
                    isButton = False
                elif "aircon_output_override" in form:
                    self.override = not self.override
                else:
                    isButton = False

                    if "aircon_com_port" == control:
                        self.actions.insert(0, Actions.COM_PORT_CHANGE.value)
                    elif "aircon_indoor_qty" == control:
                        self.actions.insert(0, Actions.AC_QTY_CHANGE.value)

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
            # self.actions = []

            # Controller Information
            mod_data["aircon_name"] = self.name
            mod_data["aircon_man"] = self.manufacturer
            mod_data["aircon_fwver"] = self.version
            mod_data["aircon_serno"] = self.serial
            mod_data["aircon_constate"] = str(self.con_state).capitalize()
            mod_data["aircon_comports"] = self.comports
            mod_data["aircon_data"] = self.outputs[2]
            mod_data["aircon_override"] = self.override
            mod_data["aircon_hvac_offset"] = self.hvac_offset
            mod_data["aircon_hvac_enable"] = self.ac_enable


            mod_data.update(self.dbData)  # I'm appending the dict here so we don't save unnecessary
            
            return [SET_PAGE_ACK, mod_data]  # data to the database.

        else:
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
        
        try:
            db.save_to_db(__name__ + "_(" + str(self.uid) + ")", self.dbData)
        except Exception as e:
            print("HVAC: " + str(e))
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
            print("HVAC: " + str(e))
          
        try:
            if tx_msg is not None:
                queue[0].put(tx_msg, block=True)
                
        except Exception as e:
            print("HVAC: " + str(e))
            
 
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
    NONE = 0  # "Warning: Midea Aircon - No Warning present"
    VAPORIZER = 1
    THAWING = 2
    CONDENSER_HIGH_TEMP = 3
    COMPRESSOR_TEMP = 4
    EVACUATION_DUCT_TEMP = 5
    DISCHARGE_HIGH_PRESSURE = 6
    DISCHARGE_LOW_PRESSURE = 7
    CURRENT_OVER_OR_UNDER_LOAD = 8
    COMPRESSOR_CURRENT_OVERLOAD = 9
    OTHER = 16


class Alarms(Enum):
    NONE = 0  # "Alarm: Midea Aircon - No Alarm present"
    PHASE_ERROR = 1
    COMMS_ERROR = 2
    T1_SENSOR_ERROR = 3
    T2A_SENSOR_ERROR = 4
    T2B_SENSOR_ERROR = 5
    T3_AND_T4_SENSOR_ERROR = 6
    ZERO_CROSS_ERROR = 7
    EEPROM_ERROR = 8
    FAN_SPEED_ERROR = 9
    COMMS_ERROR_PANEL = 10
    COMPRESSOR_ERROR = 11
    INVERTER_PROTECTION = 12
    COOLING_ERROR = 13
    OUTDOOR_UNIT_FAULT = 14
    WATER_LEVEL_FAULT = 15
    OTHER = 16


class Faults(Enum):
    NONE = 0  # "Fault: Midea Aircon - No Fault present"
    CONFIG = 1  # "Fault: Midea Aircon - Configuration"
    LOSS_OF_COMMS = 2  # "Fault: Midea Aircon - Loss of Comms"
    LOSS_OF_485_COMMS = 3
    # IO_TIMEOUT = 3
    # AC1_FAULT_LIMIT = 3  # "Fault: Midea Aircon 1 - Ambient Temp out of range"
    # AC2_FAULT_LIMIT = 4  # "Fault: Midea Aircon 2 - Ambient Temp out of range"
    # AC3_FAULT_LIMIT = 5  # "Fault: Midea Aircon 3 - Ambient Temp out of range"
    # AC4_FAULT_LIMIT = 6  # "Fault: Midea Aircon 4 - Ambient Temp out of range"
    # AC5_FAULT_LIMIT = 7  # "Fault: Midea Aircon 5 - Ambient Temp out of range"
    # AC6_FAULT_LIMIT = 8  # "Fault: Midea Aircon 6 - Ambient Temp out of range"
    # AC7_FAULT_LIMIT = 9  # "Fault: Midea Aircon 7 - Ambient Temp out of range"
    # AC8_FAULT_LIMIT = 10  # "Fault: Midea Aircon 8 - Ambient Temp out of range"


class Actions(Enum):                                                                                # Track the manual interactions with the html page
    NONE = 0
    COM_PORT_CHANGE = 1
    ENABLE_OVERRIDE_CHANGE = 2
    AC_RESET_CHANGE = 3
    AC_QTY_CHANGE = 4
    AC_SETPOINT_CHANGE = 5
    AC_FANMODE_CHANGE = 6
    AC_FANSPEED_CHANGE = 7


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
