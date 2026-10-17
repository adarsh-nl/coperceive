# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np
import pytest
from coperceive.adapters.readers.calib import pinhole_from_matrix
from coperceive.adapters.readers.lidar import LidarError, read_bin_f32, read_pcd, read_ply
from coperceive.adapters.readers.records import (
    FieldError,
    get_field,
    read_csv_rows,
    read_json_record,
    read_json_table,
    read_text_rows,
    read_yaml_record,
)


def test_get_field_walks_dotted_keys_and_list_indices() -> None:
    record = {"info": {"frames": ["000001", "000002"], "calib": [{"m": 3}]}}
    assert get_field(record, "info.frames") == ["000001", "000002"]
    assert get_field(record, "info.frames.1") == "000002"
    assert get_field(record, "info.calib.0.m") == 3
    with pytest.raises(FieldError, match="info.nope"):
        get_field(record, "info.nope")
    with pytest.raises(FieldError, match="info.frames.5"):
        get_field(record, "info.frames.5")


def test_json_and_yaml_records(tmp_path: Path) -> None:
    (tmp_path / "p.json").write_text('{"x": 1.5, "timestamp": 7}', encoding="utf-8")
    (tmp_path / "p.yaml").write_text("x: 1.5\ntimestamp: 7\n", encoding="utf-8")
    assert read_json_record(tmp_path / "p.json") == {"x": 1.5, "timestamp": 7}
    assert read_yaml_record(tmp_path / "p.yaml") == {"x": 1.5, "timestamp": 7}
    (tmp_path / "list.json").write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(FieldError, match="object"):
        read_json_record(tmp_path / "list.json")


def test_json_table_is_a_list_of_records(tmp_path: Path) -> None:
    rows = [{"token": "a", "x": 1}, {"token": "b", "x": 2}]
    (tmp_path / "t.json").write_text(json.dumps(rows), encoding="utf-8")
    assert read_json_table(tmp_path / "t.json") == rows
    (tmp_path / "bad.json").write_text('{"token": "a"}', encoding="utf-8")
    with pytest.raises(FieldError, match="list"):
        read_json_table(tmp_path / "bad.json")


def test_csv_rows_use_the_header(tmp_path: Path) -> None:
    (tmp_path / "poses.csv").write_text("frame,x,y\n1,0.5,2\n2,1.5,3\n", encoding="utf-8")
    rows = read_csv_rows(tmp_path / "poses.csv")
    assert rows == [{"frame": "1", "x": "0.5", "y": "2"}, {"frame": "2", "x": "1.5", "y": "3"}]


def test_text_rows_split_on_whitespace_and_skip_blank_lines(tmp_path: Path) -> None:
    (tmp_path / "l.txt").write_text("car 1 2\n\n  truck 3 4  \n", encoding="utf-8")
    assert read_text_rows(tmp_path / "l.txt") == [["car", "1", "2"], ["truck", "3", "4"]]


def _xyzi(count: int, blob: bytes) -> list[tuple[float, ...]]:
    assert len(blob) == count * 16
    return [struct.unpack_from("<4f", blob, i * 16) for i in range(count)]


def test_ascii_ply_matches_struct_packing(tmp_path: Path) -> None:
    text = (
        "ply\nformat ascii 1.0\nelement vertex 2\n"
        "property float32 x\nproperty float32 y\nproperty float32 z\nproperty float32 I\n"
        "end_header\n1.1 0 0 0.25\n0.1 0.2 0.3 0.7\n"
    )
    (tmp_path / "a.ply").write_text(text, encoding="utf-8")
    count, blob = read_ply(
        tmp_path / "a.ply", fields={"x": "x", "y": "y", "z": "z", "intensity": "I"}
    )
    assert count == 2
    assert blob == struct.pack("<8f", 1.1, 0, 0, 0.25, 0.1, 0.2, 0.3, 0.7)


