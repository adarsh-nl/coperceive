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
import json
import os
import struct
import subprocess
import sys
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


def test_the_routing_hint_reads_tags_as_nothing(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        "noise: !!python/object/apply:collections.OrderedDict\n- - [add_noise, false]\n"
        "evil: !!python/object/apply:os.system [touch pwned]\n"
        "model:\n  core_method: point_pillar_comm_multiscale\n"
    )
    assert zoo._core_hint(str(config)) == "point_pillar_comm_multiscale"
    config.write_text("model:\n  core_method: ../../os\n")
    assert zoo._core_hint(str(config)) == ""


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


# --------------------------------------------------------------------------------------------
# End to end: main() on a synthetic tree, with a stub torch and two stub codebases.
# --------------------------------------------------------------------------------------------

REPO = Path(__file__).resolve().parents[1]

STUB_TORCH = """
__version__ = "0.0+stub"


class _Version:
    cuda = None


version = _Version()


class _Cuda:
    @staticmethod
    def is_available():
        return False

    @staticmethod
    def get_device_name(index):
        return ""


cuda = _Cuda()
for _name in ("float32", "float64", "float16", "bfloat16", "int64", "int32", "int16",
              "int8", "uint8", "bool"):
    globals()[_name] = _name


class Tensor:
    def __init__(self, shape):
        self.shape = tuple(shape)

    def numel(self):
        count = 1
        for dim in self.shape:
            count *= dim
        return count


def frombuffer(buffer, dtype):
    return ("storage", dtype, len(buffer))


def empty(count, dtype=None):
    return ("storage", dtype, 0)


def as_strided(storage, size, stride, offset):
    return Tensor(size)
"""

STUB_TRAIN_UTILS = """
import importlib


def create_model(hypes):
    name = hypes["model"]["core_method"]
    lib = importlib.import_module(__package__.rsplit(".", 1)[0] + ".models." + name)
    model = None
    for attr, cls in lib.__dict__.items():
        if attr.lower() == name.replace("_", "").lower():
            model = cls
    if model is None:
        print("backbone not found")
        exit(0)
    return model(hypes["model"]["args"])
"""

STUB_MODEL = """
import os
from collections import OrderedDict

import torch


class {cls}:
    def __init__(self, args):
        report = os.environ.get("ZOO_TEST_REPORT", "")
        if report and not os.path.exists(report):
            raise RuntimeError("the report was not written before the first model was built")
        self.shapes = OrderedDict((k, tuple(v)) for k, v in args["shapes"].items())

    def state_dict(self):
        return OrderedDict((k, torch.Tensor(s)) for k, s in self.shapes.items())

    def load_state_dict(self, state, strict=True):
        given = OrderedDict((k, tuple(v.shape)) for k, v in state.items())
        if strict and dict(given) != dict(self.shapes):
            raise RuntimeError("Error(s) in loading state_dict for {cls}")
"""


def _stub_codebase(src: Path, package: str, models: dict[str, str]) -> None:
    for sub in ("", "hypes_yaml", "tools", "models"):
        folder = src / package / sub
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "__init__.py").write_text("")
    (src / package / "hypes_yaml" / "yaml_utils.py").write_text(
        "def load_test_params(param):\n    param['parsed'] = True\n    return param\n"
    )
    (src / package / "tools" / "train_utils.py").write_text(STUB_TRAIN_UTILS)
    for module, cls in models.items():
        (src / package / "models" / f"{module}.py").write_text(STUB_MODEL.replace("{cls}", cls))


def _config(name: str, core: str, shapes: str) -> str:
    return (
        f"name: {name}\nyaml_parser: load_test_params\n"
        f"model:\n  core_method: {core}\n  args:\n    shapes: {shapes}\n"
    )


def _weights(items: list[tuple[str, int, tuple[int, ...]]]) -> bytes:
    pickled: list[tuple[str, bytes]] = []
    records: dict[str, bytes] = {}
    for index, (name, numel, size) in enumerate(items):
        stride = (size[1], 1) if len(size) == 2 else (1,)
        pickled.append((name, _tensor(str(index), numel, size, stride)))
        records[str(index)] = _floats(*([0.0] * numel))
    return _torch_zip(_state_pickle(pickled), records)


