# SPDX-License-Identifier: Apache-2.0
"""Process entry used by ``coperceive serve``."""

from __future__ import annotations

from pathlib import Path

import uvicorn

from coperceive.app.server import create_app


def run(
    *,
    host: str,
    port: int,
    root: str,
    token: str | None,
    root_path: str,
    maps: str | None = None,
    predictions: str | None = None,
    static: str | None = None,
    media_roots: tuple[str, ...] = (),
    allow_origin: str | None = None,
    remote_stream_argv: list[str] | None = None,
    remote_cache_dir: str | None = None,
    remote_cache_bytes: int | None = None,
) -> None:
    remote = None
    if remote_stream_argv:
        # A cluster dataset viewed over SSH: `root` is the local mirror.
        from coperceive.remote_view.client import StreamClient
        from coperceive.remote_view.scenes import DEFAULT_CACHE_BYTES, DiskCache, RemoteScenes

        cache = DiskCache(
            Path(remote_cache_dir or Path(root) / ".cache"),
            DEFAULT_CACHE_BYTES if remote_cache_bytes is None else remote_cache_bytes,
        )
        remote = RemoteScenes(StreamClient(remote_stream_argv), Path(root), cache)
    app = create_app(
        root=Path(root),
        token=token,
        root_path=root_path,
        maps=maps,
        predictions=predictions,
        static=static,
        media_roots=media_roots,
        allow_origin=allow_origin,
        remote=remote,
    )
    uvicorn.run(app, host=host, port=port, root_path=root_path)


if __name__ == "__main__":
    from coperceive.cli import main

    main()
