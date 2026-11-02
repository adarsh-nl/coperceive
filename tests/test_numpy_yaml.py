# SPDX-License-Identifier: Apache-2.0
"""Numpy-tagged YAML, as real OPV2V/V2XSet per-frame records carry it, and nothing more."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import yaml
from coperceive.adapters.readers.numpy_yaml import load_dataset_yaml
from coperceive.adapters.readers.records import FieldError, read_yaml_record


def _numpy_dump(payload: object) -> str:
    # numpy's own objects through PyYAML's full dumper, as the OpenCOOD tools wrote them.
    return yaml.dump(payload, Dumper=yaml.Dumper)


@pytest.mark.parametrize("module", ["numpy._core", "numpy.core"])
def test_numpy_tagged_scalars_read_back_as_floats(tmp_path: Path, module: str) -> None:
    text = _numpy_dump(
        {
            "lidar_pose": [np.float64(10.5), np.float64(-2.25), 1.9, 0.0, np.float32(90.0), 0.0],
            "ego_speed": np.float64(5.0),
            "count": np.int64(-3),
            "flag": np.bool_(True),
            "extent": np.array([[2.4, 1.0], [0.75, 0.5]]),
        }
    )
    text = text.replace("numpy._core.", f"{module}.").replace("numpy.core.", f"{module}.")
    assert f"python/object/apply:{module}.multiarray.scalar" in text
    path = tmp_path / "000068.yaml"
    path.write_text(text, encoding="utf-8")

    record = read_yaml_record(path)

    assert record["lidar_pose"] == [10.5, -2.25, 1.9, 0.0, 90.0, 0.0]
    assert all(type(v) is float for v in record["lidar_pose"])  # type: ignore[union-attr]
    assert record["ego_speed"] == 5.0 and type(record["ego_speed"]) is float
    assert record["count"] == -3 and type(record["count"]) is int
    assert record["flag"] is True
    assert record["extent"] == [[2.4, 1.0], [0.75, 0.5]]


def test_big_endian_scalars_keep_their_byte_order() -> None:
    text = _numpy_dump({"v": np.array([7], dtype=">i4")[0], "w": np.dtype(">f8").type(1.25)})
    assert load_dataset_yaml(text) == {"v": 7, "w": 1.25}


@pytest.mark.parametrize(
    "text",
    [
        '!!python/object/apply:os.system ["echo owned"]',
        "!!python/object/apply:subprocess.check_output [[id]]",
        '!!python/name:os.system ""',
        "!!python/object:collections.OrderedDict {}",
        "!!python/tuple [1, 2]",
        "!!python/object/new:numpy.ndarray []",
    ],
)
def test_every_other_python_tag_is_still_refused(tmp_path: Path, text: str) -> None:
    with pytest.raises(yaml.YAMLError, match="could not determine a constructor"):
        load_dataset_yaml(f"value: {text}\n")
    path = tmp_path / "bad.yaml"
    path.write_text(f"value: {text}\n", encoding="utf-8")
    with pytest.raises(FieldError, match=r"not readable YAML: could not determine .* \(line 1,"):
        read_yaml_record(path)


def test_a_non_numeric_dtype_is_refused() -> None:
    text = (
        "v: !!python/object/apply:numpy.core.multiarray.scalar\n"
        "- !!python/object/apply:numpy.dtype {args: [O8, false, true]}\n"
        "- !!binary AAAAAAAAAAA=\n"
    )
    with pytest.raises(yaml.YAMLError, match="not a plain numeric type"):
        load_dataset_yaml(text)


def test_scalar_bytes_must_match_the_dtype() -> None:
    text = (
        "v: !!python/object/apply:numpy.core.multiarray.scalar\n"
        "- !!python/object/apply:numpy.dtype {args: [f8, false, true]}\n"
        "- !!binary AAAA\n"
    )
    with pytest.raises(yaml.YAMLError, match="do not match"):
        load_dataset_yaml(text)