def _zoo_tree(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    stub = tmp_path / "stub"
    (stub / "torch").mkdir(parents=True)
    (stub / "torch" / "__init__.py").write_text(STUB_TORCH)
    primary, other = tmp_path / "primary", tmp_path / "other"
    _stub_codebase(primary, "fakecood", {"net_a": "NetA"})
    _stub_codebase(other, "fakecood2", {"net_b": "NetB"})

    root = tmp_path / "ckpts"
    (root / "ck" / "other").mkdir(parents=True)
    with zipfile.ZipFile(root / "ck" / "ok.zip", "w") as bundle:
        bundle.writestr("a/config.yaml", _config("a", "net_a", "{w: [2, 3], b: [2]}"))
        bundle.writestr("a/net_epoch1.pth", _weights([("w", 6, (2, 3)), ("b", 2, (2,))]))
    with zipfile.ZipFile(root / "ck" / "bad.zip", "w") as bundle:
        bundle.writestr("bad/config.yaml", _config("bad", "net_a", "{w: [2, 3], b: [2]}"))
        bundle.writestr("bad/latest.pth", _weights([("w", 6, (3, 2)), ("extra", 1, (1,))]))
    (root / "ck" / "other" / "config.yaml").write_text(_config("o", "net_b", "{v: [4]}"))
    (root / "ck" / "other" / "net_epoch2.pth").write_bytes(_weights([("v", 4, (4,))]))
    (root / "dataset").mkdir()
    (root / "dataset" / "net_epoch9.pth").write_bytes(b"never read")
    return stub, primary, other, root


def _run_zoo(
    tmp_path: Path, stub: Path, primary: Path, other: Path, root: Path, primary_src: Path
) -> tuple[subprocess.CompletedProcess[str], Path]:
    out = tmp_path / "out" / "report.json"
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(stub), str(primary)])
    env["ZOO_TEST_REPORT"] = str(out)
    cmd = [
        sys.executable,
        "-B",
        str(SCRIPT),
        "--root",
        str(root),
        "--include",
        "ck",
        "--stage",
        str(tmp_path / "stage"),
        "--out",
        str(out),
        "--repo",
        str(REPO),
        "--primary-src",
        str(primary_src),
        "--package",
        "fakecood",
        "--expect-count",
        "3",
        "--codebase",
        f"other={other}:fakecood2:{sys.executable}",
    ]
    done = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=300)
    return done, out


def _tree_state(root: Path) -> dict[str, tuple[int, int]]:
    return {
        str(path.relative_to(root)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_the_driver_end_to_end_on_a_synthetic_zoo(tmp_path: Path) -> None:
    stub, primary, other, root = _zoo_tree(tmp_path)
    before = _tree_state(root)
    done, out = _run_zoo(tmp_path, stub, primary, other, root, primary)
    assert done.returncode == 0, done.stdout + done.stderr
    report: Any = json.loads(out.read_text())
    assert report["complete"] is True
    assert report["discovered"] == 3
    assert report["includes"] == ["ck"]
    rows: dict[str, Any] = {r["container"]: r for r in report["checkpoints"]}
    assert sorted(rows) == ["ck/bad.zip", "ck/ok.zip", "ck/other/net_epoch2.pth"]

    ok = rows["ck/ok.zip"]
    assert ok["config_status"] == "ok"
    assert ok["config_facts"]["model.core_method"] == "net_a"
    assert ok["reader_status"] == "ok"
    assert ok["tensors"] == 2
    assert ok["model_status"] == "built"
    assert ok["strict_load"] == "ok"
    assert len(ok["container_hash"]["md5"]) == 32

    bad = rows["ck/bad.zip"]
    assert bad["strict_load"] == "failed"
    assert bad["missing_keys"]["keys"] == ["b"]
    assert bad["unexpected_keys"]["keys"] == ["extra"]
    assert bad["shape_mismatches"]["items"] == [{"key": "w", "model": [2, 3], "checkpoint": [3, 2]}]

    moved = rows["ck/other/net_epoch2.pth"]
    assert moved["model_status"] == "module not found"
    assert moved["strict_load"] == "not run"
    (retry,) = moved["other_codebases"]
    assert retry["codebase"] == "other"
    assert retry["model_status"] == "built"
    assert retry["strict_load"] == "ok"
    assert retry["codebase_package"] == "fakecood2"

    assert report["strict_load_counts"] == {"ok": 1, "failed": 1, "not run": 1}
    assert "missing_keys (1):" in done.stdout
    assert "unexpected_keys (1):" in done.stdout
    assert "w: model [2, 3] checkpoint [3, 2]" in done.stdout
    assert "in other: ok" in done.stdout
    assert _tree_state(root) == before
    assert not (tmp_path / "stage" / "work").exists()


def test_the_driver_stops_when_the_package_is_not_from_the_pinned_checkout(
    tmp_path: Path,
) -> None:
    stub, primary, other, root = _zoo_tree(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    done, out = _run_zoo(tmp_path, stub, primary, other, root, elsewhere)
    assert done.returncode == 3, done.stdout + done.stderr
    assert "not from --primary-src" in done.stderr
    assert not out.exists()
