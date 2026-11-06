# SPDX-License-Identifier: Apache-2.0
"""``opencood.data_utils.datasets`` stand-in with the dataset protocol the engine drives.

The engine instantiates the class named by the checkpoint config's ``fusion.core_method`` and
overrides only ``retrieve_base_data``. This fake keeps the same shape: the injected base data
goes through ``__getitem__``, ``collate_batch_test`` and ``post_process``, and
``post_process`` returns one fixed ego-frame prediction as OpenCOOD corner boxes (which is
what the real ``VoxelPostprocessor`` returns). The values are the chain test's contract:

* a box centred at CARLA-ego ``(8.0, 2.0, -1.5)`` with ``size_lwh`` ``(4.0, 2.0, 1.5)`` and
  heading 90 degrees, one car at score 0.9.

Reflected through the worker's conversion and the cooperative scene's identity-rotation ego
pose at 100_000 us (translation (10, 0, 0)), that becomes the schema-1 world box the chain test
asserts: centre ``(18.0, -2.0, -1.5)``, size ``(4.0, 2.0, 1.5)``, yaw -90 degrees.
"""

from __future__ import annotations

import math

COM_RANGE = 70

CENTER = (8.0, 2.0, -1.5)
SIZE_LWH = (4.0, 2.0, 1.5)
HEADING_DEG = 90.0
SCORE = 0.9
BOX_ORDER = "hwl"


def _corners(order):  # noqa: ANN001, ANN201 - the fake config's postprocess.order
    """The eight corners ``boxes_to_corners_3d`` writes, for the fixed prediction."""
    axes = (0, 1, 2) if order == "lwh" else (2, 1, 0)
    halves = [SIZE_LWH[axes[0]] / 2.0, SIZE_LWH[axes[1]] / 2.0, SIZE_LWH[axes[2]] / 2.0]
    cy, sy = math.cos(math.radians(HEADING_DEG)), math.sin(math.radians(HEADING_DEG))
    signs = (
        (1.0, -1.0, -1.0),
        (1.0, 1.0, -1.0),
        (-1.0, 1.0, -1.0),
        (-1.0, -1.0, -1.0),
        (1.0, -1.0, 1.0),
        (1.0, 1.0, 1.0),
        (-1.0, 1.0, 1.0),
        (-1.0, -1.0, 1.0),
    )
    out = []
    for sx, sy_, sz in signs:
        lx, ly, lz = sx * halves[0], sy_ * halves[1], sz * halves[2]
        out.append(
            [
                CENTER[0] + cy * lx - sy * ly,
                CENTER[1] + sy * lx + cy * ly,
                CENTER[2] + lz,
            ]
        )
    return out


class IntermediateFusionDataset:
    """Stand-in for OpenCOOD's intermediate-fusion dataset (same method names and shapes)."""

    def __init__(self, params, visualize, train=True):  # noqa: ANN001
        self.params = params
        self.visualize = visualize
        self.train = train
        train_params = params.get("train_params") if isinstance(params, dict) else None
        self.max_cav = int((train_params or {}).get("max_cav", 7))

    def __getitem__(self, idx):  # noqa: ANN001, ANN201
        base_data = self.retrieve_base_data(idx)
        return {"ego": {"base_data": base_data, "cav_num": len(base_data)}}

    def retrieve_base_data(self, idx, cur_ego_pose_flag=True):  # noqa: ANN001, ANN201
        raise NotImplementedError("the engine injects the frame")

    def collate_batch_test(self, batch):  # noqa: ANN001, ANN201
        item = batch[0]["ego"]
        return {
            "ego": {
                "base_data": item["base_data"],
                "cav_num": item["cav_num"],
                "anchor_box": None,
                "transformation_matrix": [
                    [1.0, 0.0, 0.0, 0.0],
                    [0.0, 1.0, 0.0, 0.0],
                    [0.0, 0.0, 1.0, 0.0],
                    [0.0, 0.0, 0.0, 1.0],
                ],
            }
        }

    def post_process(self, data_dict, output_dict):  # noqa: ANN001, ANN201
        order = BOX_ORDER
        postprocess = self.params.get("postprocess") if isinstance(self.params, dict) else None
        if isinstance(postprocess, dict) and "order" in postprocess:
            order = str(postprocess["order"])
        return ([_corners(order)], [SCORE], None)


class EarlyFusionDataset(IntermediateFusionDataset):
    """The early-fusion dataset has the same surface for this seam."""
