# Nexceris - Li-Ion Tamer Module

import sys
from pymodbus.client.sync import ModbusTcpClient as mb_tcp_client
from threading import Thread, Event
from FlexDB import FlexTinyDB
from enum import Enum
from datetime import datetime
import copy
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
        self.uid = uid                                                                              # A unique identifier passed from the HMI via a mangled URL
        self.icon = "/static/images/LiIon.png"
        self.name = "Li-Ion Tamer"
        self.module_type = ModTypes.LI_ION.value
        self.manufacturer = "Nexceris"
        self.model = ""
        self.options = ""
        self.version = "-"
        self.serial = "-"                                                                            # This can be replaced with the device serial number later
        self.website = "/Mod/FlexMod_NexcerisLiIonTamer"                                       # This is the template name itself
        self.hw_gen = "gen2"

        # Run state
        self.con_state = False
        self.state = "Idle"
        self.set_state_text(State.IDLE)

        # Non-Volatile Data (Loaded from DB)
        self.dbData = db.fetch_from_db(__name__ + "_(" + str(self.uid) + ")")
        if self.dbData is None:
            self.dbData = dict()
            self.dbData["li_ion_ipaddr_local"] = "0.0.0.0"

        # Volatile Data
        self.tcp_client = None
        self.tcp_timeout = 0
        self.enabled = False
        self.enabled_echo = False
        self.override = False
        self.inputs = [self.uid, False, [False]*24]
        self.outputs = [self.uid, self.enabled, [0]*150]                                             # A few extra for supplemental data from Li-Ion Tamer
        self.heartbeat = 0
        self.heartbeat_echo = 0
        self.system = [False]*4
        self.refs = [False]*3
        self.dev_hb = 0
        self.dev_hb_echo = 0

        # Device Volatiles
        self.li_ion_quantity = 1
        self.li_ion_heartbeat = 0

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

        print("Starting " + self.name + " with UID " + str(self.uid))

    def process(self):
        global loop_time
        #print("(10)   LiIon Heartbeat: " + str(self.heartbeat) + ", loop time: " + str(loop_time) + " Seconds")
        
        s = time.time()

        self.heartbeat += 1
        if self.heartbeat >= 0xFFFF:
            self.heartbeat = 0

        if self.tcp_timeout >= 5:
            self.tcp_timeout = 0
            self.tcp_client = None

        if self.tcp_client is None:

            self.con_state = False
            if "li_ion_ipaddr_local" in self.dbData:
                if self.dbData["li_ion_ipaddr_local"] != "0.0.0.0":
                    try:
                        if "li_ion_hw_gen" in self.dbData:
                            if self.dbData["li_ion_hw_gen"] == "gen2":
                                self.hw_gen = "gen2"
                                self.tcp_client = mb_tcp_client(self.dbData["li_ion_ipaddr_local"], port=502)
                            else:
                                self.hw_gen = "gen3"
                                self.tcp_client = mb_tcp_client(self.dbData["li_ion_ipaddr_local"], port=5020)

                        if self.tcp_client.connect() is True:
                            self.update_faults(Faults.LOSS_OF_COMMS.value, False)               # Clear Fault
                            self.set_state_text(State.CONNECTED)
                            self.con_state = True
                        else:
                            self.update_faults(Faults.LOSS_OF_COMMS.value, True)                # Raise Fault
                            self.set_state_text(State.CONNECTING)
                            self.tcp_client = None
                            return

                    except Exception as e:
                        print("Li-Ion Tamer: " + str(e))
                        self.update_faults(Faults.LOSS_OF_COMMS.value, True)
                        self.set_state_text(State.CONNECTING)
                else:
                    self.update_faults(Faults.LOSS_OF_COMMS.value, True)  # Raise Fault
                    self.set_state_text(State.CONFIG)
            else:
                self.update_faults(Faults.LOSS_OF_COMMS.value, True)
                self.set_state_text(State.CONFIG)
        else:

            if "li_ion_hw_gen" in self.dbData:
                if self.dbData["li_ion_hw_gen"] == "gen2":
                    self.hw_gen = "gen2"
                    try:
                        rr = self.tcp_client.read_input_registers(5, 1, unit=1, timeout=0.25)
                        if rr.isError():
                            self.tcp_timeout += 1
                            return
                        else:
                            self.tcp_timeout = 0
                            self.dev_hb = rr.registers[0]
                    except Exception as e:
                        print("Li-Ion Tamer: " + str(e))
                        self.tcp_timeout += 1
                        return

                    try:
                        rr = self.tcp_client.read_coils(0, 60, unit=1, timeout=0.25)
                        if rr.isError():
                            self.tcp_timeout += 1
                            return
                        else:
                            # Check for Sensor Alarms and Errors
                            self.tcp_timeout = 0
                            for x in range(12):
                                # Alarms
                                if rr.bits[x] is True:
                                    self.update_alarms(Alarms["SENSOR" + str(x+1) + "_ALARM"].value, True)
                                else:
                                    self.update_alarms(Alarms["SENSOR" + str(x+1) + "_ALARM"].value, False)

                                # Errors
                                if rr.bits[x+17] is True:
                                    self.update_warnings(Warnings["SENSOR" + str(x+1) + "_WARNING"].value, True)
                                else:
                                    self.update_warnings(Warnings["SENSOR" + str(x+1) + "_WARNING"].value, False)

                            # Check for Reference Errors
                            for x in range(3):
                                if rr.bits[x+29] is True:
                                    self.update_warnings(Warnings["REF" + str(x+1) + "_WARNING"].value, True)
                                else:
                                    self.update_warnings(Warnings["REF" + str(x+1) + "_WARNING"].value, False)

                            # Check for Any Alarm
                            if rr.bits[15] is True:
                                self.update_alarms(Alarms["SENSOR_ANY_ALARM"].value, True)
                            else:
                                self.update_alarms(Alarms["SENSOR_ANY_ALARM"].value, False)

                            # Check for Sensor Error
                            if rr.bits[16] is True:
                                self.update_warnings(Warnings["SENSOR_ANY_WARNING"].value, True)
                            else:
                                self.update_warnings(Warnings["SENSOR_ANY_WARNING"].value, False)

                            # Check for System Alarm
                            if rr.bits[58] is True:
                                self.update_alarms(Alarms["SYSTEM_ALARM"].value, True)
                            else:
                                self.update_alarms(Alarms["SYSTEM_ALARM"].value, False)

                            # Check the heartbeat is still running
                            if self.dev_hb - self.dev_hb_echo > 5:
                                self.update_alarms(Alarms["HEARTBEAT"].value, True)
                            else:
                                self.update_alarms(Alarms["HEARTBEAT"].value, False)
                            self.dev_hb_echo = self.dev_hb

                            if len(self.actions) == 0:
                                self.actions.append(0)  # Dummy

                            self.li_ion_heartbeat = self.dev_hb

                            # Modify self.outputs
                            self.outputs[2][0] = self.li_ion_heartbeat
                            self.outputs[2][1] = 0
                            self.outputs[2][2] = 7
                            self.outputs[2][3] = rr.bits[0]
                            self.outputs[2][4] = rr.bits[1]
                            self.outputs[2][5] = rr.bits[2]
                            self.outputs[2][6] = rr.bits[3]
                            self.outputs[2][7] = rr.bits[4]
                            self.outputs[2][8] = rr.bits[5]
                            self.outputs[2][9] = rr.bits[6]
                            self.outputs[2][10] = rr.bits[7]
                            self.outputs[2][11] = rr.bits[8]
                            self.outputs[2][12] = rr.bits[9]
                            self.outputs[2][13] = rr.bits[10]
                            self.outputs[2][14] = rr.bits[11]
                            self.outputs[2][15] = rr.bits[15]
                            self.outputs[2][16] = rr.bits[16]
                            self.outputs[2][17] = rr.bits[58]
                            self.outputs[2][18] = 0
                            self.outputs[2][19] = 0
                            self.outputs[2][20] = self.warnings
                            self.outputs[2][21] = self.alarms
                            self.outputs[2][22] = self.faults
                            self.outputs[2][23] = self.actions[0]
                            # Sensor Errors (This is additional to the spec only because the page already supported this data)
                            self.outputs[2][24] = rr.bits[17]
                            self.outputs[2][25] = rr.bits[18]
                            self.outputs[2][26] = rr.bits[19]
                            self.outputs[2][27] = rr.bits[20]
                            self.outputs[2][28] = rr.bits[21]
                            self.outputs[2][29] = rr.bits[22]
                            self.outputs[2][30] = rr.bits[23]
                            self.outputs[2][31] = rr.bits[24]
                            self.outputs[2][32] = rr.bits[25]
                            self.outputs[2][33] = rr.bits[26]
                            self.outputs[2][34] = rr.bits[27]
                            self.outputs[2][35] = rr.bits[28]
                            # Reference states
                            self.outputs[2][36] = rr.bits[29]
                            self.outputs[2][37] = rr.bits[30]
                            self.outputs[2][38] = rr.bits[31]


                    except Exception as e:
                        print("Li-Ion Tamer: " + str(e))
                        self.tcp_timeout += 1
                        return

                elif self.dbData["li_ion_hw_gen"] == "gen3":
                    self.hw_gen = "gen3"
                    # So first what we want to do is determine how many sensors there are
                    sensor_qty = 0
                    try:
                        rr = self.tcp_client.read_input_registers(1, 1, unit=1, timeout=0.25)
                        if rr.isError():
                            self.tcp_timeout += 1
                            return
                        else:
                            self.tcp_timeout = 0

                            sensor_qty = rr.registers[0]

                    except Exception as e:
                        print("Li-Ion Tamer: " + str(e))
                        self.tcp_timeout += 1

                    # Currently supporting up to 12 sensors as per Gen 2
                    sensor_id = [0] * 12
                    sensor_temp = [0] * 12
                    sensor_humidity = [0] * 12
                    sensor_scalar = [0] * 12
                    sensor_state = [0] * 12                                                         # Compatibility with Gen 2 output data
                    sensor_alarm_any = False
                    sensor_error_any = False
                    system_alarm = False

                    # Hard stop for now. We are unlikely to have more than 10 for the time being...
                    # Li-Ion Tamer also has this fun feature of reporting unusable values when the network interface is unreliable
                    if sensor_qty > 12:
                        sensor_qty = 0
                    else:
                        for x in range(sensor_qty):
                            try:
                                rr = self.tcp_client.read_input_registers(15+(5*x), 5, unit=1, timeout=0.25)
                                if rr.isError():
                                    self.tcp_timeout += 1
                                    return
                                else:
                                    sensor_id[x] = rr.registers[0]
                                    sensor_temp[x] = "{:.2f}".format(self.half_prec_to_float(rr.registers[1]))
                                    sensor_humidity[x] = "{:.2f}".format(self.half_prec_to_float(rr.registers[2]))
                                    sensor_scalar[x] = "{:.2f}".format(self.half_prec_to_float(rr.registers[3]))
                                    sensor_state[x] = rr.registers[4]

                                    if sensor_state[x] & 0x01:
                                        sensor_alarm_any = True

                                    if sensor_state[x] & 0x02:
                                        sensor_error_any = True

                                    self.tcp_timeout = 0

                            except Exception as e:
                                print("Li-Ion Tamer: " + str(e))
                                self.tcp_timeout += 1
                                return

                        try:
                            rr = self.tcp_client.read_coils(2, 1, unit=1, timeout=0.25)

                            if rr.isError():
                                self.tcp_timeout += 1
                                return
                            else:
                                self.tcp_timeout = 0
                                system_alarm = rr.bits[0]

                            self.tcp_timeout = 0
                        except Exception as e:
                            print("Li-Ion Tamer: " + str(e))
                            self.tcp_timeout += 1
                            return

                    self.tcp_timeout = 0

                    if len(self.actions) == 0:
                        self.actions.append(0)  # Dummy

                    # Modify self.outputs (Note: outputs[2][0-23] must stay in this format because other modules expect it as per Gen 2. After that, we can append any Gen3 data we want to)
                    self.outputs[2][0] = self.heartbeat
                    self.outputs[2][1] = 0
                    self.outputs[2][2] = sensor_qty
                    self.outputs[2][3] = True if sensor_state[0] & 0x01 else False
                    self.outputs[2][4] = True if sensor_state[1] & 0x01 else False
                    self.outputs[2][5] = True if sensor_state[2] & 0x01 else False
                    self.outputs[2][6] = True if sensor_state[3] & 0x01 else False
                    self.outputs[2][7] = True if sensor_state[4] & 0x01 else False
                    self.outputs[2][8] = True if sensor_state[5] & 0x01 else False
                    self.outputs[2][9] = True if sensor_state[6] & 0x01 else False
                    self.outputs[2][10] = True if sensor_state[7] & 0x01 else False
                    self.outputs[2][11] = True if sensor_state[8] & 0x01 else False
                    self.outputs[2][12] = True if sensor_state[9] & 0x01 else False
                    self.outputs[2][13] = True if sensor_state[10] & 0x01 else False
                    self.outputs[2][14] = True if sensor_state[11] & 0x01 else False
                    self.outputs[2][15] = sensor_alarm_any
                    self.outputs[2][16] = sensor_error_any
                    self.outputs[2][17] = system_alarm
                    self.outputs[2][18] = 0
                    self.outputs[2][19] = 0
                    self.outputs[2][20] = self.warnings
                    self.outputs[2][21] = self.alarms
                    self.outputs[2][22] = self.faults
                    self.outputs[2][23] = self.actions[0]

                    # Sensor Errors (This is additional to the spec only because the page already supported this data)
                    self.outputs[2][24] = True if sensor_state[0] & 0x02 else False
                    self.outputs[2][25] = True if sensor_state[1] & 0x02 else False
                    self.outputs[2][26] = True if sensor_state[2] & 0x02 else False
                    self.outputs[2][27] = True if sensor_state[3] & 0x02 else False
                    self.outputs[2][28] = True if sensor_state[4] & 0x02 else False
                    self.outputs[2][29] = True if sensor_state[5] & 0x02 else False
                    self.outputs[2][30] = True if sensor_state[6] & 0x02 else False
                    self.outputs[2][31] = True if sensor_state[7] & 0x02 else False
                    self.outputs[2][32] = True if sensor_state[8] & 0x02 else False
                    self.outputs[2][33] = True if sensor_state[9] & 0x02 else False
                    self.outputs[2][34] = True if sensor_state[10] & 0x02 else False
                    self.outputs[2][35] = True if sensor_state[11] & 0x02 else False
                    # Reference states
                    self.outputs[2][36] = 0
                    self.outputs[2][37] = 0
                    self.outputs[2][38] = 0
                    # Sensor IDs State
                    self.outputs[2][39] = sensor_id[0]
                    self.outputs[2][40] = sensor_id[1]
                    self.outputs[2][41] = sensor_id[2]
                    self.outputs[2][42] = sensor_id[3]
                    self.outputs[2][43] = sensor_id[4]
                    self.outputs[2][44] = sensor_id[5]
                    self.outputs[2][45] = sensor_id[6]
                    self.outputs[2][46] = sensor_id[7]
                    self.outputs[2][47] = sensor_id[8]
                    self.outputs[2][48] = sensor_id[9]
                    self.outputs[2][49] = sensor_id[10]
                    self.outputs[2][50] = sensor_id[11]
                    # Reserved
                    self.outputs[2][51] = 0
                    self.outputs[2][52] = 0
                    self.outputs[2][53] = 0
                    # Sensor Temperatures
                    self.outputs[2][54] = sensor_temp[0]
                    self.outputs[2][55] = sensor_temp[1]
                    self.outputs[2][56] = sensor_temp[2]
                    self.outputs[2][57] = sensor_temp[3]
                    self.outputs[2][58] = sensor_temp[4]
                    self.outputs[2][59] = sensor_temp[5]
                    self.outputs[2][60] = sensor_temp[6]
                    self.outputs[2][61] = sensor_temp[7]
                    self.outputs[2][62] = sensor_temp[8]
                    self.outputs[2][63] = sensor_temp[9]
                    self.outputs[2][64] = sensor_temp[10]
                    self.outputs[2][65] = sensor_temp[11]
                    # Reserved
                    self.outputs[2][66] = 0
                    self.outputs[2][67] = 0
                    self.outputs[2][68] = 0
                    # Sensor Humidities
                    self.outputs[2][69] = sensor_humidity[0]
                    self.outputs[2][70] = sensor_humidity[1]
                    self.outputs[2][71] = sensor_humidity[2]
                    self.outputs[2][72] = sensor_humidity[3]
                    self.outputs[2][73] = sensor_humidity[4]
                    self.outputs[2][74] = sensor_humidity[5]
                    self.outputs[2][75] = sensor_humidity[6]
                    self.outputs[2][76] = sensor_humidity[7]
                    self.outputs[2][77] = sensor_humidity[8]
                    self.outputs[2][78] = sensor_humidity[9]
                    self.outputs[2][79] = sensor_humidity[10]
                    self.outputs[2][80] = sensor_humidity[11]
                    # Reserved
                    self.outputs[2][81] = 0
                    self.outputs[2][82] = 0
                    self.outputs[2][83] = 0
                    # Sensor Humidities
                    self.outputs[2][84] = sensor_scalar[0]
                    self.outputs[2][85] = sensor_scalar[1]
                    self.outputs[2][86] = sensor_scalar[2]
                    self.outputs[2][87] = sensor_scalar[3]
                    self.outputs[2][88] = sensor_scalar[4]
                    self.outputs[2][89] = sensor_scalar[5]
                    self.outputs[2][90] = sensor_scalar[6]
                    self.outputs[2][91] = sensor_scalar[7]
                    self.outputs[2][92] = sensor_scalar[8]
                    self.outputs[2][93] = sensor_scalar[9]
                    self.outputs[2][94] = sensor_scalar[10]
                    self.outputs[2][95] = sensor_scalar[11]
                    # Reserved
                    self.outputs[2][96] = 0
                    self.outputs[2][97] = 0
                    self.outputs[2][98] = 0
                    # Sensor state bits (zone ID, active state, alarms, errors)
                    self.outputs[2][99] = sensor_state[0]
                    self.outputs[2][100] = sensor_state[1]
                    self.outputs[2][101] = sensor_state[2]
                    self.outputs[2][102] = sensor_state[3]
                    self.outputs[2][103] = sensor_state[4]
                    self.outputs[2][104] = sensor_state[5]
                    self.outputs[2][105] = sensor_state[6]
                    self.outputs[2][106] = sensor_state[7]
                    self.outputs[2][107] = sensor_state[8]
                    self.outputs[2][108] = sensor_state[9]
                    self.outputs[2][109] = sensor_state[10]
                    self.outputs[2][110] = sensor_state[11]
                    # Reserved
                    self.outputs[2][111] = 0
                    self.outputs[2][112] = 0
                    self.outputs[2][113] = 0

            # Confirm we are running. No other non-critical tasks
            if self.enabled:
                self.enabled_echo = True
            else:
                self.enabled_echo = False

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
        return [GET_OUTPUTS_ACK, self.outputs]

    def set_page(self, page, form):                                                                       # Respond to GET/POST requests

        if page == self.website + "_(" + str(self.uid) + ")":  # It was a page request for a numbered device (POST)

            # Save all control changes to database
            isButton = True
            for control in form:
                if "li_ion_override" in form:

                    self.actions.insert(0, Actions.CTRL_OVERRIDE_CHANGE.value)
                    self.override = not self.override

                else:
                    isButton = False

                    if "li_ion_ipaddr_local" == control:
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

            # Clear old lists
            #self.actions = []

            # Controller Information
            mod_data["li_ion_name"] = self.name
            mod_data["li_ion_man"] = self.manufacturer
            mod_data["li_ion_fwver"] = self.version
            mod_data["li_ion_serial"] = self.serial
            mod_data["li_ion_constate"] = str(self.con_state).capitalize()
            mod_data["li_ion_override"] = self.override
            mod_data["li_ion_data"] = self.outputs[2]
            mod_data["li_ion_hwgen"] = self.hw_gen

            mod_data.update(self.dbData)  # I'm appending the dict here so we don't save unnecessary
            
            return [SET_PAGE_ACK, mod_data]  # data to the database.

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

    def half_prec_to_float(self, input):
        # Ok this is going to get nasty but here's how Nexceris do it...
        test = input
        sign = pow(-1, (test >> 15) & 0x01)
        exp_val = (test >> 10) & 0x1F

        exponent = 0
        # There are 5 bits in the Biased Exponent
        for y in range(5):
            exponent += pow(2, y) if (exp_val >> y) & 0x01 else 0

        bias = pow(2, (5 - 1)) - 1
        biased_exponent = exponent - bias

        fraction = 0
        for f in range(10):
            fraction += pow(2, -(10 - f)) if (test >> f) & 0x01 else 0

        hp_val = sign * pow(2, biased_exponent) * (pow(2, 0) + fraction)
        return hp_val

    def save_to_db(self):
        
        try:
            db.save_to_db(__name__ + "_(" + str(self.uid) + ")", self.dbData)
        except Exception as e:
            print("MSP LOGO: " + str(e))
            print("Unable to save record, may already exist?")  # Todo find error code from tinydb

    def kill(self):
        self.stop.set()                                                                             # Tells the timer thread to cease operation


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
            print("Li-Ion Tamer: " + str(e))
          
        try:
            if tx_msg is not None:
                queue[0].put(tx_msg, block=True)
                
        except Exception as e:
            print("Li-Ion Tamer: " + str(e))
            
 
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
    NONE = 0                                                                                        # "Warning: Li-Ion Tamer - No Warning present"
    SENSOR1_WARNING = 1                                                                             # "Warning: Li-Ion Tamer - Sensor 1"
    SENSOR2_WARNING = 2                                                                             # "Warning: Li-Ion Tamer - Sensor 2"
    SENSOR3_WARNING = 3                                                                             # "Warning: Li-Ion Tamer - Sensor 3"
    SENSOR4_WARNING = 4                                                                             # "Warning: Li-Ion Tamer - Sensor 4"
    SENSOR5_WARNING = 5                                                                             # "Warning: Li-Ion Tamer - Sensor 5"
    SENSOR6_WARNING = 6                                                                             # "Warning: Li-Ion Tamer - Sensor 6"
    SENSOR7_WARNING = 7                                                                             # "Warning: Li-Ion Tamer - Sensor 7"
    SENSOR8_WARNING = 8                                                                             # "Warning: Li-Ion Tamer - Sensor 8"
    SENSOR9_WARNING = 9                                                                             # "Warning: Li-Ion Tamer - Sensor 9"
    SENSOR10_WARNING = 10                                                                           # "Warning: Li-Ion Tamer - Sensor 10"
    SENSOR11_WARNING = 11                                                                           # "Warning: Li-Ion Tamer - Sensor 11"
    SENSOR12_WARNING = 12                                                                           # "Warning: Li-Ion Tamer - Sensor 12"

    SENSOR_ANY_WARNING = 13                                                                         # "Alarm: Li-Ion Tamer - Sensor Error"
    REF1_WARNING = 14                                                                               # "Warning: Li-Ion Tamer - Reference 1"
    REF2_WARNING = 15                                                                               # "Warning: Li-Ion Tamer - Reference 2"
    REF3_WARNING = 16                                                                               # "Warning: Li-Ion Tamer - Reference 3"


