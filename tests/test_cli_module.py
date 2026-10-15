# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pkg


def test_version() -> None:
    assert pkg.__version__ == "0.0.0"
