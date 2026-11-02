# SPDX-License-Identifier: Apache-2.0
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportArgumentType=false
"""A YAML safe loader that also reads the numpy scalars OPV2V/V2XSet records hold.

Real OpenCOOD per-frame YAMLs were written with PyYAML's full dumper, so some
values are ``!!python/object/apply:numpy.core.multiarray.scalar`` (a dtype and
the value's raw bytes) and, in places, ``numpy.core.multiarray._reconstruct``
(a small array). These files come from datasets we do not control, so this
loader never imports or calls anything named in a tag. It adds constructors for
exactly those numpy tags, rebuilds the value with ``numpy.frombuffer`` from the
numeric dtype descriptor alone, and returns plain Python numbers and lists.
Every other ``python/*`` tag is refused, as ``yaml.SafeLoader`` refuses it.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

_BASE: type[yaml.SafeLoader] = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
_APPLY = "tag:yaml.org,2002:python/object/apply:"
_TUPLE = "tag:yaml.org,2002:python/tuple"
# numpy 1.x writes numpy.core, numpy 2.x writes numpy._core.
_MODULES = ("numpy.core.multiarray", "numpy._core.multiarray")
_NUMERIC_KINDS = frozenset("biufc")


class NumpyYamlError(yaml.constructor.ConstructorError):
    """A numpy-tagged value this loader will not rebuild."""


class DatasetYamlLoader(_BASE):  # type: ignore[misc, valid-type]
    """``SafeLoader`` plus the numpy scalar, dtype and array tags. Nothing else."""


def _fail(node: Node, reason: str) -> NumpyYamlError:
    return NumpyYamlError(None, None, reason, node.start_mark)


def _items(loader: yaml.SafeLoader, node: Node) -> list[Any]:
    """Children of a sequence (``python/tuple`` allowed only here), built safely."""
    if not isinstance(node, SequenceNode):
        raise _fail(node, "expected a sequence")
    out: list[Any] = []
    for child in node.value:
        if child.tag == _TUPLE:
            out.append(tuple(_items(loader, child)))
        else:
            out.append(loader.construct_object(child, deep=True))
    return out


def _fields(node: Node) -> dict[str, Node]:
    if not isinstance(node, MappingNode):
        raise _fail(node, "expected a mapping")
    out: dict[str, Node] = {}
    for key, value in node.value:
        if not isinstance(key, ScalarNode):
            raise _fail(key, "expected a plain key")
        out[str(key.value)] = value
    return out


def _dtype(loader: yaml.SafeLoader, node: Node) -> np.dtype[Any]:
    """``numpy.dtype`` from its descriptor (``f8``) and byte order only."""
    fields = _fields(node)
    if "args" not in fields:
        raise _fail(node, "numpy.dtype without args")
    args = _items(loader, fields["args"])
    if not args or not isinstance(args[0], str):
        raise _fail(node, "numpy.dtype without a descriptor")
    order = "="
    if "state" in fields:
        state = _items(loader, fields["state"])
        if len(state) > 1 and state[1] in ("<", ">", "|", "="):
            order = str(state[1])
    descriptor = args[0]
    if not descriptor or not descriptor[0].isalpha() or not descriptor.isalnum():
        raise _fail(node, f"numpy dtype {descriptor!r} is not a plain numeric type")
    try:
        dtype = np.dtype(descriptor)
    except TypeError:
        raise _fail(node, f"numpy dtype {descriptor!r} is not a plain numeric type") from None
    if dtype.kind not in _NUMERIC_KINDS:
        raise _fail(node, f"numpy dtype {descriptor!r} is not a plain numeric type")
    return dtype.newbyteorder(order) if order in "<>" else dtype


def _construct_dtype(loader: yaml.SafeLoader, node: Node) -> np.dtype[Any]:
    return _dtype(loader, node)


def _dtype_of(loader: yaml.SafeLoader, node: Node) -> np.dtype[Any]:
    value = loader.construct_object(node, deep=True)
    if not isinstance(value, np.dtype):
        raise _fail(node, "expected a numpy dtype")
    return value


def _construct_scalar(loader: yaml.SafeLoader, node: Node) -> object:
    """``scalar(dtype, bytes)`` -> a Python bool/int/float/complex."""
    args = node
    if isinstance(node, MappingNode):
        fields = _fields(node)
        if "args" not in fields:
            raise _fail(node, "numpy scalar without args")
        args = fields["args"]
    if not isinstance(args, SequenceNode) or len(args.value) != 2:
        raise _fail(node, "numpy scalar needs a dtype and its bytes")
    dtype = _dtype_of(loader, args.value[0])
    raw = loader.construct_object(args.value[1], deep=True)
    if not isinstance(raw, bytes) or len(raw) != dtype.itemsize:
        raise _fail(node, "numpy scalar bytes do not match its dtype")
    return np.frombuffer(raw, dtype=dtype, count=1)[0].item()


def _construct_reconstruct(loader: yaml.SafeLoader, node: Node) -> object:
    """``_reconstruct`` -> nested lists, from ``state = (v, shape, dtype, fortran, bytes)``.

    The ``args`` (``!!python/name:numpy.ndarray`` …) are never constructed.
    """
    fields = _fields(node)
    if "state" not in fields or not isinstance(fields["state"], SequenceNode):
        raise _fail(node, "numpy array without state")
    state = fields["state"].value
    if len(state) != 5:
        raise _fail(node, "numpy array state has an unexpected shape")
    shape_node = state[1]
    shape = _items(loader, shape_node) if isinstance(shape_node, SequenceNode) else []
    if not all(isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in shape):
        raise _fail(node, "numpy array shape is not a list of sizes")
    dtype = _dtype_of(loader, state[2])
    fortran = loader.construct_object(state[3], deep=True)
    raw = loader.construct_object(state[4], deep=True)
    count = int(np.prod(shape)) if shape else 1
    if not isinstance(raw, bytes) or len(raw) != count * dtype.itemsize:
        raise _fail(node, "numpy array bytes do not match its shape and dtype")
    array = np.frombuffer(raw, dtype=dtype, count=count)
    array = array.reshape(shape, order="F" if fortran is True else "C")
    return array.tolist()


for _module in _MODULES:
    DatasetYamlLoader.add_constructor(f"{_APPLY}{_module}.scalar", _construct_scalar)
    DatasetYamlLoader.add_constructor(f"{_APPLY}{_module}._reconstruct", _construct_reconstruct)
DatasetYamlLoader.add_constructor(f"{_APPLY}numpy.dtype", _construct_dtype)


def load_dataset_yaml(text: str) -> object:
    """Parse one dataset YAML document with :class:`DatasetYamlLoader`."""
    return yaml.load(text, Loader=DatasetYamlLoader)  # noqa: S506 - a SafeLoader subclass
