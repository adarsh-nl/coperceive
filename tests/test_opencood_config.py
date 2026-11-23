# SPDX-License-Identifier: Apache-2.0
"""The OpenCOOD plugin reads a checkpoint archive without running code from it by default.

Two gates sit in front of a user-supplied checkpoint, both named ``trust_checkpoint_pickle``:

- the archive's ``config.yaml``: OpenCOOD's own ``load_yaml`` parses with PyYAML's full
  ``yaml.Loader`` and ``eval``s ``yaml_parser``, so the plugin reads the file itself, as data,
  and only hands it to that loader when the user trusts the checkpoint. A genuine config is
  a training run's ``yaml.dump`` and carries numpy and tuple tags, which must still load;
- the weights: ``torch.load(weights_only=True)`` can run code from a crafted file on torch
  before 2.6.0 (CVE-2025-32434), so that path is refused on an older torch unless trusted.

``tests/fake_opencood``'s ``load_yaml`` does what upstream's does (full loader + ``eval``), so
these tests fail if the engine calls it on an untrusted config. ``torch`` is a stub module:
nothing here needs a real torch.
"""

from __future__ import annotations

import importlib
import json
import math
import sys
import types
from fractions import Fraction
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest
import yaml

from tests.opencood_plugin import FAKE_OPENCOOD_PARENT, fake_config, load

# -- helpers -----------------------------------------------------------------------------------


class _StubTorch(types.ModuleType):
    """Just enough ``torch`` for the engine; ``load`` records the keyword arguments it got."""

    def __init__(self, version: str | None) -> None:
        super().__init__("torch")
        if version is not None:
            self.__version__ = version
        self.loads: list[dict[str, object]] = []

    def load(self, path: str, map_location: object = None, **kwargs: object) -> object:
        del path, map_location
        self.loads.append(dict(kwargs))
        return {"model": {"fake.weight": object()}}

    def manual_seed(self, seed: int) -> None:
        del seed


@pytest.fixture
def stub_torch(monkeypatch: pytest.MonkeyPatch) -> _StubTorch:
    """The fake OpenCOOD on ``sys.path`` and a patched (2.7.0) stub as ``torch``."""
    monkeypatch.syspath_prepend(  # pyright: ignore[reportUnknownMemberType]
        str(FAKE_OPENCOOD_PARENT)
    )
    torch = _StubTorch("2.7.0")
    monkeypatch.setitem(sys.modules, "torch", torch)
    return torch


def _upstream_yaml_utils() -> Any:
    """The fake ``opencood.hypes_yaml.yaml_utils`` (upstream's loader behaviour)."""
    if str(FAKE_OPENCOOD_PARENT) not in sys.path:
        raise AssertionError("use the stub_torch fixture first: it puts the fake on sys.path")
    return importlib.import_module("opencood.hypes_yaml.yaml_utils")


def _build(tmp_path: Path, config_text: str, **kwargs: object) -> Any:
    """``OpenCOODEngine`` over ``config_text`` and a stub weights file (never parsed)."""
    engine = load("engine")
    config = tmp_path / "config.yaml"
    config.write_text(config_text, encoding="utf-8")
    weights = tmp_path / "weights.pth"
    weights.write_bytes(b"stub weights; the stub torch never parses them")
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    return engine.OpenCOODEngine(str(config), str(weights), work_dir=str(work), **kwargs)


def _build_error(tmp_path: Path, config_text: str, **kwargs: object) -> Exception | None:
    engine = load("engine")
    try:
        _build(tmp_path, config_text, **kwargs)
    except engine.EngineError as error:
        return error
    return None


