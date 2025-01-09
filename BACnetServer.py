# BACnet Modbus Server.
# So this is fun, in this implementation we needed data from two grid meters and one solar meter provided to us over BACnet by the client.
# The BAC0 module (and Yabe) only allows one connection to a BACnet server through port 47808 (0xBAC0!), but we want 3 Flex3 modules to present the data as ACMeter1, ACMeter2 and ACSolar1.
# So in this case we'll collect *all* the data here, and present it to the other modules as a generic multi-access Modbus device! (ikr, crazy). I think it'll be cleaner than trying to unidirectionally
# pipe large amounts of registers between multiple subprocesses, and it means the Flex3 module won't have to worry about BACnet connection handling.

import BAC0
import time

from memory_profiler import profile

from threading import Thread, Event
from pymodbus.server.sync import ModbusTcpServer
from pymodbus.datastore import ModbusSequentialDataBlock
from pymodbus.datastore import ModbusSlaveContext, ModbusServerContext


# Modbus server (similar to the Flex3 SCADA Server, but on a different port)
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
                scadaServer = ReusableModbusTcpServer(self.context, address=('', 504))              # 502 is reserved for our usual SCADA interface
                scadaServer.serve_forever()
            except OSError:
                print("Error while starting BACnet server! : ", OSError)


# Modbus Server
mbtcp_server_stop = Event()
mbtcp_server_registers = []

# BACnet Client
bacnet = BAC0.lite(ip="192.168.33.131/23")                                                          # Local Network interface to BACnet
scada_state = 0
proc_state = 0


def int_to_twos_comp(num):
    return num if num >= 0 else (0xFFFF + num + 1)

@profile
def looping():
    pass

while True:
    looping()
    # SCADA State Machine
    if proc_state == 0:  # Begin SCADA requests

        # Start Modbus-TCP Server for SCADA interface
        try:
            # Server sits in an infinite loop so must have its own thread.
            store = ModbusSlaveContext(hr=ModbusSequentialDataBlock(0, [0] * 100))                  # We're only allocating 100 Holding Registers

            mbtcp_server_registers = ModbusServerContext(slaves=store, single=True)

            mbtcp_server_thread = MbusTcpServer(mbtcp_server_stop, mbtcp_server_registers)
            mbtcp_server_thread.start()

            proc_state = 1

        except OSError:
            pass

    elif proc_state == 1:

        # I already know which device and which data points I need (less than 10), so feed them straight into the SCADA Holding Registers wherever it suits
        bacnet_data = [0] * 10

        try:
            # Data we're retrieving from ECOPark's BACnet
            ephGrid = bacnet.read("192.168.33.53 analogInput 157 presentValue")
            ephLoad = bacnet.read("192.168.33.53 analogInput 159 presentValue")
            pvM = bacnet.read("192.168.33.53 analogInput 158 presentValue")
            pv1 = bacnet.read("192.168.33.53 analogInput 133 presentValue")
            pv2 = bacnet.read("192.168.33.53 analogInput 134 presentValue")
            pv3 = bacnet.read("192.168.33.53 analogInput 135 presentValue")
            pv4 = bacnet.read("192.168.33.53 analogInput 136 presentValue")
            pv5 = bacnet.read("192.168.33.53 analogInput 137 presentValue")
            pv6 = bacnet.read("192.168.33.53 analogInput 138 presentValue")
            pvT = pv1 + pv2 + pv3 + pv4 + pv5 + pv6

            bacnet_data[0] = int_to_twos_comp(int(ephGrid * 10))                                        # Grid Meter
            bacnet_data[1] = int_to_twos_comp(int(ephLoad * 10))                                        # Load Meter (EcoPark House)
            bacnet_data[2] = int_to_twos_comp(int(pvM * 10))                                            # PV Meter (unreliable, use pvT)
            bacnet_data[3] = int_to_twos_comp(int(pv1 * 10))                                            # PV Inverter 1
            bacnet_data[4] = int_to_twos_comp(int(pv2 * 10))                                            # PV Inverter 2
            bacnet_data[5] = int_to_twos_comp(int(pv3 * 10))                                            # PV Inverter 3
            bacnet_data[6] = int_to_twos_comp(int(pv4 * 10))                                            # PV Inverter 4
            bacnet_data[7] = int_to_twos_comp(int(pv5 * 10))                                            # PV Inverter 5
            bacnet_data[8] = int_to_twos_comp(int(pv6 * 10))                                            # PV Inverter 6
            bacnet_data[9] = int_to_twos_comp(int(pvT * 10))                                            # PV Inverter Total
        except:
            print("Connection lost to BACnet Interface.")

        try:
            mbtcp_server_registers[0x00].setValues(3, 0, bacnet_data)
        except:
            print("Issue when updating BACnet Data")

    time.sleep(1)