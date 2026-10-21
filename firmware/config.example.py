"""Copy this file to the board as ``config.py`` and edit the pins.

The defaults suit an ESP32 dev board with the sensors on the default I2C pins.
"""

NODE_ID = "microsense-1"

# I2C pins (ESP32 defaults; a Pico would use 4 and 5).
SCL_PIN = 22
SDA_PIN = 21

# ADC pin for the voltage/current channels.
ADC_PIN = 34

# Modbus TCP. Set the port to 0 to disable the server.
BIND = "0.0.0.0"
MODBUS_PORT = 1502
UNIT_ID = 1

# Sampling period of the loop, in milliseconds. Individual sensors keep their own
# interval; this is how often the scheduler gets a chance to run.
POLL_MS = 100

# Print one JSON line per poll to the REPL. Handy while wiring up, noisy later.
PRINT_JSON = False
