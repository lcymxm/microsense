"""Modbus: frame codec, register image and a read-only TCP server.

Modbus is the reason a sensor node can be dropped into an existing plant: every
supervisory system already speaks it. This module keeps the codec separate from
the socket so the byte layout can be tested without opening a port, exactly the
way the C++ side of this family does it.

Only functions 03 (read holding) and 04 (read input) are served. That is
deliberate: a sensor node publishes data, it does not expose its actuators over
a network protocol with no authentication.
"""

import struct

MAX_READ_REGISTERS = 125

FUNCTION_READ_HOLDING = 0x03
FUNCTION_READ_INPUT = 0x04
FUNCTION_WRITE_SINGLE = 0x06

EXCEPTION_ILLEGAL_FUNCTION = 0x01
EXCEPTION_ILLEGAL_DATA_ADDRESS = 0x02
EXCEPTION_ILLEGAL_DATA_VALUE = 0x03
EXCEPTION_SERVER_FAILURE = 0x04

MBAP_HEADER_SIZE = 7


def crc16(data):
    """CRC-16/MODBUS: reflected 0xA001, seed 0xFFFF, no final XOR."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def wrap_rtu(unit, pdu):
    frame = bytes([unit]) + bytes(pdu)
    checksum = crc16(frame)
    return frame + bytes([checksum & 0xFF, (checksum >> 8) & 0xFF])


def unwrap_rtu(frame):
    """Returns (unit, pdu) or raises ValueError on a bad CRC."""
    if len(frame) < 4:
        raise ValueError("RTU frame too short")
    expected = frame[-2] | (frame[-1] << 8)
    if crc16(frame[:-2]) != expected:
        raise ValueError("RTU CRC mismatch")
    return frame[0], bytes(frame[1:-2])


def wrap_mbap(transaction, unit, pdu):
    length = len(pdu) + 1
    return struct.pack(">HHHB", transaction & 0xFFFF, 0, length, unit) + bytes(pdu)


def unwrap_mbap(frame):
    """Returns (transaction, unit, pdu); raises ValueError if malformed."""
    if len(frame) < MBAP_HEADER_SIZE:
        raise ValueError("MBAP frame too short")
    transaction, protocol, length, unit = struct.unpack(">HHHB", frame[:MBAP_HEADER_SIZE])
    if protocol != 0:
        raise ValueError("MBAP protocol id must be zero")
    if len(frame) != 6 + length:
        raise ValueError("MBAP length field does not match the frame")
    return transaction, unit, bytes(frame[MBAP_HEADER_SIZE:])


def mbap_frame_length(buffer):
    """Total frame length announced by the header, or 0 if more bytes are needed."""
    if len(buffer) < 6:
        return 0
    protocol, length = struct.unpack(">HH", bytes(buffer[2:6]))
    if protocol != 0:
        raise ValueError("MBAP protocol id must be zero")
    return 6 + length


def exception_response(function, code):
    return bytes([(function | 0x80) & 0xFF, code])


def process_pdu(registers, pdu):
    """Turns one request PDU into one response PDU.

    `registers` is the current register image (a list of ints). Returns b"" when
    the request is not a Modbus request at all, which the server treats as a
    reason to drop the connection.
    """
    if not pdu:
        return b""
    function = pdu[0]

    if function in (FUNCTION_READ_HOLDING, FUNCTION_READ_INPUT):
        if len(pdu) != 5:
            return exception_response(function, EXCEPTION_ILLEGAL_DATA_VALUE)
        address = (pdu[1] << 8) | pdu[2]
        count = (pdu[3] << 8) | pdu[4]
        if count < 1 or count > MAX_READ_REGISTERS:
            return exception_response(function, EXCEPTION_ILLEGAL_DATA_VALUE)
        if address + count > len(registers):
            return exception_response(function, EXCEPTION_ILLEGAL_DATA_ADDRESS)
        payload = bytearray([function, count * 2])
        for index in range(address, address + count):
            value = registers[index] & 0xFFFF
            payload.append((value >> 8) & 0xFF)
            payload.append(value & 0xFF)
        return bytes(payload)

    return exception_response(function, EXCEPTION_ILLEGAL_FUNCTION)


class ModbusTcpServer:
    """Single client, single thread.

    MicroPython has no threads, and a sensor node has no business serving a
    crowd: one supervisory connection is the normal case.
    """

    def __init__(self, register_source, host="0.0.0.0", port=1502, unit_id=1):
        self._register_source = register_source
        self.host = host
        self.port = int(port)
        self.unit_id = unit_id
        self.requests_served = 0
        self.last_error = ""
        self._socket = None
        self._client = None

    def start(self):
        import socket

        self._socket = socket.socket()
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind((self.host, self.port))
        # Port 0 means "any free port"; record what the OS chose so a test can
        # connect to it.
        self.port = self._socket.getsockname()[1]
        self._socket.listen(1)
        return self

    def stop(self):
        for sock in (self._client, self._socket):
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        self._client = None
        self._socket = None

    def serve_one(self, timeout=1.0):
        """Accepts a client if needed and serves one request.

        Returns True when a request was answered, False on timeout. Embedding the
        loop this way lets the caller keep sampling sensors between requests.
        """
        if self._client is None:
            self._socket.settimeout(timeout)
            try:
                self._client, _ = self._socket.accept()
                self._client.settimeout(timeout)
            except OSError:
                return False

        try:
            header = self._recv_exactly(6)
            if header is None:
                return False
            protocol, length = struct.unpack(">HH", header[2:6])
            if protocol != 0 or length < 2 or length > 260:
                self._drop_client("malformed MBAP header")
                return False
            rest = self._recv_exactly(length)
            if rest is None:
                return False
            frame = header + rest
        except OSError as error:
            self._drop_client(str(error))
            return False

        transaction = struct.unpack(">H", frame[0:2])[0]
        pdu = frame[7:]  # header is 7 bytes: transaction, protocol, length, unit
        registers = self._register_source()
        response = process_pdu(registers, pdu)
        if not response:
            self._drop_client("unparseable request")
            return False

        out = wrap_mbap(transaction, self.unit_id, response)
        try:
            self._client.sendall(out)
        except OSError as error:
            self._drop_client(str(error))
            return False
        self.requests_served += 1
        return True

    def serve_forever(self, on_idle=None, timeout=0.2):
        self.start()
        try:
            while True:
                if not self.serve_one(timeout):
                    if on_idle is not None:
                        on_idle()
        finally:
            self.stop()

    def _recv_exactly(self, count):
        chunks = bytearray()
        while len(chunks) < count:
            try:
                chunk = self._client.recv(count - len(chunks))
            except OSError:
                return None
            if not chunk:
                self._drop_client("peer closed")
                return None
            chunks += chunk
        return bytes(chunks)

    def _drop_client(self, reason):
        self.last_error = reason
        if self._client is not None:
            try:
                self._client.close()
            except OSError:
                pass
        self._client = None
