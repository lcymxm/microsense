"""End to end: the simulated node, its register image and a real Modbus client.

This is the test that would have caught every integration mistake so far -- the
point naming, the register order, the scaling and the server framing all have to
line up for it to pass.
"""

import json
import socket
import struct

import pytest

from microsense import cli, sim
from microsense.modbus import ModbusTcpServer, unwrap_mbap, wrap_mbap


def read_registers(server, address, count):
    client = socket.create_connection(("127.0.0.1", server.port), timeout=5)
    try:
        client.sendall(wrap_mbap(1, 1, bytes([3, address >> 8, address & 0xFF,
                                              count >> 8, count & 0xFF])))
        assert server.serve_one(timeout=2.0)
        header = b""
        while len(header) < 6:
            header += client.recv(6 - len(header))
        length = struct.unpack(">H", header[4:6])[0]
        body = b""
        while len(body) < length:
            body += client.recv(length - len(body))
        _, _, pdu = unwrap_mbap(header + body)
        byte_count = pdu[1]
        values = []
        for index in range(byte_count // 2):
            high = pdu[2 + index * 2]
            low = pdu[3 + index * 2]
            values.append((high << 8) | low)
        return values
    finally:
        client.close()


def test_point_order_matches_the_published_table():
    node, _ = cli.build_node()
    node.poll()
    names = [point.name for point in node.points()]
    assert names == list(cli.POINT_ORDER)


def test_register_block_matches_the_scaled_values():
    node, _ = cli.build_node()
    node.poll()
    registers = node.register_block()
    assert len(registers) == cli.REGISTER_COUNT

    temperature = node.point("sht31.temperature").value
    assert registers[0] == int(round(temperature * 100))


def test_modbus_reports_the_same_temperature_the_node_holds():
    node, _ = cli.build_node()
    node.poll()

    server = ModbusTcpServer(node.register_block, host="127.0.0.1", port=0).start()
    try:
        values = read_registers(server, 0, 3)
        expected = int(round(node.point("sht31.temperature").value * 100))
        assert values[0] == expected

        humidity = int(round(node.point("sht31.humidity").value * 100))
        assert values[1] == humidity
    finally:
        server.stop()


def test_modbus_can_read_the_status_registers():
    node, _ = cli.build_node()
    node.poll()
    server = ModbusTcpServer(node.register_block, host="127.0.0.1", port=0).start()
    try:
        values = read_registers(server, cli.ALARM_FLAGS_REGISTER, 1)
        assert values[0] == node.register_block()[cli.ALARM_FLAGS_REGISTER]
    finally:
        server.stop()


def test_a_silent_sensor_shows_up_as_a_fault_over_modbus():
    node, _ = cli.build_node()

    # Break the I2C bus the temperature sensor lives on.
    def broken():
        raise OSError(19, "ENODEV")

    for sensor in node._sensors:
        if sensor.name == "sht31":
            original = sensor.reader
            sensor.reader = broken
    node.poll()

    temperature = node.point("sht31.temperature")
    assert temperature.quality == "fault"
    # The register still carries the last value rather than a fake zero.
    assert node.register_block()[0] == 0


def test_cli_dump_prints_json(capsys):
    assert cli.main(["dump", "--format", "json"]) == 0
    captured = capsys.readouterr().out
    payload = json.loads(captured.strip())
    assert payload["node"]
    assert len(payload["points"]) == len(cli.POINT_ORDER)


def test_cli_run_once_prints_a_table(capsys):
    assert cli.main(["run", "--once", "--format", "table"]) == 0
    captured = capsys.readouterr().out
    assert "sht31.temperature" in captured
    assert "quality" in captured


def test_cli_run_stops_after_its_budget(capsys):
    assert cli.main(["run", "--format", "json", "--seconds", "1", "--interval-ms", "200"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) >= 2
    for line in lines:
        json.loads(line)


def test_cli_help_when_no_command(capsys):
    assert cli.main([]) == 1
    assert "usage" in capsys.readouterr().out.lower()


def test_simulated_bus_exposes_all_three_devices():
    i2c, adc_voltage, adc_current, plant = sim.build_simulated_bus()
    assert sorted(i2c.scan()) == [0x23, 0x44, 0x68]
    assert 0.0 <= adc_voltage.read_ratio() <= 1.0
    assert 0.0 <= adc_current.read_ratio() <= 1.0
    assert plant is not None
