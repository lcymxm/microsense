"""Sensor drivers.

Every driver takes an `I2C` or `ADC` object from microsense.bus rather than
importing `machine` itself. That is what lets the tests run the exact same code
against the register-level fakes in microsense.sim.
"""

from .adc import CurrentChannel, VoltageChannel
from .bh1750 import BH1750
from .mpu6050 import MPU6050
from .sht31 import SHT31, crc8

__all__ = ["BH1750", "CurrentChannel", "MPU6050", "SHT31", "VoltageChannel", "crc8"]
