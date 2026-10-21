"""Analog channels: a voltage divider and a shunt, with oversampling.

Two things separate a usable analog reading from a naive one:

* **Oversampling.** A single ADC conversion on an ESP32 is noisy enough to move
  the last two or three bits. Averaging N readings costs microseconds.
* **Calibration.** The divider resistors have tolerance and the ADC reference is
  not exactly 3.3 V, so the raw ratio needs a two point correction before it
  means anything.
"""

from ..bus import ADC, BusError
from ..calibration import LinearCalibration


class _AnalogChannel:
    def __init__(self, adc, vref=3.3, oversample=8, calibration=None):
        if not isinstance(adc, ADC):
            raise TypeError("adc must be an ADC channel")
        if oversample < 1:
            raise ValueError("oversample must be at least 1")
        if vref <= 0.0:
            raise ValueError("vref must be positive")
        self._adc = adc
        self.vref = float(vref)
        self.oversample = int(oversample)
        self.calibration = calibration or LinearCalibration()

    def read_ratio(self):
        """Mean of `oversample` conversions, in 0.0 .. 1.0."""
        total = 0.0
        for _ in range(self.oversample):
            total += self._adc.read_ratio()
        return total / self.oversample

    def read_volts(self):
        """Voltage at the ADC pin."""
        return self.read_ratio() * self.vref


class VoltageChannel(_AnalogChannel):
    """Measures a voltage through a resistor divider.

    `divider_ratio` is (R_top + R_bottom) / R_bottom, i.e. how much the divider
    attenuates the input. 5.0 suits a 12 V rail on a 3.3 V ADC.
    """

    def __init__(self, adc, vref=3.3, divider_ratio=5.0, oversample=8, calibration=None):
        _AnalogChannel.__init__(self, adc, vref, oversample, calibration)
        if divider_ratio <= 0.0:
            raise ValueError("divider_ratio must be positive")
        self.divider_ratio = float(divider_ratio)

    def read_raw(self):
        """Input voltage before calibration."""
        return self.read_volts() * self.divider_ratio

    def read(self):
        return self.calibration.apply(self.read_raw())


class CurrentChannel(_AnalogChannel):
    """Measures a current through a shunt plus an optional amplifier.

    voltage at the pin = I * shunt_ohms * amplifier_gain, so the current is the
    pin voltage divided by both. A 0.1 ohm shunt with a gain of 10 gives 1 V per
    amp, which suits a 0..2 A load on a 3.3 V ADC.
    """

    def __init__(self, adc, vref=3.3, shunt_ohms=0.1, amplifier_gain=1.0, oversample=8,
                 calibration=None):
        _AnalogChannel.__init__(self, adc, vref, oversample, calibration)
        if shunt_ohms <= 0.0:
            raise ValueError("shunt_ohms must be positive")
        if amplifier_gain <= 0.0:
            raise ValueError("amplifier_gain must be positive")
        self.shunt_ohms = float(shunt_ohms)
        self.amplifier_gain = float(amplifier_gain)

    def read_raw(self):
        volts = self.read_volts()
        return volts / (self.shunt_ohms * self.amplifier_gain)

    def read(self):
        return self.calibration.apply(self.read_raw())


class PowerChannel:
    """Real power from a voltage and a current channel on the same circuit.

    This assumes a direct current load (power factor 1). For AC it would need
    simultaneous sampling and an RMS calculation, which an ESP32 ADC cannot do
    properly -- see the README.
    """

    def __init__(self, voltage, current):
        self.voltage = voltage
        self.current = current

    def read(self):
        return self.voltage.read() * self.current.read()


def check_wiring(adc_max_volts, expected_volts):
    """Raises when the expected signal would clip the ADC input."""
    if expected_volts > adc_max_volts:
        raise BusError(
            "expected {:.2f} V at the pin but the ADC only measures {:.2f} V; "
            "increase the divider ratio".format(expected_volts, adc_max_volts)
        )
