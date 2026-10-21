"""The sensor node: registration, scheduling, quality and alarms.

This is the part that turns a handful of drivers into something a supervisory
system can trust. The rule it follows is the same one every RTU follows: a value
is never reported without saying how much it can be believed.

    good   the reading was taken within its scheduled interval
    stale  the sensor stopped updating (the loop is blocked, or the device hung)
    fault  the last read raised, so the value on screen is the last good one

A fault keeps the previous value rather than replacing it with zero. Zero looks
like a real measurement; a flagged stale value does not.
"""

from . import clock

GOOD = "good"
STALE = "stale"
FAULT = "fault"

DEFAULT_STALE_FACTOR = 3


class Point:
    """One published quantity inside a sensor."""

    def __init__(self, name, unit="", scale=100, signed=True, decimals=2,
                 hi=None, lo=None, deadband=0.0):
        self.name = name
        self.unit = unit
        self.scale = int(scale)
        self.signed = bool(signed)
        self.decimals = int(decimals)
        self.hi = hi
        self.lo = lo
        self.deadband = float(deadband)

        self.value = None
        self.quality = FAULT
        self.timestamp_ms = 0
        self.error = "no reading yet"
        self.alarm_active = False
        self.alarm_acknowledged = True
        self.alarm_kind = None

    def to_register(self):
        """Value scaled to a 16 bit register, clamped instead of wrapping."""
        if self.value is None:
            return 0
        raw = int(round(self.value * self.scale))
        low, high = (-32768, 32767) if self.signed else (0, 65535)
        if raw < low:
            return low
        if raw > high:
            return high
        return raw

    def to_dict(self):
        return {
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "quality": self.quality,
            "timestamp_ms": self.timestamp_ms,
            "alarm": self.alarm_kind if self.alarm_active else "normal",
            "acknowledged": self.alarm_acknowledged,
            "error": self.error,
        }


class Sensor:
    """A device read as a unit; produces one or more points."""

    def __init__(self, name, reader, interval_ms, points):
        self.name = name
        self.reader = reader
        self.interval_ms = int(interval_ms)
        self.points = points
        self.last_read_ms = None
        self.reads = 0
        self.failures = 0


