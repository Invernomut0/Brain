"""Robust JSON extraction from noisy local-LLM output."""
from __future__ import annotations

import json
import re
from typing import Any

_THINK = re.compile(r"<think>.*?</think>", re.S)
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def strip_thinking(text: str) -> str:
    text = _THINK.sub("", text)
    # Unterminated think block (generation cut off): drop everything after the opening tag.
    if "<think>" in text:
        text = text.split("<think>", 1)[0]
    return text.strip()


def _balanced(text: str) -> list[str]:
    """Return every top-level balanced {...} substring, string-aware."""
    out, depth, start, in_str, esc = [], 0, -1, False, False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0:
                out.append(text[start : i + 1])
    return out


def _loads(s: str) -> Any:
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        s2 = re.sub(r",\s*([}\]])", r"\1", s)  # trailing commas
        return json.loads(s2)


def extract_json(text: str) -> dict:
    """Parse the most plausible JSON object in `text`; raises ValueError if none."""
    text = strip_thinking(text)
    candidates = [m.strip() for m in _FENCE.findall(text)] + [text]
    for cand in candidates:
        for chunk in [cand] + _balanced(cand):
            try:
                val = _loads(chunk)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(val, dict):
                return val
    raise ValueError("no JSON object found in model output")
