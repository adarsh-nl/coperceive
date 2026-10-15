# SPDX-License-Identifier: Apache-2.0
"""Cooperative multi-agent perception data library.

The distribution name ``pkg`` is a placeholder until a name is chosen.
"""

from pkg.errors import (
    ExtrapolationError,
    GapError,
    PkgError,
    SchemaVersionError,
    TimeQueryError,
    ToleranceError,
)
from pkg.model import (
    SCHEMA_VERSION,
    Agent,
    Annotation,
    Clock,
    ImageRef,
    PointCloud,
    Scene,
    Sensor,
    SharedObject,
)
from pkg.query import TimeHit, TimeMode, query_time
from pkg.se3 import SE3
from pkg.transform import T_dst_from_src, lookup_pose

__version__ = "0.0.0"

__all__ = [
    "SCHEMA_VERSION",
    "Agent",
    "Annotation",
    "Clock",
    "ExtrapolationError",
    "GapError",
    "ImageRef",
    "PkgError",
    "PointCloud",
    "SE3",
    "Scene",
    "SchemaVersionError",
    "Sensor",
    "SharedObject",
    "T_dst_from_src",
    "TimeHit",
    "TimeMode",
    "ToleranceError",
    "TimeQueryError",
    "__version__",
    "lookup_pose",
    "query_time",
]
