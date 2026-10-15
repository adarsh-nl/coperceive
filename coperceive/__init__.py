# SPDX-License-Identifier: Apache-2.0
"""Cooperative multi-agent perception data library."""

from coperceive.errors import (
    CoperceiveError,
    ExtrapolationError,
    GapError,
    SchemaVersionError,
    TimeQueryError,
    ToleranceError,
)
from coperceive.model import (
    SCHEMA_VERSION,
    Agent,
    AgentType,
    Annotation,
    BoxFrame,
    Clock,
    ImageRef,
    Modality,
    PointCloud,
    Scene,
    Sensor,
    SharedObject,
)
from coperceive.query import AgentTimeHit, TimeHit, TimeMode, query_agents, query_time
from coperceive.se3 import SE3
from coperceive.store import read_scene, write_scene
from coperceive.transform import (
    Frame,
    T_dst_from_src,
    body_frame,
    lookup_pose,
    sensor_frame,
    transform,
    world_frame,
)

__version__ = "0.0.0"

__all__ = [
    "SCHEMA_VERSION",
    "Agent",
    "AgentTimeHit",
    "AgentType",
    "Annotation",
    "BoxFrame",
    "Clock",
    "ExtrapolationError",
    "Frame",
    "GapError",
    "ImageRef",
    "CoperceiveError",
    "Modality",
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
    "body_frame",
    "lookup_pose",
    "query_agents",
    "query_time",
    "read_scene",
    "sensor_frame",
    "transform",
    "world_frame",
    "write_scene",
]
