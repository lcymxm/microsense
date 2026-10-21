import math

import pytest

from microsense.filters import (
    ComplementaryFilter,
    ExponentialFilter,
    MedianFilter,
    MovingAverage,
)


def test_moving_average_fills_then_slides():
    average = MovingAverage(3)
    assert average.update(1) == pytest.approx(1.0)
    assert average.update(2) == pytest.approx(1.5)
    assert average.update(3) == pytest.approx(2.0)
    # The window slides: 4 replaces the oldest sample, so (2 + 3 + 4) / 3.
    assert average.update(4) == pytest.approx(3.0)
    assert average.value == pytest.approx(3.0)


def test_moving_average_window_of_one_is_the_input():
    average = MovingAverage(1)
    assert average.update(7) == pytest.approx(7.0)
    assert average.update(9) == pytest.approx(9.0)


def test_moving_average_rejects_a_silly_window():
    with pytest.raises(ValueError):
        MovingAverage(0)


def test_moving_average_reset():
    average = MovingAverage(2)
    average.update(5)
    average.reset()
    assert average.value is None
    assert average.update(1) == pytest.approx(1.0)


def test_median_ignores_a_single_outlier():
    median = MedianFilter(3)
    median.update(1.0)
    median.update(100.0)  # a glitch
    # The median of (1, 100, 2) is 2, while the mean would be 34.3.
    assert median.update(2.0) == pytest.approx(2.0)


def test_median_of_an_even_window_averages_the_middle_two():
    median = MedianFilter(4)
    for value in (1.0, 2.0, 3.0, 4.0):
        result = median.update(value)
    assert result == pytest.approx(2.5)


def test_exponential_filter_moves_towards_the_input():
    low_pass = ExponentialFilter(0.5)
    assert low_pass.update(10.0) == pytest.approx(10.0)
    assert low_pass.update(20.0) == pytest.approx(15.0)
    assert low_pass.update(20.0) == pytest.approx(17.5)


def test_exponential_filter_rejects_bad_alpha():
    with pytest.raises(ValueError):
        ExponentialFilter(0.0)
    with pytest.raises(ValueError):
        ExponentialFilter(1.5)


def test_complementary_filter_starts_from_the_accelerometer():
    # Tilted 30 degrees about y: ax = -sin(30), az = cos(30).
    angle = math.radians(30.0)
    filter_ = ComplementaryFilter(0.98)
    pitch, roll = filter_.update(-math.sin(angle), 0.0, math.cos(angle), 0.0, 0.0, 0.1)
    assert pitch == pytest.approx(30.0, abs=0.01)
    assert roll == pytest.approx(0.0, abs=0.01)


def test_complementary_filter_trusts_the_gyro_over_short_intervals():
    filter_ = ComplementaryFilter(0.98)
    # Level, then the gyro reports 90 deg/s about y for 0.1 s.
    filter_.update(0.0, 0.0, 1.0, 0.0, 0.0, 0.1)
    pitch, _ = filter_.update(0.0, 0.0, 1.0, 0.0, 90.0, 0.1)
    # 0.98 * (0 + 9 deg) + 0.02 * 0 deg = 8.82 deg
    assert pitch == pytest.approx(8.82, abs=0.01)


def test_complementary_filter_pulls_towards_the_accelerometer_slowly():
    filter_ = ComplementaryFilter(0.9)
    filter_.update(0.0, 0.0, 1.0, 0.0, 0.0, 0.1)
    # The board is now tilted but the gyro reports nothing: the angle has to
    # creep towards the accelerometer reading at (1 - alpha).
    angle = math.radians(45.0)
    pitch = 0.0
    for _ in range(80):
        pitch, _ = filter_.update(-math.sin(angle), 0.0, math.cos(angle), 0.0, 0.0, 0.1)
    assert pitch == pytest.approx(45.0, abs=0.5)


def test_complementary_filter_reset_reseeds():
    filter_ = ComplementaryFilter()
    filter_.update(0.0, 0.0, 1.0, 0.0, 90.0, 0.1)
    filter_.reset()
    assert filter_.pitch == 0.0
    angle = math.radians(10.0)
    pitch, _ = filter_.update(-math.sin(angle), 0.0, math.cos(angle), 0.0, 0.0, 0.1)
    assert pitch == pytest.approx(10.0, abs=0.01)
