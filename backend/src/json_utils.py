"""Tolerant JSON extraction for local-model output.

Local VLMs often wrap JSON in prose or ```json fences. These helpers pull the
first JSON array/object out of a noisy string.
"""

from __future__ import annotations

import json
from typing import Any


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        # drop the opening fence line (``` or ```json) and the closing fence
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


def _extract_span(text: str, open_ch: str, close_ch: str) -> str | None:
    """Return the first balanced ``open_ch..close_ch`` span, respecting strings."""
    start = text.find(open_ch)
    if start == -1:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def extract_json(text: str) -> Any:
    """Parse JSON from a possibly-noisy string.

    Tries, in order: the whole (de-fenced) string, the first balanced array,
    then the first balanced object. Raises ValueError if nothing parses.
    """
    cleaned = _strip_fences(text)

    for candidate in (
        cleaned,
        _extract_span(cleaned, "[", "]"),
        _extract_span(cleaned, "{", "}"),
    ):
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue

    raise ValueError(f"No parseable JSON found in model output: {text[:200]!r}")


def extract_json_list(text: str) -> list[Any]:
    """Like ``extract_json`` but always returns a list.

    A bare object is wrapped; an object with a single list value is unwrapped.
    """
    data = extract_json(text)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        # common shapes: {"items": [...]} / {"line_items": [...]}
        for value in data.values():
            if isinstance(value, list):
                return value
        return [data]
    return [data]
