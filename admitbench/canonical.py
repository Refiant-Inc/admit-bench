"""Canonical serialisation — one encoding for everything that gets hashed.

Ported from the ADMIT-WASM reference encoder (`harness/canonical.py`). Floats
become fixed-point integers at a declared scale and are rendered by integer
arithmetic, so no language's float formatter is load-bearing.

See ADMIT-WASM SPEC-DELTAS D-13.
"""

from __future__ import annotations

import hashlib
import math

SCALE_DIGITS = 6
SCALE = float(10 ** SCALE_DIGITS)

INT_MIN = -(2 ** 63)
INT_MAX = 2 ** 63 - 1


class NonCanonical(ValueError):
    """Input cannot be canonically encoded."""


def quantize(x: float) -> int:
    if isinstance(x, bool):
        raise NonCanonical("bool is not a number in canonical form")
    x = float(x)
    if not math.isfinite(x):
        raise NonCanonical(f"non-finite value cannot be canonicalised: {x!r}")
    y = x * SCALE
    if not math.isfinite(y):
        raise NonCanonical(f"value overflows the canonical fixed-point range: {x!r}")
    return int(math.floor(y + 0.5)) if y >= 0.0 else int(math.ceil(y - 0.5))


def render_fixed(n: int) -> str:
    sign = "-" if n < 0 else ""
    a = abs(n)
    whole, frac = divmod(a, 10 ** SCALE_DIGITS)
    return f"{sign}{whole}.{frac:0{SCALE_DIGITS}d}"


def encode_float(x: float) -> str:
    return render_fixed(quantize(x))


_SHORT = {'"': '\\"', "\\": "\\\\", "\b": "\\b", "\f": "\\f",
          "\n": "\\n", "\r": "\\r", "\t": "\\t"}


def encode_string(s: str) -> str:
    out = ['"']
    for ch in s:
        short = _SHORT.get(ch)
        if short is not None:
            out.append(short)
        elif ch < "\x20":
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def encode(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        if not (INT_MIN <= value <= INT_MAX):
            raise NonCanonical(
                f"integer {value} is outside the canonical range "
                f"[{INT_MIN}, {INT_MAX}]")
        return str(value)
    if isinstance(value, float):
        return encode_float(value)
    if isinstance(value, str):
        return encode_string(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(encode(v) for v in value) + "]"
    if isinstance(value, dict):
        items = []
        for k in sorted(value.keys()):
            if not isinstance(k, str):
                raise NonCanonical(f"object keys must be strings, got {type(k).__name__}")
            items.append(encode_string(k) + ":" + encode(value[k]))
        return "{" + ",".join(items) + "}"
    raise NonCanonical(f"no canonical form for {type(value).__name__}")


def dumps(value) -> bytes:
    return encode(value).encode("utf-8")


def fingerprint(value) -> str:
    return hashlib.sha256(dumps(value)).hexdigest()
