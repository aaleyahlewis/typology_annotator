"""Decode React Router "turbo-stream" payloads embedded in ChatGPT share pages.

The payload is a flat JSON array. Values refer to other entries by index:
objects are {"_<key index>": <value index>}, arrays are lists of indexes, and
negative numbers are constants. Deferred values arrive as extra lines
"P<id>:<json>". Unknown shapes decode to None rather than raising, because
this only feeds a search for conversation data that is validated afterwards.
"""
import json

HOLE, NAN, NEG_INF, NEG_ZERO, NULL, POS_INF, UNDEFINED = -1, -2, -3, -4, -5, -6, -7
CONSTANTS = {HOLE: None, NAN: None, NEG_INF: None, NEG_ZERO: 0, NULL: None, POS_INF: None, UNDEFINED: None}
MAX_DEPTH = 200


def decode(values):
    if not isinstance(values, list) or not values:
        return None
    cache = {}

    def hydrate(index, depth=0):
        if isinstance(index, int) and index < 0:
            return CONSTANTS.get(index)
        if not isinstance(index, int) or index >= len(values) or depth > MAX_DEPTH:
            return None
        if index in cache:
            return cache[index]
        raw = values[index]
        if isinstance(raw, dict):
            out = {}
            cache[index] = out
            for k, v in raw.items():
                if k.startswith("_") and k[1:].isdigit():
                    key = hydrate(int(k[1:]), depth + 1)
                    if isinstance(key, str):
                        out[key] = hydrate(v, depth + 1)
            return out
        if isinstance(raw, list):
            if raw and isinstance(raw[0], str):
                # Typed value such as ["D", 1700000000000] (Date) or ["P", 12] (promise).
                cache[index] = raw[1] if raw[0] in ("D", "B", "U", "R", "Y") and len(raw) > 1 else None
                return cache[index]
            out = []
            cache[index] = out
            out.extend(hydrate(i, depth + 1) for i in raw)
            return out
        cache[index] = raw
        return raw

    return hydrate(0)


def decode_stream(text):
    """Decode every line of a concatenated stream; returns a list of decoded roots."""
    roots = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("P") and ":" in line[:12]:
            line = line.split(":", 1)[1]
        try:
            roots.append(decode(json.loads(line)))
        except (ValueError, RecursionError):
            continue
    return roots
