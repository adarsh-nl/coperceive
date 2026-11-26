# SPDX-License-Identifier: Apache-2.0
"""Strict-load the OpenCOOD checkpoints at named paths, running no code from the files.

A diagnostic job for the OpenCOOD model zoo (v0.1.3), run by ``zoo_strict_load.sbatch`` on a
compute node, never on the login node (importing torch there takes minutes). For every weights
file (``.pth``/``.pt``/``.ckpt``) in the ``--include`` paths (folders or files relative to
``--root``; nothing else under the root is read), in a ``.zip`` or as a plain file:

1. the file holding it is copied to ``--stage`` (node-local scratch) and hashed (MD5, the
   protocol's checkpoint digest, and SHA-256);
2. the ``config.yaml`` beside it is read with the plugin's own safe reader
   (``plugins/opencood/coperceive_opencood_model/opencood_config.py``), which builds only
   tuples and numpy arrays and applies ``yaml_parser`` only as a ``load_<name>_params`` name;
3. the model is built with that codebase's ``tools.train_utils.create_model(config)``;
4. the weights are read with :class:`TensorOnlyUnpickler` below: a ``pickle.Unpickler`` whose
   ``find_class`` returns only this file's own functions for the handful of names a plain
   tensor state dict uses, and refuses every other name. Nothing the file names is imported or
   called; tensors are rebuilt with ``torch.frombuffer`` over the archive's raw storage bytes;
5. missing, unexpected and shape-mismatched keys are listed, then
   ``model.load_state_dict(state, strict=True)`` is called and its outcome recorded.

One JSON report is written to ``--out``, rewritten after every checkpoint (``complete`` is
true only at the end), and the job log lists each checkpoint's keys. Nothing is written under
``--root``: ``--stage`` and ``--out`` are refused if they resolve inside it, and the root is
only opened for reading. No dataset is opened and no forward pass runs. The primary package
must import from ``--primary-src`` (exit 3 otherwise), so the commit the job script checks is
the code that runs.

A model whose module is not in the primary codebase (``opencood``), or whose config the
primary codebase cannot read, is retried in a child process in each
``--codebase NAME=SRC_ROOT:PACKAGE[:PYTHON]`` that has ``PACKAGE/models/<module>.py`` (with
that interpreter, if given); the report labels those results with the codebase, so a
non-OpenCOOD build is never reported as an OpenCOOD one.

Python 3.7 compatible: the opencood-official environment is Python 3.7.16, torch 1.12.1.
"""

from __future__ import annotations

import argparse
import collections
import gc
import hashlib
import importlib
import importlib.util
import io
import json
import os
import pickle
import platform
import shutil
import subprocess
import sys
import time
import traceback
import zipfile

WEIGHTS_SUFFIXES = (".pth", ".pt", ".ckpt")
CONFIG_NAMES = ("config.yaml", "config.yml")
REPORT_SCHEMA = "coperceive.zoo_strict_load/1"
# Upper bound on the bytes one weights member may decompress to (a wrong-file guard).
MAX_WEIGHTS_BYTES = 4 * 1024**3
MAX_KEYS_LISTED = 200
CHILD_TIMEOUT_S = 600


# --------------------------------------------------------------------------------------------
# The tensor-only reader (standard library; torch enters only through the two factories).
# --------------------------------------------------------------------------------------------

# torch's typed storage names -> (torch dtype attribute, bytes per element).
STORAGE_DTYPES = {
    "FloatStorage": ("float32", 4),
    "DoubleStorage": ("float64", 8),
    "HalfStorage": ("float16", 2),
    "BFloat16Storage": ("bfloat16", 2),
    "LongStorage": ("int64", 8),
    "IntStorage": ("int32", 4),
    "ShortStorage": ("int16", 2),
    "CharStorage": ("int8", 1),
    "ByteStorage": ("uint8", 1),
    "BoolStorage": ("bool", 1),
}


class ReaderRefused(Exception):
    """The weights file needs something other than plain tensors in (ordered) dicts."""


class _StorageType:
    """The value ``find_class`` gives for ``torch.<X>Storage``: a name, never torch's class."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:
        return "torch." + self.name


class _Storage:
    """A storage this reader built from the archive's ``data/<key>`` bytes."""

    def __init__(self, value: object, dtype: str, numel: int) -> None:
        self.value = value
        self.dtype = dtype
        self.numel = numel


def _as_int_tuple(value: object, what: str) -> tuple:
    if not isinstance(value, tuple) or not all(
        isinstance(item, int) and not isinstance(item, bool) for item in value
    ):
        raise ReaderRefused(f"tensor {what} is not a tuple of ints: {value!r}")
    return value


