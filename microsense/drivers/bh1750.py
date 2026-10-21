"""ROHM BH1750 ambient light sensor, in lux.

The chip reports counts where 1 lux is 1.2 counts, so the conversion is a single
division. What actually matters in practice is the measurement mode: continuous
high resolution gives one reading every ~120 ms without being asked again, which
is what a small node wants.
"""

from ..bus import BusError, I2C, require

LUX_PER_COUNT = 1.0 / 1.2
RESOLUTION_DELAY_MS = 120


class BH1750:
    ADDRESS = 0x23
    ADDRESS_ALTERNATE = 0x5C

    POWER_OFF = 0x00
    POWER_ON = 0x01
    RESET = 0x07
    CONTINUOUS_HIGH = 0x10
    CONTINUOUS_HIGH_2 = 0x11
    CONTINUOUS_LOW = 0x13
    ONE_SHOT_HIGH = 0x20
    ONE_SHOT_LOW = 0x23

    def __init__(self, i2c, address=ADDRESS, mode=CONTINUOUS_HIGH):
        if not isinstance(i2c, I2C):
            raise TypeError("i2c must be an I2C bus")
        self._i2c = i2c
        self.address = address
        self.mode = mode
        self._started = False

    def start(self):
        """Power up and put the chip into the configured measurement mode."""
        self._command(self.POWER_ON)
        self._command(self.mode)
        self._started = True

    def power_off(self):
        self._command(self.POWER_OFF)
        self._started = False

    def read(self):
        """Returns illuminance in lux."""
        if not self._started:
            self.start()
        try:
            payload = self._i2c.read(self.address, 2)
        except OSError as error:
            raise BusError("BH1750 did not answer: {}".format(error))
        require(len(payload) == 2, "BH1750 returned {} bytes, expected 2".format(len(payload)))
        counts = (payload[0] << 8) | payload[1]
        return counts * LUX_PER_COUNT

    def _command(self, command):
        try:
            self._i2c.write(self.address, bytes((command,)))
        except OSError as error:
            raise BusError("BH1750 did not answer: {}".format(error))