def _same(left: object, right: object) -> bool:
    """Deep equality that also compares ndarrays/numpy scalars by dtype, shape and value."""
    if isinstance(left, np.ndarray) or isinstance(right, np.ndarray):
        if not (isinstance(left, np.ndarray) and isinstance(right, np.ndarray)):
            return False
        a = cast("np.ndarray[Any, Any]", left)
        b = cast("np.ndarray[Any, Any]", right)
        # tobytes() is the logical content in C order (NaN-safe, byte order included).
        return a.dtype == b.dtype and a.shape == b.shape and a.tobytes() == b.tobytes()
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        a_map = cast("dict[object, object]", left)
        b_map = cast("dict[object, object]", right)
        return a_map.keys() == b_map.keys() and all(
            _same(value, b_map[key]) for key, value in a_map.items()
        )
    if isinstance(left, (list, tuple)):
        a_seq = cast("list[object] | tuple[object, ...]", left)
        b_seq = cast("list[object] | tuple[object, ...]", right)
        return len(a_seq) == len(b_seq) and all(
            _same(a, b) for a, b in zip(a_seq, b_seq, strict=True)
        )
    if isinstance(left, float) and isinstance(right, float) and math.isnan(left):
        return math.isnan(right)
    return left == right


# A hand-written CoBEVT/point-pillar hypes file, the shape of upstream's point_pillar_cobevt.yaml
# (``1e-3``/``1e-10`` need OpenCOOD's float resolver; ``yaml_parser`` fills grid_size).
_HYPES = """\
name: point_pillar_cobevt
yaml_parser: load_point_pillar_params
train_params: {max_cav: 5, epoches: 60}
optimizer: {core_method: AdamW, lr: 1e-3, args: {eps: 1e-10, weight_decay: 1e-2}}
fusion: {core_method: IntermediateFusionDataset, args: []}
preprocess:
  core_method: SpVoxelPreprocessor
  args: {voxel_size: [0.4, 0.4, 4], max_points_per_voxel: 32}
  cav_lidar_range: &cav_lidar [-140.8, -40, -3, 140.8, 40, 1]
postprocess:
  core_method: VoxelPostprocessor
  order: hwl
  anchor_args: {cav_lidar_range: *cav_lidar, l: 3.9, w: 1.6, h: 1.56, r: [0, 90], num: 2}
  target_args: {score_threshold: 0.2}
model:
  core_method: point_pillar_cobevt
  args: {point_pillar_scatter: {num_features: 64}}
"""


def _training_dump(tmp_path: Path, numpy_module: str) -> str:
    """The ``config.yaml`` a training run writes: ``load_yaml`` then ``yaml.dump(hypes)``.

    Upstream ``train_utils.setup_train`` dumps the hypes *after* ``yaml_parser`` ran, so the
    int64 ndarray ``grid_size`` is written as numpy reduce tags. ``numpy_module`` picks the
    module path numpy 1.x (``numpy.core.multiarray``) or 2.x (``numpy._core.multiarray``)
    writes, whichever numpy this test process has.
    """
    source = tmp_path / "hypes.yaml"
    source.write_text(_HYPES, encoding="utf-8")
    hypes = _upstream_yaml_utils().load_yaml(str(source))
    text = yaml.dump(hypes)  # the default (full) Dumper, as setup_train uses it
    for spelling in ("numpy._core.multiarray", "numpy.core.multiarray"):
        text = text.replace(spelling, "NUMPY_MULTIARRAY")
    assert "NUMPY_MULTIARRAY._reconstruct" in text and "!!python/tuple" in text
    return text.replace("NUMPY_MULTIARRAY", numpy_module)


def _attack(marker: Path, how: str) -> str:
    """A loadable config plus one way of running code (it creates ``marker``)."""
    text = yaml.safe_dump(fake_config(), sort_keys=True)
    if how == "python_tag":
        return text + f"payload: !!python/object/apply:os.mkdir [{json.dumps(str(marker))}]\n"
    expression = f"(__import__('os').mkdir({str(marker)!r}), lambda param: param)[1]"
    return text + f"yaml_parser: {json.dumps(expression)}\n"


# -- the archive's config.yaml (CKPT-1 / ARC-1) ------------------------------------------------


