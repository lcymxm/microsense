import json

import pytest

from microsense.node import FAULT, GOOD, STALE, SensorNode


class Clock:
    """Feeds explicit timestamps into the node instead of sleeping."""

    def __init__(self):
        self.now = 1000

    def advance(self, milliseconds):
        self.now += milliseconds
        return self.now


def make_node():
    return SensorNode("test-node")


def test_scalar_sensor_publishes_one_point():
    node = make_node()
    node.add_sensor("temperature", lambda: 21.5, interval_ms=1000,
                    points={"unit": "degC", "scale": 100})
    node.poll(1000)

    points = node.points()
    assert len(points) == 1
    assert points[0].name == "temperature"
    assert points[0].value == pytest.approx(21.5)
    assert points[0].quality == GOOD


def test_dict_sensor_publishes_one_point_per_key():
    node = make_node()
    node.add_sensor("sht31", lambda: {"temperature": 21.5, "humidity": 45.0}, 1000,
                    points={"temperature": {"unit": "degC"}, "humidity": {"unit": "%"}})
    node.poll(1000)

    names = [point.name for point in node.points()]
    assert names == ["sht31.temperature", "sht31.humidity"]


def test_sensor_without_options_still_publishes():
    node = make_node()
    node.add_sensor("plain", lambda: 1.0)
    node.poll(1000)
    assert node.point("plain").value == pytest.approx(1.0)


def test_sensor_interval_is_respected():
    node = make_node()
    reads = {"count": 0}

    def reader():
        reads["count"] += 1
        return 1.0

    node.add_sensor("slow", reader, interval_ms=1000)
    node.poll(1000)
    node.poll(1200)  # still inside the interval
    node.poll(2100)  # now due
    assert reads["count"] == 2


def test_a_raising_reader_marks_the_points_faulted():
    node = make_node()
    node.add_sensor("broken", lambda: (_ for _ in ()).throw(OSError("no answer")), 1000)
    node.poll(1000)
    point = node.point("broken")
    assert point.quality == FAULT
    assert "no answer" in point.error


def test_a_fault_keeps_the_last_good_value():
    # Replacing the value with zero would look like a real measurement; keeping
    # the old one with a bad quality flag does not.
    state = {"fail": False}

    def reader():
        if state["fail"]:
            raise OSError("bus error")
        return 12.5

    node = make_node()
    node.add_sensor("v", reader, 1000)
    node.poll(1000)
    state["fail"] = True
    node.poll(2000)
    point = node.point("v")
    assert point.value == pytest.approx(12.5)
    assert point.quality == FAULT


def test_values_go_stale_when_the_loop_stops():
    # Staleness is about the node, not the sensor: it means the sampling loop
    # stopped running. It has to be visible without another poll, otherwise a
    # blocked loop would keep reporting "good" forever.
    node = make_node()
    node.add_sensor("v", lambda: 1.0, interval_ms=100)
    node.poll(1000)
    assert node.point("v").quality == GOOD

    payload = node.snapshot(1000 + 100 * 3 + 1)
    assert payload["points"][0]["quality"] == STALE
    assert node.point("v").quality == STALE


def test_a_fresh_sample_after_staleness_recovers():
    node = make_node()
    node.add_sensor("v", lambda: 1.0, interval_ms=100)
    node.poll(1000)
    node.snapshot(5000)
    assert node.point("v").quality == STALE
    node.poll(6000)
    assert node.point("v").quality == GOOD


def test_hi_alarm_trips_and_is_unacknowledged():
    node = make_node()
    node.add_sensor("t", lambda: 35.0, 1000, points={"hi": 30.0, "deadband": 1.0})
    node.poll(1000)
    point = node.point("t")
    assert point.alarm_active
    assert point.alarm_kind == "hi"
    assert not point.alarm_acknowledged
    assert len(node.unacknowledged()) == 1
    assert node.events[-1]["kind"] == "alarm"


def test_alarm_clears_only_below_limit_minus_deadband():
    values = {"t": 35.0}
    node = make_node()
    node.add_sensor("t", lambda: values["t"], 1000, points={"hi": 30.0, "deadband": 1.0})
    node.poll(1000)
    assert node.point("t").alarm_active

    values["t"] = 29.5  # below the limit, but inside the deadband
    node.poll(2000)
    assert node.point("t").alarm_active

    values["t"] = 28.0
    node.poll(3000)
    assert not node.point("t").alarm_active
    assert node.events[-1]["kind"] == "cleared"


