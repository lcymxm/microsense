"""Command line entry point: dump once, or simulate a node and serve Modbus.

Everything here runs on CPython (with the register-level fakes) *and* on
MicroPython, so a board can be exercised from the REPL before any hardware is
wired up.
"""

from . import clock, lineproto, sim
from .bus import MachineADC, MachineI2C
from .drivers import BH1750, MPU6050, SHT31, CurrentChannel, VoltageChannel
from .filters import ComplementaryFilter
from .node import SensorNode
from .modbus import ModbusTcpServer

# The published point order. It is also the Modbus register order, so changing it
# breaks every tag table that points at this node.
POINT_ORDER = (
    "sht31.temperature",
    "sht31.humidity",
    "bh1750.lux",
    "imu.ax",
    "imu.ay",
    "imu.az",
    "imu.pitch",
    "imu.roll",
    "adc.voltage",
    "adc.current",
    "adc.power",
)

# Register offsets that are not points.
ALARM_FLAGS_REGISTER = len(POINT_ORDER)
UPTIME_HIGH_REGISTER = len(POINT_ORDER) + 1
UPTIME_LOW_REGISTER = len(POINT_ORDER) + 2
REGISTER_COUNT = len(POINT_ORDER) + 3

# Analog front end. A 12 V rail through a 5:1 divider lands at 2.4 V on a 3.3 V
# ADC; a 0.1 ohm shunt with a gain of 10 gives 1 V per amp.
VREF = 3.3
DIVIDER_RATIO = 5.0
SHUNT_OHMS = 0.1
AMPLIFIER_GAIN = 10.0


def build_node(use_hardware=False, node_id="microsense-1"):
    """Wires the drivers into a node.

    With `use_hardware=False` everything talks to the fakes, which is what makes
    `python -m microsense simulate` work on a laptop.
    """
    if use_hardware:
        i2c = MachineI2C(0, scl=_arg_int("SCL_PIN", 22), sda=_arg_int("SDA_PIN", 21))
        adc_voltage = MachineADC(_arg_int("ADC_V_PIN", 34), vref=VREF)
        adc_current = MachineADC(_arg_int("ADC_I_PIN", 35), vref=VREF)
        plant = None
    else:
        i2c, adc_voltage, adc_current, plant = sim.build_simulated_bus(
            vref=VREF, divider_ratio=DIVIDER_RATIO, shunt_ohms=SHUNT_OHMS,
            amplifier_gain=AMPLIFIER_GAIN)

    sht31 = SHT31(i2c)
    bh1750 = BH1750(i2c)
    mpu = MPU6050(i2c, accel_range=2, gyro_range=250)
    voltage = VoltageChannel(adc_voltage, vref=VREF, divider_ratio=DIVIDER_RATIO)
    current = CurrentChannel(adc_current, vref=VREF, shunt_ohms=SHUNT_OHMS,
                             amplifier_gain=AMPLIFIER_GAIN)
    attitude = ComplementaryFilter(alpha=0.98)
    imu_state = {"last_ms": None}

    node = SensorNode(node_id)

    def read_sht31():
        temperature, humidity = sht31.read()
        return {"temperature": temperature, "humidity": humidity}

    def read_imu():
        sample = mpu.read()
        now = clock.ticks_ms()
        if imu_state["last_ms"] is None:
            dt = 0.05
        else:
            dt = clock.ticks_diff(now, imu_state["last_ms"]) / 1000.0
            if dt <= 0.0:
                dt = 0.001
        imu_state["last_ms"] = now
        pitch, roll = attitude.update(sample["ax"], sample["ay"], sample["az"],
                                      sample["gx"], sample["gy"], dt)
        return {
            "ax": sample["ax"],
            "ay": sample["ay"],
            "az": sample["az"],
            "pitch": pitch,
            "roll": roll,
        }

    def read_power():
        volts = voltage.read()
        amps = current.read()
        return {"voltage": volts, "current": amps, "power": volts * amps}

    node.add_sensor("sht31", read_sht31, 2000, points={
        "temperature": {"unit": "degC", "scale": 100, "decimals": 2,
                        "hi": 30.0, "lo": 5.0, "deadband": 0.3},
        "humidity": {"unit": "%", "scale": 100, "decimals": 1,
                     "hi": 80.0, "lo": 20.0, "deadband": 2.0},
    })
    node.add_sensor("bh1750", lambda: {"lux": bh1750.read()}, 1000,
                    points={"lux": {"unit": "lux", "scale": 1, "decimals": 0,
                                    "signed": False}})
    node.add_sensor("imu", read_imu, 100, points={
        "ax": {"unit": "g", "scale": 1000, "decimals": 3},
        "ay": {"unit": "g", "scale": 1000, "decimals": 3},
        "az": {"unit": "g", "scale": 1000, "decimals": 3},
        "pitch": {"unit": "deg", "scale": 100, "decimals": 1, "hi": 45.0, "lo": -45.0,
                  "deadband": 1.0},
        "roll": {"unit": "deg", "scale": 100, "decimals": 1},
    })
    node.add_sensor("adc", read_power, 500, points={
        "voltage": {"unit": "V", "scale": 100, "decimals": 2, "signed": False,
                    "hi": 13.2, "lo": 10.8, "deadband": 0.05},
        "current": {"unit": "A", "scale": 1000, "decimals": 3, "signed": False,
                    "hi": 1.2, "lo": 0.1, "deadband": 0.01},
        "power": {"unit": "W", "scale": 100, "decimals": 2, "signed": False},
    })

    node.register_order = POINT_ORDER
    return node, plant


