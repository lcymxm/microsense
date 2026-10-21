"""Bus abstraction: I2C and ADC.

The point of this module is the seam. Sensors talk to an `I2C` object and never
to `machine.I2C` directly, so the exact same driver code runs against real
hardware on the board and against a register-level fake in the tests.
"""


class BusError(Exception):
    """Raised when a device does not answer or returns something impossible."""


class I2C:
    """Minimal I2C surface used by the drivers."""

    def scan(self):
        raise NotImplementedError

    def read(self, address, length):
        """Read `length` bytes with no register address (SHT31 style)."""
        raise NotImplementedError

    def write(self, address, data):
        """Write raw bytes (a command, usually)."""
        raise NotImplementedError

    def read_registers(self, address, register, length):
        """Read `length` bytes starting at `register` (repeated start)."""
        self.write(address, bytes([register]))
        return self.read(address, length)

    def write_register(self, address, register, data):
        self.write(address, bytes([register]) + bytes(data))


class MachineI2C(I2C):
    """Wraps machine.I2C. Constructed lazily so this module imports on CPython."""

    def __init__(self, bus_id=0, scl=None, sda=None, frequency=400000, address_width=8):
        import machine

        self._address_width = address_width
        if scl is not None and sda is not None:
            self._bus = machine.I2C(bus_id, scl=machine.Pin(scl), sda=machine.Pin(sda),
                                    freq=frequency)
        elif scl is not None or sda is not None:
            raise ValueError("pass both scl and sda, or neither")
        else:
            self._bus = machine.I2C(bus_id, freq=frequency)

    def scan(self):
        return sorted(self._bus.scan())

    def read(self, address, length):
        return self._bus.readfrom(address, length)

    def write(self, address, data):
        self._bus.writeto(address, bytes(data))

    def read_registers(self, address, register, length):
        return self._bus.readfrom_mem(address, register, length, addrsize=self._address_width)

    def write_register(self, address, register, data):
        self._bus.writeto_mem(address, register, bytes(data), addrsize=self._address_width)


class ADC:
    """Normalised ADC channel: read_ratio() returns 0.0 .. 1.0."""

    def read_ratio(self):
        raise NotImplementedError


class MachineADC(ADC):
    """Wraps machine.ADC.

    `read_u16()` returns 0..65535 on both ESP32 and RP2040, which makes the
    conversion to a ratio portable. A two point calibration of the reference
    happens further up, in the sensor driver.
    """

    def __init__(self, pin, vref=3.3, attenuation=None):
        import machine

        self._pin = machine.ADC(machine.Pin(pin))
        if attenuation is not None and hasattr(self._pin, "atten"):
            self._pin.atten(attenuation)
        self.vref = float(vref)
        self._full_scale = 65535

    def read_ratio(self):
        return self._pin.read_u16() / self._full_scale


def require(condition, message):
    """Small assertion helper shared by the drivers."""
    if not condition:
        raise BusError(message)
