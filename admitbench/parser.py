"""Model text → action record.

Tolerant of fences and surrounding prose, strict about substance: if no valid
record can be recovered, the episode is not evaluable — that is a T0 verdict,
not a parsing inconvenience to paper over.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from admitbench.record import ActionRecord

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _balanced_object(text: str) -> Optional[str]:
    """First balanced {...} block, string-aware."""
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
        start = text.find("{", start + 1)
    return None


def _try_load(candidate: str) -> Optional[dict]:
    for attempt in (candidate, re.sub(r",\s*([}\]])", r"\1", candidate)):
        try:
            data = json.loads(attempt)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue
    return None


def parse_action_record(text: str) -> tuple[Optional[ActionRecord], Optional[str]]:
    """Returns (record, error). Exactly one of the two is None."""
    if not text or not text.strip():
        return None, "empty model output"

    candidates = [m.strip() for m in _FENCE.findall(text)]
    block = _balanced_object(text)
    if block:
        candidates.append(block)

    data = None
    for candidate in candidates:
        data = _try_load(candidate)
        if data is not None:
            break
    if data is None:
        return None, "no JSON object found in model output"

    if not str(data.get("action", "")).strip():
        return None, "record has no 'action' field"
    try:
        record = ActionRecord.from_dict(data, raw=text)
    except (TypeError, ValueError) as exc:
        return None, f"record fields malformed: {exc}"
    return record, None
