"""InvenSense MPU6050 accelerometer and gyroscope.

The chip returns raw signed 16 bit counts. Turning those into g and deg/s needs
the full scale setting, and the full scale setting is a register the driver
writes at start-up -- so the conversion and the configuration have to agree or
every reading is quietly off by a factor of two or four.
"""

from ..bus import BusError, I2C, require

REG_ACCEL_XOUT_H = 0x3B
REG_GYRO_CONFIG = 0x1B
REG_ACCEL_CONFIG = 0x1C
REG_PWR_MGMT_1 = 0x6B
REG_WHO_AM_I = 0x75

WHO_AM_I_VALUE = 0x68

# LSB per g for +-2, +-4, +-8, +-16 g
ACCEL_SCALES = (16384.0, 8192.0, 4096.0, 2048.0)
# LSB per deg/s for +-250, +-500, +-1000, +-2000 deg/s
GYRO_SCALES = (131.0, 65.5, 32.8, 16.4)

ACCEL_RANGES = (2, 4, 8, 16)
GYRO_RANGES = (250, 500, 1000, 2000)


class MPU6050:
    def __init__(self, i2c, address=0x68, accel_range=2, gyro_range=250):
        if not isinstance(i2c, I2C):
            raise TypeError("i2c must be an I2C bus")
        self._i2c = i2c
        self.address = address
        self.accel_range = accel_range
        self.gyro_range = gyro_range
        self._accel_index = self._index_of(ACCEL_RANGES, accel_range, "+-2/4/8/16 g")
        self._gyro_index = self._index_of(GYRO_RANGES, gyro_range, "+-250/500/1000/2000 dps")
        self._configure()

    def who_am_i(self):
        payload = self._read_registers(REG_WHO_AM_I, 1)
        return payload[0]

    def read(self):
        """Returns a dict with accel in g and gyro in deg/s."""
        payload = self._read_registers(REG_ACCEL_XOUT_H, 14)
        require(len(payload) == 14, "MPU6050 returned {} bytes, expected 14".format(len(payload)))

        raw = (
            _int16(payload, 0),   # ax
            _int16(payload, 2),   # ay
            _int16(payload, 4),   # az
            _int16(payload, 6),   # die temperature, unused
            _int16(payload, 8),   # gx
            _int16(payload, 10),  # gy
            _int16(payload, 12),  # gz
        )

        accel_scale = ACCEL_SCALES[self._accel_index]
        gyro_scale = GYRO_SCALES[self._gyro_index]
        return {
            "ax": raw[0] / accel_scale,
            "ay": raw[1] / accel_scale,
            "az": raw[2] / accel_scale,
            "temperature": raw[3] / 340.0 + 36.53,
            "gx": raw[4] / gyro_scale,
            "gy": raw[5] / gyro_scale,
            "gz": raw[6] / gyro_scale,
        }

    # -- internals --------------------------------------------------------------

    def _configure(self):
        # Leave sleep mode: the part powers up asleep and reads nothing until
        # this bit is cleared. 0x01 selects the X axis gyro PLL as the clock.
        self._write_register(REG_PWR_MGMT_1, 0x01)
        # The three bits above the full scale selection are self test bits and
        # must stay zero.
        self._write_register(REG_ACCEL_CONFIG, (self._accel_index & 0x03) << 3)
        self._write_register(REG_GYRO_CONFIG, (self._gyro_index & 0x03) << 3)

    @staticmethod
    def _index_of(options, value, description):
        try:
            return options.index(value)
        except ValueError:
            raise ValueError("range must be one of {} degrees, got {}".format(description, value))

    def _read_registers(self, register, length):
        try:
            return self._i2c.read_registers(self.address, register, length)
        except OSError as error:
            raise BusError("MPU6050 did not answer: {}".format(error))

    def _write_register(self, register, value):
        try:
            self._i2c.write_register(self.address, register, bytes((value,)))
        except OSError as error:
            raise BusError("MPU6050 did not answer: {}".format(error))


def _int16(payload, offset):
    value = (payload[offset] << 8) | payload[offset + 1]
    if value >= 0x8000:
        value -= 0x10000
    return value