class TensorOnlyUnpickler(pickle.Unpickler):
    """Unpickle a torch zip checkpoint's ``data.pkl`` allowing only plain tensors.

    ``find_class`` answers five names with this module's own functions and refuses the rest,
    so the file cannot import or call anything. ``persistent_load`` answers only
    ``('storage', torch.<X>Storage, key, location, numel)`` and builds the storage from the
    archive's raw bytes with ``make_storage(dtype, raw)``; ``make_tensor(storage, offset,
    size, stride)`` makes each tensor a view of it.
    """

    def __init__(self, data_pkl: bytes, read_record, make_storage, make_tensor) -> None:
        super().__init__(io.BytesIO(data_pkl))
        self._read_record = read_record
        self._make_storage = make_storage
        self._make_tensor = make_tensor
        self._storages: dict = {}
        self.globals_seen: list = []

    def find_class(self, module: str, name: str):  # noqa: ANN201 - pickle's signature
        qualified = module + "." + name
        self.globals_seen.append(qualified)
        if module == "collections" and name == "OrderedDict":
            return collections.OrderedDict
        if module == "torch._utils" and name in ("_rebuild_tensor_v2", "_rebuild_tensor"):
            return self._rebuild_tensor
        if module == "torch._utils" and name == "_rebuild_parameter":
            return self._rebuild_parameter
        if module == "torch" and name in STORAGE_DTYPES:
            return _StorageType(name)
        raise ReaderRefused(f"the weights pickle names {qualified}, which is not a plain tensor")

    def persistent_load(self, pid: object) -> object:
        if not isinstance(pid, tuple) or len(pid) != 5 or pid[0] != "storage":
            raise ReaderRefused(f"unexpected persistent id {pid!r}")
        _tag, storage_type, key, _location, numel = pid
        if not isinstance(storage_type, _StorageType):
            raise ReaderRefused(f"storage type {storage_type!r} is not a torch typed storage")
        if not isinstance(key, str) or not key.isalnum():
            raise ReaderRefused(f"storage key {key!r} is not a plain record name")
        if not isinstance(numel, int) or isinstance(numel, bool) or numel < 0:
            raise ReaderRefused(f"storage element count {numel!r} is not a non-negative int")
        cached = self._storages.get(key)
        if cached is not None:
            if cached.dtype != STORAGE_DTYPES[storage_type.name][0]:
                raise ReaderRefused(f"storage {key} is referenced with two dtypes")
            return cached
        dtype, itemsize = STORAGE_DTYPES[storage_type.name]
        raw = self._read_record("data/" + key)
        if len(raw) != numel * itemsize:
            raise ReaderRefused(
                f"storage {key}: {len(raw)} bytes on disk, {numel} x {itemsize} declared"
            )
        storage = _Storage(self._make_storage(dtype, raw), dtype, numel)
        self._storages[key] = storage
        return storage

    def _rebuild_tensor(self, storage, offset, size, stride, *rest):  # noqa: ANN001, ANN202
        if not isinstance(storage, _Storage):
            raise ReaderRefused("a tensor is rebuilt from something that is not a storage")
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise ReaderRefused(f"tensor offset {offset!r} is not a non-negative int")
        size = _as_int_tuple(size, "size")
        stride = _as_int_tuple(stride, "stride")
        if len(size) != len(stride) or any(item < 0 for item in size + stride):
            raise ReaderRefused(f"tensor size {size} / stride {stride} do not fit")
        if all(item > 0 for item in size):
            # Lengths are equal (checked above); Python 3.7 has no zip(strict=).
            pairs = zip(size, stride)  # noqa: B905
            last = offset + sum((dim - 1) * step for dim, step in pairs)
            if last >= storage.numel:
                raise ReaderRefused(f"tensor reaches element {last} of a {storage.numel} storage")
        # rest: requires_grad, backward_hooks[, metadata]; hooks must be an empty mapping.
        for extra in rest[1:]:
            if isinstance(extra, dict) and extra:
                raise ReaderRefused("a tensor carries backward hooks or metadata")
        return self._make_tensor(storage.value, offset, size, stride)

    def _rebuild_parameter(self, data, requires_grad, backward_hooks):  # noqa: ANN001, ANN202
        if isinstance(backward_hooks, dict) and backward_hooks:
            raise ReaderRefused("a parameter carries backward hooks")
        return data


