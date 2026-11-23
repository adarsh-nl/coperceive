# SPDX-License-Identifier: Apache-2.0
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportMissingTypeArgument=false
"""Read a checkpoint archive's ``config.yaml`` without running anything the file names.

OpenCOOD's own ``opencood.hypes_yaml.yaml_utils.load_yaml`` parses with PyYAML's full
``yaml.Loader``, which builds a ``!!python/object/apply:`` tag by importing and calling what
it names, and then runs ``eval(param["yaml_parser"])(param)``. The archive is a user-supplied
file, so handing its config to that loader would run code from it before, and regardless of,
the ``trust_checkpoint_pickle`` gate on the weights. This module is the default reader:

- ``yaml.SafeLoader`` plus the implicit float resolver upstream's ``load_yaml`` adds (so
  ``1e-3`` is a float there and here), on a subclass: ``yaml.SafeLoader`` is not modified;
- the tags a genuine training config carries, rebuilt as data. Upstream
  ``train_utils.setup_train`` writes ``config.yaml`` with ``yaml.dump(hypes)`` *after*
  ``load_yaml`` applied ``yaml_parser``, and ``load_point_pillar_params`` /
  ``load_second_params`` leave ``grid_size`` as an int64 ndarray (``load_bev_params`` leaves
  tuples). The dump therefore holds ``!!python/tuple`` and numpy's ``_reconstruct``/``scalar``
  /``dtype`` reduce tags (``numpy.core.multiarray`` from numpy 1.x, ``numpy._core.multiarray``
  from 2.x). They come back as tuples, ndarrays, numpy scalars and dtypes, as upstream builds
  them, but from ``numpy.frombuffer`` over a plain numeric dtype: nothing named in a tag is
  imported or called, and the ``!!python/name:numpy.ndarray`` in ``_reconstruct``'s args is
  checked, never constructed;
- ``yaml_parser`` is applied only when it is a bare ``load_<name>_params`` identifier, looked
  up with ``getattr`` on the installed ``yaml_utils`` module (the user's OpenCOOD code), never
  ``eval``'d.

Anything else, any other ``python/`` tag or a ``yaml_parser`` expression, raises
:class:`ConfigRefused`; the engine turns that into an error naming ``trust_checkpoint_pickle``,
the one option under which upstream's own loader may parse the file.

Standard library, PyYAML and numpy only, all present in an OpenCOOD environment.
"""

from __future__ import annotations

import re
from typing import Any

import numpy as np
import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

_PYTHON = "tag:yaml.org,2002:python/"
_APPLY = _PYTHON + "object/apply:"
_TUPLE = _PYTHON + "tuple"
_NDARRAY_NAME = _PYTHON + "name:numpy.ndarray"
# numpy 1.x pickles (and so yaml.dump) name numpy.core.multiarray, numpy 2.x numpy._core.
_NUMPY_MODULES = ("numpy.core.multiarray", "numpy._core.multiarray")
_NUMERIC_KINDS = frozenset("biufc")
_BYTE_ORDERS = ("<", ">", "|", "=")
# Upstream's parsers are load_voxel_params, load_point_pillar_params, load_second_params and
# load_bev_params; forks add more under the same naming (load_lift_splat_shoot_params, ...).
_PARSER_NAME = re.compile(r"load_[A-Za-z0-9_]+_params")
# The float resolver upstream's load_yaml registers, verbatim in meaning: YAML 1.1 needs a dot,
# OpenCOOD's hand-written configs write learning rates as 1e-3.
_OPENCOOD_FLOAT = re.compile(
    r"""^(?:
     [-+]?(?:[0-9][0-9_]*)\.[0-9_]*(?:[eE][-+]?[0-9]+)?
    |[-+]?(?:[0-9][0-9_]*)(?:[eE][-+]?[0-9]+)
    |\.[0-9_]+(?:[eE][-+][0-9]+)?
    |[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\.[0-9_]*
    |[-+]?\.(?:inf|Inf|INF)
    |\.(?:nan|NaN|NAN))$""",
    re.X,
)


class ConfigError(ValueError):
    """The config cannot be read (for example it names a parser this OpenCOOD lacks)."""


class ConfigRefused(ConfigError):
    """The config needs something only upstream's code-running loader would build."""


class CheckpointConfigLoader(yaml.SafeLoader):
    """``SafeLoader`` + OpenCOOD's float resolver + the tuple/numpy tags of a dumped config."""


def _refused(node: Node, problem: str) -> ConfigRefused:
    mark = node.start_mark
    where = f" (line {mark.line + 1})" if mark is not None else ""
    return ConfigRefused(problem + where)


