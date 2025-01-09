# FlexMod_MspControl.py, Safety controls and SCADA passthrough

# Description
#  Note: "ERROR:engineio.client:websocket-client package not installed"
#         Needed this: pip3 install "python-socketio[client]"  , where "client" is literally "client"

# Versions
# 3.5.24.10.16 - SC - Known good starting point, uses thread.is_alive to prevent module stalling. 

import FlexAlerts
import sys
from threading import Thread, Event
from FlexDB import FlexTinyDB
import socket
from enum import Enum
import copy
import time
from datetime import datetime
import random
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

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
        self.uid = uid  # A unique identifier passed from the HMI via a mangled URL
        self.icon = "/static/images/Control.png"
        self.name = "MSP Controller"
        self.module_type = ModTypes.CONTROL.value
        self.module_version = "3.5.24.10.16"                                                        # Last update on "Flex version | Year | Month | Day"
        self.model = ""
        self.options = ""
        self.manufacturer = "MSP"
        self.version = "1.02"
        self.serial = "-"  # This can be replaced with the device serial number later
        self.website = "/Mod/FlexMod_MspControl"  # This is the template name itself

        # Run state
        self.con_state = False
        self.state = "Idle"
        self.set_state_text(State.IDLE)

        # Non-Volatile Data (Loaded from DB)
        self.dbData = db.fetch_from_db(__name__ + "_(" + str(self.uid) + ")")
        if self.dbData is None:
            self.dbData = dict()

        # Volatile Data
        self.inputs = []
        self.enabled = False
        self.enabled_echo = False
        self.outputs = [self.uid, self.enabled, [0] * 50]
        self.ctrl_inputs = dict()
        self.heartbeat = 0
        self.heartbeat_echo = 0
        self.operating_control_scada = 0
        self.operating_state_scada = 0
        self.operating_state_hmi = 0
        self.remote_request = 0
        self.real_power_command = 0
        self.reactive_power_command = 0
        self.real_current_command = 0
        self.reactive_current_command = 0
        self.project_name = ""
        self.project_type = ""
        self.build_serial = ""
        self.latitude = ""
        self.longitude = ""
        self.hmi_timeout = ""
        self.hmi_timeout_count = 0
        self.uptime = 0
        self.master_ip_address_network = 0
        self.master_ip_address_host = 0
        self.this_ip_address_network = 0
        self.this_ip_address_host = 0
        self.map_version = 1.02
        self.scale_factor1 = -1
        self.scale_factor2 = -2
        self.scale_factor3 = -3
        self.scale_factor4 = -4

        self.min_system_temp = 25
        self.max_system_temp = 25
        self.warning_temp_min = 25
        self.warning_temp_max = 25
        self.alarm_temp_min = 25
        self.alarm_temp_max = 25
        self.fault_temp_min = 25
        self.fault_temp_max = 25

        self.min_inverter_temp = 25
        self.max_inverter_temp = 25
        self.inverter_warning_temp_min = 25
        self.inverter_warning_temp_max = 25
        self.inverter_alarm_temp_min = 25
        self.inverter_alarm_temp_max = 25
        self.inverter_fault_temp_min = 25
        self.inverter_fault_temp_max = 25

        # Digital IO
        self.module_enable_state = 0

        self.remote_start = 0
        self.door_open = False
        self.smoke_alarm_active = False
        self.e_stop_active = True
        self.thermal_warning_switch_active = False
        self.thermal_alarm_switch_active = False
        self.off_gas_detected = False

        self.inverter_g100_breach_count = 0
        self.g100_breach_fault = False
        
        self.mod_timeout_alert = False

        self.low_volt_fault = False
        self.low_volt_alert = False
        self.high_volt_fault = False
        self.high_volt_alert = False
        self.high_temp_fault = False
        self.high_temp_alert = False
        self.rack_offline_alert = False
        self.bank_offline_fault = False
        self.e_stop_fault = False
        self.system_healthy = False
        self.li_ion_tamer_alert = False
        self.racks_online = 0
        self.send_alert_email = False
        self.send_fault_email = False
        self.email_cooldown = 600  # Email repeat send until the issue is resolved. Currently 10 minutes
        self.alert_cooldown_count = 0
        self.fault_cooldown_count = 0

        # Client
        self.client_active = False

        # Auto-control states
        self.system_enable = False
        self.smoke_reset = False
        self.gas_release = False
        self.internal_lighting = False

        # HMI Button States
        self.system_enable_test = False
        self.smoke_reset_test = False
        self.gas_release_test = False
        self.system_fault_test = False
        self.internal_lighting_test = False

        # Events
        self.warnings = 0
        self.alarms = 0
        self.faults = 0
        self.actions = [0]
        
        self.logging_warnings = 0
        self.logging_alarms = 0
        self.logging_faults = 0
        
        
        '''
        self.heartbeat_check = [0] * 25
        self.heartbeat_check_count = [0] * 25
        self.controller_status_string = ""
        self.battery_status_string = ""
        self.inverter_status_string = ""
        self.ac_meter_status_string = ""
        self.dc_meter_status_string = ""
        self.digital_io_status_string = ""
        self.analogue_io_status_string = ""
        self.mixed_io_status_string = ""
        self.switch_status_string = ""
        self.li_ion_status_string = ""
        self.dcdc_status_string = ""
        self.aircon_status_string = ""
        self.sensor_status_string = ""
        self.fuel_cell_status_string = ""
        self.ac_gen_status_string = ""
        self.ac_wind_status_string = ""
        self.ac_solar_status_string = ""
        self.dc_solar_status_string = ""
        self.ac_efm_status_string = ""
        self.dc_efm_status_string = ""
        self.ev_charge_status_string = ""
        self.scada_status_string = ""
        self.logging_status_string = ""
        self.client_status_string = ""
        self.undefined_status_string = ""
        '''
        # HMI data, from which power is derived.
        self.priV = 0  # Primary units of AC Voltage and Current
        self.priA = 0
        self.secV = 0  # Secondary, for DC units
        self.secA = 0
        self.terV = 0  # Tertiary, if a device has a third port, like a PD Hydra
        self.terA = 0

        print("Starting " + self.name + " with UID " + str(self.uid) + " on version " + str(self.module_version))

        # Get the localhost IP
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
        self.this_ip_address_network = (int(local_ip.split('.')[0]) << 8) + int(local_ip.split('.')[1])
        self.this_ip_address_host = (int(local_ip.split('.')[2]) << 8) + int(local_ip.split('.')[3])

        # Boot options
        # Start the system
        if "ctrl_boot_enable" in self.dbData:
            if self.dbData["ctrl_boot_enable"] == "Enabled":
                self.operating_state_hmi |= (1 << 0)

        # Force Remote mode
        if "ctrl_boot_pilot" in self.dbData:
            if self.dbData["ctrl_boot_pilot"] == "Remote":
                self.operating_state_hmi |= (1 << 1)
        
        # Track Interval usage (GIL interference)
        self.start_time = time.time()
        self.interval_count = 0
        self.max_delay = 0
        self.last_time = 0
    '''
    def send_email(self, subject, body):

        # Email specifics
        self.email_host = "smtp-mail.outlook.com"
        self.email_host_port = 587
        self.sent_from_email = "siteview@multisourcepower.com"
        self.sent_from_pw = "Bot45881!"
        self.send_to_addresses = ["siteview@multisourcepower.com"]  # The siteview account is configured to forward the email onto interested recipients based on "fault" or "alert" phrases.
        self.email_server = None

        self.email_server = smtplib.SMTP(
            host=self.email_host, port=self.email_host_port
        )
        self.email_server.starttls()
        self.email_server.login(self.sent_from_email, self.sent_from_pw)

        email_config = MIMEMultipart()

        email_config["From"] = self.sent_from_email
        email_config["To"] = ", ".join(self.send_to_addresses)
        email_config["Subject"] = subject

        email_body = MIMEText(body, "html")
        email_config.attach(email_body)

        try:
            self.email_server.send_message(email_config)
            self.email_server.quit()
            return True
        except:
            
            self.email_server.quit()
            return False
    '''
    def twos_comp_to_int(self, twoC):
        # calculate int from two's compliment input
        return twoC if not twoC & 0x8000 else -(0xFFFF - twoC + 1)

    def twos_comp_to_int32(self, twoC):
        # calculate int from two's compliment input
        return twoC if not twoC & 0x80000000 else -(0xFFFFFFFF - twoC + 1)

    def int_to_twos_comp(self, num):
        return num if num >= 0 else (0xFFFF + num + 1)

    def int_to_twos_comp32(self, num):
        return num if num >= 0 else (0xFFFFFFFF + num + 1)

    def get_io(self, control):
        uid = int(str(control).split("UID ")[1].split(" :")[0])
        if "Input" in control:
            io = int(str(control).split("Input ")[1])
        elif "Output" in control:
            io = int(str(control).split("Output ")[1])
        return uid, io

    def process(self):
        global loop_time
        
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
            #print("Control " + str(self.uid) + ": " + str(self.interval_count) + " events in 1 hour. Maximum gap between events was " + str(self.max_delay) + " seconds.")
        else:
            #print("Control " + str(self.uid) + ": " + str(self.interval_count) + " events in 1 hour. Maximum gap between events was " + str(self.max_delay) + " seconds.")
            pass

        #############################################################################################################################
        # Main Process Loop                                                                                                         #
        # Monitors the health of the system and disables it if there is something seriously wrong which the client cannot rectify.  #
        # Beyond instructing the client to start its own process, it is not responsible for runtime operations.                     #
        #############################################################################################################################
        
        #print("(1)  Control Heartbeat: " + str(self.heartbeat) + ", loop time: " + str(loop_time) + " Seconds")
        
        s = time.time()
        
        self.heartbeat += 1
        if self.heartbeat >= 0xFFFF:
            self.heartbeat = 0

        self.enabled_echo = True  # The module is always enabled, awaiting a start bit.

        # Monitor the faults detected by the logging system and shut down the system if necessary
        disable_system = False
        if self.logging_faults == FlexAlerts.BATTERY_CELL_VOLTAGE_HIGH_FAULT or \
           self.logging_faults == FlexAlerts.BATTERY_CELL_VOLTAGE_LOW_FAULT or \
           self.logging_faults == FlexAlerts.BATTERY_CELL_TEMP_HIGH_FAULT or \
           self.logging_faults == FlexAlerts.BATTERY_CELL_TEMP_LOW_FAULT:
            disable_system = True

        if self.operating_state_scada & (1 << 9):        # Only disable if we're already running.
            if disable_system:
                self.operating_state_hmi &= ~(1 << 0)    # Clear HMI Enable flag (as the user hasn't manually disabled the system)
                self.operating_state_scada &= ~(1 << 0)  # Disable the system
                self.operating_state_scada &= ~(1 << 8)  # Clear Setup state
                self.operating_state_scada &= ~(1 << 9)  # Clear Running state
                self.operating_state_scada |= (1 << 10)  # Set shutdown state

        '''
        # Alert and Alarm Email processing
        if self.client_active:  # We must be running before sending scary emails out
            if self.email_cooldown > 0:                                                             # We can disable the email feature by zeroing the timeout period

                if self.alert_cooldown_count > 0:
                    self.alert_cooldown_count -= 1

                    self.send_alert_email = False
                else:
                    if self.send_alert_email:
                    #else:
                        alert = ""
                        #severity = "ALERT"

                        if self.mod_timeout_alert:
                            alert = "Module Timeout Alert"
                            #severity = "ALERT"
                            self.mod_timeout_alert = False

                        if self.low_volt_alert:
                            alert = "Low Cell Voltage Alert"
                            #severity = "ALERT"
                            self.low_volt_alert = False
                        elif self.high_volt_alert:
                            alert = "High Cell Voltage Alert"
                            #severity = "ALERT"
                            self.high_volt_alert = False
                        if self.high_temp_alert:
                            alert = "High Cell Temperature Alert"
                            #severity = "ALERT"
                            self.high_temp_alert = False

                        if self.rack_offline_alert:
                            alert = "Rack Offline Alert"
                            #severity = "ALERT"
                            self.rack_offline_alert = False

                        if self.li_ion_tamer_alert:
                            alert = "Li-Ion Tamer Sensor Alert"
                            #severity = "ALERT"
                            self.li_ion_tamer_alert = False

                        enable_state = ""

                        self.alert_cooldown_count = self.email_cooldown  # Reset the timeout period
                        if self.send_email(
                                alert + " at site " + str(self.project_name) + " - " + str(datetime.now()),
                                "<!doctype html >" + "\n" +
                                "<html lang=\"en\">" + "\n" +
                                "<head>" + "\n" +
                                "</head>" + "\n" +
                                "<body>" +
                                "<div>" +
                                "<h1>MSP SYSTEM ALERT" + "</h1>"
                                "At " + str(datetime.now().strftime("%H:%M:%S")) + " on " + str(datetime.now().strftime("%d-%m-%Y")) + ", BESS " + self.build_serial + " on site " + str(self.project_name) + " experienced a \"" + alert + "\"" + enable_state +
                                "Please view the system parameters below for further information:" +
                                "</div>" +
                                "<br />"
                                "<div style=\"overflow-x:auto;\"><table>" +
                                self.controller_status_string +
                                self.battery_status_string +
                                self.inverter_status_string +
                                self.ac_meter_status_string +
                                self.dc_meter_status_string +
                                self.digital_io_status_string +
                                self.analogue_io_status_string +
                                self.mixed_io_status_string +
                                self.switch_status_string +
                                self.li_ion_status_string +
                                self.dcdc_status_string +
                                self.aircon_status_string +
                                self.sensor_status_string +
                                self.fuel_cell_status_string +
                                self.ac_gen_status_string +
                                self.ac_wind_status_string +
                                self.ac_solar_status_string +
                                self.dc_solar_status_string +
                                self.ac_efm_status_string +
                                self.dc_efm_status_string +
                                self.ev_charge_status_string +
                                self.scada_status_string +
                                self.logging_status_string +
                                #client_rows + "\n"
                                "</table>" + "\n" +
                                "</body>" + "\n" +
                                "</html>"
                        ):
                            self.send_alert_email = False

            # Faults and alerts are now kep separate now to prevent timeout blocking of important events
            if self.email_cooldown > 0:  # We can disable the email feature by zeroing the timeout period

                if self.fault_cooldown_count > 0:
                    self.fault_cooldown_count -= 1

                    self.send_fault_email = False
                else:
                    if self.send_fault_email:
                        # else:
                        alert = ""

                        if self.low_volt_fault:
                            alert = "Low Cell Voltage Fault"
                            #severity = "FAULT"
                            self.low_volt_fault = False
                        elif self.high_volt_fault:
                            alert = "High Cell Voltage Fault"
                            #severity = "FAULT"
                            self.high_volt_fault = False
                        if self.high_temp_fault:
                            alert = "High Cell Temperature Fault"
                            #severity = "FAULT"
                            self.high_temp_fault = False

                        if self.bank_offline_fault:
                            alert = "Bank Offline Fault"
                            #severity = "FAULT"
                            self.bank_offline_fault = False

                        if self.e_stop_fault:
                            alert = "E-Stop Fault"
                            #severity = "FAULT"
                            self.e_stop_fault = False

                        enable_state = ""

                        self.fault_cooldown_count = self.email_cooldown  # Reset the timeout period
                        if self.send_email(
                                alert + " at site " + str(self.project_name) + " - " + str(datetime.now()),
                                "<!doctype html >" + "\n" +
                                "<html lang=\"en\">" + "\n" +
                                "<head>" + "\n" +
                                "</head>" + "\n" +
                                "<body>" +
                                "<div>" +
                                "<h1>MSP SYSTEM FAULT" + "</h1>"
                                                               "At " + str(datetime.now().strftime("%H:%M:%S")) + " on " + str(
                                    datetime.now().strftime("%d-%m-%Y")) + ", BESS " + self.build_serial + " on site " + str(self.project_name) + " experienced a \"" + alert + "\"" + enable_state +
                                "Please view the system parameters below for further information:" +
                                "</div>" +
                                "<br />"
                                "<div style=\"overflow-x:auto;\"><table>" +
                                self.controller_status_string +
                                self.battery_status_string +
                                self.inverter_status_string +
                                self.ac_meter_status_string +
                                self.dc_meter_status_string +
                                self.digital_io_status_string +
                                self.analogue_io_status_string +
                                self.mixed_io_status_string +
                                self.switch_status_string +
                                self.li_ion_status_string +
                                self.dcdc_status_string +
                                self.aircon_status_string +
                                self.sensor_status_string +
                                self.fuel_cell_status_string +
                                self.ac_gen_status_string +
                                self.ac_wind_status_string +
                                self.ac_solar_status_string +
                                self.dc_solar_status_string +
                                self.ac_efm_status_string +
                                self.dc_efm_status_string +
                                self.ev_charge_status_string +
                                self.scada_status_string +
                                self.logging_status_string +
                                # client_rows + "\n"
                                "</table>" + "\n" +
                                "</body>" + "\n" +
                                "</html>"
                        ):
                            self.send_fault_email = False
                        self.operating_state_hmi &= ~(1 << 0)  # Clear HMI Enable flag (as the user hasn't manually disabled the system)
                        self.operating_state_scada &= ~(1 << 0)  # Disable the system
                        self.operating_state_scada &= ~(1 << 8)  # Clear Setup state
                        self.operating_state_scada &= ~(1 << 9)  # Clear Running state
                        self.operating_state_scada |= (1 << 10)  # Set shutdown state
        else:
            self.send_alert_email = False
            self.send_fault_email = False  # Prevent triggering emails for pre-enabled conditions
        '''
        # Prevent system start if there's a hardware e-stop active
        if not self.system_healthy:
            #self.operating_state_scada &= ~(1 << 1)  # Clear enable bit, client should shut the system down. (Looks incorrect)
            self.operating_state_scada |= (1 << 7)  # Set E-Stop Echo bit
            self.update_faults(Faults.ESTOP_ACTIVE.value, True)

        else:
            # Clear Control faults
            self.operating_state_scada &= ~(1 << 7)
            self.update_faults(Faults.ESTOP_ACTIVE.value, False)

            # Start the system
            if not self.operating_state_hmi & (1 << 1):  # Local mode
                self.operating_state_scada &= ~(1 << 1)
                if self.operating_state_hmi & (1 << 0):  # User has pressed enable on HMI or we've started on boot
                    self.operating_state_scada |= (1 << 0)
                    self.operating_state_scada &= ~(1 << 10)
                    if not self.client_active:  # Set if the client has set enabled_echo (startup complete)
                        self.operating_state_scada |= (1 << 8)  # Progress to Setup
                        self.operating_state_scada &= ~(1 << 9)
                    else:
                        self.operating_state_scada &= ~(1 << 8)
                        self.operating_state_scada |= (1 << 9)  # Progress to Running
                elif self.remote_start:  # The remote enable input has been triggered
                    self.operating_state_scada |= (1 << 0)
                    self.operating_state_scada &= ~(1 << 10)
                    if not self.client_active:  # Set if the client has set enabled_echo (startup complete)
                        self.operating_state_scada |= (1 << 8)  # Progress to Setup
                        self.operating_state_scada &= ~(1 << 9)
                    else:
                        self.operating_state_scada &= ~(1 << 8)
                        self.operating_state_scada |= (1 << 9)  # Progress to Running
                else:
                    self.operating_state_scada &= ~(1 << 0)
                    self.operating_state_scada &= ~(1 << 8)
                    self.operating_state_scada &= ~(1 << 9)
                    self.operating_state_scada |= (1 << 10)  # Shutdown

            else:  # Remote mode
                self.operating_state_scada |= (1 << 1)
                if self.operating_control_scada & (1 << 1):  # Client has requested remote control through SCADA
                    if self.operating_control_scada & (1 << 0):  # Client has set the enable bit through SCADA
                        self.operating_state_scada |= (1 << 0)  # Enable Echo
                        self.operating_state_scada &= ~(1 << 10)
                        if not self.client_active:  # Set if the client has set enabled_echo (startup complete)
                            self.operating_state_scada |= (1 << 8)  # Progress to Setup
                            self.operating_state_scada &= ~(1 << 9)
                        else:
                            self.operating_state_scada &= ~(1 << 8)
                            self.operating_state_scada |= (1 << 9)  # Progress to Running
                    else:
                        self.operating_state_scada &= ~(1 << 0)
                        self.operating_state_scada &= ~(1 << 8)
                        self.operating_state_scada &= ~(1 << 9)
                        self.operating_state_scada |= (1 << 10)
                else:
                    self.operating_state_scada &= ~(1 << 0)
                    self.operating_state_scada &= ~(1 << 8)
                    self.operating_state_scada &= ~(1 << 9)
                    self.operating_state_scada |= (1 << 10)  # Shutdown

            # System uptime since enabled (seconds)
            if self.operating_state_scada & (1 << 0):
                self.uptime += 1
            else:
                self.uptime = 0

        # Module Timeouts
        

        # Door Switch
        #if self.door_open:
        #    self.update_warnings(Warnings.DOOR_OPEN.value, True)
        #else:
        #    self.update_warnings(Warnings.DOOR_OPEN.value, False)

        # System Thermal Monitoring and reporting - Warnings
        #if self.min_system_temp < int(self.warning_temp_min):
        #    self.update_warnings(Warnings.LOW_TEMP.value, True)
        #else:
        #    self.update_warnings(Warnings.LOW_TEMP.value, False)

        #if self.max_system_temp > int(self.warning_temp_max):
        #    self.update_warnings(Warnings.HIGH_TEMP.value, True)
        #else:
        #    self.update_warnings(Warnings.HIGH_TEMP.value, False)

        # System Thermal Monitoring and reporting - Alarms
        #if self.min_system_temp < int(self.alarm_temp_min):
        #    self.update_alarms(Alarms.LOW_TEMP.value, True)
        #else:
        #    self.update_alarms(Alarms.LOW_TEMP.value, False)

        #if self.max_system_temp > int(self.alarm_temp_max):
        #    self.update_alarms(Alarms.HIGH_TEMP.value, True)
        #else:
        #    self.update_alarms(Alarms.HIGH_TEMP.value, False)

        # System Thermal Monitoring and reporting - Faults
        #if self.min_system_temp < int(self.fault_temp_min):
        #    self.update_faults(Faults.LOW_TEMP.value, True)
        #else:
        #    self.update_faults(Faults.LOW_TEMP.value, False)

        #if self.max_system_temp > int(self.fault_temp_max):
        #    self.update_faults(Faults.HIGH_TEMP.value, True)

            # Releasing the gas suppression system currently requires an overtemp condition and Li-Ion Tamer detection
        #    if self.off_gas_detected:
        #        self.gas_release = True
        #else:
        #    self.update_faults(Faults.HIGH_TEMP.value, False)

        # Inverter Thermal Monitoring and reporting - Warnings
        #if self.min_inverter_temp < int(self.inverter_warning_temp_min):
        #    self.update_warnings(Warnings.LOW_TEMP.value, True)
        #else:
        #    self.update_warnings(Warnings.LOW_TEMP.value, False)

        #if self.max_inverter_temp > int(self.inverter_warning_temp_max):
        #    self.update_warnings(Warnings.HIGH_TEMP.value, True)
        #else:
        #    self.update_warnings(Warnings.HIGH_TEMP.value, False)

        # Inverter Thermal Monitoring and reporting - Alarms
        #if self.min_inverter_temp < int(self.inverter_alarm_temp_min):
        #    self.update_alarms(Alarms.LOW_TEMP.value, True)
        #else:
        #    self.update_alarms(Alarms.LOW_TEMP.value, False)

        #if self.max_inverter_temp > int(self.inverter_alarm_temp_max):
        #    self.update_alarms(Alarms.HIGH_TEMP.value, True)
        #else:
        #    self.update_alarms(Alarms.HIGH_TEMP.value, False)

        # Inverter Thermal Monitoring and reporting - Faults
        #if self.min_inverter_temp < int(self.inverter_fault_temp_min):
        #    self.update_faults(Faults.LOW_TEMP.value, True)
        #else:
        #    self.update_faults(Faults.LOW_TEMP.value, False)

        #if self.max_inverter_temp > int(self.inverter_fault_temp_max):
        #    self.update_faults(Faults.HIGH_TEMP.value, True)
        #else:
        #    self.update_faults(Faults.HIGH_TEMP.value, False)

        # Thermal Switches
        #if self.thermal_warning_switch_active:
        #    self.update_warnings(Warnings.HIGH_TEMP_SWITCH.value, True)
        #else:
        #    self.update_warnings(Warnings.HIGH_TEMP_SWITCH.value, False)

        #if self.thermal_alarm_switch_active:
        #    self.update_alarms(Alarms.HIGH_TEMP_SWITCH.value, True)
        #else:
        #    self.update_alarms(Alarms.HIGH_TEMP_SWITCH.value, False)

        # TODO: I think we should be reporting off-gas even if the system temps haven't reached their limit(s)

        # Smoke Alarm
        #if self.smoke_alarm_active:
        #    self.update_faults(Faults.SMOKE_ALARM_ACTIVE.value, True)
        #    self.gas_release = True
        #elif not self.off_gas_detected:
        #    self.update_faults(Faults.SMOKE_ALARM_ACTIVE.value, False)
        #    self.gas_release = False

        # Data update
        self.outputs[2][0] = self.heartbeat
        self.outputs[2][1] = self.heartbeat_echo
        self.outputs[2][2] = self.operating_control_scada
        self.outputs[2][3] = self.operating_state_scada
        self.outputs[2][4] = self.real_power_command
        self.outputs[2][5] = self.reactive_power_command
        self.outputs[2][6] = self.real_current_command
        self.outputs[2][7] = self.reactive_current_command
        self.outputs[2][8] = 0
        self.outputs[2][9] = 0
        self.outputs[2][10] = int(self.uptime / 3600)  # Uptime in hours
        self.outputs[2][11] = self.master_ip_address_network
        self.outputs[2][12] = self.master_ip_address_host
        self.outputs[2][13] = self.this_ip_address_network
        self.outputs[2][14] = self.this_ip_address_host
        self.outputs[2][15] = self.map_version
        self.outputs[2][16] = self.scale_factor1
        self.outputs[2][17] = self.scale_factor2
        self.outputs[2][18] = self.scale_factor3
        self.outputs[2][19] = self.scale_factor4
        self.outputs[2][20] = self.warnings
        self.outputs[2][21] = self.alarms
        self.outputs[2][22] = self.faults
        self.outputs[2][23] = self.actions[0]
        self.outputs[2][24] = 0

        # We should only have one controller in the system. For the controller ONLY, i'm using some of the spare space for special data (non-SCADA)
        project_name = "CXP-00000"
        if self.project_name != "":
            project_name = self.project_name + str(" ") * (16 - len(self.project_name))

        build_serial = "0000/0000"
        if self.build_serial != "":
            build_serial = self.build_serial + str(" ") * (16 - len(self.build_serial))

        system_name = project_name + build_serial
        system_name = system_name + str(" ") * (32 - len(system_name))

        system_type = 0
        if self.project_type != "":
            system_type = int(self.project_type)

        latitude = int(self.int_to_twos_comp32(54.214149 * 1000000))  # We can only use this method because we expect the Google
        longitude = int(self.int_to_twos_comp32(-2.741314 * 1000000))  # format. Otherwise we would have to be more clever
        if self.latitude != "":
            latitude = int(self.int_to_twos_comp32(float(self.latitude) * 1000000))
        if self.longitude != "":
            longitude = int(self.int_to_twos_comp32(float(self.longitude) * 1000000))

        self.outputs[2][25] = (ord(system_name[0]) << 8) + ord(system_name[1])  # Project / System Name
        self.outputs[2][26] = (ord(system_name[2]) << 8) + ord(system_name[3])
        self.outputs[2][27] = (ord(system_name[4]) << 8) + ord(system_name[5])
        self.outputs[2][28] = (ord(system_name[6]) << 8) + ord(system_name[7])
        self.outputs[2][29] = (ord(system_name[8]) << 8) + ord(system_name[9])
        self.outputs[2][30] = (ord(system_name[10]) << 8) + ord(system_name[11])
        self.outputs[2][31] = (ord(system_name[12]) << 8) + ord(system_name[13])
        self.outputs[2][32] = (ord(system_name[14]) << 8) + ord(system_name[15])
        self.outputs[2][33] = (ord(system_name[16]) << 8) + ord(system_name[17])
        self.outputs[2][34] = (ord(system_name[18]) << 8) + ord(system_name[19])
        self.outputs[2][35] = (ord(system_name[20]) << 8) + ord(system_name[21])
        self.outputs[2][36] = (ord(system_name[22]) << 8) + ord(system_name[23])
        self.outputs[2][37] = (ord(system_name[24]) << 8) + ord(system_name[25])
        self.outputs[2][38] = (ord(system_name[26]) << 8) + ord(system_name[27])
        self.outputs[2][39] = (ord(system_name[28]) << 8) + ord(system_name[29])
        self.outputs[2][40] = (ord(system_name[30]) << 8) + ord(system_name[31])
        self.outputs[2][41] = 0
        self.outputs[2][42] = system_type
        self.outputs[2][43] = 0
        self.outputs[2][44] = (latitude >> 16) & 0xFFFF
        self.outputs[2][45] = latitude & 0xFFFF
        self.outputs[2][46] = 0
        self.outputs[2][47] = (longitude >> 16) & 0xFFFF
        self.outputs[2][48] = longitude & 0xFFFF
        self.outputs[2][49] = 0

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

    def get_input_state(self, inputs, io):

        state = 0
        if 0 <= io <= 7:
            state = (inputs[4] >> io) & 0x01
        elif 7 < io <= 15:
            state = (inputs[6] >> io) & 0x01
        elif 15 < io <= 23:
            state = (inputs[8] >> io) & 0x01
        elif 23 < io <= 31:
            state = (inputs[10] >> io) & 0x01

        return state

    def set_inputs(self, inputs):
        if inputs[0] == self.uid:
            
            #old_inputs = self.inputs    # So while processing our outputs we have modified our inputs structure which the Client needs for feeding back to other modules
            
            self.inputs = inputs        # ...before it is overwritten with new states
            
            # Quick check that all the module type are populated
            if len(self.inputs) < 25:
                return [SET_INPUTS_ACK]

            #############################################################################################################################
            # System Temperature monitoring                                                                                             #
            # We are searching through all installed module with temperature variables for the minimum and maximum system temperatures. #
            # These will then be compared in the process loop against the User Thermal limits set in the HMI                            #
            #############################################################################################################################

            # Accumulated min / max system-wide temps
            min_mod_temp = 100
            max_mod_temp = 0

            # Inverter temps (usually much higher)
            min_inv_mod_temp = 100
            max_inv_mod_temp = 0

            # Controller (module type 1))
            if not self.inputs[ModTypes.CONTROL.value] is not None:
                self.controller_status_string = "<tr align=\"left\"><th>CONTROLLER</th></tr>" + "<tr><td>No device installed" + "</td></tr>"
            else:
                self.controller_status_string = "<tr align=\"left\"><th>CONTROLLER</th></tr>"
                for dev in self.inputs[ModTypes.CONTROL.value]:  # Usually only one regardless
                    if dev[1]:

                        if self.operating_state_hmi & (1 << 1):  # Only allow remote commands if we are in remote mode
                            
                            # SCADA Inverter Control
                            if (dev[2][2] & 0x0003) <= 0x03:  # Exclude uninitialised bits. QMM defaults to 0xFFFF!
                                self.operating_control_scada = dev[2][2]  # Currently request remote control / enable bits
                            else:
                                self.operating_control_scada = 0

                            self.real_power_command = dev[2][4]  # Only reporting here, see Client for power usage

                        # Project Name, Build and Serial number
                        project_name = "ctrl_project_name_" + str(dev[0])
                        if project_name in self.dbData:
                            self.project_name = self.dbData[project_name]
                        build_serial = "ctrl_build_serial_" + str(dev[0])
                        if build_serial in self.dbData:
                            self.build_serial = self.dbData[build_serial]

                        # Project Type
                        project_type = "ctrl_project_type_" + str(dev[0])
                        if project_type in self.dbData:
                            self.project_type = self.dbData[project_type]

                        # Location
                        latitude = "ctrl_lat_" + str(dev[0])
                        if latitude in self.dbData:
                            self.latitude = self.dbData[latitude]
                        longitude = "ctrl_long_" + str(dev[0])
                        if longitude in self.dbData:
                            self.longitude = self.dbData[longitude]

                        # HMI Timeout (screensaver, fancy lighting)
                        hmi_timeout = "ctrl_hmi_timeout_" + str(dev[0])
                        if hmi_timeout in self.dbData:
                            self.hmi_timeout = self.dbData[hmi_timeout]

                        # Email Timeout (How often an alert is sent to recipients before it is resolved)
                        ctrl_email_timeout = "ctrl_email_timeout_" + str(dev[0])
                        if ctrl_email_timeout in self.dbData:
                            self.email_cooldown = int(self.dbData[ctrl_email_timeout])

                        # System Temperature Warning Limits
                        temp_min = "ctrl_warning_temp_min_" + str(dev[0])
                        if temp_min in self.dbData:
                            self.warning_temp_min = self.dbData[temp_min]
                        temp_max = "ctrl_warning_temp_max_" + str(dev[0])
                        if temp_max in self.dbData:
                            self.warning_temp_max = self.dbData[temp_max]

                        # System Temperature Alarm Limits
                        temp_min = "ctrl_alarm_temp_min_" + str(dev[0])
                        if temp_min in self.dbData:
                            self.alarm_temp_min = self.dbData[temp_min]
                        temp_max = "ctrl_alarm_temp_max_" + str(dev[0])
                        if temp_max in self.dbData:
                            self.alarm_temp_max = self.dbData[temp_max]

                        # System Temperature Fault Limits
                        temp_min = "ctrl_fault_temp_min_" + str(dev[0])
                        if temp_min in self.dbData:
                            self.fault_temp_min = self.dbData[temp_min]
                        temp_max = "ctrl_fault_temp_max_" + str(dev[0])
                        if temp_max in self.dbData:
                            self.fault_temp_max = self.dbData[temp_max]

                        # Inverter Temperature Warning Limits
                        inv_temp_min = "ctrl_inverter_warning_temp_min_" + str(dev[0])
                        if inv_temp_min in self.dbData:
                            self.inverter_warning_temp_min = self.dbData[inv_temp_min]
                        inv_temp_max = "ctrl_inverter_warning_temp_max_" + str(dev[0])
                        if inv_temp_max in self.dbData:
                            self.inverter_warning_temp_max = self.dbData[inv_temp_max]

                        # System Temperature Alarm Limits
                        inv_temp_min = "ctrl_inverter_alarm_temp_min_" + str(dev[0])
                        if inv_temp_min in self.dbData:
                            self.inverter_alarm_temp_min = self.dbData[inv_temp_min]
                        inv_temp_max = "ctrl_inverter_alarm_temp_max_" + str(dev[0])
                        if inv_temp_max in self.dbData:
                            self.inverter_alarm_temp_max = self.dbData[inv_temp_max]

                        # System Temperature Fault Limits
                        inv_temp_min = "ctrl_inverter_fault_temp_min_" + str(dev[0])
                        if inv_temp_min in self.dbData:
                            self.inverter_fault_temp_min = self.dbData[inv_temp_min]
                        inv_temp_max = "ctrl_inverter_fault_temp_max_" + str(dev[0])
                        if inv_temp_max in self.dbData:
                            self.inverter_fault_temp_max = self.dbData[inv_temp_max]
                    '''
                        # Alerts and Faults #
                        if self.client_active:
                            if (dev[2][0] - self.heartbeat_check[dev[0]] & 0xFFFF) > 0:            # Heartbeat has changed since last rotation
                                self.heartbeat_check[dev[0]] = dev[2][0]                            # Reset the checkpoint
                                self.heartbeat_check_count[dev[0]] = 0
                            else:
                                self.heartbeat_check_count[dev[0]] += 0.25                                  # Remember that Flex.py rotates around each module at a rough 1/4 second interval
                                if self.heartbeat_check_count[dev[0]] >= 60:
                                    self.mod_timeout_alert = True
                                    self.send_alert_email = True
                    
                    system_control = ""
                    if self.operating_state_scada & (1 << 1):                                       # Remote mode

                        if self.operating_control_scada == 0:
                            system_control += "Remote control disabled"
                        elif self.operating_control_scada == 1:
                            system_control += "Remote control not yet requested"
                        elif self.operating_control_scada == 2:
                            system_control += "Remote control not yet enabled"
                        elif self.operating_control_scada == 3:
                            if self.operating_state_scada & (1 << 0):
                                system_control += "System is enabled under Remote control"
                            else:
                                system_control += "System is disabled under Remote control"
                    else:                                                                           # Local mode
                        if self.operating_control_scada == 0:
                            if self.operating_state_scada & (1 << 0):
                                system_control += "System is enabled under Local control"
                            else:
                                system_control += "System is disabled under Local control"
                    
                    system_state = "Idle"
                    if self.operating_state_scada & (1 << 7):
                        system_state = "E-Stop"
                    elif self.operating_state_scada & (1 << 8):
                        system_state = "Setup"
                    elif self.operating_state_scada & (1 << 9):
                        system_state = "Running"
                    elif self.operating_state_scada & (1 << 10):
                        system_state = "Shutdown"

                    # Email Content
                    
                    self.controller_status_string += \
                        "<tr><td>Controller UID: </td><td>" + str(dev[0]) + "</td></tr>" + \
                        "<tr><td>Controller State: </td><td>" + str(dev[2][1]) + "</td></tr>" + \
                        "<tr><td>Operating Control: </td><td>" + system_control + "</td></tr>" + \
                        "<tr><td>Operating State: </td><td>" + system_state + "</td></tr>" + \
                        "<tr><td>Uptime: </td><td>" + str(int(self.uptime / 3600)) + " Hour(s)" + "</td></tr>" + \
                        "<tr><td>Warnings: </td><td>" + str(dev[2][20]) + "</td></tr>" + \
                        "<tr><td>Alarms: </td><td>" + str(dev[2][21]) + "</td></tr>" + \
                        "<tr><td>Faults: </td><td>" + str(dev[2][22]) + "</td></tr>"
                    '''
            # Battery (module type 2)
            if not self.inputs[ModTypes.BATTERY.value] is not None:
                self.battery_status_string = "<tr align=\"left\"><th>BATTERY</th></tr>" + "<tr><td>No device installed" + "</td></tr>"
            else:
                self.battery_status_string = "<tr align=\"left\"><th>BATTERY</th></tr>"
                for dev in self.inputs[ModTypes.BATTERY.value]:
                    if dev[1]:  # Data only valid if the contactors are closed

                        # Temperatures
                        if dev[2][13] < int(min_mod_temp):
                            min_mod_temp = dev[2][13]
                        if dev[2][14] > int(max_mod_temp):
                            max_mod_temp = dev[2][14]
                    '''
                        # Alerts and Faults #
                        if self.client_active:                                                      # Raise alerts only when the system is running
                            # System Alert Cell Voltage High
                            max_alert_volt = "ctrl_cell_hi_alert_voltage_" + str(dev[0])
                            if max_alert_volt in self.dbData:
                                if dev[2][11] > 0 and float(self.dbData[max_alert_volt]) > 0:  # Real value and check value are realistic
                                    if dev[2][11] >= float(self.dbData[max_alert_volt]):
                                        self.high_volt_alert = True
                                        self.send_alert_email = True

                            # System Disable Cell Voltage High
                            max_volt = "ctrl_cell_hi_disable_voltage_" + str(dev[0])
                            if max_volt in self.dbData:
                                if dev[2][11] > 0 and float(self.dbData[max_volt]) > 0:  # Real value and check value are realistic
                                    if dev[2][11] >= float(self.dbData[max_volt]):
                                        self.high_volt_fault = True
                                        self.send_fault_email = True

                            # System Alert Cell Voltage Low
                            min_alert_volt = "ctrl_cell_lo_alert_voltage_" + str(dev[0])
                            if min_alert_volt in self.dbData:
                                if dev[2][10] > 0 and float(self.dbData[min_alert_volt]) > 0:  # Real value and check value are realistic
                                    if dev[2][10] <= float(self.dbData[min_alert_volt]):
                                        self.low_volt_alert = True
                                        self.send_alert_email = True

                            # System Disable Cell Voltage Low
                            min_volt = "ctrl_cell_lo_disable_voltage_" + str(dev[0])
                            if min_volt in self.dbData:
                                if dev[2][10] > 0 and float(self.dbData[min_volt]) > 0:  # Real value and check value are realistic
                                    if dev[2][10] <= float(self.dbData[min_volt]):
                                        self.low_volt_fault = True
                                        self.send_fault_email = True

                            # System Alert Cell Temp High
                            max_alert_temp = "ctrl_cell_hi_alert_temp_" + str(dev[0])
                            if max_alert_temp in self.dbData:
                                if dev[2][14] > 0 and float(self.dbData[max_alert_temp]) > 0:  # Real value and check value are realistic
                                    if dev[2][14] >= float(self.dbData[max_alert_temp]):
                                        self.high_temp_alert = True
                                        self.send_alert_email = True

                            # System Disable Cell Temp High
                            max_temp = "ctrl_cell_hi_disable_temp_" + str(dev[0])
                            if max_temp in self.dbData:
                                if dev[2][14] > 0 and float(self.dbData[max_temp]) > 0:  # Real value and check value are realistic
                                    if dev[2][14] >= float(self.dbData[max_temp]):
                                        self.high_temp_fault = True
                                        self.send_fault_email = True

                            # Bank offline fault
                            if dev[2][7] == 0:
                                self.bank_offline_fault = True                                          # Priority over a rack offline alert. System must be disabled to prevent inverters running solo.
                                self.send_fault_email = True
                            # Rack offline fault
                            elif dev[2][7] < self.racks_online:
                                self.rack_offline_alert = True
                                self.send_alert_email = True
                            self.racks_online = dev[2][7]

                            # Alerts and Faults #
                            if self.client_active:
                                if (dev[2][0] - self.heartbeat_check[dev[0]] & 0xFFFF) > 0:  # Heartbeat has changed since last rotation
                                    self.heartbeat_check[dev[0]] = dev[2][0]  # Reset the checkpoint
                                    self.heartbeat_check_count[dev[0]] = 0
                                else:
                                    self.heartbeat_check_count[dev[0]] += 0.25  # Remember that Flex.py rotates around each module at a rough 1/4 second interval
                                    if self.heartbeat_check_count[dev[0]] >= 60:
                                        self.mod_timeout_alert = True
                                        self.send_alert_email = True

                    # Email Content
                    self.battery_status_string += \
                        "<tr><td>Battery UID: </td><td>" + str(dev[0]) + "</td></tr>" +\
                        "<tr><td>Battery State: </td><td>" + str(dev[2][1]) + "</td></tr>" +\
                        "<tr><td>SOC: </td><td>" + f"{dev[2][2]:.1f}" + " %" + "</td></tr>" +\
                        "<tr><td>SOH: </td><td>" + f"{dev[2][3]:.1f}" + " %" + "</td></tr>" +\
                        "<tr><td>DC Bus Voltage: </td><td>" + f"{dev[2][4]:.1f}" + " V" + "</td></tr>" +\
                        "<tr><td>DC Bus Power: </td><td>" + f"{dev[2][6]:.1f}" + " kW" + "</td></tr>" +\
                        "<tr><td>Contactor State: </td><td>" + str(dev[2][7]) + "</td></tr>" +\
                        "<tr><td>Cell Voltage Min: </td><td>" + f"{dev[2][10]:.3f}" + " V" + "</td></tr>" +\
                        "<tr><td>Cell Voltage Max: </td><td>" + f"{dev[2][11]:.3f}" + " V" + "</td></tr>" +\
                        "<tr><td>Cell Temperature Min: </td><td>" + f"{dev[2][13]:.1f}" + " °C" + "</td></tr>" +\
                        "<tr><td>Cell Temperature Max: </td><td>" + f"{dev[2][14]:.1f}" + " °C" + "</td></tr>" +\
                        "<tr><td>Cycles: </td><td>" + str(dev[2][16]) + "</td></tr>" +\
                        "<tr><td>Max Capacity: </td><td>" + f"{dev[2][17]:.1f}" + " kW" + "</td></tr>" +\
                        "<tr><td>Online Capacity: </td><td>" + f"{dev[2][18]:.1f}" + " kW" + "</td></tr>" + \
                        "<tr><td>Warnings: </td><td>" + str(dev[2][20]) + "</td></tr>" + \
                        "<tr><td>Alarms: </td><td>" + str(dev[2][21]) + "</td></tr>" + \
                        "<tr><td>Faults: </td><td>" + str(dev[2][22]) + "</td></tr>"
                    '''
            # Inverter (module type 3)
            if not self.inputs[ModTypes.INVERTER.value] is not None:
                self.inverter_status_string = "<tr align=\"left\"><th>INVERTER</th></tr>" + "<tr><td>No device installed" + "</td></tr>"
            else:
                self.inverter_status_string = "<tr align=\"left\"><th>INVERTER</th></tr>"
                for dev in self.inputs[ModTypes.INVERTER.value]:
                    if dev[1]:
                        # Temperatures
                        if 0 < dev[2][17] < min_inv_mod_temp:  # Internal Temperature
                            min_inv_mod_temp = dev[2][17]
                        if 0 < dev[2][18] < min_inv_mod_temp:  # Inlet Temperature
                            min_inv_mod_temp = dev[2][18]

                        if 100 > dev[2][17] > max_inv_mod_temp:
                            max_inv_mod_temp = dev[2][17]
                        if 100 > dev[2][18] > max_inv_mod_temp:
                            max_inv_mod_temp = dev[2][18]
                    '''
                        # Alerts and Faults #
                        if self.client_active:
                            if (dev[2][0] - self.heartbeat_check[dev[0]] & 0xFFFF) > 0:  # Heartbeat has changed since last rotation
                                self.heartbeat_check[dev[0]] = dev[2][0]  # Reset the checkpoint
                                self.heartbeat_check_count[dev[0]] = 0
                            else:
                                self.heartbeat_check_count[dev[0]] += 0.25  # Remember that Flex.py rotates around each module at a rough 1/4 second interval
                                if self.heartbeat_check_count[dev[0]] >= 60:
                                    self.mod_timeout_alert = True
                                    self.send_alert_email = True

                    # Email Content
                    self.inverter_status_string += \
                        "<tr><td>Inverter UID: </td><td>" + str(dev[0]) + "</td></tr>" + \
                        "<tr><td>Inverter State: </td><td>" + str(dev[2][1]) + "</td></tr>" + \
                        "<tr><td>Frequency: </td><td>" + f"{dev[2][2]:.1f}" + " Hz" + "</td></tr>" + \
                        "<tr><td>Line Voltage: </td><td>" + f"{dev[2][3]:.1f}" + " V" + "</td></tr>" + \
                        "<tr><td>Commanded Power: </td><td>" + f"{dev[2][8]:.1f}" + " kW" + "</td></tr>" + \
                        "<tr><td>Real Power: </td><td>" + f"{(dev[2][9]/1000):.1f}" + " kW" + "</td></tr>" + \
                        "<tr><td>DC Bus Voltage: </td><td>" + f"{dev[2][12]:.1f}" + " V" + "</td></tr>" + \
                        "<tr><td>DC Bus Current: </td><td>" + f"{dev[2][13]:.1f}" + " A" + "</td></tr>" + \
                        "<tr><td>Internal Temperature: </td><td>" + f"{dev[2][17]:.1f}" + " °C" + "</td></tr>" + \
                        "<tr><td>Inlet Temperature: </td><td>" + f"{dev[2][18]:.1f}" + " °C" + "</td></tr>" + \
                        "<tr><td>Warnings: </td><td>" + str(dev[2][20]) + "</td></tr>" + \
                        "<tr><td>Alarms: </td><td>" + str(dev[2][21]) + "</td></tr>" + \
                        "<tr><td>Faults: </td><td>" + str(dev[2][22]) + "</td></tr>"
                    '''
            # AC Meter (module type 4)
            if self.inputs[ModTypes.AC_METER.value] is not None:
                for dev in self.inputs[ModTypes.AC_METER.value]:
                    if dev[1]:
                        
                        if dev[0] == 14:    # Absolute Fudge for qpark, we know the grid meter location and need its data to estop on g100
                            ac_meter_grid_power = dev[2][7]
                            
                            if ac_meter_grid_power < 0:
                                self.inverter_g100_breach_count += 1

                                if self.inverter_g100_breach_count == 10:  # Shut the system off (15 seconds might be too late, add control)
                                    self.inverter_g100_breach_count = 0
                                    
                                    self.g100_breach_fault = True           # Force the e-stop open
                                    print("G100 Event Detected!")
                            else:
                                self.inverter_export_breach_count = 0
                                self.g100_breach_fault = False
                            
                        # Here is where we distinguish between grid / load and grid+load metering
                        # Although in this project we only have one grid+load meter so should I bother?
                        #meter_location = "client_ac_meter_location_" + str(dev[0])
                        #if meter_location in self.dbData:
                            #if self.dbData[meter_location] == "Grid" or self.dbData[meter_location] == "Grid_and_Load":
                                #self.ac_meter_grid_power = dev[2][7]
                                #self.ac_meter_grid_power_kva = dev[2][13]
                            #elif self.dbData[meter_location] == "Load":
                            #    self.ac_meter_load_power = dev[2][7]
            
            # AC Meters (module type 4)
            #if not self.inputs[ModTypes.AC_METER.value] is not None:
            #    self.ac_meter_status_string = "<tr align=\"left\"><th>AC METER</th></tr>" + "<tr><td>No device installed" + "</td></tr>"
            #else:
            #    self.ac_meter_status_string = "<tr align=\"left\"><th>AC METER</th></tr>"
            #    for dev in self.inputs[ModTypes.AC_METER.value]:
            #        if dev[1]:
            #            pass
                    '''
                        # Alerts and Faults #
                        if self.client_active:
                            if (dev[2][0] - self.heartbeat_check[dev[0]] & 0xFFFF) > 0:  # Heartbeat has changed since last rotation
                                self.heartbeat_check[dev[0]] = dev[2][0]  # Reset the checkpoint
                                self.heartbeat_check_count[dev[0]] = 0
                            else:
                                self.heartbeat_check_count[dev[0]] += 0.25  # Remember that Flex.py rotates around each module at a rough 1/4 second interval
                                if self.heartbeat_check_count[dev[0]] >= 60:
                                    self.mod_timeout_alert = True
                                    self.send_alert_email = True

                    # Email Content
                    self.ac_meter_status_string += \
                        "<tr><td>AC Meter UID: </td><td>" + str(dev[0]) + "</td></tr>" + \
                        "<tr><td>AC Meter State: </td><td>" + str(dev[2][1]) + "</td></tr>" + \
                        "<tr><td>Average Phase Voltage: </td><td>" + f"{dev[2][2]:.1f}" + " V" + "</td></tr>" + \
                        "<tr><td>Average Line Voltage: </td><td>" + f"{dev[2][3]:.1f}" + " V" + "</td></tr>" + \
                        "<tr><td>Average Current: </td><td>" + f"{dev[2][4]:.1f}" + " A" + "</td></tr>" + \
                        "<tr><td>Frequency: </td><td>" + f"{dev[2][5]:.1f}" + " Hz" + "</td></tr>" + \
                        "<tr><td>Total Power Factor: </td><td>" + f"{dev[2][6]:.2f}" + "</td></tr>" + \
                        "<tr><td>Total Active Power: </td><td>" + f"{dev[2][7]:.1f}" + " kW" + "</td></tr>" + \
                        "<tr><td>Total Apparent Power: </td><td>" + f"{dev[2][13]:.1f}" + " kVA" + "</td></tr>" + \
                        "<tr><td>Total Current: </td><td>" + f"{dev[2][15]:.1f}" + " A" + "</td></tr>" + \
                        "<tr><td>Warnings: </td><td>" + str(dev[2][20]) + "</td></tr>" + \
                        "<tr><td>Alarms: </td><td>" + str(dev[2][21]) + "</td></tr>" + \
                        "<tr><td>Faults: </td><td>" + str(dev[2][22]) + "</td></tr>"
                    '''
            # DC Meters (module type 5)
            if not self.inputs[ModTypes.DC_METER.value] is not None:
                self.dc_meter_status_string = "<tr align=\"left\"><th>DC METER</th></tr>" + "<tr><td>No device installed" + "</td></tr>"
            else:
                self.dc_meter_status_string = "<tr align=\"left\"><th>DC METER</th></tr>"
                for dev in self.inputs[ModTypes.DC_METER.value]:
                    if dev[1]:
                        pass
                    '''
                        # Alerts and Faults #
                        if self.client_active:
                            if (dev[2][0] - self.heartbeat_check[dev[0]] & 0xFFFF) > 0:  # Heartbeat has changed since last rotation
                                self.heartbeat_check[dev[0]] = dev[2][0]  # Reset the checkpoint
                                self.heartbeat_check_count[dev[0]] = 0
                            else:
                                self.heartbeat_check_count[dev[0]] += 0.25  # Remember that Flex.py rotates around each module at a rough 1/4 second interval
                                if self.heartbeat_check_count[dev[0]] >= 60:
                                    self.mod_timeout_alert = True
                                    self.send_alert_email = True

                    # Email Content
                    self.dc_meter_status_string += \
                        "<tr><td>DC Meter UID: </td><td>" + str(dev[0]) + "</td></tr>" + \
                        "<tr><td>DC Meter State: </td><td>" + str(dev[2][1]) + "</td></tr>" + \
                        "<tr><td>DC Bus Voltage: </td><td>" + f"{dev[2][2]:.1f}" + " V" + "</td></tr>" + \
                        "<tr><td>DC Bus Current: </td><td>" + f"{dev[2][3]:.1f}" + " A" + "</td></tr>" + \
                        "<tr><td>DC Bus Power: </td><td>" + f"{dev[2][4]:.1f}" + " kW" + "</td></tr>" + \
                        "<tr><td>Frequency: </td><td>" + f"{dev[2][5]:.1f}" + " Hz" + "</td></tr>" + \
                        "<tr><td>Warnings: </td><td>" + str(dev[2][20]) + "</td></tr>" + \
                        "<tr><td>Alarms: </td><td>" + str(dev[2][21]) + "</td></tr>" + \
                        "<tr><td>Faults: </td><td>" + str(dev[2][22]) + "</td></tr>"
                    '''
            # Digital IO (module type 6)
            if not self.inputs[ModTypes.DIG_IO.value] is not None:
                self.digital_io_status_string = "<tr align=\"left\"><th>DIGITAL IO</th></tr>" + "<tr><td>No device installed" + "</td></tr>"
            else:
                self.digital_io_status_string = "<tr align=\"left\"><th>DIGITAL IO</th></tr>"
                for dev in self.inputs[ModTypes.DIG_IO.value]:
                    if dev[1]:
                        # Input Response
                        
                        # Remote Start Input
                        dig_input = "ctrl_remote_start_ip_" + str(dev[0])
                        if dig_input in self.dbData:
                            if self.dbData[dig_input] != "None":
                                uid, io = self.get_io(self.dbData[dig_input])
                                if dev[0] == uid:
                                    self.remote_start = self.get_input_state(dev[2], io)
                            else:
                                self.remote_start = False

                        # Door Switch Input
                        dig_input = "ctrl_door_switch_ip_" + str(dev[0])
                        if dig_input in self.dbData:
                            if self.dbData[dig_input] != "None":
                                uid, io = self.get_io(self.dbData[dig_input])
                                if dev[0] == uid:
                                    self.door_open = self.get_input_state(dev[2], io)
                            else:
                                self.door_open = False

                        # Smoke Alarm Input
                        dig_input = "ctrl_smoke_alarm_ip_" + str(dev[0])
                        if dig_input in self.dbData:
                            if self.dbData[dig_input] != "None":
                                uid, io = self.get_io(self.dbData[dig_input])
                                if dev[0] == uid:
                                    self.smoke_alarm_active = self.get_input_state(dev[2], io)
                            else:
                                self.smoke_alarm_active = False

                        # System Healthy Input
                        dig_input = "ctrl_system_healthy_ip_" + str(dev[0])
                        if dig_input in self.dbData:
                            if self.dbData[dig_input] != "None":
                                uid, io = self.get_io(self.dbData[dig_input])
                                if dev[0] == uid:
                                    self.system_healthy = self.get_input_state(dev[2], io)

                                    if not self.system_healthy:              # E-stop state is faulted
                                        if self.client_active:
                                            self.e_stop_fault = True
                                            self.send_fault_email = True
                            else:
                                self.system_healthy = True

                        # Thermal Warning Switch
                        dig_input = "ctrl_thermal_warning_switch_ip_" + str(dev[0])
                        if dig_input in self.dbData:
                            if self.dbData[dig_input] != "None":
                                uid, io = self.get_io(self.dbData[dig_input])
                                if dev[0] == uid:
                                    self.thermal_warning_switch_active = self.get_input_state(dev[2], io)
                            else:
                                self.thermal_warning_switch_active = False

                        # Thermal Alarm Switch self.thermal_alarm_switch_active
                        dig_input = "ctrl_thermal_alarm_switch_ip_" + str(dev[0])
                        if dig_input in self.dbData:
                            if self.dbData[dig_input] != "None":
                                uid, io = self.get_io(self.dbData[dig_input])
                                if dev[0] == uid:
                                    self.thermal_alarm_switch_active = self.get_input_state(dev[2], io)
                            else:
                                self.thermal_alarm_switch_active = False
                        '''
                        # Alerts and Faults #
                        if self.client_active:
                            if (dev[2][0] - self.heartbeat_check[dev[0]] & 0xFFFF) > 0:  # Heartbeat has changed since last rotation
                                self.heartbeat_check[dev[0]] = dev[2][0]  # Reset the checkpoint
                                self.heartbeat_check_count[dev[0]] = 0
                            else:
                                self.heartbeat_check_count[dev[0]] += 0.25  # Remember that Flex.py rotates around each module at a rough 1/4 second interval
                                if self.heartbeat_check_count[dev[0]] >= 60:
                                    self.mod_timeout_alert = True
                                    self.send_alert_email = True
                        '''     
                        # Output response
                        
                        # System Enable Output
                        output = "ctrl_system_enable_op_" + str(dev[0])
                        if output in self.dbData:
                            
                            if self.dbData[output] != "None":
                                uid, io = self.get_io(self.dbData[output])
                                
                                if dev[0] == uid:
                                    
                                    if self.g100_breach_fault is False:
                                        if self.operating_state_scada & (1 << 0) or self.operating_state_hmi & (1 << 0) or self.system_enable_test:
                                            if io < 8:
                                                dev[2][5] &= ~(0x03 << (io * 2))
                                                dev[2][5] |= (0x01 << (io * 2))
                                                
                                            self.system_enable_test = False     # Reset the button state

                                        else:
                                            if not self.client_active:  # The system has been disabled but we should wait for the client to wind down
                                                dev[2][5] &= ~(0x03 << (io * 2))  # all systems before cutting off the E-stop loop
                                    else:
                                        dev[2][5] &= ~(0x03 << (io * 2))

                        # Smoke Reset Output
                        output = "ctrl_smoke_reset_op_" + str(dev[0])
                        if output in self.dbData:
                            if self.dbData[output] != "None":
                                uid, io = self.get_io(self.dbData[output])
                                if dev[0] == uid:
                                    if self.smoke_reset or self.smoke_reset_test:
                                        if io < 8:
                                            dev[2][5] &= ~(0x03 << (io * 2))
                                            dev[2][5] |= (0x01 << (io * 2))
                                    else:
                                        dev[2][5] &= ~(0x03 << (io * 2))

                        # Gas Release Output
                        output = "ctrl_gas_release_op_" + str(dev[0])
                        if output in self.dbData:
                            if self.dbData[output] != "None":
                                uid, io = self.get_io(self.dbData[output])
                                if dev[0] == uid:
                                    if self.gas_release or self.gas_release_test:
                                        if io < 8:
                                            dev[2][5] &= ~(0x03 << (io * 2))
                                            dev[2][5] |= (0x01 << (io * 2))
                                    else:
                                        dev[2][5] &= ~(0x03 << (io * 2))

                        # System Fault External
                        output = "ctrl_system_fault_external_op_" + str(dev[0])
                        if output in self.dbData:
                            if self.dbData[output] != "None":
                                uid, io = self.get_io(self.dbData[output])
                                if dev[0] == uid:
                                    if self.faults > 0 or self.system_fault_test:  # Generic for now, should be a major fault, i.e inv/batt
                                        if io < 8:
                                            dev[2][5] &= ~(0x03 << (io * 2))
                                            dev[2][5] |= (0x01 << (io * 2))
                                    else:
                                        dev[2][5] &= ~(0x03 << (io * 2))

                        # Internal Lighting                                                             # HMI touch-triggered lighting, on for a set duration
                        output = "ctrl_internal_lighting_op_" + str(dev[0])
                        if output in self.dbData:
                            if self.dbData[output] != "None":
                                uid, io = self.get_io(self.dbData[output])
                                if dev[0] == uid:
                                    if self.internal_lighting or self.internal_lighting_test:
                                        if io < 8:
                                            dev[2][5] &= ~(0x03 << (io * 2))
                                            dev[2][5] |= (0x01 << (io * 2))
                                    else:
                                        dev[2][5] &= ~(0x03 << (io * 2))
                    '''
                    # Email Content
                    self.digital_io_status_string += \
                        "<tr><td>Digital IO UID: </td><td>" + str(dev[0]) + "</td></tr>" + \
                        "<tr><td>Digital IO State: </td><td>" + str(dev[2][1]) + "</td></tr>" + \
                        "<tr><td>Input Quantity: </td><td>" + str(dev[2][2]) + "</td></tr>" + \
                        "<tr><td>Output Quantity: </td><td>" + str(dev[2][3]) + "</td></tr>" + \
                        "<tr><td>Digital Inputs (7...0): </td><td>" + str(dev[2][4]) + "</td></tr>" + \
                        "<tr><td>Digital Inputs (15...8): </td><td>" + str(dev[2][6]) + "</td></tr>" + \
                        "<tr><td>Digital Inputs (23...16): </td><td>" + str(dev[2][8]) + "</td></tr>" + \
                        "<tr><td>Digital Inputs (31...24): </td><td>" + str(dev[2][10]) + "</td></tr>" + \
                        "<tr><td>Digital Outputs (7...0): </td><td>" + str(dev[2][5]) + "</td></tr>" + \
                        "<tr><td>Digital Outputs (15...8): </td><td>" + str(dev[2][7]) + "</td></tr>" + \
                        "<tr><td>Digital Outputs (23...16): </td><td>" + str(dev[2][9]) + "</td></tr>" + \
                        "<tr><td>Digital Outputs (31...24): </td><td>" + str(dev[2][11]) + "</td></tr>" + \
                        "<tr><td>Warnings: </td><td>" + str(dev[2][20]) + "</td></tr>" + \
                        "<tr><td>Alarms: </td><td>" + str(dev[2][21]) + "</td></tr>" + \
                        "<tr><td>Faults: </td><td>" + str(dev[2][22]) + "</td></tr>"
                    '''
            # Analogue IO (module type 7)
            if not self.inputs[ModTypes.ANA_IO.value] is not None:
                self.analogue_io_status_string = "<tr align=\"left\"><th>ANALOGUE IO</th></tr>" + "<tr><td>No device installed" + "</td></tr>"
            else:
                self.analogue_io_status_string = "<tr align=\"left\"><th>ANALOGUE IO</th></tr>"
                for dev in self.inputs[ModTypes.ANA_IO.value]:
                    if dev[1]:
                        # Inlet Temp 1
                        ana_input = "ctrl_inlet_temp_1_ip_" + str(dev[0])
                        if ana_input in self.dbData:
                            if self.dbData[ana_input] != "None":
                                uid, io = self.get_io(self.dbData[ana_input])
                                if dev[0] == uid:
                                    if dev[2][io + 4] < min_mod_temp:
                                        min_mod_temp = dev[2][io + 4]
                                    elif dev[2][io + 4] > max_mod_temp:
                                        max_mod_temp = dev[2][io + 4]

                        # Inlet Temp 2
                        ana_input = "ctrl_inlet_temp_2_ip_" + str(dev[0])
                        if ana_input in self.dbData:
                            if self.dbData[ana_input] != "None":
                                uid, io = self.get_io(self.dbData[ana_input])
                                if dev[0] == uid:
                                    if dev[2][io + 4] < min_mod_temp:
                                        min_mod_temp = dev[2][io + 4]
                                    elif dev[2][io + 4] > max_mod_temp:
                                        max_mod_temp = dev[2][io + 4]

                        # Inlet Temp 3
                        ana_input = "ctrl_inlet_temp_3_ip_" + str(dev[0])
                        if ana_input in self.dbData:
                            if self.dbData[ana_input] != "None":
                                uid, io = self.get_io(self.dbData[ana_input])
                                if dev[0] == uid:
                                    if dev[2][io + 4] < min_mod_temp:
                                        min_mod_temp = dev[2][io + 4]
                                    elif dev[2][io + 4] > max_mod_temp:
                                        max_mod_temp = dev[2][io + 4]

                        # Inlet Temp 4
                        ana_input = "ctrl_inlet_temp_4_ip_" + str(dev[0])
                        if ana_input in self.dbData:
                            if self.dbData[ana_input] != "None":
                                uid, io = self.get_io(self.dbData[ana_input])
                                if dev[0] == uid:
                                    if dev[2][io + 4] < min_mod_temp:
                                        min_mod_temp = dev[2][io + 4]
                                    elif dev[2][io + 4] > max_mod_temp:
                                        max_mod_temp = dev[2][io + 4]

                        # Inlet Temp 5
                        ana_input = "ctrl_inlet_temp_5_ip_" + str(dev[0])
                        if ana_input in self.dbData:
                            if self.dbData[ana_input] != "None":
                                uid, io = self.get_io(self.dbData[ana_input])
                                if dev[0] == uid:
                                    if dev[2][io + 4] < min_mod_temp:
                                        min_mod_temp = dev[2][io + 4]
                                    elif dev[2][io + 4] > max_mod_temp:
                                        max_mod_temp = dev[2][io + 4]

                        # Inlet Temp 6
                        ana_input = "ctrl_inlet_temp_6_ip_" + str(dev[0])
                        if ana_input in self.dbData:
                            if self.dbData[ana_input] != "None":
                                uid, io = self.get_io(self.dbData[ana_input])
                                if dev[0] == uid:
                                    if dev[2][io + 4] < min_mod_temp:
                                        min_mod_temp = dev[2][io + 4]
                                    elif dev[2][io + 4] > max_mod_temp:
                                        max_mod_temp = dev[2][io + 4]
                    '''
                        # Alerts and Faults #
                        if self.client_active:
                            if (dev[2][0] - self.heartbeat_check[dev[0]] & 0xFFFF) > 0:  # Heartbeat has changed since last rotation
                                self.heartbeat_check[dev[0]] = dev[2][0]  # Reset the checkpoint
                                self.heartbeat_check_count[dev[0]] = 0
                            else:
                                self.heartbeat_check_count[dev[0]] += 0.25  # Remember that Flex.py rotates around each module at a rough 1/4 second interval
                                if self.heartbeat_check_count[dev[0]] >= 60:
                                    self.mod_timeout_alert = True
                                    self.send_alert_email = True

                    # Email Content
                    self.analogue_io_status_string += \
                        "<tr><td>Analogue IO UID: </td><td>" + str(dev[0]) + "</td></tr>" + \
                        "<tr><td>Analogue IO State: </td><td>" + str(dev[2][1]) + "</td></tr>" + \
                        "<tr><td>Input Quantity: </td><td>" + str(dev[2][2]) + "</td></tr>" + \
                        "<tr><td>Output Quantity: </td><td>" + str(dev[2][3]) + "</td></tr>" + \
                        "<tr><td>Analogue Input 0: </td><td>" + f"{dev[2][4]:.1f}" + "</td></tr>" + \
                        "<tr><td>Analogue Input 1: </td><td>" + f"{dev[2][5]:.1f}" + "</td></tr>" + \
                        "<tr><td>Analogue Input 2: </td><td>" + f"{dev[2][6]:.1f}" + "</td></tr>" + \
                        "<tr><td>Analogue Input 3: </td><td>" + f"{dev[2][7]:.1f}" + "</td></tr>" + \
                        "<tr><td>Analogue Input 4: </td><td>" + f"{dev[2][8]:.1f}" + "</td></tr>" + \
                        "<tr><td>Analogue Input 5: </td><td>" + f"{dev[2][9]:.1f}" + "</td></tr>" + \
                        "<tr><td>Analogue Input 6: </td><td>" + f"{dev[2][10]:.1f}" + "</td></tr>" + \
                        "<tr><td>Analogue Input 7: </td><td>" + f"{dev[2][11]:.1f}" + "</td></tr>" + \
                        "<tr><td>Warnings: </td><td>" + str(dev[2][20]) + "</td></tr>" + \
                        "<tr><td>Alarms: </td><td>" + str(dev[2][21]) + "</td></tr>" + \
                        "<tr><td>Faults: </td><td>" + str(dev[2][22]) + "</td></tr>"
                    '''
            # Mixed IO (module type 8)
            if not self.inputs[ModTypes.MIXED_IO.value] is not None:
                self.mixed_io_status_string = "<tr align=\"left\"><th>MIXED IO</th></tr>" + "<tr><td>No device installed" + "</td></tr>"
            else:
                self.mixed_io_status_string = "<tr align=\"left\"><th>MIXED IO</th></tr>"
                for dev in self.inputs[ModTypes.MIXED_IO.value]:
                    if dev[1]:
                        pass
                    '''
                        # Alerts and Faults #
                        if self.client_active:
                            if (dev[2][0] - self.heartbeat_check[dev[0]] & 0xFFFF) > 0:  # Heartbeat has changed since last rotation
                                self.heartbeat_check[dev[0]] = dev[2][0]  # Reset the checkpoint
                                self.heartbeat_check_count[dev[0]] = 0
                            else:
                                self.heartbeat_check_count[dev[0]] += 0.25  # Remember that Flex.py rotates around each module at a rough 1/4 second interval
                                if self.heartbeat_check_count[dev[0]] >= 60:
                                    self.mod_timeout_alert = True
                                    self.send_alert_email = True

                    # Email Content
                    self.mixed_io_status_string += \
                        "<tr><td>Mixed IO UID: </td><td>" + str(dev[0]) + "</td></tr>" + \
                        "<tr><td>Warnings: </td><td>" + str(dev[2][20]) + "</td></tr>" + \
                        "<tr><td>Alarms: </td><td>" + str(dev[2][21]) + "</td></tr>" + \
                        "<tr><td>Faults: </td><td>" + str(dev[2][22]) + "</td></tr>"
                    '''
            # Switch (module type 9)
            if not self.inputs[ModTypes.SWITCH.value] is not None:
                self.switch_status_string = "<tr align=\"left\"><th>SWITCH</th></tr>" + "<tr><td>No device installed" + "</td></tr>"
            else:
                self.switch_status_string = "<tr align=\"left\"><th>SWITCH</th></tr>"
                for dev in self.inputs[ModTypes.SWITCH.value]:
                    if dev[1]:
                        pass
                    '''
                        # Alerts and Faults #
                        if self.client_active:
                            if (dev[2][0] - self.heartbeat_check[dev[0]] & 0xFFFF) > 0:  # Heartbeat has changed since last rotation
                                self.heartbeat_check[dev[0]] = dev[2][0]  # Reset the checkpoint
                                self.heartbeat_check_count[dev[0]] = 0
                            else:
                                self.heartbeat_check_count[dev[0]] += 0.25  # Remember that Flex.py rotates around each module at a rough 1/4 second interval
                                if self.heartbeat_check_count[dev[0]] >= 60:
                                    self.mod_timeout_alert = True
                                    self.send_alert_email = True

                    # Email Content
                    self.switch_status_string += \
                        "<tr><td>Switch UID: </td><td>" + str(dev[0]) + "</td></tr>" + \
                        "<tr><td>Warnings: </td><td>" + str(dev[2][20]) + "</td></tr>" + \
                        "<tr><td>Alarms: </td><td>" + str(dev[2][21]) + "</td></tr>" + \
                        "<tr><td>Faults: </td><td>" + str(dev[2][22]) + "</td></tr>"
                    '''
            # Li-Ion Tamer (module type 10)
            if not self.inputs[ModTypes.LI_ION.value] is not None:
                self.li_ion_status_string = "<tr align=\"left\"><th>LI-ION TAMER</th></tr>" + "<tr><td>No device installed" + "</td></tr>"
            else:
                self.li_ion_status_string = "<tr align=\"left\"><th>LI-ION TAMER</th></tr>"
                for dev in self.inputs[ModTypes.LI_ION.value]:
                    if dev[1]:
                        pass                                                         # TODO: Gen 2 doesn't support temp but Gen 3 will.
                    '''
                        # Temperatures 
                        # Alarm state - GEN 2!!
                        if dev[2][15] or dev[2][17]:  # Sensor Alarm Any - A sensor or System detects off-gas
                            self.off_gas_detected = True

                            self.li_ion_tamer_alert = True
                            self.send_alert_email = True
                        else:
                            self.off_gas_detected = False
                    
                        # Alerts and Faults #
                        if self.client_active:
                            if (dev[2][0] - self.heartbeat_check[dev[0]] & 0xFFFF) > 0:  # Heartbeat has changed since last rotation
                                self.heartbeat_check[dev[0]] = dev[2][0]  # Reset the checkpoint
                                self.heartbeat_check_count[dev[0]] = 0
                            else:
                                self.heartbeat_check_count[dev[0]] += 0.25  # Remember that Flex.py rotates around each module at a rough 1/4 second interval
                                if self.heartbeat_check_count[dev[0]] >= 60:
                                    self.mod_timeout_alert = True
                                    self.send_alert_email = True

                    # Email Content
                    self.li_ion_status_string += \
                        "<tr><td>Li-Ion Tamer UID: </td><td>" + str(dev[0]) + "</td></tr>" + \
                        "<tr><td>Li-Ion Tamer State: </td><td>" + str(dev[2][1]) + "</td></tr>" + \
                        "<tr><td>Sensor Quantity: </td><td>" + str(dev[2][2]) + "</td></tr>" + \
                        "<tr><td>Sensor 1 Alarm: </td><td>" + str(dev[2][3]) + "</td></tr>" + \
                        "<tr><td>Sensor 2 Alarm: </td><td>" + str(dev[2][4]) + "</td></tr>" + \
                        "<tr><td>Sensor 3 Alarm: </td><td>" + str(dev[2][4]) + "</td></tr>" + \
                        "<tr><td>Sensor 4 Alarm: </td><td>" + str(dev[2][4]) + "</td></tr>" + \
                        "<tr><td>Sensor 5 Alarm: </td><td>" + str(dev[2][4]) + "</td></tr>" + \
                        "<tr><td>Sensor 6 Alarm: </td><td>" + str(dev[2][4]) + "</td></tr>" + \
                        "<tr><td>Sensor 7 Alarm: </td><td>" + str(dev[2][4]) + "</td></tr>" + \
                        "<tr><td>Sensor 8 Alarm: </td><td>" + str(dev[2][4]) + "</td></tr>" + \
                        "<tr><td>Sensor 9 Alarm: </td><td>" + str(dev[2][4]) + "</td></tr>" + \
                        "<tr><td>Sensor 10 Alarm: </td><td>" + str(dev[2][4]) + "</td></tr>" + \
                        "<tr><td>Sensor 11 Alarm: </td><td>" + str(dev[2][4]) + "</td></tr>" + \
                        "<tr><td>Sensor 12 Alarm: </td><td>" + str(dev[2][4]) + "</td></tr>" + \
                        "<tr><td>Sensor Alarm Any: </td><td>" + str(dev[2][4]) + "</td></tr>" + \
                        "<tr><td>Sensor Error Any: </td><td>" + str(dev[2][4]) + "</td></tr>" + \
                        "<tr><td>System Alarm: </td><td>" + str(dev[2][5]) + "</td></tr>" + \
                        "<tr><td>Warnings: </td><td>" + str(dev[2][20]) + "</td></tr>" + \
                        "<tr><td>Alarms: </td><td>" + str(dev[2][21]) + "</td></tr>" + \
                        "<tr><td>Faults: </td><td>" + str(dev[2][22]) + "</td></tr>"
                    '''
            # DC-DC (module type 11)
            if not self.inputs[ModTypes.DCDC.value] is not None:
                self.dcdc_status_string = "<tr align=\"left\"><th>DC-DC</th></tr>" + "<tr><td>No device installed" + "</td></tr>"
            else:
                self.dcdc_status_string = "<tr align=\"left\"><th>DC-DC</th></tr>"
                for dev in self.inputs[ModTypes.DCDC.value]:
                    if dev[1]:
                        # Temperatures                                                          # Usually an outside component but we'll include it for now
                        if dev[2][15] < min_mod_temp:
                            min_mod_temp = dev[2][15]
                        elif dev[2][15] > max_mod_temp:
                            max_mod_temp = dev[2][15]
                    '''
                        # Alerts and Faults #
                        if self.client_active:
                            if (dev[2][0] - self.heartbeat_check[dev[0]] & 0xFFFF) > 0:  # Heartbeat has changed since last rotation
                                self.heartbeat_check[dev[0]] = dev[2][0]  # Reset the checkpoint
                                self.heartbeat_check_count[dev[0]] = 0
                            else:
                                self.heartbeat_check_count[dev[0]] += 0.25  # Remember that Flex.py rotates around each module at a rough 1/4 second interval
                                if self.heartbeat_check_count[dev[0]] >= 60:
                                    self.mod_timeout_alert = True
                                    self.send_alert_email = True

                    # Email Content
                    self.dcdc_status_string += \
                        "<tr><td>DC-DC UID: </td><td>" + str(dev[0]) + "</td></tr>" + \
                        "<tr><td>DC-DC State: </td><td>" + str(dev[2][1]) + "</td></tr>" + \
                        "<tr><td>DC Voltage Setpoint: </td><td>" + f"{dev[2][5]:.1f}" + " V" + "</td></tr>" + \
                        "<tr><td>DC Current Setpoint: </td><td>" + f"{dev[2][6]:.1f}" + " A" + "</td></tr>" + \
                        "<tr><td>DC Input Voltage: </td><td>" + f"{dev[2][9]:.1f}" + " V" + "</td></tr>" + \
                        "<tr><td>DC Input Current: </td><td>" + f"{dev[2][10]:.1f}" + " A" + "</td></tr>" + \
                        "<tr><td>DC Output Voltage: </td><td>" + f"{dev[2][11]:.1f}" + " V" + "</td></tr>" + \
                        "<tr><td>DC Output Current: </td><td>" + f"{dev[2][12]:.1f}" + " A" + "</td></tr>" + \
                        "<tr><td>Temperature: </td><td>" + f"{dev[2][15]:.1f}" + " °C" + "</td></tr>" + \
                        "<tr><td>Humidity: </td><td>" + f"{dev[2][16]:.1f}" + " %" + "</td></tr>" + \
                        "<tr><td>Warnings: </td><td>" + str(dev[2][20]) + "</td></tr>" + \
                        "<tr><td>Alarms: </td><td>" + str(dev[2][21]) + "</td></tr>" + \
                        "<tr><td>Faults: </td><td>" + str(dev[2][22]) + "</td></tr>"
                    '''
            # Aircon (module type 12)
            if not self.inputs[ModTypes.AIRCON.value] is not None:
                self.aircon_status_string = "<tr align=\"left\"><th>AIRCON</th></tr>" + "<tr><td>No device installed" + "</td></tr>"
            else:
                self.aircon_status_string = "<tr align=\"left\"><th>AIRCON</th></tr>"
                for dev in self.inputs[ModTypes.AIRCON.value]:
                    if dev[1]:
                        # Temperatures
                        for online_ac in range(dev[2][2]):  # Loop through the number of installed HVACs
                            if dev[2][3] & 1 << online_ac:  # We can believe its ambient temperature if it is online
                                if dev[2][7 + (online_ac * 2)] < min_mod_temp:
                                    min_mod_temp = dev[2][7 + (online_ac * 2)]
                                elif dev[2][7 + (online_ac * 2)] > max_mod_temp:
                                    max_mod_temp = dev[2][7 + (online_ac * 2)]
                        '''
                        # Alerts and Faults #
                        if self.client_active:
                            if (dev[2][0] - self.heartbeat_check[dev[0]] & 0xFFFF) > 0:  # Heartbeat has changed since last rotation
                                self.heartbeat_check[dev[0]] = dev[2][0]  # Reset the checkpoint
                                self.heartbeat_check_count[dev[0]] = 0
                            else:
                                self.heartbeat_check_count[dev[0]] += 0.25  # Remember that Flex.py rotates around each module at a rough 1/4 second interval
                                if self.heartbeat_check_count[dev[0]] >= 60:
                                    self.mod_timeout_alert = True
                                    self.send_alert_email = True
                        '''
                        # Temperature Setpoint
                        setpoint = "ctrl_aircon_temp_setpoint_" + str(dev[0])

                        if setpoint in self.dbData:
                            if 17 <= int(self.dbData[setpoint]) <= 30:
                                dev[2][4] = int(self.dbData[setpoint])
                    '''
                    # TODO: Review naming as Hithium isn't HVAC
                    # Email Content
                    self.aircon_status_string += \
                        "<tr><td>Aircon UID: </td><td>" + str(dev[0]) + "</td></tr>" + \
                        "<tr><td>Aircon State: </td><td>" + str(dev[2][1]) + "</td></tr>" + \
                        "<tr><td>Aircon Qty: </td><td>" + str(dev[2][2]) + "</td></tr>" + \
                        "<tr><td>On/Off States: </td><td>" + str(dev[2][3]) + "</td></tr>" + \
                        "<tr><td>Temperature Setpoint: </td><td>" + f"{dev[2][4]:.1f}" + " °C" + "</td></tr>" + \
                        "<tr><td>Fan Mode / Speed: </td><td>" + str(dev[2][5]) + "</td></tr>" + \
                        "<tr><td>Temperature 1: </td><td>" + f"{dev[2][7]:.1f}" + " °C" + "</td></tr>" + \
                        "<tr><td>Temperature 2: </td><td>" + f"{dev[2][9]:.1f}" + " °C" + "</td></tr>" + \
                        "<tr><td>Temperature 3: </td><td>" + f"{dev[2][11]:.1f}" + " °C" + "</td></tr>" + \
                        "<tr><td>Temperature 4: </td><td>" + f"{dev[2][13]:.1f}" + " °C" + "</td></tr>" + \
                        "<tr><td>Temperature 5: </td><td>" + f"{dev[2][15]:.1f}" + " °C" + "</td></tr>" + \
                        "<tr><td>Temperature 6: </td><td>" + f"{dev[2][17]:.1f}" + " °C" + "</td></tr>" + \
                        "<tr><td>Warnings: </td><td>" + str(dev[2][20]) + "</td></tr>" + \
                        "<tr><td>Alarms: </td><td>" + str(dev[2][21]) + "</td></tr>" + \
                        "<tr><td>Faults: </td><td>" + str(dev[2][22]) + "</td></tr>"
                    '''
            # Sensor (module type 13)
            if not self.inputs[ModTypes.SENSOR.value] is not None:
                self.sensor_status_string = "<tr align=\"left\"><th>SENSOR</th></tr>" + "<tr><td>No device installed" + "</td></tr>"
            else:
                self.sensor_status_string = "<tr align=\"left\"><th>SENSOR</th></tr>"
                for dev in self.inputs[ModTypes.SENSOR.value]:
                    if dev[1]:
                        pass
                    '''
                        # Alerts and Faults #
                        if self.client_active:
                            if (dev[2][0] - self.heartbeat_check[dev[0]] & 0xFFFF) > 0:  # Heartbeat has changed since last rotation
                                self.heartbeat_check[dev[0]] = dev[2][0]  # Reset the checkpoint
                                self.heartbeat_check_count[dev[0]] = 0
                            else:
                                self.heartbeat_check_count[dev[0]] += 0.25  # Remember that Flex.py rotates around each module at a rough 1/4 second interval
                                if self.heartbeat_check_count[dev[0]] >= 60:
                                    self.mod_timeout_alert = True
                                    self.send_alert_email = True

                    # Email Content
                    self.sensor_status_string += \
                        "<tr><td>Sensor UID: </td><td>" + str(dev[0]) + "</td></tr>" + \
                        "<tr><td>Warnings: </td><td>" + str(dev[2][20]) + "</td></tr>" + \
                        "<tr><td>Alarms: </td><td>" + str(dev[2][21]) + "</td></tr>" + \
                        "<tr><td>Faults: </td><td>" + str(dev[2][22]) + "</td></tr>"
                    '''
            # Fuel Cell (module type 14)
            if not self.inputs[ModTypes.FUEL_CELL.value] is not None:
                self.fuel_cell_status_string = "<tr align=\"left\"><th>FUEL CELL</th></tr>" + "<tr><td>No device installed" + "</td></tr>"
            else:
                self.fuel_cell_status_string = "<tr align=\"left\"><th>FUEL CELL</th></tr>"
                for dev in self.inputs[ModTypes.FUEL_CELL.value]:
                    if dev[1]:
                        pass
                    '''
                        # Alerts and Faults #
                        if self.client_active:
                            if (dev[2][0] - self.heartbeat_check[dev[0]] & 0xFFFF) > 0:  # Heartbeat has changed since last rotation
                                self.heartbeat_check[dev[0]] = dev[2][0]  # Reset the checkpoint
                                self.heartbeat_check_count[dev[0]] = 0
                            else:
                                self.heartbeat_check_count[dev[0]] += 0.25  # Remember that Flex.py rotates around each module at a rough 1/4 second interval
                                if self.heartbeat_check_count[dev[0]] >= 60:
                                    self.mod_timeout_alert = True
                                    self.send_alert_email = True

                    # Email Content
                    self.fuel_cell_status_string += \
                        "<tr><td>Fuel Cell UID: </td><td>" + str(dev[0]) + "</td></tr>" + \
                        "<tr><td>Fuel Cell State: </td><td>" + str(dev[2][1]) + "</td></tr>" + \
                        "<tr><td>Fuel Cell OCV: </td><td>" + f"{dev[2][4]:.1f}" + " V" + "</td></tr>" + \
                        "<tr><td>Max Allowable Current: </td><td>" + f"{dev[2][5]:.1f}" + " A" + "</td></tr>" + \
                        "<tr><td>Max Allowable Power: </td><td>" + f"{dev[2][6]:.1f}" + " kW" + "</td></tr>" + \
                        "<tr><td>Obligated Current: </td><td>" + f"{dev[2][7]:.1f}" + " A" + "</td></tr>" + \
                        "<tr><td>DC Bus Voltage: </td><td>" + f"{dev[2][8]:.1f}" + " V" + "</td></tr>" + \
                        "<tr><td>DC Bus Current: </td><td>" + f"{dev[2][9]:.1f}" + " A" + "</td></tr>" + \
                        "<tr><td>DC Bus Power: </td><td>" + f"{dev[2][10]:.1f}" + " kW" + "</td></tr>" + \
                        "<tr><td>Warnings: </td><td>" + str(dev[2][20]) + "</td></tr>" + \
                        "<tr><td>Alarms: </td><td>" + str(dev[2][21]) + "</td></tr>" + \
                        "<tr><td>Faults: </td><td>" + str(dev[2][22]) + "</td></tr>"
                    '''
            # AC Generator (module type 15)
            if not self.inputs[ModTypes.AC_GEN.value] is not None:
                self.ac_gen_status_string = "<tr align=\"left\"><th>AC GENERATOR</th></tr>" + "<tr><td>No device installed" + "</td></tr>"
            else:
                self.ac_gen_status_string = "<tr align=\"left\"><th>AC_GENERATOR</th></tr>"
                for dev in self.inputs[ModTypes.AC_GEN.value]:
                    if dev[1]:
                        # Temperatures                                                          # Usually an outside component but we'll include it for now
                        if dev[2][7] < min_mod_temp:  # Coolant Temps
                            min_mod_temp = dev[2][7]
                        elif dev[2][7] > max_mod_temp:
                            max_mod_temp = dev[2][7]
                    '''
                        # Alerts and Faults #
                        if self.client_active:
                            if (dev[2][0] - self.heartbeat_check[dev[0]] & 0xFFFF) > 0:  # Heartbeat has changed since last rotation
                                self.heartbeat_check[dev[0]] = dev[2][0]  # Reset the checkpoint
                                self.heartbeat_check_count[dev[0]] = 0
                            else:
                                self.heartbeat_check_count[dev[0]] += 0.25  # Remember that Flex.py rotates around each module at a rough 1/4 second interval
                                if self.heartbeat_check_count[dev[0]] >= 60:
                                    self.mod_timeout_alert = True
                                    self.send_alert_email = True

                    # Email Content
                    self.ac_gen_status_string += \
                        "<tr><td>AC Generator UID: </td><td>" + str(dev[0]) + "</td></tr>" + \
                        "<tr><td>AC Generator State: </td><td>" + str(dev[2][1]) + "</td></tr>" + \
                        "<tr><td>Control Mode: </td><td>" + str(dev[2][2]) + "</td></tr>" + \
                        "<tr><td>Generator Alarms: </td><td>" + str(dev[2][3]) + "</td></tr>" + \
                        "<tr><td>Oil Pressure: </td><td>" + f"{dev[2][6]:.1f}" + " Kpa" + "</td></tr>" + \
                        "<tr><td>Coolant Temperature: </td><td>" + f"{dev[2][7]:.1f}" + " °C" +"</td></tr>" + \
                        "<tr><td>Fuel Level: </td><td>" + str(dev[2][8]) + " %" + "</td></tr>" + \
                        "<tr><td>Alternator Voltage: </td><td>" + f"{dev[2][9]:.1f}" + " V" + "</td></tr>" + \
                        "<tr><td>Battery Voltage: </td><td>" + f"{dev[2][10]:.1f}" + " V" + "</td></tr>" + \
                        "<tr><td>Engine Speed: </td><td>" + str(dev[2][11]) + " RPM" + "</td></tr>" + \
                        "<tr><td>AC Frequency: </td><td>" + f"{dev[2][12]:.1f}" + "</td></tr>" + \
                        "<tr><td>AC Line Voltage: </td><td>" + f"{dev[2][13]:.1f}" + "</td></tr>" + \
                        "<tr><td>AC Phase Voltage: </td><td>" + f"{dev[2][14]:.1f}" + "</td></tr>" + \
                        "<tr><td>AC Current: </td><td>" + f"{dev[2][15]:.1f}" + "</td></tr>" + \
                        "<tr><td>AC Earth Current: </td><td>" + f"{dev[2][16]:.1f}" + "</td></tr>" + \
                        "<tr><td>AC Power: </td><td>" + f"{dev[2][17]:.1f}" + "</td></tr>" + \
                        "<tr><td>Warnings: </td><td>" + str(dev[2][20]) + "</td></tr>" + \
                        "<tr><td>Alarms: </td><td>" + str(dev[2][21]) + "</td></tr>" + \
                        "<tr><td>Faults: </td><td>" + str(dev[2][22]) + "</td></tr>"
                    '''
            # AC Wind (module type 16)
            if not self.inputs[ModTypes.AC_WIND.value] is not None:
                self.ac_wind_status_string = "<tr align=\"left\"><th>AC WIND</th></tr>" + "<tr><td>No device installed" + "</td></tr>"
            else:
                self.ac_wind_status_string = "<tr align=\"left\"><th>AC WIND</th></tr>"
                for dev in self.inputs[ModTypes.AC_WIND.value]:
                    if dev[1]:
                        pass
                    '''
                        # Alerts and Faults #
                        if self.client_active:
                            if (dev[2][0] - self.heartbeat_check[dev[0]] & 0xFFFF) > 0:  # Heartbeat has changed since last rotation
                                self.heartbeat_check[dev[0]] = dev[2][0]  # Reset the checkpoint
                                self.heartbeat_check_count[dev[0]] = 0
                            else:
                                self.heartbeat_check_count[dev[0]] += 0.25  # Remember that Flex.py rotates around each module at a rough 1/4 second interval
                                if self.heartbeat_check_count[dev[0]] >= 60:
                                    self.mod_timeout_alert = True
                                    self.send_alert_email = True

                    # Email Content
                    self.ac_wind_status_string += \
                        "<tr><td>AC Wind UID: </td><td>" + str(dev[0]) + "</td></tr>" + \
                        "<tr><td>Warnings: </td><td>" + str(dev[2][20]) + "</td></tr>" + \
                        "<tr><td>Alarms: </td><td>" + str(dev[2][21]) + "</td></tr>" + \
                        "<tr><td>Faults: </td><td>" + str(dev[2][22]) + "</td></tr>"
                    '''
            # AC Solar (module type 17)
            if not self.inputs[ModTypes.AC_SOLAR.value] is not None:
                self.ac_solar_status_string = "<tr align=\"left\"><th>AC SOLAR</th></tr>" + "<tr><td>No device installed" + "</td></tr>"
            else:
                self.ac_solar_status_string = "<tr align=\"left\"><th>AC SOLAR</th></tr>"
                for dev in self.inputs[ModTypes.AC_SOLAR.value]:
                    if dev[1]:
                        pass
                    '''
                        # Alerts and Faults #
                        if self.client_active:
                            if (dev[2][0] - self.heartbeat_check[dev[0]] & 0xFFFF) > 0:  # Heartbeat has changed since last rotation
                                self.heartbeat_check[dev[0]] = dev[2][0]  # Reset the checkpoint
                                self.heartbeat_check_count[dev[0]] = 0
                            else:
                                self.heartbeat_check_count[dev[0]] += 0.25  # Remember that Flex.py rotates around each module at a rough 1/4 second interval
                                if self.heartbeat_check_count[dev[0]] >= 60:
                                    self.mod_timeout_alert = True
                                    self.send_alert_email = True

                    # Email Content
                    self.ac_solar_status_string += \
                        "<tr><td>AC Solar UID: </td><td>" + str(dev[0]) + "</td></tr>" + \
                        "<tr><td>AC Solar State: </td><td>" + str(dev[2][1]) + "</td></tr>" + \
                        "<tr><td>Control Input: </td><td>" + str(dev[2][3]) + "</td></tr>" + \
                        "<tr><td>AC Voltage: </td><td>" + f"{dev[2][11]:.1f}" + "</td></tr>" + \
                        "<tr><td>AC Current: </td><td>" + f"{dev[2][10]:.1f}" + "</td></tr>" + \
                        "<tr><td>AC Power: </td><td>" + f"{dev[2][12]:.1f}" + "</td></tr>" + \
                        "<tr><td>Temperature: </td><td>" + f"{dev[2][8]:.1f}" + "</td></tr>" + \
                        "<tr><td>Warnings: </td><td>" + str(dev[2][20]) + "</td></tr>" + \
                        "<tr><td>Alarms: </td><td>" + str(dev[2][21]) + "</td></tr>" + \
                        "<tr><td>Faults: </td><td>" + str(dev[2][22]) + "</td></tr>"
                    '''
            # DC Solar (module type 18)
            if not self.inputs[ModTypes.DC_SOLAR.value] is not None:
                self.dc_solar_status_string = "<tr align=\"left\"><th>DC SOLAR</th></tr>" + "<tr><td>No device installed" + "</td></tr>"
            else:
                self.dc_solar_status_string = "<tr align=\"left\"><th>DC SOLAR</th></tr>"
                for dev in self.inputs[ModTypes.DC_SOLAR.value]:
                    if dev[1]:
                        pass
                    '''
                        # Alerts and Faults #
                        if self.client_active:
                            if (dev[2][0] - self.heartbeat_check[dev[0]] & 0xFFFF) > 0:  # Heartbeat has changed since last rotation
                                self.heartbeat_check[dev[0]] = dev[2][0]  # Reset the checkpoint
                                self.heartbeat_check_count[dev[0]] = 0
                            else:
                                self.heartbeat_check_count[dev[0]] += 0.25  # Remember that Flex.py rotates around each module at a rough 1/4 second interval
                                if self.heartbeat_check_count[dev[0]] >= 60:
                                    self.mod_timeout_alert = True
                                    self.send_alert_email = True

                    # Email Content
                    self.dc_solar_status_string += \
                        "<tr><td>DC Solar UID: </td><td>" + str(dev[0]) + "</td></tr>" + \
                        "<tr><td>DC Solar State: </td><td>" + str(dev[2][1]) + "</td></tr>" + \
                        "<tr><td>Control Input: </td><td>" + str(dev[2][3]) + "</td></tr>" + \
                        "<tr><td>DC Bus Voltage: </td><td>" + f"{dev[2][5]:.1f}" + "</td></tr>" + \
                        "<tr><td>DC Bus Current: </td><td>" + f"{dev[2][6]:.1f}" + "</td></tr>" + \
                        "<tr><td>DC Bus Power: </td><td>" + f"{dev[2][7]:.1f}" + "</td></tr>" + \
                        "<tr><td>Temperature: </td><td>" + f"{dev[2][8]:.1f}" + "</td></tr>" + \
                        "<tr><td>Warnings: </td><td>" + str(dev[2][20]) + "</td></tr>" + \
                        "<tr><td>Alarms: </td><td>" + str(dev[2][21]) + "</td></tr>" + \
                        "<tr><td>Faults: </td><td>" + str(dev[2][22]) + "</td></tr>"
                    '''
            # AC EFM (module type 19)
            if not self.inputs[ModTypes.AC_EFM.value] is not None:
                self.ac_efm_status_string = "<tr align=\"left\"><th>AC EFM</th></tr>" + "<tr><td>No device installed" + "</td></tr>"
            else:
                self.ac_efm_status_string = "<tr align=\"left\"><th>AC EFM</th></tr>"
                for dev in self.inputs[ModTypes.AC_EFM.value]:
                    if dev[1]:
                        pass
                    '''
                        # Alerts and Faults #
                        if self.client_active:
                            if (dev[2][0] - self.heartbeat_check[dev[0]] & 0xFFFF) > 0:  # Heartbeat has changed since last rotation
                                self.heartbeat_check[dev[0]] = dev[2][0]  # Reset the checkpoint
                                self.heartbeat_check_count[dev[0]] = 0
                            else:
                                self.heartbeat_check_count[dev[0]] += 0.25  # Remember that Flex.py rotates around each module at a rough 1/4 second interval
                                if self.heartbeat_check_count[dev[0]] >= 60:
                                    self.mod_timeout_alert = True
                                    self.send_alert_email = True

                    # Email Content
                    self.ac_efm_status_string += \
                        "<tr><td>AC EFM UID: </td><td>" + str(dev[0]) + "</td></tr>" + \
                        "<tr><td>AC EFM State: </td><td>" + str(dev[2][1]) + "</td></tr>" + \
                        "<tr><td>Insulation Alarm 1: </td><td>" + str(dev[2][3]) + "</td></tr>" + \
                        "<tr><td>Insulation Alarm 2: </td><td>" + str(dev[2][4]) + "</td></tr>" + \
                        "<tr><td>Prewarning Value: </td><td>" + f"{dev[2][6]:.1f}" + "</td></tr>" + \
                        "<tr><td>Alarm Value: </td><td>" + f"{dev[2][7]:.1f}" + "</td></tr>" + \
                        "<tr><td>Capacitance: </td><td>" + f"{dev[2][9]:.1f}" + "</td></tr>" + \
                        "<tr><td>Warnings: </td><td>" + str(dev[2][20]) + "</td></tr>" + \
                        "<tr><td>Alarms: </td><td>" + str(dev[2][21]) + "</td></tr>" + \
                        "<tr><td>Faults: </td><td>" + str(dev[2][22]) + "</td></tr>"
                    '''
            # DC EFM (module type 20)
            if not self.inputs[ModTypes.DC_EFM.value] is not None:
                self.dc_efm_status_string = "<tr align=\"left\"><th>DC EFM</th></tr>" + "<tr><td>No device installed" + "</td></tr>"
            else:
                self.dc_efm_status_string = "<tr align=\"left\"><th>DC EFM</th></tr>"
                for dev in self.inputs[ModTypes.DC_EFM.value]:
                    if dev[1]:
                        pass
                    '''
                        # Alerts and Faults #
                        if self.client_active:
                            if (dev[2][0] - self.heartbeat_check[dev[0]] & 0xFFFF) > 0:  # Heartbeat has changed since last rotation
                                self.heartbeat_check[dev[0]] = dev[2][0]  # Reset the checkpoint
                                self.heartbeat_check_count[dev[0]] = 0
                            else:
                                self.heartbeat_check_count[dev[0]] += 0.25  # Remember that Flex.py rotates around each module at a rough 1/4 second interval
                                if self.heartbeat_check_count[dev[0]] >= 60:
                                    self.mod_timeout_alert = True
                                    self.send_alert_email = True

                    # Email Content
                    self.dc_efm_status_string += \
                        "<tr><td>DC EFM UID: </td><td>" + str(dev[0]) + "</td></tr>" + \
                        "<tr><td>DC EFM State: </td><td>" + str(dev[2][1]) + "</td></tr>" + \
                        "<tr><td>Insulation Alarm 1: </td><td>" + str(dev[2][3]) + "</td></tr>" + \
                        "<tr><td>Insulation Alarm 2: </td><td>" + str(dev[2][4]) + "</td></tr>" + \
                        "<tr><td>Prewarning Value: </td><td>" + f"{dev[2][6]:.1f}" + "</td></tr>" + \
                        "<tr><td>Alarm Value: </td><td>" + f"{dev[2][7]:.1f}" + "</td></tr>" + \
                        "<tr><td>Warnings: </td><td>" + str(dev[2][20]) + "</td></tr>" + \
                        "<tr><td>Alarms: </td><td>" + str(dev[2][21]) + "</td></tr>" + \
                        "<tr><td>Faults: </td><td>" + str(dev[2][22]) + "</td></tr>"
                    '''
            # EV Charger (module type 21)
            if not self.inputs[ModTypes.EV_CHARGE.value] is not None:
                self.ev_charge_status_string = "<tr align=\"left\"><th>EV CHARGER</th></tr>" + "<tr><td>No device installed" + "</td></tr>"
            else:
                self.ev_charge_status_string = "<tr align=\"left\"><th>EV CHARGER</th></tr>"
                for dev in self.inputs[ModTypes.EV_CHARGE.value]:
                    if dev[1]:
                        pass
                    '''
                        # Alerts and Faults #
                        if self.client_active:
                            if (dev[2][0] - self.heartbeat_check[dev[0]] & 0xFFFF) > 0:  # Heartbeat has changed since last rotation
                                self.heartbeat_check[dev[0]] = dev[2][0]  # Reset the checkpoint
                                self.heartbeat_check_count[dev[0]] = 0
                            else:
                                self.heartbeat_check_count[dev[0]] += 0.25  # Remember that Flex.py rotates around each module at a rough 1/4 second interval
                                if self.heartbeat_check_count[dev[0]] >= 60:
                                    self.mod_timeout_alert = True
                                    self.send_alert_email = True

                    # Email Content
                    self.ev_charge_status_string += \
                        "<tr><td>EV Charger UID: </td><td>" + str(dev[0]) + "</td></tr>" + \
                        "<tr><td>EV Charger State: </td><td>" + str(dev[2][1]) + "</td></tr>" + \
                        "<tr><td>EV Charger Session Active: </td><td>" + str(dev[2][3]) + "</td></tr>" + \
                        "<tr><td>EV Charger Serial Number: </td><td>" + str(dev[2][4]) + "</td></tr>" + \
                        "<tr><td>EV Charger Max Budget: </td><td>" + str(dev[2][5]) + "</td></tr>" + \
                        "<tr><td>EV Charger State: </td><td>" + str(dev[2][6]) + "</td></tr>" + \
                        "<tr><td>EV Charger Type: </td><td>" + str(dev[2][7]) + "</td></tr>" + \
                        "<tr><td>Charger Duration: </td><td>" + str(dev[2][8]) + "</td></tr>" + \
                        "<tr><td>Vehicle SOC: </td><td>" + f"{dev[2][14]:.1f}" + "</td></tr>" + \
                        "<tr><td>Vehicle DC Voltage: </td><td>" + f"{dev[2][15]:.1f}" + "</td></tr>" + \
                        "<tr><td>Vehicle DC Current: </td><td>" + f"{dev[2][16]:.1f}" + "</td></tr>" + \
                        "<tr><td>Vehicle DC Power: </td><td>" + f"{dev[2][17]:.1f}" + "</td></tr>" + \
                        "<tr><td>Warnings: </td><td>" + str(dev[2][20]) + "</td></tr>" + \
                        "<tr><td>Alarms: </td><td>" + str(dev[2][21]) + "</td></tr>" + \
                        "<tr><td>Faults: </td><td>" + str(dev[2][22]) + "</td></tr>"
                    '''
            # SCADA (module type 22)
            if not self.inputs[ModTypes.SCADA.value] is not None:
                self.scada_status_string = "<tr align=\"left\"><th>SCADA</th></tr>" + "<tr><td>No device installed" + "</td></tr>"
            else:
                self.scada_status_string = "<tr align=\"left\"><th>SCADA</th></tr>"
                for dev in self.inputs[ModTypes.SCADA.value]:
                    if dev[1]:
                        pass
                    '''
                        # Alerts and Faults #
                        if self.client_active:
                            if (dev[2][0] - self.heartbeat_check[dev[0]] & 0xFFFF) > 0:  # Heartbeat has changed since last rotation
                                self.heartbeat_check[dev[0]] = dev[2][0]  # Reset the checkpoint
                                self.heartbeat_check_count[dev[0]] = 0
                            else:
                                self.heartbeat_check_count[dev[0]] += 0.25  # Remember that Flex.py rotates around each module at a rough 1/4 second interval
                                if self.heartbeat_check_count[dev[0]] >= 60:
                                    self.mod_timeout_alert = True
                                    self.send_alert_email = True

                    # Email Content
                    self.scada_status_string += \
                        "<tr><td>SCADA UID: </td><td>" + str(dev[0]) + "</td></tr>" + \
                        "<tr><td>SCADA State: </td><td>" + str(dev[2][1]) + "</td></tr>" + \
                        "<tr><td>Warnings: </td><td>" + str(dev[2][20]) + "</td></tr>" + \
                        "<tr><td>Alarms: </td><td>" + str(dev[2][21]) + "</td></tr>" + \
                        "<tr><td>Faults: </td><td>" + str(dev[2][22]) + "</td></tr>"
                    '''
            # Logging (module type 23)
            if not self.inputs[ModTypes.LOGGING.value] is not None:
                self.logging_status_string = "<tr align=\"left\"><th>LOGGING</th></tr>" + "<tr><td>No device installed" + "</td></tr>"
            else:
                self.logging_status_string = "<tr align=\"left\"><th>LOGGING</th></tr>"
                for dev in self.inputs[ModTypes.LOGGING.value]:
                    if dev[1]:
                        
                        # Let's deal with faults (which are reported by Flexlogger.py)
                        self.logging_warnings = dev[2][20]
                        self.logging_alarms = dev[2][21]
                        self.logging_faults = dev[2][22]
                        
                    '''
                        # Alerts and Faults #
                        if self.client_active:
                            if (dev[2][0] - self.heartbeat_check[dev[0]] & 0xFFFF) > 0:  # Heartbeat has changed since last rotation
                                self.heartbeat_check[dev[0]] = dev[2][0]  # Reset the checkpoint
                                self.heartbeat_check_count[dev[0]] = 0
                            else:
                                self.heartbeat_check_count[dev[0]] += 0.25  # Remember that Flex.py rotates around each module at a rough 1/4 second interval
                                if self.heartbeat_check_count[dev[0]] >= 60:
                                    self.mod_timeout_alert = True
                                    self.send_alert_email = True

                    # Email Content
                    self.logging_status_string += \
                        "<tr><td>Logging UID: </td><td>" + str(dev[0]) + "</td></tr>" + \
                        "<tr><td>Logging State: </td><td>" + str(dev[2][1]) + "</td></tr>" + \
                        "<tr><td>Warnings: </td><td>" + str(dev[2][20]) + "</td></tr>" + \
                        "<tr><td>Alarms: </td><td>" + str(dev[2][21]) + "</td></tr>" + \
                        "<tr><td>Faults: </td><td>" + str(dev[2][22]) + "</td></tr>"
                    '''
            # Client (module type 24)
            if not self.inputs[ModTypes.CLIENT.value] is not None:
                self.client_status_string = "<tr align=\"left\"><th>CLIENT</th></tr>" + "<tr><td>No device installed" + "</td></tr>"
            else:
                self.client_status_string = "<tr align=\"left\"><th>CLIENT</th></tr>"
                for dev in self.inputs[ModTypes.CLIENT.value]:
                    if len(dev) >= 24:
                        if dev[24][1]:
                            self.client_active = True
                        else:
                            self.client_active = False

            # Undefined (module type 16)
            if not self.inputs[ModTypes.UNDEFINED.value] is not None:
                self.undefined_status_string = "<tr align=\"left\"><th>UNDEFINED</th></tr>" + "<tr><td>No device installed" + "</td></tr>"
            else:
                self.undefined_status_string = "<tr align=\"left\"><th>UNDEFINED</th></tr>"
                for dev in self.inputs[ModTypes.UNDEFINED.value]:
                    if dev[1]:
                        pass

            # Copy the accumulated system temperature limits to the process loop
            self.min_system_temp = min_mod_temp
            self.max_system_temp = max_mod_temp

            # Inverter temperature limits
            self.min_inverter_temp = min_inv_mod_temp
            self.max_inverter_temp = max_inv_mod_temp

        # In the past we just modified the inputs we received and it passed the new values to the client module by reference. It was dirty but worked.
        # In this case we are now using queues so we must explicitly pass the modified structure back to the caller for it to then send on to the client. Cleaner.
        return [SET_INPUTS_ACK, self.inputs]    

    def get_outputs(self):

        self.outputs[1] = self.enabled_echo

        #outputs = copy.deepcopy(self.outputs)
        if len(self.inputs) < 25:
            return [GET_OUTPUTS_ACK, self.outputs]

        # # Controller (module type 1)
        # module = 1
        # dev = 0
        
        # # Digital IO (module type 6)
        # if self.inputs[ModTypes.DIG_IO.value] is not None:
            # for dev in self.inputs[ModTypes.DIG_IO.value]:
                
                # if dev[1]:

                    # # System Enable Output
                    # output = "ctrl_system_enable_op_" + str(dev[0])
                    # if output in self.dbData:
                        
                        # if self.dbData[output] != "None":
                            # uid, io = self.get_io(self.dbData[output])
                            
                            # if dev[0] == uid:
                                
                                # if self.operating_state_scada & (1 << 0) or self.operating_state_hmi & (1 << 0) or self.system_enable_test:
                                    # if io < 8:
                                        # dev[2][5] &= ~(0x03 << (io * 2))
                                        # dev[2][5] |= (0x01 << (io * 2))
                                        
                                    # self.system_enable_test = False     # Reset the button state

                                # else:
                                    # if not self.client_active:  # The system has been disabled but we should wait for the client to wind down
                                        # dev[2][5] &= ~(0x03 << (io * 2))  # all systems before cutting off the E-stop loop

                    # # Smoke Reset Output
                    # output = "ctrl_smoke_reset_op_" + str(dev[0])
                    # if output in self.dbData:
                        # if self.dbData[output] != "None":
                            # uid, io = self.get_io(self.dbData[output])
                            # if dev[0] == uid:
                                # if self.smoke_reset or self.smoke_reset_test:
                                    # if io < 8:
                                        # dev[2][5] &= ~(0x03 << (io * 2))
                                        # dev[2][5] |= (0x01 << (io * 2))
                                # else:
                                    # dev[2][5] &= ~(0x03 << (io * 2))

                    # # Gas Release Output
                    # output = "ctrl_gas_release_op_" + str(dev[0])
                    # if output in self.dbData:
                        # if self.dbData[output] != "None":
                            # uid, io = self.get_io(self.dbData[output])
                            # if dev[0] == uid:
                                # if self.gas_release or self.gas_release_test:
                                    # if io < 8:
                                        # dev[2][5] &= ~(0x03 << (io * 2))
                                        # dev[2][5] |= (0x01 << (io * 2))
                                # else:
                                    # dev[2][5] &= ~(0x03 << (io * 2))

                    # # System Fault External
                    # output = "ctrl_system_fault_external_op_" + str(dev[0])
                    # if output in self.dbData:
                        # if self.dbData[output] != "None":
                            # uid, io = self.get_io(self.dbData[output])
                            # if dev[0] == uid:
                                # if self.faults > 0 or self.system_fault_test:  # Generic for now, should be a major fault, i.e inv/batt
                                    # if io < 8:
                                        # dev[2][5] &= ~(0x03 << (io * 2))
                                        # dev[2][5] |= (0x01 << (io * 2))
                                # else:
                                    # dev[2][5] &= ~(0x03 << (io * 2))

                    # # Internal Lighting                                                             # HMI touch-triggered lighting, on for a set duration
                    # output = "ctrl_internal_lighting_op_" + str(dev[0])
                    # if output in self.dbData:
                        # if self.dbData[output] != "None":
                            # uid, io = self.get_io(self.dbData[output])
                            # if dev[0] == uid:
                                # if self.internal_lighting or self.internal_lighting_test:
                                    # if io < 8:
                                        # dev[2][5] &= ~(0x03 << (io * 2))
                                        # dev[2][5] |= (0x01 << (io * 2))
                                # else:
                                    # dev[2][5] &= ~(0x03 << (io * 2))

        # # Air Conditioning (module type 12)s
        # if self.inputs[ModTypes.AIRCON.value] is not None:
            # for dev in self.inputs[ModTypes.AIRCON.value]:
                # if dev[1]:
                    # # Temperature Setpoint
                    # setpoint = "ctrl_aircon_temp_setpoint_" + str(dev[0])

                    # if setpoint in self.dbData:
                        # if 17 <= int(self.dbData[setpoint]) <= 30:
                            # dev[2][4] = int(self.dbData[setpoint])

        return [GET_OUTPUTS_ACK, self.outputs]

    def set_page(self, page, form):  # Respond to GET/POST requests

        # Add user actions to list. Format is [Action, Value]
        # for action in page[1].form:
        #    self.actions.append([action, page[1].form[action]])

        # Respond to user actions - The module itself
        if page == self.website + "_(" + str(self.uid) + ")":  # It was a page request for a numbered device (POST)

            # Save non-volatile control changes to database
            print("Length of form is " + str(len(form)))
            for control in form:  # Any named html id here will not be stored in the DB
    
                # Control Buttons
                if "ctrl_enable" in form:
                    #if self.system_healthy:
                    if self.operating_state_hmi & (1 << 0):
                        print("Disabling system!")
                        self.operating_state_hmi &= ~(1 << 0)
                    else:
                        print("Enabling system!")
                        self.system_enable_test = True                                          # So this might be a fudge, but operating_state_hmi will be ignored if there's an active e-stop
                        self.operating_state_hmi |= (1 << 0)                                    # but it needs operating_state_hmi to close the system enable relay to clear the e-stop
                                                                                                    # So I'm using the test button state to force the system_enable output on so we can start again...
                elif "ctrl_pilot" in form:
                    if self.operating_state_hmi & (1 << 1):
                        print("Setting Local Mode!")
                        self.operating_state_hmi &= ~(1 << 1)
                    else:
                        print("Setting Remote Mode!")
                        self.operating_state_hmi |= (1 << 1)  # This confirms the client can control us remotely

                # Test buttons
                elif "ctrl_system_enable_test" in control:
                    self.system_enable_test = not self.system_enable_test

                elif "ctrl_smoke_reset_test" in control:
                    self.smoke_reset_test = not self.smoke_reset_test

                elif "ctrl_gas_release_test" in control:
                    self.gas_release_test = not self.gas_release_test

                elif "ctrl_system_fault_external_test" in control:
                    self.system_fault_test = not self.system_fault_test

                elif "ctrl_internal_lighting_test" in control:
                    self.internal_lighting_test = not self.internal_lighting_test

                else:
                    print("Uncaught input: " + str(form))

                if form[control] != "":
                    self.dbData[control] = form[control]
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
            mod_data["ctrl_name"] = self.name
            mod_data["ctrl_man"] = self.manufacturer
            mod_data["ctrl_fwver"] = self.version
            mod_data["ctrl_serno"] = self.serial
            mod_data["ctrl_constate"] = "False"  # TODO

            # System-wide info to populate the configurator
            mod_data["ctrl_system"] = self.inputs

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
        except:
            print("Unable to save record, may already exist?")

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
            print("Control: " + str(e))
          
        try:
            if tx_msg is not None:
                queue[0].put(tx_msg, block=True)
                
        except Exception as e:
            print("Control: " + str(e))
            
 
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
    NONE = 0
    DOOR_OPEN = 1
    HIGH_TEMP_SWITCH = 2
    LOW_TEMP = 3
    HIGH_TEMP = 4


class Alarms(Enum):
    NONE = 0
    RESERVED1 = 1
    HIGH_TEMP_SWITCH = 2
    LOW_TEMP = 3
    HIGH_TEMP = 4


class Faults(Enum):
    NONE = 0
    CONFIG = 1
    LOSS_OF_COMMS = 2
    LOW_TEMP = 3
    HIGH_TEMP = 4
    ESTOP_ACTIVE = 5
    SMOKE_ALARM_ACTIVE = 6
    LOW_SOC_SHUTDOWN = 7
    LOW_VOLT_SHUTDOWN = 8
    HIGH_VOLT_SHUTDOWN = 9
    LOW_TEMP_SHUTDOWN = 10
    HIGH_TEMP_SHUTDOWN = 11


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
