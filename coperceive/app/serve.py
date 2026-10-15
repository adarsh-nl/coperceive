# SPDX-License-Identifier: Apache-2.0
"""Process entry used by ``coperceive serve``."""

from __future__ import annotations

from pathlib import Path

import uvicorn

from coperceive.app.server import create_app


def run(*, host: str, port: int, root: str, token: str | None, root_path: str) -> None:
    app = create_app(root=Path(root), token=token, root_path=root_path)
    uvicorn.run(app, host=host, port=port, root_path=root_path)
