from datetime import datetime
from pymodbus.client.sync import ModbusTcpClient as mbTCPClient
from multiprocessing import Process
import socketio
import sys
import time
from threading import Thread, Event

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Localhost BESS Modbus-TCP Server
mbTCP_client = mbTCPClient("127.0.0.1", port=502)
try:
    if mbTCP_client.connect():# 
        print("Flex Logger connected to Flex3 Modbus Server")
    else:
        print("Flex Logger could not connect to Flex3 Modbus Server")
except:
    print("Flex Logger could not connect to Flex3 Modbus Server")
    

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


# Separate class to define socketio connections and associated callbacks.
class SocketConnection:
    def __init__(self, url):
        self.url = url
        self.sio = socketio.Client()

    def setup(self):
        self.callbacks()
        self.sio.connect(self.url)

    def loop(self):
        self.sio.wait()

    def callbacks(self):
        @self.sio.event
        def connect():
            print(f"Connected to {self.url}")
            
        @self.sio.event
        def disconnect():
            if self.sio.connected is False:
                print("Disconnected from server.")
                
    def send_data(self, data):
        self.sio.emit("dataEmit", data=data)
        

# Class containing common data and process thread
class Module():
    def __init__(self, uid, queue):
        # Inputs
        self.logging_data_store = ""
        self.logging_data_server = "wss://bess-be.com"
        self.logging_local_interval = 0
        self.logging_local_duration = 0
        self.logging_local_max_files = 0
        self.logging_remote_interval = 0
        self.logging_remote_length = 0
        self.override = 0
        
        self.system_name = ""
        self.system_type = ""
        self.latitude = ""
        self.longitude = ""
        
        # Outputs
        self.logging_local_buffer_count = 0
        self.logging_local_file_count = 0
        self.logging_remote_buffer_count = 0
        
        # Local logic
        
        
        # Remote logic
        self.logging_remote_interval_state = 0
        self.logging_remote_interval_counter = 0
        self.logging_remote_length_counter = 0
        self.logging_remote_trigger = 0
        self.logging_remote_buffer_count = 0
        self.logging_remote_log_packet = dict()
        
        # Heartbeat timeout history
        self.heartbeat_check = [0] * 25
        self.heartbeat_check_count = [0] * 25
        self.mod_timeout_alert = False
        
        # Accumulated module strings
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
        
        # Email states
        self.send_alert_email = False
        self.send_fault_email = False
        self.email_cooldown = 600  # Email repeat send until the issue is resolved. Currently 10 minutes
        self.alert_cooldown_count = 0
        self.fault_cooldown_count = 0
        
        # Our socket object
        self.socket = None
        try:
            self.socket = SocketConnection(self.logging_data_server)
            self.socket.setup()
        except Exception as e:
            print("Logger: " + str(e))
        
    def process(self):  
            
        # Ensure we have a connection to the server
        if not mbTCP_client.is_socket_open():
            if not mbTCP_client.connect():
                print("FlexLogger: Failed to open Modbus channel")
        else:
            
            # Collect data for each module and buffer it for transmission to the remote server over SocketIO
                    
            # Create dictionary
            log_data = dict()

            now = datetime.now()
            log_data["timestamp"] = now.isoformat()
            
            # Base model placeholder only
            log_data["base_quantity"] = 0
            
            # Controller scale factors
            SF1 = 0
            SF2 = 0
            SF3 = 0
            SF4 = 0
            
            # Controller 
            try:
                # How many installed Controllers?
                rr = mbTCP_client.read_holding_registers(250, 1, unit=1)
                
                if not rr.isError():
                    
                    if len(rr.registers) >= 1:
                        log_data["controller_quantity"] = rr.registers[0]
                        
                        # Reset the alert string(s)
                        if log_data["controller_quantity"] >= 1:
                            self.controller_status_string = "<tr align=\"left\"><th>CONTROLLER</th></tr>"
                        else:
                            self.controller_status_string = "<tr align=\"left\"><th>CONTROLLER</th></tr>" + "<tr><td>No device installed" + "</td></tr>"
                        
                        # Populate the Controller data
                        for x in range(rr.registers[0]):
                            
                            uid = str(x+1)
                            offset = x * 25                                                 # There are up to 10 of each module type having 25 parameters
                            
                            try:
                                rr = mbTCP_client.read_holding_registers(251 + offset, 25, unit=1)
                
                                if not rr.isError():
                                    if len(rr.registers) >= 25:
                                        
                                        ######################
                                        # Log Data           #
                                        ######################
                                        
                                        head_ip = str((rr.registers[13] >> 8) & 0xFF) + "." + str((rr.registers[13] >> 0) & 0xFF) + "."
                                        head_ip += str((rr.registers[14] >> 8) & 0xFF) + "." + str((rr.registers[14] >> 0) & 0xFF)
                                        
                                        local_ip = str((rr.registers[15] >> 8) & 0xFF) + "." + str((rr.registers[15] >> 0) & 0xFF) + "."
                                        local_ip += str((rr.registers[16] >> 8) & 0xFF) + "." + str((rr.registers[16] >> 0) & 0xFF)
                                        
                                        log_data["controller_" + uid + "_uid"] = rr.registers[0]
                                        log_data["controller_" + uid + "_enable_state"] = True if rr.registers[1] == 1 else False
                                        log_data["controller_" + uid + "_heartbeat"] = rr.registers[2]
                                        log_data["controller_" + uid + "_heartbeat_echo"] = rr.registers[3]
                                        log_data["controller_" + uid + "_operating_control"] = rr.registers[4]
                                        log_data["controller_" + uid + "_operating_state"] = rr.registers[5]
                                        log_data["controller_" + uid + "_real_power_command"] = rr.registers[6]
                                        log_data["controller_" + uid + "_reactive_power_command"] = rr.registers[7]
                                        log_data["controller_" + uid + "_real_current_command"] = rr.registers[8]
                                        log_data["controller_" + uid + "_reactive_current_command"] = rr.registers[9]
                                        log_data["controller_" + uid + "_uptime"] = rr.registers[12]
                                        log_data["controller_" + uid + "_ip_address_master"] = "0.0.0.0"
                                        log_data["controller_" + uid + "_ip_address_network"] = local_ip
                                        log_data["controller_" + uid + "_SF1"] = SF1 = self.twos_comp_to_int(rr.registers[18])
                                        log_data["controller_" + uid + "_SF2"] = SF2 = self.twos_comp_to_int(rr.registers[19])
                                        log_data["controller_" + uid + "_SF3"] = SF3 = self.twos_comp_to_int(rr.registers[20])
                                        log_data["controller_" + uid + "_SF4"] = SF4 = self.twos_comp_to_int(rr.registers[21])
                                        log_data["controller_" + uid + "_map_version"] = rr.registers[17] * pow(10, SF2)
                                        log_data["controller_" + uid + "_warnings"] = rr.registers[22]
                                        log_data["controller_" + uid + "_alarms"] = rr.registers[23]
                                        log_data["controller_" + uid + "_faults"] = rr.registers[24]
                                        log_data["controller_" + uid + "_actions"] = 0  #rr.registers[25]   # Currently unavailable over SCADA
                                        
                                        # Controller secret data for MSP use only, stored in what would have been controller 2's data space
                                        log_data["controller_" + uid + "_ip_address_public"] = "0.0.0.0"
                                        log_data["controller_" + uid + "_system_name"] = "".join(str(self.system_name[0]))
                                        log_data["controller_" + uid + "_system_type"] = int(self.system_type[0])
                                        log_data["controller_" + uid + "_latitude"] = float(self.latitude[0])    
                                        log_data["controller_" + uid + "_longitude"] = float(self.longitude[0])
                                        
                                        ######################
                                        # Alerts             #
                                        ######################
                                        
                                        # Module Heartbeat Timeout
                                        if (rr.registers[2] - self.heartbeat_check[rr.registers[0]] & 0xFFFF) > 0:  # Heartbeat has changed since last rotation
                                           self.heartbeat_check[rr.registers[0]] = rr.registers[2]                  # Reset the Heartbeat reference
                                           self.heartbeat_check_count[rr.registers[0]] = 0                          # Reset the timeout count
                                        else:
                                           self.heartbeat_check_count[rr.registers[0]] += 1
                                            
                                        if self.heartbeat_check_count[rr.registers[0]] >= 60:                                        
                                            self.mod_timeout_alert = True
                                            self.send_alert_email = True
                                        
                                        # Module email string
                                        self.controller_status_string += \
                                            "<tr><td>Controller UID: </td><td>"             + str(log_data["controller_" + uid + "_uid"]) + "</td></tr>" + \
                                            "<tr><td>Controller Enable State: </td><td>"    + str(log_data["controller_" + uid + "_enable_state"]) + "</td></tr>" + \
                                            "<tr><td>Heartbeat: </td><td>"                  + str(log_data["controller_" + uid + "_heartbeat"]) + "</td></tr>" + \
                                            "<tr><td>Heartbeat Echo: </td><td>"             + str(log_data["controller_" + uid + "_heartbeat_echo"]) + "</td></tr>" + \
                                            "<tr><td>Operating Control: </td><td>"          + str(log_data["controller_" + uid + "_operating_control"]) + "</td></tr>" + \
                                            "<tr><td>Operating State: </td><td>"            + str(log_data["controller_" + uid + "_operating_state"]) + "</td></tr>" + \
                                            "<tr><td>Real Power Command: </td><td>"         + str(log_data["controller_" + uid + "_real_power_command"]) + "</td></tr>" + \
                                            "<tr><td>Reactive Power Command: </td><td>"     + str(log_data["controller_" + uid + "_reactive_power_command"]) + "</td></tr>" + \
                                            "<tr><td>Real Current Command: </td><td>"       + str(log_data["controller_" + uid + "_real_current_command"]) + "</td></tr>" + \
                                            "<tr><td>Reactive Current Command: </td><td>"   + str(log_data["controller_" + uid + "_reactive_current_command"]) + "</td></tr>" + \
                                            "<tr><td>Uptime: </td><td>"                     + str(log_data["controller_" + uid + "_uptime"]) + "</td></tr>" + \
                                            "<tr><td>IP Address (Master): </td><td>"        + str(log_data["controller_" + uid + "_ip_address_master"]) + "</td></tr>" + \
                                            "<tr><td>IP Address (Network): </td><td>"       + str(log_data["controller_" + uid + "_ip_address_network"]) + "</td></tr>" + \
                                            "<tr><td>Scale Factor 1): </td><td>"            + str(log_data["controller_" + uid + "_SF1"]) + "</td></tr>" + \
                                            "<tr><td>Scale Factor 2): </td><td>"            + str(log_data["controller_" + uid + "_SF2"]) + "</td></tr>" + \
                                            "<tr><td>Scale Factor 3): </td><td>"            + str(log_data["controller_" + uid + "_SF3"]) + "</td></tr>" + \
                                            "<tr><td>Scale Factor 4): </td><td>"            + str(log_data["controller_" + uid + "_SF4"]) + "</td></tr>" + \
                                            "<tr><td>Map Version: </td><td>"                + str(log_data["controller_" + uid + "_map_version"]) + "</td></tr>" + \
                                            "<tr><td>Warnings: </td><td>"                   + str(log_data["controller_" + uid + "_warnings"]) + "</td></tr>" + \
                                            "<tr><td>Alarms: </td><td>"                     + str(log_data["controller_" + uid + "_alarms"]) + "</td></tr>" + \
                                            "<tr><td>Faults: </td><td>"                     + str(log_data["controller_" + uid + "_faults"]) + "</td></tr>" + \
                                            "<tr><td>Actions: </td><td>"                    + str(log_data["controller_" + uid + "_actions"]) + "</td></tr>"
                                       
                            except Exception as e:
                                print("Logger: " + str(e))
                        
            except Exception as e:
                print("Logger: " + str(e))
            
            # Battery
            try:
                # How many installed Batteries?
                rr = mbTCP_client.read_holding_registers(500, 1, unit=1)
                
                if not rr.isError():
                    if len(rr.registers) >= 1:
                        log_data["battery_quantity"] = rr.registers[0]
                        
                        # Reset the alert string(s)
                        if log_data["battery_quantity"] >= 1:
                            self.battery_status_string = "<tr align=\"left\"><th>BATTERY</th></tr>"
                        else:
                            self.battery_status_string = "<tr align=\"left\"><th>BATTERY</th></tr>" + "<tr><td>No device installed" + "</td></tr>"
                        
                        # Populate the Battery data
                        for x in range(rr.registers[0]):
                            
                            uid = str(x+1)
                            offset = x * 25                                                 # There are up to 10 of each module type having 25 parameters
                            
                            try:
                                rr = mbTCP_client.read_holding_registers(501 + offset, 25, unit=1)
                
                                if not rr.isError():
                                    if len(rr.registers) >= 25:
                                        
                                        ######################
                                        # Log Data           #
                                        ######################
                                        
                                        log_data["battery_" + uid + "_uid"] = rr.registers[0]
                                        log_data["battery_" + uid + "_enable_state"] = True if rr.registers[1] == 1 else False
                                        log_data["battery_" + uid + "_heartbeat"] = rr.registers[2]
                                        log_data["battery_" + uid + "_charge_state"] = rr.registers[3]
                                        log_data["battery_" + uid + "_soc"] = round(rr.registers[4] * pow(10, SF1), 3)
                                        log_data["battery_" + uid + "_soh"] = round(rr.registers[5] * pow(10, SF1), 3)
                                        log_data["battery_" + uid + "_dc_bus_voltage"] = round(self.twos_comp_to_int(rr.registers[6]) * pow(10, SF1), 3)
                                        log_data["battery_" + uid + "_dc_bus_current"] = round(self.twos_comp_to_int(rr.registers[7]) * pow(10, SF1), 3)
                                        log_data["battery_" + uid + "_dc_bus_power"] = round(self.twos_comp_to_int(rr.registers[8]) * pow(10, SF1), 3)
                                        log_data["battery_" + uid + "_contactor_state"] = rr.registers[9]
                                        log_data["battery_" + uid + "_max_charge_power"] = round((rr.registers[10] * pow(10, SF1)), 3)
                                        log_data["battery_" + uid + "_max_discharge_power"] = round((rr.registers[11] * pow(10, SF1)), 3)
                                        log_data["battery_" + uid + "_cell_voltage_min"] = round(rr.registers[12] * pow(10, SF3), 3)
                                        log_data["battery_" + uid + "_cell_voltage_max"] = round(rr.registers[13] * pow(10, SF3), 3)
                                        log_data["battery_" + uid + "_cell_voltage_avg"] = round(rr.registers[14] * pow(10, SF3), 3)
                                        log_data["battery_" + uid + "_cell_temp_min"] = round(rr.registers[15] * pow(10, SF3), 3)
                                        log_data["battery_" + uid + "_cell_temp_max"] = round(rr.registers[16] * pow(10, SF3), 3)
                                        log_data["battery_" + uid + "_cell_temp_avg"] = round(rr.registers[17] * pow(10, SF3), 3)
                                        log_data["battery_" + uid + "_cycles"] = rr.registers[18]
                                        log_data["battery_" + uid + "_max_capacity"] = round(self.twos_comp_to_int(rr.registers[19]) * pow(10, SF1), 3)
                                        log_data["battery_" + uid + "_online_capacity"] = round(self.twos_comp_to_int(rr.registers[20]) * pow(10, SF1), 3)
                                        # Reserved
                                        log_data["battery_" + uid + "_warnings"] = rr.registers[22]
                                        log_data["battery_" + uid + "_alarms"] = rr.registers[23]
                                        log_data["battery_" + uid + "_faults"] = rr.registers[24]
                                        log_data["battery_" + uid + "_actions"] = 0 #rr.registers[25]  # Currently unavailable over SCADA
                                        
                                        ######################
                                        # Alerts             #
                                        ######################
                                        
                                        # Module Heartbeat Timeout
                                        if (rr.registers[2] - self.heartbeat_check[rr.registers[0]] & 0xFFFF) > 0:  # Heartbeat has changed since last rotation
                                           self.heartbeat_check[rr.registers[0]] = rr.registers[2]                  # Reset the Heartbeat reference
                                           self.heartbeat_check_count[rr.registers[0]] = 0                          # Reset the timeout count
                                        else:
                                           self.heartbeat_check_count[rr.registers[0]] += 1
                                            
                                        if self.heartbeat_check_count[rr.registers[0]] >= 60:                                        
                                            self.mod_timeout_alert = True
                                            self.send_alert_email = True
                                        
                                        # Module email string
                                        self.battery_status_string += \
                                            "<tr><td>Battery UID: </td><td>"                + str(log_data["battery_" + uid + "_uid"]) + "</td></tr>" + \
                                            "<tr><td>Battery Enable State: </td><td>"       + str(log_data["battery_" + uid + "_enable_state"]) + "</td></tr>" + \
                                            "<tr><td>Heartbeat: </td><td>"                  + str(log_data["battery_" + uid + "_heartbeat"]) + "</td></tr>" + \
                                            "<tr><td>Charge State: </td><td>"               + str(log_data["battery_" + uid + "_charge_state"]) + "</td></tr>" + \
                                            "<tr><td>SOC: </td><td>"                        + str(log_data["battery_" + uid + "_soc"]) + "</td></tr>" + \
                                            "<tr><td>SOH: </td><td>"                        + str(log_data["battery_" + uid + "_soh"]) + "</td></tr>" + \
                                            "<tr><td>DC Bus Voltage: </td><td>"             + str(log_data["battery_" + uid + "_dc_bus_voltage"]) + "</td></tr>" + \
                                            "<tr><td>DC Bus Current: </td><td>"             + str(log_data["battery_" + uid + "_dc_bus_current"]) + "</td></tr>" + \
                                            "<tr><td>DC Bus Power: </td><td>"               + str(log_data["battery_" + uid + "_dc_bus_power"]) + "</td></tr>" + \
                                            "<tr><td>Contactor State: </td><td>"            + str(log_data["battery_" + uid + "_contactor_state"]) + "</td></tr>" + \
                                            "<tr><td>Max Charge Power: </td><td>"           + str(log_data["battery_" + uid + "_max_charge_power"]) + "</td></tr>" + \
                                            "<tr><td>Max Discharge Power: </td><td>"        + str(log_data["battery_" + uid + "_max_discharge_power"]) + "</td></tr>" + \
                                            "<tr><td>Cell Voltage Min: </td><td>"           + str(log_data["battery_" + uid + "_cell_voltage_min"]) + "</td></tr>" + \
                                            "<tr><td>Cell Voltage Max: </td><td>"           + str(log_data["battery_" + uid + "_cell_voltage_max"]) + "</td></tr>" + \
                                            "<tr><td>Cell Voltage Avg: </td><td>"           + str(log_data["battery_" + uid + "_cell_voltage_avg"]) + "</td></tr>" + \
                                            "<tr><td>Cell Temp Min: </td><td>"              + str(log_data["battery_" + uid + "_cell_temp_min"]) + "</td></tr>" + \
                                            "<tr><td>Cell Temp Max: </td><td>"              + str(log_data["battery_" + uid + "_cell_temp_max"]) + "</td></tr>" + \
                                            "<tr><td>Cell Temp Avg: </td><td>"              + str(log_data["battery_" + uid + "_cell_temp_avg"]) + "</td></tr>" + \
                                            "<tr><td>Cycles: </td><td>"                     + str(log_data["battery_" + uid + "_cycles"]) + "</td></tr>" + \
                                            "<tr><td>Max Capacity: </td><td>"               + str(log_data["battery_" + uid + "_max_capacity"]) + "</td></tr>" + \
                                            "<tr><td>Online Capacity: </td><td>"            + str(log_data["battery_" + uid + "_online_capacity"]) + "</td></tr>" + \
                                            "<tr><td>Warnings: </td><td>"                   + str(log_data["battery_" + uid + "_warnings"]) + "</td></tr>" + \
                                            "<tr><td>Alarms: </td><td>"                     + str(log_data["battery_" + uid + "_alarms"]) + "</td></tr>" + \
                                            "<tr><td>Faults: </td><td>"                     + str(log_data["battery_" + uid + "_faults"]) + "</td></tr>" + \
                                            "<tr><td>Actions: </td><td>"                    + str(log_data["battery_" + uid + "_actions"]) + "</td></tr>"
                                        
                            except Exception as e:
                                print("Logger: " + str(e))
                        
            except Exception as e:
                print("Logger: " + str(e))
            
            # Inverter
            try:
                # How many installed Inverters?
                rr = mbTCP_client.read_holding_registers(750, 1, unit=1)
                
                if not rr.isError():
                    if len(rr.registers) >= 1:
                        log_data["inverter_quantity"] = rr.registers[0]
                        
                        # Reset the alert string(s)
                        if log_data["inverter_quantity"] >= 1:
                            self.inverter_status_string = "<tr align=\"left\"><th>INVERTER</th></tr>"
                        else:
                            self.inverter_status_string = "<tr align=\"left\"><th>INVERTER</th></tr>" + "<tr><td>No device installed" + "</td></tr>"
                        
                        # Populate the Inverter data
                        for x in range(rr.registers[0]):
                            
                            uid = str(x+1)
                            offset = x * 25                                                 # There are up to 10 of each module type having 25 parameters
                            
                            try:
                                rr = mbTCP_client.read_holding_registers(751 + offset, 25, unit=1)
                
                                if not rr.isError():
                                    if len(rr.registers) >= 25:
                                        
                                        ######################
                                        # Log Data           #
                                        ######################
                                        
                                        log_data["inverter_" + uid + "_uid"] = rr.registers[0]
                                        log_data["inverter_" + uid + "_enable_state"] = True if rr.registers[1] == 1 else False
                                        log_data["inverter_" + uid + "_heartbeat"] = rr.registers[2]
                                        log_data["inverter_" + uid + "_inverter_state"] = rr.registers[3]
                                        log_data["inverter_" + uid + "_frequency"] = round(rr.registers[4] * pow(10, SF3), 3)
                                        log_data["inverter_" + uid + "_ac_line_voltage"] = round(self.twos_comp_to_int(rr.registers[5]) * pow(10, SF1), 3)
                                        log_data["inverter_" + uid + "_real_current_echo"] = round(self.twos_comp_to_int(rr.registers[6]))
                                        log_data["inverter_" + uid + "_real_current"] = round(self.twos_comp_to_int(rr.registers[7]) * pow(10, SF1), 3)
                                        log_data["inverter_" + uid + "_reactive_current_echo"] = round(self.twos_comp_to_int(rr.registers[8]))
                                        log_data["inverter_" + uid + "_reactive_current"] = round(self.twos_comp_to_int(rr.registers[9]) * pow(10, SF1), 3)
                                        log_data["inverter_" + uid + "_real_power_echo"] = round(self.twos_comp_to_int(rr.registers[10]))
                                        log_data["inverter_" + uid + "_real_power"] = round(self.twos_comp_to_int(rr.registers[11]) * pow(10, SF1), 3)
                                        log_data["inverter_" + uid + "_reactive_power_echo"] = round(self.twos_comp_to_int(rr.registers[12]))
                                        log_data["inverter_" + uid + "_reactive_power"] = round(self.twos_comp_to_int(rr.registers[13]) * pow(10, SF1), 3)
                                        log_data["inverter_" + uid + "_dc_bus_voltage"] = round(self.twos_comp_to_int(rr.registers[14]) * pow(10, SF1), 3)
                                        log_data["inverter_" + uid + "_dc_bus_current"] = round(self.twos_comp_to_int(rr.registers[15]) * pow(10, SF1), 3)
                                        log_data["inverter_" + uid + "_input_pv_voltage"] = round(self.twos_comp_to_int(rr.registers[16]) * pow(10, SF1), 3)
                                        log_data["inverter_" + uid + "_input_pv_current_command"] = round(self.twos_comp_to_int(rr.registers[17]))
                                        log_data["inverter_" + uid + "_input_pv_current"] = round(self.twos_comp_to_int(rr.registers[18]) * pow(10, SF1), 3)
                                        log_data["inverter_" + uid + "_internal_temperature"] = round(rr.registers[19] * pow(10, SF2), 3)
                                        log_data["inverter_" + uid + "_inlet_temperature"] = round(rr.registers[20] * pow(10, SF2), 3)
                                        log_data["inverter_" + uid + "_warnings"] = rr.registers[22]
                                        log_data["inverter_" + uid + "_alarms"] = rr.registers[23]
                                        log_data["inverter_" + uid + "_faults"] = rr.registers[24]
                                        log_data["inverter_" + uid + "_actions"] = 0    #rr.registers[25]  # Currently unavailable over SCADA
                                        
                                        ######################
                                        # Alerts             #
                                        ######################
                                        
                                        # Module Heartbeat Timeout
                                        if (rr.registers[2] - self.heartbeat_check[rr.registers[0]] & 0xFFFF) > 0:  # Heartbeat has changed since last rotation
                                           self.heartbeat_check[rr.registers[0]] = rr.registers[2]                  # Reset the Heartbeat reference
                                           self.heartbeat_check_count[rr.registers[0]] = 0                          # Reset the timeout count
                                        else:
                                           self.heartbeat_check_count[rr.registers[0]] += 1
                                            
                                        if self.heartbeat_check_count[rr.registers[0]] >= 60:                                        
                                            self.mod_timeout_alert = True
                                            self.send_alert_email = True
                                        
                                        # Module email string
                                        self.inverter_status_string += \
                                            "<tr><td>Inverter UID: </td><td>"               + str(log_data["inverter_" + uid + "_uid"]) + "</td></tr>" + \
                                            "<tr><td>Inverter Enable State: </td><td>"      + str(log_data["inverter_" + uid + "_enable_state"]) + "</td></tr>" + \
                                            "<tr><td>Heartbeat: </td><td>"                  + str(log_data["inverter_" + uid + "_heartbeat"]) + "</td></tr>" + \
                                            "<tr><td>Inverter State: </td><td>"             + str(log_data["inverter_" + uid + "_inverter_state"]) + "</td></tr>" + \
                                            "<tr><td>Frequency: </td><td>"                  + str(log_data["inverter_" + uid + "_frequency"]) + "</td></tr>" + \
                                            "<tr><td>AC Line Voltage: </td><td>"            + str(log_data["inverter_" + uid + "_ac_line_voltage"]) + "</td></tr>" + \
                                            "<tr><td>Real Current Echo: </td><td>"          + str(log_data["inverter_" + uid + "_real_current_echo"]) + "</td></tr>" + \
                                            "<tr><td>Real Current: </td><td>"               + str(log_data["inverter_" + uid + "_real_current"]) + "</td></tr>" + \
                                            "<tr><td>Reactive Current Echo: </td><td>"      + str(log_data["inverter_" + uid + "_reactive_current_echo"]) + "</td></tr>" + \
                                            "<tr><td>Reactive Current: </td><td>"           + str(log_data["inverter_" + uid + "_reactive_current"]) + "</td></tr>" + \
                                            "<tr><td>Real Power Echo: </td><td>"            + str(log_data["inverter_" + uid + "_real_power_echo"]) + "</td></tr>" + \
                                            "<tr><td>Real Power: </td><td>"                 + str(log_data["inverter_" + uid + "_real_power"]) + "</td></tr>" + \
                                            "<tr><td>Reative Power Echo: </td><td>"         + str(log_data["inverter_" + uid + "_reactive_power_echo"]) + "</td></tr>" + \
                                            "<tr><td>Reactive Power: </td><td>"             + str(log_data["inverter_" + uid + "_reactive_power"]) + "</td></tr>" + \
                                            "<tr><td>DC Bus Voltage: </td><td>"             + str(log_data["inverter_" + uid + "_dc_bus_voltage"]) + "</td></tr>" + \
                                            "<tr><td>DC Bus Current: </td><td>"             + str(log_data["inverter_" + uid + "_dc_bus_current"]) + "</td></tr>" + \
                                            "<tr><td>Input_PV_Voltage: </td><td>"           + str(log_data["inverter_" + uid + "_input_pv_voltage"]) + "</td></tr>" + \
                                            "<tr><td>Input PV Current Command: </td><td>"   + str(log_data["inverter_" + uid + "_input_pv_current_command"]) + "</td></tr>" + \
                                            "<tr><td>Input PV Current: </td><td>"           + str(log_data["inverter_" + uid + "_input_pv_current"]) + "</td></tr>" + \
                                            "<tr><td>Internal Temperature: </td><td>"       + str(log_data["inverter_" + uid + "_internal_temperature"]) + "</td></tr>" + \
                                            "<tr><td>Inlet Temperature: </td><td>"          + str(log_data["inverter_" + uid + "_inlet_temperature"]) + "</td></tr>" + \
                                            "<tr><td>Warnings: </td><td>"                   + str(log_data["inverter_" + uid + "_warnings"]) + "</td></tr>" + \
                                            "<tr><td>Alarms: </td><td>"                     + str(log_data["inverter_" + uid + "_alarms"]) + "</td></tr>" + \
                                            "<tr><td>Faults: </td><td>"                     + str(log_data["inverter_" + uid + "_faults"]) + "</td></tr>" + \
                                            "<tr><td>Actions: </td><td>"                    + str(log_data["inverter_" + uid + "_actions"]) + "</td></tr>"
                            
                            except Exception as e:
                                print("Logger: " + str(e))
                        
            except Exception as e:
                print("Logger: " + str(e))
            
            # AC Meter
            try:
                # How many installed AC Meters?
                rr = mbTCP_client.read_holding_registers(1000, 1, unit=1)
                
                if not rr.isError():
                    if len(rr.registers) >= 1:
                        log_data["ac_meter_quantity"] = rr.registers[0]
                        
                        # Reset the alert string(s)
                        if log_data["ac_meter_quantity"] >= 1:
                            self.ac_meter_status_string = "<tr align=\"left\"><th>AC METER</th></tr>"
                        else:
                            self.ac_meter_status_string = "<tr align=\"left\"><th>AC METER</th></tr>" + "<tr><td>No device installed" + "</td></tr>"
                        
                        # Populate the AC Meter data
                        for x in range(rr.registers[0]):
                            
                            uid = str(x+1)
                            offset = x * 25                                                 # There are up to 10 of each module type having 25 parameters
                            
                            try:
                                rr = mbTCP_client.read_holding_registers(1001 + offset, 25, unit=1)
                
                                if not rr.isError():
                                    if len(rr.registers) >= 25:
                                       
                                        ######################
                                        # Log Data           #
                                        ######################
                                        
                                        log_data["ac_meter_" + uid + "_uid"] = rr.registers[0]
                                        log_data["ac_meter_" + uid + "_enable_state"] = True if rr.registers[1] == 1 else False
                                        log_data["ac_meter_" + uid + "_heartbeat"] = rr.registers[2]
                                        log_data["ac_meter_" + uid + "_ac_meter_state"] = rr.registers[3]
                                        log_data["ac_meter_" + uid + "_average_phase_voltage"] = round(self.twos_comp_to_int(rr.registers[4]) * pow(10, SF1), 3)
                                        log_data["ac_meter_" + uid + "_average_line_voltage"] = round(self.twos_comp_to_int(rr.registers[5]) * pow(10, SF1), 3)
                                        log_data["ac_meter_" + uid + "_average_current"] = round(self.twos_comp_to_int(rr.registers[6]) * pow(10, SF1), 3)
                                        log_data["ac_meter_" + uid + "_frequency"] = round(rr.registers[7] * pow(10, SF3), 3)
                                        log_data["ac_meter_" + uid + "_total_power_factor"] = round(self.twos_comp_to_int(rr.registers[8]) * pow(10, SF2), 3)
                                        log_data["ac_meter_" + uid + "_total_active_power"] = round(self.twos_comp_to_int(rr.registers[9]) * pow(10, SF1), 3)
                                        log_data["ac_meter_" + uid + "_total_apparent_power"] = round(self.twos_comp_to_int(rr.registers[15]) * pow(10, SF1), 3)
                                        log_data["ac_meter_" + uid + "_total_current"] = round(self.twos_comp_to_int(rr.registers[17]) * pow(10, SF1), 3)
                                        log_data["ac_meter_" + uid + "_import_active_energy_hi"] = rr.registers[10]
                                        log_data["ac_meter_" + uid + "_import_active_energy_lo"] = rr.registers[11]
                                        log_data["ac_meter_" + uid + "_export_active_energy_hi"] = rr.registers[12]
                                        log_data["ac_meter_" + uid + "_export_active_energy_lo"] = rr.registers[13]
                                        log_data["ac_meter_" + uid + "_warnings"] = rr.registers[22]
                                        log_data["ac_meter_" + uid + "_alarms"] = rr.registers[23]
                                        log_data["ac_meter_" + uid + "_faults"] = rr.registers[24]
                                        log_data["ac_meter_" + uid + "_actions"] = 0    #rr.registers[25]  # Currently unavailable over SCADA
                                        
                                        ######################
                                        # Alerts             #
                                        ######################
                                        
                                        # Module Heartbeat Timeout
                                        if (rr.registers[2] - self.heartbeat_check[rr.registers[0]] & 0xFFFF) > 0:  # Heartbeat has changed since last rotation
                                           self.heartbeat_check[rr.registers[0]] = rr.registers[2]                  # Reset the Heartbeat reference
                                           self.heartbeat_check_count[rr.registers[0]] = 0                          # Reset the timeout count
                                        else:
                                           self.heartbeat_check_count[rr.registers[0]] += 1
                                            
                                        if self.heartbeat_check_count[rr.registers[0]] >= 60:                                        
                                            self.mod_timeout_alert = True
                                            self.send_alert_email = True
                                        
                                        # Module email string
                                        self.ac_meter_status_string += \
                                            "<tr><td>AC Meter UID: </td><td>"               + str(log_data["ac_meter_" + uid + "_uid"]) + "</td></tr>" + \
                                            "<tr><td>AC Meter Enable State: </td><td>"      + str(log_data["ac_meter_" + uid + "_enable_state"]) + "</td></tr>" + \
                                            "<tr><td>Heartbeat: </td><td>"                  + str(log_data["ac_meter_" + uid + "_heartbeat"]) + "</td></tr>" + \
                                            "<tr><td>AC Meter State: </td><td>"             + str(log_data["ac_meter_" + uid + "_ac_meter_state"]) + "</td></tr>" + \
                                            "<tr><td>Average Phase Voltage: </td><td>"      + str(log_data["ac_meter_" + uid + "_average_phase_voltage"]) + "</td></tr>" + \
                                            "<tr><td>Average Line Current: </td><td>"       + str(log_data["ac_meter_" + uid + "_average_line_voltage"]) + "</td></tr>" + \
                                            "<tr><td>Average Current: </td><td>"            + str(log_data["ac_meter_" + uid + "_average_current"]) + "</td></tr>" + \
                                            "<tr><td>Frequency: </td><td>"                  + str(log_data["ac_meter_" + uid + "_frequency"]) + "</td></tr>" + \
                                            "<tr><td>Total Power Factor: </td><td>"         + str(log_data["ac_meter_" + uid + "_total_power_factor"]) + "</td></tr>" + \
                                            "<tr><td>Total Active Power: </td><td>"         + str(log_data["ac_meter_" + uid + "_total_active_power"]) + "</td></tr>" + \
                                            "<tr><td>Total Apparent Power: </td><td>"       + str(log_data["ac_meter_" + uid + "_total_apparent_power"]) + "</td></tr>" + \
                                            "<tr><td>Total Current: </td><td>"              + str(log_data["ac_meter_" + uid + "_total_current"]) + "</td></tr>" + \
                                            "<tr><td>Import Active Energy Hi: </td><td>"    + str(log_data["ac_meter_" + uid + "_import_active_energy_hi"]) + "</td></tr>" + \
                                            "<tr><td>Import Active Energy Lo: </td><td>"    + str(log_data["ac_meter_" + uid + "_import_active_energy_lo"]) + "</td></tr>" + \
                                            "<tr><td>Export Active Energy Hi: </td><td>"    + str(log_data["ac_meter_" + uid + "_export_active_energy_hi"]) + "</td></tr>" + \
                                            "<tr><td>Export Active Energy Lo: </td><td>"    + str(log_data["ac_meter_" + uid + "_export_active_energy_lo"]) + "</td></tr>" + \
                                            "<tr><td>Warnings: </td><td>"                   + str(log_data["ac_meter_" + uid + "_warnings"]) + "</td></tr>" + \
                                            "<tr><td>Alarms: </td><td>"                     + str(log_data["ac_meter_" + uid + "_alarms"]) + "</td></tr>" + \
                                            "<tr><td>Faults: </td><td>"                     + str(log_data["ac_meter_" + uid + "_faults"]) + "</td></tr>" + \
                                            "<tr><td>Actions: </td><td>"                    + str(log_data["ac_meter_" + uid + "_actions"]) + "</td></tr>"
                                        
                            except Exception as e:
                                print("Logger: " + str(e))
                        
            except Exception as e:
                print("Logger: " + str(e))
            
            # DC Meter
            try:
                # How many installed DC Meters?
                rr = mbTCP_client.read_holding_registers(1250, 1, unit=1)
                
                if not rr.isError():
                    if len(rr.registers) >= 1:
                        log_data["dc_meter_quantity"] = rr.registers[0]
                        
                        # Reset the alert string(s)
                        if log_data["dc_meter_quantity"] >= 1:
                            self.dc_meter_status_string = "<tr align=\"left\"><th>DC METER</th></tr>"
                        else:
                            self.dc_meter_status_string = "<tr align=\"left\"><th>DC METER</th></tr>" + "<tr><td>No device installed" + "</td></tr>"
                        
                        # Populate the DC Meter data
                        for x in range(rr.registers[0]):
                            
                            uid = str(x+1)
                            offset = x * 25                                                 # There are up to 10 of each module type having 25 parameters
                            
                            try:
                                rr = mbTCP_client.read_holding_registers(1251 + offset, 25, unit=1)
                
                                if not rr.isError():
                                    if len(rr.registers) >= 25:
                                    
                                        ######################
                                        # Log Data           #
                                        ######################
                                        
                                        log_data["dc_meter_" + uid + "_uid"] = rr.registers[0]
                                        log_data["dc_meter_" + uid + "_enable_state"] = True if rr.registers[1] == 1 else False
                                        log_data["dc_meter_" + uid + "_heartbeat"] = rr.registers[2]
                                        log_data["dc_meter_" + uid + "_dc_meter_state"] = rr.registers[3]
                                        log_data["dc_meter_" + uid + "_dc_bus_voltage"] = round(self.twos_comp_to_int(rr.registers[4]) * pow(10, SF1), 3)
                                        log_data["dc_meter_" + uid + "_dc_bus_current"] = round(self.twos_comp_to_int(rr.registers[5]) * pow(10, SF1), 3)
                                        log_data["dc_meter_" + uid + "_dc_bus_power"] = round(self.twos_comp_to_int(rr.registers[6]) * pow(10, SF1), 3)
                                        log_data["dc_meter_" + uid + "_import_active_energy_hi"] = rr.registers[10]
                                        log_data["dc_meter_" + uid + "_import_active_energy_lo"] = rr.registers[11]
                                        log_data["dc_meter_" + uid + "_export_active_energy_hi"] = rr.registers[12]
                                        log_data["dc_meter_" + uid + "_export_active_energy_lo"] = rr.registers[13]
                                        log_data["dc_meter_" + uid + "_warnings"] = rr.registers[22]
                                        log_data["dc_meter_" + uid + "_alarms"] = rr.registers[23]
                                        log_data["dc_meter_" + uid + "_faults"] = rr.registers[24]
                                        log_data["dc_meter_" + uid + "_actions"] = 0    #rr.registers[25]  # Currently unavailable over SCADA
                                    
                                        ######################
                                        # Alerts             #
                                        ######################
                                        
                                        # Module Heartbeat Timeout
                                        if (rr.registers[2] - self.heartbeat_check[rr.registers[0]] & 0xFFFF) > 0:  # Heartbeat has changed since last rotation
                                           self.heartbeat_check[rr.registers[0]] = rr.registers[2]                  # Reset the Heartbeat reference
                                           self.heartbeat_check_count[rr.registers[0]] = 0                          # Reset the timeout count
                                        else:
                                           self.heartbeat_check_count[rr.registers[0]] += 1
                                            
                                        if self.heartbeat_check_count[rr.registers[0]] >= 60:                                        
                                            self.mod_timeout_alert = True
                                            self.send_alert_email = True
                                        
                                        # Module email string
                                        self.dc_meter_status_string += \
                                            "<tr><td>DC Meter UID: </td><td>"               + str(log_data["dc_meter_" + uid + "_uid"]) + "</td></tr>" + \
                                            "<tr><td>DC Meter Enable State: </td><td>"      + str(log_data["dc_meter_" + uid + "_enable_state"]) + "</td></tr>" + \
                                            "<tr><td>Heartbeat: </td><td>"                  + str(log_data["dc_meter_" + uid + "_heartbeat"]) + "</td></tr>" + \
                                            "<tr><td>DC Meter State: </td><td>"             + str(log_data["dc_meter_" + uid + "_dc_meter_state"]) + "</td></tr>" + \
                                            "<tr><td>DC Bus Voltage: </td><td>"             + str(log_data["dc_meter_" + uid + "_dc_bus_voltage"]) + "</td></tr>" + \
                                            "<tr><td>DC Bus Current: </td><td>"             + str(log_data["dc_meter_" + uid + "_dc_bus_current"]) + "</td></tr>" + \
                                            "<tr><td>DC Bus Power: </td><td>"               + str(log_data["dc_meter_" + uid + "_dc_bus_power"]) + "</td></tr>" + \
                                            "<tr><td>Import Active Energy Hi: </td><td>"    + str(log_data["dc_meter_" + uid + "_import_active_energy_hi"]) + "</td></tr>" + \
                                            "<tr><td>Import Active Energy Lo: </td><td>"    + str(log_data["dc_meter_" + uid + "_import_active_energy_lo"]) + "</td></tr>" + \
                                            "<tr><td>Export Active Energy Hi: </td><td>"    + str(log_data["dc_meter_" + uid + "_export_active_energy_hi"]) + "</td></tr>" + \
                                            "<tr><td>Export Active Energy Lo: </td><td>"    + str(log_data["dc_meter_" + uid + "_export_active_energy_lo"]) + "</td></tr>" + \
                                            "<tr><td>Warnings: </td><td>"                   + str(log_data["dc_meter_" + uid + "_warnings"]) + "</td></tr>" + \
                                            "<tr><td>Alarms: </td><td>"                     + str(log_data["dc_meter_" + uid + "_alarms"]) + "</td></tr>" + \
                                            "<tr><td>Faults: </td><td>"                     + str(log_data["dc_meter_" + uid + "_faults"]) + "</td></tr>" + \
                                            "<tr><td>Actions: </td><td>"                    + str(log_data["dc_meter_" + uid + "_actions"]) + "</td></tr>"
                                        
                            except Exception as e:
                                print("Logger: " + str(e))
                        
            except Exception as e:
                print("Logger: " + str(e))
            
            # Digital IO
            try:
                # How many installed Digital IOs?
                rr = mbTCP_client.read_holding_registers(1500, 1, unit=1)
                
                if not rr.isError():
                    if len(rr.registers) >= 1:
                        log_data["digio_quantity"] = rr.registers[0]
                        
                        # Reset the alert string(s)
                        if log_data["digio_quantity"] >= 1:
                            self.digital_io_status_string = "<tr align=\"left\"><th>DIGITAL IO</th></tr>"
                        else:
                            self.digital_io_status_string = "<tr align=\"left\"><th>DIGITAL IO</th></tr>" + "<tr><td>No device installed" + "</td></tr>"
                        
                        # Populate the Digital IO data
                        for x in range(rr.registers[0]):
                            
                            uid = str(x+1)
                            offset = x * 25                                                 # There are up to 10 of each module type having 25 parameters
                            
                            try:
                                rr = mbTCP_client.read_holding_registers(1501 + offset, 25, unit=1)
                
                                if not rr.isError():
                                    if len(rr.registers) >= 25:
                                        
                                        ######################
                                        # Log Data           #
                                        ######################
                                        
                                        log_data["digio_" + uid + "_uid"] = rr.registers[0]
                                        log_data["digio_" + uid + "_enable_state"] = True if rr.registers[1] == 1 else False
                                        log_data["digio_" + uid + "_heartbeat"] = rr.registers[2]
                                        log_data["digio_" + uid + "_digio_state"] = rr.registers[3]
                                        log_data["digio_" + uid + "_input_quantity"] = rr.registers[4]
                                        log_data["digio_" + uid + "_output_quantity"] = rr.registers[5]
                                        log_data["digio_" + uid + "_digital_input_bits_7_0"] = rr.registers[6]
                                        log_data["digio_" + uid + "_digital_output_bits_7_0"] = rr.registers[7]
                                        log_data["digio_" + uid + "_digital_input_bits_15_8"] = rr.registers[8]
                                        log_data["digio_" + uid + "_digital_output_bits_15_8"] = rr.registers[9]
                                        log_data["digio_" + uid + "_digital_input_bits_23_16"] = rr.registers[10]
                                        log_data["digio_" + uid + "_digital_output_bits_23_16"] = rr.registers[11]
                                        log_data["digio_" + uid + "_digital_input_bits_31_24"] = rr.registers[12]
                                        log_data["digio_" + uid + "_digital_output_bits_31_24"] = rr.registers[13]
                                        log_data["digio_" + uid + "_warnings"] = rr.registers[22]
                                        log_data["digio_" + uid + "_alarms"] = rr.registers[23]
                                        log_data["digio_" + uid + "_faults"] = rr.registers[24]
                                        log_data["digio_" + uid + "_actions"] = 0    #rr.registers[25]  # Currently unavailable over SCADA
                                        
                                        ######################
                                        # Alerts             #
                                        ######################
                                        
                                        # Module Heartbeat Timeout
                                        if (rr.registers[2] - self.heartbeat_check[rr.registers[0]] & 0xFFFF) > 0:  # Heartbeat has changed since last rotation
                                           self.heartbeat_check[rr.registers[0]] = rr.registers[2]                  # Reset the Heartbeat reference
                                           self.heartbeat_check_count[rr.registers[0]] = 0                          # Reset the timeout count
                                        else:
                                           self.heartbeat_check_count[rr.registers[0]] += 1
                                            
                                        if self.heartbeat_check_count[rr.registers[0]] >= 60:                                                           
                                            self.mod_timeout_alert = True
                                            self.send_alert_email = True
                                        
                                        # Module email string
                                        self.digital_io_status_string += \
                                            "<tr><td>Digital IO UID: </td><td>"             + str(log_data["digio_" + uid + "_uid"]) + "</td></tr>" + \
                                            "<tr><td>Digital IO Enable State: </td><td>"    + str(log_data["digio_" + uid + "_enable_state"]) + "</td></tr>" + \
                                            "<tr><td>Heartbeat: </td><td>"                  + str(log_data["digio_" + uid + "_heartbeat"]) + "</td></tr>" + \
                                            "<tr><td>Digital IO State: </td><td>"           + str(log_data["digio_" + uid + "_digio_state"]) + "</td></tr>" + \
                                            "<tr><td>Input Quantity: </td><td>"             + str(log_data["digio_" + uid + "_input_quantity"]) + "</td></tr>" + \
                                            "<tr><td>Output Quantity: </td><td>"            + str(log_data["digio_" + uid + "_output_quantity"]) + "</td></tr>" + \
                                            "<tr><td>Digital Inputs (7...0): </td><td>"     + str(log_data["digio_" + uid + "_digital_input_bits_7_0"]) + "</td></tr>" + \
                                            "<tr><td>Digital Inputs (15...8): </td><td>"    + str(log_data["digio_" + uid + "_digital_input_bits_15_8"]) + "</td></tr>" + \
                                            "<tr><td>Digital Inputs (23...16): </td><td>"   + str(log_data["digio_" + uid + "_digital_input_bits_23_16"]) + "</td></tr>" + \
                                            "<tr><td>Digital Inputs (31...24): </td><td>"   + str(log_data["digio_" + uid + "_digital_input_bits_31_24"]) + "</td></tr>" + \
                                            "<tr><td>Digital Outputs (7...0): </td><td>"    + str(log_data["digio_" + uid + "_digital_output_bits_7_0"]) + "</td></tr>" + \
                                            "<tr><td>Digital Outputs (15...8): </td><td>"   + str(log_data["digio_" + uid + "_digital_output_bits_15_8"]) + "</td></tr>" + \
                                            "<tr><td>Digital Outputs (23...16): </td><td>"  + str(log_data["digio_" + uid + "_digital_output_bits_23_16"]) + "</td></tr>" + \
                                            "<tr><td>Digital Outputs (31...24): </td><td>"  + str(log_data["digio_" + uid + "_digital_output_bits_31_24"]) + "</td></tr>" + \
                                            "<tr><td>Warnings: </td><td>"                   + str(log_data["digio_" + uid + "_warnings"]) + "</td></tr>" + \
                                            "<tr><td>Alarms: </td><td>"                     + str(log_data["digio_" + uid + "_alarms"]) + "</td></tr>" + \
                                            "<tr><td>Faults: </td><td>"                     + str(log_data["digio_" + uid + "_faults"]) + "</td></tr>" + \
                                            "<tr><td>Actions: </td><td>"                    + str(log_data["digio_" + uid + "_actions"]) + "</td></tr>"
                                        
                            except Exception as e:
                                print("Logger: " + str(e))
                        
            except Exception as e:
                print("Logger: " + str(e))
                
            # Analogue IO
            try:
                # How many installed Analogue IOs?
                rr = mbTCP_client.read_holding_registers(1750, 1, unit=1)
                
                if not rr.isError():
                    if len(rr.registers) >= 1:
                        log_data["anaio_quantity"] = rr.registers[0]
                        
                        # Reset the alert string(s)
                        if log_data["anaio_quantity"] >= 1:
                            self.analogue_io_status_string = "<tr align=\"left\"><th>ANALOGUE IO</th></tr>"
                        else:
                            self.analogue_io_status_string = "<tr align=\"left\"><th>ANALOGUE IO</th></tr>" + "<tr><td>No device installed" + "</td></tr>"
                        
                        # Populate the Analogue IO data
                        for x in range(rr.registers[0]):
                            
                            uid = str(x+1)
                            offset = x * 25                                                 # There are up to 10 of each module type having 25 parameters
                            
                            try:
                                rr = mbTCP_client.read_holding_registers(1751 + offset, 25, unit=1)
                
                                if not rr.isError():
                                    if len(rr.registers) >= 25:
                                        
                                        ######################
                                        # Log Data           #
                                        ######################
                                        
                                        log_data["anaio_" + uid + "_uid"] = rr.registers[0]
                                        log_data["anaio_" + uid + "_enable_state"] = True if rr.registers[1] == 1 else False
                                        log_data["anaio_" + uid + "_heartbeat"] = rr.registers[2]
                                        log_data["anaio_" + uid + "_anaio_state"] = rr.registers[3]
                                        log_data["anaio_" + uid + "_input_quantity"] = rr.registers[4]
                                        log_data["anaio_" + uid + "_output_quantity"] = rr.registers[5]
                                        log_data["anaio_" + uid + "_analogue_input_0"] = round(self.twos_comp_to_int(rr.registers[6]) * pow(10, SF1), 3)
                                        log_data["anaio_" + uid + "_analogue_input_1"] = round(self.twos_comp_to_int(rr.registers[7]) * pow(10, SF1), 3)
                                        log_data["anaio_" + uid + "_analogue_input_2"] = round(self.twos_comp_to_int(rr.registers[8]) * pow(10, SF1), 3)
                                        log_data["anaio_" + uid + "_analogue_input_3"] = round(self.twos_comp_to_int(rr.registers[9]) * pow(10, SF1), 3)
                                        log_data["anaio_" + uid + "_analogue_input_4"] = round(self.twos_comp_to_int(rr.registers[10]) * pow(10, SF1), 3)
                                        log_data["anaio_" + uid + "_analogue_input_5"] = round(self.twos_comp_to_int(rr.registers[11]) * pow(10, SF1), 3)
                                        log_data["anaio_" + uid + "_analogue_input_6"] = round(self.twos_comp_to_int(rr.registers[12]) * pow(10, SF1), 3)
                                        log_data["anaio_" + uid + "_analogue_input_7"] = round(self.twos_comp_to_int(rr.registers[13]) * pow(10, SF1), 3)
                                        log_data["anaio_" + uid + "_analogue_output_0"] = rr.registers[14]
                                        log_data["anaio_" + uid + "_analogue_output_1"] = rr.registers[15]
                                        log_data["anaio_" + uid + "_analogue_output_2"] = rr.registers[16]
                                        log_data["anaio_" + uid + "_analogue_output_3"] = rr.registers[17]
                                        log_data["anaio_" + uid + "_analogue_output_4"] = rr.registers[18]
                                        log_data["anaio_" + uid + "_analogue_output_5"] = rr.registers[19]
                                        log_data["anaio_" + uid + "_analogue_output_6"] = rr.registers[20]
                                        log_data["anaio_" + uid + "_analogue_output_7"] = rr.registers[21]
                                        log_data["anaio_" + uid + "_warnings"] = rr.registers[22]
                                        log_data["anaio_" + uid + "_alarms"] = rr.registers[23]
                                        log_data["anaio_" + uid + "_faults"] = rr.registers[24]
                                        log_data["anaio_" + uid + "_actions"] = 0    #rr.registers[25]  # Currently unavailable over SCADA
                                        
                                        ######################
                                        # Alerts             #
                                        ######################
                                        
                                        # Module Heartbeat Timeout
                                        if (rr.registers[2] - self.heartbeat_check[rr.registers[0]] & 0xFFFF) > 0:  # Heartbeat has changed since last rotation
                                           self.heartbeat_check[rr.registers[0]] = rr.registers[2]                  # Reset the Heartbeat reference
                                           self.heartbeat_check_count[rr.registers[0]] = 0                          # Reset the timeout count
                                        else:
                                           self.heartbeat_check_count[rr.registers[0]] += 1
                                            
                                        if self.heartbeat_check_count[rr.registers[0]] >= 60:                                                           
                                            self.mod_timeout_alert = True
                                            self.send_alert_email = True
                                        
                                        # Module email string
                                        self.analogue_io_status_string += \
                                            "<tr><td>Analogue IO UID: </td><td>"            + str(log_data["anaio_" + uid + "_uid"]) + "</td></tr>" + \
                                            "<tr><td>Analogue IO Enable State: </td><td>"   + str(log_data["anaio_" + uid + "_enable_state"]) + "</td></tr>" + \
                                            "<tr><td>Heartbeat: </td><td>"                  + str(log_data["anaio_" + uid + "_heartbeat"]) + "</td></tr>" + \
                                            "<tr><td>Analogue IO State: </td><td>"          + str(log_data["anaio_" + uid + "_anaio_state"]) + "</td></tr>" + \
                                            "<tr><td>Input Quantity: </td><td>"             + str(log_data["anaio_" + uid + "_input_quantity"]) + "</td></tr>" + \
                                            "<tr><td>Output Quantity: </td><td>"            + str(log_data["anaio_" + uid + "_output_quantity"]) + "</td></tr>" + \
                                            "<tr><td>Analogue Input 0: </td><td>"           + str(log_data["anaio_" + uid + "_analogue_input_0"]) + "</td></tr>" + \
                                            "<tr><td>Analogue Input 1: </td><td>"           + str(log_data["anaio_" + uid + "_analogue_input_1"]) + "</td></tr>" + \
                                            "<tr><td>Analogue Input 2: </td><td>"           + str(log_data["anaio_" + uid + "_analogue_input_2"]) + "</td></tr>" + \
                                            "<tr><td>Analogue Input 3: </td><td>"           + str(log_data["anaio_" + uid + "_analogue_input_3"]) + "</td></tr>" + \
                                            "<tr><td>Analogue Input 4: </td><td>"           + str(log_data["anaio_" + uid + "_analogue_input_4"]) + "</td></tr>" + \
                                            "<tr><td>Analogue Input 5: </td><td>"           + str(log_data["anaio_" + uid + "_analogue_input_5"]) + "</td></tr>" + \
                                            "<tr><td>Analogue Input 6: </td><td>"           + str(log_data["anaio_" + uid + "_analogue_input_6"]) + "</td></tr>" + \
                                            "<tr><td>Analogue Input 7: </td><td>"           + str(log_data["anaio_" + uid + "_analogue_input_7"]) + "</td></tr>" + \
                                            "<tr><td>Analogue Output 0: </td><td>"          + str(log_data["anaio_" + uid + "_analogue_output_0"]) + "</td></tr>" + \
                                            "<tr><td>Analogue Output 1: </td><td>"          + str(log_data["anaio_" + uid + "_analogue_output_1"]) + "</td></tr>" + \
                                            "<tr><td>Analogue Output 2: </td><td>"          + str(log_data["anaio_" + uid + "_analogue_output_2"]) + "</td></tr>" + \
                                            "<tr><td>Analogue Output 3: </td><td>"          + str(log_data["anaio_" + uid + "_analogue_output_3"]) + "</td></tr>" + \
                                            "<tr><td>Analogue Output 4: </td><td>"          + str(log_data["anaio_" + uid + "_analogue_output_4"]) + "</td></tr>" + \
                                            "<tr><td>Analogue Output 5: </td><td>"          + str(log_data["anaio_" + uid + "_analogue_output_5"]) + "</td></tr>" + \
                                            "<tr><td>Analogue Output 6: </td><td>"          + str(log_data["anaio_" + uid + "_analogue_output_6"]) + "</td></tr>" + \
                                            "<tr><td>Analogue Output 7: </td><td>"          + str(log_data["anaio_" + uid + "_analogue_output_7"]) + "</td></tr>" + \
                                            "<tr><td>Warnings: </td><td>"                   + str(log_data["anaio_" + uid + "_warnings"]) + "</td></tr>" + \
                                            "<tr><td>Alarms: </td><td>"                     + str(log_data["anaio_" + uid + "_alarms"]) + "</td></tr>" + \
                                            "<tr><td>Faults: </td><td>"                     + str(log_data["anaio_" + uid + "_faults"]) + "</td></tr>" + \
                                            "<tr><td>Actions: </td><td>"                    + str(log_data["anaio_" + uid + "_actions"]) + "</td></tr>"
                                        
                            except Exception as e:
                                print("Logger: " + str(e))
                        
            except Exception as e:
                print("Logger: " + str(e))
            
            # Mixed IO
            try:
                # How many installed Mixed IOs?
                rr = mbTCP_client.read_holding_registers(2000, 1, unit=1)
                
                if not rr.isError():
                    if len(rr.registers) >= 1:
                        log_data["mixedio_quantity"] = rr.registers[0]
                        
                        # Reset the alert string(s)
                        if log_data["mixedio_quantity"] >= 1:
                            self.mixed_io_status_string = "<tr align=\"left\"><th>MIXED IO</th></tr>"
                        else:
                            self.mixed_io_status_string = "<tr align=\"left\"><th>MIXED IO</th></tr>" + "<tr><td>No device installed" + "</td></tr>"
                        
                        # Populate the Mixed IO data
                        for x in range(rr.registers[0]):
                            
                            uid = str(x+1)
                            offset = x * 25                                                 # There are up to 10 of each module type having 25 parameters
                            
                            try:
                                rr = mbTCP_client.read_holding_registers(2001 + offset, 25, unit=1)
                
                                if not rr.isError():
                                    if len(rr.registers) >= 25:
                                        
                                        ######################
                                        # Log Data           #
                                        ######################
                                        
                                        log_data["mixedio_" + uid + "_uid"] = rr.registers[0]
                                        log_data["mixedio_" + uid + "_enable_state"] = True if rr.registers[1] == 1 else False
                                        log_data["mixedio_" + uid + "_heartbeat"] = rr.registers[2]                                       
                                        log_data["mixedio_" + uid + "_warnings"] = rr.registers[22]
                                        log_data["mixedio_" + uid + "_alarms"] = rr.registers[23]
                                        log_data["mixedio_" + uid + "_faults"] = rr.registers[24]
                                        log_data["mixedio_" + uid + "_actions"] = 0    #rr.registers[25]  # Currently unavailable over SCADA
                                        
                                        ######################
                                        # Alerts             #
                                        ######################
                                        
                                        # Module Heartbeat Timeout
                                        if (rr.registers[2] - self.heartbeat_check[rr.registers[0]] & 0xFFFF) > 0:  # Heartbeat has changed since last rotation
                                           self.heartbeat_check[rr.registers[0]] = rr.registers[2]                  # Reset the Heartbeat reference
                                           self.heartbeat_check_count[rr.registers[0]] = 0                          # Reset the timeout count
                                        else:
                                           self.heartbeat_check_count[rr.registers[0]] += 1
                                            
                                        if self.heartbeat_check_count[rr.registers[0]] >= 60:                                                           
                                            self.mod_timeout_alert = True
                                            self.send_alert_email = True
                                        
                                        # Module email string
                                        self.mixed_io_status_string += \
                                            "<tr><td>Mixed IO UID: </td><td>"               + str(log_data["mixedio_" + uid + "_uid"]) + "</td></tr>" + \
                                            "<tr><td>Mixed IO Enable State: </td><td>"      + str(log_data["mixedio_" + uid + "_enable_state"]) + "</td></tr>" + \
                                            "<tr><td>Heartbeat: </td><td>"                  + str(log_data["mixedio_" + uid + "_heartbeat"]) + "</td></tr>" + \
                                            "<tr><td>Warnings: </td><td>"                   + str(log_data["mixedio_" + uid + "_warnings"]) + "</td></tr>" + \
                                            "<tr><td>Alarms: </td><td>"                     + str(log_data["mixedio_" + uid + "_alarms"]) + "</td></tr>" + \
                                            "<tr><td>Faults: </td><td>"                     + str(log_data["mixedio_" + uid + "_faults"]) + "</td></tr>" + \
                                            "<tr><td>Actions: </td><td>"                    + str(log_data["mixedio_" + uid + "_actions"]) + "</td></tr>"
                                        
                            except Exception as e:
                                print("Logger: " + str(e))
                        
            except Exception as e:
                print("Logger: " + str(e))

            # Switch
            try:
                # How many installed Switches?
                rr = mbTCP_client.read_holding_registers(2250, 1, unit=1)
                
                if not rr.isError():
                    if len(rr.registers) >= 1:
                        log_data["switch_quantity"] = rr.registers[0]
                        
                        # Reset the alert string(s)
                        if log_data["switch_quantity"] >= 1:
                            self.switch_status_string = "<tr align=\"left\"><th>SWITCH</th></tr>"
                        else:
                            self.switch_status_string = "<tr align=\"left\"><th>SWITCH</th></tr>" + "<tr><td>No device installed" + "</td></tr>"
                        
                        # Populate the Switch data
                        for x in range(rr.registers[0]):
                            
                            uid = str(x+1)
                            offset = x * 25                                                 # There are up to 10 of each module type having 25 parameters
                            
                            try:
                                rr = mbTCP_client.read_holding_registers(2251 + offset, 25, unit=1)
                
                                if not rr.isError():
                                    if len(rr.registers) >= 25:
                                        
                                        ######################
                                        # Log Data           #
                                        ######################
                                        
                                        log_data["switch_" + uid + "_uid"] = rr.registers[0]
                                        log_data["switch_" + uid + "_enable_state"] = True if rr.registers[1] == 1 else False
                                        log_data["switch_" + uid + "_heartbeat"] = rr.registers[2]                                       
                                        log_data["switch_" + uid + "_warnings"] = rr.registers[22]
                                        log_data["switch_" + uid + "_alarms"] = rr.registers[23]
                                        log_data["switch_" + uid + "_faults"] = rr.registers[24]
                                        log_data["switch_" + uid + "_actions"] = 0    #rr.registers[25]  # Currently unavailable over SCADA
                                        
                                        ######################
                                        # Alerts             #
                                        ######################
                                        
                                        # Module Heartbeat Timeout
                                        if (rr.registers[2] - self.heartbeat_check[rr.registers[0]] & 0xFFFF) > 0:  # Heartbeat has changed since last rotation
                                           self.heartbeat_check[rr.registers[0]] = rr.registers[2]                  # Reset the Heartbeat reference
                                           self.heartbeat_check_count[rr.registers[0]] = 0                          # Reset the timeout count
                                        else:
                                           self.heartbeat_check_count[rr.registers[0]] += 1
                                            
                                        if self.heartbeat_check_count[rr.registers[0]] >= 60:                                                           
                                            self.mod_timeout_alert = True
                                            self.send_alert_email = True
                                        
                                        # Module email string
                                        self.switch_status_string += \
                                            "<tr><td>Switch UID: </td><td>"                 + str(log_data["switch_" + uid + "_uid"]) + "</td></tr>" + \
                                            "<tr><td>Switch Enable State: </td><td>"        + str(log_data["switch_" + uid + "_enable_state"]) + "</td></tr>" + \
                                            "<tr><td>Heartbeat: </td><td>"                  + str(log_data["switch_" + uid + "_heartbeat"]) + "</td></tr>" + \
                                            "<tr><td>Warnings: </td><td>"                   + str(log_data["switch_" + uid + "_warnings"]) + "</td></tr>" + \
                                            "<tr><td>Alarms: </td><td>"                     + str(log_data["switch_" + uid + "_alarms"]) + "</td></tr>" + \
                                            "<tr><td>Faults: </td><td>"                     + str(log_data["switch_" + uid + "_faults"]) + "</td></tr>" + \
                                            "<tr><td>Actions: </td><td>"                    + str(log_data["switch_" + uid + "_actions"]) + "</td></tr>"
                                        
                            except Exception as e:
                                print("Logger: " + str(e))
                        
            except Exception as e:
                print("Logger: " + str(e))
            
            # Li-Ion Tamer
            try:
                # How many installed Li-Ion Tamers?
                rr = mbTCP_client.read_holding_registers(2500, 1, unit=1)
                
                if not rr.isError():
                    if len(rr.registers) >= 1:
                        log_data["li_ion_quantity"] = rr.registers[0]
                        
                        # Reset the alert string(s)
                        if log_data["li_ion_quantity"] >= 1:
                            self.li_ion_status_string = "<tr align=\"left\"><th>LI-ION TAMER</th></tr>"
                        else:
                            self.li_ion_status_string = "<tr align=\"left\"><th>LI-ION TAMER</th></tr>" + "<tr><td>No device installed" + "</td></tr>"
                        
                        # Populate the Li-Ion data
                        for x in range(rr.registers[0]):
                            
                            uid = str(x+1)
                            offset = x * 25                                                 # There are up to 10 of each module type having 25 parameters
                            
                            try:
                                rr = mbTCP_client.read_holding_registers(2501 + offset, 25, unit=1)
                
                                if not rr.isError():
                                    if len(rr.registers) >= 25:
                                        
                                        ######################
                                        # Log Data           #
                                        ######################
                                        
                                        log_data["li_ion_" + uid + "_uid"] = rr.registers[0]
                                        log_data["li_ion_" + uid + "_enable_state"] = True if rr.registers[1] == 1 else False
                                        log_data["li_ion_" + uid + "_heartbeat"] = rr.registers[2]
                                        log_data["li_ion_" + uid + "_li_ion_state"] = rr.registers[3]
                                        log_data["li_ion_" + uid + "_sensor_quantity"] = rr.registers[4]
                                        log_data["li_ion_" + uid + "_sensor_1_alarm"] = True if rr.registers[5] == 1 else False
                                        log_data["li_ion_" + uid + "_sensor_2_alarm"] = True if rr.registers[6] == 1 else False
                                        log_data["li_ion_" + uid + "_sensor_3_alarm"] = True if rr.registers[7] == 1 else False
                                        log_data["li_ion_" + uid + "_sensor_4_alarm"] = True if rr.registers[8] == 1 else False
                                        log_data["li_ion_" + uid + "_sensor_5_alarm"] = True if rr.registers[9] == 1 else False
                                        log_data["li_ion_" + uid + "_sensor_6_alarm"] = True if rr.registers[10] == 1 else False
                                        log_data["li_ion_" + uid + "_sensor_7_alarm"] = True if rr.registers[11] == 1 else False
                                        log_data["li_ion_" + uid + "_sensor_8_alarm"] = True if rr.registers[12] == 1 else False
                                        log_data["li_ion_" + uid + "_sensor_9_alarm"] = True if rr.registers[13] == 1 else False
                                        log_data["li_ion_" + uid + "_sensor_10_alarm"] = True if rr.registers[14] == 1 else False
                                        log_data["li_ion_" + uid + "_sensor_11_alarm"] = True if rr.registers[15] == 1 else False
                                        log_data["li_ion_" + uid + "_sensor_12_alarm"] = True if rr.registers[16] == 1 else False
                                        log_data["li_ion_" + uid + "_sensor_alarm_any"] = True if rr.registers[17] == 1 else False
                                        log_data["li_ion_" + uid + "_sensor_error_any"] = True if rr.registers[18] == 1 else False
                                        log_data["li_ion_" + uid + "_system_alarm"] = True if rr.registers[19] == 1 else False
                                        log_data["li_ion_" + uid + "_warnings"] = rr.registers[22]
                                        log_data["li_ion_" + uid + "_alarms"] = rr.registers[23]
                                        log_data["li_ion_" + uid + "_faults"] = rr.registers[24]
                                        log_data["li_ion_" + uid + "_actions"] = 0    #rr.registers[25]  # Currently unavailable over SCADA
                                        
                                        ######################
                                        # Alerts             #
                                        ######################
                                        
                                        # Module Heartbeat Timeout
                                        if (rr.registers[2] - self.heartbeat_check[rr.registers[0]] & 0xFFFF) > 0:  # Heartbeat has changed since last rotation
                                           self.heartbeat_check[rr.registers[0]] = rr.registers[2]                  # Reset the Heartbeat reference
                                           self.heartbeat_check_count[rr.registers[0]] = 0                          # Reset the timeout count
                                        else:
                                           self.heartbeat_check_count[rr.registers[0]] += 1
                                            
                                        if self.heartbeat_check_count[rr.registers[0]] >= 60:                                                           
                                            self.mod_timeout_alert = True
                                            self.send_alert_email = True
                                        
                                        # Module email string
                                        self.li_ion_status_string += \
                                            "<tr><td>Li-Ion Tamer UID: </td><td>"           + str(log_data["li_ion_" + uid + "_uid"]) + "</td></tr>" + \
                                            "<tr><td>Li-Ion Tamer Enable State: </td><td>"  + str(log_data["li_ion_" + uid + "_enable_state"]) + "</td></tr>" + \
                                            "<tr><td>Heartbeat: </td><td>"                  + str(log_data["li_ion_" + uid + "_heartbeat"]) + "</td></tr>" + \
                                            "<tr><td>Li-Ion Tamer State: </td><td>"         + str(log_data["li_ion_" + uid + "_li_ion_state"]) + "</td></tr>" + \
                                            "<tr><td>Sensor Quantity: </td><td>"            + str(log_data["li_ion_" + uid + "_sensor_quantity"]) + "</td></tr>" + \
                                            "<tr><td>Sensor 1 Alarm: </td><td>"             + str(log_data["li_ion_" + uid + "_sensor_1_alarm"]) + "</td></tr>" + \
                                            "<tr><td>Sensor 2 Alarm: </td><td>"             + str(log_data["li_ion_" + uid + "_sensor_2_alarm"]) + "</td></tr>" + \
                                            "<tr><td>Sensor 3 Alarm: </td><td>"             + str(log_data["li_ion_" + uid + "_sensor_3_alarm"]) + "</td></tr>" + \
                                            "<tr><td>Sensor 4 Alarm: </td><td>"             + str(log_data["li_ion_" + uid + "_sensor_4_alarm"]) + "</td></tr>" + \
                                            "<tr><td>Sensor 5 Alarm: </td><td>"             + str(log_data["li_ion_" + uid + "_sensor_5_alarm"]) + "</td></tr>" + \
                                            "<tr><td>Sensor 6 Alarm: </td><td>"             + str(log_data["li_ion_" + uid + "_sensor_6_alarm"]) + "</td></tr>" + \
                                            "<tr><td>Sensor 7 Alarm: </td><td>"             + str(log_data["li_ion_" + uid + "_sensor_7_alarm"]) + "</td></tr>" + \
                                            "<tr><td>Sensor 8 Alarm: </td><td>"             + str(log_data["li_ion_" + uid + "_sensor_8_alarm"]) + "</td></tr>" + \
                                            "<tr><td>Sensor 9 Alarm: </td><td>"             + str(log_data["li_ion_" + uid + "_sensor_9_alarm"]) + "</td></tr>" + \
                                            "<tr><td>Sensor 10 Alarm: </td><td>"            + str(log_data["li_ion_" + uid + "_sensor_10_alarm"]) + "</td></tr>" + \
                                            "<tr><td>Sensor 11 Alarm: </td><td>"            + str(log_data["li_ion_" + uid + "_sensor_11_alarm"]) + "</td></tr>" + \
                                            "<tr><td>Sensor 12 Alarm: </td><td>"            + str(log_data["li_ion_" + uid + "_sensor_12_alarm"]) + "</td></tr>" + \
                                            "<tr><td>Sensor Alarm Any: </td><td>"           + str(log_data["li_ion_" + uid + "_sensor_alarm_any"]) + "</td></tr>" + \
                                            "<tr><td>Sensor Error Any: </td><td>"           + str(log_data["li_ion_" + uid + "_sensor_error_any"]) + "</td></tr>" + \
                                            "<tr><td>System Alarm: </td><td>"               + str(log_data["li_ion_" + uid + "_system_alarm"]) + "</td></tr>" + \
                                            "<tr><td>Warnings: </td><td>"                   + str(log_data["li_ion_" + uid + "_warnings"]) + "</td></tr>" + \
                                            "<tr><td>Alarms: </td><td>"                     + str(log_data["li_ion_" + uid + "_alarms"]) + "</td></tr>" + \
                                            "<tr><td>Faults: </td><td>"                     + str(log_data["li_ion_" + uid + "_faults"]) + "</td></tr>" + \
                                            "<tr><td>Actions: </td><td>"                    + str(log_data["li_ion_" + uid + "_actions"]) + "</td></tr>"
                                        
                            except Exception as e:
                                print("Logger: " + str(e))
                        
            except Exception as e:
                print("Logger: " + str(e))
            
            # DCDC
            try:
                # How many installed DCDCs?
                rr = mbTCP_client.read_holding_registers(2750, 1, unit=1)
                
                if not rr.isError():
                    if len(rr.registers) >= 1:
                        log_data["dcdc_quantity"] = rr.registers[0]
                        
                        # Reset the alert string(s)
                        if log_data["dcdc_quantity"] >= 1:
                            self.dcdc_status_string = "<tr align=\"left\"><th>DC-DC</th></tr>"
                        else:
                            self.dcdc_status_string = "<tr align=\"left\"><th>DC-DC</th></tr>" + "<tr><td>No device installed" + "</td></tr>"
                        
                        # Populate the DCDC data
                        for x in range(rr.registers[0]):
                            
                            uid = str(x+1)
                            offset = x * 25                                                 # There are up to 10 of each module type having 25 parameters
                            
                            try:
                                rr = mbTCP_client.read_holding_registers(2751 + offset, 25, unit=1)
                
                                if not rr.isError():
                                    if len(rr.registers) >= 25:
                                        
                                        ######################
                                        # Log Data           #
                                        ######################
                                        
                                        log_data["dcdc_" + uid + "_uid"] = rr.registers[0]
                                        log_data["dcdc_" + uid + "_enable_state"] = True if rr.registers[1] == 1 else False
                                        log_data["dcdc_" + uid + "_heartbeat"] = rr.registers[2] 
                                        log_data["dcdc_" + uid + "_dcdc_state"] = rr.registers[3] 
                                        log_data["dcdc_" + uid + "_voltage_setpoint"] = rr.registers[7]
                                        log_data["dcdc_" + uid + "_current_setpoint"] = rr.registers[8]
                                        log_data["dcdc_" + uid + "_dc_input_voltage"] = rr.registers[11]
                                        log_data["dcdc_" + uid + "_dc_input_current"] = rr.registers[12]
                                        log_data["dcdc_" + uid + "_dc_output_voltage"] = rr.registers[13]
                                        log_data["dcdc_" + uid + "_dc_output_current"] = rr.registers[14]
                                        log_data["dcdc_" + uid + "_temperature"] = rr.registers[17]
                                        log_data["dcdc_" + uid + "_humidity"] = rr.registers[18]
                                        log_data["dcdc_" + uid + "_warnings"] = rr.registers[22]
                                        log_data["dcdc_" + uid + "_alarms"] = rr.registers[23]
                                        log_data["dcdc_" + uid + "_faults"] = rr.registers[24]
                                        log_data["dcdc_" + uid + "_actions"] = 0    #rr.registers[25]  # Currently unavailable over SCADA
                                        
                                        ######################
                                        # Alerts             #
                                        ######################
                                        
                                        # Module Heartbeat Timeout
                                        if (rr.registers[2] - self.heartbeat_check[rr.registers[0]] & 0xFFFF) > 0:  # Heartbeat has changed since last rotation
                                           self.heartbeat_check[rr.registers[0]] = rr.registers[2]                  # Reset the Heartbeat reference
                                           self.heartbeat_check_count[rr.registers[0]] = 0                          # Reset the timeout count
                                        else:
                                           self.heartbeat_check_count[rr.registers[0]] += 1
                                            
                                        if self.heartbeat_check_count[rr.registers[0]] >= 60:                                                           
                                            self.mod_timeout_alert = True
                                            self.send_alert_email = True
                                        
                                        # Module email string
                                        self.dcdc_status_string += \
                                            "<tr><td>DCDC UID: </td><td>"                   + str(log_data["dcdc_" + uid + "_uid"]) + "</td></tr>" + \
                                            "<tr><td>DCDC Enable State: </td><td>"          + str(log_data["dcdc_" + uid + "_enable_state"]) + "</td></tr>" + \
                                            "<tr><td>Heartbeat: </td><td>"                  + str(log_data["dcdc_" + uid + "_heartbeat"]) + "</td></tr>" + \
                                            "<tr><td>DCDC State: </td><td>"                 + str(log_data["dcdc_" + uid + "_dcdc_state"]) + "</td></tr>" + \
                                            "<tr><td>Voltage Setpoint: </td><td>"           + str(log_data["dcdc_" + uid + "_voltage_setpoint"]) + "</td></tr>" + \
                                            "<tr><td>Current Setpoint: </td><td>"           + str(log_data["dcdc_" + uid + "_current_setpoint"]) + "</td></tr>" + \
                                            "<tr><td>Input Voltage: </td><td>"              + str(log_data["dcdc_" + uid + "_dc_input_voltage"]) + "</td></tr>" + \
                                            "<tr><td>Input Current: </td><td>"              + str(log_data["dcdc_" + uid + "_dc_input_current"]) + "</td></tr>" + \
                                            "<tr><td>Output Voltage: </td><td>"             + str(log_data["dcdc_" + uid + "_dc_output_voltage"]) + "</td></tr>" + \
                                            "<tr><td>Output Current: </td><td>"             + str(log_data["dcdc_" + uid + "_dc_output_current"]) + "</td></tr>" + \
                                            "<tr><td>Temperature: </td><td>"                + str(log_data["dcdc_" + uid + "_temperature"]) + "</td></tr>" + \
                                            "<tr><td>Humidity: </td><td>"                   + str(log_data["dcdc_" + uid + "_humidity"]) + "</td></tr>" + \
                                            "<tr><td>Warnings: </td><td>"                   + str(log_data["dcdc_" + uid + "_warnings"]) + "</td></tr>" + \
                                            "<tr><td>Alarms: </td><td>"                     + str(log_data["dcdc_" + uid + "_alarms"]) + "</td></tr>" + \
                                            "<tr><td>Faults: </td><td>"                     + str(log_data["dcdc_" + uid + "_faults"]) + "</td></tr>" + \
                                            "<tr><td>Actions: </td><td>"                    + str(log_data["dcdc_" + uid + "_actions"]) + "</td></tr>"
                                        
                            except Exception as e:
                                print("Logger: " + str(e))
                        
            except Exception as e:
                print("Logger: " + str(e))
                
            # HVACs
            try:
                # How many installed HVACs?
                rr = mbTCP_client.read_holding_registers(3000, 1, unit=1)
                
                if not rr.isError():
                    if len(rr.registers) >= 1:
                        log_data["aircon_quantity"] = rr.registers[0]
                        
                        # Reset the alert string(s)
                        if log_data["aircon_quantity"] >= 1:
                            self.aircon_status_string = "<tr align=\"left\"><th>AIRCON</th></tr>"
                        else:
                            self.aircon_status_string = "<tr align=\"left\"><th>AIRCON</th></tr>" + "<tr><td>No device installed" + "</td></tr>"
                        
                        # Populate the HVAC data
                        for x in range(rr.registers[0]):
                            
                            uid = str(x+1)
                            offset = x * 25                                                 # There are up to 10 of each module type having 25 parameters
                            
                            try:
                                rr = mbTCP_client.read_holding_registers(3001 + offset, 25, unit=1)
                
                                if not rr.isError():
                                    if len(rr.registers) >= 25:
                                        
                                        ######################
                                        # Log Data           #
                                        ######################
                                        
                                        log_data["aircon_" + uid + "_uid"] = rr.registers[0]
                                        log_data["aircon_" + uid + "_enable_state"] = True if rr.registers[1] == 1 else False
                                        log_data["aircon_" + uid + "_heartbeat"] = rr.registers[2]
                                        log_data["aircon_" + uid + "_aircon_state"] = rr.registers[3]
                                        log_data["aircon_" + uid + "_internal_ac_quantity"] = rr.registers[4]
                                        log_data["aircon_" + uid + "_internal_ac_onoff_states"] = rr.registers[5]
                                        log_data["aircon_" + uid + "_temperature_setpoint"] = rr.registers[6]
                                        log_data["aircon_" + uid + "_fan_mode_speed"] = rr.registers[7]
                                        log_data["aircon_" + uid + "_ac_1_ambient_temperature"] = round(self.twos_comp_to_int(rr.registers[9]) * pow(10, SF2), 3)
                                        log_data["aircon_" + uid + "_ac_1_fan_mode_speed"] = rr.registers[10]
                                        log_data["aircon_" + uid + "_ac_2_ambient_temperature"] = round(self.twos_comp_to_int(rr.registers[11]) * pow(10, SF2), 3)
                                        log_data["aircon_" + uid + "_ac_2_fan_mode_speed"] = rr.registers[12]
                                        log_data["aircon_" + uid + "_ac_3_ambient_temperature"] = round(self.twos_comp_to_int(rr.registers[13]) * pow(10, SF2), 3)
                                        log_data["aircon_" + uid + "_ac_3_fan_mode_speed"] = rr.registers[14]
                                        log_data["aircon_" + uid + "_ac_4_ambient_temperature"] = round(self.twos_comp_to_int(rr.registers[15]) * pow(10, SF2), 3)
                                        log_data["aircon_" + uid + "_ac_4_fan_mode_speed"] = rr.registers[16]
                                        log_data["aircon_" + uid + "_ac_5_ambient_temperature"] = round(self.twos_comp_to_int(rr.registers[17]) * pow(10, SF2), 3)
                                        log_data["aircon_" + uid + "_ac_5_fan_mode_speed"] = rr.registers[18]
                                        log_data["aircon_" + uid + "_ac_6_ambient_temperature"] = round(self.twos_comp_to_int(rr.registers[19]) * pow(10, SF2), 3)
                                        log_data["aircon_" + uid + "_ac_6_fan_mode_speed"] = rr.registers[20]
                                        log_data["aircon_" + uid + "_warnings"] = rr.registers[22]
                                        log_data["aircon_" + uid + "_alarms"] = rr.registers[23]
                                        log_data["aircon_" + uid + "_faults"] = rr.registers[24]
                                        log_data["aircon_" + uid + "_actions"] = 0    #rr.registers[25]  # Currently unavailable over SCADA
                                        
                                        ######################
                                        # Alerts             #
                                        ######################
                                        
                                        # Module Heartbeat Timeout
                                        if (rr.registers[2] - self.heartbeat_check[rr.registers[0]] & 0xFFFF) > 0:  # Heartbeat has changed since last rotation
                                           self.heartbeat_check[rr.registers[0]] = rr.registers[2]                  # Reset the Heartbeat reference
                                           self.heartbeat_check_count[rr.registers[0]] = 0                          # Reset the timeout count
                                        else:
                                           self.heartbeat_check_count[rr.registers[0]] += 1
                                            
                                        if self.heartbeat_check_count[rr.registers[0]] >= 60:                                                           
                                            self.mod_timeout_alert = True
                                            self.send_alert_email = True
                                        
                                        # Module email string
                                        self.aircon_status_string += \
                                            "<tr><td>Aircon UID: </td><td>"                 + str(log_data["aircon_" + uid + "_uid"]) + "</td></tr>" + \
                                            "<tr><td>Aircon Enable State: </td><td>"        + str(log_data["aircon_" + uid + "_enable_state"]) + "</td></tr>" + \
                                            "<tr><td>Heartbeat: </td><td>"                  + str(log_data["aircon_" + uid + "_heartbeat"]) + "</td></tr>" + \
                                            "<tr><td>Aircon State: </td><td>"               + str(log_data["aircon_" + uid + "_aircon_state"]) + "</td></tr>" + \
                                            "<tr><td>Aircon Quantity: </td><td>"            + str(log_data["aircon_" + uid + "_internal_ac_quantity"]) + "</td></tr>" + \
                                            "<tr><td>On/Off States: </td><td>"              + str(log_data["aircon_" + uid + "_internal_ac_onoff_states"]) + "</td></tr>" + \
                                            "<tr><td>Temperature Setpoint: </td><td>"       + str(log_data["aircon_" + uid + "_temperature_setpoint"]) + "</td></tr>" + \
                                            "<tr><td>Fan Mode / Speed All: </td><td>"       + str(log_data["aircon_" + uid + "_fan_mode_speed"]) + "</td></tr>" + \
                                            "<tr><td>Temperature 1: </td><td>"              + str(log_data["aircon_" + uid + "_ac_1_ambient_temperature"]) + "</td></tr>" + \
                                            "<tr><td>Fan Mode / Speed 1: </td><td>"         + str(log_data["aircon_" + uid + "_ac_1_fan_mode_speed"]) + "</td></tr>" + \
                                            "<tr><td>Temperature 2: </td><td>"              + str(log_data["aircon_" + uid + "_ac_2_ambient_temperature"]) + "</td></tr>" + \
                                            "<tr><td>Fan Mode / Speed 2: </td><td>"         + str(log_data["aircon_" + uid + "_ac_2_fan_mode_speed"]) + "</td></tr>" + \
                                            "<tr><td>Temperature 3: </td><td>"              + str(log_data["aircon_" + uid + "_ac_3_ambient_temperature"]) + "</td></tr>" + \
                                            "<tr><td>Fan Mode / Speed 3: </td><td>"         + str(log_data["aircon_" + uid + "_ac_3_fan_mode_speed"]) + "</td></tr>" + \
                                            "<tr><td>Temperature 4: </td><td>"              + str(log_data["aircon_" + uid + "_ac_4_ambient_temperature"]) + "</td></tr>" + \
                                            "<tr><td>Fan Mode / Speed: </td><td>"           + str(log_data["aircon_" + uid + "_ac_4_fan_mode_speed"]) + "</td></tr>" + \
                                            "<tr><td>Temperature 5: </td><td>"              + str(log_data["aircon_" + uid + "_ac_5_ambient_temperature"]) + "</td></tr>" + \
                                            "<tr><td>Fan Mode / Speed 5: </td><td>"         + str(log_data["aircon_" + uid + "_ac_5_fan_mode_speed"]) + "</td></tr>" + \
                                            "<tr><td>Temperature 6: </td><td>"              + str(log_data["aircon_" + uid + "_ac_6_ambient_temperature"]) + "</td></tr>" + \
                                            "<tr><td>Fan Mode / Speed 6: </td><td>"         + str(log_data["aircon_" + uid + "_ac_6_fan_mode_speed"]) + "</td></tr>" + \
                                            "<tr><td>Warnings: </td><td>"                   + str(log_data["aircon_" + uid + "_warnings"]) + "</td></tr>" + \
                                            "<tr><td>Alarms: </td><td>"                     + str(log_data["aircon_" + uid + "_alarms"]) + "</td></tr>" + \
                                            "<tr><td>Faults: </td><td>"                     + str(log_data["aircon_" + uid + "_faults"]) + "</td></tr>" + \
                                            "<tr><td>Actions: </td><td>"                    + str(log_data["aircon_" + uid + "_actions"]) + "</td></tr>"
                                        
                            except Exception as e:
                                print("Logger: " + str(e))
                        
            except Exception as e:
                print("Logger: " + str(e))
            
            # Sensor
            try:
                # How many installed Sensors?
                rr = mbTCP_client.read_holding_registers(3250, 1, unit=1)
                
                if not rr.isError():
                    if len(rr.registers) >= 1:
                        log_data["sensor_quantity"] = rr.registers[0]
                        
                        # Reset the alert string(s)
                        if log_data["sensor_quantity"] >= 1:
                            self.sensor_status_string = "<tr align=\"left\"><th>SENSOR</th></tr>"
                        else:
                            self.sensor_status_string = "<tr align=\"left\"><th>SENSOR</th></tr>" + "<tr><td>No device installed" + "</td></tr>"
                        
                        # Populate the Sensor data
                        for x in range(rr.registers[0]):
                            
                            uid = str(x+1)
                            offset = x * 25                                                 # There are up to 10 of each module type having 25 parameters
                            
                            try:
                                rr = mbTCP_client.read_holding_registers(3251 + offset, 25, unit=1)
                
                                if not rr.isError():
                                    if len(rr.registers) >= 25:
                                        
                                        ######################
                                        # Log Data           #
                                        ######################
                                        
                                        log_data["sensor_" + uid + "_uid"] = rr.registers[0]
                                        log_data["sensor_" + uid + "_enable_state"] = True if rr.registers[1] == 1 else False
                                        log_data["sensor_" + uid + "_heartbeat"] = rr.registers[2]                                       
                                        log_data["sensor_" + uid + "_warnings"] = rr.registers[22]
                                        log_data["sensor_" + uid + "_alarms"] = rr.registers[23]
                                        log_data["sensor_" + uid + "_faults"] = rr.registers[24]
                                        log_data["sensor_" + uid + "_actions"] = 0    #rr.registers[25]  # Currently unavailable over SCADA
                                        
                                        ######################
                                        # Alerts             #
                                        ######################
                                        
                                        # Module Heartbeat Timeout
                                        if (rr.registers[2] - self.heartbeat_check[rr.registers[0]] & 0xFFFF) > 0:  # Heartbeat has changed since last rotation
                                           self.heartbeat_check[rr.registers[0]] = rr.registers[2]                  # Reset the Heartbeat reference
                                           self.heartbeat_check_count[rr.registers[0]] = 0                          # Reset the timeout count
                                        else:
                                           self.heartbeat_check_count[rr.registers[0]] += 1
                                            
                                        if self.heartbeat_check_count[rr.registers[0]] >= 60:                                                           
                                            self.mod_timeout_alert = True
                                            self.send_alert_email = True
                                        
                                        # Module email string
                                        self.sensor_status_string += \
                                            "<tr><td>Switch UID: </td><td>"                 + str(log_data["sensor_" + uid + "_uid"]) + "</td></tr>" + \
                                            "<tr><td>Switch Enable State: </td><td>"        + str(log_data["sensor_" + uid + "_enable_state"]) + "</td></tr>" + \
                                            "<tr><td>Heartbeat: </td><td>"                  + str(log_data["sensor_" + uid + "_heartbeat"]) + "</td></tr>" + \
                                            "<tr><td>Warnings: </td><td>"                   + str(log_data["sensor_" + uid + "_warnings"]) + "</td></tr>" + \
                                            "<tr><td>Alarms: </td><td>"                     + str(log_data["sensor_" + uid + "_alarms"]) + "</td></tr>" + \
                                            "<tr><td>Faults: </td><td>"                     + str(log_data["sensor_" + uid + "_faults"]) + "</td></tr>" + \
                                            "<tr><td>Actions: </td><td>"                    + str(log_data["sensor_" + uid + "_actions"]) + "</td></tr>"
                                        
                            except Exception as e:
                                print("Logger: " + str(e))
                        
            except Exception as e:
                print("Logger: " + str(e))
                
            # Fuel Cells
            try:
                # How many installed Fuel Cells?
                rr = mbTCP_client.read_holding_registers(3500, 1, unit=1)
                
                if not rr.isError():
                    if len(rr.registers) >= 1:
                        log_data["fuel_cell_quantity"] = rr.registers[0]
                        
                        # Reset the alert string(s)
                        if log_data["fuel_cell_quantity"] >= 1:
                            self.fuel_cell_status_string = "<tr align=\"left\"><th>FUEL CELL</th></tr>"
                        else:
                            self.fuel_cell_status_string = "<tr align=\"left\"><th>FUEL CELL</th></tr>" + "<tr><td>No device installed" + "</td></tr>"
                        
                        # Populate the Fuel Cell data
                        for x in range(rr.registers[0]):
                            
                            uid = str(x+1)
                            offset = x * 25                                                 # There are up to 10 of each module type having 25 parameters
                            
                            try:
                                rr = mbTCP_client.read_holding_registers(3501 + offset, 25, unit=1)
                
                                if not rr.isError():
                                    if len(rr.registers) >= 25:
                                        
                                        ######################
                                        # Log Data           #
                                        ######################
                                        
                                        log_data["fuel_cell_" + uid + "_uid"] = rr.registers[0]
                                        log_data["fuel_cell_" + uid + "_enable_state"] = True if rr.registers[1] == 1 else False
                                        log_data["fuel_cell_" + uid + "_heartbeat"] = rr.registers[2]
                                        log_data["fuel_cell_" + uid + "_fuel_cell_state"] = rr.registers[3]
                                        log_data["fuel_cell_" + uid + "_fuel_cell_control"] = rr.registers[4]
                                        log_data["fuel_cell_" + uid + "_fuel_cell_faults"] = rr.registers[5]
                                        log_data["fuel_cell_" + uid + "_fuel_cell_ocv"] = rr.registers[6]
                                        log_data["fuel_cell_" + uid + "_max_allowable_current"] = rr.registers[7]
                                        log_data["fuel_cell_" + uid + "_max_allowable_power"] = rr.registers[8]
                                        log_data["fuel_cell_" + uid + "_obligated_current"] = rr.registers[9]
                                        log_data["fuel_cell_" + uid + "_dc_bus_voltage"] = round(self.twos_comp_to_int(rr.registers[10]), 3)
                                        log_data["fuel_cell_" + uid + "_dc_bus_current"] = round(self.twos_comp_to_int(rr.registers[11]), 3)
                                        log_data["fuel_cell_" + uid + "_dc_bus_power"] = round(self.twos_comp_to_int(rr.registers[12]), 3)
                                        log_data["fuel_cell_" + uid + "_v0_voltage"] = round(self.twos_comp_to_int(rr.registers[14]), 3)
                                        log_data["fuel_cell_" + uid + "_a0_current"] = round(self.twos_comp_to_int(rr.registers[15]), 3)
                                        log_data["fuel_cell_" + uid + "_v1_voltage"] = round(self.twos_comp_to_int(rr.registers[16]), 3)
                                        log_data["fuel_cell_" + uid + "_a1_current"] = round(self.twos_comp_to_int(rr.registers[17]), 3)
                                        log_data["fuel_cell_" + uid + "_warnings"] = rr.registers[22]
                                        log_data["fuel_cell_" + uid + "_alarms"] = rr.registers[23]
                                        log_data["fuel_cell_" + uid + "_faults"] = rr.registers[24]
                                        log_data["fuel_cell_" + uid + "_actions"] = 0    #rr.registers[25]  # Currently unavailable over SCADA
                                        
                                        ######################
                                        # Alerts             #
                                        ######################
                                        
                                        # Module Heartbeat Timeout
                                        if (rr.registers[2] - self.heartbeat_check[rr.registers[0]] & 0xFFFF) > 0:  # Heartbeat has changed since last rotation
                                           self.heartbeat_check[rr.registers[0]] = rr.registers[2]                  # Reset the Heartbeat reference
                                           self.heartbeat_check_count[rr.registers[0]] = 0                          # Reset the timeout count
                                        else:
                                           self.heartbeat_check_count[rr.registers[0]] += 1
                                            
                                        if self.heartbeat_check_count[rr.registers[0]] >= 60:                                                           
                                            self.mod_timeout_alert = True
                                            self.send_alert_email = True
                                        
                                        # Module email string
                                        self.fuel_cell_status_string += \
                                            "<tr><td>Fuel Cell UID: </td><td>"              + str(log_data["fuel_cell_" + uid + "_uid"]) + "</td></tr>" + \
                                            "<tr><td>Fuel Cell Enable State: </td><td>"     + str(log_data["fuel_cell_" + uid + "_enable_state"]) + "</td></tr>" + \
                                            "<tr><td>Heartbeat: </td><td>"                  + str(log_data["fuel_cell_" + uid + "_heartbeat"]) + "</td></tr>" + \
                                            "<tr><td>Fuel Cell State: </td><td>"            + str(log_data["fuel_cell_" + uid + "_fuel_cell_state"]) + "</td></tr>" + \
                                            "<tr><td>Fuel Cell Control: </td><td>"          + str(log_data["fuel_cell_" + uid + "_fuel_cell_control"]) + "</td></tr>" + \
                                            "<tr><td>Fuel Cell Faults: </td><td>"           + str(log_data["fuel_cell_" + uid + "_fuel_cell_faults"]) + "</td></tr>" + \
                                            "<tr><td>Fuel Cell OCV: </td><td>"              + str(log_data["fuel_cell_" + uid + "_fuel_cell_ocv"]) + "</td></tr>" + \
                                            "<tr><td>Max Allowable Current: </td><td>"      + str(log_data["fuel_cell_" + uid + "_max_allowable_current"]) + "</td></tr>" + \
                                            "<tr><td>Max Allowable Power: </td><td>"        + str(log_data["fuel_cell_" + uid + "_max_allowable_power"]) + "</td></tr>" + \
                                            "<tr><td>Obligated Current: </td><td>"          + str(log_data["fuel_cell_" + uid + "_obligated_current"]) + "</td></tr>" + \
                                            "<tr><td>DC Bus Voltage: </td><td>"             + str(log_data["fuel_cell_" + uid + "_dc_bus_voltage"]) + "</td></tr>" + \
                                            "<tr><td>DC Bus Current: </td><td>"             + str(log_data["fuel_cell_" + uid + "_dc_bus_current"]) + "</td></tr>" + \
                                            "<tr><td>DC Bus Power: </td><td>"               + str(log_data["fuel_cell_" + uid + "_dc_bus_power"]) + "</td></tr>" + \
                                            "<tr><td>V0 Voltage: </td><td>"                 + str(log_data["fuel_cell_" + uid + "_v0_voltage"]) + "</td></tr>" + \
                                            "<tr><td>A0 Current: </td><td>"                 + str(log_data["fuel_cell_" + uid + "_a0_current"]) + "</td></tr>" + \
                                            "<tr><td>V1 Voltage: </td><td>"                 + str(log_data["fuel_cell_" + uid + "_v1_voltage"]) + "</td></tr>" + \
                                            "<tr><td>A1 Current: </td><td>"                 + str(log_data["fuel_cell_" + uid + "_a1_current"]) + "</td></tr>" + \
                                            "<tr><td>Warnings: </td><td>"                   + str(log_data["fuel_cell_" + uid + "_warnings"]) + "</td></tr>" + \
                                            "<tr><td>Alarms: </td><td>"                     + str(log_data["fuel_cell_" + uid + "_alarms"]) + "</td></tr>" + \
                                            "<tr><td>Faults: </td><td>"                     + str(log_data["fuel_cell_" + uid + "_faults"]) + "</td></tr>" + \
                                            "<tr><td>Actions: </td><td>"                    + str(log_data["fuel_cell_" + uid + "_actions"]) + "</td></tr>"
                                        
                            except Exception as e:
                                print("Logger: " + str(e))
                        
            except Exception as e:
                print("Logger: " + str(e))
                
            # AC Generators
            try:
                # How many installed AC Generators?
                rr = mbTCP_client.read_holding_registers(3750, 1, unit=1)
                
                if not rr.isError():
                    if len(rr.registers) >= 1:
                        log_data["ac_gen_quantity"] = rr.registers[0]
                        
                        # Reset the alert string(s)
                        if log_data["ac_gen_quantity"] >= 1:
                            self.ac_gen_status_string = "<tr align=\"left\"><th>AC_GENERATOR</th></tr>"
                        else:
                            self.ac_gen_status_string = "<tr align=\"left\"><th>AC GENERATOR</th></tr>" + "<tr><td>No device installed" + "</td></tr>"
                        
                        # Populate the AC Generators data
                        for x in range(rr.registers[0]):
                            
                            uid = str(x+1)
                            offset = x * 25                                                 # There are up to 10 of each module type having 25 parameters
                            
                            try:
                                rr = mbTCP_client.read_holding_registers(3751 + offset, 25, unit=1)
                
                                if not rr.isError():
                                    if len(rr.registers) >= 25:
                                        
                                        ######################
                                        # Log Data           #
                                        ######################
                                        
                                        log_data["ac_gen_" + uid + "_uid"] = rr.registers[0]
                                        log_data["ac_gen_" + uid + "_enable_state"] = True if rr.registers[1] == 1 else False
                                        log_data["ac_gen_" + uid + "_heartbeat"] = rr.registers[2]
                                        log_data["ac_gen_" + uid + "_ac_gen_state"] = rr.registers[3]
                                        log_data["ac_gen_" + uid + "_control_mode"] = rr.registers[4]
                                        log_data["ac_gen_" + uid + "_generator_alarms"] = rr.registers[5]
                                        log_data["ac_gen_" + uid + "_oil_pressure"] = round(self.twos_comp_to_int(rr.registers[8]) * pow(10, SF1), 3)
                                        log_data["ac_gen_" + uid + "_coolant_temperature"] = round(self.twos_comp_to_int(rr.registers[9]) * pow(10, SF2), 3)
                                        log_data["ac_gen_" + uid + "_fuel_level"] = round(self.twos_comp_to_int(rr.registers[10]) * pow(10, SF1), 3)
                                        log_data["ac_gen_" + uid + "_alternator_voltage"] = round(self.twos_comp_to_int(rr.registers[11]) * pow(10, SF1), 3)
                                        log_data["ac_gen_" + uid + "_battery_voltage"] = round(self.twos_comp_to_int(rr.registers[12]) * pow(10, SF1), 3)
                                        log_data["ac_gen_" + uid + "_engine_speed"] = round(self.twos_comp_to_int(rr.registers[13]) * pow(10, SF1), 3)
                                        log_data["ac_gen_" + uid + "_ac_frequency"] = round(self.twos_comp_to_int(rr.registers[14]) * pow(10, SF2), 3)
                                        log_data["ac_gen_" + uid + "_ac_line_voltage"] = round(self.twos_comp_to_int(rr.registers[15]) * pow(10, SF1), 3)
                                        log_data["ac_gen_" + uid + "_ac_phase_voltage"] = round(self.twos_comp_to_int(rr.registers[16]) * pow(10, SF1), 3)
                                        log_data["ac_gen_" + uid + "_ac_current"] = round(self.twos_comp_to_int(rr.registers[17]) * pow(10, SF1), 3)
                                        log_data["ac_gen_" + uid + "_ac_earth_current"] = round(self.twos_comp_to_int(rr.registers[18]) * pow(10, SF1), 3)
                                        log_data["ac_gen_" + uid + "_ac_power"] = round(self.twos_comp_to_int(rr.registers[19]) * pow(10, SF1), 3)
                                        log_data["ac_gen_" + uid + "_warnings"] = rr.registers[22]
                                        log_data["ac_gen_" + uid + "_alarms"] = rr.registers[23]
                                        log_data["ac_gen_" + uid + "_faults"] = rr.registers[24]
                                        log_data["ac_gen_" + uid + "_actions"] = 0    #rr.registers[25]  # Currently unavailable over SCADA
                                        
                                        ######################
                                        # Alerts             #
                                        ######################
                                        
                                        # Module Heartbeat Timeout
                                        if (rr.registers[2] - self.heartbeat_check[rr.registers[0]] & 0xFFFF) > 0:  # Heartbeat has changed since last rotation
                                           self.heartbeat_check[rr.registers[0]] = rr.registers[2]                  # Reset the Heartbeat reference
                                           self.heartbeat_check_count[rr.registers[0]] = 0                          # Reset the timeout count
                                        else:
                                           self.heartbeat_check_count[rr.registers[0]] += 1
                                            
                                        if self.heartbeat_check_count[rr.registers[0]] >= 60:                                                           
                                            self.mod_timeout_alert = True
                                            self.send_alert_email = True
                                        
                                        # Module email string
                                        self.ac_gen_status_string += \
                                            "<tr><td>AC Gen UID: </td><td>"                 + str(log_data["ac_gen_" + uid + "_uid"]) + "</td></tr>" + \
                                            "<tr><td>AC Gen Enable State: </td><td>"        + str(log_data["ac_gen_" + uid + "_enable_state"]) + "</td></tr>" + \
                                            "<tr><td>Heartbeat: </td><td>"                  + str(log_data["ac_gen_" + uid + "_heartbeat"]) + "</td></tr>" + \
                                            "<tr><td>AC Gen State: </td><td>"               + str(log_data["ac_gen_" + uid + "_ac_gen_state"]) + "</td></tr>" + \
                                            "<tr><td>Control Mode: </td><td>"               + str(log_data["ac_gen_" + uid + "_control_mode"]) + "</td></tr>" + \
                                            "<tr><td>Generator Alarms: </td><td>"           + str(log_data["ac_gen_" + uid + "_generator_alarms"]) + "</td></tr>" + \
                                            "<tr><td>Oil Pressure: </td><td>"               + str(log_data["ac_gen_" + uid + "_oil_pressure"]) + "</td></tr>" + \
                                            "<tr><td>Coolant Temperature: </td><td>"        + str(log_data["ac_gen_" + uid + "_coolant_temperature"]) + "</td></tr>" + \
                                            "<tr><td>Fuel Level: </td><td>"                 + str(log_data["ac_gen_" + uid + "_fuel_level"]) + "</td></tr>" + \
                                            "<tr><td>Alternator Voltage: </td><td>"         + str(log_data["ac_gen_" + uid + "_alternator_voltage"]) + "</td></tr>" + \
                                            "<tr><td>Battery Voltage: </td><td>"            + str(log_data["ac_gen_" + uid + "_battery_voltage"]) + "</td></tr>" + \
                                            "<tr><td>Engine Speed: </td><td>"               + str(log_data["ac_gen_" + uid + "_engine_speed"]) + "</td></tr>" + \
                                            "<tr><td>AC Frequency: </td><td>"               + str(log_data["ac_gen_" + uid + "_ac_frequency"]) + "</td></tr>" + \
                                            "<tr><td>AC Line Voltage: </td><td>"            + str(log_data["ac_gen_" + uid + "_ac_line_voltage"]) + "</td></tr>" + \
                                            "<tr><td>AC Phase Voltage: </td><td>"           + str(log_data["ac_gen_" + uid + "_ac_phase_voltage"]) + "</td></tr>" + \
                                            "<tr><td>AC Current: </td><td>"                 + str(log_data["ac_gen_" + uid + "_ac_current"]) + "</td></tr>" + \
                                            "<tr><td>AC Earth Current: </td><td>"           + str(log_data["ac_gen_" + uid + "_ac_earth_current"]) + "</td></tr>" + \
                                            "<tr><td>AC Power: </td><td>"                   + str(log_data["ac_gen_" + uid + "_ac_power"]) + "</td></tr>" + \
                                            "<tr><td>Warnings: </td><td>"                   + str(log_data["ac_gen_" + uid + "_warnings"]) + "</td></tr>" + \
                                            "<tr><td>Alarms: </td><td>"                     + str(log_data["ac_gen_" + uid + "_alarms"]) + "</td></tr>" + \
                                            "<tr><td>Faults: </td><td>"                     + str(log_data["ac_gen_" + uid + "_faults"]) + "</td></tr>" + \
                                            "<tr><td>Actions: </td><td>"                    + str(log_data["ac_gen_" + uid + "_actions"]) + "</td></tr>"
                                        
                            except Exception as e:
                                print("Logger: " + str(e))
                        
            except Exception as e:
                print("Logger: " + str(e))
            
            # AC Wind
            try:
                # How many installed Wind Turbines?
                rr = mbTCP_client.read_holding_registers(4000, 1, unit=1)
                
                if not rr.isError():
                    if len(rr.registers) >= 1:
                        log_data["ac_wind_quantity"] = rr.registers[0]
                        
                        # Reset the alert string(s)
                        if log_data["ac_wind_quantity"] >= 1:
                            self.ac_wind_status_string = "<tr align=\"left\"><th>AC WIND</th></tr>"
                        else:
                            self.ac_wind_status_string = "<tr align=\"left\"><th>AC WIND</th></tr>" + "<tr><td>No device installed" + "</td></tr>"
                        
                        # Populate the AC Wind data
                        for x in range(rr.registers[0]):
                            
                            uid = str(x+1)
                            offset = x * 25                                                 # There are up to 10 of each module type having 25 parameters
                            
                            try:
                                rr = mbTCP_client.read_holding_registers(4001 + offset, 25, unit=1)
                
                                if not rr.isError():
                                    if len(rr.registers) >= 25:
                                        
                                        ######################
                                        # Log Data           #
                                        ######################
                                        
                                        log_data["ac_wind_" + uid + "_uid"] = rr.registers[0]
                                        log_data["ac_wind_" + uid + "_enable_state"] = True if rr.registers[1] == 1 else False
                                        log_data["ac_wind_" + uid + "_heartbeat"] = rr.registers[2]                                       
                                        log_data["ac_wind_" + uid + "_warnings"] = rr.registers[22]
                                        log_data["ac_wind_" + uid + "_alarms"] = rr.registers[23]
                                        log_data["ac_wind_" + uid + "_faults"] = rr.registers[24]
                                        log_data["ac_wind_" + uid + "_actions"] = 0    #rr.registers[25]  # Currently unavailable over SCADA
                                        
                                        ######################
                                        # Alerts             #
                                        ######################
                                        
                                        # Module Heartbeat Timeout
                                        if (rr.registers[2] - self.heartbeat_check[rr.registers[0]] & 0xFFFF) > 0:  # Heartbeat has changed since last rotation
                                           self.heartbeat_check[rr.registers[0]] = rr.registers[2]                  # Reset the Heartbeat reference
                                           self.heartbeat_check_count[rr.registers[0]] = 0                          # Reset the timeout count
                                        else:
                                           self.heartbeat_check_count[rr.registers[0]] += 1
                                            
                                        if self.heartbeat_check_count[rr.registers[0]] >= 60:                                                           
                                            self.mod_timeout_alert = True
                                            self.send_alert_email = True
                                        
                                        # Module email string
                                        self.ac_wind_status_string += \
                                            "<tr><td>AC Wind UID: </td><td>"                + str(log_data["ac_wind_" + uid + "_uid"]) + "</td></tr>" + \
                                            "<tr><td>AC Wind Enable State: </td><td>"       + str(log_data["ac_wind_" + uid + "_enable_state"]) + "</td></tr>" + \
                                            "<tr><td>Heartbeat: </td><td>"                  + str(log_data["ac_wind_" + uid + "_heartbeat"]) + "</td></tr>" + \
                                            "<tr><td>Warnings: </td><td>"                   + str(log_data["ac_wind_" + uid + "_warnings"]) + "</td></tr>" + \
                                            "<tr><td>Alarms: </td><td>"                     + str(log_data["ac_wind_" + uid + "_alarms"]) + "</td></tr>" + \
                                            "<tr><td>Faults: </td><td>"                     + str(log_data["ac_wind_" + uid + "_faults"]) + "</td></tr>" + \
                                            "<tr><td>Actions: </td><td>"                    + str(log_data["ac_wind_" + uid + "_actions"]) + "</td></tr>"
                                        
                            except Exception as e:
                                print("Logger: " + str(e))
                        
            except Exception as e:
                print("Logger: " + str(e))
               
            # AC Solar
            try:
                # How many installed AC Solar?
                rr = mbTCP_client.read_holding_registers(4250, 1, unit=1)
                
                if not rr.isError():
                    if len(rr.registers) >= 1:
                        log_data["ac_solar_quantity"] = rr.registers[0]
                        
                        # Populate the AC Solar data
                        for x in range(rr.registers[0]):
                            
                            uid = str(x+1)
                            offset = x * 25                                                 # There are up to 10 of each module type having 25 parameters
                            
                            try:
                                rr = mbTCP_client.read_holding_registers(4251 + offset, 25, unit=1)
                
                                if not rr.isError():
                                    if len(rr.registers) >= 25:
                                        
                                        ######################
                                        # Log Data           #
                                        ######################
                                        
                                        log_data["ac_solar_" + uid + "_uid"] = rr.registers[0]
                                        log_data["ac_solar_" + uid + "_enable_state"] = True if rr.registers[1] == 1 else False
                                        log_data["ac_solar_" + uid + "_heartbeat"] = rr.registers[2]
                                        log_data["ac_solar_" + uid + "_ac_solar_state"] = rr.registers[3]
                                        log_data["ac_solar_" + uid + "_control_input"] = rr.registers[5]
                                        log_data["ac_solar_" + uid + "_dc_bus_voltage"] = round(self.twos_comp_to_int(rr.registers[7]) * pow(10, SF1), 3)
                                        log_data["ac_solar_" + uid + "_dc_bus_current"] = round(self.twos_comp_to_int(rr.registers[8]) * pow(10, SF1), 3)
                                        log_data["ac_solar_" + uid + "_dc_bus_power"] = round(self.twos_comp_to_int(rr.registers[9]) * pow(10, SF1), 3)
                                        log_data["ac_solar_" + uid + "_temperature"] = round(self.twos_comp_to_int(rr.registers[10]) * pow(10, SF2), 3)
                                        log_data["ac_solar_" + uid + "_ac_frequency"] = round(self.twos_comp_to_int(rr.registers[11]) * pow(10, SF2), 3)
                                        log_data["ac_solar_" + uid + "_ac_total_current"] = round(self.twos_comp_to_int(rr.registers[12]) * pow(10, SF1), 3)
                                        log_data["ac_solar_" + uid + "_ac_line_voltage"] = round(self.twos_comp_to_int(rr.registers[13]) * pow(10, SF1), 3)
                                        log_data["ac_solar_" + uid + "_ac_power"] = round(self.twos_comp_to_int(rr.registers[14]) * pow(10, SF1), 3)
                                        log_data["ac_solar_" + uid + "_import_active_energy_hi"] = rr.registers[16]
                                        log_data["ac_solar_" + uid + "_import_active_energy_lo"] = rr.registers[17]
                                        log_data["ac_solar_" + uid + "_warnings"] = rr.registers[22]
                                        log_data["ac_solar_" + uid + "_alarms"] = rr.registers[23]
                                        log_data["ac_solar_" + uid + "_faults"] = rr.registers[24]
                                        log_data["ac_solar_" + uid + "_actions"] = 0    #rr.registers[25]  # Currently unavailable over SCADA
                                        
                                        ######################
                                        # Alerts             #
                                        ######################
                                        
                                        # Module Heartbeat Timeout
                                        if (rr.registers[2] - self.heartbeat_check[rr.registers[0]] & 0xFFFF) > 0:  # Heartbeat has changed since last rotation
                                           self.heartbeat_check[rr.registers[0]] = rr.registers[2]                  # Reset the Heartbeat reference
                                           self.heartbeat_check_count[rr.registers[0]] = 0                          # Reset the timeout count
                                        else:
                                           self.heartbeat_check_count[rr.registers[0]] += 1
                                            
                                        if self.heartbeat_check_count[rr.registers[0]] >= 60:                                                           
                                            self.mod_timeout_alert = True
                                            self.send_alert_email = True
                                        
                                        # Module email string
                                        self.ac_solar_status_string += \
                                            "<tr><td>AC Solar UID: </td><td>"               + str(log_data["ac_solar_" + uid + "_uid"]) + "</td></tr>" + \
                                            "<tr><td>AC Solar Enable State: </td><td>"      + str(log_data["ac_solar_" + uid + "_enable_state"]) + "</td></tr>" + \
                                            "<tr><td>Heartbeat: </td><td>"                  + str(log_data["ac_solar_" + uid + "_heartbeat"]) + "</td></tr>" + \
                                            "<tr><td>AC Solar State: </td><td>"             + str(log_data["ac_solar_" + uid + "_ac_solar_state"]) + "</td></tr>" + \
                                            "<tr><td>Control Input: </td><td>"              + str(log_data["ac_solar_" + uid + "_control_input"]) + "</td></tr>" + \
                                            "<tr><td>DC Bus Voltage: </td><td>"             + str(log_data["ac_solar_" + uid + "_dc_bus_voltage"]) + "</td></tr>" + \
                                            "<tr><td>DC Bus Current: </td><td>"             + str(log_data["ac_solar_" + uid + "_dc_bus_current"]) + "</td></tr>" + \
                                            "<tr><td>DC Bus Power: </td><td>"               + str(log_data["ac_solar_" + uid + "_dc_bus_power"]) + "</td></tr>" + \
                                            "<tr><td>Temperature: </td><td>"                + str(log_data["ac_solar_" + uid + "_temperature"]) + "</td></tr>" + \
                                            "<tr><td>Frequency: </td><td>"                  + str(log_data["ac_solar_" + uid + "_ac_frequency"]) + "</td></tr>" + \
                                            "<tr><td>AC Current: </td><td>"                 + str(log_data["ac_solar_" + uid + "_ac_total_current"]) + "</td></tr>" + \
                                            "<tr><td>AC Voltage: </td><td>"                 + str(log_data["ac_solar_" + uid + "_ac_line_voltage"]) + "</td></tr>" + \
                                            "<tr><td>AC Power: </td><td>"                   + str(log_data["ac_solar_" + uid + "_ac_power"]) + "</td></tr>" + \
                                            "<tr><td>Import Energy Hi: </td><td>"           + str(log_data["ac_solar_" + uid + "_import_active_energy_hi"]) + "</td></tr>" + \
                                            "<tr><td>Import Active Lo: </td><td>"           + str(log_data["ac_solar_" + uid + "_import_active_energy_lo"]) + "</td></tr>" + \
                                            "<tr><td>Warnings: </td><td>"                   + str(log_data["ac_solar_" + uid + "_warnings"]) + "</td></tr>" + \
                                            "<tr><td>Alarms: </td><td>"                     + str(log_data["ac_solar_" + uid + "_alarms"]) + "</td></tr>" + \
                                            "<tr><td>Faults: </td><td>"                     + str(log_data["ac_solar_" + uid + "_faults"]) + "</td></tr>" + \
                                            "<tr><td>Actions: </td><td>"                    + str(log_data["ac_solar_" + uid + "_actions"]) + "</td></tr>"
                                        
                            except Exception as e:
                                print("Logger: " + str(e))
                        
            except Exception as e:
                print("Logger: " + str(e))
               
            # DC Solar
            try:
                # How many installed DC Solar?
                rr = mbTCP_client.read_holding_registers(4500, 1, unit=1)
                
                if not rr.isError():
                    if len(rr.registers) >= 1:
                        log_data["dc_solar_quantity"] = rr.registers[0]
                        
                        # Populate the DC Solar data
                        for x in range(rr.registers[0]):
                            
                            uid = str(x+1)
                            offset = x * 25                                                 # There are up to 10 of each module type having 25 parameters
                            
                            try:
                                rr = mbTCP_client.read_holding_registers(4501 + offset, 25, unit=1)
                
                                if not rr.isError():
                                    if len(rr.registers) >= 25:
                                        
                                        ######################
                                        # Log Data           #
                                        ######################
                                        
                                        log_data["dc_solar_" + uid + "_uid"] = rr.registers[0]
                                        log_data["dc_solar_" + uid + "_enable_state"] = True if rr.registers[1] == 1 else False
                                        log_data["dc_solar_" + uid + "_heartbeat"] = rr.registers[2]
                                        log_data["dc_solar_" + uid + "_ac_solar_state"] = rr.registers[3]
                                        log_data["dc_solar_" + uid + "_control_input"] = rr.registers[5]
                                        log_data["dc_solar_" + uid + "_dc_bus_voltage"] = round(self.twos_comp_to_int(rr.registers[7]) * pow(10, SF1), 3)
                                        log_data["dc_solar_" + uid + "_dc_bus_current"] = round(self.twos_comp_to_int(rr.registers[8]) * pow(10, SF1), 3)
                                        log_data["dc_solar_" + uid + "_dc_bus_power"] = round(self.twos_comp_to_int(rr.registers[9]) * pow(10, SF1), 3)
                                        log_data["dc_solar_" + uid + "_temperature"] = round(self.twos_comp_to_int(rr.registers[10]) * pow(10, SF2), 3)
                                        log_data["dc_solar_" + uid + "_ac_frequency"] = round(self.twos_comp_to_int(rr.registers[11]) * pow(10, SF2), 3)
                                        log_data["dc_solar_" + uid + "_ac_total_current"] = round(self.twos_comp_to_int(rr.registers[12]) * pow(10, SF1), 3)
                                        log_data["dc_solar_" + uid + "_ac_line_voltage"] = round(self.twos_comp_to_int(rr.registers[13]) * pow(10, SF1), 3)
                                        log_data["dc_solar_" + uid + "_ac_power"] = round(self.twos_comp_to_int(rr.registers[14]) * pow(10, SF1), 3)
                                        log_data["dc_solar_" + uid + "_import_active_energy_hi"] = rr.registers[16]
                                        log_data["dc_solar_" + uid + "_import_active_energy_lo"] = rr.registers[17]
                                        log_data["dc_solar_" + uid + "_warnings"] = rr.registers[22]
                                        log_data["dc_solar_" + uid + "_alarms"] = rr.registers[23]
                                        log_data["dc_solar_" + uid + "_faults"] = rr.registers[24]
                                        log_data["dc_solar_" + uid + "_actions"] = 0    #rr.registers[25]  # Currently unavailable over SCADA
                                        
                                        ######################
                                        # Alerts             #
                                        ######################
                                        
                                        # Module Heartbeat Timeout
                                        if (rr.registers[2] - self.heartbeat_check[rr.registers[0]] & 0xFFFF) > 0:  # Heartbeat has changed since last rotation
                                           self.heartbeat_check[rr.registers[0]] = rr.registers[2]                  # Reset the Heartbeat reference
                                           self.heartbeat_check_count[rr.registers[0]] = 0                          # Reset the timeout count
                                        else:
                                           self.heartbeat_check_count[rr.registers[0]] += 1
                                            
                                        if self.heartbeat_check_count[rr.registers[0]] >= 60:                                                           
                                            self.mod_timeout_alert = True
                                            self.send_alert_email = True
                                        
                                        # Module email string
                                        self.dc_solar_status_string += \
                                            "<tr><td>DC Solar UID: </td><td>"               + str(log_data["dc_solar_" + uid + "_uid"]) + "</td></tr>" + \
                                            "<tr><td>DC Solar Enable State: </td><td>"      + str(log_data["dc_solar_" + uid + "_enable_state"]) + "</td></tr>" + \
                                            "<tr><td>Heartbeat: </td><td>"                  + str(log_data["dc_solar_" + uid + "_heartbeat"]) + "</td></tr>" + \
                                            "<tr><td>DC Solar State: </td><td>"             + str(log_data["dc_solar_" + uid + "_ac_solar_state"]) + "</td></tr>" + \
                                            "<tr><td>Control Input: </td><td>"              + str(log_data["dc_solar_" + uid + "_control_input"]) + "</td></tr>" + \
                                            "<tr><td>DC Bus Voltage: </td><td>"             + str(log_data["dc_solar_" + uid + "_dc_bus_voltage"]) + "</td></tr>" + \
                                            "<tr><td>DC Bus Current: </td><td>"             + str(log_data["dc_solar_" + uid + "_dc_bus_current"]) + "</td></tr>" + \
                                            "<tr><td>DC Bus Power: </td><td>"               + str(log_data["dc_solar_" + uid + "_dc_bus_power"]) + "</td></tr>" + \
                                            "<tr><td>Temperature: </td><td>"                + str(log_data["dc_solar_" + uid + "_temperature"]) + "</td></tr>" + \
                                            "<tr><td>Frequency: </td><td>"                  + str(log_data["dc_solar_" + uid + "_ac_frequency"]) + "</td></tr>" + \
                                            "<tr><td>AC Current: </td><td>"                 + str(log_data["dc_solar_" + uid + "_ac_total_current"]) + "</td></tr>" + \
                                            "<tr><td>AC Voltage: </td><td>"                 + str(log_data["dc_solar_" + uid + "_ac_line_voltage"]) + "</td></tr>" + \
                                            "<tr><td>AC Power: </td><td>"                   + str(log_data["dc_solar_" + uid + "_ac_power"]) + "</td></tr>" + \
                                            "<tr><td>Import Energy Hi: </td><td>"           + str(log_data["dc_solar_" + uid + "_import_active_energy_hi"]) + "</td></tr>" + \
                                            "<tr><td>Import Energy Lo: </td><td>"           + str(log_data["dc_solar_" + uid + "_import_active_energy_lo"]) + "</td></tr>" + \
                                            "<tr><td>Warnings: </td><td>"                   + str(log_data["dc_solar_" + uid + "_warnings"]) + "</td></tr>" + \
                                            "<tr><td>Alarms: </td><td>"                     + str(log_data["dc_solar_" + uid + "_alarms"]) + "</td></tr>" + \
                                            "<tr><td>Faults: </td><td>"                     + str(log_data["dc_solar_" + uid + "_faults"]) + "</td></tr>" + \
                                            "<tr><td>Actions: </td><td>"                    + str(log_data["dc_solar_" + uid + "_actions"]) + "</td></tr>"
                                        
                            except Exception as e:
                                print("Logger: " + str(e))
                        
            except Exception as e:
                print("Logger: " + str(e))
           
            # AC EFM
            try:
                # How many installed AC EFM?
                rr = mbTCP_client.read_holding_registers(4750, 1, unit=1)
                
                if not rr.isError():
                    if len(rr.registers) >= 1:
                        log_data["ac_efm_quantity"] = rr.registers[0]
                        
                        # Populate the AC EFM data
                        for x in range(rr.registers[0]):
                            
                            uid = str(x+1)
                            offset = x * 25                                                 # There are up to 10 of each module type having 25 parameters
                            
                            try:
                                rr = mbTCP_client.read_holding_registers(4751 + offset, 25, unit=1)
                
                                if not rr.isError():
                                    if len(rr.registers) >= 25:
                                        
                                        ######################
                                        # Log Data           #
                                        ######################
                                        
                                        log_data["ac_efm_" + uid + "_uid"] = rr.registers[0]
                                        log_data["ac_efm_" + uid + "_enable_state"] = True if rr.registers[1] == 1 else False
                                        log_data["ac_efm_" + uid + "_heartbeat"] = rr.registers[2]
                                        log_data["ac_efm_" + uid + "_ac_efm_state"] = rr.registers[3]
                                        log_data["ac_efm_" + uid + "_insulation_alarm_1"] = round(self.twos_comp_to_int(rr.registers[5]) * pow(10, SF1), 3)
                                        log_data["ac_efm_" + uid + "_insulation_alarm_2"] = round(self.twos_comp_to_int(rr.registers[6]) * pow(10, SF1), 3)
                                        log_data["ac_efm_" + uid + "_prewarning_value"] = round(self.twos_comp_to_int(rr.registers[8]) * pow(10, SF1), 3)
                                        log_data["ac_efm_" + uid + "_alarm_value"] = round(self.twos_comp_to_int(rr.registers[9]) * pow(10, SF1), 3)
                                        log_data["ac_efm_" + uid + "_capacitance"] = round(self.twos_comp_to_int(rr.registers[11]) * pow(10, SF1), 3)
                                        log_data["ac_efm_" + uid + "_warnings"] = rr.registers[22]
                                        log_data["ac_efm_" + uid + "_alarms"] = rr.registers[23]
                                        log_data["ac_efm_" + uid + "_faults"] = rr.registers[24]
                                        log_data["ac_efm_" + uid + "_actions"] = 0    #rr.registers[25]  # Currently unavailable over SCADA
                                        
                                        ######################
                                        # Alerts             #
                                        ######################
                                        
                                        # Module Heartbeat Timeout
                                        if (rr.registers[2] - self.heartbeat_check[rr.registers[0]] & 0xFFFF) > 0:  # Heartbeat has changed since last rotation
                                           self.heartbeat_check[rr.registers[0]] = rr.registers[2]                  # Reset the Heartbeat reference
                                           self.heartbeat_check_count[rr.registers[0]] = 0                          # Reset the timeout count
                                        else:
                                           self.heartbeat_check_count[rr.registers[0]] += 1
                                            
                                        if self.heartbeat_check_count[rr.registers[0]] >= 60:                                                           
                                            self.mod_timeout_alert = True
                                            self.send_alert_email = True
                                        
                                        # Module email string
                                        self.ac_efm_status_string += \
                                            "<tr><td>AC EFM UID: </td><td>"                 + str(log_data["ac_efm_" + uid + "_uid"]) + "</td></tr>" + \
                                            "<tr><td>AC EFM Enable State: </td><td>"        + str(log_data["ac_efm_" + uid + "_enable_state"]) + "</td></tr>" + \
                                            "<tr><td>Heartbeat: </td><td>"                  + str(log_data["ac_efm_" + uid + "_heartbeat"]) + "</td></tr>" + \
                                            "<tr><td>AC EFM State: </td><td>"               + str(log_data["ac_efm_" + uid + "_ac_efm_state"]) + "</td></tr>" + \
                                            "<tr><td>Insulation Alarm 1: </td><td>"         + str(log_data["ac_efm_" + uid + "_insulation_alarm_1"]) + "</td></tr>" + \
                                            "<tr><td>Insulation Alarm 2: </td><td>"         + str(log_data["ac_efm_" + uid + "_insulation_alarm_2"]) + "</td></tr>" + \
                                            "<tr><td>Prewarning Alarm: </td><td>"           + str(log_data["ac_efm_" + uid + "_prewarning_value"]) + "</td></tr>" + \
                                            "<tr><td>Alarm Value: </td><td>"                + str(log_data["ac_efm_" + uid + "_alarm_value"]) + "</td></tr>" + \
                                            "<tr><td>Capacitance: </td><td>"                + str(log_data["ac_efm_" + uid + "_capacitance"]) + "</td></tr>" + \
                                            "<tr><td>Warnings: </td><td>"                   + str(log_data["ac_efm_" + uid + "_warnings"]) + "</td></tr>" + \
                                            "<tr><td>Alarms: </td><td>"                     + str(log_data["ac_efm_" + uid + "_alarms"]) + "</td></tr>" + \
                                            "<tr><td>Faults: </td><td>"                     + str(log_data["ac_efm_" + uid + "_faults"]) + "</td></tr>" + \
                                            "<tr><td>Actions: </td><td>"                    + str(log_data["ac_efm_" + uid + "_actions"]) + "</td></tr>"
                                        
                            except Exception as e:
                                print("Logger: " + str(e))
                        
            except Exception as e:
                print("Logger: " + str(e))
                
            # DC EFM
            try:
                # How many installed DC EFM?
                rr = mbTCP_client.read_holding_registers(5000, 1, unit=1)
                
                if not rr.isError():
                    if len(rr.registers) >= 1:
                        log_data["dc_efm_quantity"] = rr.registers[0]
                        
                        # Populate the DC EFM data
                        for x in range(rr.registers[0]):
                            
                            uid = str(x+1)
                            offset = x * 25                                                 # There are up to 10 of each module type having 25 parameters
                            
                            try:
                                rr = mbTCP_client.read_holding_registers(5001 + offset, 25, unit=1)
                
                                if not rr.isError():
                                    if len(rr.registers) >= 25:
                                        
                                        ######################
                                        # Log Data           #
                                        ######################
                                        
                                        log_data["dc_efm_" + uid + "_uid"] = rr.registers[0]
                                        log_data["dc_efm_" + uid + "_enable_state"] = True if rr.registers[1] == 1 else False
                                        log_data["dc_efm_" + uid + "_heartbeat"] = rr.registers[2]
                                        log_data["dc_efm_" + uid + "_dc_efm_state"] = rr.registers[3]
                                        log_data["dc_efm_" + uid + "_insulation_alarm_1"] = round(self.twos_comp_to_int(rr.registers[5]) * pow(10, SF1), 3)
                                        log_data["dc_efm_" + uid + "_insulation_alarm_2"] = round(self.twos_comp_to_int(rr.registers[6]) * pow(10, SF1), 3)
                                        log_data["dc_efm_" + uid + "_prewarning_value"] = round(self.twos_comp_to_int(rr.registers[8]) * pow(10, SF1), 3)
                                        log_data["dc_efm_" + uid + "_alarm_value"] = round(self.twos_comp_to_int(rr.registers[9]) * pow(10, SF1), 3)
                                        log_data["dc_efm_" + uid + "_capacitance"] = round(self.twos_comp_to_int(rr.registers[11]) * pow(10, SF1), 3)
                                        log_data["dc_efm_" + uid + "_warnings"] = rr.registers[22]
                                        log_data["dc_efm_" + uid + "_alarms"] = rr.registers[23]
                                        log_data["dc_efm_" + uid + "_faults"] = rr.registers[24]
                                        log_data["dc_efm_" + uid + "_actions"] = 0    #rr.registers[25]  # Currently unavailable over SCADA
                                        
                                        ######################
                                        # Alerts             #
                                        ######################
                                        
                                        # Module Heartbeat Timeout
                                        if (rr.registers[2] - self.heartbeat_check[rr.registers[0]] & 0xFFFF) > 0:  # Heartbeat has changed since last rotation
                                           self.heartbeat_check[rr.registers[0]] = rr.registers[2]                  # Reset the Heartbeat reference
                                           self.heartbeat_check_count[rr.registers[0]] = 0                          # Reset the timeout count
                                        else:
                                           self.heartbeat_check_count[rr.registers[0]] += 1
                                            
                                        if self.heartbeat_check_count[rr.registers[0]] >= 60:                                                           
                                            self.mod_timeout_alert = True
                                            self.send_alert_email = True
                                        
                                        # Module email string
                                        self.dc_efm_status_string += \
                                            "<tr><td>DC EFM UID: </td><td>"                 + str(log_data["dc_efm_" + uid + "_uid"]) + "</td></tr>" + \
                                            "<tr><td>DC EFM Enable State: </td><td>"        + str(log_data["dc_efm_" + uid + "_enable_state"]) + "</td></tr>" + \
                                            "<tr><td>Heartbeat: </td><td>"                  + str(log_data["dc_efm_" + uid + "_heartbeat"]) + "</td></tr>" + \
                                            "<tr><td>DC EFM State: </td><td>"               + str(log_data["dc_efm_" + uid + "_dc_efm_state"]) + "</td></tr>" + \
                                            "<tr><td>Insulation Alarm 1: </td><td>"         + str(log_data["dc_efm_" + uid + "_insulation_alarm_1"]) + "</td></tr>" + \
                                            "<tr><td>Insulation Alarm 2: </td><td>"         + str(log_data["dc_efm_" + uid + "_insulation_alarm_2"]) + "</td></tr>" + \
                                            "<tr><td>Prewarning Value: </td><td>"           + str(log_data["dc_efm_" + uid + "_prewarning_value"]) + "</td></tr>" + \
                                            "<tr><td>Alarm Value: </td><td>"                + str(log_data["dc_efm_" + uid + "_alarm_value"]) + "</td></tr>" + \
                                            "<tr><td>Capacitance: </td><td>"                + str(log_data["dc_efm_" + uid + "_capacitance"]) + "</td></tr>" + \
                                            "<tr><td>Warnings: </td><td>"                   + str(log_data["dc_efm_" + uid + "_warnings"]) + "</td></tr>" + \
                                            "<tr><td>Alarms: </td><td>"                     + str(log_data["dc_efm_" + uid + "_alarms"]) + "</td></tr>" + \
                                            "<tr><td>Faults: </td><td>"                     + str(log_data["dc_efm_" + uid + "_faults"]) + "</td></tr>" + \
                                            "<tr><td>Actions: </td><td>"                    + str(log_data["dc_efm_" + uid + "_actions"]) + "</td></tr>"
                                        
                            except Exception as e:
                                print("Logger: " + str(e))
                        
            except Exception as e:
                print("Logger: " + str(e))
            
            # EV Chargers
            try:
                # How many installed EV Chargers?
                rr = mbTCP_client.read_holding_registers(5250, 1, unit=1)
                
                if not rr.isError():
                    if len(rr.registers) >= 1:
                        log_data["ev_charge_quantity"] = rr.registers[0]
                        
                        # Populate the EV Charger data
                        for x in range(rr.registers[0]):
                            
                            uid = str(x+1)
                            offset = x * 25                                                 # There are up to 10 of each module type having 25 parameters
                            
                            try:
                                rr = mbTCP_client.read_holding_registers(5251 + offset, 25, unit=1)
                
                                if not rr.isError():
                                    if len(rr.registers) >= 25:
                                        
                                        # Incomplete: Builds the VID of the vehicle, if available
                                        vehicle_vid = ""
                                        # for char in range(3):
                                            # We have to pull apart characters and form the 16-char string name.
                                            # TODO: located in registers 11,12,13
                                            # vehicle_vid += str((self.inputs[module][dev][2][11 + char] >> 8) & 0xFF)
                                            # vehicle_vid += ':'
                                            # vehicle_vid += str(self.inputs[module][dev][2][11 + char] & 0xFF)
                                            # if char < 2:
                                            #     vehicle_vid += ':'
                                        
                                        ######################
                                        # Log Data           #
                                        ######################
                                        
                                        log_data["ev_charge_" + uid + "_uid"] = rr.registers[0]
                                        log_data["ev_charge_" + uid + "_enable_state"] = True if rr.registers[1] == 1 else False
                                        log_data["ev_charge_" + uid + "_heartbeat"] = rr.registers[2]
                                        log_data["ev_charge_" + uid + "_ev_charge_state"] = rr.registers[3]
                                        log_data["ev_charge_" + uid + "_charger_session_active"] = rr.registers[5]
                                        log_data["ev_charge_" + uid + "_charger_serial_number"] = rr.registers[6]
                                        log_data["ev_charge_" + uid + "_charger_max_power_budget"] = round(self.twos_comp_to_int(rr.registers[7]) * pow(10, SF1), 3)
                                        log_data["ev_charge_" + uid + "_charger_state"] = rr.registers[8]
                                        log_data["ev_charge_" + uid + "_charger_type"] = rr.registers[9]
                                        log_data["ev_charge_" + uid + "_charger_duration"] = rr.registers[10]
                                        log_data["ev_charge_" + uid + "_vehicle_vid"] = vehicle_vid
                                        log_data["ev_charge_" + uid + "_vehicle_soc"] = round(self.twos_comp_to_int(rr.registers[16]) * pow(10, SF1), 3)
                                        log_data["ev_charge_" + uid + "_vehicle_dc_voltage"] = round(self.twos_comp_to_int(rr.registers[17]) * pow(10, SF1), 3)
                                        log_data["ev_charge_" + uid + "_vehicle_dc_current"] = round(self.twos_comp_to_int(rr.registers[18]) * pow(10, SF1), 3)
                                        log_data["ev_charge_" + uid + "_vehicle_dc_power"] = round(self.twos_comp_to_int(rr.registers[19]) * pow(10, SF1), 3)
                                        log_data["ev_charge_" + uid + "_warnings"] = rr.registers[22]
                                        log_data["ev_charge_" + uid + "_alarms"] = rr.registers[23]
                                        log_data["ev_charge_" + uid + "_faults"] = rr.registers[24]
                                        log_data["ev_charge_" + uid + "_actions"] = 0    #rr.registers[25]  # Currently unavailable over SCADA
                                        
                                        ######################
                                        # Alerts             #
                                        ######################
                                        
                                        # Module Heartbeat Timeout
                                        if (rr.registers[2] - self.heartbeat_check[rr.registers[0]] & 0xFFFF) > 0:  # Heartbeat has changed since last rotation
                                           self.heartbeat_check[rr.registers[0]] = rr.registers[2]                  # Reset the Heartbeat reference
                                           self.heartbeat_check_count[rr.registers[0]] = 0                          # Reset the timeout count
                                        else:
                                           self.heartbeat_check_count[rr.registers[0]] += 1
                                            
                                        if self.heartbeat_check_count[rr.registers[0]] >= 60:                                                           
                                            self.mod_timeout_alert = True
                                            self.send_alert_email = True
                                        
                                        # Module email string
                                        self.ev_charge_status_string += \
                                            "<tr><td>EV Charge UID: </td><td>"              + str(log_data["ev_charge_" + uid + "_uid"]) + "</td></tr>" + \
                                            "<tr><td>EV Charge Enable State: </td><td>"     + str(log_data["ev_charge_" + uid + "_enable_state"]) + "</td></tr>" + \
                                            "<tr><td>Heartbeat: </td><td>"                  + str(log_data["ev_charge_" + uid + "_heartbeat"]) + "</td></tr>" + \
                                            "<tr><td>EV Charge State: </td><td>"            + str(log_data["ev_charge_" + uid + "_ev_charge_state"]) + "</td></tr>" + \
                                            "<tr><td>Session Active: </td><td>"             + str(log_data["ev_charge_" + uid + "_charger_session_active"]) + "</td></tr>" + \
                                            "<tr><td>Serial Number: </td><td>"              + str(log_data["ev_charge_" + uid + "_charger_serial_number"]) + "</td></tr>" + \
                                            "<tr><td>Max Power Budget: </td><td>"           + str(log_data["ev_charge_" + uid + "_charger_max_power_budget"]) + "</td></tr>" + \
                                            "<tr><td>State: </td><td>"                      + str(log_data["ev_charge_" + uid + "_charger_state"]) + "</td></tr>" + \
                                            "<tr><td>Type: </td><td>"                       + str(log_data["ev_charge_" + uid + "_charger_type"]) + "</td></tr>" + \
                                            "<tr><td>Duration: </td><td>"                   + str(log_data["ev_charge_" + uid + "_charger_duration"]) + "</td></tr>" + \
                                            "<tr><td>VID: </td><td>"                        + str(log_data["ev_charge_" + uid + "_vehicle_vid"]) + "</td></tr>" + \
                                            "<tr><td>SOC: </td><td>"                        + str(log_data["ev_charge_" + uid + "_vehicle_soc"]) + "</td></tr>" + \
                                            "<tr><td>DC Voltage: </td><td>"                 + str(log_data["ev_charge_" + uid + "_vehicle_dc_voltage"]) + "</td></tr>" + \
                                            "<tr><td>DC Current: </td><td>"                 + str(log_data["ev_charge_" + uid + "_vehicle_dc_current"]) + "</td></tr>" + \
                                            "<tr><td>DC Power: </td><td>"                   + str(log_data["ev_charge_" + uid + "_vehicle_dc_power"]) + "</td></tr>" + \
                                            "<tr><td>Warnings: </td><td>"                   + str(log_data["ev_charge_" + uid + "_warnings"]) + "</td></tr>" + \
                                            "<tr><td>Alarms: </td><td>"                     + str(log_data["ev_charge_" + uid + "_alarms"]) + "</td></tr>" + \
                                            "<tr><td>Faults: </td><td>"                     + str(log_data["ev_charge_" + uid + "_faults"]) + "</td></tr>" + \
                                            "<tr><td>Actions: </td><td>"                    + str(log_data["ev_charge_" + uid + "_actions"]) + "</td></tr>"
                                        
                            except Exception as e:
                                print("Logger: " + str(e))
            
            except Exception as e:
                print("Logger: " + str(e))
                
            # SCADA
            try:
                # How many installed SCADA?
                rr = mbTCP_client.read_holding_registers(5500, 1, unit=1)
                
                if not rr.isError():
                    if len(rr.registers) >= 1:
                        log_data["scada_quantity"] = rr.registers[0]
                        
                        # Populate the SCADA Charger data
                        for x in range(rr.registers[0]):
                            
                            uid = str(x+1)
                            offset = x * 25                                                 # There are up to 10 of each module type having 25 parameters
                            
                            try:
                                rr = mbTCP_client.read_holding_registers(5501 + offset, 25, unit=1)
                
                                if not rr.isError():
                                    if len(rr.registers) >= 25:
                                        
                                        ######################
                                        # Log Data           #
                                        ######################
                                        
                                        log_data["scada_" + uid + "_uid"] = rr.registers[0]
                                        log_data["scada_" + uid + "_enable_state"] = True if rr.registers[1] == 1 else False
                                        log_data["scada_" + uid + "_heartbeat"] = rr.registers[2]
                                        log_data["scada_" + uid + "_scada_state"] = rr.registers[3]
                                        log_data["scada_" + uid + "_scada_type"] = rr.registers[4]
                                        log_data["scada_" + uid + "_warnings"] = rr.registers[22]
                                        log_data["scada_" + uid + "_alarms"] = rr.registers[23]
                                        log_data["scada_" + uid + "_faults"] = rr.registers[24]
                                        log_data["scada_" + uid + "_actions"] = 0    #rr.registers[25]  # Currently unavailable over SCADA
                                        
                                        ######################
                                        # Alerts             #
                                        ######################
                                        
                                        # Module Heartbeat Timeout
                                        if (rr.registers[2] - self.heartbeat_check[rr.registers[0]] & 0xFFFF) > 0:  # Heartbeat has changed since last rotation
                                           self.heartbeat_check[rr.registers[0]] = rr.registers[2]                  # Reset the Heartbeat reference
                                           self.heartbeat_check_count[rr.registers[0]] = 0                          # Reset the timeout count
                                        else:
                                           self.heartbeat_check_count[rr.registers[0]] += 1
                                            
                                        if self.heartbeat_check_count[rr.registers[0]] >= 60:                                                           
                                            self.mod_timeout_alert = True
                                            self.send_alert_email = True
                                        
                                        # Module email string
                                        self.scada_status_string += \
                                            "<tr><td>SCADA UID: </td><td>"                  + str(log_data["scada_" + uid + "_uid"]) + "</td></tr>" + \
                                            "<tr><td>SCADA Enable State: </td><td>"         + str(log_data["scada_" + uid + "_enable_state"]) + "</td></tr>" + \
                                            "<tr><td>Heartbeat: </td><td>"                  + str(log_data["scada_" + uid + "_heartbeat"]) + "</td></tr>" + \
                                            "<tr><td>SCADA State: </td><td>"                + str(log_data["scada_" + uid + "_scada_state"]) + "</td></tr>" + \
                                            "<tr><td>SCADA Type: </td><td>"                 + str(log_data["scada_" + uid + "_scada_type"]) + "</td></tr>" + \
                                            "<tr><td>Warnings: </td><td>"                   + str(log_data["scada_" + uid + "_warnings"]) + "</td></tr>" + \
                                            "<tr><td>Alarms: </td><td>"                     + str(log_data["scada_" + uid + "_alarms"]) + "</td></tr>" + \
                                            "<tr><td>Faults: </td><td>"                     + str(log_data["scada_" + uid + "_faults"]) + "</td></tr>" + \
                                            "<tr><td>Actions: </td><td>"                    + str(log_data["scada_" + uid + "_actions"]) + "</td></tr>"
                                        
                            except Exception as e:
                                print("Logger: " + str(e))
            
            except Exception as e:
                print("Logger: " + str(e))
            
            # Logging
            try:
                # How many installed Logging?
                rr = mbTCP_client.read_holding_registers(5750, 1, unit=1)
                
                if not rr.isError():
                    if len(rr.registers) >= 1:
                        log_data["logging_quantity"] = rr.registers[0]
                        
                        # Populate the Logging data
                        for x in range(rr.registers[0]):
                            
                            uid = str(x+1)
                            offset = x * 25                                                 # There are up to 10 of each module type having 25 parameters
                            
                            try:
                                rr = mbTCP_client.read_holding_registers(5751 + offset, 25, unit=1)
                
                                if not rr.isError():
                                    if len(rr.registers) >= 25:
                                        
                                        ######################
                                        # Log Data           #
                                        ######################
                                        
                                        log_data["logging_" + uid + "_uid"] = rr.registers[0]
                                        log_data["logging_" + uid + "_enable_state"] = True if rr.registers[1] == 1 else False
                                        log_data["logging_" + uid + "_heartbeat"] = rr.registers[2]
                                        log_data["logging_" + uid + "_logging_state"] = rr.registers[3]
                                        log_data["logging_" + uid + "_local_log_interval"] = rr.registers[5]
                                        log_data["logging_" + uid + "_local_log_duration"] = rr.registers[6]
                                        log_data["logging_" + uid + "_local_log_trigger"] = rr.registers[7]
                                        log_data["logging_" + uid + "_local_log_buffer"] = rr.registers[10]
                                        log_data["logging_" + uid + "_local_log_file_count"] = rr.registers[11]
                                        log_data["logging_" + uid + "_local_log_file_limit"] = rr.registers[12]
                                        log_data["logging_" + uid + "_remote_logging_state"] = rr.registers[13]
                                        log_data["logging_" + uid + "_remote_log_interval"] = rr.registers[15]
                                        log_data["logging_" + uid + "_remote_log_length"] = rr.registers[16]
                                        log_data["logging_" + uid + "_remote_log_trigger"] = rr.registers[17]
                                        log_data["logging_" + uid + "_remote_log_buffer"] = rr.registers[20]
                                        log_data["logging_" + uid + "_warnings"] = rr.registers[22]
                                        log_data["logging_" + uid + "_alarms"] = rr.registers[23]
                                        log_data["logging_" + uid + "_faults"] = rr.registers[24]
                                        log_data["logging_" + uid + "_actions"] = 0    #rr.registers[25]  # Currently unavailable over SCADA
                                        
                                        ######################
                                        # Alerts             #
                                        ######################
                                        
                                        # Module Heartbeat Timeout
                                        if (rr.registers[2] - self.heartbeat_check[rr.registers[0]] & 0xFFFF) > 0:  # Heartbeat has changed since last rotation
                                           self.heartbeat_check[rr.registers[0]] = rr.registers[2]                  # Reset the Heartbeat reference
                                           self.heartbeat_check_count[rr.registers[0]] = 0                          # Reset the timeout count
                                        else:
                                           self.heartbeat_check_count[rr.registers[0]] += 1
                                            
                                        if self.heartbeat_check_count[rr.registers[0]] >= 60:                                                           
                                            self.mod_timeout_alert = True
                                            self.send_alert_email = True
                                        
                                        # Module email string
                                        self.logging_status_string += \
                                            "<tr><td>Logging UID: </td><td>"                + str(log_data["logging_" + uid + "_uid"]) + "</td></tr>" + \
                                            "<tr><td>Logging Enable State: </td><td>"       + str(log_data["logging_" + uid + "_enable_state"]) + "</td></tr>" + \
                                            "<tr><td>Heartbeat: </td><td>"                  + str(log_data["logging_" + uid + "_heartbeat"]) + "</td></tr>" + \
                                            "<tr><td>Logging State: </td><td>"              + str(log_data["logging_" + uid + "_logging_state"]) + "</td></tr>" + \
                                            "<tr><td>Local Interval: </td><td>"             + str(log_data["logging_" + uid + "_local_log_interval"]) + "</td></tr>" + \
                                            "<tr><td>Local Duration: </td><td>"             + str(log_data["logging_" + uid + "_local_log_duration"]) + "</td></tr>" + \
                                            "<tr><td>Local Trigger: </td><td>"              + str(log_data["logging_" + uid + "_local_log_trigger"]) + "</td></tr>" + \
                                            "<tr><td>Local Buffer: </td><td>"               + str(log_data["logging_" + uid + "_local_log_buffer"]) + "</td></tr>" + \
                                            "<tr><td>Local File Count: </td><td>"           + str(log_data["logging_" + uid + "_local_log_file_count"]) + "</td></tr>" + \
                                            "<tr><td>Local File Limit: </td><td>"           + str(log_data["logging_" + uid + "_local_log_file_limit"]) + "</td></tr>" + \
                                            "<tr><td>Remote State: </td><td>"               + str(log_data["logging_" + uid + "_remote_logging_state"]) + "</td></tr>" + \
                                            "<tr><td>Remote Interval: </td><td>"            + str(log_data["logging_" + uid + "_remote_log_interval"]) + "</td></tr>" + \
                                            "<tr><td>Remote Length: </td><td>"              + str(log_data["logging_" + uid + "_remote_log_length"]) + "</td></tr>" + \
                                            "<tr><td>Remote Trigger: </td><td>"             + str(log_data["logging_" + uid + "_remote_log_trigger"]) + "</td></tr>" + \
                                            "<tr><td>Remote Buffer: </td><td>"              + str(log_data["logging_" + uid + "_remote_log_buffer"]) + "</td></tr>" + \
                                            "<tr><td>Warnings: </td><td>"                   + str(log_data["logging_" + uid + "_warnings"]) + "</td></tr>" + \
                                            "<tr><td>Alarms: </td><td>"                     + str(log_data["logging_" + uid + "_alarms"]) + "</td></tr>" + \
                                            "<tr><td>Faults: </td><td>"                     + str(log_data["logging_" + uid + "_faults"]) + "</td></tr>" + \
                                            "<tr><td>Actions: </td><td>"                    + str(log_data["logging_" + uid + "_actions"]) + "</td></tr>"
                                        
                            except Exception as e:
                                print("Logger: " + str(e))
            
            except Exception as e:
                print("Logger: " + str(e))
            
            # Client
            try:
                # How many installed Clients?
                rr = mbTCP_client.read_holding_registers(6000, 1, unit=1)
                
                if not rr.isError():
                    if len(rr.registers) >= 1:
                        log_data["client_quantity"] = rr.registers[0]
                        
                        # Reset the alert string(s)
                        if log_data["client_quantity"] >= 1:
                            self.client_status_string = "<tr align=\"left\"><th>CLIENT</th></tr>"
                        else:
                            self.client_status_string = "<tr align=\"left\"><th>CLIENT</th></tr>" + "<tr><td>No device installed" + "</td></tr>"
                        
                        # Populate the Client data
                        for x in range(rr.registers[0]):
                            
                            uid = str(x+1)
                            offset = x * 25                                                 # There are up to 10 of each module type having 25 parameters
                            
                            try:
                                rr = mbTCP_client.read_holding_registers(6001 + offset, 25, unit=1)
                
                                if not rr.isError():
                                    if len(rr.registers) >= 25:
                                        
                                        ######################
                                        # Log Data           #
                                        ######################
                                        
                                        log_data["client_" + uid + "_uid"] = rr.registers[0]
                                        log_data["client_" + uid + "_enable_state"] = True if rr.registers[1] == 1 else False
                                        log_data["client_" + uid + "_heartbeat"] = rr.registers[2]                                       
                                        log_data["client_" + uid + "_warnings"] = rr.registers[22]
                                        log_data["client_" + uid + "_alarms"] = rr.registers[23]
                                        log_data["client_" + uid + "_faults"] = rr.registers[24]
                                        log_data["client_" + uid + "_actions"] = 0    #rr.registers[25]  # Currently unavailable over SCADA
                                        
                                        ######################
                                        # Alerts             #
                                        ######################
                                        
                                        # Module Heartbeat Timeout
                                        if (rr.registers[2] - self.heartbeat_check[rr.registers[0]] & 0xFFFF) > 0:  # Heartbeat has changed since last rotation
                                           self.heartbeat_check[rr.registers[0]] = rr.registers[2]                  # Reset the Heartbeat reference
                                           self.heartbeat_check_count[rr.registers[0]] = 0                          # Reset the timeout count
                                        else:
                                           self.heartbeat_check_count[rr.registers[0]] += 1
                                            
                                        if self.heartbeat_check_count[rr.registers[0]] >= 60:                                                           
                                            self.mod_timeout_alert = True
                                            self.send_alert_email = True
                                        
                                        # Module email string
                                        self.client_status_string += \
                                            "<tr><td>Client UID: </td><td>"                 + str(log_data["client_" + uid + "_uid"]) + "</td></tr>" + \
                                            "<tr><td>Client Enable State: </td><td>"        + str(log_data["client_" + uid + "_enable_state"]) + "</td></tr>" + \
                                            "<tr><td>Heartbeat: </td><td>"                  + str(log_data["client_" + uid + "_heartbeat"]) + "</td></tr>" + \
                                            "<tr><td>Warnings: </td><td>"                   + str(log_data["client_" + uid + "_warnings"]) + "</td></tr>" + \
                                            "<tr><td>Alarms: </td><td>"                     + str(log_data["client_" + uid + "_alarms"]) + "</td></tr>" + \
                                            "<tr><td>Faults: </td><td>"                     + str(log_data["client_" + uid + "_faults"]) + "</td></tr>" + \
                                            "<tr><td>Actions: </td><td>"                    + str(log_data["client_" + uid + "_actions"]) + "</td></tr>"
                                        
                            except Exception as e:
                                print("Logger: " + str(e))
                        
            except Exception as e:
                print("Logger: " + str(e))
                
            # Undefined
            try:
                # How many installed Undefined Modules?
                rr = mbTCP_client.read_holding_registers(6250, 1, unit=1)
                
                if not rr.isError():
                    if len(rr.registers) >= 1:
                        log_data["undefined_quantity"] = rr.registers[0]
                        
                        # Reset the alert string(s)
                        if log_data["undefined_quantity"] >= 1:
                            self.undefined_status_string = "<tr align=\"left\"><th>UNDEFINED</th></tr>"
                        else:
                            self.undefined_status_string = "<tr align=\"left\"><th>UNDEFINED</th></tr>" + "<tr><td>No device installed" + "</td></tr>"
                        
                        # Populate the Undefined data
                        for x in range(rr.registers[0]):
                            
                            uid = str(x+1)
                            offset = x * 25                                                 # There are up to 10 of each module type having 25 parameters
                            
                            try:
                                rr = mbTCP_client.read_holding_registers(6251 + offset, 25, unit=1)
                
                                if not rr.isError():
                                    if len(rr.registers) >= 25:
                                        
                                        ######################
                                        # Log Data           #
                                        ######################
                                        
                                        log_data["undefined_" + uid + "_uid"] = rr.registers[0]
                                        log_data["undefined_" + uid + "_enable_state"] = True if rr.registers[1] == 1 else False
                                        log_data["undefined_" + uid + "_heartbeat"] = rr.registers[2]                                       
                                        log_data["undefined_" + uid + "_warnings"] = rr.registers[22]
                                        log_data["undefined_" + uid + "_alarms"] = rr.registers[23]
                                        log_data["undefined_" + uid + "_faults"] = rr.registers[24]
                                        log_data["undefined_" + uid + "_actions"] = 0    #rr.registers[25]  # Currently unavailable over SCADA
                                        
                                        ######################
                                        # Alerts             #
                                        ######################
                                        
                                        # Module Heartbeat Timeout
                                        if (rr.registers[2] - self.heartbeat_check[rr.registers[0]] & 0xFFFF) > 0:  # Heartbeat has changed since last rotation
                                           self.heartbeat_check[rr.registers[0]] = rr.registers[2]                  # Reset the Heartbeat reference
                                           self.heartbeat_check_count[rr.registers[0]] = 0                          # Reset the timeout count
                                        else:
                                           self.heartbeat_check_count[rr.registers[0]] += 1
                                            
                                        if self.heartbeat_check_count[rr.registers[0]] >= 60:                                                           
                                            self.mod_timeout_alert = True
                                            self.send_alert_email = True
                                        
                                        # Module email string
                                        self.undefined_status_string += \
                                            "<tr><td>Undefined UID: </td><td>"              + str(log_data["undefined_" + uid + "_uid"]) + "</td></tr>" + \
                                            "<tr><td>Undefined Enable State: </td><td>"     + str(log_data["undefined_" + uid + "_enable_state"]) + "</td></tr>" + \
                                            "<tr><td>Heartbeat: </td><td>"                  + str(log_data["undefined_" + uid + "_heartbeat"]) + "</td></tr>" + \
                                            "<tr><td>Warnings: </td><td>"                   + str(log_data["undefined_" + uid + "_warnings"]) + "</td></tr>" + \
                                            "<tr><td>Alarms: </td><td>"                     + str(log_data["undefined_" + uid + "_alarms"]) + "</td></tr>" + \
                                            "<tr><td>Faults: </td><td>"                     + str(log_data["undefined_" + uid + "_faults"]) + "</td></tr>" + \
                                            "<tr><td>Actions: </td><td>"                    + str(log_data["undefined_" + uid + "_actions"]) + "</td></tr>"
                                        
                            except Exception as e:
                                print("Logger: " + str(e))
                        
            except Exception as e:
                print("Logger: " + str(e))

            
            # Accumulate remote logs at every interval ----------
            self.logging_remote_interval_counter += 1
            if self.logging_remote_interval_counter >= int(self.logging_remote_interval):
                self.logging_remote_interval_counter = 0

                if self.logging_remote_interval_state == 0:                                         # JSON-ify messages and build packets
            
                    self.logging_remote_log_packet[self.logging_remote_length_counter] = log_data
                    self.logging_remote_length_counter += 1
                    self.logging_remote_buffer_count += 1
                    
                    # Transmit the log to the server when we've met the buffer length
                    if self.logging_remote_length_counter >= self.logging_remote_length:
                        self.logging_remote_length_counter = 0
                        self.logging_remote_buffer_count = 0

                        try:
                            self.log_to_server(self.logging_remote_log_packet, str(self.system_name))
                        except Exception as e:
                            print("Logger: " + str(e))
                        
                        # Clear the log_packet data
                        self.logging_remote_log_packet.clear()


            # Local logs ---------
            
            
            
            # Alert and Alarm Email processing ----------
            if self.email_cooldown > 0:                                                             # We can disable the email feature by zeroing the timeout period

                if self.alert_cooldown_count > 0:
                    self.alert_cooldown_count -= 1

                    self.send_alert_email = False
                else:
                    if self.send_alert_email:
                        alert = ""

                        if self.mod_timeout_alert:
                            alert = "Module Timeout Alert."
                            self.mod_timeout_alert = False

                        enable_state = " "

                        project_name = " "
                        project_serial = " "
                        if len(self.system_name) >= 1:
                            project_name = self.system_name[0][:15]
                            project_serial = self.system_name[0][16:]
                            
                        self.alert_cooldown_count = self.email_cooldown  # Reset the timeout period
                        if self.send_email(         
                                alert + " at site " + project_name + " - " + str(datetime.now()),
                                "<!doctype html >" + "\n" +
                                "<html lang=\"en\">" + "\n" +
                                "<head>" + "\n" +
                                "</head>" + "\n" +
                                "<body>" +
                                "<div>" +
                                "<h1>MSP SYSTEM ALERT" + "</h1>"                                                                                  
                                "At " + str(datetime.now().strftime("%H:%M:%S")) + " on " + str(datetime.now().strftime("%d-%m-%Y")) + ", BESS " + project_serial + " on site " + project_name + " experienced a \"" + 
                                alert + "\"" + enable_state +
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
        #else:
        #    self.send_alert_email = False
        #    self.send_fault_email = False  # Prevent triggering emails for pre-enabled conditions
      
      
    def get_outputs(self):
    
        # Return logging statitics for the HMI
        self.outputs = [0] * 20
        self.outputs[0] = self.logging_local_buffer_count
        self.outputs[1] = self.logging_local_file_count
        self.outputs[2] = self.logging_remote_buffer_count
        
        return [GET_OUTPUTS_ACK, self.outputs]
        
    def set_inputs(self, inputs):
        
        # Retrieve the user's logging configuration
        self.logging_data_store = inputs[0]
        #self.logging_data_server = inputs[1]                                                        # We have no local DB yet so this is currently ignored 
        self.logging_local_interval = inputs[2]
        self.logging_local_duration = inputs[3]
        self.logging_local_max_files = inputs[4]
        self.logging_remote_interval = inputs[5]
        self.logging_remote_length = inputs[6]
        self.override = inputs[7]
                            
        # Pass on system parameters that won't be accessible over SCADA
        self.system_name = inputs[10]
        self.system_type = inputs[11]
        self.latitude = inputs[12]
        self.longitude = inputs[13]
        
        return [SET_INPUTS_ACK]
    
    def send_email(self, subject, body):

        # Email specifics
        email_host = "smtp-mail.outlook.com"
        email_host_port = 587
        sent_from_email = "siteview@multisourcepower.com"
        sent_from_pw = "Bot45881!"
        send_to_addresses = ["siteview@multisourcepower.com"]  # The siteview account is configured to forward the email onto interested recipients based on "fault" or "alert" phrases.
        email_server = None

        email_server = smtplib.SMTP(
            host=email_host, port=email_host_port
        )
        email_server.starttls()
        email_server.login(sent_from_email, sent_from_pw)

        email_config = MIMEMultipart()

        email_config["From"] = sent_from_email
        email_config["To"] = ", ".join(send_to_addresses)
        email_config["Subject"] = subject

        email_body = MIMEText(body, "html")
        email_config.attach(email_body)

        try:
            email_server.send_message(email_config)
            email_server.quit()
            return True
        except:
            email_server.quit()
            return False
    
    def twos_comp_to_int(self, twoC):
        # Calculate int from two's compliment input
        if type(twoC) == int:
            return twoC if not twoC & 0x8000 else -(0xFFFF - twoC + 1)
        else:
            return 0
            
    def log_to_server(self, data, project):
        try:
            self.socket.send_data(data)
            #print("Sending log data to server as " + project + " at " + str(datetime.now()))

        except:
            print("Communication fault when sending log data to server at " + str(self.logging_data_server) + ", " + str(datetime.now()))
    
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
                if rx_msg[0] == SYNC:   # 1-Second poll    
                    
                    if not thread.is_alive():
                        thread = Thread(target=flex_module.process)
                        thread.start()
                    tx_msg = None
                    
                elif rx_msg[0] == GET_INFO: 
                    pass#tx_msg = flex_module.get_info()
                    
                elif rx_msg[0] == GET_STATUS:  
                    pass#tx_msg = flex_module.get_status()
                    
                elif rx_msg[0] == GET_PAGE:
                    pass#tx_msg = flex_module.get_page()
                    
                elif rx_msg[0] == SET_PAGE:
                    pass#tx_msg = flex_module.set_page(rx_msg[1], rx_msg[2])              
                
                elif rx_msg[0] == GET_OUTPUTS:
                    tx_msg = flex_module.get_outputs()
                   
                elif rx_msg[0] == SET_INPUTS:                                                       # Configuration from the HMI
                    tx_msg = flex_module.set_inputs(rx_msg[1])
                
                else:
                    print("Command Unknown: " + str(rx_msg[0]))
                    
        except Exception as e:
            print("Logger: " + str(e))
          
        try:
            if tx_msg is not None:
                queue[0].put(tx_msg, block=True)
                
        except Exception as e:
            print("Logger: " + str(e))