@pytest.mark.parametrize("how", ["python_tag", "yaml_parser_expression"])
def test_archive_config_runs_no_code_before_the_trust_gate(
    stub_torch: _StubTorch, tmp_path: Path, how: str
) -> None:
    """Default options: neither a python tag nor an eval'd yaml_parser runs; the error names
    the trust option, and no weights are read."""
    marker = tmp_path / "config-yaml-ran-code"
    error = _build_error(tmp_path, _attack(marker, how))
    assert not marker.exists(), "the checkpoint's config.yaml ran code with default options"
    assert error is not None, "a config that needs code to load was accepted"
    message = str(error)
    assert "trust_checkpoint_pickle" in message
    assert ("os.mkdir" in message) if how == "python_tag" else ("yaml_parser" in message)
    assert stub_torch.loads == []


@pytest.mark.parametrize("numpy_module", ["numpy.core.multiarray", "numpy._core.multiarray"])
def test_genuine_training_config_with_numpy_tags_loads_by_default(
    stub_torch: _StubTorch, tmp_path: Path, numpy_module: str
) -> None:
    """A real checkpoint config (yaml.dump after yaml_parser) holds numpy tags, which a plain
    SafeLoader refuses; the plugin must still read it, as data, with default options."""
    text = _training_dump(tmp_path, numpy_module)
    with pytest.raises(yaml.constructor.ConstructorError):
        yaml.safe_load(text)  # the premise: SafeLoader alone would refuse a genuine config
    built = _build(tmp_path, text)
    config = built.model.config  # the fake create_model keeps the config it was given
    grid: object = config["model"]["args"]["point_pillar_scatter"]["grid_size"]
    assert isinstance(grid, np.ndarray)
    array = cast("np.ndarray[Any, Any]", grid)
    assert array.dtype == np.int64 and array.tolist() == [704, 200, 1]
    anchors = config["postprocess"]["anchor_args"]
    assert (anchors["W"], anchors["H"], anchors["D"]) == (704, 200, 1)
    assert config["optimizer"]["lr"] == pytest.approx(1e-3)
    assert built.order == "hwl" and built.max_cav == 5
    assert built.describe()["config_loader"] == "safe"
    assert stub_torch.loads == [{"weights_only": True}]


def test_hand_written_hypes_file_reads_like_open_coods_loader(
    stub_torch: _StubTorch, tmp_path: Path
) -> None:
    """An archive may ship the hand-written hypes YAML: same values as upstream's load_yaml,
    including ``1e-3`` as a float and the yaml_parser's grid_size."""
    built = _build(tmp_path, _HYPES)
    config = dict(built.model.config)
    source = tmp_path / "upstream.yaml"
    source.write_text(_HYPES, encoding="utf-8")
    upstream = _upstream_yaml_utils().load_yaml(str(source))
    for key in ("root_dir", "validate_dir"):  # the engine points these at empty scratch dirs
        config.pop(key)
    assert _same(config, upstream)
    assert isinstance(config["optimizer"]["lr"], float)
    assert stub_torch.loads == [{"weights_only": True}]


def test_trusted_config_is_read_by_open_coods_own_loader(
    stub_torch: _StubTorch, tmp_path: Path
) -> None:
    """With trust_checkpoint_pickle=true a tag the plugin will not build goes to upstream's
    load_yaml (which builds it), and plugin.log says so."""
    text = (
        yaml.safe_dump(fake_config()) + "extra: !!python/object/apply:fractions.Fraction [1, 3]\n"
    )
    logs: list[str] = []
    built = _build(tmp_path, text, trust_checkpoint_pickle=True, log=logs.append)
    assert built.model.config["extra"] == Fraction(1, 3)
    assert any("load_yaml" in line and "trust_checkpoint_pickle" in line for line in logs)
    assert built.describe()["config_loader"] == "opencood load_yaml (trusted)"
    assert stub_torch.loads == [{"weights_only": True}]


