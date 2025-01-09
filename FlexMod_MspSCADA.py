# SCADA Module

# Description
# The SCADA module takes all the other module outputs (as does the controller and logger), and puts
# them out over a modbus/sunspec/mqtt? register map supporting scalability for all installed devices

# Versions
# 3.5.24.10.16 - SC - Known good starting point, uses thread.is_alive to prevent module stalling.

import sys
from threading import Thread, Event
from FlexDB import FlexTinyDB
from enum import Enum

from pymodbus.server.sync import ModbusTcpServer
from pymodbus.datastore import ModbusSequentialDataBlock
from pymodbus.datastore import ModbusSlaveContext, ModbusServerContext

from pymodbus.client.sync import ModbusTcpClient as modbus_client
from pymodbus.constants import Endian
from pymodbus.payload import BinaryPayloadDecoder
from datetime import datetime
import time
import copy

# Database
db = FlexTinyDB()

scadaServer = None

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


class ReusableModbusTcpServer(ModbusTcpServer):
    def __init__(self, context, framer=None, identity=None, address=None, handler=None, **kwargs):
        self.allow_reuse_address = True
        ModbusTcpServer.__init__(self, context, framer, identity, address, handler, **kwargs)


class MbusTcpServer(Thread):
    def __init__(self, event, context):
        Thread.__init__(self)
        self.stopped = event
        self.context = context
        
    def run(self):
        
        global scadaServer
        while not self.stopped.wait(1):
            try:
                scadaServer = ReusableModbusTcpServer(self.context, address=('', 502))
                scadaServer.serve_forever()
                
            except OSError:
                print("Error while starting server! : ", OSError)


