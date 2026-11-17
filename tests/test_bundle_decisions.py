# SPDX-License-Identifier: Apache-2.0
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportPrivateUsage=false, reportIndexIssue=false, reportArgumentType=false
"""The decided bundle behaviours, each pinned, and the safety of opening a bundle.

1. Sensor data only for demo scenes: ``generator: "coperceive demo"`` plus the
   recorded ``generic`` adapter, per scene.
2. Griffin is off the allow list (its catalog entry is ``requires_terms: true``).
3. Sensor data is opt-in, with a 50 MiB cap checked before anything is written
   on export and before anything is extracted on open.
4. A recipient's local copy is read by reference and must resolve inside a
   library root.
5. ``jsonschema`` is a test-only dev dependency.

Opening refuses zip-slip names, absolute paths, link members, oversized members
and reserved names, and writes nothing outside ``<data_root>/bundles/``.
Synthetic demo data only.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tomllib
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest
from coperceive.bundle import (
    SENSOR_MAX_BYTES,
    BundleError,
    BundleExportRequest,
    OpenLimits,
    allow_list_document,
    export_bundle,
    open_bundle,
    sensor_data_policy,
)
from coperceive.cli import build_parser, main
from coperceive.demo import create

SCENE = "demo-junction"
REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def demo_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return create(tmp_path_factory.mktemp("decisions-demo"), seed=0).root


@pytest.fixture
def demo(demo_root: Path, tmp_path: Path) -> Path:
    root = tmp_path / "demo"
    shutil.copytree(demo_root, root, symlinks=True)
    return root


def _request(root: Path, out: Path, **kwargs: object) -> BundleExportRequest:
    return BundleExportRequest(
        run_id="demo",
        scenes_root=root / "scenes",
        predictions_root=root / "predictions",
        out=out,
        created_utc="2026-01-01T00:00:00Z",
        **kwargs,  # type: ignore[arg-type]
    )


def _manifest(bundle: Path) -> dict[str, object]:
    with zipfile.ZipFile(bundle) as archive:
        return json.loads(archive.read("bundle.json"))


def _sensor_files(manifest: dict[str, object]) -> list[str]:
    return [
        item["path"]
        for item in manifest["contents"]  # type: ignore[union-attr]
        if str(item["kind"]).startswith("sensor-")
    ]


@pytest.fixture(scope="module")
def included(demo_root: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("decisions-included") / "included.cpbundle"
    return export_bundle(_request(demo_root, out, dataset_id="demo", include_sensor_data=True))


# 1. demo generator marker plus the recorded adapter, per scene ----------------


@pytest.mark.parametrize(
    ("generator", "adapter", "dataset_id", "allowed"),
    [
        ("coperceive demo", "generic", "demo", True),
        ("coperceive demo", "generic", None, True),
        (None, "generic", "demo", False),
        ("coperceive demo", None, "demo", False),
        ("coperceive demo", "opv2v", "demo", False),
        ("coperceive synthetic", "generic", "synthetic", False),
        ("Coperceive Demo", "generic", "demo", False),
        (None, None, "demo", False),
        (None, None, None, False),
    ],
)
def test_sensor_policy_needs_the_demo_generator_and_the_generic_adapter(
    generator: str | None, adapter: str | None, dataset_id: str | None, allowed: bool
) -> None:
    policy = sensor_data_policy(dataset_id, recorded_adapter=adapter, generator=generator)
    assert policy.allowed is allowed
    if allowed:
        assert policy.licence == "Apache-2.0"
    else:
        assert policy.reason


def test_demo_scene_without_the_generator_marker_omits_sensor_data(
    demo: Path, tmp_path: Path
) -> None:
    complete = demo / "scenes" / SCENE / "COMPLETE.json"
    marker = json.loads(complete.read_text(encoding="utf-8"))
    del marker["generator"]
    complete.write_text(json.dumps(marker), encoding="utf-8")
    bundle = export_bundle(
        _request(demo, tmp_path / "unmarked.cpbundle", dataset_id="demo", include_sensor_data=True)
    )
    manifest = _manifest(bundle)
    assert manifest["sensor_data"]["status"] == "omitted"
    assert "generator marker" in manifest["sensor_data"]["reason"]
    assert not _sensor_files(manifest)


def test_each_scene_proves_itself_not_only_the_first(demo: Path, tmp_path: Path) -> None:
    """A demo scene first must not let a second, non-demo scene's sensors travel."""
    from coperceive.bundle.export import _plan_sensor

    scenes = demo / "scenes"
    shutil.copytree(scenes / SCENE, scenes / "second")
    manifest = json.loads((scenes / "second" / "manifest.json").read_text(encoding="utf-8"))
    manifest["scene_id"] = "second"
    (scenes / "second" / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    complete = json.loads((scenes / "second" / "COMPLETE.json").read_text(encoding="utf-8"))
    complete.update(adapter="opv2v", scene_id="second")
    complete.pop("generator")
    (scenes / "second" / "COMPLETE.json").write_text(json.dumps(complete), encoding="utf-8")
    plan = _plan_sensor(
        _request(demo, tmp_path / "x.cpbundle", dataset_id="demo", include_sensor_data=True),
        [(SCENE, 0), ("second", 0)],
    )
    assert plan.status == "omitted"
    assert "adapter 'opv2v'" in str(plan.reason)
    assert plan.bytes == 0


# 2. Griffin is off the allow list --------------------------------------------


def test_griffin_is_refused_because_its_catalog_entry_requires_terms(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from coperceive.catalog.runner import get_dataset

    griffin = get_dataset("griffin")
    assert griffin is not None and griffin["requires_terms"] is True
    for policy in (
        sensor_data_policy("griffin"),
        sensor_data_policy("Griffin"),
        sensor_data_policy("griffin", recorded_adapter="griffin"),
    ):
        assert not policy.allowed
        assert "requires_terms" in policy.reason
    document = allow_list_document()
    assert "griffin" in document["refused"]  # type: ignore[operator]
    assert "griffin" not in json.dumps(document["allowed"]).lower()
    main(["bundle", "sensor-policy", "--dataset-id", "griffin"])
    assert json.loads(capsys.readouterr().out)["allowed"] is False


# 3. opt-in, and the 50 MiB cap before writing or extracting ------------------


def test_sensor_data_is_opt_in(demo_root: Path, tmp_path: Path) -> None:
    assert BundleExportRequest.__dataclass_fields__["include_sensor_data"].default is False
    args = build_parser().parse_args(
        ["bundle", "export", "demo", "--root", "s", "--predictions", "p", "-o", "o"]
    )
    assert args.include_sensor_data is False
    bundle = export_bundle(_request(demo_root, tmp_path / "default.cpbundle", dataset_id="demo"))
    manifest = _manifest(bundle)
    assert manifest["sensor_data"]["status"] == "omitted"
    assert manifest["sensor_data"]["reason"] == "the exporter did not opt in to sensor data"
    assert not _sensor_files(manifest)


def test_the_default_cap_is_50_mib() -> None:
    assert SENSOR_MAX_BYTES == 50 * 1024 * 1024
    assert BundleExportRequest.__dataclass_fields__["sensor_max_bytes"].default == SENSOR_MAX_BYTES
    assert OpenLimits().max_sensor_bytes == SENSOR_MAX_BYTES


def test_export_cap_refuses_before_the_staging_folder_exists(
    demo_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tempfile

    from coperceive.bundle import BundleExportError

    staged: list[str] = []
    real = tempfile.TemporaryDirectory

    def watched(*args: object, **kwargs: object) -> tempfile.TemporaryDirectory[str]:
        staged.append("staging")
        return real(*args, **kwargs)  # type: ignore[call-overload,no-any-return]

    monkeypatch.setattr("coperceive.bundle.export.tempfile.TemporaryDirectory", watched)
    with pytest.raises(BundleExportError, match="sensor-data cap"):
        export_bundle(
            _request(
                demo_root,
                tmp_path / "cap.cpbundle",
                dataset_id="demo",
                include_sensor_data=True,
                sensor_max_bytes=1024,
            )
        )
    assert staged == []
    assert not (tmp_path / "cap.cpbundle").exists()


def _files(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


def test_open_cap_refuses_before_extracting(included: Path, tmp_path: Path) -> None:
    sensor_bytes = int(_manifest(included)["sensor_data"]["bytes"])  # type: ignore[index]
    data = tmp_path / "data"
    with pytest.raises(BundleError, match="sensor-data cap"):
        open_bundle(included, data_root=data, limits=OpenLimits(max_sensor_bytes=sensor_bytes - 1))
    assert _files(data) == []
    opened = open_bundle(included, data_root=data, limits=OpenLimits(max_sensor_bytes=sensor_bytes))
    assert opened.report["sensor_data"] == "included"


def test_open_cap_counts_sensor_files_by_path_not_by_declared_kind(
    included: Path, tmp_path: Path
) -> None:
    """Relabelled sensor files (kind and status say none) still count against the cap."""

    def relabel(manifest: dict[str, object]) -> None:
        for item in manifest["contents"]:  # type: ignore[union-attr]
            if str(item["kind"]).startswith("sensor-"):
                item["kind"] = "scene-structure"
        manifest["sensor_data"].update(status="omitted", bytes=0)  # type: ignore[union-attr]

    relabelled = _rewrite_manifest(included, tmp_path / "relabelled.cpbundle", relabel)
    data = tmp_path / "data"
    with pytest.raises(BundleError, match="sensor-data cap"):
        open_bundle(relabelled, data_root=data, limits=OpenLimits(max_sensor_bytes=1024))
    assert _files(data) == []


# 4. the local copy is read by reference, inside the library roots ------------


def test_local_copy_is_referenced_inside_the_library_root(demo_root: Path, tmp_path: Path) -> None:
    bundle = export_bundle(_request(demo_root, tmp_path / "omitted.cpbundle", dataset_id="demo"))
    library = demo_root / "scenes"
    opened = open_bundle(bundle, data_root=tmp_path / "data", library_roots=[library])
    row = opened.report["scenes"][0]  # type: ignore[index]
    assert row["sensor_data"] == "local-copy"
    assert Path(row["local_copy"]).is_relative_to(library.resolve())
    assert not [p for p in _files(opened.root) if "/agents/" in p or p.endswith("points.bin")]


def test_local_copy_reached_through_a_link_out_of_the_library_is_not_used(
    demo_root: Path, tmp_path: Path
) -> None:
    bundle = export_bundle(_request(demo_root, tmp_path / "omitted.cpbundle", dataset_id="demo"))
    library = tmp_path / "library"
    library.mkdir()
    (library / SCENE).symlink_to(demo_root / "scenes" / SCENE, target_is_directory=True)
    opened = open_bundle(bundle, data_root=tmp_path / "data", library_roots=[library])
    row = opened.report["scenes"][0]  # type: ignore[index]
    assert opened.report["sensor_data"] == "omitted"
    assert row["local_copy"] is None


# 5. jsonschema is a dev dependency only ---------------------------------------


def test_jsonschema_is_a_test_only_dev_dependency() -> None:
    project = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]

    def names(requirements: list[str]) -> set[str]:
        return {
            requirement.split(">")[0].split("=")[0].split("[")[0].split(";")[0].strip().lower()
            for requirement in requirements
        }

    assert "jsonschema" not in names(project["dependencies"])
    extras = {
        extra
        for extra, requirements in project["optional-dependencies"].items()
        if "jsonschema" in names(requirements)
    }
    assert extras == {"dev"}
    importers = [
        path.relative_to(REPO).as_posix()
        for path in (REPO / "coperceive").rglob("*.py")
        if "jsonschema" in path.read_text(encoding="utf-8")
    ]
    assert importers == []


# opening: nothing outside the destination -------------------------------------


def _rewrite_manifest(
    source: Path, dest: Path, mutate: Callable[[dict[str, object]], None]
) -> Path:
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(dest, "w") as target:
        for info in original.infolist():
            data = original.read(info.filename)
            if info.filename == "bundle.json":
                manifest = json.loads(data)
                mutate(manifest)
                data = json.dumps(manifest).encode()
            target.writestr(info.filename, data)
    return dest


def _with_member(source: Path, dest: Path, name: str, data: bytes, *, mode: int = 0) -> Path:
    """A copy of ``source`` with one more member, listed in bundle.json with its hash."""
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(dest, "w") as target:
        for info in original.infolist():
            payload = original.read(info.filename)
            if info.filename == "bundle.json":
                manifest = json.loads(payload)
                manifest["contents"].append(
                    {
                        "path": name,
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "size": len(data),
                        "kind": "frame-json",
                    }
                )
                payload = json.dumps(manifest).encode()
            target.writestr(info.filename, payload)
        member = zipfile.ZipInfo(name)
        if mode:
            member.external_attr = mode << 16
        target.writestr(member, data)
    return dest


def _snapshot(root: Path, skip: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p != skip)


def _assert_refused_and_contained(bundle: Path, tmp_path: Path, match: str) -> None:
    """Refused, nothing extracted, and nothing new anywhere outside the data root."""
    data = tmp_path / "data"
    before = _snapshot(tmp_path, data)
    with pytest.raises(BundleError, match=match):
        open_bundle(bundle, data_root=data)
    assert _files(data) == []
    after = [p for p in _snapshot(tmp_path, data) if not p.startswith("data/")]
    assert after == [p for p in before if not p.startswith("data/")]
    assert not Path("/tmp/coperceive-escaped-abs.txt").exists()


@pytest.fixture(scope="module")
def plain_bundle(demo_root: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("decisions-plain") / "plain.cpbundle"
    return export_bundle(_request(demo_root, out, dataset_id="demo"))


@pytest.mark.parametrize(
    "name",
    [
        "../escaped.txt",
        "../../escaped.txt",
        "frames/../../escaped.txt",
        "frames/../../../escaped.txt",
        "..\\escaped.txt",
        "frames\\..\\..\\escaped.txt",
    ],
)
def test_zip_slip_members_are_refused(plain_bundle: Path, tmp_path: Path, name: str) -> None:
    bundle = _with_member(plain_bundle, tmp_path / "slip.cpbundle", name, b"escaped")
    _assert_refused_and_contained(bundle, tmp_path, "unsafe bundle path")


@pytest.mark.parametrize(
    "name", ["/tmp/coperceive-escaped-abs.txt", "C:/escaped.txt", "//host/escaped"]
)
def test_absolute_member_paths_are_refused(plain_bundle: Path, tmp_path: Path, name: str) -> None:
    bundle = _with_member(plain_bundle, tmp_path / "abs.cpbundle", name, b"escaped")
    _assert_refused_and_contained(bundle, tmp_path, "unsafe bundle path")


def test_a_symlink_member_is_refused(plain_bundle: Path, tmp_path: Path) -> None:
    bundle = _with_member(
        plain_bundle,
        tmp_path / "link.cpbundle",
        "frames/escaped",
        b"../../../escaped",
        mode=0o120777,
    )
    _assert_refused_and_contained(bundle, tmp_path, "symlink entry")


def test_an_oversized_member_is_refused_before_extraction(
    plain_bundle: Path, tmp_path: Path
) -> None:
    big = b"0" * 4096
    bundle = _with_member(plain_bundle, tmp_path / "big.cpbundle", "frames/big.json", big)
    data = tmp_path / "data"
    with pytest.raises(BundleError, match="too large"):
        open_bundle(bundle, data_root=data, limits=OpenLimits(max_file_bytes=len(big) - 1))
    assert _files(data) == []


def test_a_member_larger_than_its_manifest_size_is_refused(
    plain_bundle: Path, tmp_path: Path
) -> None:
    def shrink(manifest: dict[str, object]) -> None:
        for item in manifest["contents"]:  # type: ignore[union-attr]
            if item["path"] == "repro/card.json":
                item["size"] = 10

    bundle = _rewrite_manifest(plain_bundle, tmp_path / "shrunk.cpbundle", shrink)
    _assert_refused_and_contained(bundle, tmp_path, "larger than declared")


@pytest.mark.parametrize("name", ["open.json", "OPEN.JSON", "BUNDLE.JSON"])
def test_reserved_names_are_refused(plain_bundle: Path, tmp_path: Path, name: str) -> None:
    """open.json is Coperceive's own report, and BUNDLE.JSON would replace the manifest
    on a case-insensitive file system."""
    bundle = _with_member(plain_bundle, tmp_path / "reserved.cpbundle", name, b"{}")
    _assert_refused_and_contained(bundle, tmp_path, "reserved name")
