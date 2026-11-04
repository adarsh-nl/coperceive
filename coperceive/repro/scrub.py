# SPDX-License-Identifier: Apache-2.0
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Remove secrets, hostnames, usernames and absolute paths from a card.

Export builds the card from known fields and then runs this pass so a value
that arrived inside a pipeline report or a perturbation object cannot leak.
A fact that has to be dropped becomes ``null`` and its dotted path is added
to ``unknown``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import cast

_SECRET_KEY = re.compile(
    r"(token|password|passwd|secret|api[_-]?key|private[_-]?key|credential|"
    r"authorization|cookie|keyring)",
    re.IGNORECASE,
)
_TOKEN_VALUE = re.compile(
    r"(?i)(bearer\s+\S{8,}|ghp_[a-z0-9]{20,}|github_pat_[a-z0-9_]{20,}|"
    r"sk-[a-z0-9]{16,}|akia[0-9a-z]{16}|xox[baprs]-[a-z0-9-]{10,}|"
    r"hf_[a-z0-9]{20,}|-----begin [a-z ]*private key-----)"
)
_HOSTNAME = re.compile(
    r"(?i)(?<![a-z0-9])(?:[a-z0-9-]+\.)+(?:edu|com|org|net|io|gov|nl|uk|de|local)\b"
)
_USER_AT = re.compile(r"\b[A-Za-z0-9._-]{1,32}@[A-Za-z0-9.-]+\b")
_HOME_PATH = re.compile(r"(?:^|[\s\"'])(?:/(?:home|Users)/[A-Za-z0-9._-]+)(?:/|\b)")
_ABS_PATH = re.compile(r"(?:^|[\s\"'])(?:~|/|/[A-Za-z0-9._-]+)(?:/[^\s\"']+)+")

# sha256 and git commits are hex, not hostnames. Package versions like 1.26.4
# have no letter suffix, so the hostname expression does not match them.


def scrub_card(card: dict[str, object]) -> dict[str, object]:
    """Return a card with secret-shaped data removed.

    ``unknown`` lists every dotted path whose value was cleared.
    """
    unknown = _string_list(card.get("unknown"))
    cleaned, dropped = _scrub(card, "")
    if not isinstance(cleaned, dict):
        cleaned = {}
    for path in dropped:
        if path and path not in unknown and not path.startswith("unknown"):
            unknown.append(path)
    cleaned["unknown"] = sorted(set(unknown))
    return cleaned


def leaks(document: object) -> list[str]:
    """Human-readable leaks still present in a JSON value. Empty means clean."""
    found: list[str] = []
    _scan(document, "$", found)
    return found


def _scan(value: object, path: str, found: list[str]) -> None:
    if isinstance(value, str):
        if _TOKEN_VALUE.search(value):
            found.append(f"{path} looks like a credential")
        if _HOSTNAME.search(value) or _USER_AT.search(value):
            found.append(f"{path} contains a hostname or an account name")
        if _looks_absolute(value) or _HOME_PATH.search(value):
            found.append(f"{path} contains a filesystem path")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            name = str(key)
            if _SECRET_KEY.search(name):
                found.append(f"{path}.{name} is a credential field")
            _scan(item, f"{path}.{name}", found)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _scan(item, f"{path}[{index}]", found)


def _scrub(value: object, path: str) -> tuple[object, list[str]]:
    if isinstance(value, str):
        return _scrub_string(value, path)
    if isinstance(value, list):
        items: list[object] = []
        dropped: list[str] = []
        for index, item in enumerate(value):
            cleaned, extra = _scrub(item, f"{path}[{index}]")
            items.append(cleaned)
            dropped.extend(extra)
        return items, dropped
    if isinstance(value, dict):
        return _scrub_object(value, path)
    if type(value) in (int, float, bool) or value is None:
        return value, []
    return None, [path] if path else []


def _scrub_object(value: dict[object, object], path: str) -> tuple[dict[str, object], list[str]]:
    cleaned: dict[str, object] = {}
    dropped: list[str] = []
    for key, item in value.items():
        if not isinstance(key, str):
            continue
        child = f"{path}.{key}" if path else key
        if _SECRET_KEY.search(key):
            dropped.append(child)
            continue
        new_item, extra = _scrub(item, child)
        cleaned[key] = new_item
        dropped.extend(extra)
    return cleaned, dropped


def _scrub_string(value: str, path: str) -> tuple[object, list[str]]:
    if _TOKEN_VALUE.search(value) or _USER_AT.search(value):
        return None, [path]
    if _looks_absolute(value):
        return None, [path]
    redacted = _HOSTNAME.sub("[redacted]", value)
    redacted = _HOME_PATH.sub(" ", redacted)
    if redacted != value:
        if not redacted.strip() or redacted.strip() == "[redacted]":
            return None, [path]
        return redacted, [path]
    return value, []


def _looks_absolute(value: str) -> bool:
    text = value.strip()
    if text.startswith("~") or text.startswith("/"):
        return True
    if len(text) > 2 and text[1] == ":" and text[0].isalpha() and text[2] in "\\/":
        return True
    return False


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in cast(list[object], value) if isinstance(item, str)]


def relativise(path: str, roots: Mapping[str, tuple[str, ...]]) -> dict[str, str] | None:
    """``{role, relative}`` when ``path`` sits under one profile root.

    The longest matching root wins. ``~`` is not expanded. A path with a
    ``..`` segment, or one that matches no root, returns ``None``.
    """
    raw = path.replace("\\", "/").strip()
    if not raw or raw.startswith("~") or "\x00" in raw:
        return None
    if any(part == ".." for part in raw.split("/")):
        return None
    if not raw.startswith("/"):
        return None
    best: tuple[int, str, str] | None = None
    for role, candidates in roots.items():
        for root in candidates:
            normalised = root.replace("\\", "/").rstrip("/") or "/"
            if raw == normalised or raw.startswith(normalised + "/"):
                relative = raw[len(normalised) :].lstrip("/")
                if not relative or any(part == ".." for part in relative.split("/")):
                    continue
                if best is None or len(normalised) > best[0]:
                    best = (len(normalised), role, relative)
    if best is None:
        return None
    return {"role": best[1], "relative": best[2]}


def file_name(path: str) -> str | None:
    """The last segment of a path, or ``None`` when it is not a file name."""
    name = path.replace("\\", "/").rstrip("/").split("/")[-1]
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        return None
    if _SECRET_KEY.search(name) or _TOKEN_VALUE.search(name):
        return None
    return name
