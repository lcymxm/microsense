import socket
import struct

import pytest

from microsense.modbus import (
    EXCEPTION_ILLEGAL_DATA_ADDRESS,
    EXCEPTION_ILLEGAL_DATA_VALUE,
    EXCEPTION_ILLEGAL_FUNCTION,
    ModbusTcpServer,
    crc16,
    mbap_frame_length,
    process_pdu,
    unwrap_mbap,
    unwrap_rtu,
    wrap_mbap,
    wrap_rtu,
)

REGISTERS = [100, 200, 300, 400]


# -- CRC -------------------------------------------------------------------


def test_crc16_matches_the_standard_check_value():
    assert crc16(b"123456789") == 0x4B37


def test_crc16_of_a_complete_frame_is_zero():
    frame = wrap_rtu(1, bytes([3, 0, 0, 0, 1]))
    assert crc16(frame) == 0


# -- RTU -------------------------------------------------------------------


def test_rtu_round_trip():
    pdu = bytes([3, 0, 10, 0, 2])
    frame = wrap_rtu(7, pdu)
    unit, restored = unwrap_rtu(frame)
    assert unit == 7
    assert restored == pdu


def test_rtu_rejects_a_flipped_bit():
    frame = bytearray(wrap_rtu(1, bytes([3, 0, 0, 0, 1])))
    frame[2] ^= 0x01
    with pytest.raises(ValueError):
        unwrap_rtu(bytes(frame))


def test_rtu_rejects_a_short_frame():
    with pytest.raises(ValueError):
        unwrap_rtu(b"\x01\x03")


# -- MBAP ------------------------------------------------------------------


def test_mbap_round_trip():
    pdu = bytes([4, 0, 0, 0, 1])
    frame = wrap_mbap(0x1234, 9, pdu)
    transaction, unit, restored = unwrap_mbap(frame)
    assert transaction == 0x1234
    assert unit == 9
    assert restored == pdu


def test_mbap_length_helper_agrees_with_the_frame():
    frame = wrap_mbap(1, 1, bytes([3, 0, 0, 0, 1]))
    assert mbap_frame_length(frame) == len(frame)


def test_mbap_rejects_a_nonzero_protocol_id():
    frame = bytearray(wrap_mbap(1, 1, bytes([3, 0, 0, 0, 1])))
    frame[3] = 0x01
    with pytest.raises(ValueError):
        unwrap_mbap(bytes(frame))


def test_mbap_rejects_a_wrong_length():
    frame = wrap_mbap(1, 1, bytes([3, 0, 0, 0, 1]))
    with pytest.raises(ValueError):
        unwrap_mbap(frame[:-1])


# -- PDU -------------------------------------------------------------------


def test_read_holding_registers():
    response = process_pdu(REGISTERS, bytes([3, 0, 1, 0, 2]))
    assert response == bytes([3, 4, 0, 200, 0x01, 0x2C])


def test_read_input_registers_is_also_served():
    response = process_pdu(REGISTERS, bytes([4, 0, 0, 0, 1]))
    assert response == bytes([4, 2, 0, 100])


def test_read_out_of_range_returns_illegal_address():
    response = process_pdu(REGISTERS, bytes([3, 0, 3, 0, 5]))
    assert response == bytes([0x83, EXCEPTION_ILLEGAL_DATA_ADDRESS])


def test_read_with_a_zero_count_returns_illegal_value():
    assert process_pdu(REGISTERS, bytes([3, 0, 0, 0, 0])) == bytes(
        [0x83, EXCEPTION_ILLEGAL_DATA_VALUE])


def test_read_beyond_the_protocol_limit_is_rejected():
    assert process_pdu(REGISTERS, bytes([3, 0, 0, 0, 126])) == bytes(
        [0x83, EXCEPTION_ILLEGAL_DATA_VALUE])


def test_unknown_function_returns_illegal_function():
    assert process_pdu(REGISTERS, bytes([0x41, 0, 0])) == bytes(
        [(0x41 | 0x80), EXCEPTION_ILLEGAL_FUNCTION])


def test_writing_is_refused():
    # A sensor node publishes; it does not accept writes from the network.
    assert process_pdu(REGISTERS, bytes([6, 0, 0, 0, 1])) == bytes(
        [0x86, EXCEPTION_ILLEGAL_FUNCTION])


def test_empty_pdu_is_rejected():
    assert process_pdu(REGISTERS, b"") == b""


def test_values_are_masked_to_sixteen_bits():
    response = process_pdu([-1], bytes([3, 0, 0, 0, 1]))
    assert response == bytes([3, 2, 0xFF, 0xFF])


# -- TCP server ------------------------------------------------------------


def send_request(sock, transaction, unit, pdu):
    sock.sendall(wrap_mbap(transaction, unit, pdu))
    header = b""
    while len(header) < 6:
        chunk = sock.recv(6 - len(header))
        assert chunk
        header += chunk
    length = struct.unpack(">H", header[4:6])[0]
    body = b""
    while len(body) < length:
        chunk = sock.recv(length - len(body))
        assert chunk
        body += chunk
    return header + body


def test_tcp_server_answers_a_read():
    server = ModbusTcpServer(lambda: REGISTERS, host="127.0.0.1", port=0).start()
    try:
        client = socket.create_connection(("127.0.0.1", server.port), timeout=5)
        client.sendall(wrap_mbap(0x0042, 1, bytes([3, 0, 0, 0, 2])))
        assert server.serve_one(timeout=2.0)

        header = client.recv(6)
        length = struct.unpack(">H", header[4:6])[0]
        body = client.recv(length)
        transaction, unit, pdu = unwrap_mbap(header + body)

        assert transaction == 0x0042
        assert unit == 1
        assert pdu == bytes([3, 4, 0, 100, 0, 200])
        client.close()
    finally:
        server.stop()


def test_tcp_server_serves_registers_that_change_between_requests():
    state = {"value": 10}
    server = ModbusTcpServer(lambda: [state["value"]], host="127.0.0.1", port=0).start()
    try:
        client = socket.create_connection(("127.0.0.1", server.port), timeout=5)
        client.sendall(wrap_mbap(1, 1, bytes([3, 0, 0, 0, 1])))
        assert server.serve_one(timeout=2.0)
        first = client.recv(11)
        assert first[-2:] == bytes([0, 10])

        state["value"] = 11
        client.sendall(wrap_mbap(2, 1, bytes([3, 0, 0, 0, 1])))
        assert server.serve_one(timeout=2.0)
        second = client.recv(11)
        assert second[-2:] == bytes([0, 11])
        client.close()
    finally:
        server.stop()


def test_tcp_server_counts_requests():
    server = ModbusTcpServer(lambda: REGISTERS, host="127.0.0.1", port=0).start()
    try:
        client = socket.create_connection(("127.0.0.1", server.port), timeout=5)
        for _ in range(3):
            client.sendall(wrap_mbap(1, 1, bytes([3, 0, 0, 0, 1])))
            assert server.serve_one(timeout=2.0)
            client.recv(11)
        assert server.requests_served == 3
        client.close()
    finally:
        server.stop()
