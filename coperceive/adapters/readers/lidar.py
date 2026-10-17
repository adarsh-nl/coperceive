# SPDX-License-Identifier: Apache-2.0
"""Lidar readers. Each returns ``(count, bytes)``: little-endian float32
``x, y, z, intensity`` per point, which is the schema 1 blob layout.

ASCII values go through Python ``float`` and then one cast to float32, so the
bytes agree with ``struct.pack('<f', float(text))``.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import numpy as np
import numpy.typing as npt

from coperceive.errors import CoperceiveError

_PLY_TYPES: dict[str, str] = {
    "char": "i1",
    "int8": "i1",
    "uchar": "u1",
    "uint8": "u1",
    "short": "i2",
    "int16": "i2",
    "ushort": "u2",
    "uint16": "u2",
    "int": "i4",
    "int32": "i4",
    "uint": "u4",
    "uint32": "u4",
    "float": "f4",
    "float32": "f4",
    "double": "f8",
    "float64": "f8",
}

_PCD_TYPES: dict[tuple[str, int], str] = {
    ("F", 4): "f4",
    ("F", 8): "f8",
    ("I", 1): "i1",
    ("I", 2): "i2",
    ("I", 4): "i4",
    ("U", 1): "u1",
    ("U", 2): "u2",
    ("U", 4): "u4",
}


class LidarError(CoperceiveError):
    """A point file is malformed or lacks a named property."""


def read_ply(
    path: Path,
    *,
    fields: Mapping[str, str],
    intensity_fill: float | None = None,
) -> tuple[int, bytes]:
    """ASCII or binary little-endian PLY with named vertex properties."""
    data = path.read_bytes()
    marker = b"end_header\n"
    end = data.find(marker)
    if end < 0:
        raise LidarError(f"{path}: no end_header")
    header = data[:end].decode("ascii", errors="replace").splitlines()
    body = data[end + len(marker) :]
    fmt = ""
    count = 0
    names: list[str] = []
    types: list[str] = []
    in_vertex = False
    for line in header:
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "format":
            fmt = parts[1]
        elif parts[0] == "element":
            in_vertex = parts[1] == "vertex"
            if in_vertex:
                count = int(parts[2])
        elif parts[0] == "property" and in_vertex:
            if parts[1] == "list":
                raise LidarError(f"{path}: list properties are not supported")
            types.append(parts[1])
            names.append(parts[2])
    if fmt == "ascii":
        columns = _ascii_columns(body.decode("utf-8"), count, names)
    elif fmt == "binary_little_endian":
        dtype = np.dtype(
            [(name, "<" + _ply_type(kind, path)) for name, kind in zip(names, types, strict=True)]
        )
        rows = np.frombuffer(body, dtype=dtype, count=count)
        columns = {name: rows[name].astype(np.float64) for name in names}
    else:
        raise LidarError(f"{path}: PLY format {fmt!r} is not supported")
    return _pack(columns, fields, intensity_fill, count, path, what="property")


def read_pcd(
    path: Path,
    *,
    fields: Mapping[str, str],
    intensity_fill: float | None = None,
) -> tuple[int, bytes]:
    """PCD 0.7 with ``DATA ascii`` or ``DATA binary``."""
    data = path.read_bytes()
    names: list[str] = []
    sizes: list[int] = []
    kinds: list[str] = []
    counts: list[int] = []
    points = 0
    mode = ""
    offset = 0
    while offset < len(data):
        newline = data.find(b"\n", offset)
        if newline < 0:
            raise LidarError(f"{path}: no DATA line")
        line = data[offset:newline].decode("ascii", errors="replace")
        offset = newline + 1
        parts = line.split()
        if not parts or parts[0].startswith("#"):
            continue
        key = parts[0]
        if key == "FIELDS":
            names = parts[1:]
        elif key == "SIZE":
            sizes = [int(v) for v in parts[1:]]
        elif key == "TYPE":
            kinds = parts[1:]
        elif key == "COUNT":
            counts = [int(v) for v in parts[1:]]
        elif key == "POINTS":
            points = int(parts[1])
        elif key == "DATA":
            mode = parts[1]
            break
    if mode == "binary_compressed":
        raise LidarError(f"{path}: DATA binary_compressed is not supported")
    if not counts:
        counts = [1] * len(names)
    if any(c != 1 for c in counts):
        raise LidarError(f"{path}: COUNT other than 1 is not supported")
    if mode == "ascii":
        columns = _ascii_columns(data[offset:].decode("utf-8"), points, names)
    elif mode == "binary":
        dtype = np.dtype(
            [
                (name, "<" + _pcd_type(kind, size, path))
                for name, kind, size in zip(names, kinds, sizes, strict=True)
            ]
        )
        rows = np.frombuffer(data, dtype=dtype, count=points, offset=offset)
        columns = {name: rows[name].astype(np.float64) for name in names}
    else:
        raise LidarError(f"{path}: DATA {mode!r} is not supported")
    return _pack(columns, fields, intensity_fill, points, path, what="field")


def read_bin_f32(
    path: Path,
    *,
    stride: int,
    offsets: Mapping[str, int],
    intensity_fill: float | None = None,
) -> tuple[int, bytes]:
    """Headerless little-endian float32 records of ``stride`` values."""
    if stride < 3:
        raise LidarError("stride must be at least 3")
    data = path.read_bytes()
    record = stride * 4
    if len(data) % record != 0:
        raise LidarError(f"{path}: {len(data)} bytes is not a multiple of {record}")
    count = len(data) // record
    grid = np.frombuffer(data, dtype="<f4").reshape(count, stride)
    for name, index in offsets.items():
        if index < 0 or index >= stride:
            raise LidarError(f"{path}: offset {index} for {name!r} is outside the stride")
    columns = {name: grid[:, index].astype(np.float64) for name, index in offsets.items()}
    fields = {name: name for name in offsets}
    return _pack(columns, fields, intensity_fill, count, path, what="offset")


def _ascii_columns(text: str, count: int, names: list[str]) -> dict[str, npt.NDArray[np.float64]]:
    width = len(names)
    values = [float(part) for part in text.split()]
    needed = count * width
    if len(values) < needed:
        raise LidarError(f"expected {needed} values, found {len(values)}")
    grid = np.asarray(values[:needed], dtype=np.float64).reshape(count, width)
    return {name: grid[:, index] for index, name in enumerate(names)}


def _pack(
    by_name: Mapping[str, npt.NDArray[np.float64]],
    fields: Mapping[str, str],
    intensity_fill: float | None,
    count: int,
    path: Path,
    *,
    what: str,
) -> tuple[int, bytes]:
    out = np.empty((count, 4), dtype=np.float64)
    for column, key in enumerate(("x", "y", "z")):
        if key not in fields:
            raise LidarError(f"{path}: no source {what} named for {key!r}")
        out[:, column] = _column(by_name, fields[key], path, what)
    if "intensity" in fields:
        out[:, 3] = _column(by_name, fields["intensity"], path, what)
    elif intensity_fill is not None:
        out[:, 3] = intensity_fill
    else:
        raise LidarError(f"{path}: no intensity {what} and no fill value")
    return count, out.astype("<f4").tobytes()


def _column(
    by_name: Mapping[str, npt.NDArray[np.float64]], name: str, path: Path, what: str
) -> npt.NDArray[np.float64]:
    if name not in by_name:
        raise LidarError(f"{path}: no {what} {name!r}")
    return by_name[name]


def _ply_type(kind: str, path: Path) -> str:
    try:
        return _PLY_TYPES[kind]
    except KeyError:
        raise LidarError(f"{path}: PLY type {kind!r} is not supported") from None


def _pcd_type(kind: str, size: int, path: Path) -> str:
    try:
        return _PCD_TYPES[(kind, size)]
    except KeyError:
        raise LidarError(f"{path}: PCD type {kind}{size} is not supported") from None
