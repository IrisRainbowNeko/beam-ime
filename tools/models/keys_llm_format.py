#!/usr/bin/env python3
"""Shared prompt contract for the Beam v8 keys-conditioned LLM experiment.

Every raw key becomes its own space-separated token so the model can align
letters with characters, and a user-typed separator becomes ``'``. The target
optionally spells the key segmentation before the Hanzi result, following the
GeneInput pyseg intermediate.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

PROMPT_VERSION = "keys_llm_v1"
KEY_PATTERN = re.compile(r"[a-z' ]+")
HAN_PATTERN = re.compile(r"[一-鿿]+")
SEGMENT_PREFIX = "切分："
RESULT_PREFIX = "结果："


def normalize_keys(raw: str) -> str:
    """Lowercase keys and collapse user separators into single apostrophes."""
    keys = raw.strip().lower()
    if not keys or not KEY_PATTERN.fullmatch(keys):
        raise ValueError(f"invalid keys: {raw!r}")
    keys = re.sub(r"[' ]+", "'", keys).strip("'")
    if not keys:
        raise ValueError(f"keys contain only separators: {raw!r}")
    return keys


def letters(keys: str) -> str:
    return keys.replace("'", "")


def spaced_keys(keys: str) -> str:
    return " ".join(normalize_keys(keys))


def build_prompt(keys: str, context: str = "") -> str:
    prefix = f"上文：{context}\n" if context else ""
    return f"{prefix}按键：{spaced_keys(keys)}\n"


def build_target(target: str, segments: list[str] | None) -> str:
    if segments is None:
        return f"{RESULT_PREFIX}{target}"
    return f"{SEGMENT_PREFIX}{' '.join(segments)}\n{RESULT_PREFIX}{target}"


def split_pyseg(pyseg: str) -> list[str]:
    """Split GeneInput CamelCase pyseg (``ZhebMBiY``) into lowercase segments."""
    if not re.fullmatch(r"(?:[A-Z][a-z]*)+", pyseg):
        raise ValueError(f"unsupported pyseg: {pyseg!r}")
    return [value.lower() for value in re.findall(r"[A-Z][a-z]*", pyseg)]


def parse_output(text: str) -> dict[str, Any]:
    """Parse one generated continuation of ``build_prompt``."""
    segments: list[str] | None = None
    if text.startswith(SEGMENT_PREFIX):
        line, _, text = text[len(SEGMENT_PREFIX):].partition("\n")
        segments = line.split()
    if not text.startswith(RESULT_PREFIX):
        return {"segments": segments, "result": None}
    result = text[len(RESULT_PREFIX):].split("\n", 1)[0].strip()
    return {"segments": segments, "result": result or None}


@lru_cache(maxsize=65536)
def readings(character: str) -> frozenset[str]:
    from pypinyin import Style, pinyin

    values = pinyin(character, style=Style.NORMAL, heteronym=True, errors="ignore")
    return frozenset(value.replace("ü", "v") for group in values for value in group)


UNIT_PATTERN = re.compile(r"[一-鿿]|[A-Za-z]+| ")
ENGLISH_PATTERN = re.compile(r"[A-Za-z]+")


def units(result: str) -> list[str] | None:
    """Split a result into Hanzi characters and English words (spaces only between English words)."""
    values = UNIT_PATTERN.findall(result)
    if not values or "".join(values) != result:
        return None
    for index, value in enumerate(values):
        if value == " " and not (0 < index < len(values) - 1 and values[index - 1].isascii() and values[index + 1].isascii()
                                 and values[index - 1] != " " and values[index + 1] != " "):
            return None
    return [value for value in values if value != " "]


def _aligns(keys: str, pieces: list[str]) -> bool:
    """Each Hanzi consumes >= 1 key letter, each English word exactly its lowercase letters, covering all keys."""
    reachable = {0}
    for piece in pieces:
        step = set()
        for position in reachable:
            if ENGLISH_PATTERN.fullmatch(piece):
                if keys.startswith(piece.lower(), position):
                    step.add(position + len(piece))
            else:
                step.update(range(position + 1, len(keys) + 1))
        reachable = step
        if not reachable:
            return False
    return len(keys) in reachable


def structural_valid(keys: str, parsed: dict[str, Any]) -> bool:
    """Check the result is Hanzi/English units that the keys (or the given segments) can produce."""
    result = parsed["result"]
    pieces = units(result) if result else None
    if not pieces:
        return False
    segments = parsed["segments"]
    if segments is None:
        return _aligns(letters(keys), pieces)
    if "".join(segments) != letters(keys) or len(segments) != len(pieces):
        return False
    return all(segment == piece.lower() for segment, piece in zip(segments, pieces) if ENGLISH_PATTERN.fullmatch(piece))


def strict_valid(keys: str, parsed: dict[str, Any]) -> bool:
    """Additionally require each Hanzi segment to prefix a reading of its character."""
    if not structural_valid(keys, parsed) or parsed["segments"] is None:
        return False
    return all(
        ENGLISH_PATTERN.fullmatch(piece) or any(reading.startswith(segment) for reading in readings(piece))
        for segment, piece in zip(parsed["segments"], units(parsed["result"]))
    )
