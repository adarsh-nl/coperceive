# SPDX-License-Identifier: Apache-2.0
"""Adapter manifest: loader, validation and the frozen spec dataclasses.

``spec_version`` 1 is the manifest format described in ADR 0009. It is not the
scene ``schema_version``. The loader uses ``yaml.safe_load``. Every key is
checked; an unknown key is an error that names its path, so a typo cannot fall
back to a default.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import cast

from coperceive.adapters.paths import PLACEHOLDERS, PathError, Template
from coperceive.adapters.readers import registry as readers
from coperceive.errors import CoperceiveError
from coperceive.model import AgentType, Modality

SPEC_VERSION = 1
_NAME = re.compile(r"^[a-z0-9_-]+$")


def _record_readers() -> tuple[str, ...]:
    return readers.names("record") + readers.names("table")


def _table_readers() -> tuple[str, ...]:
    return readers.names("table")


def _box_readers() -> tuple[str, ...]:
    return readers.BUILTIN_TEXT + readers.names("record") + readers.names("table")


def _lidar_readers() -> tuple[str, ...]:
    return readers.names("lidar")


class SpecError(CoperceiveError):
    """The manifest is malformed. The message starts with the key path."""


def builtin_manifest_path(name: str) -> Path:
    if not _NAME.match(name):
        raise SpecError(f"adapter name {name!r} is not lower-case [a-z0-9_-]")
    with resources.as_file(resources.files("coperceive.adapters.builtin") / f"{name}.yaml") as p:
        if not p.is_file():
            raise SpecError(f"no built-in adapter named {name!r}")
        return Path(p)


def builtin_manifest_names() -> tuple[str, ...]:
    folder = resources.files("coperceive.adapters.builtin")
    names = [entry.name[:-5] for entry in folder.iterdir() if entry.name.endswith(".yaml")]
    return tuple(sorted(names))


def load_manifest(path: Path) -> Manifest:
    import yaml

    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SpecError(f"{path}: {exc}") from None
    return parse_manifest(payload, source=str(path))


def parse_manifest(payload: object, *, source: str = "<manifest>") -> Manifest:
    root = _Node("", payload)
    adapter = root.str("adapter")
    if not _NAME.match(adapter):
        raise SpecError("adapter: must match [a-z0-9_-]+")
    version = root.int("spec_version")
    if version != SPEC_VERSION:
        raise SpecError(f"spec_version: this build reads {SPEC_VERSION}, manifest says {version}")
    params = _params(root.sub("params", optional=True))
    extra = frozenset(params)
    world_frame = root.str("world_frame")
    clock = _clock(root.sub("clock"))
    scenes = _scenes(root.sub("scenes"), extra)
    agents = _agents(root.any("agents"), extra)
    conventions = _conventions(root.sub("conventions"))
    streams = _streams(root.sub("streams"), extra)
    checks = _checks(root.sub("checks", optional=True), streams)
    report = _report(root.sub("report", optional=True), streams)
    root.done()
    manifest = Manifest(
        adapter=adapter,
        spec_version=version,
        source=source,
        params=params,
        world_frame=world_frame,
        clock=clock,
        scenes=scenes,
        agents=agents,
        conventions=conventions,
        streams=streams,
        checks=checks,
        report=report,
    )
    _cross_check(manifest)
    return manifest


# --- spec dataclasses -------------------------------------------------------


@dataclass(frozen=True)
class ParamSpec:
    allowed: tuple[str, ...] | None


@dataclass(frozen=True)
class ClockSpec:
    source: str  # frame | field
    field: str | None
    unit: str  # frame | s | ms | us | ns
    scale_us: int | None
    offset_us: int
    rounding: str  # exact | nearest
    scale_note: str


@dataclass(frozen=True)
class PlatformValue:
    field: str | None = None
    param: str | None = None
    const: str | None = None
    number: bool = False
    format: str | None = None


@dataclass(frozen=True)
class IndexSpec:
    file: Template
    must_equal: tuple[str, ...]
    scene_id: str
    samples: str
    sample_kind: str  # frame | sample
    platform: Mapping[str, PlatformValue]


@dataclass(frozen=True)
class GlobSpec:
    path: Template
    sample_source: str
    platform: Mapping[str, PlatformValue]


@dataclass(frozen=True)
class ScenesSpec:
    index: IndexSpec | None
    glob: GlobSpec | None


@dataclass(frozen=True)
class AgentSpec:
    id: str
    type: AgentType
    root: str


@dataclass(frozen=True)
class DiscoverSpec:
    glob: Template
    type: AgentType
    exclude: tuple[str, ...]


@dataclass(frozen=True)
class AgentsSpec:
    fixed: tuple[AgentSpec, ...]
    discover: DiscoverSpec | None


@dataclass(frozen=True)
class Conventions:
    length_unit: str  # m | cm | mm
    angle_unit: str  # deg | rad
    body_axes: str
    camera_axes: str


@dataclass(frozen=True)
class EulerSpec:
    fields: tuple[str, str, str]  # about x, about y, about z
    order: str
    intrinsic: bool


@dataclass(frozen=True)
class QuaternionSpec:
    fields: tuple[str, str, str, str]
    order: str  # wxyz | xyzw


@dataclass(frozen=True)
class MatrixSpec:
    field: str
    shape: str
    column_major: bool


@dataclass(frozen=True)
class YawOnlySpec:
    field: str
    axis: str


@dataclass(frozen=True)
class MissingAngles:
    fill: str  # zero
    reason: str


@dataclass(frozen=True)
class RotationSpec:
    euler: EulerSpec | None = None
    quaternion: QuaternionSpec | None = None
    matrix: MatrixSpec | None = None
    yaw_only: YawOnlySpec | None = None
    missing_angles: MissingAngles | None = None


@dataclass(frozen=True)
class JoinSpec:
    table: Template
    on: str
    key: str  # key in the joined table


@dataclass(frozen=True)
class PosesSpec:
    reader: str
    path: Template
    missing: str
    translation: tuple[str, str, str]
    rotation: RotationSpec
    direction: str  # child_to_parent | parent_to_child
    check: Mapping[str, str]
    sample_field: str | None
    agent_field: str | None
    joins: tuple[JoinSpec, ...]


@dataclass(frozen=True)
class ExtrinsicSpec:
    matrix: MatrixSpec | None
    translation: tuple[str, str, str] | None
    rotation: RotationSpec | None
    direction: str


@dataclass(frozen=True)
class IntrinsicSpec:
    matrix: str | None
    fields: tuple[str, str, str, str] | None
    optional: bool


@dataclass(frozen=True)
class ImageSizeSpec:
    fields: tuple[str, str] | None
    derive: str | None
    reason: str | None


@dataclass(frozen=True)
class ModalityRule:
    has_intrinsic: bool | None
    stem_prefix: str | None
    default: bool
    modality: Modality


@dataclass(frozen=True)
class SensorsSpec:
    reader: str
    path: Template
    extrinsic: ExtrinsicSpec
    intrinsic: IntrinsicSpec | None
    image_size: ImageSizeSpec | None
    modality: tuple[ModalityRule, ...]
    sensor_field: str | None
    agent_field: str | None


@dataclass(frozen=True)
class BoxesSpec:
    reader: str
    path: Template
    missing: str
    frame: str  # body | world | sensor:<id>
    variants: Mapping[int, tuple[str, ...]]
    fields: Mapping[str, str]
    records: str | None
    rotation: RotationSpec
    centre_origin: str  # centre | bottom
    track_id_cast: str  # string | int_string
    class_map: Mapping[str, str]
    class_map_unknown: str  # keep | error | drop
    fill: Mapping[str, float]
    fill_reason: str | None


@dataclass(frozen=True)
class ImagesSpec:
    for_each: str
    path: Template
    require_file: bool


@dataclass(frozen=True)
class LidarSpec:
    reader: str
    agents: tuple[str, ...] | None
    sensor: str
    path: Template
    directory: Template | None
    when_directory_absent: str  # skip | error
    missing: str
    fields: Mapping[str, str]
    intensity_fill: float | None
    intensity_reason: str | None
    stride: int | None
    offsets: Mapping[str, int]


@dataclass(frozen=True)
class Streams:
    poses: PosesSpec
    sensors: SensorsSpec
    boxes: BoxesSpec | None
    images: ImagesSpec | None
    lidar: LidarSpec | None


@dataclass(frozen=True)
class Checks:
    required_per_sample: tuple[str, ...]


@dataclass(frozen=True)
class Report:
    variant_counts: str | None


@dataclass(frozen=True)
class Manifest:
    adapter: str
    spec_version: int
    source: str
    params: Mapping[str, ParamSpec]
    world_frame: str
    clock: ClockSpec
    scenes: ScenesSpec
    agents: AgentsSpec
    conventions: Conventions
    streams: Streams
    checks: Checks
    report: Report = field(default_factory=lambda: Report(None))


# --- node walker --------------------------------------------------------------


class _Node:
    """One mapping in the manifest, with its key path for error messages."""

    def __init__(self, path: str, payload: object) -> None:
        self.path = path
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            raise SpecError(f"{path or '<root>'}: expected a mapping")
        self.payload: dict[str, object] = {str(k): v for k, v in payload.items()}  # type: ignore[union-attr]
        self.seen: set[str] = set()

    def at(self, key: str) -> str:
        return f"{self.path}.{key}" if self.path else key

    def has(self, key: str) -> bool:
        return key in self.payload and self.payload[key] is not None

    def any(self, key: str, *, optional: bool = False) -> object:
        self.seen.add(key)
        if key not in self.payload or self.payload[key] is None:
            if optional:
                return None
            raise SpecError(f"{self.at(key)}: required")
        return self.payload[key]

    def sub(self, key: str, *, optional: bool = False) -> _Node | None:
        value = self.any(key, optional=optional)
        if value is None:
            return None
        return _Node(self.at(key), value)

    def str(
        self, key: str, *, optional: bool = False, choices: tuple[str, ...] | None = None
    ) -> str:
        value = self.any(key, optional=optional)
        if value is None:
            return ""
        if not isinstance(value, str) or value == "":
            raise SpecError(f"{self.at(key)}: expected a non-empty string")
        if choices is not None and value not in choices:
            raise SpecError(f"{self.at(key)}: expected one of {', '.join(choices)}, got {value!r}")
        return value

    def opt_str(self, key: str, choices: tuple[str, ...] | None = None) -> str | None:
        if not self.has(key):
            self.seen.add(key)
            return None
        return self.str(key, choices=choices)

    def int(self, key: str, *, optional: bool = False) -> int:
        value = self.any(key, optional=optional)
        if value is None:
            return 0
        if isinstance(value, bool) or not isinstance(value, int):
            raise SpecError(f"{self.at(key)}: expected an integer")
        return value

    def opt_int(self, key: str) -> int | None:
        if not self.has(key):
            self.seen.add(key)
            return None
        return self.int(key)

    def float(self, key: str) -> float:
        value = self.any(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SpecError(f"{self.at(key)}: expected a number")
        return float(value)

    def bool(self, key: str, default: bool) -> bool:
        if not self.has(key):
            self.seen.add(key)
            return default
        value = self.any(key)
        if not isinstance(value, bool):
            raise SpecError(f"{self.at(key)}: expected true or false")
        return value

    def strs(
        self, key: str, *, optional: bool = False, count: int | None = None
    ) -> tuple[str, ...]:
        value = self.any(key, optional=optional)
        if value is None:
            return ()
        if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):  # type: ignore[union-attr]
            raise SpecError(f"{self.at(key)}: expected a list of non-empty strings")
        items = tuple(str(v) for v in value)  # type: ignore[union-attr]
        if count is not None and len(items) != count:
            raise SpecError(f"{self.at(key)}: expected {count} names, got {len(items)}")
        return items

    def str_map(self, key: str, *, optional: bool = False) -> dict[str, str]:
        node = self.sub(key, optional=optional)
        if node is None:
            return {}
        out: dict[str, str] = {}
        for k in list(node.payload):
            out[k] = node.str(k)
        node.done()
        return out

    def template(
        self, key: str, extra: frozenset[str], *, optional: bool = False
    ) -> Template | None:
        text = self.str(key, optional=optional)
        if text == "":
            return None
        try:
            return Template(text, extra)
        except PathError as exc:
            raise SpecError(f"{self.at(key)}: {exc}") from None

    def done(self) -> None:
        unknown = sorted(set(self.payload) - self.seen)
        if unknown:
            raise SpecError(f"{self.path or '<root>'}: unknown key {unknown[0]!r}")


# --- section parsers ------------------------------------------------------------


def _params(node: _Node | None) -> dict[str, ParamSpec]:
    if node is None:
        return {}
    out: dict[str, ParamSpec] = {}
    for name in list(node.payload):
        if not _NAME.match(name) or name in PLACEHOLDERS:
            raise SpecError(
                f"{node.at(name)}: param name clashes with a placeholder or is not a name"
            )
        sub = node.sub(name, optional=True)
        allowed = sub.strs("allowed", optional=True) if sub is not None else ()
        if sub is not None:
            sub.done()
        out[name] = ParamSpec(allowed=allowed or None)
    node.done()
    return out


def _clock(node: _Node | None) -> ClockSpec:
    assert node is not None
    source = node.str("from", choices=("frame", "field"))
    field_path = node.opt_str("field")
    if source == "field" and field_path is None:
        raise SpecError(f"{node.at('field')}: required when from is field")
    unit = node.str("unit", choices=("frame", "s", "ms", "us", "ns"))
    scale_us = node.opt_int("scale_us")
    if unit == "frame" and scale_us is None:
        raise SpecError(f"{node.at('scale_us')}: required when unit is frame")
    if scale_us is not None and scale_us <= 0:
        raise SpecError(f"{node.at('scale_us')}: must be positive")
    offset = node.opt_int("offset_us") or 0
    rounding = node.opt_str("rounding", choices=("exact", "nearest")) or "exact"
    note = node.str("scale_note")
    node.done()
    return ClockSpec(source, field_path, unit, scale_us, offset, rounding, note.strip())


def _platform(node: _Node | None) -> dict[str, PlatformValue]:
    if node is None:
        return {}
    out: dict[str, PlatformValue] = {}
    for key in list(node.payload):
        sub = node.sub(key)
        assert sub is not None
        value = PlatformValue(
            field=sub.opt_str("field"),
            param=sub.opt_str("param"),
            const=sub.opt_str("const"),
            number=sub.bool("number", False),
            format=sub.opt_str("format"),
        )
        sources = [v for v in (value.field, value.param, value.const) if v is not None]
        if len(sources) != 1:
            raise SpecError(f"{sub.path}: exactly one of field, param, const")
        if value.format is not None and not value.number:
            raise SpecError(f"{sub.path}: format needs number: true")
        sub.done()
        out[key] = value
    node.done()
    return out


def _scenes(node: _Node | None, extra: frozenset[str]) -> ScenesSpec:
    assert node is not None
    index = node.sub("index", optional=True)
    glob = node.sub("glob", optional=True)
    if (index is None) == (glob is None):
        raise SpecError(f"{node.path}: exactly one of index, glob")
    index_spec = None
    glob_spec = None
    if index is not None:
        file = index.template("file", extra)
        assert file is not None
        index_spec = IndexSpec(
            file=file,
            must_equal=index.strs("must_equal", optional=True),
            scene_id=index.str("scene_id"),
            samples=index.str("samples"),
            sample_kind=index.opt_str("sample_kind", choices=("frame", "sample")) or "frame",
            platform=_platform(index.sub("platform", optional=True)),
        )
        index.done()
    if glob is not None:
        path = glob.template("path", extra)
        assert path is not None
        if not path.text.endswith("/") or "scene" not in path.placeholders:
            raise SpecError(f"{glob.at('path')}: must end in / and contain {{scene}}")
        glob_spec = GlobSpec(
            path=path,
            sample_source=glob.str("sample_source", choices=("poses", "boxes", "lidar", "images")),
            platform=_platform(glob.sub("platform", optional=True)),
        )
        glob.done()
    node.done()
    return ScenesSpec(index_spec, glob_spec)


def _agent_type(node: _Node, key: str) -> AgentType:
    try:
        return AgentType.parse(node.str(key))
    except ValueError as exc:
        raise SpecError(f"{node.at(key)}: {exc}") from None


def _agents(payload: object, extra: frozenset[str]) -> AgentsSpec:
    if isinstance(payload, list):
        fixed: list[AgentSpec] = []
        for index, item in enumerate(cast(list[object], payload)):
            node = _Node(f"agents[{index}]", item)
            root = node.str("root")
            try:
                Template(root, extra)
            except PathError as exc:
                raise SpecError(f"{node.at('root')}: {exc}") from None
            fixed.append(AgentSpec(node.str("id"), _agent_type(node, "type"), root))
            node.done()
        if not fixed:
            raise SpecError("agents: list is empty")
        ids = [a.id for a in fixed]
        if len(ids) != len(set(ids)):
            raise SpecError("agents: duplicate id")
        return AgentsSpec(tuple(fixed), None)
    node = _Node("agents", payload)
    discover = node.sub("discover")
    assert discover is not None
    glob = discover.template("glob", extra)
    assert glob is not None
    if not glob.text.endswith("/") or "agent" not in glob.placeholders:
        raise SpecError(f"{discover.at('glob')}: must end in / and contain {{agent}}")
    spec = DiscoverSpec(
        glob, _agent_type(discover, "type"), discover.strs("exclude", optional=True)
    )
    discover.done()
    node.done()
    return AgentsSpec((), spec)


def _conventions(node: _Node | None) -> Conventions:
    assert node is not None
    from coperceive.adapters.rotation import AxisRemap, RotationError

    out = Conventions(
        length_unit=node.str("length_unit", choices=("m", "cm", "mm")),
        angle_unit=node.str("angle_unit", choices=("deg", "rad")),
        body_axes=node.str("body_axes"),
        camera_axes=node.str("camera_axes"),
    )
    for key in ("body_axes", "camera_axes"):
        try:
            AxisRemap.parse(getattr(out, key))
        except RotationError as exc:
            raise SpecError(f"{node.at(key)}: {exc}") from None
    node.done()
    return out


def _rotation(node: _Node | None, *, allow_missing: bool) -> RotationSpec:
    if node is None:
        raise SpecError("rotation: required")
    euler = None
    quaternion = None
    matrix = None
    yaw_only = None
    sub = node.sub("euler", optional=True)
    if sub is not None:
        fields = sub.strs("fields", count=3)
        order = sub.str("order")
        if len(order) != 3 or set(order) != {"x", "y", "z"}:
            raise SpecError(f"{sub.at('order')}: must be a permutation of xyz")
        frame = sub.str("frame", choices=("intrinsic", "extrinsic"))
        euler = EulerSpec((fields[0], fields[1], fields[2]), order, frame == "intrinsic")
        sub.done()
    sub = node.sub("quaternion", optional=True)
    if sub is not None:
        fields = sub.strs("fields", count=4)
        order = sub.opt_str("order", choices=("wxyz", "xyzw")) or "wxyz"
        quaternion = QuaternionSpec((fields[0], fields[1], fields[2], fields[3]), order)
        sub.done()
    sub = node.sub("matrix", optional=True)
    if sub is not None:
        matrix = _matrix(sub)
    sub = node.sub("yaw_only", optional=True)
    if sub is not None:
        yaw_only = YawOnlySpec(
            sub.str("field"), sub.opt_str("axis", choices=("x", "y", "z")) or "z"
        )
        sub.done()
    count = sum(1 for v in (euler, quaternion, matrix, yaw_only) if v is not None)
    if count != 1:
        raise SpecError(f"{node.path}: exactly one of euler, quaternion, matrix, yaw_only")
    missing = None
    sub = node.sub("missing_angles", optional=True)
    if sub is not None:
        if not allow_missing:
            raise SpecError(f"{sub.path}: only allowed on boxes")
        missing = MissingAngles(sub.str("fill", choices=("zero",)), sub.str("reason").strip())
        sub.done()
    node.done()
    return RotationSpec(euler, quaternion, matrix, yaw_only, missing)


def _matrix(node: _Node) -> MatrixSpec:
    spec = MatrixSpec(
        node.str("field"),
        node.str("shape", choices=("3x3", "3x4", "4x4")),
        node.opt_str("layout", choices=("row_major", "column_major")) == "column_major",
    )
    node.done()
    return spec


def _joins(node: _Node, extra: frozenset[str]) -> tuple[JoinSpec, ...]:
    raw = node.any("join", optional=True)
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raw = [raw]
    joins: list[JoinSpec] = []
    for index, item in enumerate(raw):  # type: ignore[arg-type]
        sub = _Node(f"{node.at('join')}[{index}]", item)
        table = sub.template("table", extra)
        assert table is not None
        joins.append(JoinSpec(table, sub.str("on"), sub.opt_str("key") or "token"))
        sub.done()
    if len(joins) > 3:
        raise SpecError(f"{node.at('join')}: at most three hops")
    return tuple(joins)


def _poses(node: _Node | None, extra: frozenset[str]) -> PosesSpec:
    if node is None:
        raise SpecError("streams.poses: required")
    reader = node.str("reader", choices=_record_readers())
    path = node.template("path", extra)
    assert path is not None
    translation = node.strs("translation", count=3)
    rotation = _rotation(node.sub("rotation"), allow_missing=False)
    spec = PosesSpec(
        reader=reader,
        path=path,
        missing=node.opt_str("missing", choices=("error", "skip")) or "error",
        translation=(translation[0], translation[1], translation[2]),
        rotation=rotation,
        direction=node.opt_str("direction", choices=("child_to_parent", "parent_to_child"))
        or "child_to_parent",
        check=node.str_map("check", optional=True),
        sample_field=node.opt_str("sample_field"),
        agent_field=node.opt_str("agent_field"),
        joins=_joins(node, extra),
    )
    if reader in _table_readers() and spec.sample_field is None:
        raise SpecError(f"{node.at('sample_field')}: required for a table reader")
    node.done()
    return spec


def _sensors(node: _Node | None, extra: frozenset[str]) -> SensorsSpec:
    if node is None:
        raise SpecError("streams.sensors: required")
    reader = node.str("reader", choices=_record_readers())
    path = node.template("path", extra)
    assert path is not None
    ext = node.sub("extrinsic")
    assert ext is not None
    matrix = None
    translation = None
    rotation = None
    sub = ext.sub("matrix", optional=True)
    if sub is not None:
        matrix = _matrix(sub)
    else:
        items = ext.strs("translation", count=3)
        translation = (items[0], items[1], items[2])
        rotation = _rotation(ext.sub("rotation"), allow_missing=False)
    extrinsic = ExtrinsicSpec(
        matrix,
        translation,
        rotation,
        ext.opt_str("direction", choices=("child_to_parent", "parent_to_child"))
        or "child_to_parent",
    )
    ext.done()
    intrinsic = None
    sub = node.sub("intrinsic", optional=True)
    if sub is not None:
        matrix_field = sub.opt_str("matrix")
        fields = sub.strs("fields", optional=True)
        if (matrix_field is None) == (not fields):
            raise SpecError(f"{sub.path}: exactly one of matrix, fields")
        if fields and len(fields) != 4:
            raise SpecError(f"{sub.at('fields')}: expected fx, fy, cx, cy")
        intrinsic = IntrinsicSpec(
            matrix_field,
            (fields[0], fields[1], fields[2], fields[3]) if fields else None,
            sub.bool("optional", False),
        )
        sub.done()
    image_size = None
    sub = node.sub("image_size", optional=True)
    if sub is not None:
        fields = sub.strs("fields", optional=True)
        derive = sub.opt_str("derive", choices=("principal_point_x2",))
        reason = sub.opt_str("reason")
        if (not fields) == (derive is None):
            raise SpecError(f"{sub.path}: exactly one of fields, derive")
        if fields and len(fields) != 2:
            raise SpecError(f"{sub.at('fields')}: expected width, height")
        if derive is not None and reason is None:
            raise SpecError(f"{sub.at('reason')}: required with derive")
        image_size = ImageSizeSpec((fields[0], fields[1]) if fields else None, derive, reason)
        sub.done()
    rules_raw = node.any("modality")
    if not isinstance(rules_raw, list):
        raise SpecError(f"{node.at('modality')}: expected a list of rules")
    rules: list[ModalityRule] = []
    for index, item in enumerate(cast(list[object], rules_raw)):
        sub = _Node(f"{node.at('modality')}[{index}]", item)
        has_intrinsic = sub.bool("has_intrinsic", False) if sub.has("has_intrinsic") else None
        stem_prefix = sub.opt_str("stem_prefix")
        default = sub.has("default")
        if default:
            modality_name = sub.str("default")
            sub.seen.add("modality")
        else:
            modality_name = sub.str("modality")
        try:
            modality = Modality.parse(modality_name)
        except ValueError as exc:
            raise SpecError(f"{sub.path}: {exc}") from None
        if sum(1 for v in (has_intrinsic, stem_prefix) if v is not None) + int(default) != 1:
            raise SpecError(f"{sub.path}: exactly one of has_intrinsic, stem_prefix, default")
        rules.append(ModalityRule(has_intrinsic, stem_prefix, default, modality))
        sub.done()
    if not rules or not rules[-1].default:
        raise SpecError(f"{node.at('modality')}: the last rule must be default")
    spec = SensorsSpec(
        reader,
        path,
        extrinsic,
        intrinsic,
        image_size,
        tuple(rules),
        node.opt_str("sensor_field"),
        node.opt_str("agent_field"),
    )
    if reader in _table_readers() and spec.sensor_field is None:
        raise SpecError(f"{node.at('sensor_field')}: required for a table reader")
    node.done()
    return spec


_BOX_COLUMNS = frozenset(
    {
        "category",
        "cx",
        "cy",
        "cz",
        "length",
        "width",
        "height",
        "roll",
        "pitch",
        "yaw",
        "track_id",
        "visibility",
        "qw",
        "qx",
        "qy",
        "qz",
        "_",
    }
)


def _boxes(node: _Node | None, extra: frozenset[str]) -> BoxesSpec | None:
    if node is None:
        return None
    reader = node.str("reader", choices=_box_readers())
    path = node.template("path", extra)
    assert path is not None
    variants: dict[int, tuple[str, ...]] = {}
    vnode = node.sub("variants", optional=True)
    if vnode is not None:
        for key in list(vnode.payload):
            if not key.isdigit():
                raise SpecError(f"{vnode.at(key)}: variant key must be a field count")
            columns = vnode.strs(key)
            if len(columns) != int(key):
                raise SpecError(f"{vnode.at(key)}: {len(columns)} names for a {key}-field line")
            for column in columns:
                if column not in _BOX_COLUMNS:
                    raise SpecError(f"{vnode.at(key)}: unknown box column {column!r}")
            variants[int(key)] = columns
        vnode.done()
    fields = node.str_map("fields", optional=True)
    for name in fields:
        if name not in _BOX_COLUMNS:
            raise SpecError(f"{node.at('fields')}: unknown box column {name!r}")
    if reader == "text_rows" and not variants:
        raise SpecError(f"{node.at('variants')}: required for text_rows")
    if reader != "text_rows" and not fields:
        raise SpecError(f"{node.at('fields')}: required for {reader}")
    frame = node.opt_str("frame") or "body"
    if frame not in {"body", "world"} and not frame.startswith("sensor:"):
        raise SpecError(f"{node.at('frame')}: body, world or sensor:<id>")
    fill: dict[str, float] = {}
    fnode = node.sub("fill", optional=True)
    fill_reason = None
    if fnode is not None:
        fill_reason = fnode.str("reason")
        for key in list(fnode.payload):
            if key == "reason":
                continue
            if key not in _BOX_COLUMNS:
                raise SpecError(f"{fnode.at(key)}: unknown box column")
            fill[key] = fnode.float(key)
        fnode.done()
    spec = BoxesSpec(
        reader=reader,
        path=path,
        missing=node.opt_str("missing", choices=("error", "skip")) or "error",
        frame=frame,
        variants=variants,
        fields=fields,
        records=node.opt_str("records"),
        rotation=_rotation(node.sub("rotation"), allow_missing=True),
        centre_origin=node.opt_str("centre_origin", choices=("centre", "bottom")) or "centre",
        track_id_cast=node.opt_str("track_id_cast", choices=("string", "int_string")) or "string",
        class_map=node.str_map("class_map", optional=True),
        class_map_unknown=node.opt_str("class_map_unknown", choices=("keep", "error", "drop"))
        or "keep",
        fill=fill,
        fill_reason=fill_reason,
    )
    node.done()
    return spec


def _images(node: _Node | None, extra: frozenset[str]) -> ImagesSpec | None:
    if node is None:
        return None
    path = node.template("path", extra)
    assert path is not None
    if "sensor" not in path.placeholders:
        raise SpecError(f"{node.at('path')}: must contain {{sensor}}")
    spec = ImagesSpec(
        node.opt_str("for_each", choices=("camera",)) or "camera",
        path,
        node.bool("require_file", True),
    )
    node.done()
    return spec


def _lidar(node: _Node | None, extra: frozenset[str]) -> LidarSpec | None:
    if node is None:
        return None
    reader = node.str("reader", choices=_lidar_readers())
    path = node.template("path", extra)
    assert path is not None
    agents = node.strs("agents", optional=True) if node.has("agents") else None
    if not node.has("agents"):
        node.seen.add("agents")
    fields = node.str_map("fields", optional=True)
    stride = node.opt_int("stride")
    offsets_raw = node.sub("offsets", optional=True)
    offsets: dict[str, int] = {}
    if offsets_raw is not None:
        for key in list(offsets_raw.payload):
            offsets[key] = offsets_raw.int(key)
        offsets_raw.done()
    if reader == "bin_f32":
        if stride is None or not offsets:
            raise SpecError(f"{node.path}: bin_f32 needs stride and offsets")
    elif not fields:
        raise SpecError(f"{node.at('fields')}: required for {reader}")
    intensity_fill = None
    intensity_reason = None
    sub = node.sub("intensity", optional=True)
    if sub is not None:
        intensity_fill = sub.float("fill")
        intensity_reason = sub.str("reason")
        sub.done()
    has_intensity = "intensity" in fields or "intensity" in offsets
    if has_intensity == (intensity_fill is not None):
        raise SpecError(f"{node.path}: name an intensity source or an intensity fill, not both")
    spec = LidarSpec(
        reader=reader,
        agents=agents,
        sensor=node.str("sensor"),
        path=path,
        directory=node.template("directory", extra, optional=True),
        when_directory_absent=node.opt_str("when_directory_absent", choices=("skip", "error"))
        or "error",
        missing=node.opt_str("missing", choices=("error", "skip")) or "error",
        fields=fields,
        intensity_fill=intensity_fill,
        intensity_reason=intensity_reason,
        stride=stride,
        offsets=offsets,
    )
    if spec.directory is None and spec.when_directory_absent == "skip":
        raise SpecError(f"{node.at('directory')}: required with when_directory_absent: skip")
    node.done()
    return spec


def _streams(node: _Node | None, extra: frozenset[str]) -> Streams:
    assert node is not None
    streams = Streams(
        poses=_poses(node.sub("poses"), extra),
        sensors=_sensors(node.sub("sensors"), extra),
        boxes=_boxes(node.sub("boxes", optional=True), extra),
        images=_images(node.sub("images", optional=True), extra),
        lidar=_lidar(node.sub("lidar", optional=True), extra),
    )
    node.done()
    return streams


def _checks(node: _Node | None, streams: Streams) -> Checks:
    if node is None:
        return Checks(())
    names = node.strs("required_per_sample", optional=True)
    for name in names:
        if name not in {"poses", "boxes", "lidar", "images"} or getattr(streams, name) is None:
            raise SpecError(f"{node.at('required_per_sample')}: {name!r} is not a declared stream")
    node.done()
    return Checks(names)


def _report(node: _Node | None, streams: Streams) -> Report:
    if node is None:
        return Report(None)
    name = node.opt_str("variant_counts", choices=("boxes",))
    if name is not None and (streams.boxes is None or not streams.boxes.variants):
        raise SpecError(f"{node.at('variant_counts')}: boxes has no variants")
    node.done()
    return Report(name)


def _cross_check(manifest: Manifest) -> None:
    if manifest.scenes.glob is not None:
        source = manifest.scenes.glob.sample_source
        stream = getattr(manifest.streams, source)
        if stream is None:
            raise SpecError(f"scenes.glob.sample_source: {source!r} is not a declared stream")
        if not {"frame", "sample"} & set(stream.path.placeholders):
            raise SpecError(
                f"scenes.glob.sample_source: streams.{source}.path has no {{frame}} or {{sample}}"
            )
    lidar = manifest.streams.lidar
    if lidar is not None and lidar.agents is not None and manifest.agents.fixed:
        known = {agent.id for agent in manifest.agents.fixed}
        for agent in lidar.agents:
            if agent not in known:
                raise SpecError(f"streams.lidar.agents: unknown agent {agent!r}")
    if manifest.clock.source == "frame" and manifest.scenes.index is not None:
        if manifest.scenes.index.sample_kind != "frame":
            raise SpecError("clock.from: frame needs scenes.index.sample_kind: frame")