def read_torch_zip(raw: bytes, make_storage, make_tensor) -> tuple:
    """``(object, info)`` from the bytes of a torch zip-format weights file.

    ``info`` holds the record prefix, the globals the pickle named and the byte order.
    Raises :class:`ReaderRefused` for anything that is not a torch zip of plain tensors.
    """
    if not zipfile.is_zipfile(io.BytesIO(raw)):
        raise ReaderRefused("not a torch zip-format file (legacy torch format is not read)")
    archive = zipfile.ZipFile(io.BytesIO(raw))
    pickles = [name for name in archive.namelist() if name.endswith("/data.pkl")]
    if len(pickles) != 1:
        raise ReaderRefused(f"expected one <prefix>/data.pkl record, found {pickles}")
    prefix = pickles[0][: -len("data.pkl")]
    names = set(archive.namelist())
    byteorder = "little (no byteorder record)"
    if prefix + "byteorder" in names:
        byteorder = archive.read(prefix + "byteorder").decode("ascii", "replace").strip()
        if byteorder != "little":
            raise ReaderRefused(f"byte order {byteorder!r}; this reader reads little-endian only")
    if sys.byteorder != "little":
        raise ReaderRefused("this host is not little-endian")

    def read_record(name: str) -> bytes:
        full = prefix + name
        if full not in names:
            raise ReaderRefused(f"record {full} is missing")
        return archive.read(full)

    unpickler = TensorOnlyUnpickler(
        archive.read(pickles[0]), read_record, make_storage, make_tensor
    )
    try:
        obj = unpickler.load()
    except ReaderRefused:
        raise
    except Exception as exc:  # noqa: BLE001 - a malformed pickle is a refusal, not a crash
        raise ReaderRefused(
            f"the weights pickle could not be read: {type(exc).__name__}: {exc}"
        ) from None
    info = {
        "record_prefix": prefix,
        "byteorder": byteorder,
        "globals": sorted(set(unpickler.globals_seen)),
        "storages": len(unpickler._storages),
    }
    return obj, info


def unwrap_state(obj: object) -> tuple:
    """The engine's unwrapping (``opencood_engine._load_weights``): ``(state, notes)``."""
    notes = []
    if not isinstance(obj, dict):
        raise ReaderRefused(f"the weights file holds a {type(obj).__name__}, not a state dict")
    state = obj
    for key in ("model", "state_dict", "model_state_dict", "net"):
        inner = state.get(key)
        if isinstance(inner, dict):
            notes.append(f"unwrapped [{key!r}]")
            state = inner
            break
    state = collections.OrderedDict((str(key), value) for key, value in state.items())
    if state and all(key.startswith("module.") for key in state):
        notes.append("stripped module. prefix")
        state = collections.OrderedDict((key[len("module.") :], v) for key, v in state.items())
    return state, notes


# --------------------------------------------------------------------------------------------
# Discovery and staging (read-only on the root).
# --------------------------------------------------------------------------------------------


def _inside(path: str, root: str) -> bool:
    path, root = os.path.realpath(path), os.path.realpath(root)
    return os.path.commonpath([path, root]) == root


def _sibling_config(names: set, weights: str) -> str:
    folder = weights.rsplit("/", 1)[0] + "/" if "/" in weights else ""
    for candidate in CONFIG_NAMES:
        if folder + candidate in names:
            return folder + candidate
    return ""


# A zip bigger than this is listed as skipped and never opened: the checkpoint zips here are
# 8-100 MB, while dataset zips that may sit beside them are gigabytes.
DEFAULT_MAX_ZIP_BYTES = 1024**3


def _scan(full: str, rel: str, siblings: set, max_zip_bytes: int) -> list:
    """The weights entries one file contributes (none for a file that holds no weights)."""
    lower = full.lower()
    if lower.endswith(".zip"):
        size = os.path.getsize(full)
        if size > max_zip_bytes:
            return [
                {"container": rel, "skipped": f"{size} bytes, over {max_zip_bytes}; not opened"}
            ]
        try:
            with zipfile.ZipFile(full) as bundle:
                infos = sorted(
                    (i for i in bundle.infolist() if not i.is_dir()),
                    key=lambda i: i.filename,
                )
        except (zipfile.BadZipFile, OSError) as exc:
            return [{"container": rel, "error": f"unreadable zip: {exc}"}]
        names = {i.filename for i in infos}
        out = []
        for info in infos:
            member = info.filename
            if member.startswith("__MACOSX/") or not member.lower().endswith(WEIGHTS_SUFFIXES):
                continue
            folder = member.rsplit("/", 1)[0] + "/" if "/" in member else ""
            out.append(
                {
                    "container": rel,
                    "member": member,
                    "member_bytes": info.file_size,
                    "config": _sibling_config(names, member),
                    "eval_yaml": folder + "eval.yaml" in names,
                }
            )
        return out
    if lower.endswith(WEIGHTS_SUFFIXES):
        return [
            {
                "container": rel,
                "member": "",
                "member_bytes": os.path.getsize(full),
                "config": next((c for c in CONFIG_NAMES if c in siblings), ""),
                "eval_yaml": "eval.yaml" in siblings,
            }
        ]
    return []