class SensorNode:
    def __init__(self, node_id="microsense", stale_factor=DEFAULT_STALE_FACTOR):
        self.node_id = node_id
        self.stale_factor = int(stale_factor)
        self._sensors = []
        self._points = []
        self._by_name = {}
        self._start_ms = clock.ticks_ms()
        self.events = []
        self.event_capacity = 256
        self.poll_count = 0

    # -- registration -----------------------------------------------------------

    def add_sensor(self, name, reader, interval_ms=1000, points=None):
        """Registers a device.

        `reader` is called with no arguments and returns either a number (a
        single point named after the sensor) or a dict (one point per key, named
        "<sensor>.<key>").

        `points` maps a key to {"unit": ..., "scale": ..., "signed": ...,
        "decimals": ..., "hi": ..., "lo": ..., "deadband": ...}. It may also be
        a single dict of options when the reader returns a number.
        """
        if name in [sensor.name for sensor in self._sensors]:
            raise ValueError("a sensor named {!r} is already registered".format(name))

        if not points:
            # No options at all: one point, named after the sensor, defaults for
            # everything else.
            specs = {name: {}}
            multi = False
        elif _is_mapping(_first_value(points)):
            # One entry per value the reader returns.
            specs = points
            multi = True
        elif _is_mapping(points):
            # A single set of options for a scalar reader.
            specs = {name: points}
            multi = False
        else:
            raise ValueError("points must be a dict")

        created = []
        for key, options in specs.items():
            full_name = "{}.{}".format(name, key) if multi else name
            point = Point(full_name, **_point_options(options))
            created.append(point)
            self._points.append(point)
            self._by_name[point.name] = point

        sensor = Sensor(name, reader, interval_ms, created)
        self._sensors.append(sensor)
        return sensor

    # -- running ----------------------------------------------------------------

    def poll(self, now_ms=None):
        """Reads every sensor whose interval has elapsed. Returns reads taken."""
        if now_ms is None:
            now_ms = clock.ticks_ms()
        self.poll_count += 1
        taken = 0
        for sensor in self._sensors:
            if sensor.last_read_ms is not None:
                elapsed = clock.ticks_diff(now_ms, sensor.last_read_ms)
                if elapsed < sensor.interval_ms:
                    continue
            self._read(sensor, now_ms)
            taken += 1
        self._age(now_ms)
        return taken

    def _read(self, sensor, now_ms):
        sensor.last_read_ms = now_ms
        try:
            result = sensor.reader()
        except Exception as error:  # noqa: BLE001 - a driver must never kill the loop
            sensor.failures += 1
            for point in sensor.points:
                point.quality = FAULT
                point.error = "{}: {}".format(type(error).__name__, error)
            self._record("fault", sensor.points[0].name, None, str(error), now_ms)
            return

        sensor.reads += 1
        if len(sensor.points) == 1 and not _is_mapping(result):
            self._publish(sensor.points[0], result, now_ms)
            return

        if not _is_mapping(result):
            for point in sensor.points:
                point.quality = FAULT
                point.error = "reader returned {!r}, expected a dict".format(result)
            return

        for point in sensor.points:
            key = point.name.split(".", 1)[1] if "." in point.name else point.name
            if key not in result:
                point.quality = FAULT
                point.error = "reader did not return {!r}".format(key)
                continue
            self._publish(point, result[key], now_ms)

    def _publish(self, point, value, now_ms):
        if value is None:
            point.quality = FAULT
            point.error = "reader returned None"
            return
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            point.quality = FAULT
            point.error = "reading {!r} is not a number".format(value)
            return

        point.value = numeric
        point.quality = GOOD
        point.timestamp_ms = now_ms
        point.error = ""
        self._evaluate_alarm(point, numeric, now_ms)

    def _evaluate_alarm(self, point, value, now_ms):
        kind = None
        if point.hi is not None and value > point.hi:
            kind = "hi"
        elif point.lo is not None and value < point.lo:
            kind = "lo"

        if kind is not None:
            if not point.alarm_active or point.alarm_kind != kind:
                point.alarm_active = True
                point.alarm_kind = kind
                point.alarm_acknowledged = False
                self._record("alarm", point.name, value, kind, now_ms)
            return

        if not point.alarm_active:
            return
        # Clearing needs the value to come back past the deadband, otherwise a
        # value sitting on the limit toggles every scan.
        cleared = True
        if point.alarm_kind == "hi" and point.hi is not None:
            cleared = value <= point.hi - point.deadband
        elif point.alarm_kind == "lo" and point.lo is not None:
            cleared = value >= point.lo + point.deadband
        if cleared:
            point.alarm_active = False
            previous = point.alarm_kind
            point.alarm_kind = None
            self._record("cleared", point.name, value, previous, now_ms)

    def _age(self, now_ms):
        for sensor in self._sensors:
            if sensor.last_read_ms is None:
                continue
            limit = sensor.interval_ms * self.stale_factor
            if clock.ticks_diff(now_ms, sensor.last_read_ms) <= limit:
                continue
            for point in sensor.points:
                if point.quality == GOOD:
                    point.quality = STALE
                    point.error = "no update for over {} ms".format(limit)

    def _record(self, kind, name, value, detail, now_ms):
        self.events.append({
            "kind": kind,
            "point": name,
            "value": value,
            "detail": detail,
            "timestamp_ms": now_ms,
        })
        if len(self.events) > self.event_capacity:
            del self.events[: len(self.events) - self.event_capacity]

    # -- inspection -------------------------------------------------------------

    def points(self):
        return list(self._points)

    def point(self, name):
        return self._by_name.get(name)

    def acknowledge(self, name, now_ms=None):
        point = self._by_name.get(name)
        if point is None or not point.alarm_active:
            return False
        if point.alarm_acknowledged:
            return False
        point.alarm_acknowledged = True
        self._record("ack", point.name, point.value, point.alarm_kind, now_ms or clock.ticks_ms())
        return True

    def acknowledge_all(self, now_ms=None):
        count = 0
        for point in self._points:
            if self.acknowledge(point.name, now_ms):
                count += 1
        return count

    def unacknowledged(self):
        return [point for point in self._points if point.alarm_active and not point.alarm_acknowledged]

    def register_block(self):
        """Holding register image: one register per point, then status words."""
        registers = [point.to_register() for point in self._points]

        flags = 0
        for index, point in enumerate(self._points[:16]):
            if point.alarm_active:
                flags |= 1 << index
        registers.append(flags & 0xFFFF)

        uptime = clock.uptime_seconds(self._start_ms) & 0xFFFFFFFF
        registers.append((uptime >> 16) & 0xFFFF)
        registers.append(uptime & 0xFFFF)
        return registers

    def snapshot(self, now_ms=None):
        if now_ms is None:
            now_ms = clock.ticks_ms()
        # Age lazily as well as during poll(): if the loop is stuck (a long
        # Modbus timeout, say) the values must still be reported as stale rather
        # than silently frozen at "good".
        self._age(now_ms)
        return {
            "node": self.node_id,
            "uptime_s": clock.uptime_seconds(self._start_ms),
            "polls": self.poll_count,
            "unacknowledged": len(self.unacknowledged()),
            "points": [point.to_dict() for point in self._points],
            "events": self.events[-20:],
        }


def _is_mapping(value):
    return hasattr(value, "items") and hasattr(value, "keys")


def _first_value(mapping):
    for value in mapping.values():
        return value
    return None


def _point_options(options):
    allowed = {"unit", "scale", "signed", "decimals", "hi", "lo", "deadband"}
    unknown = set(options) - allowed
    if unknown:
        raise ValueError("unknown point option(s): {}".format(", ".join(sorted(unknown))))
    return dict(options)
