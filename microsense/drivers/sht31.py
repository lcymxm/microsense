"""Sensirion SHT3x temperature and humidity sensor.

Datasheet conversion, which is refreshingly plain compared with the older
integer-heavy parts:

    T  = -45 + 175 * S_T  / 65535      (degC)
    RH = 100 * S_RH / 65535            (percent)

Each two byte word is followed by a CRC-8: polynomial 0x31, initial value 0xFF,
no final XOR. Checking it is the difference between "a bad reading" and "a
reading that is silently wrong", so a mismatch raises.
"""

from ..bus import BusError, I2C, require

CRC8_POLYNOMIAL = 0x31
CRC8_INIT = 0xFF

MEASUREMENT_DELAY_MS = 20


def crc8(data):
    """CRC-8 as specified for SHT3x (and several other Sensirion parts)."""
    crc = CRC8_INIT
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ CRC8_POLYNOMIAL) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


class SHT31:
    """Single shot, high repeatability reads with clock stretching disabled."""

    ADDRESS = 0x44
    ADDRESS_ALTERNATE = 0x45
    COMMAND_SINGLE_SHOT = 0x2400
    COMMAND_SOFT_RESET = 0x300A

    def __init__(self, i2c, address=ADDRESS):
        if not isinstance(i2c, I2C):
            raise TypeError("i2c must be an I2C bus")
        self._i2c = i2c
        self.address = address
        self._verified = False

    def soft_reset(self):
        self._write_command(self.COMMAND_SOFT_RESET)
        self._verified = False

    def read(self):
        """Returns (temperature_degc, relative_humidity_percent)."""
        from .. import clock

        self._write_command(self.COMMAND_SINGLE_SHOT)
        clock.sleep_ms(MEASUREMENT_DELAY_MS)

        try:
            payload = self._i2c.read(self.address, 6)
        except OSError as error:
            raise BusError("SHT31 did not answer: {}".format(error))

        require(len(payload) == 6, "SHT31 returned {} bytes, expected 6".format(len(payload)))

        raw_temperature = self._word(payload, 0, "temperature")
        raw_humidity = self._word(payload, 3, "humidity")

        temperature = -45.0 + 175.0 * raw_temperature / 65535.0
        humidity = 100.0 * raw_humidity / 65535.0
        return temperature, humidity

    # -- internals --------------------------------------------------------------

    def _write_command(self, command):
        try:
            self._i2c.write(self.address, bytes(((command >> 8) & 0xFF, command & 0xFF)))
        except OSError as error:
            raise BusError("SHT31 did not answer: {}".format(error))

    def _word(self, payload, offset, what):
        high = payload[offset]
        low = payload[offset + 1]
        expected = payload[offset + 2]
        actual = crc8(payload[offset:offset + 2])
        if actual != expected:
            raise BusError(
                "SHT31 {} CRC mismatch (got 0x{:02X}, expected 0x{:02X})".format(
                    what, expected, actual
                )
            )
        return (high << 8) | low
