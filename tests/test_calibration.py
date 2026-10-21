import json

import pytest

from microsense.calibration import LinearCalibration, load, save


def test_identity_by_default():
    calibration = LinearCalibration()
    assert calibration.apply(12.5) == pytest.approx(12.5)


def test_two_point_fits_a_gain_and_offset():
    # 0 counts -> 0 V, 1000 counts -> 10 V: ten millivolts per count.
    calibration = LinearCalibration.two_point(0, 0.0, 1000, 10.0)
    assert calibration.gain == pytest.approx(0.01)
    assert calibration.offset == pytest.approx(0.0)
    assert calibration.apply(500) == pytest.approx(5.0)


def test_two_point_handles_an_offset():
    # 100 counts -> 5 V, 200 counts -> 15 V.
    calibration = LinearCalibration.two_point(100, 5.0, 200, 15.0)
    assert calibration.gain == pytest.approx(0.1)
    assert calibration.offset == pytest.approx(-5.0)
    assert calibration.apply(100) == pytest.approx(5.0)
    assert calibration.apply(200) == pytest.approx(15.0)


def test_invert_undoes_apply():
    calibration = LinearCalibration(0.25, -3.0)
    assert calibration.invert(calibration.apply(42.0)) == pytest.approx(42.0)


def test_two_point_rejects_identical_raw_readings():
    with pytest.raises(ValueError):
        LinearCalibration.two_point(100, 1.0, 100, 2.0)


def test_from_points_takes_two_pairs():
    calibration = LinearCalibration.from_points([(0, 0.0), (2048, 3.3)])
    assert calibration.apply(2048) == pytest.approx(3.3)
    with pytest.raises(ValueError):
        LinearCalibration.from_points([(0, 0.0)])


def test_invert_refuses_a_zero_gain():
    with pytest.raises(ZeroDivisionError):
        LinearCalibration(0.0, 1.0).invert(2.0)


def test_round_trips_through_a_dict():
    calibration = LinearCalibration(0.125, -1.5)
    restored = LinearCalibration.from_dict(calibration.to_dict())
    assert restored.gain == pytest.approx(0.125)
    assert restored.offset == pytest.approx(-1.5)


def test_save_and_load(tmp_path):
    path = tmp_path / "calibration.json"
    save(path, {"voltage": LinearCalibration(0.01, 0.2), "current": LinearCalibration(0.5, 0.0)})

    payload = json.loads(path.read_text())
    assert payload["voltage"]["gain"] == pytest.approx(0.01)

    restored = load(path)
    assert set(restored) == {"voltage", "current"}
    assert restored["voltage"].apply(100) == pytest.approx(1.2)


def test_save_accepts_plain_dicts(tmp_path):
    path = tmp_path / "calibration.json"
    save(path, {"voltage": {"gain": 2.0, "offset": 1.0}})
    assert load(path)["voltage"].apply(1.0) == pytest.approx(3.0)
