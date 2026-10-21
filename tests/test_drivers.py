import math

import pytest

from microsense.bus import ADC, BusError
from microsense.drivers import BH1750, MPU6050, SHT31, CurrentChannel, VoltageChannel, crc8
from microsense.sim import BH1750Device, FakeADC, FakeI2C, MPU6050Device, SHT31Device, SimulatedPlant


class FixedPlant:
    """Plant stand-in with values the test chooses."""

    def __init__(self, temperature=21.5, humidity=48.0, lux=250.0,
                 accel=(0.0, 0.0, 1.0), gyro=(0.0, 0.0, 0.0)):
        self.temperature = temperature
        self.humidity = humidity
        self.lux = lux
        self.accel = accel
        self.gyro = gyro


def make_i2c(**kwargs):
    plant = FixedPlant(**kwargs)
    return FakeI2C({
        SHT31Device.ADDRESS: SHT31Device(plant),
        BH1750Device.ADDRESS: BH1750Device(plant),
        MPU6050Device.ADDRESS: MPU6050Device(plant),
    }), plant


class SequenceADC(ADC):
    """Returns the ratios given, then repeats the last one."""

    def __init__(self, ratios):
        self._ratios = list(ratios)
        self._index = 0

    def read_ratio(self):
        value = self._ratios[min(self._index, len(self._ratios) - 1)]
        self._index += 1
        return value


# -- CRC -------------------------------------------------------------------


def test_crc8_matches_the_datasheet_example():
    # SHT3x datasheet: the bytes 0xBE 0xEF carry the CRC 0x92.
    assert crc8(bytes([0xBE, 0xEF])) == 0x92


def test_crc8_of_an_empty_message_is_the_seed():
    assert crc8(b"") == 0xFF


# -- SHT31 -----------------------------------------------------------------


def test_sht31_returns_the_plant_temperature_and_humidity():
    i2c, _ = make_i2c(temperature=23.4, humidity=41.5)
    temperature, humidity = SHT31(i2c).read()
    assert temperature == pytest.approx(23.4, abs=0.01)
    assert humidity == pytest.approx(41.5, abs=0.01)


def test_sht31_handles_below_zero():
    i2c, _ = make_i2c(temperature=-12.5)
    temperature, _ = SHT31(i2c).read()
    assert temperature == pytest.approx(-12.5, abs=0.01)


def test_sht31_rejects_a_bad_crc():
    i2c, _ = make_i2c()
    device = i2c.devices[SHT31Device.ADDRESS]
    original = device._encode

    def corrupt(temperature, humidity):
        payload = bytearray(original(temperature, humidity))
        payload[2] ^= 0xFF  # break the temperature CRC
        return bytes(payload)

    device._encode = corrupt
    with pytest.raises(BusError) as error:
        SHT31(i2c).read()
    assert "CRC" in str(error.value)


def test_sht31_missing_device_is_reported():
    with pytest.raises(BusError):
        SHT31(FakeI2C({})).read()


def test_sht31_soft_reset_is_accepted():
    i2c, _ = make_i2c()
    sensor = SHT31(i2c)
    sensor.soft_reset()
    temperature, _ = sensor.read()
    assert temperature == pytest.approx(21.5, abs=0.01)


# -- BH1750 ----------------------------------------------------------------


def test_bh1750_converts_counts_to_lux():
    i2c, _ = make_i2c(lux=250.0)
    sensor = BH1750(i2c)
    sensor.start()
    assert sensor.read() == pytest.approx(250.0, abs=1.0)


def test_bh1750_start_is_implicit_on_first_read():
    i2c, _ = make_i2c(lux=100.0)
    assert BH1750(i2c).read() == pytest.approx(100.0, abs=1.0)


def test_bh1750_wakes_up_for_the_next_read():
    # power_off() puts the chip to sleep between samples; a read powers it back
    # up rather than failing, which is what a duty-cycled node wants.
    i2c, _ = make_i2c(lux=120.0)
    sensor = BH1750(i2c)
    sensor.start()
    first = sensor.read()
    sensor.power_off()
    assert sensor.read() == pytest.approx(first, abs=1.0)


# -- MPU6050 ---------------------------------------------------------------


def test_mpu6050_reports_its_identity():
    i2c, _ = make_i2c()
    assert MPU6050(i2c).who_am_i() == 0x68