def _short(tag: str) -> str:
    return "!!" + tag[len("tag:yaml.org,2002:") :] if tag.startswith("tag:yaml.org,2002:") else tag


def _construct_undefined(loader: CheckpointConfigLoader, node: Node) -> object:
    if str(node.tag).startswith(_PYTHON):
        raise _refused(
            node,
            f"holds the tag {_short(str(node.tag))}, which only PyYAML's full loader builds, by "
            "importing and calling what it names; this plugin reads only the tuple and numpy "
            "tags a training run writes",
        )
    return yaml.SafeLoader.construct_undefined(loader, node)


def _construct_tuple(loader: CheckpointConfigLoader, node: Node) -> tuple[object, ...]:
    if not isinstance(node, SequenceNode):
        raise _refused(node, "holds a !!python/tuple that is not a sequence")
    return tuple(loader.construct_sequence(node, deep=True))


def _fields(node: Node, allowed: tuple[str, ...], what: str) -> dict[str, Node]:
    """The ``args``/``state`` keys of a reduce tag's mapping form; anything else is refused."""
    if not isinstance(node, MappingNode):
        raise _refused(node, f"holds a {what} that is not a mapping")
    fields: dict[str, Node] = {}
    for key, value in node.value:
        if not isinstance(key, ScalarNode) or key.value not in allowed:
            raise _refused(key, f"holds a {what} with a key other than {', '.join(allowed)}")
        fields[str(key.value)] = value
    return fields


def _items(node: Node | None, owner: Node, what: str) -> list[Node]:
    """The children of a sequence ``node`` (missing or not a sequence: refused at ``owner``)."""
    if not isinstance(node, SequenceNode):
        raise _refused(node or owner, f"holds a {what} that is not a sequence")
    return list(node.value)


def _construct_dtype(loader: CheckpointConfigLoader, node: Node) -> np.dtype:
    """``numpy.dtype(descriptor)`` with the pickled byte order, for plain numeric types only."""
    fields = _fields(node, ("args", "state"), "numpy dtype")
    args = [
        loader.construct_object(item, deep=True)
        for item in _items(fields.get("args"), node, "numpy dtype")
    ]
    if not args or not isinstance(args[0], str):
        raise _refused(node, "holds a numpy dtype without a descriptor")
    descriptor = args[0]
    if not (descriptor[:1].isalpha() and descriptor.isalnum()):
        raise _refused(node, f"holds the numpy dtype {descriptor!r}, not a plain numeric type")
    try:
        dtype = np.dtype(descriptor)
    except TypeError:
        raise _refused(
            node, f"holds the numpy dtype {descriptor!r}, not a plain numeric type"
        ) from None
    if dtype.kind not in _NUMERIC_KINDS:
        raise _refused(node, f"holds the numpy dtype {descriptor!r}, not a plain numeric type")
    if "state" in fields:
        state = [
            loader.construct_object(item, deep=True)
            for item in _items(fields["state"], node, "numpy dtype state")
        ]
        if len(state) > 1 and state[1] in ("<", ">"):
            dtype = dtype.newbyteorder("<" if state[1] == "<" else ">")
        elif len(state) > 1 and state[1] not in _BYTE_ORDERS:
            raise _refused(node, f"holds a numpy dtype with byte order {state[1]!r}")
    return dtype


def _dtype_of(loader: CheckpointConfigLoader, node: Node) -> np.dtype:
    value = loader.construct_object(node, deep=True)
    if not isinstance(value, np.dtype):
        raise _refused(node, "holds a numpy value whose dtype is not a numpy dtype")
    return value


def _construct_scalar(loader: CheckpointConfigLoader, node: Node) -> object:
    """``scalar(dtype, raw bytes)`` -> the numpy scalar upstream's loader would build."""
    args_node: Node | None = node
    if isinstance(node, MappingNode):
        args_node = _fields(node, ("args",), "numpy scalar").get("args")
    args = _items(args_node, node, "numpy scalar")
    if len(args) != 2:
        raise _refused(node, "holds a numpy scalar that is not (dtype, bytes)")
    dtype = _dtype_of(loader, args[0])
    raw = loader.construct_object(args[1], deep=True)
    if not isinstance(raw, bytes) or len(raw) != dtype.itemsize:
        raise _refused(node, "holds a numpy scalar whose bytes do not match its dtype")
    return np.frombuffer(raw, dtype=dtype, count=1)[0]