def test_numpy_scalars_arrays_dtypes_and_tuples_are_rebuilt_as_upstream_builds_them(
    tmp_path: Path,
) -> None:
    """Every value type a dumped config can hold comes back with upstream's types and values,
    including anchors/aliases (a shared dict, a reused dtype) and a Fortran-ordered array."""
    config_module = load("opencood_config")
    shared = {"input_shape": (176, 50, 7), "res": 0.4}
    value = {
        "f8": np.float64(0.25),
        "f4": np.float32(-1.5),
        "i8": np.int64(7),
        "u1": np.uint8(200),
        "bool": np.bool_(True),
        "big_endian": np.array([1.0, 2.0], dtype=">f8"),
        "grid": np.array([704, 200, 1], dtype=np.int64),
        "matrix": np.asfortranarray(np.arange(6, dtype=np.float32).reshape(2, 3)),
        "empty": np.zeros((0, 3), dtype=np.float64),
        "zero_d": np.array(3.5),
        "dtype": np.dtype("<i4"),
        "tuple": (1, (2.5, "x"), [3]),
        "pre": shared,
        "post": shared,
        "lr": 1e-3,
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.dump(value), encoding="utf-8")
    loaded = config_module.load_checkpoint_config(str(path), types.SimpleNamespace())
    upstream = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.UnsafeLoader)  # noqa: S506
    assert _same(loaded, upstream)
    assert loaded["pre"] is loaded["post"]  # an alias stays one object, as upstream
    assert loaded["matrix"].flags.f_contiguous and loaded["grid"].flags.writeable


