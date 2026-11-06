# pyright: basic
# This package doubles for an untyped third-party dependency in tests (the real OpenCOOD
# has no annotations either); strict-mode Unknown-type diagnostics do not apply here.
# SPDX-License-Identifier: Apache-2.0
"""``opencood.data_utils.datasets`` stand-in with the dataset protocol the engine drives.

The engine instantiates the class named by the checkpoint config's ``fusion.core_method`` and
overrides only ``retrieve_base_data``. This fake keeps the same shape *and the same input
types* as upstream: ``__getitem__`` consumes ``lidar_np`` with ``.shape`` and
``np.random.permutation`` and the 4×4 matrices as ndarrays (a list-shaped batch fails exactly
where upstream would), and ``post_process`` returns one fixed ego-frame prediction as corner
boxes, transcribed from upstream ``boxes_to_corners_3d``.

The box is the chain test's contract: ``[x, y, z, d0, d1, d2, yaw]`` =
``(8.0, 2.0, -1.5, l=4.0, w=2.0, h=1.5, 90°)``. Upstream reorders the 7-vector for a ``hwl``
config (``[:, [0, 1, 2, 5, 4, 3, 6]]``) and then always uses the l, w, h corner template.
Reflected through the worker's conversion and the cooperative scene's identity-rotation ego
pose at 100_000 us (translation (10, 0, 0)), that becomes the schema-1 world box the chain
test asserts: centre ``(18.0, -2.0, -1.5)``, size ``(4.0, 2.0, 1.5)``, yaw -90 degrees.
"""

from __future__ import annotations

import numpy as np

COM_RANGE = 70

CENTER = (8.0, 2.0, -1.5)
SIZE_LWH = (4.0, 2.0, 1.5)
HEADING_DEG = 90.0
SCORE = 0.9
BOX_ORDER = "hwl"

# Upstream opencood/utils/box_utils.py boxes_to_corners_3d template, for the l, w, h axes.
_TEMPLATE = (
    np.array(
        [
            [1.0, -1.0, -1.0],
            [1.0, 1.0, -1.0],
            [-1.0, 1.0, -1.0],
            [-1.0, -1.0, -1.0],
            [1.0, -1.0, 1.0],
            [1.0, 1.0, 1.0],
            [-1.0, 1.0, 1.0],
            [-1.0, -1.0, 1.0],
        ]
    )
    / 2.0
)


def _corners(order):
    """``boxes_to_corners_3d`` for one box, transcribed from upstream (incl. the hwl reorder).

    The model/post-processor 7-vector is in the config's order, so a ``hwl`` config publishes
    ``[x, y, z, h, w, l, yaw]``; upstream reorders that to ``[x, y, z, l, w, h, yaw]`` with
    ``[:, [0, 1, 2, 5, 4, 3, 6]]`` and then uses the l, w, h template.
    """
    height, width, length = SIZE_LWH[2], SIZE_LWH[1], SIZE_LWH[0]
    values = [CENTER[0], CENTER[1], CENTER[2], length, width, height, HEADING_DEG]
    if order == "hwl":
        values = [
            CENTER[0],
            CENTER[1],
            CENTER[2],
            height,
            width,
            length,
            HEADING_DEG,
        ]
    box = np.array([values], dtype=np.float64)
    if order == "hwl":
        box = box[:, [0, 1, 2, 5, 4, 3, 6]]
    corners = _TEMPLATE[None, :, :] * box[:, None, 3:6]  # (1, 8, 3)
    angle = np.deg2rad(box[:, 6])  # upstream headings are radians
    cos, sin = np.cos(angle), np.sin(angle)
    rotation = np.zeros((box.shape[0], 3, 3), dtype=np.float64)
    rotation[:, 0, 0] = cos
    rotation[:, 0, 1] = -sin
    rotation[:, 1, 0] = sin
    rotation[:, 1, 1] = cos
    rotation[:, 2, 2] = 1.0
    corners = np.einsum("nij,nkj->nki", rotation, corners)  # rotate each point about z
    corners = corners + box[:, None, 0:3]
    return corners[0].tolist()


class IntermediateFusionDataset:
    """Stand-in for OpenCOOD's intermediate-fusion dataset (same methods, shapes and types)."""

    def __init__(self, params, visualize, train=True):  # noqa: ANN001
        self.params = params
        self.visualize = visualize
        self.train = train
        train_params = params.get("train_params") if isinstance(params, dict) else None
        self.max_cav = int((train_params or {}).get("max_cav", 7))

    def __getitem__(self, idx):  # noqa: ANN001, ANN201
        base_data = self.retrieve_base_data(idx)
        processed = {}
        for cav_id, cav in base_data.items():
            # The first things the real dataset does per CAV: shuffle_points(lidar_np)
            # indexes by lidar_np.shape[0], and the reform_param matrices are ndarrays used in
            # project_points_by_matrix_torch/project_box3d. A list raises here, upstream too.
            lidar_np = cav["lidar_np"]
            shuffled = lidar_np[np.random.permutation(lidar_np.shape[0])]
            matrix = cav["params"]["transformation_matrix"]
            matrix.astype(np.float64)  # np.ndarray has astype; a Python list does not
            homogeneous = np.vstack(
                [shuffled[:, :3].T, np.ones((1, shuffled.shape[0]), dtype=np.float64)]
            )
            projected = matrix @ homogeneous
            correction = cav["params"]["spatial_correction_matrix"]
            _ = correction @ np.eye(4)  # ndarray @ ndarray; a Python list has no matmul
            processed[cav_id] = {
                "projected_lidar": projected,
                "lidar_pose": cav["params"]["lidar_pose"],
            }
        return {"ego": {"base_data": base_data, "processed": processed, "cav_num": len(base_data)}}

    def retrieve_base_data(self, idx, cur_ego_pose_flag=True):  # noqa: ANN001, ANN201
        raise NotImplementedError("the engine injects the frame")

    def collate_batch_test(self, batch):  # noqa: ANN001, ANN201
        item = batch[0]["ego"]
        return {
            "ego": {
                "base_data": item["base_data"],
                "processed": item["processed"],
                "cav_num": item["cav_num"],
                "anchor_box": None,
                "transformation_matrix": np.eye(4, dtype=np.float64),
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
