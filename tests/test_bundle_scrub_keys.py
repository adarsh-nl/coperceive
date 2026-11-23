# SPDX-License-Identifier: Apache-2.0
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportPrivateUsage=false, reportIndexIssue=false
"""Every JSON file a bundle stages loses the values under credential-like keys.

A key is credential-like when, compared case-insensitively with ``-`` and ``.``
read as ``_``, it is one of ``password``, ``passwd``, ``secret``, ``token``,
``api_key``, ``apikey``, ``access_key``, ``private_key`` or ``authorization``, or
ends with ``_`` followed by one of them. Its value, whatever its type, becomes
``"<redacted>"``. Nothing is redacted by value pattern alone, and keys that only
contain such a word (``tokenizer``, ``max_tokens``, ``secretariat``) are kept.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from coperceive.repro.scrub import scrub_json

REDACTED = "<redacted>"

CREDENTIAL_KEYS = [
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "access_key",
    "private_key",
    "authorization",
    "PASSWORD",
    "Authorization",
    "hf_token",
    "HF_TOKEN",
    "X-Api-Key",
    "x-apikey",
    "aws.secret",
    "db-password",
    "s3_access_key",
    "ssh.private_key",
]
HARMLESS_KEYS = [
    "tokenizer",
    "max_tokens",
    "secretariat",
    "keyring_name",
    "tokens",
    "password_hint_shown",
]


@pytest.mark.parametrize("key", CREDENTIAL_KEYS)
def test_credential_keys_are_redacted(key: str) -> None:
    assert scrub_json({key: "plain-value-1"}) == {key: REDACTED}


@pytest.mark.parametrize("key", HARMLESS_KEYS)
def test_harmless_keys_are_kept(key: str) -> None:
    assert scrub_json({key: "plain-value-1"}) == {key: "plain-value-1"}


def test_nested_dicts_lists_and_non_string_values() -> None:
    document = {
        "run": {
            "env": {"HF_TOKEN": "plain-value-1", "tokenizer": "bert-base"},
            "headers": [{"X-Api-Key": "plain-value-2"}, {"Accept": "json"}],
            "db": {"password": {"nested": "plain-value-3"}, "port": 5432},
            "max_tokens": 128,
        },
        "token": 1234,
        "items": [[{"secret": ["a", "b"]}]],
    }
    assert scrub_json(document) == {
        "run": {
            "env": {"HF_TOKEN": REDACTED, "tokenizer": "bert-base"},
            "headers": [{"X-Api-Key": REDACTED}, {"Accept": "json"}],
            "db": {"password": REDACTED, "port": 5432},
            "max_tokens": 128,
        },
        "token": REDACTED,
        "items": [[{"secret": REDACTED}]],
    }


def test_a_value_alone_is_never_reason_to_redact() -> None:
    assert scrub_json({"note": "password", "label": "secret token"}) == {
        "note": "password",
        "label": "secret token",
    }


def test_every_staged_json_file_is_redacted_by_key(tmp_path: Path) -> None:
    """The export scrubs each staged JSON file with the same rule (``_scrub_work``)."""
    from coperceive.bundle.export import _scrub_work

    work = tmp_path / "work"
    (work / "eval" / "scene").mkdir(parents=True)
    (work / "eval" / "scene" / "metrics.json").write_text(
        json.dumps(
            {
                "config": {"api_key": "plain-value-1", "max_tokens": 3},
                "runs": [{"Authorization": "plain-value-2", "tokenizer": "kept-value"}],
            }
        ),
        encoding="utf-8",
    )
    redactions, details = _scrub_work(work, ())
    document = json.loads((work / "eval" / "scene" / "metrics.json").read_text(encoding="utf-8"))
    assert document == {
        "config": {"api_key": REDACTED, "max_tokens": 3},
        "runs": [{"Authorization": REDACTED, "tokenizer": "kept-value"}],
    }
    assert redactions == 1
    assert details == [{"file": "eval/scene/metrics.json", "kind": "json"}]
