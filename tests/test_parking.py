from datetime import datetime, timedelta

from canbus import ParkingDetector

T0 = datetime(2026, 11, 18, 12, 0, 0)


def at(seconds):
    return T0 + timedelta(seconds=seconds)


def test_off_without_ignition():
    d = ParkingDetector()
    assert d.update(at(0), False, 0, True) is False


def test_pulling_out_counts_as_parking():
    d = ParkingDetector()
    assert d.update(at(0), True, 0, False) is True
    assert d.update(at(5), True, 15, False) is True  # between the thresholds: kept
    assert d.update(at(10), True, 30, False) is False


def test_motorway_and_short_stop_is_not_parking():
    d = ParkingDetector()
    d.update(at(0), True, 100, False)
    assert d.update(at(20), True, 0, False) is False  # traffic lights
    assert d.update(at(50), True, 5, False) is False


def test_slow_for_a_while_after_driving_is_parking():
    d = ParkingDetector()
    d.update(at(0), True, 80, False)
    d.update(at(30), True, 20, False)  # entering the campsite
    assert d.update(at(59), True, 8, False) is False
    assert d.update(at(61), True, 8, False) is True
    assert d.update(at(70), True, 18, False) is True  # manoeuvring a bit faster


def test_reverse_gear_is_parking_at_once():
    d = ParkingDetector()
    d.update(at(0), True, 50, False)
    assert d.update(at(5), True, 3, True) is True


def test_missing_speed_means_standing():
    d = ParkingDetector()
    assert d.update(at(0), True, None, False) is True


def test_ignition_off_resets():
    d = ParkingDetector()
    d.update(at(0), True, 100, False)
    d.update(at(10), False, None, False)
    assert d.update(at(11), True, 0, False) is True
