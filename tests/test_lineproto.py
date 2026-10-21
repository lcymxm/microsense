import json

import pytest

from microsense import lineproto


def make_snapshot():
    return {
        "node": "test",
        "uptime_s": 42,
        "polls": 10,
        "unacknowledged": 1,
        "points": [
            {"name": "sht31.temperature", "value": 21.5, "unit": "degC",
             "quality": "good", "timestamp_ms": 1000, "alarm": "normal",
             "acknowledged": True, "error": ""},
            {"name": "adc.current", "value": 0.8571, "unit": "A",
             "quality": "good", "timestamp_ms": 1000, "alarm": "hi",
             "acknowledged": False, "error": ""},
            {"name": "imu.pitch", "value": None, "unit": "deg",
             "quality": "fault", "timestamp_ms": 0, "alarm": "normal",
             "acknowledged": True, "error": "no answer"},
        ],
        "events": [],
    }


def test_json_line_round_trips():
    snapshot = make_snapshot()
    line = lineproto.to_json_line(snapshot)
    assert "\n" not in line
    restored = json.loads(line)
    assert restored["points"][0]["name"] == "sht31.temperature"


def test_json_line_is_compact():
    line = lineproto.to_json_line(make_snapshot())
    assert ", " not in line  # separators are tightened on purpose


def test_csv_header_lists_every_point():
    header = lineproto.csv_header(make_snapshot())
    assert header == "uptime_s,sht31.temperature,adc.current,imu.pitch"


def test_csv_line_formats_values_and_blanks_missing_ones():
    line = lineproto.to_csv_line(make_snapshot())
    assert line == "42,21.500,0.857,"


def test_table_marks_an_unacknowledged_alarm():
    table = lineproto.to_text_table(make_snapshot())
    assert "sht31.temperature" in table
    assert "hi (unacked)" in table
    assert "--" in table  # the missing value


def test_render_dispatches_by_name():
    snapshot = make_snapshot()
    assert lineproto.render(snapshot, "json").startswith("{")
    assert lineproto.render(snapshot, "csv").startswith("42,")
    assert "point" in lineproto.render(snapshot, "table")


def test_render_rejects_an_unknown_format():
    with pytest.raises(ValueError):
        lineproto.render(make_snapshot(), "yaml")
