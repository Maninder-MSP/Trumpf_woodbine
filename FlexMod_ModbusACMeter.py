# Modbus Interface - AC Meter Module

# A customer wishes to send their AC meter data to the client module of our SCADA interface.
# So this is a generic AC Meter which reads our own client module data instead of a physical peripheral and presents it to the system as if it were real.
# in a very similar way to our BACnet modules.


from threading import Thread, Event
from FlexMod import BaseModule, ModTypes
from FlexDB import FlexTinyDB
from enum import Enum
from pymodbus.client.sync import ModbusTcpClient as modbus_client
import copy

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


class Interval(Thread):
    def __init__(self, event, process, interval):
        Thread.__init__(self)
        self.stopped = event
        self.target = process
        self.interval = interval

    def run(self):
        while not self.stopped.wait(self.interval):
            self.target()


class Module():
    def __init__(self, uid, queue):
        super(Module, self).__init__()

        # General Module Data
        self.author = "Sophie Coates"
        self.uid = uid
        self.icon = "/static/images/ACmeter.png"
        self.name = "Modbus Bridged AC Meter"
        self.module_type = ModTypes.AC_METER.value
        self.manufacturer = "Multi Source Power"
        self.model = ""
        self.options = ""
        self.version = "-"
        self.serial = "-"
        self.website = "/Mod/FlexMod_ModbusACMeter"

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

    def process(self):

        self.heartbeat += 1
        if self.heartbeat >= 0xFFFF:
            self.heartbeat = 0
        self.ac_meter_heartbeat = self.heartbeat
        
        if self.tcp_timeout >= 5:
            self.tcp_timeout = 0
            self.tcp_client = None

        if self.tcp_client is None:
            self.con_state = False
            if "ac_meter_ipaddr_local" in self.dbData:
                if self.dbData["ac_meter_ipaddr_local"] != "0.0.0.0":

                    try:
                        self.tcp_client = modbus_client(self.dbData["ac_meter_ipaddr_local"], port=502)     # Retrieve the data we need from our own SCAD interface, using 127.0.0.1

                        if self.tcp_client.connect() is True:
                            self.update_faults(Faults.LOSS_OF_COMMS.value, False)  # Clear Fault
                            self.set_state_text(State.CONNECTED)
                            self.con_state = True
                        else:
                            self.update_faults(Faults.LOSS_OF_COMMS.value, True)  # Raise Fault
                            self.set_state_text(State.CONNECTING)
                            self.tcp_client = None

                    except Exception as e:
                        print("Modbus AC Meter: " + str(e))
                        self.update_faults(Faults.LOSS_OF_COMMS.value, True)
                        self.set_state_text(State.CONNECTING)
                else:
                    self.update_faults(Faults.LOSS_OF_COMMS.value, True)  # Raise Fault
                    self.set_state_text(State.CONFIG)
            else:
                self.update_faults(Faults.LOSS_OF_COMMS.value, True)
                self.set_state_text(State.CONFIG)
        else:   # Main process loop

            # Basic Metering
            try:
                rr = self.tcp_client.read_holding_registers(6013, 2, unit=1)

                if rr.isError():
                    self.tcp_timeout += 1
                    return
                else:
                    if len(rr.registers) >= 2:
                        self.tcp_timeout = 0
                        self.ac_meter_total_active_power = self.twos_comp_to_int(rr.registers[0]) / 10
                        self.ac_meter_power_factor = self.twos_comp_to_int(rr.registers[1]) / 10

            except Exception as e:
                print("Modbus AC Meter: " + str(e))
                self.tcp_timeout += 1
                return

            # There is little to do beyond accepting requests
            if self.enabled:
                self.enabled_echo = True
            else:
                self.enabled_echo = False

            if len(self.actions) == 0:
                self.actions.append(0)  # Dummy

            # Modify self.outputs
            self.outputs[2][0] = self.ac_meter_heartbeat
            self.outputs[2][1] = 0
            self.outputs[2][2] = 0
            self.outputs[2][3] = 0
            self.outputs[2][4] = 0
            self.outputs[2][5] = 0
            self.outputs[2][6] = self.ac_meter_power_factor
            self.outputs[2][7] = self.ac_meter_total_active_power
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

            # These three are used primarily to mitigate the load balancing issues we've experienced on REFU systems
            mod_data["ac_meter_phase_a_power"] = 0
            mod_data["ac_meter_phase_b_power"] = 0
            mod_data["ac_meter_phase_c_power"] = 0
            
            mod_data.update(self.dbData)                                                       # I'm appending the dict here so we don't save unnecessary
            
            return [SET_PAGE_ACK, mod_data]                                                                    # data to the database.

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
    
    def save_to_db(self):
        
        try:
            db.save_to_db(__name__ + "_(" + str(self.uid) + ")", self.dbData)
        except:
            print("Unable to save record, may already exist?")  # Todo find error code from tinydb

    def twos_comp_to_int(self, twoC):
        return twoC if not twoC & 0x8000 else -(0xFFFF - twoC + 1)

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
            print("Modbus AC Meter: " + str(e))
          
        try:
            if tx_msg is not None:
                queue[0].put(tx_msg, block=True)
                
        except Exception as e:
            print("Modbus AC Meter: " + str(e))
            
 
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