def discover(root: str, includes: list, max_zip_bytes: int = DEFAULT_MAX_ZIP_BYTES) -> list:
    """Every weights file in the ``includes`` (paths relative to ``root``), in order.

    Only the named folders and files are looked at, so a dataset tree beside the checkpoints
    is never walked. An include that is absolute, leaves ``root`` or does not exist is an
    error entry; symbolic links are not followed out of ``root``.
    """
    found = []
    real_root = os.path.realpath(root)
    for include in includes:
        parts = include.replace(os.sep, "/").strip("/").split("/")
        full = os.path.join(real_root, *parts)
        if os.path.isabs(include) or ".." in parts or not _inside(full, real_root):
            found.append({"container": include, "error": "include is not a path inside the root"})
            continue
        if os.path.isfile(full):
            siblings = set(os.listdir(os.path.dirname(full)))
            rel = os.path.relpath(full, real_root).replace(os.sep, "/")
            found.extend(_scan(full, rel, siblings, max_zip_bytes))
            continue
        if not os.path.isdir(full):
            found.append({"container": include, "error": "include does not exist"})
            continue
        for directory, dirs, files in os.walk(full):
            dirs.sort()
            for name in sorted(files):
                path = os.path.join(directory, name)
                if os.path.islink(path) and not _inside(path, real_root):
                    continue
                rel = os.path.relpath(path, real_root).replace(os.sep, "/")
                found.extend(_scan(path, rel, set(files), max_zip_bytes))
    return found


def stage_copy(source: str, destination: str) -> dict:
    """Copy ``source`` to ``destination`` (read-only on the source) and hash the bytes."""
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    md5, sha = hashlib.md5(), hashlib.sha256()  # noqa: S324 - the protocol's MD5 digest
    size = 0
    with open(source, "rb") as src, open(destination, "wb") as dst:
        for chunk in iter(lambda: src.read(1 << 22), b""):
            md5.update(chunk)
            sha.update(chunk)
            dst.write(chunk)
            size += len(chunk)
    return {"bytes": size, "md5": md5.hexdigest(), "sha256": sha.hexdigest()}


# --------------------------------------------------------------------------------------------
# One checkpoint: config -> model -> weights -> strict load.
# --------------------------------------------------------------------------------------------


