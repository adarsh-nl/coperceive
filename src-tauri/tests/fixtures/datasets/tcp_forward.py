# SPDX-License-Identifier: Apache-2.0
"""Loopback port forward standing in for `ssh -N -L` in the dataset e2e.

Usage: tcp_forward.py LOCAL_HOST LOCAL_PORT REMOTE_HOST REMOTE_PORT
Runs until killed, like the ssh tunnel it replaces.
"""

import asyncio
import sys


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        writer.close()


async def main() -> None:
    local_host, local_port, remote_host, remote_port = sys.argv[1:5]

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            upstream_reader, upstream_writer = await asyncio.open_connection(
                remote_host, int(remote_port)
            )
        except OSError:
            writer.close()
            return
        await asyncio.gather(_pipe(reader, upstream_writer), _pipe(upstream_reader, writer))

    server = await asyncio.start_server(handle, local_host, int(local_port))
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
