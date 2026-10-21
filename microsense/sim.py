"""Register-level fakes and a small simulated environment.

Everything here exists so the drivers and the node can be exercised without a
soldering iron: the fakes answer the same byte sequences the real chips do,
including the CRC bytes, the configuration registers and the full scale settings.

They also make the package demonstrable on a desktop:

    python -m microsense simulate
"""

import struct

from . import clock
from .bus import ADC, BusError, I2C
from .drivers.sht31 import crc8 as sht31_crc8

# --- a tiny deterministic PRNG -------------------------------------------------
# random.Random is bigger than the whole package on some builds, and a fixed
# sequence keeps test output reproducible.


class _Random:
    def __init__(self, seed=1):
        self._state = seed & 0x7FFFFFFF or 1

    def next(self):
        self._state = (self._state * 1103515245 + 12345) & 0x7FFFFFFF
        return self._state / 2147483647.0

    def uniform(self, low, high):
        return low + (high - low) * self.next()

    def gauss(self, sigma):
        # Sum of uniforms is close enough to normal for sensor noise.
        total = 0.0
        for _ in range(6):
            total += self.next()
        return (total - 3.0) * sigma


class SimulatedPlant:
    """Slowly moving environment, in engineering units."""

    def __init__(self, seed=1):
        self._rng = _Random(seed)
        self._start_ms = clock.ticks_ms()
        self.temperature = 22.0
        self.humidity = 45.0
        self.lux = 320.0
        self.accel = (0.02, 0.01, 1.0)
        self.gyro = (0.5, -0.3, 0.0)
        self.voltage = 12.10
        self.current = 0.85
        self._seconds = 0.0
        self._last_ms = None

    def tick(self, now_ms=None):
        if now_ms is None:
            now_ms = clock.ticks_ms()
        if self._last_ms is None:
            self._last_ms = now_ms
        dt = (now_ms - self._last_ms) / 1000.0
        self._last_ms = now_ms
        if dt < 0.0:
            dt = 0.0
        if dt > 2.0:
            dt = 2.0
        self._seconds += dt

        # 60 s and 15 s cycles, plus a little noise, so the HMI has something to
        # show instead of a flat line.
        slow = _sin(self._seconds * 6.283185307179586 / 60.0)
        fast = _sin(self._seconds * 6.283185307179586 / 15.0)

        self.temperature = 22.0 + 1.2 * slow + self._rng.gauss(0.05)
        self.humidity = 45.0 + 6.0 * fast + self._rng.gauss(0.3)
        self.lux = 320.0 + 120.0 * max(0.0, slow) + self._rng.gauss(2.0)

        # The bench tilts: a small oscillation on both axes.
        pitch = _radians(8.0 * slow)
        roll = _radians(5.0 * fast)
        self.accel = (
            0.02 + 0.14 * _sin(pitch),
            _sin(roll) * 0.09,
            1.0,
        )
        self.gyro = (2.0 * fast, 1.5 * slow, 0.2 * _sin(self._seconds))

        self.voltage = 12.10 + 0.08 * slow + self._rng.gauss(0.005)
        self.current = 0.85 + 0.25 * fast + self._rng.gauss(0.004)


# --- device models -------------------------------------------------------------


class SHT31Device:
    """SHT3x temperature/humidity sensor. Address 0x44 by default."""

    ADDRESS = 0x44

    def __init__(self, plant):
        self.plant = plant
        self._pending = False

    def write(self, data):
        if len(data) < 2:
            raise BusError("SHT31 command must be two bytes")
        command = (data[0] << 8) | data[1]
        # Single shot commands start a conversion; the variant only changes the
        # repeatability and whether the clock is stretched.
        if command in (0x2400, 0x2402, 0x240B, 0x2416, 0x2C06):
            self._pending = True
        elif command == 0x300A:  # soft reset
            self._pending = False
        elif command == 0x3041:  # heater on
            pass
        else:
            raise BusError("unsupported SHT31 command 0x{:04X}".format(command))

    def read(self, length):
        if length != 6:
            raise BusError("SHT31 returns six bytes")
        if not self._pending:
            raise BusError("SHT31 measurement not started")
        self._pending = False
        return self._encode(self.plant.temperature, self.plant.humidity)

    @staticmethod
    def _encode(temperature, humidity):
        temperature = min(max(temperature, -45.0), 130.0)
        humidity = min(max(humidity, 0.0), 100.0)
        raw_t = int(round((temperature + 45.0) / 175.0 * 65535.0))
        raw_rh = int(round(humidity / 100.0 * 65535.0))
        raw_t = min(max(raw_t, 0), 65535)
        raw_rh = min(max(raw_rh, 0), 65535)
        out = bytearray()
        for raw in (raw_t, raw_rh):
            high = (raw >> 8) & 0xFF
            low = raw & 0xFF
            out.append(high)
            out.append(low)
            out.append(sht31_crc8(bytes([high, low])))
        return bytes(out)


class BH1750Device:
    """BH1750 illuminance sensor. Address 0x23."""

    ADDRESS = 0x23
    POWER_ON = 0x01
    POWER_OFF = 0x00
    CONTINUOUS_HIGH = 0x10
    ONE_SHOT_HIGH = 0x20

    def __init__(self, plant):
        self.plant = plant
        self.powered = False
        self.measuring = False

    def write(self, data):
        if not data:
            raise BusError("BH1750 needs a command byte")
        command = data[0]
        if command == self.POWER_ON:
            self.powered = True
        elif command == self.POWER_OFF:
            self.powered = False
            self.measuring = False
        elif command in (self.CONTINUOUS_HIGH, self.ONE_SHOT_HIGH):
            if not self.powered:
                raise BusError("BH1750 is not powered")
            self.measuring = True
        else:
            raise BusError("unsupported BH1750 command 0x{:02X}".format(command))

    def read(self, length):
        if length != 2:
            raise BusError("BH1750 returns two bytes")
        if not self.measuring:
            raise BusError("BH1750 has no measurement ready")
        raw = int(round(max(self.plant.lux, 0.0) * 1.2))
        raw = min(max(raw, 0), 65535)
        return struct.pack(">H", raw)


