"""Line protocols for a serial link.

A sensor node usually talks to something that is not a computer: a logger, an
HMI or a serial-to-Ethernet bridge. Two formats cover almost every one of those:

    json   one object per line, easy to parse and debug
    csv    one header line then one line per sample, trivial for a logger

Both are newline terminated so the receiver can split on '\\n' without a state
machine.
"""

import json


def to_json_line(snapshot):
    return json.dumps(snapshot, separators=(",", ":"))


def csv_header(snapshot):
    names = [point["name"] for point in snapshot["points"]]
    return "uptime_s," + ",".join(names)


def to_csv_line(snapshot):
    values = []
    for point in snapshot["points"]:
        if point["value"] is None:
            values.append("")
        else:
            values.append("{:.3f}".format(point["value"]))
    return "{},{}".format(snapshot["uptime_s"], ",".join(values))


def to_text_table(snapshot, marks=None):
    """Fixed width table for a terminal or the REPL."""
    lines = []
    header = "{:<22}{:>12}  {:<7}{:<8}{}".format("point", "value", "unit", "quality", "alarm")
    lines.append(header)
    lines.append("-" * len(header))
    for point in snapshot["points"]:
        value = point["value"]
        rendered = "--" if value is None else "{:.3f}".format(value)
        alarm = point["alarm"]
        if alarm != "normal" and not point["acknowledged"]:
            alarm += " (unacked)"
        lines.append(
            "{:<22}{:>12}  {:<7}{:<8}{}".format(
                point["name"], rendered, point["unit"], point["quality"], alarm
            )
        )
    return "\n".join(lines)


def render(snapshot, fmt="table"):
    if fmt == "json":
        return to_json_line(snapshot)
    if fmt == "csv":
        return to_csv_line(snapshot)
    if fmt == "table":
        return to_text_table(snapshot)
    raise ValueError("unknown format {!r}".format(fmt))
