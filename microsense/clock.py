# MicroPython-friendly clock.
#
# The board exposes time.ticks_ms()/ticks_diff(), which wrap around instead of
# growing forever; CPython has neither. Everything in this package asks this
# module for time so that the same code runs on a Pico and in CI.

try:  # MicroPython
    import time

    _ticks_ms = time.ticks_ms
    _ticks_diff = time.ticks_diff
    _sleep_ms = time.sleep_ms
    MICROPYTHON = True
except AttributeError:  # CPython
    import time

    MICROPYTHON = False

    def _ticks_ms():
        return int(time.monotonic() * 1000)

    def _ticks_diff(new, old):
        return new - old

    def _sleep_ms(milliseconds):
        time.sleep(milliseconds / 1000.0)


def ticks_ms():
    """Current time in milliseconds. May wrap; compare with ticks_diff()."""
    return _ticks_ms()


def ticks_diff(new, old):
    """Signed difference in milliseconds that survives a wrap-around."""
    return _ticks_diff(new, old)


def sleep_ms(milliseconds):
    if milliseconds > 0:
        _sleep_ms(int(milliseconds))


def uptime_seconds(start_ms):
    """Whole seconds elapsed since `start_ms`."""
    return ticks_diff(ticks_ms(), start_ms) // 1000
