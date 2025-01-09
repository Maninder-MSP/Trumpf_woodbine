# FlexMod_Client.py,

# Description
# Useful resources: Peak Shaving vs Load Shifting: https://www.power-sonic.com/blog/the-power-of-peak-shaving-a-complete-guide/#:~:text=In%20other%20words%2C%20peak%20shaving,reduce%20the%20total%20energy%20used.

# Versions
# 3.5.24.10.16 - SC - Known good starting point, uses thread.is_alive to prevent module stalling.

import requests
from pymodbus.client.sync import ModbusTcpClient as mb_tcp_client
from threading import Thread, Event
from FlexDB import FlexTinyDB
from enum import Enum

import copy
from datetime import datetime, time
import time as ltime    # don't confuse with Datetime.time
import sys

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
        self.uid = uid
        self.icon = "/static/images/Client.png"
        self.name = "Client"
        self.module_type = ModTypes.CLIENT.value
        self.module_version = "3.5.24.10.16"
        self.manufacturer = "MSP"
        self.model = ""
        self.options = ""
        self.version = "-"
        self.serial = "-"
        self.website = "/Mod/FlexMod_Client"

        # Run state
        self.con_state = False
        self.state = State.IDLE
        self.set_state_text(self.state)

        # Non-Volatile Data (Loaded from DB)
        self.dbData = db.fetch_from_db(__name__ + "_(" + str(self.uid) + ")")
        if self.dbData is None:
            self.dbData = dict()

        # Volatile Data
        self.tcp_client = None
        self.tcp_timeout = 0
        self.enabled = True
        self.enabled_echo = False
        self.inputs = [self.uid, False]
        self.outputs = [self.uid, self.enabled, [0] * 25]
        self.heartbeat = 0
        self.heartbeat_echo = 0

        self.system_enabled = False
        self.system_status_text = "[0] System Idle"
        self.control_real_power_command = 0

        self.bank_reset_enabled = False
        self.battery_estop_interlocks_enabled = False
        self.battery_enable = False
        self.battery_enabled = False
        self.battery_soc = 0
        self.battery_dcbus_voltage = 0
        self.battery_dcbus_power = 0
        self.battery_max_charge_power = 0
        self.battery_max_discharge_power = 0
        self.battery_avg_cell_voltage = 0
        self.battery_charge_mode = "soc_charge"
        self.battery_max_charge_soc = 0
        self.battery_min_discharge_soc = 0
        self.battery_max_charge_voltage = 0
        self.battery_min_discharge_voltage = 0
        self.battery_max_emergency_charge_soc = 0
        self.battery_min_emergency_discharge_soc = 0
        self.battery_max_emergency_charge_voltage = 0
        self.battery_min_emergency_discharge_voltage = 0
        self.battery_charging_state = 0
        self.battery_rolloff_state = 0
        self.battery_warnings = 0
        self.battery_alarms = 0
        self.battery_faults = 0

        self.inverter_following_enable = False
        self.inverter_following_enabled = False
        self.inverter_following_state = False
        self.inverter_forming_enable = False
        self.inverter_forming_enabled = False
        self.inverter_forming_state = False
        self.inverter_import_limit = 0
        self.inverter_export_limit = 0
        self.inverter_ramp_rate = 0
        self.inverter_following_warnings = 0
        self.inverter_following_alarms = 0
        self.inverter_following_faults = 0
        self.inverter_forming_warnings = 0
        self.inverter_forming_alarms = 0
        self.inverter_forming_faults = 0
        #self.inverter_state = 0

        self.ac_meter_grid_power = 0
        self.ac_meter_load_power = 0
        self.ac_meter_grid_power_kva = 0
        self.ac_meter_grid_power_echo = 0
        self.ac_meter_drift_timeout = 0

        self.ac_solar_ac_power = 0
        self.dc_solar_dc_power = 0

        self.dc_solar_enable = False
        self.dc_solar_enabled = False

        self.client_enabled = False
        self.client_heartbeat = 0
        self.client_state = 0
        self.client_peak_shaving_limit = 0
        self.client_state_timeout = 0

        self.peak_time = False

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

        # Mode-specific
        self.grid_charge1_enabled = False
        self.grid_charge1_import_limit = 0
        self.grid_charge1_start_hour = 0
        self.grid_charge1_start_min = 0
        self.grid_charge1_end_hour = 0
        self.grid_charge1_end_min = 0
        self.grid_charge1_active = False
        self.grid_charge1_params = [0] * 10

        self.grid_charge2_enabled = False
        self.grid_charge2_import_limit = 0
        self.grid_charge2_start_hour = 0
        self.grid_charge2_start_min = 0
        self.grid_charge2_end_hour = 0
        self.grid_charge2_end_min = 0
        self.grid_charge2_active = False
        self.grid_charge2_params = [0] * 10

        self.grid_charge3_enabled = False
        self.grid_charge3_import_limit = 0
        self.grid_charge3_start_hour = 0
        self.grid_charge3_start_min = 0
        self.grid_charge3_end_hour = 0
        self.grid_charge3_end_min = 0
        self.grid_charge3_active = False
        self.grid_charge3_params = [0] * 10

        self.acgen_charge1_enabled = False
        self.acgen_charge1_load_limit = 0
        self.acgen_charge1_start_hour = 0
        self.acgen_charge1_start_min = 0
        self.acgen_charge1_end_hour = 0
        self.acgen_charge1_end_min = 0
        self.acgen_charge1_active = False
        self.acgen_charge1_params = [0] * 10
        self.acgen_start = False
        self.acgen_start_state = False
        self.acgen_mains_parallel_mode = False
        self.acgen_mains_parallel_mode_state = False
        self.acgen_charge_enable = False
        self.acgen_max_load = 5
        self.acgen_state = 0
        self.acgen_emergency_state = False
        self.acgen_timeout = 0
        self.acgen_ac_frequency = 0

        self.acsolar_charge1_enabled = False
        self.acsolar_charge1_import_limit = 0
        self.acsolar_charge1_start_hour = 0
        self.acsolar_charge1_start_min = 0
        self.acsolar_charge1_end_hour = 0
        self.acsolar_charge1_end_min = 0
        self.acsolar_charge1_active = False
        self.acsolar_charge1_params = [0] * 10
        
        self.peak_shave1_enabled = False
        self.peak_shave1_limit = 0
        self.peak_shave1_start_hour = 0
        self.peak_shave1_start_min = 0
        self.peak_shave1_end_hour = 0
        self.peak_shave1_end_min = 0
        self.peak_shave1_active = False
        self.peak_shave1_params = [0] * 10

        self.peak_shave2_enabled = False
        self.peak_shave2_limit = 0
        self.peak_shave2_start_hour = 0
        self.peak_shave2_start_min = 0
        self.peak_shave2_end_hour = 0
        self.peak_shave2_end_min = 0
        self.peak_shave2_active = False
        self.peak_shave2_params = [0] * 10

        self.peak_shave3_enabled = False
        self.peak_shave3_limit = 0
        self.peak_shave3_start_hour = 0
        self.peak_shave3_start_min = 0
        self.peak_shave3_end_hour = 0
        self.peak_shave3_end_min = 0
        self.peak_shave3_active = False
        self.peak_shave3_params = [0] * 10

        self.client_tasks_enabled = False
        self.client_tasks_client = ""
        self.client_tasks_start_hour = 0
        self.client_tasks_start_min = 0
        self.client_tasks_end_hour = 0
        self.client_tasks_end_min = 0
        self.client_tasks_params = [0] * 20

        # Client module parameters
        self.client_param1 = 0
        self.client_param2 = 0
        self.client_param3 = 0
        self.client_param4 = 0
        self.client_param5 = 0
        self.client_param6 = 0
        self.client_param7 = 0
        self.client_param8 = 0
        self.client_param9 = 0
        self.client_param10 = 0
        self.client_param11 = 0
        self.client_param12 = 0
        self.client_param13 = 0
        self.client_param14 = 0
        self.client_param15 = 0
        self.client_param16 = 0
        
        self.client_peak_setpoint_last = 0
        
        # Very client-specific variables. Try to avoid doing this.
        self.client_CXP00884_setpoint_last = 0
        
        self.client_CXP00957_active_mode = 0
        self.client_CXP00957_active_mode_echo = 0
        self.client_CXP00957_charge_setpoint = 0
        self.client_CXP00957_discharge_setpoint = 0

        self.client_CXP00957_pv_bypass_output_setpoint = 0
        self.client_CXP00957_grid_output_setpoint = 0
        self.client_CXP00957_pv_output_setpoint = 0
        self.client_CXP00957_pv_output_setpoint_last = 0
        self.client_CXP00957_fc_output_setpoint = 0
        self.client_CXP00957_fc_output_setpoint_last = 0

        self.client_CXP00957_pv_bypass_active = False
        self.client_CXP00957_grid_active = False
        self.client_CXP00957_pv_active = False
        self.client_CXP00957_fc_active = False

        self.client_CXP00957_pv_enable = False
        self.client_CXP00957_pv_enabled = False

        self.inv_setpoint = 0

        # AMPT Solar table for CXP00957. Current PER OPTIMIZER
        # I don't really want it here but it's a one-off for Cummins and we don't yet have a mechanism in the DC Solar module for setting the string current. BIG TODO        
        # i13.5 String Optimizer - oc:current pairs from "AMPT-D2B-API-20240806.pdf"
        self.solar_array = {  0:0.22,    1:0.28,   2:0.34,    3:0.40,    4:0.45,    5:0.51,    6:0.57,    7:0.63,    8:0.68,    9:0.74,    10:0.80,   11:0.86,   12:0.92,   13:0.97,   14:1.03,   15:1.09,   16:1.15,   17:1.20,   18:1.26,   19:1.32,   20:1.38,   21:1.44,   22:1.49,   23:1.55,   24:1.61, 
                             25:1.67,   26:1.72,   27:1.78,   28:1.84,   29:1.90,   30:1.96,   31:2.01,   32:2.07,   33:2.13,   34:2.19,   35:2.25,   36:2.30,   37:2.36,   38:2.42,   39:2.48,   40:2.53,   41:2.59,   42:2.65,   43:2.71,   44:2.77,   45:2.82,   46:2.88,   47:2.94,   48:3.00,   49:3.05,
                             50:3.11,   51:3.17,   52:3.23,   53:3.29,   54:3.34,   55:3.40,   56:3.40,   57:3.52,   58:3.57,   59:3.63,   60:3.69,   61:3.75,   62:3.81,   63:3.86,   64:3.92,   65:3.98,   66:4.04,   67:4.09,   68:4.15,   69:4.21,   70:4.33,   71:4.33,   72:4.38,   73:4.44,   74:4.50,
                             75:4.56,   76:4.61,   77:4.67,   78:4.73,   79:4.79,   80:4.85,   81:4.90,   82:4.96,   83:5.02,   84:5.08,   85:5.14,   86:5.19,   87:5.25,   88:5.31,   89:5.37,   90:5.42,   91:5.48,   92:5.54,   93:5.60,   94:5.66,   95:5.71,   96:5.77,   97:5.83,   98:5.89,   99:5.94,
                            100:6.00,  101:6.06,  102:6.12,  103:6.18,  104:6.23,  105:6.29,  106:6.35,  107:6.41,  108:6.46,  109:6.52,  110:6.58,  111:6.58,  112:6.70,  113:6.75,  114:6.81,  115:6.87,  116:6.93,  117:6.98,  118:7.04,  119:7.10,  120:7.16,  121:7.22,  122:7.27,  123:7.33,  124:7.39,
                            125:7.45,  126:7.50,  127:7.56,  128:7.62,  129:7.68,  130:7.74,  131:7.79,  132:7.85,  133:7.91,  134:7.97,  135:8.03,  136:8.08,  137:8.14,  138:8.20,  139:8.26,  140:8.31,  141:8.37,  142:8.43,  143:8.49,  144:8.55,  145:8.60,  146:8.66,  147:8.72,  148:8.78,  149:8.83,
                            150:8.89,  151:8.95,  152:9.01,  153:9.07,  154:9.12,  155:9.18,  156:9.24,  157:9.30,  158:9.35,  159:9.41,  160:9.47,  161:9.53,  162:9.59,  163:9.64,  164:9.70,  165:9.76,  166:9.82,  167:9.87,  168:9.93,  169:9.99,  170:10.05, 171:10.11, 172:10.16, 173:10.22, 174:10.28,
                            175:10.34, 176:10.39, 177:10.45, 178:10.51, 179:10.57, 180:10.63, 181:10.68, 182:10.74, 183:10.80, 184:10.86, 185:10.92, 186:10.97, 187:11.03, 188:11.09, 189:11.15, 190:11.20, 191:11.26, 192:11.32, 193:11.38, 194:11.44, 195:11.49, 196:11.55, 197:11.61, 198:11.67, 199:11.72,
                            200:11.78, 201:11.84, 202:11.90, 203:11.96, 204:12.01, 205:12.07, 206:12.13, 207:12.19, 208:12.24, 209:12.30, 210:12.36, 211:12.42, 212:12.48, 213:12.53, 214:12.59, 215:12.65, 216:12.71, 217:12.76, 218:12.82, 219:21.88, 220:12.94, 221:13.00, 222:13.05, 223:13.11, 224:13.17,
                            225:13.23, 226:13.28, 227:13.34, 229:13.46
                           }

        print("Starting " + self.name + " with UID " + str(self.uid) + " on version " + str(self.module_version))

    def twos_comp_to_int(self, twoC):
        # calculate int from two's compliment input
        return twoC if not twoC & 0x8000 else -(0xFFFF - twoC + 1)

    def int_to_twos_comp(self, num):
        return num if num >= 0 else (0xFFFF + num + 1)

    def get_io(self, control):
        uid = int(str(control).split("UID ")[1].split(" :")[0])
        if "Input" in control:
            io = int(str(control).split("Input ")[1])
        elif "Output" in control:
            io = int(str(control).split("Output ")[1])
        return uid, io

    def process(self):
        global loop_time
        print("Heartbeat: " + str(self.heartbeat) + "          ", end='\r')  
        #print("(24)  Client Heartbeat: " + str(self.heartbeat) + ", loop time: " + str(loop_time) + " Seconds")
        
        s = ltime.time()
        #self.client_state = 4 # TEST
        self.heartbeat += 1
        if self.heartbeat >= 0xFFFF:
            self.heartbeat = 0

        if len(self.inputs) < 25:
            return
        
        # Idle state, not enabled
        if self.client_state == 0:
            if self.system_enabled:
                self.system_status_text = "[0] System Enabled"
                self.client_state = 1
            else:
                self.enabled_echo = False
                self.system_status_text = "[0] System Idle"

        # Enable the Battery RMSCs. For older systems this may be 5 relays (Individual E-Stop Interlocks), on newer systems it's just one (RMSC Power).
        elif self.client_state == 1:

            if self.system_enabled:
                if self.bank_reset_enabled:
                    self.system_status_text = "[1] Battery RMSCs Enabled"
                    self.client_state_timeout = 0
                    self.client_state = 2
                else:
                    if self.client_state_timeout < 10:
                        self.client_state_timeout += 1
                    else:  # Sit here until fixed and the enable state is cycled
                        self.system_status_text = "[1] Could not enable Battery RMSCs"

            else:
                self.system_status_text = "[1] Disabling Battery RMSCs"  # In truth we're currently leaving them enabled
                self.client_state = 0

        # Close the battery contacts to energize the DC Bus
        elif self.client_state == 2:
            if self.system_enabled:
                if not self.battery_enabled:
                    self.battery_enable = True

                    if self.client_state_timeout < 15:
                        self.client_state_timeout += 1

                    if self.client_state_timeout == 10:  # Minimum time we wait for at least one contact is closed
                        self.system_status_text = "[2] Could not close Battery Contacts"

                    elif self.client_state_timeout == 15:  # Display corrective measures
                        # Try to diagnose the alarms
                        if self.battery_faults > 0:
                            self.system_status_text = "[2] Battery Fault(s) Present"
                        elif self.battery_alarms > 0:
                            self.system_status_text = "[2] Battery Alarm(s) Present"
                        elif self.battery_warnings > 0:
                            self.system_status_text = "[2] Battery Warning(s) Present"
                        else:
                            self.system_status_text = "[2] Check E-Stop or Override"  # Catch-all when the battery can't report its condition
                else:
                    self.system_status_text = "[2] Battery Contacts Closed"
                    self.client_state_timeout = 0
                    self.client_state = 3
            else:
                if not self.inverter_following_enabled:
                    self.system_status_text = "[2] Opening Battery Contacts"
                    self.battery_enable = False
                    self.client_state = 1

        # Enable the Grid-Following Inverter
        elif self.client_state == 3:
            if self.system_enabled:
                if not self.inverter_following_enabled:
                    self.inverter_following_enable = True

                    if self.client_state_timeout < 15:
                        self.client_state_timeout += 1

                    if self.client_state_timeout == 10:  # Minimum time we wait for at least one contact is closed
                        self.system_status_text = "[3] Could not enable Inverter"

                    elif self.client_state_timeout == 15:  # Display corrective measures
                        # Try to diagnose the fault
                        if self.inverter_following_faults > 0:
                            self.system_status_text = "[3] Inverter Fault(s) Present"
                        elif self.inverter_following_alarms > 0:
                            self.system_status_text = "[3] Inverter Alarm(s) Present"
                        elif self.inverter_following_warnings > 0:
                            self.system_status_text = "[3] Inverter Warning(s) Present"
                        else:
                            self.system_status_text = "[3] Check E-Stop or Override"  # Catch-all when the battery can't report its condition

                else:
                    self.system_status_text = "[3] Following Inverter Enabled"
                    #print("catch 4")
                    self.control_real_power_command = 0  # Reset the commanded power
                    self.client_state = 4
            else:
                self.system_status_text = "[3] Disabling Following Inverter"
                #print("catch 5")
                self.control_real_power_command = 0  # TODO: Soft or hard ramp down
                self.inverter_following_enable = False
                self.client_state = 2

        # Run the Client application, if specified
        elif self.client_state == 4:
            
            if self.system_enabled:

                self.enabled_echo = True

                # We know the peak setpoint, so we should be able to offset the load
                pMaxChg = float(self.battery_max_charge_power)  # Maximum Power Kore says we can charge at.
                pMaxDChg = float(self.battery_max_discharge_power)
                pDcBus = float(self.battery_dcbus_power)  # DC Bus power
                pGrid = float(self.ac_meter_grid_power)  # Grid power, positive when importing so we negate it for calcs
                pRampRate = int(self.inverter_ramp_rate)
                pImport = float(self.inverter_import_limit)
                pExport = float(self.inverter_export_limit)

                # ######################################################################################### #
                # GRID CHARGING / TIME OF USE                                                               #
                # ######################################################################################### #
                tou_active = False
                tou_setpoint = 0

                # TOU period 1
                if self.grid_charge1_enabled:
                    # Now we need to check if we're Active, i.e. the current time is within the configured time
                    if self.check_active_time(time(self.grid_charge1_start_hour, self.grid_charge1_start_min), time(self.grid_charge1_end_hour, self.grid_charge1_end_min)):
                        self.grid_charge1_active = True

                        tou_setpoint = self.grid_charge1_import_limit
                    else:
                        self.grid_charge1_active = False

                    self.grid_charge1_params[0] = self.grid_charge1_enabled
                    self.grid_charge1_params[1] = self.grid_charge1_import_limit
                    self.grid_charge1_params[2] = self.grid_charge1_start_hour
                    self.grid_charge1_params[3] = self.grid_charge1_start_min
                    self.grid_charge1_params[4] = self.grid_charge1_end_hour
                    self.grid_charge1_params[5] = self.grid_charge1_end_min
                    self.grid_charge1_params[6] = 0
                    self.grid_charge1_params[7] = 0
                    self.grid_charge1_params[8] = 0
                    self.grid_charge1_params[9] = "Active" if self.grid_charge1_active else ""
                else:
                    self.grid_charge1_active = False
                    self.grid_charge1_params = [0] * 10
                    self.grid_charge1_params[9] = ""

                # TOU period 2
                if self.grid_charge2_enabled:
                    # Now we need to check if we're Active, i.e. the current time is within the configured time
                    if self.check_active_time(time(self.grid_charge2_start_hour, self.grid_charge2_start_min), time(self.grid_charge2_end_hour, self.grid_charge2_end_min)):
                        self.grid_charge2_active = True

                        if self.grid_charge1_active:                                                    # Use the lowest import limit for the current time period
                            if self.grid_charge2_import_limit < tou_setpoint:
                                tou_setpoint = self.grid_charge2_import_limit
                        else:
                            tou_setpoint = self.grid_charge2_import_limit

                    else:
                        self.grid_charge2_active = False

                    self.grid_charge2_params[0] = self.grid_charge2_enabled
                    self.grid_charge2_params[1] = self.grid_charge2_import_limit
                    self.grid_charge2_params[2] = self.grid_charge2_start_hour
                    self.grid_charge2_params[3] = self.grid_charge2_start_min
                    self.grid_charge2_params[4] = self.grid_charge2_end_hour
                    self.grid_charge2_params[5] = self.grid_charge2_end_min
                    self.grid_charge2_params[6] = 0
                    self.grid_charge2_params[7] = 0
                    self.grid_charge2_params[8] = 0
                    self.grid_charge2_params[9] = "Active" if self.grid_charge2_active else ""
                else:
                    self.grid_charge2_active = False
                    self.grid_charge2_params = [0] * 10
                    self.grid_charge2_params[9] = ""

                # TOU period 3
                if self.grid_charge3_enabled:
                    # Now we need to check if we're Active, i.e. the current time is within the configured time
                    if self.check_active_time(time(self.grid_charge3_start_hour, self.grid_charge3_start_min), time(self.grid_charge3_end_hour, self.grid_charge3_end_min)):
                        self.grid_charge3_active = True

                        if self.grid_charge1_active or self.grid_charge2_active:                        # Use the lowest import limit for the current time period
                            if self.grid_charge3_import_limit < tou_setpoint:
                                tou_setpoint = self.grid_charge3_import_limit
                        else:
                            tou_setpoint = self.grid_charge3_import_limit

                    else:
                        self.grid_charge3_active = False

                    self.grid_charge3_params[0] = self.grid_charge3_enabled
                    self.grid_charge3_params[1] = self.grid_charge3_import_limit
                    self.grid_charge3_params[2] = self.grid_charge3_start_hour
                    self.grid_charge3_params[3] = self.grid_charge3_start_min
                    self.grid_charge3_params[4] = self.grid_charge3_end_hour
                    self.grid_charge3_params[5] = self.grid_charge3_end_min
                    self.grid_charge3_params[6] = 0
                    self.grid_charge3_params[7] = 0
                    self.grid_charge3_params[8] = 0
                    self.grid_charge3_params[9] = "Active" if self.grid_charge3_active else ""
                else:
                    self.grid_charge3_active = False
                    self.grid_charge3_params = [0] * 10
                    self.grid_charge3_params[9] = ""

                # Now we know what we need to command from the inverter, we can filter it through other tasks
                if self.grid_charge1_active or self.grid_charge2_active or self.grid_charge3_active:

                    tou_active = True

                    if (self.battery_charge_mode == "soc_charge" and (float(self.battery_max_charge_soc) <= float(self.battery_soc))) or \
                       (self.battery_charge_mode == "volt_charge" and (int(self.battery_max_charge_voltage) <= int(self.battery_dcbus_voltage))):

                        print("Max Charge Limit Reached")

                        if self.control_real_power_command <= -pRampRate:
                            print("Max Charge Limit: Matching pMaxChg at ramp")
                            self.control_real_power_command = self.control_real_power_command + pRampRate
                        elif self.control_real_power_command <= -1:
                            print("Max Charge Limit: Matching pMaxChg")
                            self.control_real_power_command = self.control_real_power_command + 1
                    else:
                        if -pMaxChg < pDcBus:  # We *can* charge

                            print("pGrid is " + str(pGrid) + ", pImport is " + str(tou_setpoint))
                            if pGrid < tou_setpoint:

                                if self.control_real_power_command - pRampRate >= -tou_setpoint:  # self.control_real_power_command will continue to rise and will take forever to command back to zero.
                                    # if pGrid + pRampRate <= pImport:
                                    print("Charging at ramp")
                                    self.control_real_power_command = self.control_real_power_command - pRampRate
                                elif self.control_real_power_command - 1 >= -tou_setpoint:
                                    # elif pGrid + 1 <= pImport:
                                    print("Inverter accumulation")
                                    self.control_real_power_command = self.control_real_power_command - 1
                            else:
                                print("Grid charging: Limiting to pImport")
                                self.control_real_power_command = self.control_real_power_command + 1
                        else:
                            if self.control_real_power_command <= -pRampRate:
                                print("Matching pMaxChg at ramp")
                                self.control_real_power_command = self.control_real_power_command + pRampRate
                            elif self.control_real_power_command <= -1:
                                print("Matching pMaxChg")
                                self.control_real_power_command = self.control_real_power_command + 1

                # ######################################################################################### #
                # AC Generator Charging - Currently targets the DSE8610 MKII with some IO                   #
                # ######################################################################################### #
                acgen_active = False
                acgen_setpoint = 0

                # AC Generator period 1
                if self.acgen_charge1_enabled:
                    print("AC Gen Active")
                    # Now we need to check if we're Active, i.e. the current time is within the configured time
                    if self.check_active_time(time(self.acgen_charge1_start_hour, self.acgen_charge1_start_min),
                                              time(self.acgen_charge1_end_hour, self.acgen_charge1_end_min)):
                        self.acgen_charge1_active = True  # Use the lowest export limit for the current time period
                    else:
                        self.acgen_charge1_active = False

                    self.acgen_charge1_params[0] = self.acgen_charge1_enabled
                    self.acgen_charge1_params[1] = self.acgen_charge1_load_limit
                    self.acgen_charge1_params[2] = self.acgen_charge1_start_hour
                    self.acgen_charge1_params[3] = self.acgen_charge1_start_min
                    self.acgen_charge1_params[4] = self.acgen_charge1_end_hour
                    self.acgen_charge1_params[5] = self.acgen_charge1_end_min
                    self.acgen_charge1_params[6] = 0
                    self.acgen_charge1_params[7] = 0
                    self.acgen_charge1_params[8] = 0
                    self.acgen_charge1_params[9] = "Active" if self.acgen_charge1_active else ""

                else:
                    self.acgen_charge1_active = False
                    self.acgen_charge1_params = [0] * 10
                    self.acgen_charge1_params[9] = ""

                # Generator charging is enabled and the time falls within its active hours
                if self.acgen_charge1_enabled:
                    if self.acgen_charge1_active:                                                       # Normal day hours
                        # Generator Control
                        if self.acgen_state == 0:                                                       # Idle state (off or transition to off)
                            print("acgen_state 0")
                            if not self.get_acgen_runstate():
                                self.acgen_timeout = 30                                                 # Reset the timeout

                                if self.acgen_mains_parallel_mode_state:
                                    self.acgen_mains_parallel_mode = False                              # Disable Mains Parallel Mode

                                else:
                                    self.acgen_emergency_state = False                                  # If the battery drops too low we'll be enabling the generator as normal
                                    self.acgen_state = 1
                            else:
                                if self.acgen_start_state:                                              # Stop the generator if necessary
                                    self.acgen_start = False

                        elif self.acgen_state == 1:                                                     # Ready and waiting for use

                            if self.inverter_forming_enabled:                                           # The inverter has reported that it is enabled
                                print("acgen_state 1")
                                # Battery is Discharged, start the Generator
                                if (self.battery_charge_mode == "soc_charge" and (int(self.battery_min_discharge_soc) >= float(self.battery_soc))) or \
                                   (self.battery_charge_mode == "volt_charge" and (int(self.battery_min_discharge_voltage) >= int(self.battery_dcbus_voltage))):

                                    if not self.acgen_mains_parallel_mode_state:
                                        self.acgen_mains_parallel_mode = True                           # Enable Mains Parallel Mode

                                    if not self.acgen_start_state:                                      # Remote start the generator
                                        self.acgen_max_load = 5                                         # Minimise the maximum load

                                        self.acgen_timeout = 30
                                        self.acgen_start = True

                                    self.acgen_state = 2                                                # Prepare to charge the batteries

                                # Battery is Charged, stop the Generator
                                elif (self.battery_charge_mode == "soc_charge" and (float(self.battery_max_charge_soc) <= float(self.battery_soc))) or \
                                     (self.battery_charge_mode == "volt_charge" and (int(self.battery_max_charge_voltage) <= int(self.battery_dcbus_voltage))):

                                    self.acgen_charge_enable = False
                                    self.acgen_state = 0

                                if self.inverter_forming_state == 3:                                    # Inverter Fault response
                                    self.acgen_state = 3

                        elif self.acgen_state == 2:                                                     # Wait for the generator to start
                            print("acgen_state 2")
                            self.acgen_state = 1                                                        # Default return state

                            if not self.get_acgen_runstate():
                                if self.acgen_timeout > 0:
                                    self.acgen_timeout -= 1
                                else:
                                    self.acgen_state = 0

                            else:                                                                       # Generator has started, charge the battery
                                self.acgen_charge_enable = True                                         # No faults, generator running, continue monitoring SoC / Voltage

                        elif self.acgen_state == 3:                                                     # Fault case, start the Generator if the inverter faults.
                            print("acgen_state 3")
                            if self.acgen_mains_parallel_mode_state:
                                self.acgen_mains_parallel_mode = False                                  # Disable Mains Parallel Mode

                            if not self.acgen_start_state:
                                self.acgen_max_load = 100                                               # Maximise the max limit

                                self.acgen_timeout = 30
                                self.acgen_start = True
                                self.inverter_forming_enable = False                                    # Disable the inverter
                            else:
                                if not self.get_acgen_runstate():                                       # Wait until the generator starts
                                    if self.acgen_timeout > 0:
                                        self.acgen_timeout -= 1
                                    else:
                                        self.acgen_state = 0

                            if self.get_acgen_runstate():
                                if not self.inverter_forming_enabled:
                                    self.inverter_forming_enable = True
                                else:                                                                   # Wait for it to enable in Forming or sometimes Following mode
                                    if self.inverter_forming_state == 2 or self.inverter_forming_state == 4:
                                        self.acgen_state = 0
                                    else:
                                        # TODO: We're not accounting for fault states here yet and are relying on the EPC module to clear them if so.
                                        self.acgen_state = 0

                        else:                                                                           # Unknown state, revert to Idle
                            print("acgen_state unknown")
                            self.acgen_state = 0

                        # Now to actually charge the batteries
                        if self.acgen_charge_enable:
                            pass

                    else:                                                                               # Night or "peak" hours. Only run the generator for a while in an emergency as people are sleeping.
                        # Generator Control
                        if self.acgen_state == 0:
                            print("peak acgen_state 0")
                            if not self.get_acgen_runstate():
                                self.acgen_timeout = 30                                                 # Reset the timeout

                                if self.acgen_mains_parallel_mode_state:
                                    self.acgen_mains_parallel_mode = False                              # Disable Mains Parallel Mode

                                elif (self.battery_charge_mode == "soc_charge" and (int(self.battery_min_emergency_discharge_soc) >= float(self.battery_soc))) or \
                                     (self.battery_charge_mode == "volt_charge" and (int(self.battery_min_emergency_discharge_voltage) >= int(self.battery_dcbus_voltage))):
                                    self.acgen_emergency_state = True                                   # Customers may be sleeping but we must charge a little
                                    self.acgen_state = 1
                            else:
                                if self.acgen_start_state:                                              # Stop the generator if necessary
                                    self.acgen_start = False

                        elif self.acgen_state == 1:

                            if self.inverter_forming_enabled:                                           # The inverter has reported that it is enabled
                                print("peak acgen_state 1")
                                # Battery is Discharged, start the Generator
                                if (self.battery_charge_mode == "soc_charge" and (int(self.battery_min_emergency_discharge_soc) >= float(self.battery_soc))) or \
                                   (self.battery_charge_mode == "volt_charge" and (int(self.battery_min_emergency_discharge_voltage) >= int(self.battery_dcbus_voltage))):

                                    if not self.acgen_mains_parallel_mode_state:
                                        self.acgen_mains_parallel_mode = True                           # Enable Mains Parallel Mode

                                    if not self.acgen_start_state:                                      # Remote start the generator
                                        self.acgen_max_load = 5                                         # Minimise the maximum load

                                        self.acgen_timeout = 30
                                        self.acgen_start = True

                                    self.acgen_state = 2

                                # Battery is Charged, stop the Generator
                                elif (self.battery_charge_mode == "soc_charge" and (float(self.battery_max_emergency_charge_soc) <= float(self.battery_soc))) or \
                                     (self.battery_charge_mode == "volt_charge" and (int(self.battery_max_emergency_charge_soc) <= int(self.battery_dcbus_voltage))):

                                    self.acgen_emergency_state = False
                                    self.acgen_charge_enable = False
                                    self.acgen_state = 0

                                if not self.acgen_emergency_state:                                      # We've finished our emergency shift
                                    self.acgen_charge_enable = False
                                    self.acgen_state = 0

                                if self.inverter_forming_state == 3:                                    # Inverter Fault response
                                    self.acgen_state = 3

                        elif self.acgen_state == 2:

                            self.acgen_state = 1                                                        # Default return state
                            print("acgen_state 2")
                            if not self.get_acgen_runstate():
                                if self.acgen_timeout > 0:
                                    self.acgen_timeout -= 1
                                else:
                                    self.acgen_state = 0

                            else:  # Generator has started, charge the battery
                                if not self.acgen_emergency_state:                                      # We wandered into Peak time with the generator still on.
                                    if self.acgen_max_load == 5:                                        # Charging code ramped the power to minimum (magic number). Stop charging.
                                        self.acgen_charge_enable = False
                                        self.acgen_state = 0
                                else:
                                    self.acgen_charge_enable = True

                        elif self.acgen_state == 3:

                            if self.acgen_mains_parallel_mode_state:
                                self.acgen_mains_parallel_mode = False                                  # Disable Mains Parallel Mode

                            if not self.acgen_start_state:
                                self.acgen_max_load = 100                                               # Maximise the max limit

                                self.acgen_timeout = 30
                                self.acgen_start = True
                                self.inverter_forming_enable = False                                    # Disable the inverter
                            else:
                                if not self.get_acgen_runstate():                                       # Wait until the generator starts
                                    if self.acgen_timeout > 0:
                                        self.acgen_timeout -= 1
                                    else:
                                        self.acgen_state = 0

                            if self.get_acgen_runstate():
                                if not self.inverter_forming_enabled:
                                    self.inverter_forming_enable = True
                                else:                                                                   # Wait for it to enable in Forming or sometimes Following mode
                                    if self.inverter_forming_state == 2 or self.inverter_forming_state == 4:
                                        self.acgen_state = 0
                                    else:
                                        # TODO: We're not accounting for fault states here yet and are relying on the EPC module to clear them if so.
                                        pass

                        else:                                                                           # Unknown state, revert to Idle
                            print("acgen_state unknown")
                            self.acgen_state = 0

                    # Now to actually charge the batteries
                    if self.acgen_charge_enable:

                        if -pMaxChg < pDcBus:  # We *can* charge
                            if (self.acgen_charge1_active and ((self.battery_charge_mode == "soc_charge" and (float(self.battery_max_charge_soc) > float(self.battery_soc))) or
                                                               (self.battery_charge_mode == "volt_charge" and (int(self.battery_max_charge_voltage) > int(self.battery_dcbus_voltage))))) or \
                               (self.acgen_emergency_state and ((self.battery_charge_mode == "soc_charge" and (float(self.battery_max_emergency_charge_soc) > float(self.battery_soc))) or
                                                                (self.battery_charge_mode == "volt_charge" and (int(self.battery_max_emergency_charge_voltage) > int(self.battery_dcbus_voltage))))):

                                if (self.acgen_max_load + pRampRate) <= 100:
                                    print("Gen: Charging at ramp")
                                    self.acgen_max_load = self.acgen_max_load + pRampRate
                                elif (self.acgen_max_load + 1) <= 100:
                                    print("Gen: Incremental Charging")
                                    self.acgen_max_load = self.acgen_max_load + 1
                            else:
                                if (self.acgen_max_load - pRampRate) >= 5:
                                    print("Gen 1: Max Charge Limit: Matching pMaxChg at ramp")
                                    self.acgen_max_load = self.acgen_max_load - pRampRate
                                elif (self.acgen_max_load - 1) >= 5:
                                    print("Gen 1: Max Charge Limit: Matching pMaxChg")
                                    self.acgen_max_load = self.acgen_max_load - 1
                        else:
                            if (self.acgen_max_load - pRampRate) >= 5:
                                print("Gen 2: Max Charge Limit: Matching pMaxChg at ramp")
                                self.acgen_max_load = self.acgen_max_load - pRampRate
                            elif (self.acgen_max_load - 1) >= 5:
                                print("Gen 2: Max Charge Limit: Matching pMaxChg")
                                self.acgen_max_load = self.acgen_max_load - 1
                    else:
                        if (self.acgen_max_load - pRampRate) >= 5:
                            print("Gen 3: Max Charge Limit: Matching pMaxChg at ramp")
                            self.acgen_max_load = self.acgen_max_load - pRampRate
                        elif (self.acgen_max_load - 1) >= 5:
                            print("Gen 3: Max Charge Limit: Matching pMaxChg")
                            self.acgen_max_load = self.acgen_max_load - 1

                # ######################################################################################### #
                # AC Solar Charging                                                                         #
                # ######################################################################################### #
                acsolar_active = False
                acsolar_setpoint = 0

                # AC SOLAR period 1 (It's fair to assume at this point that we won't be defining different times for absorbing solar, so just the one setting for now)
                if self.acsolar_charge1_enabled:
                    # Now we need to check if we're Active, i.e. the current time is within the configured time
                    if self.check_active_time(time(self.acsolar_charge1_start_hour, self.acsolar_charge1_start_min),
                                              time(self.acsolar_charge1_end_hour, self.acsolar_charge1_end_min)):
                        self.acsolar_charge1_active = True

                        acsolar_setpoint = self.acsolar_charge1_import_limit
                    else:
                        self.acsolar_charge1_active = False

                    self.acsolar_charge1_params[0] = self.acsolar_charge1_enabled
                    self.acsolar_charge1_params[1] = self.acsolar_charge1_import_limit
                    self.acsolar_charge1_params[2] = self.acsolar_charge1_start_hour
                    self.acsolar_charge1_params[3] = self.acsolar_charge1_start_min
                    self.acsolar_charge1_params[4] = self.acsolar_charge1_end_hour
                    self.acsolar_charge1_params[5] = self.acsolar_charge1_end_min
                    self.acsolar_charge1_params[6] = 0
                    self.acsolar_charge1_params[7] = 0
                    self.acsolar_charge1_params[8] = 0
                    self.acsolar_charge1_params[9] = "Active" if self.acsolar_charge1_active else ""
                else:
                    self.acsolar_charge1_active = False
                    self.acsolar_charge1_params = [0] * 10
                    self.acsolar_charge1_params[9] = ""

                # Now we know what we need to command from the inverter, we can filter it through other tasks
                if self.acsolar_charge1_active:

                    acsolar_active = True

                    if (self.battery_charge_mode == "soc_charge" and (float(self.battery_max_charge_soc) <= float(self.battery_soc))) or \
                       (self.battery_charge_mode == "volt_charge" and (int(self.battery_max_charge_voltage) <= int(self.battery_dcbus_voltage))):   # Ramp down the import if we've hit the target
                        #print("acs Max Charge Limit Reached: " + self.battery_charge_mode, str(int(self.battery_max_charge_voltage)), str(int(self.battery_dcbus_voltage)))

                        if self.control_real_power_command <= -pRampRate:
                            print("acs Max Charge Limit: Matching pMaxChg at ramp")
                            self.control_real_power_command = self.control_real_power_command + pRampRate
                        elif self.control_real_power_command <= -1:
                            print("acs Max Charge Limit: Matching pMaxChg")
                            self.control_real_power_command = self.control_real_power_command + 1
                    else:
                        if -pMaxChg < pDcBus:  # We *can* charge

                            print("pSolar is " + str(pGrid) + ", pSolarImport is " + str(acsolar_setpoint))
                            print("Test: " + str(self.control_real_power_command) + " " + str(acsolar_setpoint) + " " + str(pRampRate))
                            if pGrid <= -1:     # TODO acsolar_setpoint:

                                if self.control_real_power_command - pRampRate >= -acsolar_setpoint:
                                    # if pGrid + pRampRate <= pImport:
                                    print("acs Charging at ramp")
                                    self.control_real_power_command = self.control_real_power_command - pRampRate
                                elif self.control_real_power_command - 1 >= -acsolar_setpoint:
                                    # elif pGrid + 1 <= pImport:
                                    print("acs Inverter accumulation")
                                    self.control_real_power_command = self.control_real_power_command - 1
                            #else:
                            #    print("Solar charging: Limiting to pImport")
                            #    self.control_real_power_command = self.control_real_power_command + 1
                        else:
                            if self.control_real_power_command <= -pRampRate:
                                print("acs Matching pMaxChg at ramp")
                                self.control_real_power_command = self.control_real_power_command + pRampRate
                            elif self.control_real_power_command <= -1:
                                print("acs Matching pMaxChg")
                                self.control_real_power_command = self.control_real_power_command + 1

                # ######################################################################################### #
                # PEAK SHAVING / LOAD SHIFT                                                                 #
                # ######################################################################################### #
                peak_active = False
                peak_setpoint = 0

                # Peak Shave period 1
                if self.peak_shave1_enabled:
                    # Now we need to check if we're Active, i.e. the current time is within the configured time
                    if self.check_active_time(time(self.peak_shave1_start_hour, self.peak_shave1_start_min),
                                              time(self.peak_shave1_end_hour, self.peak_shave1_end_min)):
                        self.peak_shave1_active = True                                                  # Use the lowest export limit for the current time period

                        peak_setpoint = self.peak_shave1_limit
                    else:
                        self.peak_shave1_active = False

                    self.peak_shave1_params[0] = self.peak_shave1_enabled
                    self.peak_shave1_params[1] = self.peak_shave1_limit
                    self.peak_shave1_params[2] = self.peak_shave1_start_hour
                    self.peak_shave1_params[3] = self.peak_shave1_start_min
                    self.peak_shave1_params[4] = self.peak_shave1_end_hour
                    self.peak_shave1_params[5] = self.peak_shave1_end_min
                    self.peak_shave1_params[6] = 0
                    self.peak_shave1_params[7] = 0
                    self.peak_shave1_params[8] = 0
                    self.peak_shave1_params[9] = "Active" if self.peak_shave1_active else ""
                else:
                    self.peak_shave1_active = False
                    self.peak_shave1_params = [0] * 10
                    self.peak_shave1_params[9] = ""

                # Peak Shave period 2
                if self.peak_shave2_enabled:
                    # Now we need to check if we're Active, i.e. the current time is within the configured time
                    if self.check_active_time(time(self.peak_shave2_start_hour, self.peak_shave2_start_min),
                                              time(self.peak_shave2_end_hour, self.peak_shave2_end_min)):
                        self.peak_shave2_active = True

                        if self.peak_shave1_active:                                                     # Use the lowest export limit for the current time period
                            if self.peak_shave2_limit < peak_setpoint:
                                peak_setpoint = self.peak_shave2_limit
                        else:
                            peak_setpoint = self.peak_shave2_limit
                    else:
                        self.peak_shave2_active = False

                    self.peak_shave2_params[0] = self.peak_shave2_enabled
                    self.peak_shave2_params[1] = self.peak_shave2_limit
                    self.peak_shave2_params[2] = self.peak_shave2_start_hour
                    self.peak_shave2_params[3] = self.peak_shave2_start_min
                    self.peak_shave2_params[4] = self.peak_shave2_end_hour
                    self.peak_shave2_params[5] = self.peak_shave2_end_min
                    self.peak_shave2_params[6] = 0
                    self.peak_shave2_params[7] = 0
                    self.peak_shave2_params[8] = 0
                    self.peak_shave2_params[9] = "Active" if self.peak_shave2_active else ""
                else:
                    self.peak_shave2_active = False
                    self.peak_shave2_params = [0] * 10
                    self.peak_shave2_params[9] = ""

                # Peak Shave period 3
                if self.peak_shave3_enabled:
                    # Now we need to check if we're Active, i.e. the current time is within the configured time
                    if self.check_active_time(time(self.peak_shave3_start_hour, self.peak_shave3_start_min),
                                              time(self.peak_shave3_end_hour, self.peak_shave3_end_min)):
                        self.peak_shave3_active = True

                        if self.peak_shave1_active or self.peak_shave2_active:                          # Use the lowest export limit for the current time period
                            if self.peak_shave3_limit < peak_setpoint:
                                peak_setpoint = self.peak_shave3_limit
                        else:
                            peak_setpoint = self.peak_shave3_limit
                    else:
                        self.peak_shave3_active = False

                    self.peak_shave3_params[0] = self.peak_shave3_enabled
                    self.peak_shave3_params[1] = self.peak_shave3_limit
                    self.peak_shave3_params[2] = self.peak_shave3_start_hour
                    self.peak_shave3_params[3] = self.peak_shave3_start_min
                    self.peak_shave3_params[4] = self.peak_shave3_end_hour
                    self.peak_shave3_params[5] = self.peak_shave3_end_min
                    self.peak_shave3_params[6] = 0
                    self.peak_shave3_params[7] = 0
                    self.peak_shave3_params[8] = 0
                    self.peak_shave3_params[9] = "Active" if self.peak_shave3_active else ""
                else:
                    self.peak_shave3_active = False
                    self.peak_shave3_params = [0] * 10
                    self.peak_shave3_params[9] = ""

                # Now we know what we need to command from the inverter, we can filter it through other tasks
                if self.peak_shave1_active or self.peak_shave2_active or self.peak_shave3_active:

                    peak_active = True

                    if (self.battery_charge_mode == "soc_charge" and (float(self.battery_min_discharge_soc) >= float(self.battery_soc))) or \
                       (self.battery_charge_mode == "volt_charge" and (int(self.battery_min_discharge_voltage) >= int(self.battery_dcbus_voltage))):

                        if pGrid >= 0:  # Hopefully this prevents the peak-shaving code from limiting solar (or other) import when we're exporting (-grid)
                            if (self.battery_charge_mode == "soc_charge" and (int(self.battery_min_discharge_soc) == int(self.battery_soc))) or \
                               (self.battery_charge_mode == "volt_charge" and (int(self.battery_min_discharge_voltage) - 1 <= int(self.battery_dcbus_voltage) <= int(self.battery_min_discharge_voltage) + 1)):
                                # Target recovered. zero commanded power. When in voltage mode the Kore value wanders a fair amount, hence the wide hysteresis. Ugh.
                                #print("catch 6")
                                self.control_real_power_command = 0
                                #print("3. Zeroing")

                            elif (self.battery_charge_mode == "soc_charge" and (float(self.battery_min_discharge_soc) > float(self.battery_soc))) or \
                                 (self.battery_charge_mode == "volt_charge" and (int(self.battery_min_discharge_voltage) > int(self.battery_dcbus_voltage))):
                                if pDcBus > -1:  # Battery is exporting so we need to import to compensate
                                    self.control_real_power_command = (self.control_real_power_command - 1)
                                    #print("2. Trickle-charging at 1kW")
                    else:
                        if pGrid >= (peak_setpoint + 1):
                            if pMaxDChg > pDcBus:  # We *can* discharge
                                if self.control_real_power_command < pExport:  # If we don't limit import, even if the inverter curtails the actual commanded power, the actual value of
                                    if (pGrid - pRampRate) >= pRampRate:  # self.control_real_power_command + pRampRate <= pGrid:
                                        #print("Discharging at ramp")
                                        self.control_real_power_command = self.control_real_power_command + pRampRate
                                    elif (pGrid - pRampRate) >= 1:  # self.control_real_power_command + 1 <= pGrid:
                                        #print("Inverter reduction")
                                        self.control_real_power_command = self.control_real_power_command + 1
                                    else:
                                        pass
                                        #print("test 4")
                                else:
                                    #print("Limiting to pExport")
                                    self.control_real_power_command = self.control_real_power_command - 1
                            else:
                                if self.control_real_power_command >= pRampRate:
                                    #print("Matching pMaxDChg at ramp")
                                    self.control_real_power_command = self.control_real_power_command - pRampRate
                                elif self.control_real_power_command >= 1:
                                    #print("Matching pMaxDChg")
                                    self.control_real_power_command = self.control_real_power_command - 1
                                else:
                                    pass
                                    #print("test 3")
                        elif pGrid <= -1:   # Compensate for negative overshoot if AC Solar isn't doing it for us.
                            if not acsolar_active:
                                self.control_real_power_command = self.control_real_power_command - 1  
                        else:
                            pass
                            #print("test 2")

                # ######################################################################################### #
                # CLIENT TASKS - Customer stuff that doesn't fit into normal control patterns               #
                # ######################################################################################### #
                client_tasks_active = False

                if self.client_tasks_enabled:

                    # Now we need to check if we're Active, i.e. the current time is within the configured time
                    if self.check_active_time(time(self.client_tasks_start_hour, self.client_tasks_start_min),
                                              time(self.client_tasks_end_hour, self.client_tasks_end_min)):
                        client_tasks_active = True
                    else:
                        client_tasks_active = False

                    self.client_tasks_params[0] = self.client_tasks_enabled
                    self.client_tasks_params[1] = "Active" if client_tasks_active else ""
                else:
                    client_tasks_active = False
                    self.client_tasks_params = [0] * 20
                    self.client_tasks_params[9] = ""
                
                # So a client task has been enabled, and it's within the specified time frame.
                if client_tasks_active:

                    ############################################################################################################################################################### 
                    # CXP00780_4 - Swadlincote - 10-stack system with multiple modes of operation as defined by "Joulen Metro Site EMS Behaviour Functions 2024.09.18.pdf".
                    # With their permission we've split the mandatory modes into the LSB and the Market-Specific modes in the MSB, which leaves room for additional modes.
                    #
                    # Solar Power and Load Meter are on 127.0.0.1 and read from our own SCADA which the client populates.
                    # 
                    # Mode 0: Not strictly defined but we need an idle mode for testing
                    # - Allow system enable and site idle. Respond to charge / discharge power if required.
                    #
                    # Mode 1: Business as usual / Dumb Battery Mode.
                    # - Effectively we always offset the site load, whether solar is available or not, as long as the inverter output matches the load. Source power is not entirely known.
                    # - If there's solar, it'll likely flow to the load before the battery as the inverter is the path of least resistance.
                    # - If there's no solar, the battery will discharge against the load down to our minimum Voltage or SoC limit.
                    # - If there's no battery, the load will continue using the grid.
                    #
                    # Mode 2: PARIS Optimisation Mode. 
                    # - This looks very much like default control where a client sets an inverter value in register 40257 to charge or discharge the battery. 
                    #
                    # Mode 3: Hold Charge (BAU and SoC Limits (I'll flesh this out later)
                    # - Charge / discharge as per BAU to remain within SoC limits so that the battery is ready for trading.
                    # - We need the latest Kore firmware and Gareth's calibration-enabled FlexMod_KoreBattery.py to ensure we have accurate SoC readings.
                    # 
                    # Mode 4: DC Mode
                    # - Dynamic containment operation within set times. Requires Shaun's kit as the Python response isn't quick enough.
                    ###############################################################################################################################################################
                    
                    if self.client_tasks_client == "CXP-00780_4":
                        
                        mode = self.client_param2                                                   # 46008, Joulen Operational Mode
                        mode = 1    # Forced into DBM / BAU mode until Joulen take control at a later date
                        
                        if mode == 0x00:                                                            # Idle, undocumented. Zero the inverter power until they select a mode.
                            pass
                        elif mode == 0x01:                                                          # (Mandatory) BOU / Dumb Mode
                            
                            # This might be easier than originally anticipated. Push power out to meet the load, whether it ultimately consists of Solar, Battery or either.
                            # We don't necessarily know what's going in, just what's going out. The load reading won't diminish because they read beyond both Grid and BESS.
                            
                            # A little misleading, load here is the site load, grid is the power out of the battery/solar measured at the Acuvim
                            if self.ac_meter_load_power > self.ac_meter_grid_power:
                                print("one")
                                peak_setpoint = self.ac_meter_load_power - self.ac_meter_grid_power
                                
                                # React on change only to avoid overshooting on slow-moving real power
                                if peak_setpoint != self.client_peak_setpoint_last:
                                    print("two")
                                    self.client_peak_setpoint_last = peak_setpoint
                                    
                                    # Discharge as long as we're above the minimum limit, whether that's Battery SoC or Voltage.
                                    if (self.battery_charge_mode == "soc_charge" and (float(self.battery_min_discharge_soc) >= float(self.battery_soc))) or \
                                       (self.battery_charge_mode == "volt_charge" and (int(self.battery_min_discharge_voltage) >= int(self.battery_dcbus_voltage))):
                                        print("three")
                                        
                                        if self.control_real_power_command >= pRampRate:
                                            print("five")
                                            self.control_real_power_command = self.control_real_power_command - pRampRate
                                        
                                        elif self.control_real_power_command >= 1:
                                            print("six")
                                            self.control_real_power_command = self.control_real_power_command - 1
                                
                                    else:
                                        if peak_setpoint > 0:
                                            print("twelve")
                                            if pMaxDChg > pDcBus:  # We *can* discharge
                                                print("thirteen")
                                                #if self.control_real_power_command < pExport:   # We want to discharge the battery but not exceed the inverter or site export limits, whichever is smaller.
                                                if self.ac_meter_grid_power < pExport:   # We want to discharge the battery but not exceed the inverter or site export limits, whichever is smaller.
                                                    print("fourteen")
                                                    if peak_setpoint >= pRampRate:
                                                        print("fifteen")
                                                        self.control_real_power_command = self.control_real_power_command + pRampRate
                                                    elif pDcBus >= 1: 
                                                        print("sixteen")
                                                        self.control_real_power_command = self.control_real_power_command + 1   
                                                    #else:    # We overshot or EPH power dropped
                                                    #    self.control_real_power_command = self.control_real_power_command - 1
                                                    
                                                else:
                                                    print("seventeen")
                                                    self.control_real_power_command = self.control_real_power_command - 1
                                            else:
                                                print("eighteen")
                                                if self.control_real_power_command >= pRampRate:
                                                    print("nineteen")
                                                    self.control_real_power_command = self.control_real_power_command - pRampRate
                                                
                                                elif self.control_real_power_command >= 1:
                                                    print("twenty")
                                                    self.control_real_power_command = self.control_real_power_command - 1
                                        else:
                                            pass
                                            print("Catch unknown")
                                            
                            elif self.ac_meter_grid_power >= self.ac_meter_load_power+1:# We're adding a little hysteresis on the overshoot to avoid hunting.
                                print("twenty-one")
                                # Overshoot so we back off again
                                if self.control_real_power_command >= pRampRate:
                                    print("twenty-two")
                                    self.control_real_power_command = self.control_real_power_command - pRampRate
                                
                                elif self.control_real_power_command >= 1:
                                    print("twenty-three")
                                    self.control_real_power_command = self.control_real_power_command - 1
                            
                        elif mode == 0x02:                                                          # (Mandatory) PARIS Optimisation Mode
                            pass
                        elif mode == 0x04:                                                          # (Mandatory) Hold Charge Mode 
                            pass
                        elif mode == 0x100:                                                         # (Market Specific) DC Mode
                            pass
                    
                    ###############################################################################################################################################################
                    # CXP-00853 (Woodbine, York) is a 2-stack Flex OD system connected to an AMPT Solar array.                                                  
                    # No special operational code, only the default peak shaving is enabled to offset load power with DC-coupled Solar energy.
                    # There is talk of exporting power from the solar array before it curtails, but it hasn't been coded yet.
                    ###############################################################################################################################################################
                      
                    if self.client_tasks_client == "CXP-00853":
                        
                        # 1) DigIO 0 and 1 need to be enabled for the Inverter and Battery to function. If the code doesn't do it, override the IO and leave it overridden.
                        # 2) Set Peak Shave / Load Shift to "Enabled", Peak Limit 0kW (gives +/-1kW tolerance), start/end times to 00:00 (always active).
                        # 3) Set Client Tasks to "Enabled", "None" so that Control Status reads "[4] Client SYstem Running" when the BESS is Enabled. 
                        pass
                    
                    ###############################################################################################################################################################
                    # CXP-00884 (EcoPark) Is a 4-stack Flex OD battery sponge using an AC Solar input but on a shared AC bus, so we can't do the usual and monitor negative grid 
                    # meter swings as the data is provided to us via BACnet.
                    # AC Solar data is read from 127.0.0.1 through an MSP MBTCP-Bacnet bridge application (BACnetServer.py) as a positive power for total Solar generation. 
                    # We only absorb power above what EPH needs.
                    # AC Load meter, EcoPark House (EPH) is an indication of the building's Import power.
                    #
                    # There is still a consideration to enable off-peak grid charging but the solar panels are so good, I'm not sure if it'll be needed, or desired.
                    #
                    # The system is wired like this:    [Grid / AC Solar]-----[AC Meter, EcoPark House (load)]-----[BESS]
                    # So if Solar > Load, AC Meter will read the load of the building, even though it is likely to ge all Solar generated. This is confusing as it is net zero but
                    # reporting the load regardless.
                    # [Grid / AC Solar]->>>-[AC Meter, EcoPark House (load)]-----[BESS]
                    #
                    # If Load > Solar, we inject opposing available BESS power onto the AC bus equivalent to the load, until the AC Meter reads close to net zero 
                    # [Grid / AC Solar]->>>-[AC Meter, EcoPark House (load)]-<<<-[BESS]
                    #
                    # It would have been preferable if the BESS sat between the Grid / AC Solar and EPH to maintain a reported 0 power throughout, but neverminnd. It still works.
                    # Local operation with Remote monitoring only.
                    #
                    # Refer to "EPH Battery Charge & Discharge Cause & Effect", revision P02 (20/05/2022) for further details.
                    ###############################################################################################################################################################
                    
                    if self.client_tasks_client == "CXP-00884":
                        
                        # So first of all we need to know if we're charging, discharging or idling.
                        
                        # EcoPark house is absorbing "self.ac_meter_grid_power worth" of "self.ac_solar_ac_power" on the AC bus, so we can use the remainder to charge the battery
                        if self.ac_solar_ac_power > self.ac_meter_grid_power:
                        
                            peak_setpoint = self.ac_solar_ac_power - self.ac_meter_grid_power
                            
                            # React on change only to avoid overshooting on slow bacnet updates!
                            if peak_setpoint != self.client_CXP00884_setpoint_last:
                                self.client_CXP00884_setpoint_last = peak_setpoint
                                
                                # DUPLICATE #
                                # The bacnet ac meter appears be updating so slowly, so if we react quickly, we'll often overshoot. 
                                # Wait for the bus to settle on a new value before assessing it. TODO: if we go negative, the above code *does* overshoot.
                                #if peak_setpoint != self.client_CXP00884_setpoint_last:
                                #    self.client_CXP00884_setpoint_last = peak_setpoint
                                    
                                # Note: this is basically the same as the default AC Solar charge mode but instead of reading the grid export, we have a client-reported Solar Power value
                                if (self.battery_charge_mode == "soc_charge" and (float(self.battery_max_charge_soc) <= float(self.battery_soc))) or \
                                   (self.battery_charge_mode == "volt_charge" and (int(self.battery_max_charge_voltage) <= int(self.battery_dcbus_voltage))):   
                                   
                                   # Ramp down the import if we've hit the target. Any excess solar then goes out the door (grid)
                                    if self.control_real_power_command <= -pRampRate:
                                        self.control_real_power_command = self.control_real_power_command + pRampRate
                                    elif self.control_real_power_command <= -1:
                                        self.control_real_power_command = self.control_real_power_command + 1   # Which gets us to around 0kW
                                   
                                else:   # We haven't hit our target SoC / Voltage
                                    ac_solar_setpoint = int(self.ac_solar_ac_power - self.ac_meter_grid_power)    # We can charge with whatever EcoPark House (EPH) isn't using
                                    
                                    if -pMaxChg < pDcBus:  # We *can* charge Because Kore says we haven't exceeded the DC Bus power budget

                                        if self.control_real_power_command - pRampRate >= -ac_solar_setpoint:
                                            self.control_real_power_command = self.control_real_power_command - pRampRate
                                        elif self.control_real_power_command - 1 >= -ac_solar_setpoint:
                                            self.control_real_power_command = self.control_real_power_command - 1
                                    else:
                                        if self.control_real_power_command <= -pRampRate:
                                            self.control_real_power_command = self.control_real_power_command + pRampRate
                                        elif self.control_real_power_command <= -1:
                                            self.control_real_power_command = self.control_real_power_command + 1
                        
                        # There's little solar available, so we need to push the power difference into EPH from the battery.
                        elif self.ac_meter_grid_power >= self.ac_solar_ac_power+1:
                        
                            peak_setpoint = self.ac_meter_grid_power - self.ac_solar_ac_power
                            
                            # React on change only to avoid overshooting on slow bacnet updates!
                            if peak_setpoint != self.client_CXP00884_setpoint_last:
                                self.client_CXP00884_setpoint_last = peak_setpoint
                             
                                # Note: this is basically like peak shaving but we're offsetting the difference between what EPH needs and the available solar. 
                                # We have to use the DC Bus power as a reference to ensure wee're offsetting the load *and* the parasitic loads.
                                if (self.battery_charge_mode == "soc_charge" and (float(self.battery_min_discharge_soc) >= float(self.battery_soc))) or \
                                   (self.battery_charge_mode == "volt_charge" and (int(self.battery_min_discharge_voltage) >= int(self.battery_dcbus_voltage))):

                                    if (self.battery_charge_mode == "soc_charge" and (int(self.battery_min_discharge_soc) == int(self.battery_soc))) or \
                                       (self.battery_charge_mode == "volt_charge" and (int(self.battery_min_discharge_voltage) - 1 <= int(self.battery_dcbus_voltage) <= int(self.battery_min_discharge_voltage) + 1)):
                                        # Target recovered. zero commanded power. When in voltage mode the Kore value wanders a fair amount, hence the wide hysteresis. Ugh.
                                        self.control_real_power_command = 0
                                        
                                    elif (self.battery_charge_mode == "soc_charge" and (float(self.battery_min_discharge_soc) > float(self.battery_soc))) or \
                                         (self.battery_charge_mode == "volt_charge" and (int(self.battery_min_discharge_voltage) > int(self.battery_dcbus_voltage))):
                                        
                                        # pRampRate here? ----
                                        
                                        #else
                                        if pDcBus > -1:  # Battery is exporting so we need to import to compensate
                                            self.control_real_power_command = (self.control_real_power_command - 1)
                                else:
                                    if peak_setpoint > 0:
                                        if pMaxDChg > pDcBus:  # We *can* discharge
                                            if self.control_real_power_command < pExport:   # We want to discharge the battery but not exceed the inverter or site export limits, whichever is smaller.
                                                if peak_setpoint >= pRampRate:
                                                    self.control_real_power_command = self.control_real_power_command + pRampRate
                                                elif pDcBus >= 1: 
                                                    self.control_real_power_command = self.control_real_power_command + 1   
                                                else:    # We overshot or EPH power dropped
                                                    self.control_real_power_command = self.control_real_power_command - 1
                                                
                                            else:
                                                self.control_real_power_command = self.control_real_power_command - 1
                                        else:
                                            if self.control_real_power_command >= pRampRate:
                                                self.control_real_power_command = self.control_real_power_command - pRampRate
                                            
                                            elif self.control_real_power_command >= 1:
                                                self.control_real_power_command = self.control_real_power_command - 1
                        
                        # We're nicely balanced, reduce inverter usage
                        else:
                            # Idle the inverter
                            pass   
                        
                    ###############################################################################################################################################################
                    # CXP-00957 (Cummins) allows the remote operator to select which source (Grid / PV / FC) he or she wishes to use in order to charge the primary battery.
                    # There are a number of modes that can be selected to best use the available power but for our part they are only ended up being symbolic, because Cummins would 
                    # select a mode on their HMI, then a power source (auto or manually driven), before charging or discharging the battery as required.
                    # What started out as a complex state diagram became a switch on the subsystem task.
                    #
                    # Grid source entails starting and stopping the inverter in the usual way
                    # PV is effectively always on, but we have to use GET/POST HTTP api requests to configure the optimizer output power (current lookup table) between 0 and >PV 
                    # Setpoint.
                    # - I've implemented it here, but set_pv_setpoint and its lookup table need to move to the AMPT driver.
                    # FC is still a work in progress, at time of writing they haven't installed it yet, so there is driver work to complete.
                    #
                    # Dumb battery otherwise with Remote control by Cummins / University of Nottingham
                    ###############################################################################################################################################################
                    
                    elif self.client_tasks_client == "CXP-00957":
                        
                        # PV SOURCE Selected
                        if self.client_CXP00957_active_mode & (1 << 0):
                        
                            if not self.client_CXP00957_active_mode & (1 << 10):    # We don't want to enable the PV if OFF Mode is actively disabling it
                                self.client_CXP00957_pv_active = True

                                # if the setpoint changes, update it.
                                if self.client_CXP00957_pv_output_setpoint_last != self.client_CXP00957_pv_output_setpoint:
                                    self.client_CXP00957_pv_output_setpoint_last = self.client_CXP00957_pv_output_setpoint
                                    self.set_pv_setpoint(self.client_CXP00957_pv_output_setpoint)

                                # We can't turn the PV off at the moment so assume always on
                                self.client_CXP00957_active_mode_echo |= (1 << 0)
                            else:
                                self.client_CXP00957_active_mode_echo &= ~(1 << 0)
                                self.client_CXP00957_pv_active = False
                            
                        else:
                            # Disable the PV by minimising import power
                            if self.client_CXP00957_pv_active == True:
                                self.set_pv_setpoint(0)
                                self.client_CXP00957_pv_active = False

                            # We can't turn it off yet, but we're echoing their inputs
                            self.client_CXP00957_active_mode_echo &= ~(1 << 0)

                        # Fuel Cell SOURCE Selected
                        if self.client_CXP00957_active_mode & (1 << 1):
                            self.client_CXP00957_fc_active = True

                            # TODO: Report that it's active once it is
                            self.client_CXP00957_active_mode_echo |= (1 << 1)
                        else:
                            self.client_CXP00957_active_mode_echo &= ~(1 << 1)
                            self.client_CXP00957_fc_active = False
                            
                        # Grid SOURCE Selected
                        if self.client_CXP00957_active_mode & (1 << 2):
                            
                            if not self.client_CXP00957_active_mode & (1 << 10):    # We don't want to enable the inverter if OFF Mode is actively disabling it
                                # Check whether the Grid-Facing Inverter is in Following mode
                                if self.inverter_following_enable:                                            
                                    self.client_CXP00957_active_mode_echo |= (1 << 2)
                                    self.client_CXP00957_grid_active = True
                                else:                            
                                    # Enable it!
                                    #print("catch 7")
                                    self.control_real_power_command = 0  # TODO: Soft or hard ramp down
                                    self.inverter_following_enable = True
                                
                                    self.client_CXP00957_active_mode_echo &= ~(1 << 2)
                                    self.client_CXP00957_grid_active = False
                              
                            else:
                                self.client_CXP00957_active_mode_echo &= ~(1 << 2)
                                self.client_CXP00957_grid_active = False
                        else:
                            
                            if self.inverter_following_enable: 
                                self.client_CXP00957_active_mode_echo |= (1 << 2)
                                self.client_CXP00957_grid_active = True
                                
                                # Disable it!
                                #print("catch 8")
                                self.control_real_power_command = 0  # TODO: Soft or hard ramp down
                                self.inverter_following_enable = False  
                            else:
                                self.client_CXP00957_active_mode_echo &= ~(1 << 2)
                                self.client_CXP00957_grid_active = False
                               
                        # PV Bypass SOURCE Selected
                        if self.client_CXP00957_active_mode & (1 << 3):
                            self.client_CXP00957_pv_bypass_active = True
                            self.client_CXP00957_active_mode_echo |= (1 << 3)
                        else:
                            self.client_CXP00957_active_mode_echo &= ~(1 << 3)
                            self.client_CXP00957_pv_bypass_active = False
                            
                        # The control modes are well defined by Cummins but setting these bits does very little for the BESS. Control is generally via registers 255 / 257 and the above inputs.

                        # Energy Store MODE #
                        if self.client_CXP00957_active_mode & (1 << 8):
                            self.client_CXP00957_active_mode_echo |= (1 << 8)
                        else:
                            self.client_CXP00957_active_mode_echo &= ~(1 << 8)

                        # Peak Shave MODE #
                        if self.client_CXP00957_active_mode & (1 << 9):
                            self.client_CXP00957_active_mode_echo |= (1 << 9)
                        else:
                            self.client_CXP00957_active_mode_echo &= ~(1 << 9)

                        # Off MODE #
                        if self.client_CXP00957_active_mode & (1 << 10):
                            self.client_CXP00957_active_mode_echo |= (1 << 10)
                        else:
                            self.client_CXP00957_active_mode_echo &= ~(1 << 10)


                        self.client_tasks_params[2] = "Energy Store Mode" if self.client_CXP00957_active_mode & (1 << 8) else \
                                                      "Peak Shave Mode" if self.client_CXP00957_active_mode & (1 << 9) else \
                                                      "Off Mode" if self.client_CXP00957_active_mode & (1 << 10) else "Invalid Mode"
                        self.client_tasks_params[3] = self.client_CXP00957_charge_setpoint
                        self.client_tasks_params[4] = self.client_CXP00957_discharge_setpoint
                        self.client_tasks_params[5] = self.client_CXP00957_pv_bypass_output_setpoint
                        self.client_tasks_params[6] = self.client_CXP00957_grid_output_setpoint
                        self.client_tasks_params[7] = self.client_CXP00957_pv_output_setpoint
                        self.client_tasks_params[8] = self.client_CXP00957_fc_output_setpoint
                        self.client_tasks_params[9] = "Active" if self.client_CXP00957_pv_bypass_active else ""
                        self.client_tasks_params[10] = "Active" if self.client_CXP00957_grid_active else ""
                        self.client_tasks_params[11] = "Active" if self.client_CXP00957_pv_active else ""
                        self.client_tasks_params[12] = "Active" if self.client_CXP00957_fc_active else ""

                        # Retrieve the data from the external controller and display it on screen

                    else:
                        pass

                # Catch-all that drops the inverter to 0 if tou / peak time etc are not in operation
                if not tou_active and not acsolar_active and not peak_active and not client_tasks_active:
                    
                    # Reduce power if still commanded above zero
                    if self.control_real_power_command >= pRampRate:
                        self.control_real_power_command = self.control_real_power_command - pRampRate
                    elif self.control_real_power_command >= 1:
                        self.control_real_power_command = self.control_real_power_command - 1

                    # Increase power if commanded below zero
                    elif self.control_real_power_command <= -pRampRate:
                        self.control_real_power_command = self.control_real_power_command + pRampRate
                    elif self.control_real_power_command <= -1:
                        self.control_real_power_command = self.control_real_power_command + 1

                self.system_status_text = "[4] Client System Running"

            else:
                self.system_status_text = "[4] Disabling Client System"

                # AC Generator
                if self.acgen_charge1_enabled:
                    self.acgen_state = 0
                    self.acgen_timeout = 30
                    self.acgen_start = False
                    self.acgen_mains_parallel_mode = False

                self.client_state = 3

        if len(self.actions) == 0:
            self.actions.append(0)  # Dummy

        self.client_heartbeat = self.heartbeat

        # Modify self.outputs
        self.outputs[2][0] = self.client_heartbeat
        self.outputs[2][1] = self.client_CXP00957_active_mode_echo
        self.outputs[2][2] = 0
        self.outputs[2][3] = 0
        self.outputs[2][4] = 0
        self.outputs[2][5] = 0
        self.outputs[2][6] = 0
        self.outputs[2][7] = 0
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

        loop_time = ltime.time() - s

    def set_pv_setpoint(self, setpoint_kW):
    
        OPT_QTY = 13
        oc_set = 0
        
        # Calculate the total current required by dividing the power request by the battery bus voltage (as that's where we want to be certain of, not at the AMPT CU.
        if self.battery_dcbus_voltage > 0:
            total_current = (setpoint_kW*1000) / self.battery_dcbus_voltage
        else:
            total_current = 0
            
        # Calculate the optimizer current by dividing the total required current by the number of optimizers
        opt_current = total_current / OPT_QTY
        
        # Search self. solar_array for the current above the requested current (cap at top and bottom)
        for oc, amps in self.solar_array.items():
            oc_set = oc
            if amps > opt_current:      # This should cap at the highest allowed value in the array regardless of what setpoint is aasked for.
                break
            
        # This is the setpoint for all Optimizers because we're not targeting a serial number, just the wildcard "*"
        data = {'oc': setpoint_kW,}

        response = requests.post('http://192.168.1.188:8080/ocapi', data=data, auth=('admin', 'password'))
        print(response)  # TODO check response and maybe retry?

    def check_active_time(self, start_time, end_time):
        # We're using 24hr time and returning true if the current time falls within the user-defined window
        actual_time = datetime.now().time()     # Use the local time, not UTC for logic, UTC for reporting

        if start_time < end_time:                                                                   # Time window sits within the same day
            #print(actual_time, start_time, end_time)
            return actual_time >= start_time and actual_time <= end_time
        else:
            return actual_time >= start_time or actual_time <= end_time

    def get_acgen_runstate(self):                                                                        # Basically the Generator is running if its output frequency is within tolerance
        if 45 < self.acgen_ac_frequency < 55:
            return True                                                                             # Else we must Follow a running generator first
        else:
            return False                                                                            # We can go into Forming

    def set_inputs(self, inputs):
        if inputs[0] == self.uid:
            self.inputs = inputs[2:]  # Save all module data from the controller onwards

            if len(self.inputs) < 25:                                                               # Wait until all modules have loaded
                return [SET_INPUTS_ACK]

            # Controller (module type 1)
            if self.inputs[ModTypes.CONTROL.value] is not None:
                for dev in self.inputs[ModTypes.CONTROL.value]:
                    # Check Controller Enable State
                    if dev[2][3] & 0x01:                                                            # Controller "operating_state_scada", bit 0 (Enable)
                        self.system_enabled = True
                    else:
                        self.system_enabled = False

                    # Commanded real power if we're operating in remote mode - this is going to be problematic if we're doing auto control in remote mode. Looking at you, Swadlincote! Force override flag?
                    if dev[2][3] & (1 << 1):                                                        # Check RemLoc Echo bit
                        #print("catch 2")
                        self.control_real_power_command = dev[2][4]                                 # Partitioned in the main loop if inverter count > 1
                        #print("Real power command = " + str(self.control_real_power_command))
            # Battery (module type 2)
            if self.inputs[ModTypes.BATTERY.value] is not None:
                for dev in self.inputs[ModTypes.BATTERY.value]:                                     # A BESS usually has one, a site might have multiple BESSs
                    if dev[1]:                                                                      # Battery Module is enabled
                        self.battery_enabled = True                                                 # Battery Contactors are closed

                        self.battery_soc = dev[2][2]
                        self.battery_dcbus_voltage = dev[2][4]
                        self.battery_dcbus_power = dev[2][6]
                        self.battery_max_charge_power = dev[2][8] / 1000                            # TODO: Check these two are calculated on active racks only
                        self.battery_max_discharge_power = dev[2][9] / 1000
                        self.battery_avg_cell_voltage = dev[2][12]

                        charge_mode = "client_battery_charge_mode_" + str(dev[0])
                        if charge_mode in self.dbData:
                            self.battery_charge_mode = self.dbData[charge_mode]

                        soc_charge_limit = "client_battery_charge_limit_" + str(dev[0])
                        if soc_charge_limit in self.dbData:
                            self.battery_max_charge_soc = self.dbData[soc_charge_limit]

                        soc_discharge_limit = "client_battery_discharge_limit_" + str(dev[0])
                        if soc_discharge_limit in self.dbData:
                            self.battery_min_discharge_soc = self.dbData[soc_discharge_limit]

                        volt_charge_limit = "client_battery_charge_voltage_limit_" + str(dev[0])
                        if volt_charge_limit in self.dbData:
                            self.battery_max_charge_voltage = self.dbData[volt_charge_limit]

                        volt_discharge_limit = "client_battery_discharge_voltage_limit_" + str(dev[0])
                        if volt_discharge_limit in self.dbData:
                            self.battery_min_discharge_voltage = self.dbData[volt_discharge_limit]

                    else:
                        self.battery_enabled = False

            # Inverter (module type 3)
            if self.inputs[ModTypes.INVERTER.value] is not None:
                for dev in self.inputs[ModTypes.INVERTER.value]:
                    if dev[1]:                                                                      # Check Inverter Enabled state
                        inverter_mode = "client_inverter_grid_mode_" + str(dev[0])
                        if inverter_mode in self.dbData:

                            # Inverter operating state
                            #self.inverter_state = dev[2][1]

                            if self.dbData[inverter_mode] == "Following":
                                self.inverter_following_enabled = True                              # This may be a bit premature

                                self.inverter_following_state = dev[2][1]

                                if self.inverter_following_state != 2:                              # Reset commanded power in fault conditions.
                                    #print("catch 3")
                                    self.control_real_power_command = 0

                                import_limit = "client_inverter_import_limit_" + str(dev[0])
                                if import_limit in self.dbData:
                                    self.inverter_import_limit = self.dbData[import_limit]

                                export_limit = "client_inverter_export_limit_" + str(dev[0])
                                if export_limit in self.dbData:
                                    self.inverter_export_limit = self.dbData[export_limit]

                                ramp_rate = "client_inverter_ramp_rate_" + str(dev[0])
                                if ramp_rate in self.dbData:
                                    self.inverter_ramp_rate = self.dbData[ramp_rate]

                            elif self.dbData[inverter_mode] == "Forming":
                                self.inverter_forming_enabled = True
                    #else:
                    #    self.inverter_following_enabled = False

            # AC Meter (module type 4)
            if self.inputs[ModTypes.AC_METER.value] is not None:
                for dev in self.inputs[ModTypes.AC_METER.value]:
                    if dev[1]:
                        # Here is where we distinguish between grid / load and grid+load metering
                        # Although in this project we only have one grid+load meter so should I bother?
                        meter_location = "client_ac_meter_location_" + str(dev[0])
                        if meter_location in self.dbData:
                            if self.dbData[meter_location] == "Grid" or self.dbData[meter_location] == "Grid_and_Load":
                                self.ac_meter_grid_power = dev[2][7]
                                self.ac_meter_grid_power_kva = dev[2][13]
                            elif self.dbData[meter_location] == "Load":
                                self.ac_meter_load_power = dev[2][7]

            # Digital IO (module type 6)
            if self.inputs[ModTypes.DIG_IO.value] is not None:
                for dev in self.inputs[ModTypes.DIG_IO.value]:
                    if dev[1]:

                        # Bank power relay (Control power to all the RMSCs)
                        control_power_state = 0
                        output = "client_bank_reset_op_" + str(dev[0])
                        if output in self.dbData:
                            if self.dbData[output] != "None":
                                uid, io = self.get_io(self.dbData[output])
                                if dev[0] == uid:
                                    if dev[2][5] & (1 << io * 2):
                                        control_power_state = 0x01

                        else:
                            # E-stop interlocks (on older systems)
                            output1 = "client_rack_1_6_reset_op_" + str(dev[0])
                            output2 = "client_rack_2_7_reset_op_" + str(dev[0])
                            output3 = "client_rack_3_8_reset_op_" + str(dev[0])
                            output4 = "client_rack_4_9_reset_op_" + str(dev[0])
                            output5 = "client_rack_5_10_reset_op_" + str(dev[0])

                            op1 = False
                            if output1 in self.dbData:
                                if self.dbData[output1] != "None":
                                    uid, io = self.get_io(self.dbData[output1])
                                    if dev[0] == uid:
                                        if dev[2][5] & (1 << io * 2):
                                            op1 = True

                            op2 = False
                            if output2 in self.dbData:
                                if self.dbData[output2] != "None":
                                    uid, io = self.get_io(self.dbData[output2])
                                    if dev[0] == uid:
                                        if dev[2][5] & (1 << io * 2):
                                            op2 = True

                            op3 = False
                            if output3 in self.dbData:
                                if self.dbData[output3] != "None":
                                    uid, io = self.get_io(self.dbData[output3])
                                    if dev[0] == uid:
                                        if dev[2][5] & (1 << io * 2):
                                            op3 = True

                            op4 = False
                            if output4 in self.dbData:
                                if self.dbData[output4] != "None":
                                    uid, io = self.get_io(self.dbData[output4])
                                    if dev[0] == uid:
                                        if dev[2][5] & (1 << io * 2):
                                            op4 = True

                            op5 = False
                            if output5 in self.dbData:
                                if self.dbData[output5] != "None":
                                    uid, io = self.get_io(self.dbData[output5])
                                    if dev[0] == uid:
                                        if dev[2][5] & (1 << io * 2):
                                            op5 = True

                            if op1 and op2 and op3 and op4 and op5:
                                control_power_state = 0
                            else:
                                control_power_state = 1

                        if control_power_state == 0x01:
                            self.bank_reset_enabled = False                                         # Inverted in hardware
                        else:
                            self.bank_reset_enabled = True

                        # Generator Start state
                        acgen_start_state = 0
                        output = "client_acgen_start_op_" + str(dev[0])
                        if output in self.dbData:
                            if self.dbData[output] != "None":
                                uid, io = self.get_io(self.dbData[output])
                                if dev[0] == uid:
                                    if dev[2][5] & (1 << io * 2):
                                        acgen_start_state = 0x01

                        if acgen_start_state == 0x01:
                            self.acgen_start_state = True
                        else:
                            self.acgen_start_state = False

                        # Generator Mains Parallel Mode state
                        acgen_mains_parallel_state = 0
                        output = "client_acgen_mains_parallel_op_" + str(dev[0])
                        if output in self.dbData:
                            if self.dbData[output] != "None":
                                uid, io = self.get_io(self.dbData[output])
                                if dev[0] == uid:
                                    if dev[2][5] & (1 << io * 2):
                                        acgen_mains_parallel_state = 0x01

                        if acgen_mains_parallel_state == 0x01:
                            self.acgen_mains_parallel_mode_state = True
                        else:
                            self.acgen_mains_parallel_mode_state = False

            # AC Generator (module type 15)
            if self.inputs[ModTypes.AC_GEN.value] is not None:
                for dev in self.inputs[ModTypes.AC_GEN.value]:
                    if dev[1]:
                        self.acgen_ac_frequency = dev[2][12]
                        
            # AC Solar (module type 17)
            if self.inputs[ModTypes.AC_SOLAR.value] is not None:
                for dev in self.inputs[ModTypes.AC_SOLAR.value]:
                    if dev[1]:
                        self.ac_solar_ac_power = dev[2][12]
            
            # DC Solar (module type 18)
            if self.inputs[ModTypes.DC_SOLAR.value] is not None:
                for dev in self.inputs[ModTypes.DC_SOLAR.value]:
                    if dev[1]:
                        self.dc_solar_dc_power = dev[2][7]

            # Client (module type 24)
            if self.inputs[ModTypes.CLIENT.value] is not None:
                for dev in self.inputs[ModTypes.CLIENT.value]:
                    if dev[1]:
                        self.client_enabled = True

                        # TOU 1 parameters ---------------------------------------------------------
                        grid_charge1_enabled = "client_grid_charge1_" + str(dev[24][0])
                        if grid_charge1_enabled in self.dbData:
                            if self.dbData[grid_charge1_enabled] == "Enabled":
                                self.grid_charge1_enabled = True
                            else:
                                self.grid_charge1_enabled = False

                        grid_charge1_import_limit = "client_grid_charge1_limit_" + str(dev[24][0])
                        if grid_charge1_import_limit in self.dbData:
                            self.grid_charge1_import_limit = int(self.dbData[grid_charge1_import_limit])

                        grid_charge1_start_hour = "client_grid_charge1_start_hr_" + str(dev[24][0])
                        if grid_charge1_start_hour in self.dbData:
                            self.grid_charge1_start_hour = int(self.dbData[grid_charge1_start_hour])

                        grid_charge1_start_min = "client_grid_charge1_start_min_" + str(dev[24][0])
                        if grid_charge1_start_min in self.dbData:
                            self.grid_charge1_start_min = int(self.dbData[grid_charge1_start_min])

                        grid_charge1_end_hour = "client_grid_charge1_end_hr_" + str(dev[24][0])
                        if grid_charge1_end_hour in self.dbData:
                            self.grid_charge1_end_hour = int(self.dbData[grid_charge1_end_hour])

                        grid_charge1_end_min = "client_grid_charge1_end_min_" + str(dev[24][0])
                        if grid_charge1_end_min in self.dbData:
                            self.grid_charge1_end_min = int(self.dbData[grid_charge1_end_min])

                        # TOU 2 parameters ---------------------------------------------------------
                        grid_charge2_enabled = "client_grid_charge2_" + str(dev[24][0])
                        if grid_charge2_enabled in self.dbData:
                            if self.dbData[grid_charge2_enabled] == "Enabled":
                                self.grid_charge2_enabled = True
                            else:
                                self.grid_charge2_enabled = False

                        grid_charge2_import_limit = "client_grid_charge2_limit_" + str(dev[24][0])
                        if grid_charge2_import_limit in self.dbData:
                            self.grid_charge2_import_limit = int(self.dbData[grid_charge2_import_limit])

                        grid_charge2_start_hour = "client_grid_charge2_start_hr_" + str(dev[24][0])
                        if grid_charge2_start_hour in self.dbData:
                            self.grid_charge2_start_hour = int(self.dbData[grid_charge2_start_hour])

                        grid_charge2_start_min = "client_grid_charge2_start_min_" + str(dev[24][0])
                        if grid_charge2_start_min in self.dbData:
                            self.grid_charge2_start_min = int(self.dbData[grid_charge2_start_min])

                        grid_charge2_end_hour = "client_grid_charge2_end_hr_" + str(dev[24][0])
                        if grid_charge2_end_hour in self.dbData:
                            self.grid_charge2_end_hour = int(self.dbData[grid_charge2_end_hour])

                        grid_charge2_end_min = "client_grid_charge2_end_min_" + str(dev[24][0])
                        if grid_charge2_end_min in self.dbData:
                            self.grid_charge2_end_min = int(self.dbData[grid_charge2_end_min])

                        # TOU 3 parameters ---------------------------------------------------------
                        grid_charge3_enabled = "client_grid_charge3_" + str(dev[24][0])
                        if grid_charge3_enabled in self.dbData:
                            if self.dbData[grid_charge3_enabled] == "Enabled":
                                self.grid_charge3_enabled = True
                            else:
                                self.grid_charge3_enabled = False

                        grid_charge3_import_limit = "client_grid_charge3_limit_" + str(dev[24][0])
                        if grid_charge3_import_limit in self.dbData:
                            self.grid_charge3_import_limit = int(self.dbData[grid_charge3_import_limit])

                        grid_charge3_start_hour = "client_grid_charge3_start_hr_" + str(dev[24][0])
                        if grid_charge3_start_hour in self.dbData:
                            self.grid_charge3_start_hour = int(self.dbData[grid_charge3_start_hour])

                        grid_charge3_start_min = "client_grid_charge3_start_min_" + str(dev[24][0])
                        if grid_charge3_start_min in self.dbData:
                            self.grid_charge3_start_min = int(self.dbData[grid_charge3_start_min])

                        grid_charge3_end_hour = "client_grid_charge3_end_hr_" + str(dev[24][0])
                        if grid_charge3_end_hour in self.dbData:
                            self.grid_charge3_end_hour = int(self.dbData[grid_charge3_end_hour])

                        grid_charge3_end_min = "client_grid_charge3_end_min_" + str(dev[24][0])
                        if grid_charge3_end_min in self.dbData:
                            self.grid_charge3_end_min = int(self.dbData[grid_charge3_end_min])

                        # AC Generator Charging 1 parameters ---------------------------------------
                        acgen_charge1_enabled = "client_acgen_charge1_" + str(dev[24][0])
                        if acgen_charge1_enabled in self.dbData:
                            if self.dbData[acgen_charge1_enabled] == "Enabled":
                                self.acgen_charge1_enabled = True
                            else:
                                self.acgen_charge1_enabled = False

                        acgen_charge1_start_hour = "client_acgen_charge1_start_hr_" + str(dev[24][0])
                        if acgen_charge1_start_hour in self.dbData:
                            self.acgen_charge1_start_hour = int(self.dbData[acgen_charge1_start_hour])

                        acgen_charge1_start_min = "client_acgen_charge1_start_min_" + str(dev[24][0])
                        if acgen_charge1_start_min in self.dbData:
                            self.acgen_charge1_start_min = int(self.dbData[acgen_charge1_start_min])

                        acgen_charge1_end_hour = "client_acgen_charge1_end_hr_" + str(dev[24][0])
                        if acgen_charge1_end_hour in self.dbData:
                            self.acgen_charge1_end_hour = int(self.dbData[acgen_charge1_end_hour])

                        acgen_charge1_end_min = "client_acgen_charge1_end_min_" + str(dev[24][0])
                        if acgen_charge1_end_min in self.dbData:
                            self.acgen_charge1_end_min = int(self.dbData[acgen_charge1_end_min])

                        acgen_max_load = "client_acgen_load_max_" + str(dev[24][0])
                        if acgen_max_load in self.dbData:
                            self.acgen_max_load = int(self.dbData[acgen_max_load])

                        # AC Solar Charging 1 parameters -------------------------------------------
                        acsolar_charge1_enabled = "client_acsolar_charge1_" + str(dev[24][0])
                        if acsolar_charge1_enabled in self.dbData:
                            if self.dbData[acsolar_charge1_enabled] == "Enabled":
                                self.acsolar_charge1_enabled = True
                            else:
                                self.acsolar_charge1_enabled = False

                        acsolar_charge1_import_limit = "client_acsolar_charge1_limit_" + str(dev[24][0])
                        if acsolar_charge1_import_limit in self.dbData:
                            self.acsolar_charge1_import_limit = int(self.dbData[acsolar_charge1_import_limit])

                        acsolar_charge1_start_hour = "client_acsolar_charge1_start_hr_" + str(dev[24][0])
                        if acsolar_charge1_start_hour in self.dbData:
                            self.acsolar_charge1_start_hour = int(self.dbData[acsolar_charge1_start_hour])

                        acsolar_charge1_start_min = "client_acsolar_charge1_start_min_" + str(dev[24][0])
                        if acsolar_charge1_start_min in self.dbData:
                            self.acsolar_charge1_start_min = int(self.dbData[acsolar_charge1_start_min])

                        acsolar_charge1_end_hour = "client_acsolar_charge1_end_hr_" + str(dev[24][0])
                        if acsolar_charge1_end_hour in self.dbData:
                            self.acsolar_charge1_end_hour = int(self.dbData[acsolar_charge1_end_hour])

                        acsolar_charge1_end_min = "client_acsolar_charge1_end_min_" + str(dev[24][0])
                        if acsolar_charge1_end_min in self.dbData:
                            self.acsolar_charge1_end_min = int(self.dbData[acsolar_charge1_end_min])

                        # Peak Shave 1 parameters --------------------------------------------------
                        peak_shave1_enabled = "client_peak_shave1_" + str(dev[24][0])
                        if peak_shave1_enabled in self.dbData:
                            if self.dbData[peak_shave1_enabled] == "Enabled":
                                self.peak_shave1_enabled = True
                            else:
                                self.peak_shave1_enabled = False

                        peak_shave1_limit = "client_peak_shave1_limit_" + str(dev[24][0])
                        if peak_shave1_limit in self.dbData:
                            self.peak_shave1_limit = int(self.dbData[peak_shave1_limit])

                        peak_shave1_start_hour = "client_peak_shave1_start_hr_" + str(dev[24][0])
                        if peak_shave1_start_hour in self.dbData:
                            self.peak_shave1_start_hour = int(self.dbData[peak_shave1_start_hour])

                        peak_shave1_start_min = "client_peak_shave1_start_min_" + str(dev[24][0])
                        if peak_shave1_start_min in self.dbData:
                            self.peak_shave1_start_min = int(self.dbData[peak_shave1_start_min])

                        peak_shave1_end_hour = "client_peak_shave1_end_hr_" + str(dev[24][0])
                        if peak_shave1_end_hour in self.dbData:
                            self.peak_shave1_end_hour = int(self.dbData[peak_shave1_end_hour])

                        peak_shave1_end_min = "client_peak_shave1_end_min_" + str(dev[24][0])
                        if peak_shave1_end_min in self.dbData:
                            self.peak_shave1_end_min = int(self.dbData[peak_shave1_end_min])

                        # Peak Shave 2 parameters --------------------------------------------------
                        peak_shave2_enabled = "client_peak_shave2_" + str(dev[24][0])
                        if peak_shave2_enabled in self.dbData:
                            if self.dbData[peak_shave2_enabled] == "Enabled":
                                self.peak_shave2_enabled = True
                            else:
                                self.peak_shave2_enabled = False

                        peak_shave2_limit = "client_peak_shave2_limit_" + str(dev[24][0])
                        if peak_shave2_limit in self.dbData:
                            self.peak_shave2_limit = int(self.dbData[peak_shave2_limit])

                        peak_shave2_start_hour = "client_peak_shave2_start_hr_" + str(dev[24][0])
                        if peak_shave2_start_hour in self.dbData:
                            self.peak_shave2_start_hour = int(self.dbData[peak_shave2_start_hour])

                        peak_shave2_start_min = "client_peak_shave2_start_min_" + str(dev[24][0])
                        if peak_shave2_start_min in self.dbData:
                            self.peak_shave2_start_min = int(self.dbData[peak_shave2_start_min])

                        peak_shave2_end_hour = "client_peak_shave2_end_hr_" + str(dev[24][0])
                        if peak_shave2_end_hour in self.dbData:
                            self.peak_shave2_end_hour = int(self.dbData[peak_shave2_end_hour])

                        peak_shave2_end_min = "client_peak_shave2_end_min_" + str(dev[24][0])
                        if peak_shave2_end_min in self.dbData:
                            self.peak_shave2_end_min = int(self.dbData[peak_shave2_end_min])

                        # Peak Shave 3 parameters --------------------------------------------------
                        peak_shave3_enabled = "client_peak_shave3_" + str(dev[24][0])
                        if peak_shave3_enabled in self.dbData:
                            if self.dbData[peak_shave3_enabled] == "Enabled":
                                self.peak_shave3_enabled = True
                            else:
                                self.peak_shave3_enabled = False

                        peak_shave3_limit = "client_peak_shave3_limit_" + str(dev[24][0])
                        if peak_shave3_limit in self.dbData:
                            self.peak_shave3_limit = int(self.dbData[peak_shave3_limit])

                        peak_shave3_start_hour = "client_peak_shave3_start_hr_" + str(dev[24][0])
                        if peak_shave3_start_hour in self.dbData:
                            self.peak_shave3_start_hour = int(self.dbData[peak_shave3_start_hour])

                        peak_shave3_start_min = "client_peak_shave3_start_min_" + str(dev[24][0])
                        if peak_shave3_start_min in self.dbData:
                            self.peak_shave3_start_min = int(self.dbData[peak_shave3_start_min])

                        peak_shave3_end_hour = "client_peak_shave3_end_hr_" + str(dev[24][0])
                        if peak_shave3_end_hour in self.dbData:
                            self.peak_shave3_end_hour = int(self.dbData[peak_shave3_end_hour])

                        peak_shave3_end_min = "client_peak_shave3_end_min_" + str(dev[24][0])
                        if peak_shave3_end_min in self.dbData:
                            self.peak_shave3_end_min = int(self.dbData[peak_shave3_end_min])

                        # Client Task parameters ---------------------------------------------------
                        client_tasks_enabled = "client_tasks_" + str(dev[24][0])
                        if client_tasks_enabled in self.dbData:
                            if self.dbData[client_tasks_enabled] == "Enabled":
                                self.client_tasks_enabled = True
                            else:
                                self.client_tasks_enabled = False

                        client_tasks_client = "client_tasks_list_" + str(dev[24][0])
                        if client_tasks_client in self.dbData:
                            self.client_tasks_client = self.dbData[client_tasks_client]

                        client_tasks_start_hour = "client_tasks_start_hr_" + str(dev[24][0])
                        if client_tasks_start_hour in self.dbData:
                            self.client_tasks_start_hour = int(self.dbData[client_tasks_start_hour])

                        client_tasks_start_min = "client_tasks_start_min_" + str(dev[24][0])
                        if client_tasks_start_min in self.dbData:
                            self.client_tasks_start_min = int(self.dbData[client_tasks_start_min])

                        client_tasks_end_hour = "client_tasks_end_hr_" + str(dev[24][0])
                        if client_tasks_end_hour in self.dbData:
                            self.client_tasks_end_hour = int(self.dbData[client_tasks_end_hour])

                        client_tasks_end_min = "client_tasks_end_min_" + str(dev[24][0])
                        if client_tasks_end_min in self.dbData:
                            self.client_tasks_end_min = int(self.dbData[client_tasks_end_min])

                        # Client-specific - I started making this client specific but we're just going to collect hundreds of variables that way,
                        # So now I'll just parameterise them and they'll be understood in the project context.
                        self.client_param1 = dev[24][2][4]                                          # 46007
                        self.client_param2 = dev[24][2][5]                                          
                        self.client_param3 = dev[24][2][6]                                          
                        self.client_param4 = dev[24][2][7]                                          
                        self.client_param5 = dev[24][2][8]                                          
                        self.client_param6 = dev[24][2][9]                                          
                        self.client_param7 = dev[24][2][10]                                         
                        self.client_param8 = dev[24][2][11]                                         
                        self.client_param9 = dev[24][2][12]                                         
                        self.client_param10 = dev[24][2][13]
                        self.client_param11 = dev[24][2][14]
                        self.client_param12 = dev[24][2][15]
                        self.client_param13 = dev[24][2][16]
                        self.client_param14 = dev[24][2][17]
                        self.client_param15 = dev[24][2][18]
                        self.client_param16 = dev[24][2][19]                                        # 46022
                        
                        # These are to be obsoleted in favour of the above to reduce variable use.
                        if self.client_tasks_client == "CXP-00884":
                            # Only power data from AC solar power and AC Meter modules are used.
                            pass
                        
                        elif self.client_tasks_client == "CXP-00957":
                            self.client_CXP00957_active_mode = dev[24][2][4]                # 46007
                            self.client_CXP00957_charge_setpoint = dev[24][2][5] / 10       # 46008
                            self.client_CXP00957_discharge_setpoint = dev[24][2][6] / 10    # 46009
                            self.client_CXP00957_pv_output_setpoint = dev[24][2][7]         # 46010
                            self.client_CXP00957_fc_output_setpoint = dev[24][2][8]         # 46011
                        
                    else:
                        self.client_enabled = False
                        
        return [SET_INPUTS_ACK]

    def get_outputs(self):

        self.outputs[1] = self.enabled_echo

        # Copy the system structure locally
        #mod_data = copy.deepcopy(self.inputs)
        mod_data = self.inputs

        if len(self.inputs) < 25:
            #return mod_data
            return [GET_OUTPUTS_ACK, mod_data]

        # Add the new client output data
        mod_data[ModTypes.CLIENT.value] = self.outputs
        #self.inputs[ModTypes.CLIENT.value] = self.outputs

        # #############################################################################################################################
        # Critical Module enable                                                                                                      #
        # These must be active and remain active throughout the system's operation. Logging should always come first.                 #
        # Those below are expected to be running on every system from boot, and are not included in the start sequence                #
        # #############################################################################################################################

        # Ensure the SCADA interface is running
        if self.inputs[ModTypes.SCADA.value] is not None:
            if not mod_data[ModTypes.SCADA.value][0][1]:
                mod_data[ModTypes.SCADA.value][0][1] = True

        # Ensure the Logging interface is running
        if self.inputs[ModTypes.LOGGING.value] is not None:
            if not mod_data[ModTypes.LOGGING.value][0][1]:
                mod_data[ModTypes.LOGGING.value][0][1] = True

        # Enable AC Earth Fault Monitor(s)
        if self.inputs[ModTypes.AC_EFM.value] is not None:
            if not mod_data[ModTypes.AC_EFM.value][0][1]:
                mod_data[ModTypes.AC_EFM.value][0][1] = True

        # Enable DC Earth Fault Monitor(s)
        if self.inputs[ModTypes.DC_EFM.value] is not None:
            if not mod_data[ModTypes.DC_EFM.value][0][1]:
                mod_data[ModTypes.DC_EFM.value][0][1] = True

        # Ensure the Li-Ion Tamer is running
        if self.inputs[ModTypes.LI_ION.value] is not None:
            if not mod_data[ModTypes.LI_ION.value][0][1]:
                mod_data[ModTypes.LI_ION.value][0][1] = True

        # Ensure the AC Meter(s) are running
        if self.inputs[ModTypes.AC_METER.value] is not None:
            for dev in mod_data[ModTypes.AC_METER.value]:
                if not dev[1]:
                    dev[1] = True

        # Enable Digital IO
        if self.inputs[ModTypes.DIG_IO.value] is not None:
            for dev in mod_data[ModTypes.DIG_IO.value]:
                if not dev[1]:
                    dev[1] = True

        # Enable Analogue IO
        if self.inputs[ModTypes.ANA_IO.value] is not None:
            if not mod_data[ModTypes.ANA_IO.value][0][1]:
                mod_data[ModTypes.ANA_IO.value][0][1] = True

        # Enable Air Conditioning
        if self.inputs[ModTypes.AIRCON.value] is not None:
            if not mod_data[ModTypes.AIRCON.value][0][1]:                                           # NO TRIGGER (AC must always be on in normal use)
                mod_data[ModTypes.AIRCON.value][0][1] = True

        # Enable AC Generator
        if self.inputs[ModTypes.AC_GEN.value] is not None:
            if not mod_data[ModTypes.AC_GEN.value][0][1]:                                           # NO TRIGGER (AC must always be on in normal use)
                mod_data[ModTypes.AC_GEN.value][0][1] = True
            else:
                for dev in mod_data[ModTypes.AC_GEN.value]:
                    dev[2][3] = self.acgen_max_load

        # Enable AC Solar
        if self.inputs[ModTypes.AC_SOLAR.value] is not None:
            if not mod_data[ModTypes.AC_SOLAR.value][0][1]:
                mod_data[ModTypes.AC_SOLAR.value][0][1] = True

        # Enable DC Solar
        if self.inputs[ModTypes.DC_SOLAR.value] is not None:
            if not mod_data[ModTypes.DC_SOLAR.value][0][1]:
                mod_data[ModTypes.DC_SOLAR.value][0][1] = True                                      # Enable the module
            else:
                if self.dc_solar_enable:                                                            # Enable all the optimizers (we do have some granularity but
                    pass


        # Enable Battery Interlocks
        if self.inputs[ModTypes.DIG_IO.value] is not None:
            if not mod_data[ModTypes.DIG_IO.value][0][1]:                                           # NO TRIGGER (Interlocks must always be on in normal use)
                mod_data[ModTypes.DIG_IO.value][0][1] = True
            else:
                for dev in mod_data[ModTypes.DIG_IO.value]:
                    if dev[1]:

                        # Bank power relay (Not used on the MSP Prototype, but new models control power to all the RMSCs, not individual E-Stop interlocks)
                        output = "client_bank_reset_op_" + str(dev[0])
                        # E-stop interlocks (on older systems)
                        output1 = "client_rack_1_6_reset_op_" + str(dev[0])
                        output2 = "client_rack_2_7_reset_op_" + str(dev[0])
                        output3 = "client_rack_3_8_reset_op_" + str(dev[0])
                        output4 = "client_rack_4_9_reset_op_" + str(dev[0])
                        output5 = "client_rack_5_10_reset_op_" + str(dev[0])

                        if output in self.dbData:
                            if self.dbData[output] != "None":
                                uid, io = self.get_io(self.dbData[output])
                                if dev[0] == uid:
                                    #dev[2][5] |= (0x01 << (io * 2))                                # Not inverted in hardware on some systems
                                    dev[2][5] &= ~(0x03 << (io * 2))                                 # RMSC Power enabled if IO is False

                        if output1 in self.dbData:
                            if self.dbData[output1] != "None":
                                uid, io = self.get_io(self.dbData[output1])
                                if dev[0] == uid:
                                    dev[2][5] |= (0x01 << (io * 2))

                        if output2 in self.dbData:
                            if self.dbData[output2] != "None":
                                uid, io = self.get_io(self.dbData[output2])
                                if dev[0] == uid:
                                    dev[2][5] |= (0x01 << (io * 2))

                        if output3 in self.dbData:
                            if self.dbData[output3] != "None":
                                uid, io = self.get_io(self.dbData[output3])
                                if dev[0] == uid:
                                    dev[2][5] |= (0x01 << (io * 2))

                        if output4 in self.dbData:
                            if self.dbData[output4] != "None":
                                uid, io = self.get_io(self.dbData[output4])
                                if dev[0] == uid:
                                    dev[2][5] |= (0x01 << (io * 2))

                        if output5 in self.dbData:
                            if self.dbData[output5] != "None":
                                uid, io = self.get_io(self.dbData[output5])
                                if dev[0] == uid:
                                    dev[2][5] |= (0x01 << (io * 2))

                        # Generator Start
                        output = "client_acgen_start_op_" + str(dev[0])
                        if output in self.dbData:
                            if self.dbData[output] != "None":
                                uid, io = self.get_io(self.dbData[output])
                                if dev[0] == uid:
                                    if self.acgen_start:
                                        dev[2][5] |= (0x01 << (io * 2))
                                    else:
                                        dev[2][5] &= ~(0x03 << (io * 2))

                        # Generator Mains Parallel Mode
                        output = "client_acgen_mains_parallel_op_" + str(dev[0])
                        if output in self.dbData:
                            if self.dbData[output] != "None":
                                uid, io = self.get_io(self.dbData[output])
                                if dev[0] == uid:
                                    if self.acgen_mains_parallel_mode:
                                        dev[2][5] |= (0x01 << (io * 2))
                                    else:
                                        dev[2][5] &= ~(0x03 << (io * 2))

        # #############################################################################################################################
        # Runtime Module enable                                                                                                       #
        # During the Client start process, it enables a specific feature of each system module in a safe sequence.                    #
        # Those below are included in the start sequence (they use a trigger) and may be disabled in response to a fault              #
        # The specific order in which they're enabled is set in the process loop after the Enable Bit is set in Operating State       #
        # #############################################################################################################################

        # Close Battery Contactors
        if self.inputs[ModTypes.BATTERY.value] is not None:
            if self.battery_enable:
                if not mod_data[ModTypes.BATTERY.value][0][1]:                                      # Check that the Battery module is enabled
                    mod_data[ModTypes.BATTERY.value][0][1] = True
            else:
                mod_data[ModTypes.BATTERY.value][0][1] = False

        # Enable the inverters
        if mod_data[ModTypes.INVERTER.value] is not None:
            for dev in mod_data[ModTypes.INVERTER.value]:
                if dev[1]:
                    if len(mod_data[ModTypes.INVERTER.value]) == 1:                          # There's only one following inverter, no power management required
                        dev[2][8] = self.control_real_power_command
                    else:
                        pass
                        # TODO: else each FOLLOWING inverter will get a portion of self.real_power_command when calculated in the main loop.
                        #  and this will be based on the SoC / Power availability of all connected BESSs

                # Check its configuration, and enable if commanded to
                inverter_mode = "client_inverter_grid_mode_" + str(dev[0])
                if inverter_mode in self.dbData:
                    if self.dbData[inverter_mode] == "Following":
                        if self.inverter_following_enable:
                            if not dev[1]:
                                dev[1] = True
                        else:
                            self.inverter_following_enabled = False
                            dev[1] = False

                    elif self.dbData[inverter_mode] == "Forming":
                        if self.inverter_forming_enable:
                            if not dev[1]:
                                dev[1] = True
                        else:
                            self.inverter_forming_enabled = False
                            dev[1] = False

        # Return the lot
        outputs = mod_data

        return [GET_OUTPUTS_ACK, outputs]

    def set_page(self, page, form):  # Respond to GET/POST requests
        data = dict()

        if page == self.website + "_(" + str(self.uid) + ")":  # It was a page request for a numbered device (POST)

            # Save all control changes to database
            isButton = True
            for control in form:
                if form[control] != "":                                                     # Save all non-button control changes to database
                    self.dbData[control] = form[control]
                    self.save_to_db()
            #print(form)
            if isButton is False:  # Button press states are to be acted upon only, not stored
                self.save_to_db()
            
            return [SET_PAGE_ACK]

        elif page == (self.website + "_(" + str(self.uid) + ")/data"):  # It was a json data fetch quest (POST)

            mod_data = dict()

            # Controller Information
            mod_data["client_name"] = self.name
            mod_data["client_man"] = self.manufacturer
            mod_data["client_fwver"] = self.version
            mod_data["client_serno"] = self.serial
            mod_data["client_constate"] = str(self.con_state).capitalize()
            mod_data["client_enablestate"] = self.enabled
            mod_data["client_data"] = self.outputs[2]

            mod_data["client_grid_charge1_params"] = self.grid_charge1_params
            mod_data["client_grid_charge2_params"] = self.grid_charge2_params
            mod_data["client_grid_charge3_params"] = self.grid_charge3_params

            mod_data["client_acgen_charge1_params"] = self.acgen_charge1_params

            mod_data["client_acsolar_charge1_params"] = self.acsolar_charge1_params

            mod_data["client_peak_shave1_params"] = self.peak_shave1_params
            mod_data["client_peak_shave2_params"] = self.peak_shave2_params
            mod_data["client_peak_shave3_params"] = self.peak_shave3_params

            mod_data["client_tasks_params"] = self.client_tasks_params

            # System-wide info to populate the configurator
            mod_data["client_system"] = self.inputs

            # Just a line of text to indicate the start sequence
            mod_data["client_status"] = self.system_status_text

            mod_data.update(self.dbData)  # I'm appending the dict here so we don't save unnecessary
            
            return [SET_PAGE_ACK, mod_data]  # data to the database.

        else:
            return [SET_PAGE_ACK, ('OK', 200)]   # Return the data to be jsonified

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
    
    def get_page(self):
        routes = [self.website + "_(" + str(self.uid) + ")/data"]  # JSON Data: FlexMod_test_v100_(<uid>)/data/
        page = [self.website + "_(" + str(self.uid) + ")", routes]  # HTML content: FlexMod_test_v100_(<uid>).html
        return [GET_PAGE_ACK, page]

    def save_to_db(self):
        
        try:
            db.save_to_db(__name__ + "_(" + str(self.uid) + ")", self.dbData)
        except:
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
            print("Client: " + str(e))
          
        try:
            if tx_msg is not None:
                queue[0].put(tx_msg, block=True)
                
        except Exception as e:
            print("Client: " + str(e))


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
    NONE = 0  # "Warning: Analogue IO - No Warning present"


class Alarms(Enum):
    NONE = 0  # "Alarm: Analogue IO - No Alarm present"


class Faults(Enum):
    NONE = 0  # Fault: Analogue IO - No Fault present
    CONFIG = 1  # Fault: Analogue IO - Configuration
    LOSS_OF_COMMS = 2  # Fault: Analogue IO - Loss of Comms
    IO_TIMEOUT = 3


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