"""microsense -- a multi-function sensor node for microcontrollers.

Core modules, in the order you normally meet them:

    bus           I2C / ADC interface (real hardware or the register fakes)
    drivers.*     SHT31, BH1750, MPU6050, analog channels
    filters       moving average, median, exponential, complementary
    calibration   two point linear calibration, stored as JSON
    node          registration, scheduling, quality codes, alarms
    modbus        CRC-16, framing, register image, Modbus TCP server
    lineproto     JSON / CSV / table rendering

The package deliberately avoids type annotations, dataclasses and enums: none of
them exist on a small MicroPython build, and the same files have to run on the
board and in CI.
"""

__version__ = "1.0.0"