def _construct_ndarray(loader: CheckpointConfigLoader, node: Node) -> np.ndarray:
    """``_reconstruct(ndarray, (0,), b'b')`` + ``state`` -> a writable ndarray.

    ``state`` is ``(version, shape, dtype, is_fortran, raw bytes)``. The args are checked to
    be exactly what ``ndarray.__reduce__`` writes; the ``ndarray`` name in them is never
    resolved.
    """
    fields = _fields(node, ("args", "state"), "numpy array")
    args = _items(fields.get("args"), node, "numpy array")
    if len(args) != 3 or args[0].tag != _NDARRAY_NAME:
        raise _refused(node, "holds a numpy _reconstruct that does not rebuild a numpy.ndarray")
    base_shape = loader.construct_object(args[1], deep=True)
    typecode = loader.construct_object(args[2], deep=True)
    if not isinstance(base_shape, tuple) or not isinstance(typecode, bytes):
        raise _refused(node, "holds a numpy _reconstruct with unexpected arguments")
    state = _items(fields.get("state"), node, "numpy array state")
    if len(state) != 5:
        raise _refused(
            node, "holds a numpy array state that is not (version, shape, dtype, order, bytes)"
        )
    shape = loader.construct_object(state[1], deep=True)
    if not isinstance(shape, tuple) or not all(
        isinstance(size, int) and not isinstance(size, bool) and size >= 0 for size in shape
    ):
        raise _refused(node, "holds a numpy array whose shape is not a tuple of sizes")
    dtype = _dtype_of(loader, state[2])
    fortran = loader.construct_object(state[3], deep=True)
    raw = loader.construct_object(state[4], deep=True)
    count = 1
    for size in shape:
        count *= int(size)
    if not isinstance(fortran, bool) or not isinstance(raw, bytes):
        raise _refused(node, "holds a numpy array state with unexpected values")
    if len(raw) != count * dtype.itemsize:
        raise _refused(node, "holds a numpy array whose bytes do not match its shape and dtype")
    order = "F" if fortran else "C"
    # numpy's own unpickling (ndarray.__setstate__) byte-swaps to native order; so does this.
    native = dtype.newbyteorder("=")
    if count == 0:
        return np.zeros(shape, dtype=native, order=order)
    flat = np.frombuffer(raw, dtype=dtype, count=count)
    return flat.reshape(shape, order=order).astype(native, order="K")


# Own copies of the resolver lists first: PyYAML before 5.1 shallow-copied them in
# add_implicit_resolver, which would have added this resolver to yaml.SafeLoader too.
CheckpointConfigLoader.yaml_implicit_resolvers = {
    first: list(resolvers) for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
CheckpointConfigLoader.add_implicit_resolver(
    "tag:yaml.org,2002:float", _OPENCOOD_FLOAT, list("-+0123456789.")
)
CheckpointConfigLoader.add_constructor(None, _construct_undefined)
CheckpointConfigLoader.add_constructor(_TUPLE, _construct_tuple)
CheckpointConfigLoader.add_constructor(_APPLY + "numpy.dtype", _construct_dtype)
for _module in _NUMPY_MODULES:
    CheckpointConfigLoader.add_constructor(_APPLY + _module + ".scalar", _construct_scalar)
    CheckpointConfigLoader.add_constructor(_APPLY + _module + "._reconstruct", _construct_ndarray)


def apply_yaml_parser(param: dict[Any, Any], yaml_utils: object) -> object:
    """Upstream's ``eval(param["yaml_parser"])(param)``, restricted to a parser *name*."""
    value = param["yaml_parser"]
    name = value.strip() if isinstance(value, str) else ""
    if not _PARSER_NAME.fullmatch(name):
        raise ConfigRefused(
            f"names yaml_parser {value!r}, which is not the bare name of a "
            "load_<name>_params function of opencood.hypes_yaml.yaml_utils"
        )
    parser = getattr(yaml_utils, name, None)
    if not callable(parser):
        raise ConfigError(
            f"its yaml_parser {name!r} is not defined by the installed "
            "opencood.hypes_yaml.yaml_utils; is this the OpenCOOD checkout the checkpoint was "
            "trained with?"
        )
    return parser(param)


def load_checkpoint_config(path: str, yaml_utils: object) -> object:
    """Parse ``path`` with :class:`CheckpointConfigLoader` and apply its ``yaml_parser``.

    Raises :class:`ConfigRefused` for anything only upstream's loader would build, and
    ``yaml.YAMLError``/``OSError``/:class:`ConfigError` (or the parser's own error) otherwise.
    """
    with open(path, "rb") as handle:  # PyYAML detects UTF-8/UTF-16 itself
        param = yaml.load(handle, Loader=CheckpointConfigLoader)  # noqa: S506 - a SafeLoader
    if isinstance(param, dict) and "yaml_parser" in param:
        return apply_yaml_parser(param, yaml_utils)
    return param
