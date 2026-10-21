"""Two-point linear calibration for analog channels.

An ADC channel is never accurate on its own: resistor tolerances, the reference
voltage and the sensor's own offset all shift the reading. A two point
calibration turns "raw counts" into "engineering units" with the smallest amount
of ceremony that actually works on a bench.
"""


class LinearCalibration:
    """value = gain * raw + offset."""

    def __init__(self, gain=1.0, offset=0.0):
        self.gain = float(gain)
        self.offset = float(offset)

    def apply(self, raw):
        return self.gain * raw + self.offset

    def invert(self, value):
        """Engineering units back to raw counts (used to check a calibration)."""
        if self.gain == 0.0:
            raise ZeroDivisionError("calibration gain is zero")
        return (value - self.offset) / self.gain

    @classmethod
    def two_point(cls, raw_low, ref_low, raw_high, ref_high):
        """Solve for gain and offset from two measured reference points."""
        if raw_high == raw_low:
            raise ValueError("the two raw readings are identical")
        gain = (ref_high - ref_low) / (raw_high - raw_low)
        offset = ref_low - gain * raw_low
        return cls(gain, offset)

    @classmethod
    def from_points(cls, points):
        """Convenience wrapper: [(raw, reference), (raw, reference)]."""
        if len(points) != 2:
            raise ValueError("two points are required")
        (raw_low, ref_low), (raw_high, ref_high) = points
        return cls.two_point(raw_low, ref_low, raw_high, ref_high)

    def to_dict(self):
        return {"gain": self.gain, "offset": self.offset}

    @classmethod
    def from_dict(cls, data):
        return cls(data.get("gain", 1.0), data.get("offset", 0.0))

    def __repr__(self):
        return "LinearCalibration(gain={!r}, offset={!r})".format(self.gain, self.offset)


def save(path, calibrations):
    """Writes {name: LinearCalibration} (or plain dicts) to a JSON file."""
    import json

    payload = {}
    for name, calibration in calibrations.items():
        if isinstance(calibration, LinearCalibration):
            payload[name] = calibration.to_dict()
        else:
            payload[name] = dict(calibration)
    with open(path, "w") as handle:
        json.dump(payload, handle)


def load(path):
    """Reads the JSON written by save(); returns {name: LinearCalibration}."""
    import json

    with open(path) as handle:
        payload = json.load(handle)
    return {name: LinearCalibration.from_dict(data) for name, data in payload.items()}