class Alarms(Enum):
    NONE = 0                                                                                        # "Alarm: Li-Ion Tamer - No Alarm present"
    SENSOR1_ALARM = 1                                                                               # "Alarm: Li-Ion Tamer - Sensor 1"
    SENSOR2_ALARM = 2                                                                               # "Alarm: Li-Ion Tamer - Sensor 2"
    SENSOR3_ALARM = 3                                                                               # "Alarm: Li-Ion Tamer - Sensor 3"
    SENSOR4_ALARM = 4                                                                               # "Alarm: Li-Ion Tamer - Sensor 4"
    SENSOR5_ALARM = 5                                                                               # "Alarm: Li-Ion Tamer - Sensor 5"
    SENSOR6_ALARM = 6                                                                               # "Alarm: Li-Ion Tamer - Sensor 6"
    SENSOR7_ALARM = 7                                                                               # "Alarm: Li-Ion Tamer - Sensor 7"
    SENSOR8_ALARM = 8                                                                               # "Alarm: Li-Ion Tamer - Sensor 8"
    SENSOR9_ALARM = 9                                                                               # "Alarm: Li-Ion Tamer - Sensor 9"
    SENSOR10_ALARM = 10                                                                             # "Alarm: Li-Ion Tamer - Sensor 10"
    SENSOR11_ALARM = 11                                                                             # "Alarm: Li-Ion Tamer - Sensor 11"
    SENSOR12_ALARM = 12                                                                             # "Alarm: Li-Ion Tamer - Sensor 12"

    SENSOR_ANY_ALARM = 13                                                                           # "Alarm: Li-Ion Tamer Sensor Any"
    SYSTEM_ALARM = 14                                                                               # "Alarm: Li-Ion Tamer System"
    HEARTBEAT = 15                                                                                  # "Alarm: Li-Ion Tamer Heartbeat"


class Faults(Enum):
    NONE = 0                                                                                        # "Fault: Li-Ion Tamer - No Fault present"
    CONFIG = 1                                                                                      # "Fault: Li-Ion Tamer - Configuration"
    LOSS_OF_COMMS = 2                                                                               # "Fault: Li-Ion Tamer - Loss of Comms"


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


if __name__ == '__main__':                                                                          # The module must be able to run solo for testing purposes
    pass