def test_mpu6050_converts_counts_to_g_and_dps():
    i2c, _ = make_i2c(accel=(0.0, 0.0, 1.0), gyro=(90.0, -45.0, 0.0))
    sample = MPU6050(i2c).read()
    assert sample["az"] == pytest.approx(1.0, abs=0.001)
    assert sample["gx"] == pytest.approx(90.0, abs=0.5)
    assert sample["gy"] == pytest.approx(-45.0, abs=0.5)


def test_mpu6050_scales_with_the_configured_range():
    # On the +-8 g range the same acceleration produces a quarter of the counts,
    # so the driver must divide by the smaller scale factor.
    i2c, _ = make_i2c(accel=(0.0, 0.0, 4.0))
    driver = MPU6050(i2c, accel_range=8)
    sample = driver.read()
    assert driver._accel_index == 2
    assert sample["az"] == pytest.approx(4.0, abs=0.01)


def test_mpu6050_rejects_an_unsupported_range():
    i2c, _ = make_i2c()
    with pytest.raises(ValueError):
        MPU6050(i2c, accel_range=3)


def test_mpu6050_reads_before_wake_up_fail():
    i2c, _ = make_i2c()
    driver = MPU6050(i2c)  # construction wakes the device
    i2c.devices[MPU6050Device.ADDRESS].awake = False
    with pytest.raises(BusError):
        driver.read()


# -- analog channels -------------------------------------------------------


def test_voltage_channel_applies_the_divider():
    channel = VoltageChannel(FakeADC(0.5), vref=3.3, divider_ratio=5.0, oversample=1)
    # 0.5 * 3.3 V at the pin, times 5 through the divider.
    assert channel.read() == pytest.approx(8.25)


def test_current_channel_applies_the_shunt_and_gain():
    channel = CurrentChannel(FakeADC(0.5), vref=3.3, shunt_ohms=0.1, amplifier_gain=10.0,
                             oversample=1)
    # 1.65 V across 0.1 ohm with a gain of 10 is 1.65 A.
    assert channel.read() == pytest.approx(1.65)


def test_oversampling_averages_the_conversions():
    adc = SequenceADC([0.2, 0.4, 0.6, 0.8])
    channel = VoltageChannel(adc, vref=1.0, divider_ratio=1.0, oversample=4)
    assert channel.read() == pytest.approx(0.5)


def test_oversampling_reads_exactly_its_count():
    adc = SequenceADC([0.5] * 10)
    VoltageChannel(adc, vref=1.0, divider_ratio=1.0, oversample=3).read()
    assert adc._index == 3


def test_calibration_corrects_a_divider_error():
    from microsense.calibration import LinearCalibration

    calibration = LinearCalibration.two_point(8.0, 8.1, 16.0, 16.2)
    channel = VoltageChannel(FakeADC(0.5), vref=3.3, divider_ratio=5.0, oversample=1,
                             calibration=calibration)
    # The raw reading is 8.25 V; the calibration pulls it onto the real value.
    assert channel.read() == pytest.approx(calibration.apply(8.25))


def test_channels_reject_impossible_geometry():
    with pytest.raises(ValueError):
        VoltageChannel(FakeADC(0.5), divider_ratio=0.0)
    with pytest.raises(ValueError):
        CurrentChannel(FakeADC(0.5), shunt_ohms=0.0)
    with pytest.raises(ValueError):
        VoltageChannel(FakeADC(0.5), vref=0.0)
    with pytest.raises(ValueError):
        VoltageChannel(FakeADC(0.5), oversample=0)


def test_adc_confidence_check_catches_a_clipping_input():
    from microsense.drivers.adc import check_wiring

    with pytest.raises(BusError):
        check_wiring(3.3, 4.5)
    check_wiring(3.3, 2.4)  # should not raise


# -- the simulated plant is self consistent ---------------------------------


def test_simulated_plant_stays_physical():
    plant = SimulatedPlant(seed=3)
    now = 0
    for _ in range(200):
        now += 100
        plant.tick(now)
        assert -10.0 < plant.temperature < 40.0
        assert 0.0 <= plant.humidity <= 100.0
        assert plant.lux >= 0.0
        assert 0.0 <= plant.current < 5.0
        assert math.fabs(plant.accel[2]) <= 1.5