class Module():
    def __init__(self, uid, queue):
        super(Module, self).__init__()

        # Module Data
        self.author = "Sophie Coates"
        self.uid = uid                                                                              # A unique identifier passed from the HMI via a mangled URL
        self.icon = "/static/images/SCADA.png"
        self.name = "MSP SCADA Interface"
        self.module_type = ModTypes.SCADA.value
        self.module_version = "3.5.24.10.16"
        self.manufacturer = "MSP"
        self.model = ""
        self.options = ""
        self.version = "-"
        self.serial = "-"                                                                           # This can be replaced with the device serial number later
        self.website = "/Mod/FlexMod_MspSCADA"                                                 # This is the template name itself

        # Run state
        self.con_state = True                                                                       # SCADA Isn't a device, just assume it's 'connected' always
        self.state = "Idle"
        self.set_state_text(State.IDLE)

        # Non-Volatile Data (Loaded from DB)
        self.dbData = db.fetch_from_db(__name__ + "_(" + str(self.uid) + ")")
        if self.dbData is None:
            self.dbData = dict()
            self.dbData["scada_ipaddr_local"] = "0.0.0.0"

        # Volatile Data
        self.tcp_client = None
        self.tcp_timeout = 0
        self.enabled = False
        self.enabled_echo = False
        self.override = False
        self.server_mode = False
        self.inputs = []            
        self.inputs_remote = []  
        self.outputs = [self.uid, self.enabled, [0]*25]
        self.heartbeat = 0
        self.heartbeat_echo = 0

        if "scada_server_mode" in self.dbData:
            if self.dbData["scada_server_mode"]:
                self.server_mode = True

        self.scada_heartbeat = 0
        self.scada_state = 0
        self.scada_client_control = 0
        self.scada_client_state = 0

        # Device Volatiles
        self.aircon_quantity = 1
        self.aircon_heartbeat = 0
        self.proc_state = 0
        self.mbtcp_server_stop = Event()
        self.mbtcp_server_registers = []
        self.mbcounter = 0

        # Remote control overrides
        self.remote_operating_control = 0
        self.remote_operating_state = 0
        self.remote_real_power_command = 0
        self.remote_reactive_power_command = 0
        self.remote_real_current_command = 0
        self.remote_reactive_current_command = 0

        # Client-specific registers. They are generic here but have special uses in the client module
        self.client_reg7 = 0
        self.client_reg8 = 0
        self.client_reg9 = 0
        self.client_reg10 = 0
        self.client_reg11 = 0
        self.client_reg12 = 0
        self.client_reg13 = 0
        self.client_reg14 = 0
        self.client_reg15 = 0
        self.client_reg16 = 0
        self.client_reg17 = 0
        self.client_reg18 = 0
        self.client_reg19 = 0
        self.client_reg20 = 0
        self.client_reg21 = 0
        self.client_reg22 = 0

        # Events
        self.warnings = 0
        self.alarms = 0
        self.faults = 0
        self.actions = []

        # HMI data, from which power is derived.
        self.priV = 0                                                                                 # Primary units of AC Voltage and Current
        self.priA = 0
        self.secV = 0                                                                                 # Secondary, for DC units
        self.secA = 0
        self.terV = 0                                                                                 # Tertiary, if a device has a third port, like a PD Hydra
        self.terA = 0

        self.fuel_cell_kw_setpoint = 0

        print("Starting " + self.name + " with UID " + str(self.uid) + " on version " + str(self.module_version))
        
        # Track Interval usage (GIL interference)
        self.start_time = time.time()
        self.interval_count = 0
        self.max_delay = 0
        self.last_time = 0

    def twos_comp_to_int(self, twoC):
        # calculate int from two's compliment input
        if type(twoC) == int:
            return twoC if not twoC & 0x8000 else -(0xFFFF - twoC + 1)
        else:
            return 0

    def int_to_twos_comp(self, num):
        return num if num >= 0 else (0xFFFF + num + 1)

    def process(self):
        global loop_time
        #print("(22)   SCADA Heartbeat: " + str(self.heartbeat) + ", loop time: " + str(loop_time) + " Seconds")
        
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
            #print("SCADA " + str(self.uid) + ": " + str(self.interval_count) + ". Max delay: " + str(self.max_delay))
        else:
            #print("SCADA " + str(self.uid) + ": " + str(self.interval_count) + " events in 1 hour. Maximum gap between events was " + str(self.max_delay) + " seconds.")
            pass
        
        s = time.time()
        
        #global interval_poll_count
        #interval_poll_count += 1
        #print(interval_poll_count)
        
        self.heartbeat += 1
        if self.heartbeat >= 0xFFFF:
            self.heartbeat = 0

        self.scada_heartbeat = self.heartbeat
        
        # Start the SCADA Interface
        if self.enabled:                                                                            # The controller can start and stop the process

            self.enabled_echo = True

            # Okay, so we need to determine if we're a Server or a Client...
            if not self.server_mode:                                                                # We're a Client. Retrieve data from remote Server
                self.scada_state = 1

                if self.icon != "/static/images/SCADAclient.png":
                    self.icon = "/static/images/SCADAclient.png"

                if self.tcp_timeout >= 5:
                    self.tcp_client = None
                    self.tcp_timeout = 0

                if self.tcp_client is None:
                    self.con_state = False
                    if "scada_ipaddr_local" in self.dbData:
                        if self.dbData["scada_ipaddr_local"] != "0.0.0.0":
                            try:
                                self.tcp_client = modbus_client(self.dbData["scada_ipaddr_local"], port=502, timeout=1)

                                if self.tcp_client.connect() is True:
                                    self.update_faults(Faults.LOSS_OF_COMMS.value, False)  # Clear Fault
                                    self.set_state_text(State.CONNECTED)
                                    self.con_state = True
                                else:
                                    self.update_faults(Faults.LOSS_OF_COMMS.value, True)  # Raise Fault
                                    self.set_state_text(State.CONNECTING)
                                    self.tcp_client = None
                                    return

                            except Exception as e:
                                print("SCADA: " + str(e))
                                self.update_faults(Faults.LOSS_OF_COMMS.value, True)
                                self.set_state_text(State.CONNECTING)
                        else:
                            self.update_faults(Faults.LOSS_OF_COMMS.value, True)  # Raise Fault
                            self.set_state_text(State.CONFIG)
                    else:
                        self.update_faults(Faults.LOSS_OF_COMMS.value, True)
                        self.set_state_text(State.CONFIG)
                else:
                    #print("Connected to BESS: " + str(self.uid))

                    self.inputs_remote = []     # Reset list

                    # Scale Factors
                    SF1 = 0
                    SF2 = 0
                    SF3 = 0
                    SF4 = 0

                    # Type 0 - Base
                    self.inputs_remote.append(None)

                    # Type 1 - Controller
                    try:
                        rr = self.tcp_client.read_holding_registers(250, 1, unit=1, timeout=0.25)

                        device_count = rr.registers[0]                                              # Controller Quantity, usually 1
                        module = []
                        for dev in range(device_count):
                            device = [0, False, [0]*25]
                            try:
                                rr = self.tcp_client.read_holding_registers((251 + (25*dev)), 25, unit=1, timeout=0.25)

                                device[0] = rr.registers[0]                             # Controller UID
                                device[1] = "True" if rr.registers[1] == 1 else "False"             # Enable State

                                self.scada_client_control = rr.registers[4]
                                self.scada_client_state = rr.registers[5]

                                # Copy all module variables
                                for x in range(23):
                                    device[2][x] = rr.registers[x+2]

                                # Scale Factors
                                device[2][16] = self.twos_comp_to_int(device[2][16])
                                device[2][17] = self.twos_comp_to_int(device[2][17])
                                device[2][18] = self.twos_comp_to_int(device[2][18])
                                device[2][19] = self.twos_comp_to_int(device[2][19])

                                # Format data which requires scaling
                                SF1 = device[2][16]
                                SF2 = device[2][17]
                                SF3 = device[2][18]
                                SF4 = device[2][19]

                                device[2][15] = int(device[2][15] * pow(10, SF2))  # Modbus map version

                                module.append(device)
                                
                                if self.remote_operating_state & 0x01:  # Local Enable Echo bit set, system is running. now start the slaves

                                    # Copy back the heartbeat otherwise the slaves will see a broken link and cancel the enable
                                    self.tcp_client.write_register(254, device[2][0], unit=1)

                                    if not device[2][3] & 0x02:  # Remote Operating state, if we're awaiting remloc, request it
                                        #print("Requesting remote control")
                                        self.tcp_client.write_register(255, 2, unit=1)  # Request remote control
                                    elif not device[2][3] & 0x01:  # We've been granted remote control (remote system has been set to "Remote")
                                        #print("Requesting remote enable")
                                        self.tcp_client.write_register(255, 3, unit=1)  # Retain remote request and Enable remote system
                                    else:
                                        #print("Remote system enabled!")
                                        pass
                                elif device[2][3] & 0x01:                                           # If we've previously enabled our slaves, disabled them
                                    self.tcp_client.write_register(255, 0, unit=1)                  # and retain remote control
                                    
                                self.tcp_timeout = 0

                            except Exception as e:
                                print("SCADA: " + str(e))
                                self.tcp_timeout += 1
                                return

                        self.inputs_remote.append(module)
                        self.tcp_timeout = 0
                        
                    except Exception as e:
                        print("SCADA: " + str(e))
                        self.tcp_timeout += 1
                        return

                    # Type 2 - Battery
                    try:
                        rr = self.tcp_client.read_holding_registers(500, 1, unit=1, timeout=0.25)

                        device_count = rr.registers[0]  # Battery Quantity
                        module = []
                        for dev in range(device_count):
                            device = [0, False, [0] * 25]
                            try:
                                rr = self.tcp_client.read_holding_registers((501 + (25 * dev)), 25, unit=1, timeout=0.25)

                                device[0] = rr.registers[0]  # Battery UID
                                device[1] = "True" if rr.registers[1] == 1 else "False"             # Enable State

                                # Copy all module variables
                                for x in range(23):
                                    device[2][x] = rr.registers[x + 2]

                                module.append(device)
                                self.tcp_timeout = 0

                            except Exception as e:
                                print("SCADA: " + str(e))
                                self.tcp_timeout += 1
                                return

                        #self.inputs_remote.append(module)  # Why was this removed? Because there's only 1? Maybe accidentally?
                        self.tcp_timeout = 0

                    except Exception as e:
                        print("SCADA: " + str(e))
                        self.tcp_timeout += 1
                        return

                    # Type 3 - Inverter
                    try:
                        rr = self.tcp_client.read_holding_registers(750, 1, unit=1, timeout=0.25)

                        device_count = rr.registers[0]  # Inverter Quantity
                        module = []
                        for dev in range(device_count):
                            device = [0, False, [0] * 25]
                            try:
                                rr = self.tcp_client.read_holding_registers((751 + (25 * dev)), 25, unit=1, timeout=0.25)

                                device[0] = rr.registers[0]  # Inverter UID
                                device[1] = "True" if rr.registers[1] == 1 else "False"             # Inverter State

                                # Copy all module variables
                                for x in range(23):
                                    device[2][x] = rr.registers[x + 2]

                                module.append(device)
                                self.tcp_timeout = 0

                            except Exception as e:
                                print("SCADA: " + str(e))
                                self.tcp_timeout += 1
                                return

                        self.inputs_remote.append(module)
                        self.tcp_timeout = 0
                        
                    except Exception as e:
                        print("SCADA: " + str(e))
                        self.tcp_timeout += 1
                        return

                    # Type 4 - AC Meter
                    try:
                        rr = self.tcp_client.read_holding_registers(1000, 1, unit=1, timeout=0.25)

                        device_count = rr.registers[0]  # AC Meter Quantity
                        module = []
                        for dev in range(device_count):
                            device = [0, False, [0] * 25]
                            try:
                                rr = self.tcp_client.read_holding_registers((1001 + (25 * dev)), 25, unit=1, timeout=0.25)

                                device[0] = rr.registers[0]  # AC Meter UID
                                device[1] = "True" if rr.registers[1] == 1 else "False"             # AC Meter State

                                # Copy all module variables
                                for x in range(23):
                                    device[2][x] = rr.registers[x + 2]

                                module.append(device)
                                self.tcp_timeout = 0

                            except Exception as e:
                                print("SCADA: " + str(e))
                                self.tcp_timeout += 1
                                return

                        self.inputs_remote.append(module)
                        self.tcp_timeout = 0
                        
                    except Exception as e:
                        print("SCADA: " + str(e))
                        self.tcp_timeout += 1
                        return

                    # Type 5 - DC Meter
                    try:
                        rr = self.tcp_client.read_holding_registers(1250, 1, unit=1, timeout=0.25)

                        device_count = rr.registers[0]  # DC Meter Quantity
                        module = []
                        for dev in range(device_count):
                            device = [0, False, [0] * 25]
                            try:
                                rr = self.tcp_client.read_holding_registers((1251 + (25 * dev)), 25, unit=1, timeout=0.25)

                                device[0] = rr.registers[0]  # DC Meter UID
                                device[1] = "True" if rr.registers[1] == 1 else "False"             # DC Meter State

                                # Copy all module variables
                                for x in range(23):
                                    device[2][x] = rr.registers[x + 2]

                                module.append(device)
                                self.tcp_timeout = 0

                            except Exception as e:
                                print("SCADA: " + str(e))
                                self.tcp_timeout += 1
                                return

                        self.inputs_remote.append(module)
                        self.tcp_timeout = 0
                        
                    except Exception as e:
                        print("SCADA: " + str(e))
                        self.tcp_timeout += 1
                        return

                    # Type 6 - Digital IO
                    try:
                        rr = self.tcp_client.read_holding_registers(1500, 1, unit=1, timeout=0.25)

                        device_count = rr.registers[0]  # Digital IO Quantity
                        module = []
                        for dev in range(device_count):
                            device = [0, False, [0] * 25]
                            try:
                                rr = self.tcp_client.read_holding_registers((1501 + (25 * dev)), 25, unit=1, timeout=0.25)

                                device[0] = rr.registers[0]  # Digital IO UID
                                device[1] = "True" if rr.registers[1] == 1 else "False"             # Digital IO State

                                # Copy all module variables
                                for x in range(23):
                                    device[2][x] = rr.registers[x + 2]

                                module.append(device)
                                self.tcp_timeout = 0

                            except Exception as e:
                                print("SCADA: " + str(e))
                                self.tcp_timeout += 1
                                return

                        self.inputs_remote.append(module)
                        self.tcp_timeout = 0
                        
                    except Exception as e:
                        print("SCADA: " + str(e))
                        self.tcp_timeout += 1
                        return

                    # Type 7 - Analogue IO
                    try:
                        rr = self.tcp_client.read_holding_registers(1750, 1, unit=1, timeout=0.25)

                        device_count = rr.registers[0]  # Analogue IO Quantity
                        module = []
                        for dev in range(device_count):
                            device = [0, False, [0] * 25]
                            try:
                                rr = self.tcp_client.read_holding_registers((1751 + (25 * dev)), 25, unit=1, timeout=0.25)

                                device[0] = rr.registers[0]  # Analogue IO UID
                                device[1] = "True" if rr.registers[1] == 1 else "False"             # Analogue IO State

                                # Copy all module variables
                                for x in range(23):
                                    device[2][x] = rr.registers[x + 2]

                                # Format data which requires scaling
                                device[2][4] = rr.registers[6] * pow(10, SF2)                       # Analogue input 0
                                device[2][5] = rr.registers[7] * pow(10, SF2)                       # Analogue input 1
                                device[2][6] = rr.registers[8] * pow(10, SF2)                       # Analogue input 2
                                device[2][7] = rr.registers[9] * pow(10, SF2)                       # Analogue input 3
                                device[2][8] = rr.registers[10] * pow(10, SF2)                     # Analogue input 4
                                device[2][9] = rr.registers[11] * pow(10, SF2)                     # Analogue input 5
                                device[2][10] = rr.registers[12] * pow(10, SF2)                     # Analogue input 6
                                device[2][11] = rr.registers[13] * pow(10, SF2)                     # Analogue input 7

                                module.append(device)
                                self.tcp_timeout = 0

                            except Exception as e:
                                print("SCADA: " + str(e))
                                self.tcp_timeout += 1
                                return

                        self.inputs_remote.append(module)
                        self.tcp_timeout = 0
                        
                    except Exception as e:
                        print("SCADA: " + str(e))
                        self.tcp_timeout += 1
                        return

                    # Type 8 - Mixed IO
                    try:
                        rr = self.tcp_client.read_holding_registers(2000, 1, unit=1, timeout=0.25)

                        device_count = rr.registers[0]  # Mixed IO Quantity
                        module = []
                        for dev in range(device_count):
                            device = [0, False, [0] * 25]
                            try:
                                rr = self.tcp_client.read_holding_registers((2001 + (25 * dev)), 25, unit=1, timeout=0.25)

                                device[0] = rr.registers[0]  # Mixed IO UID
                                device[1] = "True" if rr.registers[1] == 1 else "False"             # Mixed IO State

                                # Copy all module variables
                                for x in range(23):
                                    device[2][x] = rr.registers[x + 2]

                                module.append(device)
                                self.tcp_timeout = 0

                            except Exception as e:
                                print("SCADA: " + str(e))
                                self.tcp_timeout += 1
                                return

                        self.inputs_remote.append(module)
                        self.tcp_timeout = 0
                        
                    except Exception as e:
                        print("SCADA: " + str(e))
                        self.tcp_timeout += 1
                        return

                    # Type 9 - Switch
                    try:
                        rr = self.tcp_client.read_holding_registers(2250, 1, unit=1, timeout=0.25)

                        device_count = rr.registers[0]  # Switch Quantity
                        module = []
                        for dev in range(device_count):
                            device = [0, False, [0] * 25]
                            try:
                                rr = self.tcp_client.read_holding_registers((2251 + (25 * dev)), 25, unit=1, timeout=0.25)

                                device[0] = rr.registers[0]  # Switch UID
                                device[1] = "True" if rr.registers[1] == 1 else "False"             # Switch State

                                # Copy all module variables
                                for x in range(23):
                                    device[2][x] = rr.registers[x + 2]

                                module.append(device)
                                self.tcp_timeout = 0

                            except Exception as e:
                                print("SCADA: " + str(e))
                                self.tcp_timeout += 1
                                return

                        self.inputs_remote.append(module)
                        self.tcp_timeout = 0
                        
                    except Exception as e:
                        print("SCADA: " + str(e))
                        self.tcp_timeout += 1
                        return

                    # Type 10 - Li-Ion Tamer
                    try:
                        rr = self.tcp_client.read_holding_registers(2500, 1, unit=1, timeout=0.25)

                        device_count = rr.registers[0]  # Li-Ion Tamer Quantity
                        module = []
                        for dev in range(device_count):
                            device = [0, False, [0] * 25]
                            try:
                                rr = self.tcp_client.read_holding_registers((2501 + (25 * dev)), 25, unit=1, timeout=0.25)

                                device[0] = rr.registers[0]  # Li-Ion Tamer UID
                                device[1] = "True" if rr.registers[1] == 1 else "False"             # Li-Ion Tamer State

                                # Copy all module variables
                                for x in range(23):
                                    device[2][x] = rr.registers[x + 2]

                                # Format data which requires scaling
                                device[2][3] = "True" if rr.registers[6] == 1 else "False"
                                device[2][4] = "True" if rr.registers[7] == 1 else "False"
                                device[2][5] = "True" if rr.registers[8] == 1 else "False"
                                device[2][6] = "True" if rr.registers[9] == 1 else "False"
                                device[2][7] = "True" if rr.registers[10] == 1 else "False"
                                device[2][8] = "True" if rr.registers[11] == 1 else "False"
                                device[2][9] = "True" if rr.registers[12] == 1 else "False"
                                device[2][10] = "True" if rr.registers[13] == 1 else "False"
                                device[2][11] = "True" if rr.registers[14] == 1 else "False"
                                device[2][12] = "True" if rr.registers[15] == 1 else "False"
                                device[2][13] = "True" if rr.registers[16] == 1 else "False"
                                device[2][14] = "True" if rr.registers[17] == 1 else "False"
                                device[2][15] = "True" if rr.registers[18] == 1 else "False"
                                device[2][16] = "True" if rr.registers[19] == 1 else "False"
                                device[2][17] = "True" if rr.registers[20] == 1 else "False"

                                module.append(device)
                                self.tcp_timeout = 0

                            except Exception as e:
                                print("SCADA: " + str(e))
                                self.tcp_timeout += 1
                                return

                        self.inputs_remote.append(module)
                        self.tcp_timeout = 0
                        
                    except Exception as e:
                        print("SCADA: " + str(e))
                        self.tcp_timeout += 1
                        return

                    # Type 11 - DC-DC
                    try:
                        rr = self.tcp_client.read_holding_registers(2750, 1, unit=1, timeout=0.25)

                        device_count = rr.registers[0]  # DC-DC Tamer Quantity
                        module = []
                        for dev in range(device_count):
                            device = [0, False, [0] * 25]
                            try:
                                rr = self.tcp_client.read_holding_registers((2751 + (25 * dev)), 25, unit=1, timeout=0.25)

                                device[0] = rr.registers[0]  # DC-DC Tamer UID
                                device[1] = "True" if rr.registers[1] == 1 else "False"             # DC-DC Tamer State

                                # Copy all module variables
                                for x in range(23):
                                    device[2][x] = rr.registers[x + 2]

                                module.append(device)
                                self.tcp_timeout = 0

                            except Exception as e:
                                print("SCADA: " + str(e))
                                self.tcp_timeout += 1
                                return

                        self.inputs_remote.append(module)
                        self.tcp_timeout = 0
                        
                    except Exception as e:
                        print("SCADA: " + str(e))
                        self.tcp_timeout += 1
                        return

                    # Type 12 - Aircon
                    try:
                        rr = self.tcp_client.read_holding_registers(3000, 1, unit=1, timeout=0.25)

                        device_count = rr.registers[0]  # Aircon Quantity
                        module = []
                        for dev in range(device_count):
                            device = [0, False, [0] * 25]
                            try:
                                rr = self.tcp_client.read_holding_registers((3001 + (25 * dev)), 25, unit=1, timeout=0.25)

                                device[0] = rr.registers[0]  # Aircon UID
                                device[1] = "True" if rr.registers[1] == 1 else "False"             # Aircon State

                                # Copy all module variables
                                for x in range(23):
                                    device[2][x] = rr.registers[x + 2]

                                module.append(device)
                                self.tcp_timeout = 0

                            except Exception as e:
                                print("SCADA: " + str(e))
                                self.tcp_timeout += 1
                                return

                        self.inputs_remote.append(module)
                        self.tcp_timeout = 0
                        
                    except Exception as e:
                        print("SCADA: " + str(e))
                        self.tcp_timeout += 1
                        return

                    # Type 13 - Sensor
                    try:
                        rr = self.tcp_client.read_holding_registers(3250, 1, unit=1, timeout=0.25)

                        device_count = rr.registers[0]  # Sensor Quantity
                        module = []
                        for dev in range(device_count):
                            device = [0, False, [0] * 25]
                            try:
                                rr = self.tcp_client.read_holding_registers((3251 + (25 * dev)), 25, unit=1, timeout=0.25)

                                device[0] = rr.registers[0]  # Sensor UID
                                device[1] = "True" if rr.registers[1] == 1 else "False"             # Sensor State

                                # Copy all module variables
                                for x in range(23):
                                    device[2][x] = rr.registers[x + 2]

                                module.append(device)
                                self.tcp_timeout = 0

                            except Exception as e:
                                print("SCADA: " + str(e))
                                self.tcp_timeout += 1
                                return

                        self.inputs_remote.append(module)
                        self.tcp_timeout = 0
                        
                    except Exception as e:
                        print("SCADA: " + str(e))
                        self.tcp_timeout += 1
                        return

                    # Type 14 - Fuel Cell
                    try:
                        rr = self.tcp_client.read_holding_registers(3500, 1, unit=1, timeout=0.25)

                        device_count = rr.registers[0]  # Fuel Cell Quantity
                        module = []
                        for dev in range(device_count):
                            device = [0, False, [0] * 25]
                            try:
                                rr = self.tcp_client.read_holding_registers((3501 + (25 * dev)), 25, unit=1, timeout=0.25)

                                device[0] = rr.registers[0]  # Fuel Cell UID
                                device[1] = "True" if rr.registers[1] == 1 else "False"             # Fuel Cell State

                                # Copy all module variables
                                for x in range(23):
                                    device[2][x] = rr.registers[x + 2]

                                module.append(device)
                                self.tcp_timeout = 0

                            except Exception as e:
                                print("SCADA: " + str(e))
                                self.tcp_timeout += 1
                                return

                        self.inputs_remote.append(module)
                        self.tcp_timeout = 0
                        
                    except Exception as e:
                        print("SCADA: " + str(e))
                        self.tcp_timeout += 1
                        return

                    # Type 15 - AC Generator
                    try:
                        rr = self.tcp_client.read_holding_registers(3750, 1, unit=1, timeout=0.25)

                        device_count = rr.registers[0]  # AC Gen Quantity
                        module = []
                        for dev in range(device_count):
                            device = [0, False, [0] * 25]
                            try:
                                rr = self.tcp_client.read_holding_registers((3751 + (25 * dev)), 25, unit=1, timeout=0.25)

                                device[0] = rr.registers[0]  # AC Gen UID
                                device[1] = "True" if rr.registers[1] == 1 else "False"             # AC Gen State

                                # Copy all module variables
                                for x in range(23):
                                    device[2][x] = rr.registers[x + 2]

                                module.append(device)
                                self.tcp_timeout = 0

                            except Exception as e:
                                print("SCADA: " + str(e))
                                self.tcp_timeout += 1
                                return

                        self.inputs_remote.append(module)
                        self.tcp_timeout = 0
                        
                    except Exception as e:
                        print("SCADA: " + str(e))
                        self.tcp_timeout += 1
                        return

                    # Type 16 - AC Wind
                    try:
                        rr = self.tcp_client.read_holding_registers(4000, 1, unit=1, timeout=0.25)

                        device_count = rr.registers[0]  # AC Wind Quantity
                        module = []
                        for dev in range(device_count):
                            device = [0, False, [0] * 25]
                            try:
                                rr = self.tcp_client.read_holding_registers((4001 + (25 * dev)), 25, unit=1, timeout=0.25)

                                device[0] = rr.registers[0]  # AC Wind UID
                                device[1] = "True" if rr.registers[1] == 1 else "False"             # AC Wind State

                                # Copy all module variables
                                for x in range(23):
                                    device[2][x] = rr.registers[x + 2]

                                module.append(device)
                                self.tcp_timeout = 0

                            except Exception as e:
                                print("SCADA: " + str(e))
                                self.tcp_timeout += 1
                                return

                        self.inputs_remote.append(module)
                        self.tcp_timeout = 0
                        
                    except Exception as e:
                        print("SCADA: " + str(e))
                        self.tcp_timeout += 1
                        return

                    # Type 17 - AC Solar
                    try:
                        rr = self.tcp_client.read_holding_registers(4250, 1, unit=1, timeout=0.25)

                        device_count = rr.registers[0]  # AC Wind Quantity
                        module = []
                        for dev in range(device_count):
                            device = [0, False, [0] * 25]
                            try:
                                rr = self.tcp_client.read_holding_registers((4251 + (25 * dev)), 25, unit=1, timeout=0.25)

                                device[0] = rr.registers[0]  # AC Solar UID
                                device[1] = "True" if rr.registers[1] == 1 else "False"             # AC Solar State

                                # Copy all module variables
                                for x in range(23):
                                    device[2][x] = rr.registers[x + 2]

                                module.append(device)
                                self.tcp_timeout = 0

                            except Exception as e:  
                                print("SCADA: " + str(e))
                                self.tcp_timeout += 1
                                return

                        self.inputs_remote.append(module)
                        self.tcp_timeout = 0
                        
                    except Exception as e:
                        print("SCADA: " + str(e))
                        self.tcp_timeout += 1
                        return

                    # Type 18 - DC Solar
                    try:
                        rr = self.tcp_client.read_holding_registers(4500, 1, unit=1, timeout=0.25)

                        device_count = rr.registers[0]  # DC Wind Quantity
                        module = []
                        for dev in range(device_count):
                            device = [0, False, [0] * 25]
                            try:
                                rr = self.tcp_client.read_holding_registers((4501 + (25 * dev)), 25, unit=1, timeout=0.25)

                                device[0] = rr.registers[0]  # DC Solar UID
                                device[1] = "True" if rr.registers[1] == 1 else "False"             # DC Solar State

                                # Copy all module variables
                                for x in range(23):
                                    device[2][x] = rr.registers[x + 2]

                                module.append(device)
                                self.tcp_timeout = 0

                            except Exception as e:
                                print("SCADA: " + str(e))
                                self.tcp_timeout += 1
                                return

                        self.inputs_remote.append(module)
                        self.tcp_timeout = 0
                        
                    except Exception as e:
                        print("SCADA: " + str(e))
                        self.tcp_timeout += 1
                        return

                    # Type 19 - AC EFM
                    try:
                        pass
                        rr = self.tcp_client.read_holding_registers(4750, 1, unit=1, timeout=0.25)

                        device_count = rr.registers[0]  # AC EFM Quantity

                        module = []
                        for dev in range(device_count):
                            device = [0, False, [0] * 25]
                            try:
                                rr = self.tcp_client.read_holding_registers((4751 + (25 * dev)), 25, unit=1, timeout=0.25)

                                device[0] = rr.registers[0]  # AC EFM UID
                                device[1] = "True" if rr.registers[1] == 1 else "False"             # AC EFM State

                                # Copy all module variables
                                for x in range(23):
                                    device[2][x] = rr.registers[x + 2]

                                module.append(device)
                                self.tcp_timeout = 0

                            except Exception as e:
                                print("SCADA: " + str(e))
                                self.tcp_timeout += 1
                                return

                        self.inputs_remote.append(module)
                        self.tcp_timeout = 0
                        
                    except Exception as e:
                        print("SCADA: " + str(e))
                        self.tcp_timeout += 1
                        return

                    # Type 20 - DC EFM
                    try:
                        rr = self.tcp_client.read_holding_registers(5000, 1, unit=1, timeout=0.25)

                        device_count = rr.registers[0]  # DC EFM Quantity
                        module = []
                        for dev in range(device_count):
                            device = [0, False, [0] * 25]
                            try:
                                rr = self.tcp_client.read_holding_registers((5001 + (25 * dev)), 25, unit=1, timeout=0.25)

                                device[0] = rr.registers[0]  # DC EFM UID
                                device[1] = "True" if rr.registers[1] == 1 else "False"             # DC EFM State

                                # Copy all module variables
                                for x in range(23):
                                    device[2][x] = rr.registers[x + 2]

                                module.append(device)
                                self.tcp_timeout = 0

                            except Exception as e:
                                print("SCADA: " + str(e))
                                self.tcp_timeout += 1
                                return

                        self.inputs_remote.append(module)
                        self.tcp_timeout = 0
                        
                    except Exception as e:
                        print("SCADA: " + str(e))
                        self.tcp_timeout += 1
                        return

                    # Type 21 - EV Charger
                    try:
                        rr = self.tcp_client.read_holding_registers(5250, 1, unit=1, timeout=0.25)

                        device_count = rr.registers[0]  # EV Charger Quantity
                        module = []
                        for dev in range(device_count):
                            device = [0, False, [0] * 25]
                            try:
                                rr = self.tcp_client.read_holding_registers((5251 + (25 * dev)), 25, unit=1, timeout=0.25)

                                device[0] = rr.registers[0]  # EV Charger UID
                                device[1] = "True" if rr.registers[1] == 1 else "False"             # EV Charger State

                                # Copy all module variables
                                for x in range(23):
                                    device[2][x] = rr.registers[x + 2]

                                module.append(device)
                                self.tcp_timeout = 0

                            except Exception as e:
                                print("SCADA: " + str(e))
                                self.tcp_timeout += 1
                                return

                        self.inputs_remote.append(module)
                        self.tcp_timeout = 0
                        
                    except Exception as e:
                        print("SCADA: " + str(e))
                        self.tcp_timeout += 1
                        return

                    # Type 22 - SCADA
                    try:
                        rr = self.tcp_client.read_holding_registers(5500, 1, unit=1, timeout=0.25)

                        device_count = rr.registers[0]  # SCADA Quantity
                        module = []
                        for dev in range(device_count):
                            device = [0, False, [0] * 25]
                            try:
                                rr = self.tcp_client.read_holding_registers((5501 + (25 * dev)), 25, unit=1, timeout=0.25)

                                device[0] = rr.registers[0]  # SCADA UID
                                device[1] = "True" if rr.registers[1] == 1 else "False"             # SCADA State

                                # Copy all module variables
                                for x in range(23):
                                    device[2][x] = rr.registers[x + 2]

                                module.append(device)
                                self.tcp_timeout = 0

                            except Exception as e:
                                print("SCADA: " + str(e))
                                self.tcp_timeout += 1
                                return

                        self.inputs_remote.append(module)
                        self.tcp_timeout = 0
                        
                    except Exception as e:
                        print("SCADA: " + str(e))
                        self.tcp_timeout += 1
                        return

                    # Type 23 - Logging
                    try:
                        rr = self.tcp_client.read_holding_registers(5750, 1, unit=1, timeout=0.25)

                        device_count = rr.registers[0]  # Logging Quantity
                        module = []
                        for dev in range(device_count):
                            device = [0, False, [0] * 25]
                            try:
                                rr = self.tcp_client.read_holding_registers((5751 + (25 * dev)), 25, unit=1, timeout=0.25)

                                device[0] = rr.registers[0]  # Logging UID
                                device[1] = "True" if rr.registers[1] == 1 else "False"             # Logging State

                                # Copy all module variables
                                for x in range(23):
                                    device[2][x] = rr.registers[x + 2]

                                module.append(device)
                                self.tcp_timeout = 0

                            except Exception as e:
                                print("SCADA: " + str(e))
                                self.tcp_timeout += 1
                                return

                        self.inputs_remote.append(module)
                        self.tcp_timeout = 0
                        
                    except Exception as e:
                        print("SCADA: " + str(e))
                        self.tcp_timeout += 1
                        return

                    # Type 24 - Client
                    try:
                        rr = self.tcp_client.read_holding_registers(6000, 1, unit=1, timeout=0.25)

                        device_count = rr.registers[0]  # Client Quantity

                        module = []
                        for dev in range(device_count):
                            device = [0, False, [0] * 25]
                            try:
                                rr = self.tcp_client.read_holding_registers((6001 + (25 * dev)), 25, unit=1, timeout=0.25)

                                device[0] = rr.registers[0]  # Client UID
                                device[1] = "True" if rr.registers[1] == 1 else "False"             # Client State

                                # Copy all module variables
                                for x in range(23):
                                    device[2][x] = rr.registers[x + 2]

                                module.append(device)
                                self.tcp_timeout = 0

                            except Exception as e:
                                print("SCADA: " + str(e))
                                self.tcp_timeout += 1
                                return

                        self.inputs_remote.append(module)
                        self.tcp_timeout = 0
                        
                    except Exception as e:
                        print("SCADA: " + str(e))
                        self.tcp_timeout += 1
                        return

                    # Type 25 - Undefined
                    try:
                        rr = self.tcp_client.read_holding_registers(6250, 1, unit=1, timeout=0.25)

                        device_count = rr.registers[0]  # Undefined Quantity
                        module = []
                        for dev in range(device_count):
                            device = [0, False, [0] * 25]
                            try:
                                rr = self.tcp_client.read_holding_registers((6251 + (25 * dev)), 25, unit=1, timeout=0.25)

                                device[0] = rr.registers[0]  # Undefined UID
                                device[1] = "True" if rr.registers[1] == 1 else "False"             # Undefined State

                                # Copy all module variables
                                for x in range(23):
                                    device[2][x] = rr.registers[x + 2]

                                module.append(device)
                                self.tcp_timeout = 0

                            except Exception as e:
                                print("SCADA: " + str(e))
                                self.tcp_timeout += 1
                                return

                        self.inputs_remote.append(module)
                        self.tcp_timeout = 0
                        
                    except Exception as e:
                        print("SCADA: " + str(e))
                        self.tcp_timeout += 1
                        return
            else:                                                                                   # Operate as a Server, populate SCADA data from self.inputs
                self.scada_state = 0

                if self.icon != "/static/images/SCADA.png":
                    self.icon = "/static/images/SCADA.png"

                # SCADA State Machine
                if self.proc_state == 0:                                                            # Begin SCADA requests
                    
                    # Start Modbus-TCP Server for SCADA interface
                    try:
                        pass
                        # Now moved to the calling process by SC 220724
                        
                        # Server sits in an infinite loop so must have its own thread.
                        self.store = ModbusSlaveContext(hr=ModbusSequentialDataBlock(0, [0] * 10000))   # We're only using Holding Registers

                        self.mbtcp_server_registers = ModbusServerContext(slaves=self.store, single=True)

                        self.mbtcp_server_thread = MbusTcpServer(self.mbtcp_server_stop, self.mbtcp_server_registers)
                        self.mbtcp_server_thread.start()

                        self.proc_state = 1
                    
                    except OSError:
                        pass
                       
                elif self.proc_state == 1:
                    # ***** Process incoming Modbus-TCP requests *****
                    # Type 1 - Controller
                    heartbeat = self.mbtcp_server_registers[0x00].getValues(3, 253, 1)                  # Check returned heartbeat
                    heartbeat_echo = self.mbtcp_server_registers[0x00].getValues(3, 254, 1)
                    self.remote_operating_control = self.mbtcp_server_registers[0x00].getValues(3, 255, 1)[0]
                    self.remote_operating_state = self.mbtcp_server_registers[0x00].getValues(3, 256, 1)[0]
                    self.remote_real_power_command = self.mbtcp_server_registers[0x00].getValues(3, 257, 1)[0]
                    self.remote_reactive_power_command = self.mbtcp_server_registers[0x00].getValues(3, 258, 1)[0]
                    self.remote_real_current_command = self.mbtcp_server_registers[0x00].getValues(3, 259, 1)[0]
                    self.remote_reactive_current_command = self.mbtcp_server_registers[0x00].getValues(3, 260, 1)[0]

                    # Type 14- Fuel Cell
                    self.fuel_cell_kw_setpoint = self.mbtcp_server_registers[0x00].getValues(3, 3514, 1)[0]

                    # Type 24 - Client
                    self.client_reg7 = self.mbtcp_server_registers[0x00].getValues(3, 6007, 1)[0]
                    self.client_reg8 = self.mbtcp_server_registers[0x00].getValues(3, 6008, 1)[0]
                    self.client_reg9 = self.mbtcp_server_registers[0x00].getValues(3, 6009, 1)[0]
                    self.client_reg10 = self.mbtcp_server_registers[0x00].getValues(3, 6010, 1)[0]
                    self.client_reg11 = self.mbtcp_server_registers[0x00].getValues(3, 6011, 1)[0]
                    self.client_reg12 = self.mbtcp_server_registers[0x00].getValues(3, 6012, 1)[0]
                    self.client_reg13 = self.mbtcp_server_registers[0x00].getValues(3, 6013, 1)[0]
                    self.client_reg14 = self.mbtcp_server_registers[0x00].getValues(3, 6014, 1)[0]
                    self.client_reg15 = self.mbtcp_server_registers[0x00].getValues(3, 6015, 1)[0]
                    self.client_reg16 = self.mbtcp_server_registers[0x00].getValues(3, 6016, 1)[0]
                    self.client_reg17 = self.mbtcp_server_registers[0x00].getValues(3, 6017, 1)[0]
                    self.client_reg18 = self.mbtcp_server_registers[0x00].getValues(3, 6018, 1)[0]
                    self.client_reg19 = self.mbtcp_server_registers[0x00].getValues(3, 6019, 1)[0]
                    self.client_reg20 = self.mbtcp_server_registers[0x00].getValues(3, 6020, 1)[0]
                    self.client_reg21 = self.mbtcp_server_registers[0x00].getValues(3, 6021, 1)[0]
                    self.client_reg22 = self.mbtcp_server_registers[0x00].getValues(3, 6022, 1)[0]

                    # ***** Update outgoing Modbus-TCP register Map *****
                    # Type 0 - Base
                    type_0_data = [0] * 26
                    type_0_data[0] = 0                                                                  # Set Quantity to zero as it is not represented here yet.

                    # Type 1 - Controller
                    if self.inputs[1] is not None:
                        type_1_data = [0] * 251
                        type_1_data[0] = len(self.inputs[1])                                                # Get the Quantity of loaded controller modules (usually 1)
                        for module in range(type_1_data[0]):                                                # Parse each module of this type and offset by 25 bytes
                            type_1_data[(module * 25) + 1] = self.inputs[1][module][0]                      # Controller UID
                            type_1_data[(module * 25) + 2] = 1 if self.inputs[1][module][1] else 0          # Enable State

                            # Read new data into the outgoing buffer
                            for x in range(23):
                                type_1_data[(module * 25) + (x + 3)] = self.inputs[1][module][2][x]     # Copies all new data but overwites end-user data. So we have to reinsert it (below)

                            # Re-insert client written data to stop it from toggling
                            type_1_data[(module * 25) + 4] = heartbeat_echo[0]
                            type_1_data[(module * 25) + 5] = self.remote_operating_control
                            type_1_data[(module * 25) + 7] = self.remote_real_power_command
                            type_1_data[(module * 25) + 8] = self.remote_reactive_power_command
                            type_1_data[(module * 25) + 9] = self.remote_real_current_command
                            type_1_data[(module * 25) + 10] = self.remote_reactive_current_command

                            # Remote control requested
                            #if self.inputs[1][module][2][2] & (1 << 1):
                            #    # This should only apply if a controller has requested remote control, not just monitoring
                            #    if ((heartbeat[0] - heartbeat_echo[0]) & 0xFFFF) >= 5:  # 5-second timeout before system auto disable (HMI configurable option desirable)
                            #        if self.remote_operating_control & (1 << 0):
                            #            print("Heartbeat Echo invalid, disabling")  # TODO Raise fault, hard or soft system shutdown
                            #            self.remote_operating_control &= ~(1 << 0)  # Clear the remote user's enable bit if they haven't returned a correct heartbeat

                            # Scale Factors
                            type_1_data[19] = self.int_to_twos_comp(type_1_data[19])
                            type_1_data[20] = self.int_to_twos_comp(type_1_data[20])
                            type_1_data[21] = self.int_to_twos_comp(type_1_data[21])
                            type_1_data[22] = self.int_to_twos_comp(type_1_data[22])

                            # Format data which requires scaling
                            SF2 = pow(10, -self.twos_comp_to_int(type_1_data[20]))
                            type_1_data[18] = int(type_1_data[18] * SF2)                                    # Modbus map version
                            
                            # Late addition but True is converted to '1' which is currently breaking the interface, i.e. QModMaster connects but can't read the data. 
                            type_1_data[1] = int(type_1_data[1])
                            
                        try:
                            self.mbtcp_server_registers[0x00].setValues(3, 250, type_1_data)
                        except Exception as e:
                            print("SCADA: " + str(e))

                    # Type 2 - Battery
                    if self.inputs[2] is not None:
                        type_2_data = [0] * 251
                        type_2_data[0] = len(self.inputs[2])                                                # Get the Quantity of loaded Battery modules
                        for module in range(type_2_data[0]):                                                # Parse each module of this type and offset by 25 bytes
                            type_2_data[(module * 25) + 1] = self.inputs[2][module][0]                      # Battery UID(s)
                            type_2_data[(module * 25) + 2] = 1 if self.inputs[2][module][1] else 0          # Enable State

                            # Copy all module variables
                            for x in range(23):
                                type_2_data[(module * 25) + (x + 3)] = self.inputs[2][module][2][x]

                            # Format data which requires scaling
                            SF1 = pow(10, -self.twos_comp_to_int(type_1_data[19]))
                            SF3 = pow(10, -self.twos_comp_to_int(type_1_data[21]))
                            type_2_data[(module * 25) + 5] = int(type_2_data[(module * 25) + 5] * SF1)                                      # SoC
                            type_2_data[(module * 25) + 6] = int(type_2_data[(module * 25) + 6] * SF1)                                      # SoH
                            type_2_data[(module * 25) + 7] = self.int_to_twos_comp(int(type_2_data[(module * 25) + 7] * SF1))               # DC Bus Voltage
                            type_2_data[(module * 25) + 8] = self.int_to_twos_comp(int(type_2_data[(module * 25) + 8] * SF1))               # DC Bus Current
                            type_2_data[(module * 25) + 9] = self.int_to_twos_comp(int(type_2_data[(module * 25) + 9] * SF1))               # DC Bus Power
                            type_2_data[(module * 25) + 10] = (type_2_data[(module * 25) + 10] & 0xFFFF)
                            type_2_data[(module * 25) + 11] = (int(type_2_data[(module * 25) + 11]/1000) * SF1)                             # Max Charge Power
                            type_2_data[(module * 25) + 12] = (int(type_2_data[(module * 25) + 12]/1000) * SF1)                             # Max Discharge Power
                            type_2_data[(module * 25) + 13] = int(type_2_data[(module * 25) + 13] * SF3)                                    # Cell Voltage Min
                            type_2_data[(module * 25) + 14] = int(type_2_data[(module * 25) + 14] * SF3)                                    # Cell Voltage Max
                            type_2_data[(module * 25) + 15] = int(type_2_data[(module * 25) + 15] * SF3)                                    # Cell Voltage Avg
                            type_2_data[(module * 25) + 16] = self.int_to_twos_comp(int(type_2_data[(module * 25) + 16] * SF3))             # Cell Temp Min
                            type_2_data[(module * 25) + 17] = self.int_to_twos_comp(int(type_2_data[(module * 25) + 17] * SF3))             # Cell Temp Max
                            type_2_data[(module * 25) + 18] = self.int_to_twos_comp(int(type_2_data[(module * 25) + 18] * SF3))             # Cell Temp Avg
                            type_2_data[(module * 25) + 19] = int(type_2_data[(module * 25) + 19])  # Cycle count (string)
                            type_2_data[(module * 25) + 20] = int(type_2_data[(module * 25) + 20] * SF1)                                    # Max Capacity
                            type_2_data[(module * 25) + 21] = int(type_2_data[(module * 25) + 21] * SF1)                                    # Online Capacity

                            # Late addition but True is converted to char which is currently breaking the interface, i.e. QModMaster connects but can't read the data. 
                            type_2_data[1] = int(type_2_data[1])
                        try:
                            # print("type 2")
                            # print(type_2_data)
                            self.mbtcp_server_registers[0x00].setValues(3, 500, type_2_data)
                        except Exception as e:
                            print("SCADA: " + str(e))
                    
                    # Type 3 - Inverter
                    if self.inputs[3] is not None:
                        type_3_data = [0] * 251
                        type_3_data[0] = len(self.inputs[3])                                                # Get the Quantity of loaded Inverter modules
                        for module in range(type_3_data[0]):                                                # Parse each module of this type and offset by 25 bytes
                            type_3_data[(module * 25) + 1] = self.inputs[3][module][0]                      # Inverter UID(s)
                            type_3_data[(module * 25) + 2] = 1 if self.inputs[3][module][1] else 0          # Enable State

                            # Copy all module variables
                            for x in range(23):
                                type_3_data[(module * 25) + (x + 3)] = self.inputs[3][module][2][x]

                            # Format data which requires scaling
                            SF1 = pow(10, -self.twos_comp_to_int(type_1_data[19]))
                            SF2 = pow(10, -self.twos_comp_to_int(type_1_data[20]))
                            SF3 = pow(10, -self.twos_comp_to_int(type_1_data[21]))
                            type_3_data[(module * 25) + 5] = int(type_3_data[(module * 25) + 5] * SF3)                                      # Frequency
                            type_3_data[(module * 25) + 6] = self.int_to_twos_comp(int(type_3_data[(module * 25) + 6] * SF1))               # Measured AC Line Voltage
                            type_3_data[(module * 25) + 7] = self.int_to_twos_comp(int(type_3_data[(module * 25) + 7] * SF1))               # Real Current Echo
                            type_3_data[(module * 25) + 8] = self.int_to_twos_comp(int(type_3_data[(module * 25) + 8] * SF1))               # AC Real Current
                            type_3_data[(module * 25) + 9] = self.int_to_twos_comp(int(type_3_data[(module * 25) + 9] * SF1))               # Reactive Current Echo
                            type_3_data[(module * 25) + 10] = self.int_to_twos_comp(int(type_3_data[(module * 25) + 10] * SF1))             # AC Reactive Current
                            type_3_data[(module * 25) + 11] = self.int_to_twos_comp(int(type_3_data[(module * 25) + 11] * SF1))             # Real Power Echo
                            type_3_data[(module * 25) + 12] = self.int_to_twos_comp(int((type_3_data[(module * 25) + 12]/1000) * SF1))      # AC Real Power
                            type_3_data[(module * 25) + 13] = self.int_to_twos_comp(int(type_3_data[(module * 25) + 13] * SF1))             # Reactive Power Echo
                            type_3_data[(module * 25) + 14] = self.int_to_twos_comp(int((type_3_data[(module * 25) + 14]/1000) * SF1))      # AC Reactive Power
                            type_3_data[(module * 25) + 15] = self.int_to_twos_comp(int(type_3_data[(module * 25) + 15] * SF1))             # Measured DC Bus Voltage
                            type_3_data[(module * 25) + 16] = self.int_to_twos_comp(int(type_3_data[(module * 25) + 16] * SF1))             # Measured DC Bus Current
                            type_3_data[(module * 25) + 17] = self.int_to_twos_comp(int(type_3_data[(module * 25) + 17] * SF1))             # Measured PV Bus Voltage
                            type_3_data[(module * 25) + 18] = self.int_to_twos_comp(int(type_3_data[(module * 25) + 18] * SF1))             # PV Current Echo
                            type_3_data[(module * 25) + 19] = self.int_to_twos_comp(int(type_3_data[(module * 25) + 19] * SF1))             # Measured PV Bus Current
                            type_3_data[(module * 25) + 20] = self.int_to_twos_comp(int(type_3_data[(module * 25) + 20] * SF2))             # Internal Temperature
                            type_3_data[(module * 25) + 21] = self.int_to_twos_comp(int(type_3_data[(module * 25) + 21] * SF2))             # Inlet Temperature

                            # Late addition but True is converted to a char which is currently breaking the interface, i.e. QModMaster connects but can't read the data. 
                            type_3_data[1] = int(type_3_data[1])
                        try:
                            # print("type 3")
                            # print(type_3_data)
                            self.mbtcp_server_registers[0x00].setValues(3, 750, type_3_data)
                        except Exception as e:
                            print("SCADA: " + str(e))

                    # Type 4 - AC Meter
                    if self.inputs[4] is not None:
                        type_4_data = [0] * 251
                        type_4_data[0] = len(self.inputs[4])                                                # Get the Quantity of loaded DC Meter modules
                        for module in range(type_4_data[0]):                                                # Parse each module of this type and offset by 25 bytes
                            type_4_data[(module * 25) + 1] = self.inputs[4][module][0]                      # DC Meter UID(s)
                            type_4_data[(module * 25) + 2] = 1 if self.inputs[4][module][1] else 0          # Enable State

                            # Copy all module variables
                            for x in range(23):
                                type_4_data[(module * 25) + (x + 3)] = self.inputs[4][module][2][x]

                            # Format data which requires scaling
                            SF1 = pow(10, -self.twos_comp_to_int(type_1_data[19]))
                            SF2 = pow(10, -self.twos_comp_to_int(type_1_data[20]))
                            SF3 = pow(10, -self.twos_comp_to_int(type_1_data[21]))
                            type_4_data[(module * 25) + 5] = self.int_to_twos_comp(int(type_4_data[(module * 25) + 5] * SF1))               # Average Phase Voltage
                            type_4_data[(module * 25) + 6] = self.int_to_twos_comp(int(type_4_data[(module * 25) + 6] * SF1))               # Average Line Voltage
                            type_4_data[(module * 25) + 7] = self.int_to_twos_comp(int(type_4_data[(module * 25) + 7] * SF1))               # Average Current
                            type_4_data[(module * 25) + 8] = self.int_to_twos_comp(int(type_4_data[(module * 25) + 8] * SF3))               # Frequency
                            type_4_data[(module * 25) + 9] = self.int_to_twos_comp(int(type_4_data[(module * 25) + 9] * SF2))               # Total Power Factor
                            type_4_data[(module * 25) + 10] = self.int_to_twos_comp(int(type_4_data[(module * 25) + 10] * SF1))             # Total Active Power
                            type_4_data[(module * 25) + 16] = self.int_to_twos_comp(int(type_4_data[(module * 25) + 16] * SF1))             # Total Apparent Power
                            type_4_data[(module * 25) + 18] = self.int_to_twos_comp(int(type_4_data[(module * 25) + 18] * SF1))             # Total Current

                            # Late addition but True is converted to a char which is currently breaking the interface, i.e. QModMaster connects but can't read the data. 
                            type_4_data[1] = int(type_4_data[1])

                        try:
                            self.mbtcp_server_registers[0x00].setValues(3, 1000, type_4_data)
                        except Exception as e:
                            print("SCADA: " + str(e))

                    # Type 5 - DC Meter TODO: Not yet developed
                    #if self.inputs[5] is not None:
                        #type_5_data = [0] * 251
                        #type_5_data[0] = len(self.inputs[5])  # Get the Quantity of loaded inverter modules
                        #for module in range(type_5_data[0]):  # Parse each module of this type and offset by 25 bytes
                        #    type_5_data[(module * 25) + 1] = self.inputs[5][module][0]  # AC Meter UID(s)
                        #    type_5_data[(module * 25) + 2] = 1 if self.inputs[5][module][1] else 0  # Enable State
                        #    for x in range(23):
                        #        type_5_data[(module * 25) + (x + 3)] = self.inputs[5][module][2][x]  # The remaining DC Meter data starting from Heartbeat
                        #self.mbtcp_server_registers[0x00].setValues(3, 1250, type_5_data)
                    
                    # Type 6 - Digital IO
                    if self.inputs[6] is not None:
                        type_6_data = [0] * 251
                        type_6_data[0] = len(self.inputs[6])  # Get the Quantity of loaded Digital IO modules
                        for module in range(type_6_data[0]):  # Parse each module of this type and offset by 25 bytes
                            type_6_data[(module * 25) + 1] = self.inputs[6][module][0]  # Digital IO UID(s)
                            type_6_data[(module * 25) + 2] = 1 if self.inputs[6][module][1] else 0  # Enable State

                            # Copy all module variables
                            for x in range(23):
                                type_6_data[(module * 25) + (x + 3)] = self.inputs[6][module][2][x]  # The remaining Digital IO data starting from Heartbeat

                            # Late addition but True is converted to a char which is currently breaking the interface, i.e. QModMaster connects but can't read the data. 
                            type_6_data[1] = int(type_6_data[1])

                        try:
                            self.mbtcp_server_registers[0x00].setValues(3, 1500, type_6_data)
                        except Exception as e:
                            print("SCADA: " + str(e))

                    # Type 7 - Analogue IO  TODO: Problematic - May need to do the type conversion at point of use, keep it raw until then?
                    if self.inputs[7] is not None:
                        type_7_data = [0] * 251
                        type_7_data[0] = len(self.inputs[7])  # Get the Quantity of loaded Analogue IO modules
                        for module in range(type_7_data[0]):  # Parse each module of this type and offset by 25 bytes
                            type_7_data[(module * 25) + 1] = self.inputs[7][module][0]  # Analogue IO UID(s)
                            type_7_data[(module * 25) + 2] = 1 if self.inputs[7][module][1] else 0  # Enable State

                            # Copy all module variables
                            for x in range(23):
                                type_7_data[(module * 25) + (x + 3)] = self.inputs[7][module][2][x]  # The remaining Analogue IO data starting from Heartbeat

                            # Format data which requires scaling
                            SF2 = pow(10, -self.twos_comp_to_int(type_1_data[20]))
                            type_7_data[(module * 25) + 6] = self.int_to_twos_comp(int(type_7_data[(module * 25) + 6] * SF2))               # Analogue input 0
                            type_7_data[(module * 25) + 7] = self.int_to_twos_comp(int(type_7_data[(module * 25) + 7] * SF2))               # Analogue input 1
                            type_7_data[(module * 25) + 8] = self.int_to_twos_comp(int(type_7_data[(module * 25) + 8] * SF2))               # Analogue input 2
                            type_7_data[(module * 25) + 9] = self.int_to_twos_comp(int(type_7_data[(module * 25) + 9] * SF2))               # Analogue input 3
                            type_7_data[(module * 25) + 10] = self.int_to_twos_comp(int(type_7_data[(module * 25) + 10] * SF2))             # Analogue input 4
                            type_7_data[(module * 25) + 11] = self.int_to_twos_comp(int(type_7_data[(module * 25) + 11] * SF2))             # Analogue input 5
                            type_7_data[(module * 25) + 12] = self.int_to_twos_comp(int(type_7_data[(module * 25) + 12] * SF2))             # Analogue input 6
                            type_7_data[(module * 25) + 13] = self.int_to_twos_comp(int(type_7_data[(module * 25) + 13] * SF2))             # Analogue input 7

                            # Late addition but True is converted to a char which is currently breaking the interface, i.e. QModMaster connects but can't read the data. 
                            type_7_data[1] = int(type_7_data[1])
    
                        try:
                            self.mbtcp_server_registers[0x00].setValues(3, 1750, type_7_data)
                        except Exception as e:
                            print("SCADA: " + str(e))

                    #print(type_7_data)

                    # Type 8 - Mixed IO TODO: Not yet developed
                    #if self.inputs[8] is not None:
                        #type_8_data = [0] * 251
                        #type_8_data[0] = len(self.inputs[8])  # Get the Quantity of loaded Mixed IO modules
                        #for module in range(type_8_data[0]):  # Parse each module of this type and offset by 25 bytes
                        #    type_8_data[(module * 25) + 1] = self.inputs[8][module][0]  # Mixed IO UID(s)
                        #    type_8_data[(module * 25) + 2] = 1 if self.inputs[8][module][1] else 0  # Enable State
                        #    for x in range(23):
                        #        type_8_data[(module * 25) + (x + 3)] = self.inputs[8][module][2][x]  # The remaining Mixed IO data starting from Heartbeat
                        #self.mbtcp_server_registers[0x00].setValues(3,2000, type_8_data)

                    # Type 9 - Switch TODO: Not yet developed
                    #if self.inputs[9] is not None:
                        #type_9_data = [0] * 251
                        #type_9_data[0] = len(self.inputs[9])  # Get the Quantity of loaded Switch modules
                        #for module in range(type_9_data[0]):  # Parse each module of this type and offset by 25 bytes
                        #    type_9_data[(module * 25) + 1] = self.inputs[9][module][0]  # Switch UID(s)
                        #    type_9_data[(module * 25) + 2] = 1 if self.inputs[9][module][1] else 0  # Enable State
                        #    for x in range(23):
                        #        type_9_data[(module * 25) + (x + 3)] = self.inputs[9][module][2][x]  # The remaining Switch data starting from Heartbeat
                        #self.mbtcp_server_registers[0x00].setValues(3, 2250, type_9_data)

                    # Type 10 - Li-Ion
                    if self.inputs[10] is not None:
                        type_10_data = [0] * 251
                        type_10_data[0] = len(self.inputs[10])  # Get the Quantity of loaded Li-Ion modules
                        for module in range(type_10_data[0]):  # Parse each module of this type and offset by 25 bytes
                            type_10_data[(module * 25) + 1] = self.inputs[10][module][0]  # Li-Ion UID(s)
                            type_10_data[(module * 25) + 2] = 1 if self.inputs[10][module][1] else 0  # Enable State

                            # Copy all module variables
                            for x in range(23):
                                type_10_data[(module * 25) + (x + 3)] = self.inputs[10][module][2][x]  # The remaining Li-Ion data starting from Heartbeat

                            # Late addition but True is converted to a char which is currently breaking the interface, i.e. QModMaster connects but can't read the data. 
                            type_10_data[1] = int(type_10_data[1])

                        try:
                            # print("type 10")
                            # print(type_10_data)
                            self.mbtcp_server_registers[0x00].setValues(3, 2500, type_10_data)
                        except Exception as e:
                            print("SCADA: " + str(e))

                    # Type 11 - DCDC TODO: Not yet ready
                    # if self.inputs[11] is not None:
                    #     type_11_data = [0] * 251
                    #     type_11_data[0] = len(self.inputs[11])  # Get the Quantity of loaded DCDC modules
                    #     for module in range(type_11_data[0]):  # Parse each module of this type and offset by 25 bytes
                    #         type_11_data[(module * 25) + 1] = self.inputs[11][module][0]  # DCDC UID(s)
                    #         type_11_data[(module * 25) + 2] = 1 if self.inputs[11][module][1] else 0  # Enable State
                    #         for x in range(23):
                    #             type_11_data[(module * 25) + (x + 3)] = self.inputs[11][module][2][x]  # The remaining DCDC data starting from Heartbeat
                    #     self.mbtcp_server_registers[0x00].setValues(3, 2750, type_11_data)

                    # Type 12 - Aircon
                    if self.inputs[12] is not None:
                        type_12_data = [0] * 251
                        type_12_data[0] = len(self.inputs[12])                                              # Get the Quantity of loaded Aircon modules
                        for module in range(type_12_data[0]):                                               # Parse each module of this type and offset by 25 bytes
                            type_12_data[(module * 25) + 1] = self.inputs[12][module][0]                    # Aircon UID(s)
                            type_12_data[(module * 25) + 2] = 1 if self.inputs[12][module][1] else 0        # Enable State

                            # Copy all module variables
                            for x in range(23):
                                type_12_data[(module * 25) + (x + 3)] = self.inputs[12][module][2][x] & 0xFFFF

                            # Format data which requires scaling
                            SF2 = pow(10, -self.twos_comp_to_int(type_1_data[20]))
                            type_12_data[(module * 25) + 10] = self.int_to_twos_comp(int(type_12_data[(module * 25) + 10] * SF2))           # A/C 1 Ambient Temperature
                            type_12_data[(module * 25) + 12] = self.int_to_twos_comp(int(type_12_data[(module * 25) + 12] * SF2))           # A/C 2 Ambient Temperature
                            type_12_data[(module * 25) + 14] = self.int_to_twos_comp(int(type_12_data[(module * 25) + 14] * SF2))           # A/C 3 Ambient Temperature
                            type_12_data[(module * 25) + 16] = self.int_to_twos_comp(int(type_12_data[(module * 25) + 16] * SF2))           # A/C 4 Ambient Temperature
                            type_12_data[(module * 25) + 18] = self.int_to_twos_comp(int(type_12_data[(module * 25) + 18] * SF2))           # A/C 5 Ambient Temperature
                            type_12_data[(module * 25) + 20] = self.int_to_twos_comp(int(type_12_data[(module * 25) + 20] * SF2))           # A/C 6 Ambient Temperature

                            # Late addition but True is converted to a char which is currently breaking the interface, i.e. QModMaster connects but can't read the data. 
                            type_12_data[1] = int(type_12_data[1])

                        try:
                            # print("type 12")
                            # print(type_12_data)
                            self.mbtcp_server_registers[0x00].setValues(3, 3000, type_12_data)
                        except Exception as e:
                            print("SCADA: " + str(e))

                    # Type 13 - Sensor TODO: Not yet developed
                    # if self.inputs[13] is not None:
                        # type_13_data = [0] * 251
                        # type_13_data[0] = len(self.inputs[13])  # Get the Quantity of loaded Sensor modules
                        # for module in range(type_13_data[0]):  # Parse each module of this type and offset by 25 bytes
                        #    type_13_data[(module * 25) + 1] = self.inputs[13][module][0]  # Sensor UID(s)
                        #    type_13_data[(module * 25) + 2] = 1 if self.inputs[13][module][1] else 0  # Enable State
                        #    for x in range(23):
                        #        type_13_data[(module * 25) + (x + 3)] = self.inputs[13][module][2][x]  # The remaining Sensor data starting from Heartbeat
                        # self.mbtcp_server_registers[0x00].setValues(3, 3250, type_13_data)

                    # Type 14 - Fuel Cell
                    if self.inputs[14] is not None:
                        type_14_data = [0] * 251
                        type_14_data[0] = len(self.inputs[14])                                              # Get the Quantity of loaded Fuel Cell modules
                        for module in range(type_14_data[0]):                                               # Parse each module of this type and offset by 25 bytes
                            type_14_data[(module * 25) + 1] = self.inputs[14][module][0]                    # Fuel Cell UID(s)
                            type_14_data[(module * 25) + 2] = 1 if self.inputs[14][module][1] else 0        # Enable State

                            # Copy all module variables
                            for x in range(23):
                                type_14_data[(module * 25) + (x + 3)] = self.inputs[14][module][2][x]

                            # Format data which requires scaling
                            # SF1 = pow(10, -self.twos_comp_to_int(type_1_data[19]))
                            # type_14_data[(module * 25) + 8] = self.int_to_twos_comp(int(type_14_data[(module * 25) + 8] * SF1))
                            # type_14_data[(module * 25) + 10] = self.int_to_twos_comp(int(type_14_data[(module * 25) + 10] * SF1))
                            # type_14_data[(module * 25) + 11] = self.int_to_twos_comp(int(type_14_data[(module * 25) + 11] * SF1))
                            # type_14_data[(module * 25) + 12] = self.int_to_twos_comp(int(type_14_data[(module * 25) + 12] * SF1))
                            # type_14_data[(module * 25) + 13] = self.int_to_twos_comp(int(type_14_data[(module * 25) + 13] * SF1))

                            # Re-insert client written data to stop it from toggling
                            type_14_data[(module * 25) + 14] = self.int_to_twos_comp(int(self.fuel_cell_kw_setpoint))

                            # Late addition but True is converted to a char which is currently breaking the interface, i.e. QModMaster connects but can't read the data. 
                            type_14_data[1] = int(type_14_data[1])

                        try:
                            # print("type 14")
                            # print(type_14_data)
                            self.mbtcp_server_registers[0x00].setValues(3, 3500, type_14_data)
                        except Exception as e:
                            print("SCADA: " + str(e))

                    # Type 15 - AC Gen TODO: Not yet developed
                    if self.inputs[15] is not None:
                        type_15_data = [0] * 251
                        type_15_data[0] = len(self.inputs[15])                                              # Get the Quantity of loaded AC Gen modules
                        for module in range(type_15_data[0]):                                               # Parse each module of this type and offset by 25 bytes
                            type_15_data[(module * 25) + 1] = self.inputs[15][module][0]                    # AC Gen UID(s)
                            type_15_data[(module * 25) + 2] = 1 if self.inputs[15][module][1] else 0        # Enable State

                            # Copy all module variables
                            for x in range(23):
                                type_15_data[(module * 25) + (x + 3)] = self.inputs[15][module][2][x]

                            # Format data which requires scaling
                            SF1 = pow(10, -self.twos_comp_to_int(type_1_data[19]))
                            SF2 = pow(10, -self.twos_comp_to_int(type_1_data[20]))
                            type_15_data[(module * 25) + 9] = self.int_to_twos_comp(int(type_15_data[(module * 25) + 9] * SF1))             # Oil Pressure
                            type_15_data[(module * 25) + 10] = self.int_to_twos_comp(int(type_15_data[(module * 25) + 10] * SF2))           # Coolant Temperature
                            type_15_data[(module * 25) + 11] = self.int_to_twos_comp(int(type_15_data[(module * 25) + 11] * SF1))           # Fuel Level
                            type_15_data[(module * 25) + 12] = self.int_to_twos_comp(int(type_15_data[(module * 25) + 12] * SF1))           # Alternator Voltage
                            type_15_data[(module * 25) + 13] = self.int_to_twos_comp(int(type_15_data[(module * 25) + 13] * SF1))           # Battery Voltage
                            type_15_data[(module * 25) + 14] = self.int_to_twos_comp(int(type_15_data[(module * 25) + 14] * SF1))           # Engine Speed
                            type_15_data[(module * 25) + 15] = self.int_to_twos_comp(int(type_15_data[(module * 25) + 15] * SF2))           # AC Frequency
                            type_15_data[(module * 25) + 16] = self.int_to_twos_comp(int(type_15_data[(module * 25) + 16] * SF1))           # AC Line Voltage
                            type_15_data[(module * 25) + 17] = self.int_to_twos_comp(int(type_15_data[(module * 25) + 17] * SF1))           # AC Phase Voltage
                            type_15_data[(module * 25) + 18] = self.int_to_twos_comp(int(type_15_data[(module * 25) + 18] * SF1))           # AC Current
                            type_15_data[(module * 25) + 19] = self.int_to_twos_comp(int(type_15_data[(module * 25) + 19] * SF1))           # AC Earth Current
                            type_15_data[(module * 25) + 20] = self.int_to_twos_comp(int(type_15_data[(module * 25) + 10] * SF1))           # AC Power

                            # Late addition but True is converted to a char which is currently breaking the interface, i.e. QModMaster connects but can't read the data. 
                            type_15_data[1] = int(type_15_data[1])

                        try:
                            self.mbtcp_server_registers[0x00].setValues(3, 3750, type_15_data)
                        except Exception as e:
                            print("SCADA: " + str(e))

                    # Type 16 - AC Wind TODO: Not yet developed
                    # if self.inputs[16] is not None:
                        # type_16_data = [0] * 251
                        # type_16_data[0] = len(self.inputs[16])  # Get the Quantity of loaded AC Wind modules
                        # for module in range(type_16_data[0]):  # Parse each module of this type and offset by 25 bytes
                        #    type_16_data[(module * 25) + 1] = self.inputs[16][module][0]  # AC Wind UID(s)
                        #    type_16_data[(module * 25) + 2] = 1 if self.inputs[16][module][1] else 0  # Enable State
                        #    for x in range(23):
                        #        type_16_data[(module * 25) + (x + 3)] = self.inputs[16][module][2][x]  # The remaining AC Wind data starting from Heartbeat
                        # self.mbtcp_server_registers[0x00].setValues(3, 4000, type_16_data)

                    # Type 17 - AC Solar
                    if self.inputs[17] is not None:
                        type_17_data = [0] * 251
                        type_17_data[0] = len(self.inputs[17])  # Get the Quantity of loaded AC Solar modules
                        for module in range(type_17_data[0]):  # Parse each module of this type and offset by 25 bytes
                            type_17_data[(module * 25) + 1] = self.inputs[17][module][0]  # AC Solar UID(s)
                            type_17_data[(module * 25) + 2] = 1 if self.inputs[17][module][1] else 0  # Enable State

                            # Copy all module variables
                            for x in range(23):
                                type_17_data[(module * 25) + (x + 3)] = self.inputs[17][module][2][x]

                            # Format data which requires scaling
                            SF1 = pow(10, -self.twos_comp_to_int(type_1_data[19]))
                            SF2 = pow(10, -self.twos_comp_to_int(type_1_data[20]))
                            type_17_data[(module * 25) + 8] = self.int_to_twos_comp(int(type_17_data[(module * 25) + 8] * SF1))             # DC Bus Voltage
                            type_17_data[(module * 25) + 9] = self.int_to_twos_comp(int(type_17_data[(module * 25) + 9] * SF1))             # DC Bus Current
                            type_17_data[(module * 25) + 10] = self.int_to_twos_comp(int(type_17_data[(module * 25) + 10] * SF1))           # DC Bus Power
                            type_17_data[(module * 25) + 11] = self.int_to_twos_comp(int(type_17_data[(module * 25) + 11] * SF2))           # Temperature
                            type_17_data[(module * 25) + 12] = self.int_to_twos_comp(int(type_17_data[(module * 25) + 12] * SF2))           # AC Frequency
                            type_17_data[(module * 25) + 13] = self.int_to_twos_comp(int(type_17_data[(module * 25) + 13] * SF1))           # AC Total Current
                            type_17_data[(module * 25) + 14] = self.int_to_twos_comp(int(type_17_data[(module * 25) + 14] * SF1))           # AC Line Voltage
                            type_17_data[(module * 25) + 15] = self.int_to_twos_comp(int(type_17_data[(module * 25) + 15] * SF1))           # AC Power
                            type_17_data[(module * 25) + 16] = self.int_to_twos_comp(int(type_17_data[(module * 25) + 16] * SF1))           # SF Needs to be checked
                            type_17_data[(module * 25) + 18] = self.int_to_twos_comp(int(type_17_data[(module * 25) + 18] / 100))           # SF Needs to be checked

                            # Late addition but True is converted to a char which is currently breaking the interface, i.e. QModMaster connects but can't read the data. 
                            type_17_data[1] = int(type_17_data[1])

                        try:
                            self.mbtcp_server_registers[0x00].setValues(3, 4250, type_17_data)
                        except Exception as e:
                            print("SCADA: " + str(e))

                    # Type 18 - DC Solar
                    if self.inputs[18] is not None:
                        type_18_data = [0] * 251
                        type_18_data[0] = len(self.inputs[18])  # Get the Quantity of loaded DC Solar modules
                        for module in range(type_18_data[0]):  # Parse each module of this type and offset by 25 bytes
                            type_18_data[(module * 25) + 1] = self.inputs[18][module][0]  # DC Solar UID(s)
                            type_18_data[(module * 25) + 2] = 1 if self.inputs[18][module][1] else 0  # Enable State
                           
                            # Copy all module variables
                            for x in range(23):
                                type_18_data[(module * 25) + (x + 3)] = self.inputs[18][module][2][x]

                            # Format data which requires scaling
                            SF1 = pow(10, -self.twos_comp_to_int(type_1_data[19]))
                            SF2 = pow(10, -self.twos_comp_to_int(type_1_data[20]))
                            type_18_data[(module * 25) + 8] = self.int_to_twos_comp(int(type_18_data[(module * 25) + 8] * SF1))             # DC Bus Voltage
                            type_18_data[(module * 25) + 9] = self.int_to_twos_comp(int(type_18_data[(module * 25) + 9] * SF1))             # DC Bus Current
                            type_18_data[(module * 25) + 10] = self.int_to_twos_comp(int(type_18_data[(module * 25) + 10] * SF1))           # DC Bus Power
                            type_18_data[(module * 25) + 11] = self.int_to_twos_comp(int(type_18_data[(module * 25) + 11] * SF2))           # Temperature
                            type_18_data[(module * 25) + 12] = self.int_to_twos_comp(int(type_18_data[(module * 25) + 12] * SF2))           # AC Frequency
                            type_18_data[(module * 25) + 13] = self.int_to_twos_comp(int(type_18_data[(module * 25) + 13] * SF1))           # AC Total Current
                            type_18_data[(module * 25) + 14] = self.int_to_twos_comp(int(type_18_data[(module * 25) + 14] * SF1))           # AC Line Voltage
                            type_18_data[(module * 25) + 15] = self.int_to_twos_comp(int(type_18_data[(module * 25) + 15] * SF1))           # AC Power

                            # Late addition but True is converted to a char which is currently breaking the interface, i.e. QModMaster connects but can't read the data. 
                            type_18_data[1] = int(type_18_data[1])
                        
                        try:
                            self.mbtcp_server_registers[0x00].setValues(3, 4500, type_18_data)
                        except Exception as e:
                            print("SCADA: " + str(e))

                    # Type 19 - AC EFM
                    if self.inputs[19] is not None:
                        type_19_data = [0] * 251
                        type_19_data[0] = len(self.inputs[19])  # Get the Quantity of loaded AC EFM modules
                        for module in range(type_19_data[0]):  # Parse each module of this type and offset by 25 bytes
                            type_19_data[(module * 25) + 1] = self.inputs[19][module][0]  # AC EFM UID(s)
                            type_19_data[(module * 25) + 2] = 1 if self.inputs[19][module][1] else 0  # Enable State
                        
                            # Copy all module variables
                            for x in range(23):
                                type_19_data[(module * 25) + (x + 3)] = self.inputs[19][module][2][x]
                        
                            # Format data which requires scaling
                            SF1 = pow(10, -self.twos_comp_to_int(type_1_data[19]))
                            type_19_data[(module * 25) + 6] = self.int_to_twos_comp(int(type_19_data[(module * 25) + 6] * SF1))     # Insulation Alarm 1
                            type_19_data[(module * 25) + 7] = self.int_to_twos_comp(int(type_19_data[(module * 25) + 7] * SF1))     # Insulation Alarm 2
                            type_19_data[(module * 25) + 8] = self.int_to_twos_comp(int(type_19_data[(module * 25) + 8] * SF1))
                            type_19_data[(module * 25) + 9] = self.int_to_twos_comp(int(type_19_data[(module * 25) + 9])/1000)           # Insulation Resistance (forced scaling)
                            type_19_data[(module * 25) + 10] = self.int_to_twos_comp(int(type_19_data[(module * 25) + 10])/1000)         # Min. Insulation Resistance (forced scaling)
                            type_19_data[(module * 25) + 12] = self.int_to_twos_comp(int(type_19_data[(module * 25) + 12] * SF1))   # Capacitance

                        # Late addition but True is converted to a char which is currently breaking the interface, i.e. QModMaster connects but can't read the data. 
                        type_19_data[1] = int(type_19_data[1])

                        try:
                            self.mbtcp_server_registers[0x00].setValues(3, 4750, type_19_data)
                        except Exception as e:
                            print("SCADA: " + str(e))

                    # Type 20 - DC EFM
                    if self.inputs[20] is not None:
                        type_20_data = [0] * 251
                        type_20_data[0] = len(self.inputs[20])  # Get the Quantity of loaded DC EFM modules
                        for module in range(type_20_data[0]):  # Parse each module of this type and offset by 25 bytes
                            type_20_data[(module * 25) + 1] = self.inputs[20][module][0]  # DC EFM UID(s)
                            type_20_data[(module * 25) + 2] = 1 if self.inputs[20][module][1] else 0  # Enable State
                        
                            # Copy all module variables
                            for x in range(23):
                                type_20_data[(module * 25) + (x + 3)] = self.inputs[20][module][2][x]
                        
                            # Format data which requires scaling
                            SF1 = pow(10, -self.twos_comp_to_int(type_1_data[19]))
                            type_20_data[(module * 25) + 6] = self.int_to_twos_comp(int(type_20_data[(module * 25) + 6] * SF1))     # Insulation Alarm 1
                            type_20_data[(module * 25) + 7] = self.int_to_twos_comp(int(type_20_data[(module * 25) + 7] * SF1))     # Insulation Alarm 2
                            type_20_data[(module * 25) + 8] = self.int_to_twos_comp(int(type_20_data[(module * 25) + 8] * SF1))
                            type_20_data[(module * 25) + 9] = self.int_to_twos_comp(int(type_20_data[(module * 25) + 9])/1000)           # Insulation Resistance (forced scaling)
                            type_20_data[(module * 25) + 10] = self.int_to_twos_comp(int(type_20_data[(module * 25) + 10])/1000)         # Minimum Insulation Resistance (forced scaling)

                        # Late addition but True is converted to a char which is currently breaking the interface, i.e. QModMaster connects but can't read the data. 
                        type_20_data[1] = int(type_20_data[1])
                        
                        try:
                            self.mbtcp_server_registers[0x00].setValues(3, 5000, type_20_data)
                        except Exception as e:
                            print("SCADA: " + str(e))

                    # Type 21 - EV Charge
                    if self.inputs[21] is not None:
                        type_21_data = [0] * 251
                        type_21_data[0] = len(self.inputs[21])  # Get the Quantity of loaded EV Charge modules
                        for module in range(type_21_data[0]):  # Parse each module of this type and offset by 25 bytes
                            type_21_data[(module * 25) + 1] = self.inputs[21][module][0]  # EV Charge UID(s)
                            type_21_data[(module * 25) + 2] = 1 if self.inputs[21][module][1] else 0  # Enable State

                            # Copy all module variables
                            for x in range(23):
                                type_21_data[(module * 25) + (x + 3)] = self.inputs[21][module][2][x]

                            # Format data which requires scaling
                            SF1 = pow(10, -self.twos_comp_to_int(type_1_data[19]))
                            type_21_data[(module * 25) + 8] = self.int_to_twos_comp(int(type_21_data[(module * 25) + 8] * SF1))             # Max Power Budget
                            type_21_data[(module * 25) + 17] = self.int_to_twos_comp(int(type_21_data[(module * 25) + 17] * SF1))           # Vehicle SoC
                            type_21_data[(module * 25) + 18] = self.int_to_twos_comp(int(type_21_data[(module * 25) + 18] * SF1))           # Vehicle DC Voltage
                            type_21_data[(module * 25) + 19] = self.int_to_twos_comp(int(type_21_data[(module * 25) + 19] * SF1))           # Vehicle DC Current
                            type_21_data[(module * 25) + 20] = self.int_to_twos_comp(int(type_21_data[(module * 25) + 20] * SF1))           # Vehicle DC Power

                            # Late addition but True is converted to a char which is currently breaking the interface, i.e. QModMaster connects but can't read the data. 
                            type_21_data[1] = int(type_21_data[1])

                        try:
                            self.mbtcp_server_registers[0x00].setValues(3, 5250, type_21_data)
                        except Exception as e:
                            print("SCADA: " + str(e))

                    # Type 22 - SCADA - Seems a little recursive but all modules must operate the same way!
                    if self.inputs[22] is not None:
                        type_22_data = [0] * 251
                        type_22_data[0] = len(self.inputs[22])                                              # Get the Quantity of loaded SCADA modules
                        for module in range(type_22_data[0]):                                               # Parse each module of this type and offset by 25 bytes
                            type_22_data[(module * 25) + 1] = self.inputs[22][module][0]                    # SCADA UID(s)
                            type_22_data[(module * 25) + 2] = 1 if self.inputs[22][module][1] else 0        # Enable State

                            # Copy all module variables
                            for x in range(23):
                                type_22_data[(module * 25) + (x + 3)] = self.inputs[22][module][2][x]

                            # Late addition but True is converted to a char which is currently breaking the interface, i.e. QModMaster connects but can't read the data. 
                            type_22_data[1] = int(type_22_data[1])

                        try:
                            self.mbtcp_server_registers[0x00].setValues(3, 5500, type_22_data)
                        except Exception as e:
                            print("SCADA: " + str(e))

                    # Type 23 - Logging
                    if self.inputs[23] is not None:
                        type_23_data = [0] * 251
                        type_23_data[0] = len(self.inputs[23])                                              # Get the Quantity of loaded Logging modules
                        for module in range(type_23_data[0]):                                               # Parse each module of this type and offset by 25 bytes
                            type_23_data[(module * 25) + 1] = self.inputs[23][module][0]                    # Logging UID(s)
                            type_23_data[(module * 25) + 2] = 1 if self.inputs[23][module][1] else 0        # Enable State

                            # Copy all module variables
                            for x in range(23):
                                type_23_data[(module * 25) + (x + 3)] = self.inputs[23][module][2][x]       # The remaining Logging data starting from Heartbeat

                            # Late addition but True is converted to a char which is currently breaking the interface, i.e. QModMaster connects but can't read the data. 
                            type_23_data[1] = int(type_23_data[1])

                        try:
                            self.mbtcp_server_registers[0x00].setValues(3, 5750, type_23_data)
                        except Exception as e:
                            print("SCADA: " + str(e))

                    # Type 24 - Client
                    if self.inputs[24][0][24] is not None:                                                  # We really have to dig into the data to find the client module
                        #print(self.inputs[24][0])
                        type_24_data = [0] * 251
                        type_24_data[0] = 1                                                                 # "There can only be one!"
                        for module in range(type_24_data[0]):  # Parse each module of this type and offset by 25 bytes
                            type_24_data[(module * 25) + 1] = self.inputs[24][0][24][0]  # Client UID(s)
                            type_24_data[(module * 25) + 2] = 1 if self.inputs[24][0][24][1] else 0  # Enable State

                            # Read new data into the outgoing buffer
                            for x in range(23):
                                type_24_data[(module * 25) + (x + 3)] = self.inputs[24][0][24][2][x]  # The remaining Client data starting from Heartbeat

                            # Re-insert client written data to stop it from toggling
                            type_24_data[(module * 25) + 7] = self.client_reg7
                            type_24_data[(module * 25) + 8] = self.client_reg8
                            type_24_data[(module * 25) + 9] = self.client_reg9
                            type_24_data[(module * 25) + 10] = self.client_reg10
                            type_24_data[(module * 25) + 11] = self.client_reg11
                            type_24_data[(module * 25) + 12] = self.client_reg12
                            type_24_data[(module * 25) + 13] = self.client_reg13
                            type_24_data[(module * 25) + 14] = self.client_reg14
                            type_24_data[(module * 25) + 15] = self.client_reg15
                            type_24_data[(module * 25) + 16] = self.client_reg16
                            type_24_data[(module * 25) + 17] = self.client_reg17
                            type_24_data[(module * 25) + 18] = self.client_reg18
                            type_24_data[(module * 25) + 19] = self.client_reg19
                            type_24_data[(module * 25) + 20] = self.client_reg20
                            type_24_data[(module * 25) + 21] = self.client_reg21
                            type_24_data[(module * 25) + 22] = self.client_reg22

                            # Late addition but True is converted to a char which is currently breaking the interface, i.e. QModMaster connects but can't read the data. 
                            type_24_data[1] = int(type_24_data[1])

                        try:
                            self.mbtcp_server_registers[0x00].setValues(3, 6000, type_24_data)
                        except Exception as e:
                            print("SCADA: " + str(e))

                    # Type 25 - Undefined
                    
            if len(self.actions) == 0:
                self.actions.append(0)  # Dummy

            # Modify self.outputs
            self.outputs[2][0] = self.scada_heartbeat
            self.outputs[2][1] = self.scada_state
            self.outputs[2][2] = 0
            self.outputs[2][3] = 0
            self.outputs[2][4] = self.scada_client_control
            self.outputs[2][5] = self.scada_client_state
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

        # Stop the SCADA Server
        else:
            self.proc_state = 0                                                                     # Reset state machine

        loop_time = time.time() - s

    def set_inputs(self, inputs):
        if inputs[0] == self.uid:
            self.inputs = inputs[2:]                                                                # Shallow copy of entire system state
            
            self.enabled = self.inputs[1]

            # Control (module type 1)
            if self.inputs[ModTypes.CONTROL.value] is not None:
                for dev in self.inputs[ModTypes.CONTROL.value]:
                    if dev[1]:
                        if self.server_mode:                                                        # Forward on commands to the Control module in server mode
                            # System Enable Output
                            dev[2][2] = self.remote_operating_control
                            dev[2][4] = self.twos_comp_to_int(self.remote_real_power_command)
                            #print("SCADA Power = " + str(dev[2][4]) + " kW")
                        else:
                            self.remote_operating_state = dev[2][3]

            # Enable the inverters
            if self.inputs[ModTypes.INVERTER.value] is not None:
                for dev in self.inputs[ModTypes.INVERTER.value]:
                    
                    if dev[1]:                                                                      # Check Inverter Enabled state
                        if dev[2][1] == 2:  # Following
                            if self.proc_state == 1:
                                self.remote_real_power_command = self.twos_comp_to_int(self.mbtcp_server_registers[0x00].getValues(3, 257, 1)[0])                                
                                dev[2][8] = self.remote_real_power_command  # I've disabled Inverter control in the client for this test!!
                
            # Fuel Cell (module tyoe 14)
            if self.inputs[ModTypes.FUEL_CELL.value] is not None:
                for dev in self.inputs[ModTypes.FUEL_CELL.value]:
                    #if dev[1]:
                    if self.server_mode:                                                        # Forward on commands in server mode only
                        # Client Control
                        dev[2][11] = self.fuel_cell_kw_setpoint

            # Client (module type 24)
            if self.inputs[ModTypes.CLIENT.value] is not None:
                for dev in self.inputs[ModTypes.CLIENT.value]:
                    if dev[1]:
                        if self.server_mode:                                                        # Forward on commands in server mode only
                            # Client Control
                            dev[24][2][4] = self.client_reg7
                            dev[24][2][5] = self.client_reg8
                            dev[24][2][6] = self.client_reg9
                            dev[24][2][7] = self.client_reg10
                            dev[24][2][8] = self.client_reg11
                        #else
                        #    self.remote_operating_state = dev[2][3]    # This is similar to the control code above, reading the controller operating state. I'm not convinced it should be here at all! What were you thinking??
        return [SET_INPUTS_ACK, self.inputs] 

    def get_outputs(self):
        self.outputs[1] = self.enabled_echo

        #outputs = copy.deepcopy(self.outputs)

        return [GET_OUTPUTS_ACK, self.outputs]

    def set_page(self, page, form):                                                                       # Respond to GET/POST requests
        data = dict()

        if page == self.website + "_(" + str(self.uid) + ")":                                    # It was a page request for a numbered device (POST)

            # Save all control changes to database
            isButton = True

            for control in form:

                if "scada_override" in form:
                    self.actions.insert(0, Actions.CTRL_OVERRIDE_CHANGE.value)
                    self.override = not self.override
                elif "scada_server_mode" == control:
                    isButton = False
                    self.actions.insert(0, Actions.SERVER_MODE_SELECT.value)
                    self.server_mode = not self.server_mode
                    self.dbData["scada_server_mode"] = self.server_mode
                else:
                    isButton = False

                    if "scada_ipaddr_local" == control:
                        self.actions.insert(0, Actions.IP_ADDRESS_CHANGE.value)

                    self.dbData[control] = form[control]

            if isButton is False:  # Button press states are to be acted upon only, not stored
                self.save_to_db()

            # Let's just record the last 10 user interations for now
            if len(self.actions) >= 10:
                self.actions.pop()
            
            return [SET_PAGE_ACK]

        elif page == (self.website + "_(" + str(self.uid) + ")/data"):                           # It was a json data fetch quest (POST)

            mod_data = dict()

            # Controller Information
            mod_data["scada_name"] = self.name
            mod_data["scada_man"] = self.manufacturer
            mod_data["scada_fwver"] = self.version
            mod_data["scada_serno"] = self.serial
            mod_data["scada_override"] = self.override
            mod_data["scada_server_mode"] = self.server_mode
            mod_data["scada_constate"] = str(self.con_state).capitalize()

            # System-wide info to populate the configurator
            if self.server_mode:
                mod_data["scada_system"] = self.inputs                                            # Local System Parameters (Server-driven)
            else:
                mod_data["scada_system"] = self.inputs_remote                                     # Remote System Parameters (Client-driven)

            mod_data.update(self.dbData)  # I'm appending the dict here so we don't save unnecessary
            
            return [SET_PAGE_ACK, mod_data]  # data to the database.
        else:
            return [SET_PAGE_ACK, ('OK', 200)]                                                                            # Return the data to be jsonified

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
            print("ADAM DIGIO: " + str(e))
          
        try:
            if tx_msg is not None:
                queue[0].put(tx_msg, block=True)
                
        except Exception as e:
            print("ADAM DIGIO: " + str(e))
            
 
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


class Alarms(Enum):
    NONE = 0


class Faults(Enum):
    NONE = 0
    CONFIG = 1
    LOSS_OF_COMMS = 2


class Actions(Enum):
    NONE = 0
    IP_ADDRESS_CHANGE = 1
    CTRL_OVERRIDE_CHANGE = 2
    SERVER_MODE_SELECT = 3


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


if __name__ == '__main__':                                                                          # The module must be able to run solo for testing purposes
    pass