def _load_plugin_config_module(repo: str):  # noqa: ANN202
    path = os.path.join(
        repo, "plugins", "opencood", "coperceive_opencood_model", "opencood_config.py"
    )
    spec = importlib.util.spec_from_file_location("coperceive_zoo_opencood_config", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import the plugin's config reader from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ordered_dict_loader(cfgmod):  # noqa: ANN001, ANN202
    """The plugin's loader plus ``!!python/object/apply:collections.OrderedDict`` as data.

    Driver-only, for the report: a config the plugin refuses for that one tag is also read
    this way, so its model can still be checked. The tag's single argument (a list of
    ``[key, value]`` pairs) is built as an ``OrderedDict``; nothing is imported or called.
    """
    import yaml
    from yaml.nodes import SequenceNode

    class Loader(cfgmod.CheckpointConfigLoader):
        pass

    def construct(loader, node):  # noqa: ANN001, ANN202
        if not isinstance(node, SequenceNode) or len(node.value) != 1:
            raise cfgmod.ConfigRefused("OrderedDict tag without exactly one argument")
        pairs = loader.construct_sequence(node.value[0], deep=True)
        if not all(isinstance(p, list) and len(p) == 2 for p in pairs):
            raise cfgmod.ConfigRefused("OrderedDict tag argument is not a list of pairs")
        return collections.OrderedDict((k, v) for k, v in pairs)

    Loader.add_constructor(cfgmod._APPLY + "collections.OrderedDict", construct)

    def load(path: str, yaml_utils):  # noqa: ANN001, ANN202
        with open(path, "rb") as handle:
            param = yaml.load(handle, Loader=Loader)  # noqa: S506 - a SafeLoader subclass
        if isinstance(param, dict) and "yaml_parser" in param:
            return cfgmod.apply_yaml_parser(param, yaml_utils)
        return param

    return load


def _core_hint(path: str) -> str:
    """``model.core_method`` from a config no reader accepted, for routing only.

    Every tag is read as ``None`` (nothing is built, imported or called); the result only
    decides which ``--codebase`` child gets to read the config with its own code.
    """
    import yaml

    class Lenient(yaml.SafeLoader):
        pass

    Lenient.add_multi_constructor("", lambda loader, suffix, node: None)
    try:
        with open(path, "rb") as handle:
            data = yaml.load(handle, Loader=Lenient)  # noqa: S506 - a SafeLoader subclass
        core = data["model"]["core_method"]
    except Exception:  # noqa: BLE001 - no hint is a valid answer
        return ""
    return core if isinstance(core, str) and core.replace("_", "").isalnum() else ""


def _short_error(exc: BaseException, limit: int = 4000) -> str:
    text = f"{type(exc).__name__}: {exc}"
    return text if len(text) <= limit else text[:limit] + f" ... [{len(text) - limit} more chars]"


def _tail(limit: int = 3000) -> str:
    text = traceback.format_exc()
    return text[-limit:]


def _listed(keys: list) -> dict:
    keys = sorted(keys)
    return {
        "count": len(keys),
        "keys": keys[:MAX_KEYS_LISTED],
        "truncated": len(keys) > MAX_KEYS_LISTED,
    }


def check_one(entry: dict, stage: str, repo: str, package: str, relaxed_ordered_dict: bool) -> dict:
    """Read config, build model, read weights, strict-load; every step recorded in ``entry``."""
    import torch

    result = dict(entry)
    result["codebase_package"] = package
    staged = os.path.join(stage, "files", entry["container"])
    work = os.path.join(stage, "work", str(entry["index"]))
    os.makedirs(work, exist_ok=True)

    # Config: extract the sibling config.yaml (zip) or use the file beside the weights.
    config_path = ""
    if not entry.get("config"):
        result["config_status"] = "missing"
    elif entry.get("member"):
        config_path = os.path.join(work, "config.yaml")
        with zipfile.ZipFile(staged) as bundle, open(config_path, "wb") as out:
            out.write(bundle.read(entry["config"]))
    else:
        config_path = os.path.join(os.path.dirname(staged), entry["config"])

    config = None
    if config_path:
        cfgmod = _load_plugin_config_module(repo)
        yaml_utils = importlib.import_module(package + ".hypes_yaml.yaml_utils")
        try:
            config = cfgmod.load_checkpoint_config(config_path, yaml_utils)
            result["config_status"] = "ok"
            result["config_reader"] = "plugin opencood_config.load_checkpoint_config"
        except cfgmod.ConfigRefused as exc:
            result["config_status"] = "refused by plugin reader"
            result["config_error"] = _short_error(exc)
            if relaxed_ordered_dict:
                try:
                    config = _ordered_dict_loader(cfgmod)(config_path, yaml_utils)
                    result["config_reader"] = (
                        "plugin reader + OrderedDict tag as data (driver only, not the plugin)"
                    )
                except Exception as relaxed:  # noqa: BLE001
                    result["config_relaxed_error"] = _short_error(relaxed)
        except Exception as exc:  # noqa: BLE001
            result["config_status"] = "error"
            result["config_error"] = _short_error(exc)

    if config_path and not isinstance(config, dict):
        result["core_hint"] = _core_hint(config_path)

    if isinstance(config, dict):
        model_cfg = config.get("model") if isinstance(config.get("model"), dict) else {}
        fusion = config.get("fusion") if isinstance(config.get("fusion"), dict) else {}
        post = config.get("postprocess") if isinstance(config.get("postprocess"), dict) else {}
        pre = config.get("preprocess") if isinstance(config.get("preprocess"), dict) else {}
        result["config_facts"] = {
            "name": config.get("name"),
            "model.core_method": model_cfg.get("core_method"),
            "fusion.core_method": fusion.get("core_method"),
            "preprocess.core_method": pre.get("core_method"),
            "postprocess.core_method": post.get("core_method"),
            "root_dir": config.get("root_dir"),
            "validate_dir": config.get("validate_dir"),
            "has_wild_setting": "wild_setting" in config,
        }

    # Model.
    model = None
    if isinstance(config, dict):
        try:
            train_utils = importlib.import_module(package + ".tools.train_utils")
            model = train_utils.create_model(config)
            if model is None:
                raise RuntimeError("create_model returned None (class not found in the module)")
            result["model_status"] = "built"
            result["model_class"] = type(model).__module__ + "." + type(model).__name__
            result["model_file"] = getattr(sys.modules.get(type(model).__module__), "__file__", "")
        except ModuleNotFoundError as exc:
            # Only the model module itself missing means "not in this codebase"; a missing
            # dependency (spconv, a compiled op, icecream) is a build failure.
            core = model_cfg.get("core_method")
            own = package + ".models." + str(core)
            missing = getattr(exc, "name", "") or ""
            if missing == own or (missing.startswith(package) and own.startswith(missing + ".")):
                result["model_status"] = "module not found"
            else:
                result["model_status"] = "build failed"
                result["missing_dependency"] = missing
            result["model_error"] = _short_error(exc)
        except SystemExit as exc:
            # Upstream create_model prints and calls exit(0) when the module has no such class.
            result["model_status"] = "class not found"
            result["model_error"] = f"create_model called exit({exc.code})"
        except Exception as exc:  # noqa: BLE001
            result["model_status"] = "build failed"
            result["model_error"] = _short_error(exc)
            result["model_traceback"] = _tail()
    else:
        result["model_status"] = "not built (no config)"

    # Weights (always read, even without a model: the reader's verdict is a result too).
    state = None
    try:
        if entry.get("member"):
            with zipfile.ZipFile(staged) as bundle:
                info = bundle.getinfo(entry["member"])
                if info.file_size > MAX_WEIGHTS_BYTES:
                    raise ReaderRefused(f"member declares {info.file_size} bytes")
                raw = bundle.read(entry["member"])
        else:
            with open(staged, "rb") as handle:
                raw = handle.read()
        result["weights_md5"] = hashlib.md5(raw).hexdigest()  # noqa: S324

        def make_storage(dtype: str, data: bytes):  # noqa: ANN202
            if not data:
                return torch.empty(0, dtype=getattr(torch, dtype))
            return torch.frombuffer(bytearray(data), dtype=getattr(torch, dtype))

        def make_tensor(storage, offset, size, stride):  # noqa: ANN001, ANN202
            return torch.as_strided(storage, size, stride, offset)

        obj, info = read_torch_zip(raw, make_storage, make_tensor)
        del raw
        state, notes = unwrap_state(obj)
        if not all(isinstance(v, torch.Tensor) for v in state.values()):
            bad = sorted(k for k, v in state.items() if not isinstance(v, torch.Tensor))
            raise ReaderRefused(f"non-tensor values under {bad[:10]}")
        result["reader_status"] = "ok"
        result["reader_info"] = info
        result["reader_notes"] = notes
        result["tensors"] = len(state)
        result["params"] = int(sum(v.numel() for v in state.values()))
    except ReaderRefused as exc:
        result["reader_status"] = "refused"
        result["reader_error"] = _short_error(exc)
    except Exception as exc:  # noqa: BLE001
        result["reader_status"] = "error"
        result["reader_error"] = _short_error(exc)

    # Strict load.
    if model is not None and state is not None:
        expected = {k: tuple(v.shape) for k, v in model.state_dict().items()}
        given = {k: tuple(v.shape) for k, v in state.items()}
        result["missing_keys"] = _listed([k for k in expected if k not in given])
        result["unexpected_keys"] = _listed([k for k in given if k not in expected])
        mismatched = sorted(k for k in expected if k in given and expected[k] != given[k])
        result["shape_mismatches"] = {
            "count": len(mismatched),
            "items": [
                {"key": k, "model": list(expected[k]), "checkpoint": list(given[k])}
                for k in mismatched[:MAX_KEYS_LISTED]
            ],
        }
        result["model_tensors"] = len(expected)
        try:
            model.load_state_dict(state, strict=True)
            result["strict_load"] = "ok"
        except Exception as exc:  # noqa: BLE001
            result["strict_load"] = "failed"
            result["strict_error"] = _short_error(exc)
    else:
        result["strict_load"] = "not run"
    del model, state
    gc.collect()
    return result


# --------------------------------------------------------------------------------------------
# Driver.
# --------------------------------------------------------------------------------------------


def _git_head(path: str) -> dict:
    try:
        head = subprocess.run(
            ["git", "--no-optional-locks", "-C", path, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout.strip()
        dirty = subprocess.run(
            # --no-optional-locks: status must not refresh (write) the checkout's index.
            ["git", "--no-optional-locks", "-C", path, "status", "--porcelain", "-uno"],
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout.strip()
        return {"path": path, "head": head, "tracked_changes": bool(dirty)}
    except Exception as exc:  # noqa: BLE001
        return {"path": path, "error": _short_error(exc)}


def _environment(repo: str, primary_src: str) -> dict:
    import torch

    env = {
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "host": platform.node(),
        "slurm_job_id": os.environ.get("SLURM_JOB_ID", ""),
        "torch": torch.__version__,
        "torch_cuda": torch.version.cuda,
        "cuda_available": torch.cuda.is_available(),
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "",
        "repo": _git_head(repo),
        "primary_codebase": _git_head(primary_src),
    }
    # Distribution versions without importing them (pkg_resources: Python 3.7 has no
    # importlib.metadata).
    try:
        import pkg_resources

        for dist in ("spconv-cu113", "cumm-cu113", "numpy", "PyYAML", "timm"):
            try:
                env.setdefault("packages", {})[dist] = pkg_resources.get_distribution(dist).version
            except Exception:  # noqa: BLE001 - absent distributions are simply not listed
                pass
    except ImportError:
        pass
    return env


def _parse_codebases(values: list) -> list:
    """``NAME=SRC_ROOT:PACKAGE[:PYTHON]``; PYTHON defaults to this interpreter."""
    out = []
    for value in values or []:
        name, sep, rest = value.partition("=")
        fields = rest.split(":")
        if not sep or not name or len(fields) not in (2, 3) or not all(fields):
            raise SystemExit(f"--codebase must be NAME=SRC_ROOT:PACKAGE[:PYTHON], got {value!r}")
        python = fields[2] if len(fields) == 3 else sys.executable
        out.append(
            {
                "name": name,
                "src": os.path.abspath(fields[0]),
                "package": fields[1],
                "python": python,
            }
        )
    return out


def _run_child(args, entry: dict, codebase: dict) -> dict:  # noqa: ANN001
    env = dict(os.environ)
    env["PYTHONPATH"] = codebase["src"] + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    cmd = [
        codebase["python"],
        "-B",
        os.path.abspath(__file__),
        "--child",
        json.dumps(entry),
        "--package",
        codebase["package"],
        "--stage",
        args.stage,
        "--repo",
        args.repo,
        "--expect-src",
        codebase["src"],
    ]
    if args.relaxed_ordered_dict:
        cmd.append("--relaxed-ordered-dict")
    try:
        done = subprocess.run(
            cmd, capture_output=True, text=True, timeout=CHILD_TIMEOUT_S, env=env, cwd=args.stage
        )
    except subprocess.TimeoutExpired:
        return {
            "codebase": codebase["name"],
            "child_status": f"timed out after {CHILD_TIMEOUT_S} s",
        }
    marker = "ZOO_CHILD_RESULT "
    for line in reversed(done.stdout.splitlines()):
        if line.startswith(marker):
            result = json.loads(line[len(marker) :])
            result["codebase"] = codebase["name"]
            result["codebase_src"] = _git_head(codebase["src"])
            result["codebase_python"] = codebase["python"]
            return result
    return {
        "codebase": codebase["name"],
        "child_status": f"no result (exit {done.returncode})",
        "child_stderr_tail": done.stderr[-3000:],
    }


def child_main(args) -> int:  # noqa: ANN001
    entry = json.loads(args.child)
    try:
        pkg = importlib.import_module(args.package)
        pkg_file = os.path.realpath(getattr(pkg, "__file__", "") or "")
        if args.expect_src and not _inside(pkg_file, args.expect_src):
            raise RuntimeError(f"{args.package} imports from {pkg_file}, not {args.expect_src}")
        result = check_one(entry, args.stage, args.repo, args.package, args.relaxed_ordered_dict)
        result["package_file"] = pkg_file
    except Exception as exc:  # noqa: BLE001
        result = dict(
            entry, child_status="failed", child_error=_short_error(exc), child_traceback=_tail()
        )
    print("ZOO_CHILD_RESULT " + json.dumps(result, default=str), flush=True)
    return 0


def _summary_line(r: dict) -> str:
    where = r["container"] + ("::" + r["member"] if r.get("member") else "")
    facts = r.get("config_facts") or {}
    core = facts.get("model.core_method") or "?"
    strict = r.get("strict_load", r.get("child_status", "?"))
    extra = ""
    if strict == "failed":
        extra = " missing={} unexpected={} shape={}".format(
            r.get("missing_keys", {}).get("count"),
            r.get("unexpected_keys", {}).get("count"),
            r.get("shape_mismatches", {}).get("count"),
        )
    return f"{r['index']:>3} {strict:<8} {r.get('model_status', '?'):<16} {core:<30} {where}{extra}"


PRINTED_KEYS = 50


def _detail_lines(r: dict, indent: str = "      ") -> list:
    """The per-checkpoint detail for the job log: errors, then the key lists."""
    lines = []
    for field in (
        "config_error",
        "config_relaxed_error",
        "model_error",
        "reader_error",
        "strict_error",
        "child_status",
        "child_error",
        "driver_error",
    ):
        if r.get(field):
            lines.append(f"{indent}{field}: {str(r[field])[:300]}")
    for field in ("missing_keys", "unexpected_keys"):
        listed = r.get(field) or {}
        if listed.get("count"):
            keys = listed.get("keys", [])
            lines.append(f"{indent}{field} ({listed['count']}):")
            lines.extend(f"{indent}  {key}" for key in keys[:PRINTED_KEYS])
            if listed["count"] > PRINTED_KEYS:
                lines.append(f"{indent}  ... {listed['count'] - PRINTED_KEYS} more in the report")
    shapes = r.get("shape_mismatches") or {}
    if shapes.get("count"):
        lines.append(f"{indent}shape_mismatches ({shapes['count']}):")
        for item in shapes.get("items", [])[:PRINTED_KEYS]:
            lines.append(
                f"{indent}  {item['key']}: model {item['model']} checkpoint {item['checkpoint']}"
            )
    return lines


def main(argv=None) -> int:  # noqa: ANN001
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--root", help="checkpoint root, read only")
    parser.add_argument(
        "--include",
        action="append",
        default=[],
        help="a checkpoint folder or file, relative to --root (repeat; nothing else is read)",
    )
    parser.add_argument("--max-zip-bytes", type=int, default=DEFAULT_MAX_ZIP_BYTES)
    parser.add_argument("--stage", required=True, help="node-local scratch for copies")
    parser.add_argument("--out", help="the JSON report to write")
    parser.add_argument("--repo", required=True, help="the coperceive checkout (plugin reader)")
    parser.add_argument("--primary-src", default="", help="the primary codebase checkout")
    parser.add_argument("--package", default="opencood")
    parser.add_argument("--expect-count", type=int, default=0)
    parser.add_argument("--codebase", action="append", default=[], help="NAME=SRC_ROOT:PACKAGE")
    parser.add_argument("--relaxed-ordered-dict", action="store_true")
    parser.add_argument("--child", help=argparse.SUPPRESS)
    parser.add_argument("--expect-src", default="", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    args.stage = os.path.abspath(args.stage)
    args.repo = os.path.abspath(args.repo)
    if args.child:
        return child_main(args)
    if not args.root or not args.out or not args.include:
        parser.error("--root, --out and at least one --include are required")
    root = os.path.realpath(args.root)
    for what, path in (("--stage", args.stage), ("--out", args.out)):
        if _inside(path, root):
            parser.error(
                f"{what} {path} is inside the checkpoint root {root}; refusing to write there"
            )
    codebases = _parse_codebases(args.codebase)

    started = time.time()
    report = {
        "schema": REPORT_SCHEMA,
        "started_unix": started,
        "root": root,
        "note": (
            "Weights read by a tensor-only unpickler (no pickle code runs); configs by the "
            "plugin's safe reader. No dataset opened, no forward pass, nothing written under root."
        ),
    }
    pkg = importlib.import_module(args.package)
    pkg_file = os.path.realpath(getattr(pkg, "__file__", "") or "")
    if args.primary_src and not _inside(pkg_file, args.primary_src):
        print(
            f"zoo_strict_load: {args.package} imports from {pkg_file}, not from "
            f"--primary-src {args.primary_src}; the commit check would not cover it",
            file=sys.stderr,
        )
        return 3
    report["environment"] = _environment(args.repo, args.primary_src)
    report["environment"]["primary_package_file"] = pkg_file

    entries = discover(root, args.include, args.max_zip_bytes)
    report["includes"] = list(args.include)
    report["discovery_errors"] = [e for e in entries if "error" in e]
    report["discovery_skipped"] = [e for e in entries if "skipped" in e]
    entries = [e for e in entries if "error" not in e and "skipped" not in e]
    report["discovered"] = len(entries)
    report["expected"] = args.expect_count or None
    for item in report["discovery_errors"] + report["discovery_skipped"]:
        print(f"discovery: {item['container']}: {item.get('error') or item.get('skipped')}")

    def write_report(complete: bool) -> None:
        report["complete"] = complete
        report["checkpoints"] = results
        report["strict_load_counts"] = dict(
            collections.Counter(r.get("strict_load", "?") for r in results)
        )
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        tmp = args.out + ".tmp"
        with open(tmp, "w") as handle:
            json.dump(report, handle, indent=2, sort_keys=False, default=str)
        os.replace(tmp, args.out)

    results: list = []
    staged_hashes: dict = {}
    for index, entry in enumerate(entries, start=1):
        entry["index"] = index
        container = entry["container"]
        if container not in staged_hashes:
            source = os.path.join(root, container)
            staged_hashes[container] = stage_copy(
                source, os.path.join(args.stage, "files", container)
            )
            if not entry["member"] and entry["config"]:
                cfg_rel = os.path.join(os.path.dirname(container), entry["config"])
                stage_copy(os.path.join(root, cfg_rel), os.path.join(args.stage, "files", cfg_rel))
        entry["container_hash"] = staged_hashes[container]

    write_report(False)
    for entry in entries:
        t0 = time.time()
        try:
            result = check_one(
                entry, args.stage, args.repo, args.package, args.relaxed_ordered_dict
            )
        except Exception as exc:  # noqa: BLE001
            result = dict(
                entry,
                strict_load="driver error",
                driver_error=_short_error(exc),
                driver_traceback=_tail(),
            )
        core = (result.get("config_facts") or {}).get("model.core_method") or ""
        not_here = result.get("model_status") == "module not found"
        if not core and result.get("core_hint"):
            # This codebase could not read the config; a codebase that has the model may.
            core, not_here = result["core_hint"], True
        if not_here and core:
            others = []
            for cb in codebases:
                module_file = os.path.join(
                    cb["src"], *cb["package"].split("."), "models", core + ".py"
                )
                if os.path.isfile(module_file):
                    others.append(_run_child(args, entry, cb))
                else:
                    others.append(
                        {
                            "codebase": cb["name"],
                            "child_status": f"no {cb['package']}/models/{core}.py",
                        }
                    )
            result["other_codebases"] = others
        result["seconds"] = round(time.time() - t0, 2)
        results.append(result)
        print(_summary_line(result), flush=True)
        for line in _detail_lines(result):
            print(line, flush=True)
        for other in result.get("other_codebases", []):
            label = other.get("strict_load", other.get("child_status"))
            print(f"      in {other.get('codebase')}: {label}", flush=True)
            for line in _detail_lines(other, indent="        "):
                print(line, flush=True)
        write_report(False)  # a job stopped at its time limit still leaves the rows so far

    report["finished_unix"] = time.time()
    report["seconds"] = round(report["finished_unix"] - started, 1)
    write_report(True)
    counts = report["strict_load_counts"]
    print(f"report: {args.out}", flush=True)
    print(
        f"discovered {report['discovered']} weights files (expected {report['expected']})",
        flush=True,
    )
    print("strict load: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())), flush=True)
    if args.expect_count and report["discovered"] != args.expect_count:
        print("WARNING: discovered count differs from --expect-count", flush=True)
    shutil.rmtree(os.path.join(args.stage, "work"), ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