def test_ascii_ply_picks_named_columns_in_any_order(tmp_path: Path) -> None:
    text = (
        "ply\nformat ascii 1.0\nelement vertex 1\n"
        "property float intensity\nproperty float z\nproperty float y\nproperty float x\n"
        "end_header\n9 3 2 1\n"
    )
    (tmp_path / "b.ply").write_text(text, encoding="utf-8")
    count, blob = read_ply(
        tmp_path / "b.ply", fields={"x": "x", "y": "y", "z": "z", "intensity": "intensity"}
    )
    assert _xyzi(count, blob) == [(1.0, 2.0, 3.0, 9.0)]


def test_binary_ply_and_intensity_fill(tmp_path: Path) -> None:
    header = (
        "ply\nformat binary_little_endian 1.0\nelement vertex 2\n"
        "property float x\nproperty float y\nproperty float z\nproperty uchar flag\n"
        "end_header\n"
    ).encode("ascii")
    body = struct.pack("<fffB", 1, 2, 3, 1) + struct.pack("<fffB", 4, 5, 6, 0)
    (tmp_path / "c.ply").write_bytes(header + body)
    count, blob = read_ply(
        tmp_path / "c.ply", fields={"x": "x", "y": "y", "z": "z"}, intensity_fill=0.0
    )
    assert _xyzi(count, blob) == [(1.0, 2.0, 3.0, 0.0), (4.0, 5.0, 6.0, 0.0)]
    with pytest.raises(LidarError, match="intensity"):
        read_ply(tmp_path / "c.ply", fields={"x": "x", "y": "y", "z": "z"})
    with pytest.raises(LidarError, match="property"):
        read_ply(tmp_path / "c.ply", fields={"x": "x", "y": "y", "z": "nope"}, intensity_fill=0.0)


def test_pcd_ascii_and_binary(tmp_path: Path) -> None:
    header = (
        "# .PCD v0.7\nVERSION 0.7\nFIELDS x y z intensity\nSIZE 4 4 4 4\nTYPE F F F F\n"
        "COUNT 1 1 1 1\nWIDTH 2\nHEIGHT 1\nVIEWPOINT 0 0 0 1 0 0 0\nPOINTS 2\n"
    )
    (tmp_path / "a.pcd").write_text(
        header + "DATA ascii\n1 2 3 0.5\n4 5 6 0.25\n", encoding="utf-8"
    )
    fields = {"x": "x", "y": "y", "z": "z", "intensity": "intensity"}
    count, blob = read_pcd(tmp_path / "a.pcd", fields=fields)
    assert _xyzi(count, blob) == [(1.0, 2.0, 3.0, 0.5), (4.0, 5.0, 6.0, 0.25)]
    (tmp_path / "b.pcd").write_bytes(
        (header + "DATA binary\n").encode("ascii") + struct.pack("<8f", 1, 2, 3, 0.5, 4, 5, 6, 0.25)
    )
    assert read_pcd(tmp_path / "b.pcd", fields=fields) == (count, blob)
    (tmp_path / "c.pcd").write_bytes((header + "DATA binary_compressed\n").encode("ascii"))
    with pytest.raises(LidarError, match="binary_compressed"):
        read_pcd(tmp_path / "c.pcd", fields=fields)


def test_bin_f32_with_stride_and_offsets(tmp_path: Path) -> None:
    points = np.array([[1, 2, 3, 0.5, 9], [4, 5, 6, 0.25, 9]], dtype=np.float32)
    (tmp_path / "a.bin").write_bytes(points.tobytes())
    count, blob = read_bin_f32(
        tmp_path / "a.bin", stride=5, offsets={"x": 0, "y": 1, "z": 2, "intensity": 3}
    )
    assert _xyzi(count, blob) == [(1.0, 2.0, 3.0, 0.5), (4.0, 5.0, 6.0, 0.25)]
    (tmp_path / "bad.bin").write_bytes(b"\x00" * 18)
    with pytest.raises(LidarError, match="multiple"):
        read_bin_f32(
            tmp_path / "bad.bin", stride=4, offsets={"x": 0, "y": 1, "z": 2, "intensity": 3}
        )


def test_pinhole_from_matrix() -> None:
    assert pinhole_from_matrix([[700, 0, 960], [0, 710, 540], [0, 0, 1]]) == (
        700.0,
        710.0,
        960.0,
        540.0,
    )
    with pytest.raises(ValueError, match="3x3"):
        pinhole_from_matrix([[1, 2], [3, 4]])
