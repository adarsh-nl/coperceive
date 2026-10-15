# SPDX-License-Identifier: Apache-2.0
"""Shared HD maps for schema 1 scenes.

A map is one directory keyed by the CARLA map name (``Carla/Maps/Town03``
becomes ``Town03``). Vertices stay in the OpenDRIVE frame. ``world_from_opendrive``
is the explicit transform into the scene world. It is not a scene file and it
does not change schema 1.
"""

from coperceive.hdmap.align import align_maps
from coperceive.hdmap.opendrive import parse_opendrive
from coperceive.hdmap.store import (
    MAP_FORMAT,
    MAP_FORMAT_VERSION,
    HdMap,
    find_map,
    load_map,
    map_slug,
    scene_map_name,
    write_map,
)

__all__ = [
    "MAP_FORMAT",
    "MAP_FORMAT_VERSION",
    "HdMap",
    "align_maps",
    "find_map",
    "load_map",
    "map_slug",
    "parse_opendrive",
    "scene_map_name",
    "write_map",
]
