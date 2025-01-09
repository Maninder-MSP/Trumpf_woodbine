# FlexMod_TrumpfInverter.py, Controls and monitors a Trumpf TruConvert AC 3025 50kW Inverter

# TODO: Replace Faults / Warnings / Errors / Actions with specific codes
#  1) Improve speed of commandeed power. Currently just under 5 seconds.

from pymodbus.client.sync import ModbusTcpClient as mb_tcp_client
from threading import Thread, Event
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
        self.author = "Maninder Grewal"
        self.uid = uid
        self.icon = "/static/images/Inverter.png"
        self.name = "Inverter"
        self.module_type = ModTypes.INVERTER.value
        self.manufacturer = "Trumpf"
        self.model = ""
        self.options = ""
        self.version = 0
        self.serial = ""
        self.website = "/Mod/FlexMod_TrumpfInverter"  # This is the template name itself

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
        self.outputs = [self.uid, self.enabled, [0]*35]
        self.heartbeat = 0
        self.heartbeat_echo = 0
        self.override = False

        self.inverter_quantity = 1                                                                  # Always 1 at module level, may be greater at site level
        self.inverter_heartbeat = 0
        self.inverter_operating_state = 0
        self.inverter_number_slaves = 0
        self.inverter_frequency = 0
        self.inverter_faults1 = 0
        self.inverter_warnings1 = 0
        self.inverter_ac_rms_phase_voltage = 0
        self.inverter_apparent_power = 0
        self.inverter_apparent_power_L1 = 0
        self.inverter_apparent_power_L2 = 0
        self.inverter_apparent_power_L3 = 0

        self.inverter_command_apparent_power = 0
        self.inverter_command_apparent_power_L1 = 0
        self.inverter_command_apparent_power_L2 = 0
        self.inverter_command_apparent_power_L3 = 0

        self.inverter_commanded_apparent_power = 0
        self.inverter_commanded_apparent_power_L1 = 0
        self.inverter_commanded_apparent_power_L2 = 0
        self.inverter_commanded_apparent_power_L3 = 0


        self.inverter_commanded_real_power = 0
        self.inverter_real_power = 0
        self.inverter_commanded_reactive_power = 0
        self.inverter_reactive_power = 0
        self.inverter_commanded_real_current = 0
        self.inverter_ac_rms_phase_current = 0
        self.inverter_commanded_reactive_current = 0
        self.inverter_reactive_current = 0
        self.inverter_commanded_input_current = 0
        self.inverter_dc_voltage = 0
        self.inverter_dc_current = 0
        self.inverter_input_current = 0
        self.inverter_input_voltage = 0
        self.inverter_input_power = 0
        self.inverter_operating_mode = 0

        self.inverter_avg_module_temperature = 0
        self.inverter_inlet_air_temperature = 0
        self.inverter_module_balancer_temperature = 0
        self.inverter_fan_speed = 0


        self.auto_fault_clear = "Manual"
        self.manual_fault_clear = False

        self.alert_timeout = 5
        self.fault_clear_timeout = 15
        self.fault_clear_timeout_counter = 0
        self.warning_timeout_counter = 0
        self.alarm_timeout_counter = 0
        self.fault_timeout_counter = 0

        # Inverter registers
        self.inv_conn = 0                                                                          # Enable the inverter
        self.inv_opt = 'ASYM'                                                                           # Opteration of Inverter
        self.inv_reset = 0                                                                         # reset the alarm
        self.inv_rst_alm = False

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

        print("Starting " + self.name + " with UID " + str(self.uid))

    def process(self):
        global loop_time
        # print("(3) Inverter Heartbeat: " + str(self.heartbeat) + ", loop time: " + str(loop_time) + " Seconds")

        s = time.time()

        self.heartbeat += 1
        if self.heartbeat >= 0xFFFF:
            self.heartbeat = 0
        self.inverter_heartbeat = self.heartbeat

        if self.tcp_timeout == 5:
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
            # print('proscess running Trumpf')
            # print(self.inv_opt)
            # print(self.inverter_command_apparent_power , self.inverter_command_apparent_power_L1)
            # pass

            # Read the live operating mode of inverter
            try:
                rr = self.tcp_client.read_holding_registers(5000, 2, timeout=0.25)
                if rr.isError():
                    self.tcp_timeout += 1
                    return
                else:
                    # State
                    self.inverter_operating_state = rr.registers[0]

                    # Number Of Connected Slaves
                    self.inverter_number_slaves = rr.registers[1]

                    self.tcp_timeout = 0

            except Exception as e:
                print("Inverter: " + str(e))
                self.tcp_timeout += 1
                return

            # Inverter control and operation
            # Reset the 1st 3 coils
            try:
                rr = self.tcp_client.read_coils(4000, 3, timeout=0.25)
                # print(rr.bits)

                if rr.isError():
                    self.tcp_timeout += 1
                    return
                else:
                    #
                    # "Power stage configuration: 1 = power stage on; 0 = power stage off;"
                    # "Configuration AC set values for phases L1 - L3: 1 = symmetric; 0 = asymmetric (individual configuration possible);"
                    # 1) Resets current alarm and warning messages

                    
                    # Set the Operational Mode to 0 = asymmetric
                    if self.override == True:
                        if self.inv_opt == 'ASYM' and rr.bits[1] is True:
                            try:
                                # Reset command power for SYM = 0 (if any)

                                self.tcp_client.write_coil(4001, 0, timeout=0.25)
                                print('Inverter set to ASYM under override')
                                self.inv_opt = 'ASYM'
                                self.tcp_timeout = 0
                            except Exception as e:
                                print("Inverter: " + str(e))
                                self.tcp_timeout += 1
                                return
                        elif self.inv_opt == 'SYM' and rr.bits[1] is False:
                            try:
                                self.tcp_client.write_coil(4001, 1, timeout=0.25)
                                print('Inverter set to SYM under override')
                                self.inv_opt = 'SYM'
                                self.tcp_timeout = 0
                            except Exception as e:
                                print("Inverter: " + str(e))
                                self.tcp_timeout += 1
                                return
                    else:
                        # Set by default values at StartUp
                        # 1) it set to ASYM.
                        if rr.bits[1] is True:
                            try:
                                self.tcp_client.write_coil(4001, 0, timeout=0.25)
                                print('Inverter set to ASYM First time')
                                self.inv_opt = 'ASYM'
                                self.tcp_client.write_coil(4011, 1, timeout=0.25)
                                print('Activated_SlaveAddressing_Over_Modbus_SlaveId')
                                self.tcp_timeout = 0
                            except Exception as e:
                                print("Inverter: " + str(e))
                                self.tcp_timeout += 1
                                return

                    # Operational Mode
                    op_mode = rr.bits[0]

                    if op_mode != self.inverter_operating_mode:
                        try:
                            self.tcp_client.write_coil(4000, self.inverter_operating_mode, timeout=0.25)
                            self.tcp_timeout = 0
                        except:
                            self.tcp_timeout += 1
                            return


                    self.tcp_timeout = 0

            except Exception as e:
                print("Inverter: " + str(e))
                self.tcp_timeout += 1
                return

            # If no error enable the inverter?
            # Inverter control and operation
            try:

                # make sure inv is in asymmetric, and reset alarms (DONE)
                if self.inv_conn:
                    # if self.inv_opt == 'ASYM':                                        # asymmetric enabled ?
                        if self.inverter_operating_state != 3:
                            self.inverter_command_apparent_power = 0                  # Force zero power before starting
                            self.inverter_command_apparent_power_L1 = 0               # Force zero power before starting
                            self.inverter_command_apparent_power_L2 = 0               # Force zero power before starting
                            self.inverter_command_apparent_power_L3 = 0               # Force zero power before starting
                            if self.inverter_operating_state == 0:                    # PowerUP
                                pass
                            elif self.inverter_operating_state == 1:                  # Error
                                # Reset the alarm/Error right now its manually
                                pass
                            elif self.inverter_operating_state == 2:                  # Idle
                                self.inverter_operating_mode = 1                      # ActivatePowerStage Coil Enabled
                            elif self.inverter_operating_state == 3:                  # Operation/RUN
                                pass
                            elif self.inverter_operating_state == 4:                  # Maintenance
                                pass

                        else:
                            self.enabled_echo = True
                else:
                    if self.inverter_operating_state == 3:                            # Already in Operation
                        self.inverter_operating_mode = 0                              # ActivatePowerStage Coil Disabled
                        # # TODO Reduce power (gracefully)
                        self.inverter_command_apparent_power = 0
                        self.inverter_command_apparent_power_L1 = 0
                        self.inverter_command_apparent_power_L2 = 0
                        self.inverter_command_apparent_power_L3 = 0
                    else:
                        self.enabled_echo = False
                self.tcp_timeout = 0

            except Exception as e:
                print("Inverter: " + str(e))
                self.tcp_timeout += 1
                return



            # power command
            try:
                rr = self.tcp_client.read_holding_registers(4195, 4, timeout=0.25)
                if rr.isError():
                    self.tcp_timeout += 1
                    return
                else:


                    # Faults
                    # self.faults = rr.registers[10]

                    # Power Limit for the 25kW inv.

                    # Prevent requesting power beyond REFU power limit
                    new_power = int(self.inverter_command_apparent_power * 1000 / self.inverter_number_slaves)
                    new_power_L1 = int(self.inverter_command_apparent_power_L1 * 1000 / self.inverter_number_slaves)
                    new_power_L2 = int(self.inverter_command_apparent_power_L2 * 1000 / self.inverter_number_slaves)
                    new_power_L3 = int(self.inverter_command_apparent_power_L3 * 1000 / self.inverter_number_slaves)

                    # Pre-defined the limits for 1x25kW TruConveter inv. 
                    power_limit = range(-25000, 25000)
                    power_limit_L = range(-8333, 8333)

                    
                    
                    if self.inv_opt == 'SYM':                                # overall power
                        if new_power not in power_limit:
                            if new_power < min(power_limit):
                                new_power = min(power_limit)
                            elif new_power > max(power_limit):
                                new_power = max(power_limit)
                        self.inverter_commanded_apparent_power = int(self.inverter_number_slaves*(new_power))

                        command_power = self.int_to_twos_comp(new_power)
                        
                        if command_power != rr.registers[0]:
                            try:
                                self.tcp_client.write_register(4195, int(command_power))
                                self.tcp_timeout = 0
                            except Exception as e:
                                print("Inverter: " + str(e))
                                self.tcp_timeout += 1
                                return

                    elif self.inv_opt == 'ASYM':                             # Each phase power
                        # L1
                        if new_power_L1 not in power_limit_L:
                            if new_power_L1 < min(power_limit_L):
                                new_power_L1 = min(power_limit_L)
                            elif new_power_L1 > max(power_limit_L):
                                new_power_L1 = max(power_limit_L)
                        self.inverter_commanded_apparent_power_L1 = int(self.inverter_number_slaves*(new_power_L1))
                        # L2
                        if new_power_L2 not in power_limit_L:
                            if new_power_L2 < min(power_limit_L):
                                new_power_L2 = min(power_limit_L)
                            elif new_power_L2 > max(power_limit_L):
                                new_power_L2 = max(power_limit_L)
                        self.inverter_commanded_apparent_power_L2 = int(self.inverter_number_slaves*(new_power_L2))
                        # L3
                        if new_power_L3 not in power_limit_L:
                            if new_power_L3 < min(power_limit_L):
                                new_power_L3 = min(power_limit_L)
                            elif new_power_L3 > max(power_limit_L):
                                new_power_L3 = max(power_limit_L)
                        self.inverter_commanded_apparent_power_L3 = int(self.inverter_number_slaves*(new_power_L3))
                        # print('command_power Line :' + str(self.inverter_commanded_apparent_power_L1), '  ' + str(new_power_L2))

                        command_power_L1 = self.int_to_twos_comp(new_power_L1)
                        command_power_L2 = self.int_to_twos_comp(new_power_L2)
                        command_power_L3 = self.int_to_twos_comp(new_power_L3)
                        # remember new_power is 1/2 of the  power
                        if command_power_L1 != rr.registers[1]:
                            try:
                                self.tcp_client.write_register(4196, int(command_power_L1))
                                self.tcp_timeout = 0
                            except Exception as e:
                                print("Inverter: " + str(e))
                                self.tcp_timeout += 1
                                return
                        if command_power_L2 != rr.registers[2]:
                            try:
                                self.tcp_client.write_register(4197, int(command_power_L2))
                                self.tcp_timeout = 0
                            except Exception as e:
                                print("Inverter: " + str(e))
                                self.tcp_timeout += 1
                                return
                        if command_power_L3 != rr.registers[3]:
                            try:
                                self.tcp_client.write_register(4198, int(command_power_L3))
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



            # CLear/Reset the Faults
            # Clear Faults (0x01) and Warnings (0x02)
            if self.auto_fault_clear == "Auto" or self.manual_fault_clear:
                self.manual_fault_clear = False  # Reset it in either case
                try:
                    # TODO: Clear the fault in Auto if there is any fault after reading the fault (iterate only once)
                    print('fault clear in Auto')
                    # self.tcp_client.write_coil(4002, 1)
                    self.tcp_timeout = 0
                except Exception as e:
                    print("Inverter: " + str(e))
                    self.tcp_timeout += 1
                    return
            elif self.auto_fault_clear == "Manual":
                try:
                    if self.inv_rst_alm == True:
                        print('Inverter Alarms Reset Manually')
                        self.tcp_client.write_coil(4002, 1)
                        self.inv_rst_alm = False

                    self.tcp_timeout = 0
                except Exception as e:
                    print("Inverter: " + str(e))
                    self.tcp_timeout += 1
                    return


            # Reading other useful data
            try:
                rr = self.tcp_client.read_holding_registers(5180, 12, timeout=0.25)

                if rr.isError():
                    self.tcp_timeout += 1
                    return
                else:

                    self.inverter_apparent_power_L1 = int(rr.registers[0] | rr.registers[1])/1000
                    self.inverter_apparent_power_L2 = int(rr.registers[2] | rr.registers[3])/1000
                    self.inverter_apparent_power_L3 = int(rr.registers[4] | rr.registers[5])/1000
                    self.inverter_apparent_power = self.inverter_apparent_power_L1 + self.inverter_apparent_power_L2 + self.inverter_apparent_power_L3 # may be kVA in total  

                    real_power_L1 = int(self.twos_comp_to_int(rr.registers[7]))/1000
                    real_power_L2 = int(self.twos_comp_to_int(rr.registers[9]))/1000
                    real_power_L3 = int(self.twos_comp_to_int(rr.registers[11]))/1000
                    self.inverter_real_power = int(real_power_L1 + real_power_L2 + real_power_L3)

                    self.tcp_timeout = 0

            except Exception as e:
                print("Inverter: " + str(e))
                self.tcp_timeout += 1
                return


            try:
                rr = self.tcp_client.read_holding_registers(5234, 6, timeout=0.25)

                if rr.isError():
                    self.tcp_timeout += 1
                    return
                else:

                    reactive_power_L1 = int(self.twos_comp_to_int(rr.registers[1]))/1000
                    reactive_power_L2 = int(self.twos_comp_to_int(rr.registers[3]))/1000
                    reactive_power_L3 = int(self.twos_comp_to_int(rr.registers[5]))/1000
                    self.inverter_reactive_power = int(reactive_power_L1 + reactive_power_L2 + reactive_power_L3)
                    self.tcp_timeout = 0

            except Exception as e:
                print("Inverter: " + str(e))
                self.tcp_timeout += 1
                return


            #--------------------------------------#
            # Accumulative params for no. of Slave #
            # --------------------------------------#
            total_freq = 0
            total_intlDC_vol = 0
            total_ac_curr = 0
            total_ac_volt = 0
            total_mod_inl_temp = 0
            total_mod_temp = 0
            total_mod_bal_temp = 0
            total_mod_fan_speed = 0
            for noOfSlave in range(1, self.inverter_number_slaves+1):
                # Grid current RMS
                try:
                    rr = self.tcp_client.read_holding_registers(5150, 3, unit=noOfSlave, timeout=0.25)
                    if rr.isError():
                        self.tcp_timeout += 1
                        return
                    else:
                        total_ac_curr += (rr.registers[0] + rr.registers[1] + rr.registers[2])/3
                        self.tcp_timeout = 0
                except Exception as e:
                    print("Inverter: " + str(e))
                    self.tcp_timeout += 1
                    return
                
                # Grid voltage RMS
                try:
                    rr = self.tcp_client.read_holding_registers(5160, 3, unit=noOfSlave, timeout=0.25)
                    if rr.isError():
                        self.tcp_timeout += 1
                        return
                    else:
                        total_ac_volt += (rr.registers[0] + rr.registers[1] + rr.registers[2])/3
                        self.tcp_timeout = 0
                except Exception as e:
                    print("Inverter: " + str(e))
                    self.tcp_timeout += 1
                    return

                # Grid-Frequency
                try:
                    rr = self.tcp_client.read_holding_registers(5200, 1, unit=noOfSlave, timeout=0.25)
                    if rr.isError():
                        self.tcp_timeout += 1
                        return
                    else:
                        total_freq += rr.registers[0]
                        self.tcp_timeout = 0
                except Exception as e:
                    print("Inverter: " + str(e))
                    self.tcp_timeout += 1
                    return

                # DC internal link voltages
                try:
                    rr = self.tcp_client.read_holding_registers(5210, 2, unit=noOfSlave, timeout=0.25)
                    if rr.isError():
                        self.tcp_timeout += 1
                        return
                    else:
                        total_intlDC_vol += rr.registers[0] + rr.registers[1]
                        self.tcp_timeout = 0
                except Exception as e:
                    print("Inverter: " + str(e))
                    self.tcp_timeout += 1
                    return

                # Module Temperature and Fan Speed
                try:
                    rr = self.tcp_client.read_holding_registers(5500, 6, unit=noOfSlave, timeout=0.25)
                    if rr.isError():
                        self.tcp_timeout += 1
                        return
                    else:
                        total_mod_inl_temp += rr.registers[0]
                        total_mod_temp += (rr.registers[1] + rr.registers[2] + rr.registers[3])/3
                        total_mod_bal_temp += rr.registers[4]
                        total_mod_fan_speed += rr.registers[5]
                        self.tcp_timeout = 0

                except Exception as e:
                    print("Inverter: " + str(e))
                    self.tcp_timeout += 1
                    return

            # Accumulative params
            self.inverter_frequency = (total_freq/self.inverter_number_slaves)/100
            self.inverter_dc_voltage = total_intlDC_vol/self.inverter_number_slaves
            self.inverter_ac_rms_phase_current = total_ac_curr/100
            self.inverter_ac_rms_phase_voltage = (total_ac_volt/self.inverter_number_slaves)/10
            self.inverter_inlet_air_temperature = (total_mod_inl_temp/self.inverter_number_slaves)/10
            self.inverter_avg_module_temperature = (total_mod_temp/self.inverter_number_slaves)/10
            self.inverter_module_balancer_temperature = (total_mod_bal_temp/self.inverter_number_slaves)/10
            self.inverter_fan_speed = (total_mod_fan_speed/self.inverter_number_slaves)

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
        self.outputs[2][3] = self.inverter_ac_rms_phase_voltage
        self.outputs[2][4] = self.inverter_commanded_real_current
        self.outputs[2][5] = self.inverter_ac_rms_phase_current
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
        self.outputs[2][17] = self.inverter_avg_module_temperature
        self.outputs[2][18] = self.inverter_inlet_air_temperature
        self.outputs[2][19] = 0
        self.outputs[2][20] = self.warnings
        self.outputs[2][21] = self.alarms
        self.outputs[2][22] = self.faults
        # self.outputs[2][23] = self.actions[0]

        self.outputs[2][24] = self.inverter_commanded_apparent_power
        self.outputs[2][25] = self.inverter_commanded_apparent_power_L1
        self.outputs[2][26] = self.inverter_commanded_apparent_power_L2
        self.outputs[2][27] = self.inverter_commanded_apparent_power_L3
        self.outputs[2][28] = self.inverter_apparent_power
        self.outputs[2][29] = self.inverter_apparent_power_L1
        self.outputs[2][30] = self.inverter_apparent_power_L2
        self.outputs[2][31] = self.inverter_apparent_power_L3
        self.outputs[2][32] = self.inverter_module_balancer_temperature
        self.outputs[2][33] = self.inverter_fan_speed

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
       
        # Loop time calculations for efficiency checking and monitoring performance
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
                        '''input_power_L1 = self.inputs[2][9]
                        input_power_L2 = self.inputs[2][10]
                        input_power_L3 = self.inputs[2][11]'''

                        # TODO: Neat as this is, we should be commanding the power *here* to avoid the process loop time (currently 1 second),
                        #  after we've already spent <0.5 seconds in Flex.py going from SCADA -> Client -> inverter

                        '''if -25000 < input_power < 25000:
                            self.inverter_command_apparent_power = input_power
                        if -8333 < input_power_L1 < 8333:
                            self.inverter_apparent_power_L1 = input_power_L1
                        if -8333 < input_power_L2 < 8333:
                            self.inverter_apparent_power_L2 = input_power_L2
                        if -8333 < input_power_L3 < 8333:
                            self.inverter_apparent_power_L3 = input_power_L3'''

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

                elif "inverter_OptMod" in form:      # 1 = symmetric; 0 = asymmetric
                    # self.actions.insert(0, Actions.INVERTER_ENABLE_CHANGE.value)
                    input_opt = form[control]
                    if input_opt == 'SYM':
                        self.inv_opt = 'SYM'
                    else:
                        self.inv_opt = 'ASYM'
                    

                elif "inverter_enable_button_state" in form:
                    self.actions.insert(0, Actions.INVERTER_ENABLE_CHANGE.value)
                    if not self.inv_conn:
                        self.inv_conn = True
                    else:
                        self.inv_conn = False

                elif "inverter_fault_clear" in form:
                    # self.actions.insert(0, Actions.INVERTER_ENABLE_CHANGE.value)
                    if not self.inv_rst_alm:
                        self.inv_rst_alm = True
                    # else:
                     #   self.inv_rst_alm = False

                elif "inverter_apparent_power_set" in form:
                    if self.override:
                        self.actions.insert(0, Actions.REAL_POWER_CHANGE.value)
                        input_power = int(form[control])

                        # TODO: This needs to be compared against HMI Import and export limits
                        if -25000 < input_power < 25000:
                            self.inverter_command_apparent_power = input_power
                
                elif "inverter_apparent_power_set_L1" in form:
                    if self.override:
                        self.actions.insert(0, Actions.REAL_POWER_CHANGE.value)
                        input_power_L1 = int(form[control])

                        # TODO: This needs to be compared against HMI Import and export limits
                        if -8333 < input_power_L1 < 8333:
                            self.inverter_command_apparent_power_L1 = input_power_L1
                
                elif "inverter_apparent_power_set_L2" in form:
                    if self.override:
                        self.actions.insert(0, Actions.REAL_POWER_CHANGE.value)
                        input_power_L2 = int(form[control])

                        # TODO: This needs to be compared against HMI Import and export limits
                        if -8333 < input_power_L2 < 8333:
                            self.inverter_command_apparent_power_L2 = input_power_L2
                
                elif "inverter_apparent_power_set_L3" in form:
                    if self.override:
                        self.actions.insert(0, Actions.REAL_POWER_CHANGE.value)
                        input_power_L3 = int(form[control])

                        # TODO: This needs to be compared against HMI Import and export limits
                        if -8333 < input_power_L3 < 8333:
                            self.inverter_command_apparent_power_L3 = input_power_L3
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
            # self.actions = []

            # Controller Information
            mod_data["inverter_name"] = self.name
            mod_data["inverter_man"] = self.manufacturer
            mod_data["inverter_fwver"] = self.version
            mod_data["inverter_serno"] = self.serial
            mod_data["inverter_constate"] = str(self.con_state).capitalize()
            mod_data["inverter_data"] = self.outputs[2]
            mod_data["inverter_override"] = self.override
            mod_data["inverter_OptMod"] = self.inv_opt
            mod_data["inverter_fault_clear"] = self.auto_fault_clear
            mod_data["inverter_start"] = self.inv_conn
            mod_data["inverter_enablestate"] = self.enabled

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
    # print("MODULE STARTED with ID " + str(uid))

    # Create and init our Module class
    flex_module = Module(uid, queue)

    # Start the interval timer for periodic device polling
    # thread = Interval(Event(), flex_module.process, 1)
    # thread.start()

    # Process piped requests
    while True:
        rx_msg = None
        tx_msg = None

        try:
            rx_msg = queue[1].get()

            if isinstance(rx_msg, list):
                if rx_msg[0] == SYNC:
                    if sys.getrefcount(flex_module) <= 2:
                        Thread(target=flex_module.process).start()
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

    SCADA = 22  # Add option to select Protocol within module?
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
