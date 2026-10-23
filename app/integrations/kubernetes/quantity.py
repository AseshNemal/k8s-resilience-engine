"""Kubernetes resource quantities ("250m", "2", "512Mi", "1Gi") as plain numbers.

Only what a capacity check needs: CPU in cores and memory in bytes. An unreadable quantity is ``None``, never zero: a
capacity figure that could not be read must not look like an empty node.
"""

import re

_BINARY = {"Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4, "Pi": 1024**5, "Ei": 1024**6}
_DECIMAL = {"n": 1e-9, "u": 1e-6, "m": 1e-3, "": 1.0, "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12, "P": 1e15, "E": 1e18}
_PATTERN = re.compile(r"^([+-]?[0-9]*\.?[0-9]+(?:[eE][+-]?[0-9]+)?)([a-zA-Z]*)$")


def parse_quantity(value: object) -> float | None:
    """The numeric value of a quantity (cores for CPU, bytes for memory), or None when it cannot be read."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = _PATTERN.match(str(value).strip())
    if not match:
        return None
    number, suffix = float(match.group(1)), match.group(2)
    if suffix in _BINARY:
        return number * _BINARY[suffix]
    if suffix in _DECIMAL:
        return number * _DECIMAL[suffix]
    return None
