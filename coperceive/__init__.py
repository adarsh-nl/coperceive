# SPDX-License-Identifier: Apache-2.0
"""Cooperative multi-agent perception data library."""

from coperceive.assoc import (
    Association,
    AssociationKind,
    CoAnnotation,
    agent_annotations,
    associate,
    co_annotations,
    exclusive_annotations,
    shared_track_annotations,
)
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
from coperceive.project import ProjectedBox, ProjectedPoint, ProjectionStatus, project_box
from coperceive.query import AgentTimeHit, TimeHit, TimeMode, query_agents, query_time
from coperceive.sample import AgentSample, CooperativeSample
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

__version__ = "0.1.0"

__all__ = [
    "SCHEMA_VERSION",
    "Agent",
    "AgentSample",
    "AgentTimeHit",
    "AgentType",
    "Annotation",
    "Association",
    "AssociationKind",
    "BoxFrame",
    "Clock",
    "CoAnnotation",
    "CooperativeSample",
    "ExtrapolationError",
    "Frame",
    "GapError",
    "ImageRef",
    "CoperceiveError",
    "Modality",
    "PointCloud",
    "ProjectedBox",
    "ProjectedPoint",
    "ProjectionStatus",
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
    "agent_annotations",
    "associate",
    "body_frame",
    "co_annotations",
    "exclusive_annotations",
    "lookup_pose",
    "project_box",
    "query_agents",
    "query_time",
    "read_scene",
    "sensor_frame",
    "shared_track_annotations",
    "transform",
    "world_frame",
    "write_scene",
]