def _arg_int(environment_name, default):
    import os

    return int(os.environ.get(environment_name, default))


def _command_dump(args):
    node, _ = build_node(use_hardware=args.hardware)
    node.poll()
    print(lineproto.render(node.snapshot(), args.format))
    return 0


def _command_run(args):
    node, plant = build_node(use_hardware=args.hardware)
    server = None
    if args.modbus_port:
        server = ModbusTcpServer(node.register_block, host=args.bind, port=args.modbus_port)
        server.start()

    start_ms = clock.ticks_ms()
    last_print = None
    printed_header = False

    try:
        while True:
            if plant is not None:
                plant.tick()
            node.poll()

            now = clock.ticks_ms()
            if last_print is None or clock.ticks_diff(now, last_print) >= args.interval_ms:
                last_print = now
                snapshot = node.snapshot()
                if args.format == "table":
                    if args.once or not args.loop:
                        print(lineproto.to_text_table(snapshot))
                    else:
                        # Redraw in place so the table looks live.
                        print("\033[H\033[J", end="")
                        print("microsense node {!r}  uptime {} s".format(
                            snapshot["node"], snapshot["uptime_s"]))
                        print()
                        print(lineproto.to_text_table(snapshot))
                        if server is not None:
                            print("\nModbus TCP on {}:{}  requests: {}".format(
                                args.bind, args.modbus_port, server.requests_served))
                        print("\nCtrl+C to stop")
                        printed_header = True
                else:
                    print(lineproto.render(snapshot, args.format), flush=True)

            if args.once:
                break
            if args.seconds is not None:
                if clock.ticks_diff(clock.ticks_ms(), start_ms) >= args.seconds * 1000:
                    break

            interval_left = args.interval_ms - clock.ticks_diff(clock.ticks_ms(), last_print)
            if server is not None:
                # Keep answering Modbus while waiting for the next print.
                deadline = clock.ticks_ms() + max(interval_left, 0)
                while clock.ticks_diff(clock.ticks_ms(), deadline) < 0:
                    server.serve_one(0.05)
            else:
                clock.sleep_ms(max(interval_left, 1))
    except KeyboardInterrupt:
        pass
    finally:
        if server is not None:
            server.stop()
    if printed_header:
        print()
    return 0


def main(argv=None):
    # Imported here, not at module scope: MicroPython has no argparse, and the
    # board imports build_node from this module.
    import argparse

    parser = argparse.ArgumentParser(
        prog="microsense",
        description="Multi-function sensor node for microcontrollers.",
    )
    subparsers = parser.add_subparsers(dest="command")

    dump = subparsers.add_parser("dump", help="take one reading of every point and exit")
    dump.add_argument("--format", default="json", choices=("json", "csv", "table"))
    dump.add_argument("--hardware", action="store_true", help="talk to real I2C/ADC")
    dump.set_defaults(handler=_command_dump)

    run = subparsers.add_parser("run", help="run the node and print readings")
    run.add_argument("--format", default="table", choices=("json", "csv", "table"))
    run.add_argument("--hardware", action="store_true", help="talk to real I2C/ADC")
    run.add_argument("--interval-ms", type=int, default=1000)
    run.add_argument("--seconds", type=int, default=None, help="stop after this long")
    run.add_argument("--once", action="store_true", help="print one table and exit")
    run.add_argument("--loop", action="store_true", help="redraw the table in place")
    run.add_argument("--modbus-port", type=int, default=0,
                     help="also serve Modbus TCP on this port (0 disables)")
    run.add_argument("--bind", default="127.0.0.1")
    run.set_defaults(handler=_command_run)

    simulate = subparsers.add_parser("simulate", help="alias for run with the fakes")
    simulate.add_argument("--format", default="table", choices=("json", "csv", "table"))
    simulate.add_argument("--interval-ms", type=int, default=1000)
    simulate.add_argument("--seconds", type=int, default=None)
    simulate.add_argument("--once", action="store_true")
    simulate.add_argument("--loop", action="store_true")
    simulate.add_argument("--modbus-port", type=int, default=0)
    simulate.add_argument("--bind", default="127.0.0.1")
    simulate.set_defaults(handler=_command_run, hardware=False)

    args = parser.parse_args(argv)
    if not getattr(args, "handler", None):
        parser.print_help()
        return 1
    if not hasattr(args, "hardware"):
        args.hardware = False
    return args.handler(args)