@pytest.mark.parametrize(
    "snippet",
    [
        "!!python/object/apply:os.mkdir [MARKER]",
        "!!python/name:os.system ''",
        "!!python/module:os ''",
        "!!python/object:argparse.Namespace {a: 1}",
        "!!python/object/new:collections.OrderedDict [[[a, 1]]]",
        "!!python/str text",
        "!!python/object/apply:numpy.core.multiarray._reconstruct {args: [!!python/name:os.system "
        "'', !!python/tuple [0], !!binary 'Yg=='], state: !!python/tuple [1, !!python/tuple [1], "
        "!!python/object/apply:numpy.dtype {args: [i8, false, true]}, false, !!binary "
        "'AQAAAAAAAAA=']}",
        "!!python/object/apply:numpy.core.multiarray._reconstruct {args: [!!python/name:numpy."
        "ndarray '', !!python/tuple [0], !!python/object/apply:os.mkdir [MARKER]], state: "
        "!!python/tuple [1, !!python/tuple [1], !!python/object/apply:numpy.dtype {args: [i8, "
        "false, true]}, false, !!binary 'AQAAAAAAAAA=']}",
        "!!python/object/apply:numpy.core.multiarray.scalar [!!python/object/apply:numpy.dtype "
        "{args: [O8, false, true]}, !!binary 'AAAAAAAAAAA=']",
        "!!python/object/apply:numpy.dtype {args: [U3, false, true]}",
        "!!python/object/apply:numpy.dtype {args: [!!python/object/apply:os.mkdir [MARKER], "
        "false, true]}",
        "!!python/object/apply:numpy.core.multiarray.scalar [!!python/object/apply:numpy.dtype "
        "{args: [f8, false, true]}, !!binary 'AAAA']",
        "!!python/object/apply:numpy.core.multiarray.scalar [!!python/object/apply:numpy.dtype "
        "{args: [f8, false, true]}, !!binary 'AAAAAAAAAAA=', extra]",
    ],
    ids=[
        "apply-os-mkdir",
        "name",
        "module",
        "object",
        "object-new",
        "python-str",
        "ndarray-args-not-numpy",
        "ndarray-args-carry-a-call",
        "scalar-object-dtype",
        "dtype-unicode",
        "dtype-descriptor-is-a-call",
        "scalar-short-bytes",
        "scalar-extra-args",
    ],
)
def test_other_python_tags_are_refused_without_running(tmp_path: Path, snippet: str) -> None:
    config_module = load("opencood_config")
    marker = tmp_path / "ran"
    path = tmp_path / "config.yaml"
    path.write_text(
        "name: x\nvalue: " + snippet.replace("MARKER", json.dumps(str(marker))) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(config_module.ConfigRefused) as caught:
        config_module.load_checkpoint_config(str(path), types.SimpleNamespace())
    assert not marker.exists()
    assert "line 2" in str(caught.value)


def test_non_python_unknown_tags_are_an_ordinary_yaml_error(tmp_path: Path) -> None:
    """``!custom`` is malformed for upstream too: a plain YAML error, not a trust question."""
    config_module = load("opencood_config")
    path = tmp_path / "config.yaml"
    path.write_text("value: !custom 1\n", encoding="utf-8")
    with pytest.raises(yaml.YAMLError):
        config_module.load_checkpoint_config(str(path), types.SimpleNamespace())


class _Parsers:
    """A ``yaml_utils`` stand-in whose parser records its calls."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.load_yaml = self._never
        self.load_point_pillar_params = self._parse

    def _parse(self, param: dict[str, object]) -> dict[str, object]:
        self.calls.append("load_point_pillar_params")
        return {**param, "parsed": True}

    def _never(self, *args: object) -> object:
        raise AssertionError(f"load_yaml must not be called as a yaml_parser: {args}")


@pytest.mark.parametrize("name", ["load_point_pillar_params", "  load_point_pillar_params\n"])
def test_yaml_parser_is_looked_up_never_evaluated(tmp_path: Path, name: str) -> None:
    config_module = load("opencood_config")
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"name": "x", "yaml_parser": name}), encoding="utf-8")
    parsers = _Parsers()
    loaded = config_module.load_checkpoint_config(str(path), parsers)
    assert loaded["parsed"] is True
    assert parsers.calls == ["load_point_pillar_params"]


@pytest.mark.parametrize(
    "value",
    [
        "load_yaml",
        "os.system",
        "eval",
        "__import__('os')",
        "load_point_pillar_params(1)",
        "yaml_utils.load_point_pillar_params",
        "lambda p: p",
        "",
        3,
        None,
    ],
)
def test_yaml_parser_that_is_not_a_parser_name_is_refused(tmp_path: Path, value: object) -> None:
    config_module = load("opencood_config")
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"name": "x", "yaml_parser": value}), encoding="utf-8")
    parsers = _Parsers()
    with pytest.raises(config_module.ConfigRefused, match="yaml_parser"):
        config_module.load_checkpoint_config(str(path), parsers)
    assert parsers.calls == []


def test_yaml_parser_this_open_cood_lacks_is_an_error_not_a_trust_question(
    tmp_path: Path,
) -> None:
    config_module = load("opencood_config")
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"yaml_parser": "load_lift_splat_shoot_params"}), "utf-8")
    with pytest.raises(config_module.ConfigError, match="load_lift_splat_shoot_params") as caught:
        config_module.load_checkpoint_config(str(path), _Parsers())
    assert not isinstance(caught.value, config_module.ConfigRefused)


def test_float_resolution_matches_open_coods_loader(stub_torch: _StubTorch, tmp_path: Path) -> None:
    """OpenCOOD's own float resolver: ``1e-3`` is a float (YAML 1.1 alone makes it a string)."""
    del stub_torch
    config_module = load("opencood_config")
    scalars = [
        "1e-3", "-2E+5", "1_000.5", ".5", "6.", "+1.5e3", "1:30.5", ".inf", "-.Inf", ".NaN",
        "1e3", "10", "0x1F", "0o17", "1_000", "true", "~", "1.0.0", "e5", "1e", "2026-11-16",
    ]  # fmt: skip
    path = tmp_path / "config.yaml"
    path.write_text("".join(f"k{i}: {text}\n" for i, text in enumerate(scalars)), "utf-8")
    loaded = config_module.load_checkpoint_config(str(path), types.SimpleNamespace())
    upstream = _upstream_yaml_utils().load_yaml(str(path))
    assert _same(loaded, upstream)
    assert loaded["k0"] == pytest.approx(1e-3) and loaded["k10"] == pytest.approx(1e3)
    # Why the resolver is needed, and that it is not added to yaml.SafeLoader itself.
    assert yaml.safe_load("lr: 1e-3") == {"lr": "1e-3"}


# -- the weights: torch before 2.6.0 (CKPT-2, CVE-2025-32434) -------------------------------------


def _weights(tmp_path: Path) -> str:
    path = tmp_path / "weights.pth"
    path.write_bytes(b"stub weights; the stub torch never parses them")
    return str(path)


@pytest.mark.parametrize(
    "version",
    [
        "2.5.1",
        "2.5.1+cu121",
        "2.0.0",
        "1.13.1+cu117",
        "1.12.1",
        "2.6.0rc1",
        "2.6.0a0+ecf3bae40a",
        "2.6.0.dev20241112+cpu",
        "2.6.0rc1.post1",
        "2.5.1.post1",
        "",
        "nonsense",
        None,
    ],
)
def test_weights_only_on_torch_before_2_6_needs_the_trust_option(
    tmp_path: Path, version: str | None
) -> None:
    """weights_only=True runs code from a crafted file on torch <= 2.5.1 (CVE-2025-32434);
    an older, pre-release or unknown torch is refused by default, before torch.load runs."""
    engine = load("engine")
    weights = _weights(tmp_path)
    torch = _StubTorch(version)
    with pytest.raises(engine.EngineError) as caught:
        engine._load_weights(torch, weights)  # noqa: SLF001 - the loader under test
    message = str(caught.value)
    assert "CVE-2025-32434" in message and "2.6.0" in message
    assert "trust_checkpoint_pickle" in message
    assert torch.loads == []
    state = engine._load_weights(torch, weights, trust_pickle=True)  # noqa: SLF001
    assert list(state) == ["fake.weight"]


@pytest.mark.parametrize(
    "version",
    [
        "2.6.0",
        "2.6.0+cu124",
        "2.6.0.post1",
        "2.6.0.post100+cpu",
        "2.6.1",
        "2.7.0a0+79aa17489c.nv25.04",
        "2.10.0",
        "3.0.0",
    ],
)
def test_patched_torch_keeps_weights_only_as_the_default(tmp_path: Path, version: str) -> None:
    engine = load("engine")
    torch = _StubTorch(version)
    state = engine._load_weights(torch, _weights(tmp_path))  # noqa: SLF001
    assert list(state) == ["fake.weight"]
    assert torch.loads == [{"weights_only": True}]


@pytest.mark.parametrize(
    ("version", "safe"),
    [
        # The release itself, in every spelling PEP 440 normalises to 2.6.0.
        ("2.6.0", True),
        ("2.6", True),
        ("2.6.0.0", True),
        ("v2.6.0", True),
        (" 2.6.0 \n", True),
        ("0!2.6.0", True),
        ("2.6.0+cu124", True),
        ("2.6.0+rocm6.2.4", True),
        # Post-releases sort after the release (PEP 440), also with a dev or local part.
        ("2.6.0.post1", True),
        ("2.6.0.post100+cpu", True),
        ("2.6.0post1", True),
        ("2.6.0-post1", True),
        ("2.6.0_post1", True),
        ("2.6.0.post.1", True),
        ("2.6.0-1", True),
        ("2.6.0.rev2", True),
        ("2.6.0.r2", True),
        ("2.6.0.POST1", True),
        ("2.6.0.post1.dev3", True),
        ("2.6.0.post", True),
        # Later releases, including their own pre-releases and dev builds.
        ("2.6.0.1", True),
        ("2.6.1rc1", True),
        ("2.6.1.dev20250101", True),
        ("2.7.0a0+79aa17489c.nv25.04", True),
        ("2.7.0.dev20241215+cpu", True),
        ("2.10.0", True),
        ("3", True),
        # Pre-releases and dev builds of 2.6.0 sort before it, post or not.
        ("2.6.0rc1", False),
        ("2.6.0-rc.1", False),
        ("2.6.0c1", False),
        ("2.6.0alpha1", False),
        ("2.6.0b2", False),
        ("2.6.0preview1", False),
        ("2.6.0RC1", False),
        ("2.6.0a0+ecf3bae40a", False),
        ("2.6.0.dev20241112+cpu", False),
        ("2.6.0dev1", False),
        ("2.6.0rc1.post1", False),
        ("2.6.0rc1.post1.dev2", False),
        # Older releases, whatever their suffix.
        ("2.5.1", False),
        ("2.5.1.post1", False),
        ("2.5.9.9", False),
        ("2.5.1+cu121", False),
        ("1.13.1+cu117", False),
        ("1.9.1", False),
        # Not a PEP 440 version, or an epoch torch never uses: fail closed.
        ("", False),
        ("nonsense", False),
        ("2.6.0-foo", False),
        ("2.7.0 garbage", False),
        ("2.6.0+", False),
        ("2.6.0++cpu", False),
        ("1!1.0", False),
        ("2.6.0rc1rc2", False),
        ("2..6", False),
    ],
)
def test_weights_only_is_safe_follows_pep_440_order(version: str, *, safe: bool) -> None:
    """The torch gate compares versions as PEP 440 does: the release tuple first; at an equal
    release a pre-release or dev build is older and a post-release (or its dev build) is
    newer; a local part (``+cu124``) is ignored; anything else is refused."""
    engine = load("engine")
    assert engine.weights_only_is_safe(version) is safe


# -- trust_checkpoint_pickle only widens the config reader for a refused file -----------------


class _UpstreamLoadYamlSpy:
    """Wraps the fake upstream ``load_yaml`` and records each call."""

    def __init__(self, real: Any) -> None:
        self.real = real
        self.calls: list[str] = []

    def __call__(self, path: str) -> object:
        self.calls.append(path)
        return self.real(path)


@pytest.fixture
def load_yaml_spy(stub_torch: _StubTorch, monkeypatch: pytest.MonkeyPatch) -> _UpstreamLoadYamlSpy:
    del stub_torch  # it puts the fake OpenCOOD on sys.path
    yaml_utils = _upstream_yaml_utils()
    spy = _UpstreamLoadYamlSpy(yaml_utils.load_yaml)
    monkeypatch.setattr(yaml_utils, "load_yaml", spy)
    return spy


@pytest.mark.parametrize("numpy_module", ["numpy.core.multiarray", "numpy._core.multiarray"])
def test_trust_option_with_a_genuine_config_stays_on_the_safe_reader(
    stub_torch: _StubTorch,
    load_yaml_spy: _UpstreamLoadYamlSpy,
    tmp_path: Path,
    numpy_module: str,
) -> None:
    """trust_checkpoint_pickle=true does not send every config to upstream's code-running
    load_yaml: a file the safe reader accepts is still read by it, and the log says nothing
    about the trusted loader."""
    text = _training_dump(tmp_path, numpy_module)
    load_yaml_spy.calls.clear()  # _training_dump itself uses upstream's loader
    logs: list[str] = []
    built = _build(tmp_path, text, trust_checkpoint_pickle=True, log=logs.append)
    assert built.describe()["config_loader"] == "safe"
    assert load_yaml_spy.calls == []
    assert not any("load_yaml" in line for line in logs)
    grid = cast(
        "np.ndarray[Any, Any]",
        built.model.config["model"]["args"]["point_pillar_scatter"]["grid_size"],
    )
    assert grid.tolist() == [704, 200, 1]
    assert stub_torch.loads == [{"weights_only": True}]


@pytest.mark.parametrize(
    ("addition", "expected"),
    [
        ("yaml_parser: load_lift_splat_shoot_params\n", "load_lift_splat_shoot_params"),
        ("custom: !custom 1\n", "!custom"),
        ("broken: [unclosed\n", "cannot read the checkpoint config"),
    ],
)
def test_trust_option_does_not_hand_other_config_errors_to_upstream(
    stub_torch: _StubTorch,
    load_yaml_spy: _UpstreamLoadYamlSpy,
    tmp_path: Path,
    addition: str,
    expected: str,
) -> None:
    """Only a ConfigRefused (code the file names) falls back to upstream's load_yaml under
    trust_checkpoint_pickle=true; a parser this OpenCOOD lacks, an unknown non-python tag or
    malformed YAML stays an error, and upstream's loader is never called."""
    text = yaml.safe_dump(fake_config(), sort_keys=True) + addition
    logs: list[str] = []
    error = _build_error(tmp_path, text, trust_checkpoint_pickle=True, log=logs.append)
    assert error is not None, "a malformed config was accepted"
    message = str(error)
    assert expected in message and "cannot read the checkpoint config" in message
    assert load_yaml_spy.calls == []
    assert not any("load_yaml" in line for line in logs)
    assert stub_torch.loads == []
