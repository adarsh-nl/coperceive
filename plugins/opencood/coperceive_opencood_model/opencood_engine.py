# SPDX-License-Identifier: Apache-2.0
"""The thin OpenCOOD seam: load the checkpoint's own config, run its own inference pipeline.

Everything OpenCOOD-specific lives in this module, which is imported lazily by the worker and
exercised in CI through ``tests/fake_opencood`` (a stand-in package with the same small
surface). Nothing from OpenCOOD is vendored: the code calls its public entry points, as
``opencood/tools/inference.py`` does, and feeds them a dataset-backed pipeline whose raw data
we provide for the current frame:

- ``opencood.hypes_yaml.yaml_utils.load_yaml(config)`` parses the checkpoint's ``config.yaml``
  (including its ``yaml_parser``, e.g. ``load_point_pillar_params``);
- ``opencood.tools.train_utils.create_model(config)`` builds the model; the weights are read
  here with ``torch.load`` because a user-supplied checkpoint file has no fixed name;
- the dataset class named by ``config['fusion']['core_method']`` is instantiated with its own
  ``__init__`` (a real one) and only ``retrieve_base_data`` is overridden to return the CAV
  dicts ``batch.build_base_data`` built, so its existing preprocessing, collation and
  post-processing run unmodified;
- ``dataset.collate_batch_test`` / ``model(batch['ego'])`` / ``dataset.post_process`` are the
  same three calls OpenCOOD's own inference loop makes.

The injected CAV dicts are converted to the types the real dataset uses: ``lidar_np`` is a
float32 ``(n, 4)`` ndarray (as upstream ``pcd_utils.pcd_to_np`` returns) and the three 4×4
matrices are float64 ndarrays (as ``x_to_world``/``np.eye`` produce); lists fail the
dataset's first ``lidar_np.shape``/``shuffle_points`` call.

Priors sent with every frame are the CoBEVT/V2XSet-era defaults, because schema 1 stores no
speeds or asynchronous delays: ``ego_speed = 0.0``, ``time_delay = 0.0`` and an identity
``spatial_correction_matrix``. Configs that consume ``wild_setting``/spatial correction
(V2X-ViT/Where2comm style) are **not supported or tested**; CoBEVT does not use them.

Ground truth is never fetched: ``params['vehicles']`` is empty, so the label generation that
the dataset runs on a test item yields empty targets. ``COM_RANGE``, ``max_cav`` and the box
order come from the OpenCOOD environment and the checkpoint config, never from constants
here. What is *not* verified without the cluster: the archive's contents, the OpenCOOD commit
in the user's conda environment, and the numeric boxes. They are cluster checks in
``docs/models/opencood.md``.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import importlib.util
import os
import shutil
import tempfile

import numpy as np
from opencood_convert import ConvertError, boxes_from_opencood

SUPPORTED_FUSION = ("IntermediateFusionDataset", "EarlyFusionDataset")


class EngineError(RuntimeError):
    """OpenCOOD is missing or does not expose the API this seam drives, or the config is not one."""


def _module(name: str):
    try:
        return importlib.import_module(name)
    except Exception as exc:  # noqa: BLE001 - the user's environment reports its own errors
        raise EngineError(f"cannot import {name} from this interpreter: {exc}") from None


def _metadata_version(distribution: str) -> str:
    try:
        from importlib import metadata
    except ImportError:  # pragma: no cover - Python 3.7 has no importlib.metadata
        return ""
    try:
        return str(metadata.version(distribution))
    except Exception:  # noqa: BLE001 - not installed as a distribution
        return ""


def opencood_environment() -> dict[str, str]:
    """Cheap (import-free) OpenCOOD facts for ``hello.environment``: version and package path."""
    try:
        spec = importlib.util.find_spec("opencood")
    except Exception:  # noqa: BLE001 - a broken checkout is a fact to report, not a crash
        spec = None
    if spec is None:
        return {"opencood": "not-installed"}
    environment = {"opencood": _metadata_version("opencood") or "unknown-version"}
    origin = spec.origin or ""
    if origin and origin.endswith("__init__.py"):
        environment["opencood_path"] = os.path.dirname(origin)
    elif origin:
        environment["opencood_path"] = origin
    return environment


def torch_environment() -> dict[str, str]:
    """``torch`` version and GPU name for ``hello.environment``; absent/broken is reported."""
    try:
        import torch  # noqa: PLC0415 - the OpenCOOD environment provides it, lazily
    except Exception:  # noqa: BLE001 - absent or broken torch is a fact to report
        return {"torch": "absent"}
    environment = {"torch": str(torch.__version__)}
    try:
        if torch.cuda.is_available():
            environment["gpu"] = str(torch.cuda.get_device_name(0))
            environment["cuda"] = str(torch.version.cuda)
    except Exception:  # noqa: BLE001 - a broken CUDA stack is a fact to report
        pass
    return environment


def frame_seed(scene_id: str, time_us: int) -> int:
    """A stable per-frame seed: two passes over one frame shuffle points the same way.

    OpenCOOD's dataset calls ``shuffle_points`` before voxelisation, so two ``model check``
    passes over one scene would otherwise see different point order and could differ box for
    box. Seeding numpy/torch from ``(scene_id, time_us)`` makes a rerun of the same frame
    repeat the shuffle; CUDA kernel determinism is a separate, unverified question
    (docs/models/opencood.md).
    """
    material = f"{scene_id}|{int(time_us)}".encode()
    return int.from_bytes(hashlib.blake2s(material).digest()[:4], "little")


def _order_of(config: dict[str, object]) -> str:
    postprocess = config.get("postprocess")
    if not isinstance(postprocess, dict):
        raise EngineError("the checkpoint config has no postprocess section")
    order = postprocess.get("order")
    if order not in ("lwh", "hwl"):
        raise EngineError(f"the checkpoint config's postprocess.order is {order!r}, not lwh/hwl")
    return str(order)


def _fusion_of(config: dict[str, object]) -> str:
    fusion = config.get("fusion")
    if not isinstance(fusion, dict):
        raise EngineError("the checkpoint config has no fusion section")
    method = fusion.get("core_method")
    if method not in SUPPORTED_FUSION:
        raise EngineError(
            f"fusion {method!r} is not supported by this plugin yet; supported: "
            + ", ".join(SUPPORTED_FUSION)
        )
    return str(method)


def _load_weights(torch, path: str) -> dict[str, object]:
    """Load a state dict, preferring the safe unpickler and falling back to the old one."""
    try:
        state = torch.load(path, map_location="cpu", weights_only=True)
    except TypeError:  # torch older than 2.0 has no weights_only argument
        try:
            state = torch.load(path, map_location="cpu")
        except Exception as exc:  # noqa: BLE001 - a bad file is reported to the parent
            raise EngineError(f"cannot load weights {path}: {exc}") from None
    except Exception:
        # Checkpoints saved by an old torch often carry numpy scalars or other globals the
        # safe unpickler refuses; a user-supplied checkpoint is trusted either way.
        try:
            state = torch.load(path, map_location="cpu", weights_only=False)
        except Exception as exc:  # noqa: BLE001 - a bad file is reported to the parent
            raise EngineError(f"cannot load weights {path}: {exc}") from None
    if not isinstance(state, dict):
        raise EngineError(f"weights file {path} does not hold a state dict")
    for key in ("model", "state_dict", "model_state_dict", "net"):
        inner = state.get(key)
        if isinstance(inner, dict):
            state = inner
            break
    state = {str(key): value for key, value in state.items()}
    if state and all(key.startswith("module.") for key in state):
        state = {key[len("module.") :]: value for key, value in state.items()}
    if not state:
        raise EngineError(f"weights file {path} holds no parameters")
    return state


def _to_list(value: object) -> list:
    """A tensor/ndarray/sequence as plain Python lists (the protocol's format)."""
    if value is None:
        return []
    if hasattr(value, "detach"):
        value = value.detach().cpu().numpy()  # type: ignore[union-attr]
    if hasattr(value, "tolist"):
        return list(value.tolist())  # type: ignore[union-attr]
    return list(value)  # type: ignore[arg-type]


def dataset_frame(base_data: object) -> dict[str, object]:
    """The injected frame in the types the real OpenCOOD dataset consumes.

    ``lidar_np`` becomes a float32 ``(n, 4)`` ndarray (the dtype ``pcd_utils.pcd_to_np``
    returns) and ``transformation_matrix``/``gt_transformation_matrix``/
    ``spatial_correction_matrix`` become float64 ``(4, 4)`` ndarrays (what ``x_to_world`` and
    ``np.eye`` produce upstream). ``lidar_pose`` stays a 6-float list and ``vehicles`` a dict,
    as upstream keeps them. Converting here, once per frame, means a list-shaped batch fails
    with a clear EngineError instead of a TypeError deep inside the dataset.
    """
    if not isinstance(base_data, dict):
        raise EngineError(f"the injected frame must be a dict, got {type(base_data).__name__}")
    frame: dict[str, object] = {}
    for cav_id, cav in base_data.items():
        if not isinstance(cav, dict):
            raise EngineError(f"CAV {cav_id!r}: the CAV content must be a dict")
        params = cav.get("params")
        if not isinstance(params, dict):
            raise EngineError(f"CAV {cav_id!r}: params must be a dict")
        try:
            lidar = np.asarray(cav.get("lidar_np"), dtype=np.float32)
        except (TypeError, ValueError) as exc:
            raise EngineError(f"CAV {cav_id!r}: lidar_np is not a numeric array: {exc}") from None
        if lidar.ndim != 2 or lidar.shape[1] != 4:
            raise EngineError(
                f"CAV {cav_id!r}: lidar_np must be (n, 4) [x, y, z, intensity], "
                f"got shape {lidar.shape}"
            )
        converted = dict(params)
        for key in (
            "transformation_matrix",
            "gt_transformation_matrix",
            "spatial_correction_matrix",
        ):
            try:
                matrix = np.asarray(converted.get(key), dtype=np.float64)
            except (TypeError, ValueError) as exc:
                raise EngineError(f"CAV {cav_id!r}: {key} is not a numeric matrix: {exc}") from None
            if matrix.shape != (4, 4):
                raise EngineError(
                    f"CAV {cav_id!r}: {key} must be a 4x4 matrix, got shape {matrix.shape}"
                )
            converted[key] = matrix
        pose = converted.get("lidar_pose")
        if not isinstance(pose, (list, tuple)) or len(pose) != 6:
            raise EngineError(f"CAV {cav_id!r}: lidar_pose must be [x, y, z, roll, yaw, pitch]")
        converted["lidar_pose"] = [float(value) for value in pose]
        if not isinstance(converted.get("vehicles"), dict):
            converted["vehicles"] = {}
        frame[cav_id] = {**cav, "lidar_np": lidar, "params": converted}
    return frame


class OpenCOODEngine:
    """One loaded checkpoint: build once per worker, ``infer`` once per frame."""

    def __init__(
        self,
        config_path: str,
        weights_path: str,
        *,
        work_dir: str,
        device: str = "",
        score_threshold: float = -1.0,
        log=None,
    ) -> None:
        self._log = log or (lambda text: None)
        self._torch = _module("torch")
        self._numpy = _module("numpy")
        datasets = _module("opencood.data_utils.datasets")
        yaml_utils = _module("opencood.hypes_yaml.yaml_utils")
        self._train_utils = _module("opencood.tools.train_utils")
        if not callable(getattr(yaml_utils, "load_yaml", None)):
            raise EngineError("opencood.hypes_yaml.yaml_utils.load_yaml is not available")
        if not callable(getattr(self._train_utils, "create_model", None)):
            raise EngineError("opencood.tools.train_utils.create_model is not available")
        try:
            loaded = yaml_utils.load_yaml(config_path)
        except Exception as exc:  # noqa: BLE001 - the user's parser reports its own error
            raise EngineError(f"cannot read the checkpoint config {config_path}: {exc}") from None
        if not isinstance(loaded, dict):
            raise EngineError(f"the checkpoint config {config_path} is not a mapping")
        config = copy.deepcopy(loaded)
        self.fusion = _fusion_of(config)
        self.order = _order_of(config)
        train_params = config.get("train_params")
        self.max_cav = int(
            (train_params or {}).get("max_cav", 7) if isinstance(train_params, dict) else 7
        )
        com_range = getattr(datasets, "COM_RANGE", None)
        if not isinstance(com_range, (int, float)):
            raise EngineError("opencood.data_utils.datasets.COM_RANGE is not available")
        self.com_range = float(com_range)
        if score_threshold >= 0.0:
            postprocess = config["postprocess"]
            assert isinstance(postprocess, dict)
            target_args = postprocess.setdefault("target_args", {})
            assert isinstance(target_args, dict)
            target_args["score_threshold"] = float(score_threshold)
            self.score_threshold = float(score_threshold)
        else:
            self.score_threshold = -1.0
        dataset_class = getattr(datasets, self.fusion, None)
        if dataset_class is None:
            raise EngineError(f"opencood.data_utils.datasets has no {self.fusion}")
        # The dataset's __init__ enumerates its roots even in test mode: give it empty ones,
        # and remove them again once it has built its (empty) bookkeeping. Nothing reads them
        # afterwards because only retrieve_base_data is used.
        dataset_root = tempfile.mkdtemp(prefix="opencood-dataset-", dir=work_dir)
        for split in ("train", "validate"):
            os.makedirs(os.path.join(dataset_root, split), exist_ok=True)
        config["root_dir"] = os.path.join(dataset_root, "train")
        config["validate_dir"] = os.path.join(dataset_root, "validate")

        injected = {"frame": None}

        class _Injected(dataset_class):  # type: ignore[misc, valid-type]
            def retrieve_base_data(self, idx, cur_ego_pose_flag=True):  # noqa: ANN001, ANN201
                if injected["frame"] is None:
                    raise EngineError("no frame has been injected into the dataset")
                return injected["frame"]

        try:
            self._dataset = _Injected(config, visualize=False, train=False)
        except Exception as exc:  # noqa: BLE001 - dataset construction errors are the env's
            raise EngineError(
                f"cannot build {self.fusion} from the checkpoint config: {exc}"
            ) from None
        finally:
            shutil.rmtree(dataset_root, ignore_errors=True)
        self._injected = injected
        self.device = device or "cpu"
        if self.device.startswith("cuda") and not self._torch.cuda.is_available():
            raise EngineError(f"device {self.device} was asked for but torch sees no CUDA")
        try:
            self.model = self._train_utils.create_model(config)
        except Exception as exc:  # noqa: BLE001 - model construction errors are the env's
            raise EngineError(f"cannot build the model from the checkpoint config: {exc}") from None
        state = _load_weights(self._torch, weights_path)
        try:
            result = self.model.load_state_dict(state, strict=False)
        except Exception as exc:  # noqa: BLE001 - shape/key mismatches are reported with counts
            raise EngineError(f"the weights do not fit the model: {exc}") from None
        missing = list(getattr(result, "missing_keys", []) or [])
        unexpected = list(getattr(result, "unexpected_keys", []) or [])
        if len(unexpected) == len(state):
            raise EngineError(
                "none of the checkpoint's parameter names matches the model; the config and "
                "the weights are probably from different experiments"
            )
        if missing or unexpected:
            self._log(
                f"loaded {len(state) - len(unexpected)} of {len(state)} checkpoint tensors; "
                f"{len(missing)} model tensors without weights, {len(unexpected)} unused"
            )
        try:
            self.model.to(self.device)
        except Exception as exc:  # noqa: BLE001 - a CPU-only build asked for cuda, etc.
            raise EngineError(f"cannot move the model to {self.device}: {exc}") from None
        self.model.eval()
        # A first seed for anything the build itself draws; `infer` reseeds per frame.
        self._numpy.random.seed(0)
        self._torch.manual_seed(0)
        self._log(
            f"OpenCOOD {self.fusion}: max_cav {self.max_cav}, com_range {self.com_range:g} m, "
            f"box order {self.order}, device {self.device}"
        )

    # -- inference -----------------------------------------------------------------------

    def infer(
        self, base_data: dict[str, object], *, scene_id: str = "", time_us: int = 0
    ) -> list[dict[str, object]]:
        """One frame's predictions as ``[{center, size_lwh, heading, score}]`` in the ego frame.

        The point shuffle is seeded from ``(scene_id, time_us)`` (``frame_seed``) so a second
        pass over the same frame in the same worker repeats it.
        """
        seed = frame_seed(scene_id, time_us)
        self._numpy.random.seed(seed)
        self._torch.manual_seed(seed)
        self._injected["frame"] = dataset_frame(base_data)
        try:
            item = self._dataset[0]
            batch = self._dataset.collate_batch_test([item])
            batch = self._to_device(batch)
            with self._torch.no_grad():
                output = self.model(batch["ego"])
            processed = self._dataset.post_process(batch, {"ego": output})
        except EngineError:
            raise
        except Exception as exc:  # noqa: BLE001 - report the user's stack, not a bare traceback
            raise EngineError(f"OpenCOOD inference failed: {type(exc).__name__}: {exc}") from None
        pred = _to_list(processed[0])
        scores = _to_list(processed[1])
        try:
            return boxes_from_opencood(pred, self.order, scores)
        except ConvertError as exc:
            raise EngineError(f"OpenCOOD returned boxes this plugin cannot read: {exc}") from None

    def _to_device(self, value: object) -> object:
        to_device = getattr(self._train_utils, "to_device", None)
        if callable(to_device):
            try:
                return to_device(value, self.device)
            except Exception:  # noqa: BLE001 - fall back to a plain recursive move
                pass
        if isinstance(value, dict):
            return {key: self._to_device(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._to_device(item) for item in value]
        if hasattr(value, "to"):
            return value.to(self.device)  # type: ignore[union-attr]
        return value

    def describe(self) -> dict[str, str]:
        """The loaded configuration, for the worker's ``plugin.log`` (not for the protocol)."""
        return {
            "fusion": self.fusion,
            "max_cav": str(self.max_cav),
            "com_range_m": f"{self.com_range:g}",
            "box_order": self.order,
            "score_threshold": (
                "checkpoint config" if self.score_threshold < 0.0 else f"{self.score_threshold:g}"
            ),
            "device": self.device,
        }