def test_lo_alarm_trips_downwards():
    node = make_node()
    node.add_sensor("v", lambda: 9.0, 1000, points={"lo": 10.0})
    node.poll(1000)
    assert node.point("v").alarm_active
    assert node.point("v").alarm_kind == "lo"


def test_acknowledge_marks_the_alarm_seen_but_leaves_it_active():
    node = make_node()
    node.add_sensor("t", lambda: 35.0, 1000, points={"hi": 30.0})
    node.poll(1000)

    assert node.acknowledge("t", 1500) is True
    point = node.point("t")
    assert point.alarm_active  # still in alarm
    assert point.alarm_acknowledged  # but no longer unseen
    assert node.unacknowledged() == []

    # Acknowledging twice is not an error, just a no-op.
    assert node.acknowledge("t", 1600) is False


def test_acknowledge_unknown_or_quiet_point_is_false():
    node = make_node()
    node.add_sensor("t", lambda: 20.0, 1000, points={"hi": 30.0})
    node.poll(1000)
    assert node.acknowledge("nope") is False
    assert node.acknowledge("t") is False


def test_register_block_scales_and_clamps():
    node = make_node()
    node.add_sensor("a", lambda: 21.5, 1000, points={"scale": 100})
    node.add_sensor("b", lambda: 1000.0, 1000, points={"scale": 100, "signed": True})
    node.add_sensor("c", lambda: -5.0, 1000, points={"scale": 1, "signed": False})
    node.poll(1000)
    registers = node.register_block()
    assert registers[0] == 2150
    assert registers[1] == 32767  # clamped, not wrapped
    assert registers[2] == 0  # unsigned cannot go negative


def test_register_block_ends_with_flags_and_uptime():
    node = make_node()
    node.add_sensor("t", lambda: 35.0, 1000, points={"hi": 30.0})
    node.poll(1000)
    registers = node.register_block()
    values = len(node.points())
    assert len(registers) == values + 3
    assert registers[values] == 1  # bit 0: the first point is in alarm
    assert 0 <= registers[values + 1] <= 0xFFFF
    assert 0 <= registers[values + 2] <= 0xFFFF


def test_alarm_flag_bitfield_covers_the_points():
    node = make_node()
    for index in range(3):
        node.add_sensor("p{}".format(index), lambda value=index: float(value), 1000,
                        points={"hi": 1.5})
    node.poll(1000)
    # Only the third reading (2.0) exceeds 1.5.
    flags = node.register_block()[-3]
    assert flags == 0b100


def test_snapshot_is_json_serialisable():
    node = make_node()
    node.add_sensor("sht31", lambda: {"temperature": 21.5, "humidity": 45.0}, 1000,
                    points={"temperature": {"unit": "degC"}, "humidity": {"unit": "%"}})
    node.poll(1000)
    payload = json.dumps(node.snapshot(1000))
    restored = json.loads(payload)
    assert restored["node"] == "test-node"
    assert len(restored["points"]) == 2
    assert restored["points"][0]["quality"] == GOOD


def test_duplicate_sensor_names_are_rejected():
    node = make_node()
    node.add_sensor("a", lambda: 1.0)
    with pytest.raises(ValueError):
        node.add_sensor("a", lambda: 2.0)


def test_unknown_point_option_is_rejected():
    node = make_node()
    with pytest.raises(ValueError):
        node.add_sensor("a", lambda: 1.0, points={"colour": "red"})


def test_reader_returning_none_is_a_fault():
    node = make_node()
    node.add_sensor("a", lambda: None, 1000)
    node.poll(1000)
    assert node.point("a").quality == FAULT


def test_reader_returning_garbage_is_a_fault():
    node = make_node()
    node.add_sensor("a", lambda: "hot", 1000)
    node.poll(1000)
    point = node.point("a")
    assert point.quality == FAULT
    assert "not a number" in point.error


def test_missing_dict_key_is_a_fault_for_that_point_only():
    node = make_node()
    node.add_sensor("s", lambda: {"temperature": 20.0}, 1000,
                    points={"temperature": {}, "humidity": {}})
    node.poll(1000)
    assert node.point("s.temperature").quality == GOOD
    assert node.point("s.humidity").quality == FAULT


def test_events_are_capped():
    node = make_node()
    node.event_capacity = 5
    counter = {"n": 0}

    def reader():
        counter["n"] += 1
        return 40.0 if counter["n"] % 2 else 10.0

    node.add_sensor("t", reader, 1000, points={"hi": 30.0, "deadband": 0.0})
    for step in range(40):
        node.poll(1000 + step * 1000)
    assert len(node.events) == 5
