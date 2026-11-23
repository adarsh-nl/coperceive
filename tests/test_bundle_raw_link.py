# SPDX-License-Identifier: Apache-2.0
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportIndexIssue=false
"""A prepared cluster scene's ``raw`` camera link is left out of a bundle, not followed.

``camera_images.stage_images`` links ``<scene>/raw`` to the dataset root. An
export without sensor data leaves that one link out (never followed, read or
copied) and lists it in ``bundle.json`` under ``omitted_links``. The same link
with sensor data, any other link, and a regular file or folder at that path are
refused. The link's target never appears in the bundle or in any output.
Synthetic demo data only.
"""

from __future__ import annotations

import json
import logging
import shutil
import zipfile
from pathlib import Path

import pytest
from coperceive.bundle import BundleExportError, BundleExportRequest, export_bundle, preview_bundle
from coperceive.camera_images import RAW_LINK, stage_images
from coperceive.cli import main
from coperceive.demo import create

SCENE = "demo-junction"
CANARY = "CANARY-RAW-ROOT-7f3a"


@pytest.fixture(scope="module")
def pristine(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return create(tmp_path_factory.mktemp("raw-link-demo"), seed=0).root


@pytest.fixture
def cluster(pristine: Path, tmp_path: Path) -> tuple[Path, Path]:
    """A copy of the demo whose scene was prepared like a cluster scene: ``raw`` links out."""
    root = tmp_path / "demo"
    shutil.copytree(pristine, root, symlinks=True)
    raw_root = tmp_path / f"dataset-{CANARY}"
    shutil.copytree(root / "scenes" / SCENE / "agents", raw_root / "agents")
    (raw_root / "secret.txt").write_text(f"{CANARY} private dataset file\n", encoding="utf-8")
    stage_images(root / "scenes" / SCENE, raw_root, "originals")
    assert (root / "scenes" / SCENE / RAW_LINK).is_symlink()
    return root, raw_root


def _request(root: Path, out: Path, *, sensor: bool = False) -> BundleExportRequest:
    return BundleExportRequest(
        run_id="demo",
        scenes_root=root / "scenes",
        predictions_root=root / "predictions",
        out=out,
        dataset_id="demo",
        include_sensor_data=sensor,
        created_utc="2026-01-01T00:00:00Z",
    )


def _archive_bytes(bundle: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(bundle) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _assert_target_absent(text: str | bytes, raw_root: Path) -> None:
    data = text.encode() if isinstance(text, str) else text
    for needle in {str(raw_root), str(raw_root.resolve()), CANARY, "dataset-CANARY"}:
        assert needle.encode() not in data, needle


def test_raw_link_is_left_out_without_sensor_data(
    cluster: tuple[Path, Path], tmp_path: Path
) -> None:
    root, raw_root = cluster
    bundle = export_bundle(_request(root, tmp_path / "out" / "cluster.cpbundle"))
    members = _archive_bytes(bundle)
    link = f"scenes/{SCENE}/{RAW_LINK}"
    assert not [name for name in members if name == link or name.startswith(link + "/")]
    manifest = json.loads(members["bundle.json"])
    assert manifest["omitted_links"] == [link]
    assert link not in {item["path"] for item in manifest["contents"]}
    review = preview_bundle(_request(root, tmp_path / "review" / "cluster.cpbundle"))
    assert review["omitted_links"] == [link]
    for data in members.values():
        _assert_target_absent(data, raw_root)


def test_raw_link_with_sensor_data_is_refused(cluster: tuple[Path, Path], tmp_path: Path) -> None:
    root, _raw_root = cluster
    out = tmp_path / "out" / "cluster.cpbundle"
    with pytest.raises(BundleExportError, match="left out only when the bundle carries no sensor"):
        export_bundle(_request(root, out, sensor=True))
    assert not out.exists()


@pytest.mark.parametrize(
    "relative",
    [
        f"scenes/{SCENE}/agents/cav-a/{RAW_LINK}",  # the name, but not where stage_images puts it
        f"scenes/{SCENE}/camera-{RAW_LINK}",  # another link at the scene's top level
        f"predictions/demo/{RAW_LINK}",  # the run folder is not a scene
    ],
)
def test_any_other_link_is_still_refused(
    cluster: tuple[Path, Path], tmp_path: Path, relative: str
) -> None:
    """Refused for that link alone: without it, the same scene exports with raw left out."""
    root, raw_root = cluster
    other = root / relative
    other.symlink_to(raw_root, target_is_directory=True)
    out = tmp_path / "out" / "cluster.cpbundle"
    shown = relative.replace(".", r"\.")
    with pytest.raises(BundleExportError, match=rf"{shown} is a symbolic link"):
        export_bundle(_request(root, out))
    assert not out.exists()
    other.unlink()
    bundle = export_bundle(_request(root, out))
    manifest = json.loads(_archive_bytes(bundle)["bundle.json"])
    assert manifest["omitted_links"] == [f"scenes/{SCENE}/{RAW_LINK}"]


@pytest.mark.parametrize("kind", ["folder", "file"])
def test_a_real_folder_or_file_at_the_raw_path_is_refused(
    pristine: Path, tmp_path: Path, kind: str
) -> None:
    root = tmp_path / "demo"
    shutil.copytree(pristine, root, symlinks=True)
    raw = root / "scenes" / SCENE / RAW_LINK
    if kind == "folder":
        raw.mkdir()
        (raw / "frame.txt").write_text("not a link\n", encoding="utf-8")
    else:
        raw.write_text("not a link\n", encoding="utf-8")
    out = tmp_path / "out" / "plain.cpbundle"
    with pytest.raises(BundleExportError, match="is not the link to the raw camera folder"):
        export_bundle(_request(root, out))
    assert not out.exists()


def test_raw_link_target_never_appears_in_output_or_logs(
    cluster: tuple[Path, Path],
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, raw_root = cluster
    monkeypatch.chdir(tmp_path)  # bundle preview stages next to the working directory
    out = tmp_path / "out" / "cli.cpbundle"
    caplog.set_level(logging.DEBUG)
    args = ["--root", str(root / "scenes"), "--predictions", str(root / "predictions")]
    main(["bundle", "export", "demo", *args, "-o", str(out), "--dataset-id", "demo"])
    main(["bundle", "preview", "demo", *args, "--dataset-id", "demo"])
    captured = capsys.readouterr()
    assert "The link to the raw camera folder is not included." in captured.err
    for text in (captured.out, captured.err, caplog.text):
        _assert_target_absent(text, raw_root)
    for data in _archive_bytes(out).values():
        _assert_target_absent(data, raw_root)
    assert json.loads(captured.out[captured.out.index("{") :])["omitted_links"] == [
        f"scenes/{SCENE}/{RAW_LINK}"
    ]
