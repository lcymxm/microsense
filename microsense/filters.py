"""Signal conditioning filters.

Written without annotations or third-party imports so the same file runs under
MicroPython. Every filter keeps its own small buffer and is deterministic, which
is what makes them testable against hand-computed sequences.
"""


class MovingAverage:
    """Arithmetic mean of the last `window` samples."""

    def __init__(self, window):
        if window < 1:
            raise ValueError("window must be at least 1")
        self.window = int(window)
        self._buffer = []
        self._index = 0
        self._total = 0.0

    def update(self, value):
        value = float(value)
        if len(self._buffer) < self.window:
            self._buffer.append(value)
            self._total += value
        else:
            self._total += value - self._buffer[self._index]
            self._buffer[self._index] = value
            self._index = (self._index + 1) % self.window
        return self._total / len(self._buffer)

    @property
    def value(self):
        if not self._buffer:
            return None
        return self._total / len(self._buffer)

    def reset(self):
        self._buffer = []
        self._index = 0
        self._total = 0.0


class MedianFilter:
    """Median of the last `window` samples.

    Worth the extra memory over a moving average when the sensor occasionally
    returns a wild sample: one outlier cannot move the median far.
    """

    def __init__(self, window):
        if window < 1:
            raise ValueError("window must be at least 1")
        self.window = int(window)
        self._buffer = []

    def update(self, value):
        self._buffer.append(float(value))
        if len(self._buffer) > self.window:
            self._buffer.pop(0)
        ordered = sorted(self._buffer)
        middle = len(ordered) // 2
        if len(ordered) % 2 == 1:
            return ordered[middle]
        return (ordered[middle - 1] + ordered[middle]) / 2.0

    @property
    def value(self):
        if not self._buffer:
            return None
        ordered = sorted(self._buffer)
        middle = len(ordered) // 2
        if len(ordered) % 2 == 1:
            return ordered[middle]
        return (ordered[middle - 1] + ordered[middle]) / 2.0

    def reset(self):
        self._buffer = []


class ExponentialFilter:
    """First-order low pass: y = a*x + (1 - a)*y.

    `alpha` near 1 follows the input closely, near 0 smooths hard.
    """

    def __init__(self, alpha, initial=None):
        if not 0.0 < alpha <= 1.0:
            raise ValueError("alpha must be in (0, 1]")
        self.alpha = float(alpha)
        self._value = initial

    def update(self, value):
        value = float(value)
        if self._value is None:
            self._value = value
        else:
            self._value = self.alpha * value + (1.0 - self.alpha) * self._value
        return self._value

    @property
    def value(self):
        return self._value

    def reset(self, initial=None):
        self._value = initial


class ComplementaryFilter:
    """Fuses accelerometer and gyro into pitch and roll.

    The accelerometer gives an absolute but noisy angle, the gyro a smooth but
    drifting rate. A complementary filter trusts the gyro over short intervals
    and the accelerometer over long ones:

        angle = alpha * (angle + rate * dt) + (1 - alpha) * accel_angle

    `alpha` is usually 0.95..0.99 for a 100 Hz loop. Output is in degrees.
    """

    def __init__(self, alpha=0.98):
        if not 0.0 < alpha < 1.0:
            raise ValueError("alpha must be in (0, 1)")
        self.alpha = float(alpha)
        self.pitch = 0.0
        self.roll = 0.0
        self._seeded = False

    def update(self, ax, ay, az, gx, gy, dt):
        """ax/ay/az in g, gx/gy in deg/s, dt in seconds."""
        accel_pitch = _degrees(_atan2(-ax, _sqrt(ay * ay + az * az)))
        accel_roll = _degrees(_atan2(ay, az))

        if not self._seeded:
            # Start from the accelerometer so the first readings are sensible
            # instead of climbing from zero.
            self.pitch = accel_pitch
            self.roll = accel_roll
            self._seeded = True
            return self.pitch, self.roll

        self.pitch = self.alpha * (self.pitch + gy * dt) + (1.0 - self.alpha) * accel_pitch
        self.roll = self.alpha * (self.roll + gx * dt) + (1.0 - self.alpha) * accel_roll
        return self.pitch, self.roll

    def reset(self):
        self.pitch = 0.0
        self.roll = 0.0
        self._seeded = False


def _sqrt(value):
    # math.sqrt exists in MicroPython; kept behind a helper so tests can see the
    # single place it is used.
    import math

    return math.sqrt(value)


def _atan2(y, x):
    import math

    return math.atan2(y, x)


def _degrees(radians):
    return radians * 57.29577951308232
