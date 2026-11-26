# SPDX-License-Identifier: Apache-2.0
"""The zoo strict-load driver's tensor-only reader and discovery, without torch.

The pickles are written opcode by opcode, as ``torch.save`` lays them out, so the test needs
neither torch nor a real checkpoint. The reader gets plain-Python storage and tensor
factories; on the cluster the driver passes torch's.
"""

from __future__ import annotations

import collections
import importlib.util
import io
import struct
import zipfile
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "hpc" / "zoo_strict_load.py"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("zoo_strict_load_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


zoo: Any = _load()


def _text(value: str) -> bytes:
    data = value.encode()
    return b"X" + struct.pack("<I", len(data)) + data


def _int(value: int) -> bytes:
    return b"K" + bytes([value]) if 0 <= value < 256 else b"J" + struct.pack("<i", value)


def _global(module: str, name: str) -> bytes:
    return b"c" + module.encode() + b"\n" + name.encode() + b"\n"


def _ints(values: tuple[int, ...]) -> bytes:
    return b"(" + b"".join(_int(v) for v in values) + b"t"


ORDERED_DICT = _global("collections", "OrderedDict") + b")R"


def _tensor(
    key: str,
    numel: int,
    size: tuple[int, ...],
    stride: tuple[int, ...],
    *,
    storage: str = "FloatStorage",
    offset: int = 0,
) -> bytes:
    persistent = (
        b"("
        + _text("storage")
        + _global("torch", storage)
        + _text(key)
        + _text("cpu")
        + _int(numel)
        + b"tQ"
    )
    args = (
        b"("
        + persistent
        + _int(offset)
        + _ints(size)
        + _ints(stride)
        + b"\x89"
        + ORDERED_DICT
        + b"t"
    )
    return _global("torch._utils", "_rebuild_tensor_v2") + args + b"R"


def _state_pickle(items: list[tuple[str, bytes]]) -> bytes:
    body = b"\x80\x02" + ORDERED_DICT
    for name, value in items:
        body += _text(name) + value + b"s"
    return body + b"."


def _torch_zip(data_pkl: bytes, records: dict[str, bytes], byteorder: str | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("archive/data.pkl", data_pkl)
        for name, raw in records.items():
            bundle.writestr("archive/data/" + name, raw)
        bundle.writestr("archive/version", "3\n")
        if byteorder is not None:
            bundle.writestr("archive/byteorder", byteorder)
    return buffer.getvalue()


def _floats(*values: float) -> bytes:
    return struct.pack(f"<{len(values)}f", *values)


def _storage(dtype: str, raw: bytes) -> tuple[str, bytes]:
    return (dtype, raw)


def _view(
    storage: tuple[str, bytes], offset: int, size: tuple[int, ...], stride: tuple[int, ...]
) -> dict[str, object]:
    return {"dtype": storage[0], "offset": offset, "size": size, "stride": stride}


def _read(raw: bytes) -> tuple[Any, Any]:
    return zoo.read_torch_zip(raw, _storage, _view)


def test_reads_a_plain_tensor_state_dict_and_shares_storages() -> None:
    pkl = _state_pickle(
        [
            ("conv.weight", _tensor("0", 6, (2, 3), (3, 1))),
            ("conv.bias", _tensor("0", 6, (2,), (1,), offset=4)),
            ("bn.num_batches_tracked", _tensor("1", 1, (), (), storage="LongStorage")),
        ]
    )
    raw = _torch_zip(pkl, {"0": _floats(1, 2, 3, 4, 5, 6), "1": struct.pack("<q", 7)})
    obj, info = _read(raw)
    assert isinstance(obj, collections.OrderedDict)
    assert list(obj) == ["conv.weight", "conv.bias", "bn.num_batches_tracked"]
    assert obj["conv.weight"] == {"dtype": "float32", "offset": 0, "size": (2, 3), "stride": (3, 1)}
    assert obj["conv.bias"]["offset"] == 4
    assert obj["bn.num_batches_tracked"]["dtype"] == "int64"
    assert info["storages"] == 2
    assert info["record_prefix"] == "archive/"
    assert info["globals"] == [
        "collections.OrderedDict",
        "torch.FloatStorage",
        "torch.LongStorage",
        "torch._utils._rebuild_tensor_v2",
    ]


def test_a_global_outside_the_allow_list_is_refused_before_it_is_called(tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    evil = _global("os", "system") + b"(" + _text(f"touch {marker}") + b"tR"
    pkl = _state_pickle([("w", _tensor("0", 1, (1,), (1,))), ("x", evil)])
    with pytest.raises(zoo.ReaderRefused, match=r"os\.system"):
        _read(_torch_zip(pkl, {"0": _floats(1)}))
    assert not marker.exists()


@pytest.mark.parametrize(
    ("module", "name"),
    [
        ("builtins", "eval"),
        ("torch", "load"),
        ("torch._utils", "_rebuild_parameter_with_state"),
        ("numpy.core.multiarray", "_reconstruct"),
        ("torch", "Tensor"),
    ],
)
def test_every_other_name_is_refused(module: str, name: str) -> None:
    pkl = b"\x80\x02" + _global(module, name) + b"."
    with pytest.raises(zoo.ReaderRefused, match="not a plain tensor"):
        _read(_torch_zip(pkl, {}))


def test_storage_bytes_must_match_the_declared_element_count() -> None:
    pkl = _state_pickle([("w", _tensor("0", 6, (6,), (1,)))])
    with pytest.raises(zoo.ReaderRefused, match="20 bytes on disk, 6 x 4 declared"):
        _read(_torch_zip(pkl, {"0": _floats(1, 2, 3, 4, 5)}))


def test_a_tensor_may_not_reach_past_its_storage() -> None:
    pkl = _state_pickle([("w", _tensor("0", 6, (3, 3), (3, 1)))])
    with pytest.raises(zoo.ReaderRefused, match="reaches element 8 of a 6 storage"):
        _read(_torch_zip(pkl, {"0": _floats(1, 2, 3, 4, 5, 6)}))


def test_a_missing_storage_record_is_refused() -> None:
    pkl = _state_pickle([("w", _tensor("0", 1, (1,), (1,)))])
    with pytest.raises(zoo.ReaderRefused, match="archive/data/0 is missing"):
        _read(_torch_zip(pkl, {}))


def test_a_storage_key_that_is_a_path_is_refused() -> None:
    pkl = _state_pickle([("w", _tensor("../../etc/passwd", 1, (1,), (1,)))])
    with pytest.raises(zoo.ReaderRefused, match="not a plain record name"):
        _read(_torch_zip(pkl, {}))


def test_big_endian_and_legacy_files_are_refused() -> None:
    pkl = _state_pickle([("w", _tensor("0", 1, (1,), (1,)))])
    with pytest.raises(zoo.ReaderRefused, match="byte order 'big'"):
        _read(_torch_zip(pkl, {"0": _floats(1)}, byteorder="big"))
    with pytest.raises(zoo.ReaderRefused, match="not a torch zip-format file"):
        _read(b"\x80\x02}q\x00.")


def test_a_truncated_pickle_is_a_refusal_not_a_crash() -> None:
    with pytest.raises(zoo.ReaderRefused, match="could not be read"):
        _read(_torch_zip(b"\x80\x02" + ORDERED_DICT, {}))


def test_unwrap_matches_the_engine() -> None:
    state, notes = zoo.unwrap_state({"state_dict": {"module.a": 1, "module.b": 2}, "epoch": 3})
    assert dict(state) == {"a": 1, "b": 2}
    assert notes == ["unwrapped ['state_dict']", "stripped module. prefix"]
    with pytest.raises(zoo.ReaderRefused, match="holds a list"):
        zoo.unwrap_state([1])


def test_discovery_reads_only_the_included_paths(tmp_path: Path) -> None:
    root = tmp_path / "ckpts"
    (root / "zips").mkdir(parents=True)
    with zipfile.ZipFile(root / "zips" / "two.zip", "w") as bundle:
        bundle.writestr("two/a/config.yaml", "name: a\n")
        bundle.writestr("two/a/latest.pth", b"x")
        bundle.writestr("two/b/config.yaml", "name: b\n")
        bundle.writestr("two/b/eval.yaml", "name: b\n")
        bundle.writestr("two/b/net_epoch3.pth", b"x")
        bundle.writestr("__MACOSX/two/a/._latest.pth", b"x")
        bundle.writestr("loose/net_epoch1.pth", b"x")
    with zipfile.ZipFile(root / "zips" / "data.zip", "w") as bundle:
        bundle.writestr("scene/000.pcd", b"x" * 4096)
    (root / "folder").mkdir()
    (root / "folder" / "config.yaml").write_text("name: f\n")
    (root / "folder" / "net_epoch9.pth").write_bytes(b"x")
    (root / "broken.zip").write_bytes(b"not a zip")
    # A dataset tree beside the checkpoints: never named, so never read.
    (root / "dataset").mkdir()
    with zipfile.ZipFile(root / "dataset" / "frames.zip", "w") as bundle:
        bundle.writestr("model/net_epoch1.pth", b"x")
    (tmp_path / "outside.pth").write_bytes(b"x")

    includes = ["folder", "zips", "broken.zip", "../outside.pth", "/abs", "missing"]
    two_bytes = (root / "zips" / "two.zip").stat().st_size
    found = zoo.discover(str(root), includes, max(two_bytes, 1000))
    ok = [
        (e["container"], e["member"], e["config"], e["eval_yaml"])
        for e in found
        if "error" not in e and "skipped" not in e
    ]
    assert ok == [
        ("folder/net_epoch9.pth", "", "config.yaml", False),
        ("zips/two.zip", "loose/net_epoch1.pth", "", False),
        ("zips/two.zip", "two/a/latest.pth", "two/a/config.yaml", False),
        ("zips/two.zip", "two/b/net_epoch3.pth", "two/b/config.yaml", True),
    ]
    assert [e["container"] for e in found if "skipped" in e] == ["zips/data.zip"]
    assert [(e["container"], e["error"]) for e in found if "error" in e] == [
        ("broken.zip", "unreadable zip: File is not a zip file"),
        ("../outside.pth", "include is not a path inside the root"),
        ("/abs", "include is not a path inside the root"),
        ("missing", "include does not exist"),
    ]
    assert all("dataset" not in e["container"] for e in found)


def test_the_driver_refuses_to_write_under_the_checkpoint_root(tmp_path: Path) -> None:
    root = tmp_path / "ckpts"
    root.mkdir()
    for argv in (
        ["--root", str(root), "--stage", str(root / "stage"), "--out", str(tmp_path / "r.json")],
        ["--root", str(root), "--stage", str(tmp_path / "s"), "--out", str(root / "r.json")],
    ):
        with pytest.raises(SystemExit) as exc:
            zoo.main([*argv, "--include", "x", "--repo", str(tmp_path)])
        assert exc.value.code == 2
    assert list(root.iterdir()) == []
