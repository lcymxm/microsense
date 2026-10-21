"""Board entry point.

Copy the ``microsense`` package and ``config.py`` to the board, then either
``import main`` from the REPL or save this file as ``main.py`` so it runs at
boot.

The loop is deliberately boring: poll the sensors on schedule, answer Modbus
between polls. There is no threading because MicroPython does not have any, and
a sensor node does not need it -- the whole point is that the loop is short
enough to be predictable.
"""

import config
from microsense import clock, lineproto
from microsense.cli import build_node
from microsense.modbus import ModbusTcpServer


def main():
    node, _ = build_node(use_hardware=True, node_id=config.NODE_ID)

    server = None
    if config.MODBUS_PORT:
        server = ModbusTcpServer(node.register_block, host=config.BIND,
                                 port=config.MODBUS_PORT, unit_id=config.UNIT_ID)
        server.start()
        print("Modbus TCP on {}:{}".format(config.BIND, config.MODBUS_PORT))

    next_poll = clock.ticks_ms()
    while True:
        now = clock.ticks_ms()
        if clock.ticks_diff(now, next_poll) >= 0:
            node.poll(now)
            if config.PRINT_JSON:
                print(lineproto.to_json_line(node.snapshot()))
            next_poll = now + config.POLL_MS

        if server is not None:
            server.serve_one(0.05)
        else:
            clock.sleep_ms(20)


if __name__ == "__main__":
    main()
