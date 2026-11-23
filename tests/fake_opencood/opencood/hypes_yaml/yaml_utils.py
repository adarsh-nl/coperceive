# pyright: basic
# This package doubles for an untyped third-party dependency in tests (the real OpenCOOD
# has no annotations either); strict-mode Unknown-type diagnostics do not apply here.
# SPDX-License-Identifier: Apache-2.0
"""``opencood.hypes_yaml.yaml_utils`` as the plugin's engine calls it, with upstream's hazards.

Upstream ``load_yaml`` parses with PyYAML's full ``yaml.Loader`` (any ``!!python/object/apply:``
tag imports and calls what it names), adds a float resolver so ``1e-3`` is a float, and then
runs ``eval(param["yaml_parser"])(param)`` in this module's namespace. This double does the
same three things (on a ``yaml.Loader`` subclass, so the test process's ``yaml.Loader`` is not
mutated), so a test of the plugin's config reader fails if the plugin hands an archive's
config to it unguarded.

``load_point_pillar_params`` reproduces what upstream's parser leaves in the config (written
here, not copied): ``model.args.point_pillar_scatter.grid_size`` as an **int64 ndarray** and
the anchor ``vw/vh/vd`` and ``W/H/D``. A training run's ``yaml.dump`` of that config is what
puts numpy tags into a genuine checkpoint ``config.yaml``.
"""

from __future__ import annotations

import math
import os
import re

import numpy as np
import yaml

# The implicit float resolver upstream registers (YAML 1.1 needs a dot; OpenCOOD's configs
# write learning rates like 1e-3).
_FLOAT = re.compile(
    r"""^(?:
     [-+]?(?:[0-9][0-9_]*)\.[0-9_]*(?:[eE][-+]?[0-9]+)?
    |[-+]?(?:[0-9][0-9_]*)(?:[eE][-+]?[0-9]+)
    |\.[0-9_]+(?:[eE][-+][0-9]+)?
    |[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\.[0-9_]*
    |[-+]?\.(?:inf|Inf|INF)
    |\.(?:nan|NaN|NAN))$""",
    re.X,
)


class _UpstreamLoader(yaml.Loader):
    """PyYAML's full (code-running) loader, as upstream uses it."""


_UpstreamLoader.add_implicit_resolver("tag:yaml.org,2002:float", _FLOAT, list("-+0123456789."))


def load_yaml(file, opt=None):  # noqa: ANN001, ANN201 - the real signature is untyped
    """Load a config YAML the way upstream does: full loader, then ``eval(yaml_parser)``."""
    path = file
    model_dir = getattr(opt, "model_dir", None) if opt is not None else None
    if model_dir:
        path = os.path.join(model_dir, "config.yaml")
    with open(path, encoding="utf-8") as handle:
        param = yaml.load(handle, Loader=_UpstreamLoader)  # noqa: S506 - mirrors upstream
    if "yaml_parser" in param:
        param = eval(param["yaml_parser"])(param)  # noqa: S307 - mirrors upstream
    return param


def load_point_pillar_params(param):  # noqa: ANN001, ANN201 - upstream's parser signature
    """Fill ``grid_size`` (int64 ndarray) and the anchor sizes, as upstream's parser does."""
    lidar_range = param["preprocess"]["cav_lidar_range"]
    voxel = param["preprocess"]["args"]["voxel_size"]
    lower = np.array(lidar_range[0:3], dtype=np.float64)
    upper = np.array(lidar_range[3:6], dtype=np.float64)
    grid = np.round((upper - lower) / np.array(voxel, dtype=np.float64)).astype(np.int64)
    param["model"]["args"]["point_pillar_scatter"]["grid_size"] = grid
    anchors = param["postprocess"]["anchor_args"]
    anchors["vw"], anchors["vh"], anchors["vd"] = voxel[0], voxel[1], voxel[2]
    anchors["W"] = math.ceil((lidar_range[3] - lidar_range[0]) / voxel[0])
    anchors["H"] = math.ceil((lidar_range[4] - lidar_range[1]) / voxel[1])
    anchors["D"] = math.ceil((lidar_range[5] - lidar_range[2]) / voxel[2])
    return param
