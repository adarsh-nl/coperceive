# SPDX-License-Identifier: Apache-2.0
"""Griffin licence check and the once-per-machine acknowledgement.

The acknowledgement lives under ``$XDG_DATA_HOME/coperceive`` (or
``~/.local/share/coperceive``). It records that the dataset card said MIT and
that the toolkit ``LICENSE.txt`` was the MIT text. It does not store a token.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

CARD_LICENSE = "mit"
TOOLKIT_HEADING = "MIT License"
TOOLKIT_COPYRIGHT = "Copyright (c) [2025] [Jiahao Wang]"
ACK_NAME = "griffin-licence-accepted"


class LicenceError(RuntimeError):
    """The dataset card or the toolkit licence was not MIT."""


def ack_path() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "coperceive" / ACK_NAME


def card_license(readme: str) -> str | None:
    """Return the YAML ``license`` value from a dataset card, lowercased."""
    if not readme.startswith("---"):
        return None
    end = readme.find("\n---", 3)
    if end < 0:
        return None
    for line in readme[3:end].splitlines():
        if line.startswith("license:"):
            value = line.split(":", 1)[1].strip().strip("\"'")
            return value.lower()
    return None


def assert_mit(readme: str, toolkit_license: str) -> str:
    """Refuse unless the card and the toolkit licence are MIT.

    Returns a short notice safe to print. It contains no token.
    """
    found = card_license(readme)
    if found != CARD_LICENSE:
        raise LicenceError(
            f"Hugging Face dataset card license is {found!r}, expected {CARD_LICENSE!r}"
        )
    if TOOLKIT_HEADING not in toolkit_license or TOOLKIT_COPYRIGHT not in toolkit_license:
        raise LicenceError("toolkit LICENSE.txt is not the expected MIT text")
    return (
        "Griffin dataset wjh-svm/Griffin card license: mit\n"
        "Griffin toolkit github.com/wang-jh18-SVM/Griffin LICENSE.txt: MIT\n"
        f"{TOOLKIT_COPYRIGHT}"
    )


def acknowledgement_present(path: Path | None = None) -> bool:
    target = ack_path() if path is None else path
    if not target.is_file():
        return False
    text = target.read_text(encoding="utf-8")
    return bool(text.strip()) and "HF_TOKEN" not in text


def write_acknowledgement(
    notice: str,
    path: Path | None = None,
    *,
    when: str | None = None,
) -> Path:
    target = ack_path() if path is None else path
    target.parent.mkdir(parents=True, exist_ok=True)
    stamp = when if when is not None else datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    text = (
        f"{notice}\n"
        "Acknowledged once for this machine. Dataset bytes stay outside the git checkout.\n"
        f"acknowledged_utc={stamp}\n"
    )
    if "HF_TOKEN" in text or "hf_" in text:
        raise LicenceError("refusing to write an acknowledgement that contains a token")
    target.write_text(text, encoding="utf-8")
    return target