class MPU6050Device:
    """MPU6050 accelerometer + gyroscope. Address 0x68."""

    ADDRESS = 0x68
    REG_ACCEL_XOUT_H = 0x3B
    REG_ACCEL_CONFIG = 0x1C
    REG_GYRO_CONFIG = 0x1B
    REG_PWR_MGMT_1 = 0x6B
    REG_WHO_AM_I = 0x75
    WHO_AM_I_VALUE = 0x68

    # LSB per unit for each full scale setting.
    ACCEL_SCALES = (16384.0, 8192.0, 4096.0, 2048.0)  # +-2, 4, 8, 16 g
    GYRO_SCALES = (131.0, 65.5, 32.8, 16.4)  # +-250, 500, 1000, 2000 deg/s

    def __init__(self, plant):
        self.plant = plant
        self.registers = {}
        self.accel_range = 0
        self.gyro_range = 0
        self.awake = False
        self.registers[self.REG_WHO_AM_I] = self.WHO_AM_I_VALUE
        self.registers[self.REG_PWR_MGMT_1] = 0x40  # asleep at power on

    def write_register(self, register, data):
        if not data:
            return
        value = data[0]
        if register == self.REG_PWR_MGMT_1:
            self.awake = (value & 0x40) == 0
            self.registers[register] = value
        elif register == self.REG_ACCEL_CONFIG:
            self.accel_range = (value >> 3) & 0x03
            self.registers[register] = value
        elif register == self.REG_GYRO_CONFIG:
            self.gyro_range = (value >> 3) & 0x03
            self.registers[register] = value
        else:
            self.registers[register] = value

    def read_registers(self, register, length):
        if not self.awake:
            raise BusError("MPU6050 is asleep")
        if register == self.REG_WHO_AM_I:
            return bytes([self.WHO_AM_I_VALUE])
        if register == self.REG_ACCEL_XOUT_H and length >= 14:
            ax, ay, az = self.plant.accel
            gx, gy, gz = self.plant.gyro
            accel_scale = self.ACCEL_SCALES[self.accel_range]
            gyro_scale = self.GYRO_SCALES[self.gyro_range]
            values = (
                _to_signed16(ax * accel_scale),
                _to_signed16(ay * accel_scale),
                _to_signed16(az * accel_scale),
                0,  # die temperature, unused
                _to_signed16(gx * gyro_scale),
                _to_signed16(gy * gyro_scale),
                _to_signed16(gz * gyro_scale),
            )
            payload = struct.pack(">hhhHhhh", *values)
            return payload[:length]
        # Any other register reads back what was written.
        out = bytearray()
        for offset in range(length):
            out.append(self.registers.get(register + offset, 0))
        return bytes(out)


class FakeI2C(I2C):
    """An I2C bus backed by device models instead of wires.

    A missing device raises OSError like MicroPython does, so the "sensor is not
    fitted" path is the same in tests as on the bench.
    """

    def __init__(self, devices=None):
        self.devices = dict(devices or {})
        self.reads = 0
        self.writes = 0

    def _device(self, address):
        try:
            return self.devices[address]
        except KeyError:
            raise OSError(19, "ENODEV: no device at 0x{:02X}".format(address))

    def scan(self):
        return sorted(self.devices)

    def read(self, address, length):
        self.reads += 1
        return self._device(address).read(length)

    def write(self, address, data):
        self.writes += 1
        self._device(address).write(bytes(data))

    def read_registers(self, address, register, length):
        self.reads += 1
        return self._device(address).read_registers(register, length)

    def write_register(self, address, register, data):
        self.writes += 1
        self._device(address).write_register(register, bytes(data))


class FakeADC(ADC):
    """ADC channel reporting a fixed ratio, or one produced by `source`."""

    def __init__(self, ratio=0.5, source=None, noise=0.0, seed=7):
        self.ratio = ratio
        self.source = source
        self.noise = noise
        self._rng = _Random(seed)

    def read_ratio(self):
        value = self.source() if self.source is not None else self.ratio
        if self.noise:
            value += self._rng.gauss(self.noise)
        return min(max(value, 0.0), 1.0)


def build_simulated_bus(seed=1, vref=3.3, divider_ratio=5.0, shunt_ohms=0.1,
                        amplifier_gain=10.0):
    """Returns (i2c, adc_voltage, adc_current, plant) with every model attached.

    Two ADC objects, not one: on a real board the divider and the shunt sit on
    different pins, and giving each channel its own pin keeps that visible.
    """
    plant = SimulatedPlant(seed)
    i2c = FakeI2C({
        SHT31Device.ADDRESS: SHT31Device(plant),
        BH1750Device.ADDRESS: BH1750Device(plant),
        MPU6050Device.ADDRESS: MPU6050Device(plant),
    })
    adc_voltage = FakeADC(source=lambda: plant.voltage / (vref * divider_ratio),
                          noise=0.0006, seed=11)
    adc_current = FakeADC(
        source=lambda: plant.current * shunt_ohms * amplifier_gain / vref,
        noise=0.0006, seed=12)
    return i2c, adc_voltage, adc_current, plant


# --- helpers -------------------------------------------------------------------


def _to_signed16(value):
    raw = int(round(value))
    raw = min(max(raw, -32768), 32767)
    return raw


def _sin(radians):
    import math

    return math.sin(radians)


def _radians(degrees):
    return degrees * 0.017453292519943295
