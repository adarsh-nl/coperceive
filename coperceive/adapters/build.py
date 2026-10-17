# SPDX-License-Identifier: Apache-2.0
"""Build schema 1 scenes from a manifest: ``ManifestAdapter``.

The builder reads files through the generic readers, applies the manifest's
units, rotation encodings and axis remaps, and returns a :class:`Scene`. It
writes nothing. The ingest driver (``coperceive.adapters.ingest``) does the
parallel run, ``.partial/`` and ``COMPLETE.json``.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from coperceive.adapters.paths import PathError, Template, confine
from coperceive.adapters.readers import registry as readers
from coperceive.adapters.readers.calib import pinhole_from_matrix
from coperceive.adapters.readers.lidar import LidarError, read_bin_f32, read_pcd, read_ply
from coperceive.adapters.readers.records import (
    FieldError,
    get_field,
    read_csv_rows,
    read_json_record,
    read_json_table,
    read_text_rows,
    read_yaml_record,
)
from coperceive.adapters.rotation import (
    AxisRemap,
    RotationError,
    euler_to_se3,
    matrix_to_se3,
    quaternion_to_se3,
    remap_transform,
)
from coperceive.adapters.spec import (
    BoxesSpec,
    LidarSpec,
    Manifest,
    PlatformValue,
    PosesSpec,
    RotationSpec,
    SensorsSpec,
)
from coperceive.diagnostics import shared_objects
from coperceive.errors import CoperceiveError
from coperceive.model import (
    SCHEMA_VERSION,
    Agent,
    Annotation,
    BoxFrame,
    Clock,
    ImageRef,
    Modality,
    PointCloud,
    Scene,
    Sensor,
    StampedPose,
)
from coperceive.se3 import SE3

Sample = int | str
_LENGTH = {"m": 1.0, "cm": 0.01, "mm": 0.001}
_UNIT_US = {"s": 1_000_000.0, "ms": 1_000.0, "us": 1.0, "ns": 0.001}


class BuildError(CoperceiveError):
    """A source file is missing, malformed, or does not match the manifest."""


@dataclass(frozen=True)
class SceneRef:
    """One scene the adapter can load. ``samples`` are frames or string keys."""

    scene_id: str
    samples: tuple[Sample, ...]
    platform: tuple[tuple[str, str], ...]
    params: tuple[tuple[str, str], ...]

    @property
    def frames(self) -> tuple[int, ...]:
        return tuple(s for s in self.samples if isinstance(s, int))


@dataclass(frozen=True)
class CheckReport:
    scenes: int
    errors: tuple[str, ...]
    counts: Mapping[str, tuple[tuple[str, int], ...]]
    variant_counts: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True)
class _AgentCtx:
    agent_id: str
    agent_type: object
    values: dict[str, object]  # template values: scene, agent, agent_root, params


class ManifestAdapter:
    """The :class:`Adapter` protocol, driven by a manifest."""

    def __init__(self, spec: Manifest) -> None:
        self.spec = spec
        self.name = spec.adapter
        self._body = AxisRemap.parse(spec.conventions.body_axes)
        self._camera = AxisRemap.parse_camera(spec.conventions.camera_axes)
        self._length = _LENGTH[spec.conventions.length_unit]
        self._degrees = spec.conventions.angle_unit == "deg"

    # -- discovery ---------------------------------------------------------------

    def scenes(self, raw_root: Path, params: Mapping[str, str]) -> tuple[SceneRef, ...]:
        checked = self._params(params)
        values: dict[str, object] = dict(checked)
        index = self.spec.scenes.index
        refs: list[SceneRef] = []
        if index is not None:
            file = self._path(raw_root, index.file, values)
            rows = _wrap(lambda: read_json_table(file), f"{index.file.text}")
            for other in index.must_equal:
                other_path = self._path(raw_root, Template(other, frozenset(checked)), values)
                other_rows = _wrap(lambda: read_json_table(other_path), other)
                if other_rows != rows:
                    raise BuildError(f"scenes.index.must_equal: {other} differs from {index.file.text}")
            for row in rows:
                scene_id = _scene_id(_wrap(lambda: get_field(row, index.scene_id), index.file.text))
                raw_samples = _wrap(lambda: get_field(row, index.samples), index.file.text)
                samples = _samples(raw_samples, index.sample_kind, scene_id)
                platform = self._platform(index.platform, row, checked, scene_id)
                refs.append(SceneRef(scene_id, samples, platform, tuple(sorted(checked.items()))))
            return tuple(refs)
        glob = self.spec.scenes.glob
        assert glob is not None
        for found in glob.path.discover(raw_root, values, "scene"):
            scene_id = _scene_id(found["scene"])
            agents = self._agents(raw_root, scene_id, checked)
            stream = getattr(self.spec.streams, glob.sample_source)
            template: Template = stream.path
            free = "frame" if "frame" in template.placeholders else "sample"
            keys: set[Sample] = set()
            for ctx in agents:
                for hit in template.discover(raw_root, ctx.values, free):
                    value = hit[free]
                    keys.add(value if isinstance(value, int) else str(value))
            if not keys:
                raise BuildError(f"{scene_id}: no samples found with streams.{glob.sample_source}")
            samples = tuple(sorted(keys, key=lambda s: (isinstance(s, str), s)))
            platform = self._platform(glob.platform, {}, checked, scene_id)
            refs.append(SceneRef(scene_id, samples, platform, tuple(sorted(checked.items()))))
        return tuple(refs)

    def load(self, raw_root: Path, ref: SceneRef) -> Scene:
        return self._build(raw_root, ref, ref.samples)

    def check(
        self,
        raw_root: Path,
        params: Mapping[str, str],
        *,
        scene_ids: Sequence[str] | None = None,
    ) -> CheckReport:
        refs = self.scenes(raw_root, params)
        if scene_ids is not None:
            wanted = set(scene_ids)
            refs = tuple(ref for ref in refs if ref.scene_id in wanted)
        errors: list[str] = []
        counts: dict[str, tuple[tuple[str, int], ...]] = {}
        variants: dict[int, int] = {}
        for ref in refs:
            subset = ref.samples if len(ref.samples) <= 2 else (ref.samples[0], ref.samples[-1])
            tally: dict[str, int] = {}
            try:
                scene = self._build(raw_root, ref, subset, tally=tally, variants=variants)
                tally["agents"] = len(scene.agents)
            except (BuildError, ValueError) as exc:
                errors.append(f"{ref.scene_id}: {exc}")
            counts[ref.scene_id] = tuple(sorted(tally.items()))
        return CheckReport(len(refs), tuple(errors), counts, tuple(sorted(variants.items())))

    def variant_counts(self, raw_root: Path, ref: SceneRef) -> tuple[tuple[int, int], ...]:
        """Label-line widths per count, for ``report.variant_counts``. Reads only labels."""
        boxes = self.spec.streams.boxes
        if self.spec.report.variant_counts is None or boxes is None:
            return ()
        counts: dict[int, int] = {}
        for ctx in self._agents(raw_root, ref.scene_id, dict(ref.params)):
            for sample in ref.samples:
                values = _with_sample(ctx.values, sample)
                path = self._path(raw_root, boxes.path, values)
                if not path.is_file():
                    if boxes.missing == "skip":
                        continue
                    raise BuildError(f"{ctx.agent_id}: missing label file {boxes.path.expand(values)}")
                for parts in read_text_rows(path):
                    counts[len(parts)] = counts.get(len(parts), 0) + 1
        return tuple(sorted(counts.items()))

    # -- building ----------------------------------------------------------------

    def _build(
        self,
        raw_root: Path,
        ref: SceneRef,
        samples: Sequence[Sample],
        *,
        tally: dict[str, int] | None = None,
        variants: dict[int, int] | None = None,
    ) -> Scene:
        params = dict(ref.params)
        agents_ctx = self._agents(raw_root, ref.scene_id, params)
        agents: list[Agent] = []
        sensors: list[Sensor] = []
        annotations: list[Annotation] = []
        images: list[ImageRef] = []
        clouds: list[PointCloud] = []
        for ctx in agents_ctx:
            poses = self._poses(raw_root, ctx, samples, tally)
            agents.append(
                Agent(
                    agent_id=ctx.agent_id,
                    agent_type=ctx.agent_type,  # type: ignore[arg-type]
                    poses=tuple(poses),
                    platform=ref.platform,
                )
            )
            agent_sensors = self._sensors(raw_root, ctx, tally)
            sensors.extend(agent_sensors)
            if self.spec.streams.boxes is not None:
                annotations.extend(
                    self._boxes(raw_root, ctx, samples, agent_sensors, poses, tally, variants)
                )
            if self.spec.streams.images is not None:
                images.extend(self._images(raw_root, ctx, samples, agent_sensors, tally))
            if self.spec.streams.lidar is not None:
                clouds.extend(self._lidar(raw_root, ctx, samples, agent_sensors, tally))
        try:
            return Scene(
                schema_version=SCHEMA_VERSION,
                scene_id=ref.scene_id,
                world_frame=self.spec.world_frame,
                clock=Clock(unit="us", scale_note=self.spec.clock.scale_note),
                agents=tuple(agents),
                sensors=tuple(sensors),
                annotations=tuple(annotations),
                objects=shared_objects(annotations),
                point_clouds=tuple(clouds),
                images=tuple(images),
            )
        except ValueError as exc:
            raise BuildError(f"{ref.scene_id}: {exc}") from None

    def _agents(self, raw_root: Path, scene_id: str, params: Mapping[str, str]) -> list[_AgentCtx]:
        base: dict[str, object] = {**params, "scene": scene_id}
        out: list[_AgentCtx] = []
        spec = self.spec.agents
        if spec.discover is None:
            for agent in spec.fixed:
                root = Template(agent.root, frozenset(params)).expand(base).rstrip("/")
                values = {**base, "agent": agent.id, "agent_root": root}
                out.append(_AgentCtx(agent.id, agent.type, values))
            return out
        for hit in spec.discover.glob.discover(raw_root, base, "agent"):
            agent_id = str(hit["agent"])
            if agent_id in spec.discover.exclude:
                continue
            root = spec.discover.glob.expand({**base, "agent": agent_id}).rstrip("/")
            values = {**base, "agent": agent_id, "agent_root": root}
            out.append(_AgentCtx(agent_id, spec.discover.type, values))
        if not out:
            raise BuildError(f"{scene_id}: no agents matched {spec.discover.glob.text}")
        return out

    def _poses(
        self,
        raw_root: Path,
        ctx: _AgentCtx,
        samples: Sequence[Sample],
        tally: dict[str, int] | None,
    ) -> list[StampedPose]:
        spec = self.spec.streams.poses
        out: list[StampedPose] = []
        table = self._table(raw_root, spec.reader, spec.path, ctx.values, spec.sample_field)
        for sample in samples:
            values = _with_sample(ctx.values, sample)
            if table is not None:
                record = table.get(_key(sample))
                if record is None:
                    if spec.missing == "skip":
                        continue
                    raise BuildError(f"{ctx.agent_id}: no pose row for sample {sample}")
                record = self._join(raw_root, record, spec, values)
                where = f"{spec.path.text} sample {sample}"
            else:
                path = self._path(raw_root, spec.path, values)
                if not path.is_file():
                    if spec.missing == "skip":
                        continue
                    raise BuildError(f"{ctx.agent_id}: missing pose file {spec.path.expand(values)}")
                record = self._record(spec.reader, path)
                where = spec.path.expand(values)
            for key, expect in spec.check.items():
                got = _wrap(lambda: get_field(record, key), where)
                want: object = sample if expect == "frame" or expect == "sample" else expect
                if str(got) != str(want) and got != want:
                    raise BuildError(f"{where}: {key} is {got!r}, expected {want!r}")
            translation = tuple(
                _num(_wrap(lambda: get_field(record, f), where), f, where) * self._length
                for f in spec.translation
            )
            rotation = self._rotation(spec.rotation, record, where, allow_missing=False)
            pose = rotation.translation(translation[0], translation[1], translation[2])
            if spec.direction == "parent_to_child":
                pose = pose.inverse()
            pose = self._body.world_from_body(pose)
            out.append(StampedPose(time_us=self._time_us(sample, record, where), world_from_body=pose))
            _count(tally, "poses")
        return out

    def _sensors(self, raw_root: Path, ctx: _AgentCtx, tally: dict[str, int] | None) -> list[Sensor]:
        spec = self.spec.streams.sensors
        out: list[Sensor] = []
        records: list[tuple[str, dict[str, object], str]] = []
        table = self._table(raw_root, spec.reader, spec.path, ctx.values, spec.sensor_field)
        if table is not None:
            for key, record in table.items():
                if spec.agent_field is not None:
                    owner = _wrap(lambda: get_field(record, spec.agent_field or ""), spec.path.text)
                    if str(owner) != ctx.agent_id:
                        continue
                records.append((key, record, f"{spec.path.text} sensor {key}"))
        else:
            for hit in spec.path.discover(raw_root, ctx.values, "sensor"):
                sensor_id = str(hit["sensor"])
                values = {**ctx.values, "sensor": sensor_id}
                path = self._path(raw_root, spec.path, values)
                records.append((sensor_id, self._record(spec.reader, path), spec.path.expand(values)))
        for sensor_id, record, where in records:
            out.append(self._sensor(ctx.agent_id, sensor_id, record, where))
            _count(tally, "sensors")
        return out

    def _sensor(self, agent_id: str, sensor_id: str, record: dict[str, object], where: str) -> Sensor:
        spec = self.spec.streams.sensors
        ext = spec.extrinsic
        if ext.matrix is not None:
            raw = _wrap(lambda: get_field(record, ext.matrix.field if ext.matrix else ""), where)
            extrinsic = _wrap(
                lambda: matrix_to_se3(
                    raw, shape=ext.matrix.shape if ext.matrix else "4x4", column_major=bool(ext.matrix and ext.matrix.column_major)
                ),
                where,
            )
            extrinsic = extrinsic.translation(
                extrinsic.tx * self._length, extrinsic.ty * self._length, extrinsic.tz * self._length
            )
        else:
            assert ext.translation is not None and ext.rotation is not None
            t = tuple(
                _num(_wrap(lambda: get_field(record, f), where), f, where) * self._length
                for f in ext.translation
            )
            extrinsic = self._rotation(ext.rotation, record, where, allow_missing=False).translation(
                t[0], t[1], t[2]
            )
        if ext.direction == "parent_to_child":
            extrinsic = extrinsic.inverse()
        fx = fy = cx = cy = None
        has_intrinsic = False
        if spec.intrinsic is not None:
            intr = spec.intrinsic
            try:
                if intr.matrix is not None:
                    raw_m = get_field(record, intr.matrix)
                    if raw_m is not None:
                        fx, fy, cx, cy = pinhole_from_matrix(raw_m)
                        has_intrinsic = True
                else:
                    assert intr.fields is not None
                    values = [get_field(record, f) for f in intr.fields]
                    if all(v is not None for v in values):
                        fx, fy, cx, cy = (_num(v, f, where) for v, f in zip(values, intr.fields))
                        has_intrinsic = True
            except (FieldError, ValueError) as exc:
                if not intr.optional:
                    raise BuildError(f"{where}: intrinsic: {exc}") from None
        width = height = None
        if has_intrinsic and spec.image_size is not None:
            size = spec.image_size
            if size.derive == "principal_point_x2":
                assert cx is not None and cy is not None
                width, height = int(round(cx * 2)), int(round(cy * 2))
            elif size.fields is not None:
                width = _int(_wrap(lambda: get_field(record, size.fields[0] if size.fields else ""), where), where)
                height = _int(_wrap(lambda: get_field(record, size.fields[1] if size.fields else ""), where), where)
        modality = Modality.CAMERA
        for rule in spec.modality:
            if rule.default:
                modality = rule.modality
                break
            if rule.has_intrinsic is not None and rule.has_intrinsic == has_intrinsic:
                modality = rule.modality
                break
            if rule.stem_prefix is not None and sensor_id.startswith(rule.stem_prefix):
                modality = rule.modality
                break
        child = self._camera if modality is Modality.CAMERA else self._body
        body_from_sensor = remap_transform(self._body, child, extrinsic)
        try:
            return Sensor(
                sensor_id=sensor_id,
                agent_id=agent_id,
                modality=modality,
                body_from_sensor=body_from_sensor,
                image_width_px=width,
                image_height_px=height,
                fx=fx,
                fy=fy,
                cx=cx,
                cy=cy,
            )
        except ValueError as exc:
            raise BuildError(f"{where}: {exc}") from None

    def _boxes(
        self,
        raw_root: Path,
        ctx: _AgentCtx,
        samples: Sequence[Sample],
        sensors: Sequence[Sensor],
        poses: Sequence[StampedPose],
        tally: dict[str, int] | None,
        variants: dict[int, int] | None,
    ) -> list[Annotation]:
        spec = self.spec.streams.boxes
        assert spec is not None
        out: list[Annotation] = []
        table = self._table(raw_root, spec.reader, spec.path, ctx.values, None) if spec.reader == "json_table" else None
        for sample in samples:
            values = _with_sample(ctx.values, sample)
            where = spec.path.expand(values)
            rows: list[dict[str, object]] = []
            if spec.reader == "text_rows":
                path = self._path(raw_root, spec.path, values)
                if not path.is_file():
                    if spec.missing == "skip":
                        continue
                    raise BuildError(f"{ctx.agent_id}: missing label file {where}")
                for parts in read_text_rows(path):
                    columns = spec.variants.get(len(parts))
                    if columns is None:
                        known = ", ".join(str(k) for k in sorted(spec.variants))
                        raise BuildError(f"{where}: a line has {len(parts)} fields, expected {known}")
                    if variants is not None:
                        variants[len(parts)] = variants.get(len(parts), 0) + 1
                    rows.append({c: v for c, v in zip(columns, parts) if c != "_"})
            else:
                if table is not None:
                    record: dict[str, object] | None = table.get(_key(sample))
                else:
                    path = self._path(raw_root, spec.path, values)
                    record = self._record(spec.reader, path) if path.is_file() else None
                if record is None:
                    if spec.missing == "skip":
                        continue
                    raise BuildError(f"{ctx.agent_id}: missing boxes for sample {sample} at {where}")
                items: object = record
                if spec.records is not None:
                    items = _wrap(lambda: get_field(record, spec.records or ""), where)
                if not isinstance(items, list):
                    raise BuildError(f"{where}: {spec.records or 'record'} is not a list of boxes")
                for item in items:  # type: ignore[union-attr]
                    row: dict[str, object] = {}
                    for column, field_path in spec.fields.items():
                        try:
                            row[column] = get_field(item, field_path)
                        except FieldError:
                            continue
                    rows.append(row)
            time_us = self._time_us(sample, None, where)
            for row in rows:
                box = self._box(spec, row, ctx.agent_id, time_us, sensors, where)
                if box is not None:
                    out.append(box)
                    _count(tally, "boxes")
        return out

    def _box(
        self,
        spec: BoxesSpec,
        row: Mapping[str, object],
        agent_id: str,
        time_us: int,
        sensors: Sequence[Sensor],
        where: str,
    ) -> Annotation | None:
        def get(name: str) -> object:
            if name in row:
                return row[name]
            if name in spec.fill:
                return spec.fill[name]
            raise BuildError(f"{where}: box has no {name!r}")

        category = str(get("category"))
        if category in spec.class_map:
            category = spec.class_map[category]
        elif spec.class_map and spec.class_map_unknown != "keep":
            if spec.class_map_unknown == "drop":
                return None
            raise BuildError(f"{where}: class {category!r} is not in class_map")
        sizes = [_num(get(n), n, where) * self._length for n in ("length", "width", "height")]
        order = spec.size_order
        named = dict(zip(("length", "width", "height"), sizes))
        if order != "lwh":
            # The manifest columns were named in source order; reassign.
            source = dict(zip(tuple(order), sizes))
            named = {"length": source["l"], "width": source["w"], "height": source["h"]}
        centre = [_num(get(n), n, where) * self._length for n in ("cx", "cy", "cz")]
        if spec.centre_origin == "bottom":
            centre[2] += named["height"] / 2.0
        rotation = self._rotation(spec.rotation, row, where, allow_missing=True)
        track: str | None = None
        if "track_id" in row and row["track_id"] is not None:
            raw_track = row["track_id"]
            if spec.track_id_cast == "int_string":
                try:
                    track = str(int(float(str(raw_track))))
                except ValueError:
                    raise BuildError(f"{where}: track id {raw_track!r} is not an integer") from None
            else:
                track = str(raw_track)
        visibility = None
        if "visibility" in row and row["visibility"] is not None:
            visibility = _num(row["visibility"], "visibility", where)
        frame = BoxFrame.BODY
        c = (centre[0], centre[1], centre[2])
        if spec.frame == "world":
            frame = BoxFrame.WORLD
        elif spec.frame.startswith("sensor:"):
            sensor_id = spec.frame[len("sensor:") :]
            sensor = next((s for s in sensors if s.sensor_id == sensor_id), None)
            if sensor is None:
                raise BuildError(f"{where}: box frame names unknown sensor {sensor_id!r}")
            # The sensor extrinsic is already in library axes; bring the box
            # into source body axes first, then through the remap below.
            child = self._camera if sensor.modality is Modality.CAMERA else self._body
            source_body_from_sensor = remap_transform(
                self._body.inverse(), child.inverse(), sensor.body_from_sensor
            )
            c = source_body_from_sensor.apply(c)
            rotation = source_body_from_sensor.translation(0.0, 0.0, 0.0).compose(rotation)
        if frame is BoxFrame.BODY:
            c = self._body.point(c)
            rotation = self._body.rotation(rotation)
        try:
            return Annotation(
                agent_id=agent_id,
                time_us=time_us,
                track_id=track,
                category=category,
                center=c,
                size_lwh=(named["length"], named["width"], named["height"]),
                box_from_rpy=rotation,
                visibility=visibility,
                frame=frame,
            )
        except ValueError as exc:
            raise BuildError(f"{where}: {exc}") from None

    def _images(
        self,
        raw_root: Path,
        ctx: _AgentCtx,
        samples: Sequence[Sample],
        sensors: Sequence[Sensor],
        tally: dict[str, int] | None,
    ) -> list[ImageRef]:
        spec = self.spec.streams.images
        assert spec is not None
        out: list[ImageRef] = []
        for sensor in sensors:
            if sensor.modality is not Modality.CAMERA:
                continue
            for sample in samples:
                values = {**_with_sample(ctx.values, sample), "sensor": sensor.sensor_id}
                relative = spec.path.expand(values)
                if spec.require_file and not self._path(raw_root, spec.path, values).is_file():
                    raise BuildError(f"{ctx.agent_id}: missing image {relative}")
                out.append(
                    ImageRef(
                        agent_id=ctx.agent_id,
                        sensor_id=sensor.sensor_id,
                        time_us=self._time_us(sample, None, relative),
                        path=relative,
                    )
                )
                _count(tally, "images")
        return out

    def _lidar(
        self,
        raw_root: Path,
        ctx: _AgentCtx,
        samples: Sequence[Sample],
        sensors: Sequence[Sensor],
        tally: dict[str, int] | None,
    ) -> list[PointCloud]:
        spec = self.spec.streams.lidar
        assert spec is not None
        if spec.agents is not None and ctx.agent_id not in spec.agents:
            return []
        if spec.directory is not None:
            directory = self._path(raw_root, spec.directory, ctx.values)
            if not directory.is_dir():
                if spec.when_directory_absent == "skip":
                    return []
                raise BuildError(f"{ctx.agent_id}: missing lidar directory {spec.directory.expand(ctx.values)}")
        if not any(s.sensor_id == spec.sensor for s in sensors):
            raise BuildError(f"{ctx.agent_id}: lidar sensor {spec.sensor!r} is not a calibrated sensor")
        out: list[PointCloud] = []
        for sample in samples:
            values = _with_sample(ctx.values, sample)
            relative = spec.path.expand(values)
            path = self._path(raw_root, spec.path, values)
            if not path.is_file():
                if spec.missing == "skip":
                    continue
                raise BuildError(f"{ctx.agent_id}: missing point file {relative}")
            count, blob = self._read_points(spec, path)
            blob = self._remap_points(blob)
            out.append(
                PointCloud(
                    agent_id=ctx.agent_id,
                    sensor_id=spec.sensor,
                    time_us=self._time_us(sample, None, relative),
                    count=count,
                    xyz_i_f32=blob,
                )
            )
            _count(tally, "lidar")
        return out

    def _read_points(self, spec: LidarSpec, path: Path) -> tuple[int, bytes]:
        try:
            if spec.reader == "ply":
                return read_ply(path, fields=spec.fields, intensity_fill=spec.intensity_fill)
            if spec.reader == "pcd":
                return read_pcd(path, fields=spec.fields, intensity_fill=spec.intensity_fill)
            if spec.reader == "bin_f32":
                assert spec.stride is not None
                return read_bin_f32(
                    path, stride=spec.stride, offsets=spec.offsets, intensity_fill=spec.intensity_fill
                )
            plugin = readers.plugin("lidar", spec.reader)
            if plugin is None:
                raise BuildError(f"reader {spec.reader!r} is not installed")
            result = plugin(path, fields=spec.fields, intensity_fill=spec.intensity_fill)
            count, blob = result  # type: ignore[misc]
            return int(count), bytes(blob)  # type: ignore[arg-type]
        except (LidarError, ValueError, OSError) as exc:
            raise BuildError(f"{path.name}: {exc}") from None

    def _remap_points(self, blob: bytes) -> bytes:
        if self._body.is_identity and self._length == 1.0:
            return blob
        points = np.frombuffer(blob, dtype="<f4").reshape(-1, 4).astype(np.float64)
        matrix = np.asarray(self._body.rows, dtype=np.float64)
        xyz = (points[:, :3] * self._length) @ matrix.T
        points[:, :3] = xyz
        return points.astype("<f4").tobytes()

    # -- helpers -------------------------------------------------------------------

    def _params(self, params: Mapping[str, str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for name, spec in self.spec.params.items():
            if name not in params:
                raise BuildError(f"param {name!r} is required (--param {name}=...)")
            value = params[name]
            if spec.allowed is not None and value not in spec.allowed:
                raise BuildError(f"param {name!r} must be one of {', '.join(spec.allowed)}")
            out[name] = value
        for name in params:
            if name not in self.spec.params:
                raise BuildError(f"param {name!r} is not declared by adapter {self.name!r}")
        return out

    def _platform(
        self,
        values: Mapping[str, PlatformValue],
        row: Mapping[str, object],
        params: Mapping[str, str],
        scene_id: str,
    ) -> tuple[tuple[str, str], ...]:
        out: dict[str, str] = {}
        for key, value in values.items():
            if value.param is not None:
                out[key] = params[value.param]
            elif value.const is not None:
                out[key] = value.const
            else:
                assert value.field is not None
                raw = _wrap(lambda: get_field(row, value.field or ""), scene_id)
                if value.number:
                    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                        raise BuildError(f"{scene_id}: platform {key} is not a number")
                    out[key] = format(float(raw), value.format) if value.format else repr(float(raw))
                else:
                    out[key] = str(raw)
        return tuple(sorted(out.items()))

    def _path(self, raw_root: Path, template: Template, values: Mapping[str, object]) -> Path:
        try:
            return confine(raw_root, template.expand(values))
        except PathError as exc:
            raise BuildError(str(exc)) from None

    def _record(self, reader: str, path: Path) -> dict[str, object]:
        try:
            if reader == "json_record":
                return read_json_record(path)
            if reader == "yaml_record":
                return read_yaml_record(path)
            plugin = readers.plugin("record", reader)
            if plugin is not None:
                record = plugin(path)
                if not isinstance(record, dict):
                    raise BuildError(f"{path.name}: reader {reader!r} did not return a mapping")
                return {str(k): v for k, v in record.items()}  # type: ignore[union-attr]
        except (FieldError, ValueError, OSError) as exc:
            raise BuildError(f"{path.name}: {exc}") from None
        raise BuildError(f"reader {reader!r} does not read one record")

    def _table(
        self,
        raw_root: Path,
        reader: str,
        template: Template,
        values: Mapping[str, object],
        key_field: str | None,
    ) -> dict[str, dict[str, object]] | None:
        """Rows of a table reader keyed by ``key_field``. None for record readers."""
        if reader not in readers.names("table"):
            return None
        path = self._path(raw_root, template, values)
        if not path.is_file():
            raise BuildError(f"missing table {template.expand(values)}")
        try:
            rows: list[dict[str, object]]
            if reader == "csv_rows":
                rows = [dict(r) for r in read_csv_rows(path)]
            elif reader == "json_table":
                rows = read_json_table(path)
            else:
                plugin = readers.plugin("table", reader)
                if plugin is None:
                    raise BuildError(f"reader {reader!r} is not installed")
                loaded = plugin(path)
                if not isinstance(loaded, list):
                    raise BuildError(f"{path.name}: reader {reader!r} did not return a list")
                rows = [dict(r) for r in loaded]  # type: ignore[union-attr]
        except (FieldError, ValueError, OSError) as exc:
            raise BuildError(f"{path.name}: {exc}") from None
        if key_field is None:
            return {str(i): row for i, row in enumerate(rows)}
        out: dict[str, dict[str, object]] = {}
        for row in rows:
            key = _wrap(lambda: get_field(row, key_field), path.name)
            out[_key(key if isinstance(key, int) else str(key))] = row
        return out

    def _join(
        self,
        raw_root: Path,
        record: dict[str, object],
        spec: PosesSpec,
        values: Mapping[str, object],
    ) -> dict[str, object]:
        merged = dict(record)
        for join in spec.joins:
            table = self._table(raw_root, "json_table", join.table, values, join.key)
            assert table is not None
            on = _wrap(lambda: get_field(merged, join.on), join.table.text)
            hit = table.get(_key(on if isinstance(on, int) else str(on)))
            if hit is None:
                raise BuildError(f"{join.table.text}: no row with {join.key} == {on!r}")
            merged = {**hit, **merged}
        return merged

    def _rotation(
        self,
        spec: RotationSpec,
        record: Mapping[str, object],
        where: str,
        *,
        allow_missing: bool,
    ) -> SE3:
        try:
            if spec.euler is not None:
                angles: list[float] = []
                for name in spec.euler.fields:
                    if name in record or not allow_missing:
                        angles.append(_num(_wrap(lambda: get_field(record, name), where), name, where))
                    elif spec.missing_angles is not None:
                        angles.append(0.0)
                    else:
                        raise BuildError(f"{where}: box has no {name!r} and no missing_angles rule")
                return euler_to_se3(
                    (angles[0], angles[1], angles[2]),
                    order=spec.euler.order,
                    intrinsic=spec.euler.intrinsic,
                    degrees=self._degrees,
                )
            if spec.quaternion is not None:
                q = [_num(_wrap(lambda: get_field(record, n), where), n, where) for n in spec.quaternion.fields]
                return quaternion_to_se3(q, order=spec.quaternion.order)
            if spec.matrix is not None:
                m = spec.matrix
                raw = _wrap(lambda: get_field(record, m.field), where)
                return matrix_to_se3(raw, shape=m.shape, column_major=m.column_major).translation(0.0, 0.0, 0.0)
            assert spec.yaw_only is not None
            angle = _num(_wrap(lambda: get_field(record, spec.yaw_only.field if spec.yaw_only else ""), where), "yaw", where)
            about = {"x": (angle, 0.0, 0.0), "y": (0.0, angle, 0.0), "z": (0.0, 0.0, angle)}[spec.yaw_only.axis]
            return euler_to_se3(about, order="zyx", intrinsic=True, degrees=self._degrees)
        except RotationError as exc:
            raise BuildError(f"{where}: {exc}") from None

    def _time_us(self, sample: Sample, record: Mapping[str, object] | None, where: str) -> int:
        clock = self.spec.clock
        if clock.source == "frame":
            if not isinstance(sample, int):
                raise BuildError(f"{where}: clock.from is frame but the sample key is {sample!r}")
            assert clock.scale_us is not None
            return sample * clock.scale_us + clock.offset_us
        if record is None:
            raise BuildError(f"{where}: clock.from is field, which needs a pose record")
        assert clock.field is not None
        raw = _num(_wrap(lambda: get_field(record, clock.field or ""), where), clock.field, where)
        if clock.unit == "frame":
            assert clock.scale_us is not None
            value = raw * clock.scale_us
        else:
            value = raw * _UNIT_US[clock.unit]
        value += clock.offset_us
        if clock.rounding == "exact":
            if value != math.floor(value):
                raise BuildError(f"{where}: {clock.field} = {raw!r} is not a whole microsecond")
            return int(value)
        return int(round(value))


def _with_sample(values: Mapping[str, object], sample: Sample) -> dict[str, object]:
    key = "frame" if isinstance(sample, int) else "sample"
    return {**values, key: sample}


def _key(sample: object) -> str:
    return str(sample)


def _count(tally: dict[str, int] | None, name: str) -> None:
    if tally is not None:
        tally[name] = tally.get(name, 0) + 1


def _scene_id(value: object) -> str:
    scene_id = str(value)
    if not scene_id or scene_id in {".", ".."} or scene_id.startswith("."):
        raise BuildError(f"refusing scene id {scene_id!r}")
    if "/" in scene_id or "\\" in scene_id:
        raise BuildError(f"refusing scene id {scene_id!r}")
    return scene_id


def _samples(raw: object, kind: str, scene_id: str) -> tuple[Sample, ...]:
    if not isinstance(raw, list) or not raw:  # type: ignore[arg-type]
        raise BuildError(f"{scene_id} lists no samples")
    out: list[Sample] = []
    for item in raw:  # type: ignore[union-attr]
        if kind == "frame":
            try:
                out.append(int(str(item)))
            except ValueError:
                raise BuildError(f"{scene_id}: frame {item!r} is not an integer") from None
        else:
            out.append(str(item))
    return tuple(out)


def _num(value: object, name: str, where: str) -> float:
    if isinstance(value, bool):
        raise BuildError(f"{where}: {name} is a bool, not a number")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            pass
    raise BuildError(f"{where}: {name} is not a number: {value!r}")


def _int(value: object, where: str) -> int:
    number = _num(value, "image size", where)
    if number != int(number) or number <= 0:
        raise BuildError(f"{where}: image size must be a positive integer")
    return int(number)


def _wrap(fn: object, where: str) -> object:
    try:
        return fn()  # type: ignore[operator]
    except (FieldError, PathError, ValueError, OSError, json.JSONDecodeError) as exc:
        raise BuildError(f"{where}: {exc}") from None
