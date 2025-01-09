# Flex-ESS Base class
# S. Coates, 17-10-22
# MSP Technologies Ltd
from enum import Enum
import random


class BaseModule:
    def __init__(self):

        # Module Data
        self.uid = 0
        self.icon = "none.png"
        self.name = "Unknown"
        self.type = ModTypes.BASE.value
        self.manufacturer = "Unknown"
        self.model = "Unknown"
        self.options = 0
        self.version = 0
        self.serial = ""
        self.website = ""
        self.enable = False                                                                         # Issued by a controller
        self.enabled = False                                                                        # Echoed to the controller when ready to be controlled
        self.state = "Idle"
        self.warnings = []
        self.alarms = []
        self.faults = []
        self.actions = []
        self.heartbeat = 0

        # Line data, from which power is derived.
        self.pV = 1                                                                                 # Primary units of AC Voltage and Current
        self.pA = 1
        self.sV = 0                                                                                 # Secondary, for DC units
        self.sA = 0
        self.tV = 0                                                                                 # Tertiary, if a device has a third port, like a PD Hydra
        self.tA = 0

    def get_info(self):
        info = [self.uid, self.type, self.icon, self.name, self.manufacturer, self.model, self.options, self.version, self.website]
        return info

    def get_status(self):
        data = [self.uid, self.heartbeat, self.pV, self.pA, self.sV, self.sA, self.tV, self.tA,
                self.state, self.warnings, self.alarms, self.faults, self.actions, self.icon]
        return data

    def set_inputs(self, inputs):
        self.enable = inputs[0]
        return inputs

    def get_outputs(self):
        outputs = [self.uid]
        return outputs

    def set_page(self, page):
        return

    def get_page(self):
        page = ["",""]
        return page

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

    def __del__(self):
        pass


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