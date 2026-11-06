# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportArgumentType=false, reportPrivateUsage=false, reportIndexIssue=false, reportAttributeAccessIssue=false, reportOptionalMemberAccess=false, reportGeneralTypeIssues=false, reportUnusedFunction=false
"""Result bundles: round trip and the security refusals."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from coperceive.bundle import BundleError, BundleExportRequest, export_bundle, open_bundle
from coperceive.demo import create
from coperceive.evaluation.predictions import read_boxes, read_run_meta
from coperceive.repro.scrub import scrub_text

RUN = "demo"


@pytest.fixture(scope="module")
def demo_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return create(tmp_path_factory.mktemp("bundle-demo"), seed=0).root


@pytest.fixture(scope="module")
def bundle(demo_root: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("bundle-out") / "demo.cpbundle"
    return export_bundle(
        BundleExportRequest(
            run_id=RUN,
            scenes_root=demo_root / "scenes",
            predictions_root=demo_root / "predictions",
            out=out,
            title="Synthetic demo results",
        )
    )


def _rewrite(source: Path, dest: Path, mutate) -> Path:
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(dest, "w") as target:
        for info in original.infolist():
            data = mutate(info.filename, original.read(info.filename))
            if data is not None:
                target.writestr(info.filename, data)
    return dest


def test_round_trip_scores_boxes_and_frames(bundle: Path, tmp_path: Path, demo_root: Path) -> None:
    opened = open_bundle(bundle, data_root=tmp_path / "data")
    assert opened.manifest["schema_version"] == 1
    opened_meta = read_run_meta(opened.root / "predictions", RUN)
    original_meta = read_run_meta(demo_root / "predictions", RUN)
    assert opened_meta.model == original_meta.model
    boxes = read_boxes(opened.root / "predictions", RUN, "demo-junction")
    assert boxes
    metrics = json.loads((opened.root / "eval/demo-junction/metrics.json").read_text())
    assert metrics["outputs"]
    assert list((opened.root / "frames").glob("*.png"))
    assert list((opened.root / "frames").glob("*.json"))
    # Opening the same bundle again is idempotent and read-only.
    assert open_bundle(bundle, data_root=tmp_path / "data").root == opened.root


def test_every_file_has_a_hash_and_sizes_are_listed(bundle: Path) -> None:
    with zipfile.ZipFile(bundle) as archive:
        manifest = json.loads(archive.read("bundle.json"))
        for item in manifest["contents"]:
            data = archive.read(item["path"])
            assert len(data) == item["size"]
            assert len(item["sha256"]) == 64
    assert manifest["privacy"]["scrubbed"] is True


def test_tampered_file_fails_hash(bundle: Path, tmp_path: Path) -> None:
    def mutate(name: str, data: bytes) -> bytes:
        return data + b"\n" if name.endswith("frame-01.json") else data

    tampered = _rewrite(bundle, tmp_path / "tampered.cpbundle", mutate)
    with pytest.raises(BundleError, match="hash or size mismatch"):
        open_bundle(tampered, data_root=tmp_path / "data")


def test_newer_schema_is_a_clear_message(bundle: Path, tmp_path: Path) -> None:
    def mutate(name: str, data: bytes) -> bytes:
        if name != "bundle.json":
            return data
        payload = json.loads(data)
        payload["schema_version"] = 99
        return json.dumps(payload).encode()

    newer = _rewrite(bundle, tmp_path / "newer.cpbundle", mutate)
    with pytest.raises(BundleError, match="newer Coperceive"):
        open_bundle(newer, data_root=tmp_path / "data")


def _bad_zip(path: Path, name: str, data: bytes = b"x") -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        manifest = {
            "schema": "coperceive-result-bundle",
            "schema_version": 1,
            "created_utc": "2026-01-01T00:00:00Z",
            "app_version": "0.1.2",
            "title": "bad",
            "run_id": "demo",
            "contents": [],
            "licences": [],
            "privacy": {"scrubbed": True},
        }
        archive.writestr("bundle.json", json.dumps(manifest))
        info = zipfile.ZipInfo(name)
        archive.writestr(info, data)
    return path


@pytest.mark.parametrize("name", ["/absolute", "../parent", "a/../../b", "dir\\file", "a//b"])
def test_zip_slip_names_are_refused(name: str, tmp_path: Path) -> None:
    path = _bad_zip(tmp_path / "bad.cpbundle", name)
    with pytest.raises(BundleError, match="unsafe|not listed|does not list"):
        open_bundle(path, data_root=tmp_path / "data")


def test_symlink_and_duplicate_entries_are_refused(bundle: Path, tmp_path: Path) -> None:
    symlink_path = tmp_path / "symlink.cpbundle"
    with zipfile.ZipFile(bundle) as source, zipfile.ZipFile(symlink_path, "w") as target:
        for item in source.infolist():
            info = zipfile.ZipInfo(item.filename)
            info.external_attr = (0o120777) << 16
            target.writestr(info, source.read(item.filename))
    with pytest.raises(BundleError, match="symlink"):
        open_bundle(symlink_path, data_root=tmp_path / "data")

    duplicate_path = tmp_path / "duplicate.cpbundle"
    with zipfile.ZipFile(bundle) as source, zipfile.ZipFile(duplicate_path, "w") as target:
        for item in source.infolist():
            target.writestr(item.filename, source.read(item.filename))
        target.writestr("repro/card.json", source.read("repro/card.json"))
    with pytest.raises(BundleError, match="duplicate"):
        open_bundle(duplicate_path, data_root=tmp_path / "data")


def test_unlisted_file_and_encrypted_entry_are_refused(bundle: Path, tmp_path: Path) -> None:
    with (
        zipfile.ZipFile(bundle) as source,
        zipfile.ZipFile(tmp_path / "unlisted.cpbundle", "w") as target,
    ):
        for item in source.infolist():
            target.writestr(item.filename, source.read(item.filename))
        target.writestr("extra.txt", b"not in manifest")
    with pytest.raises(BundleError, match="unlisted"):
        open_bundle(tmp_path / "unlisted.cpbundle", data_root=tmp_path / "data")


def test_identifier_planted_in_title_is_scrubbed(tmp_path: Path, demo_root: Path) -> None:
    out = export_bundle(
        BundleExportRequest(
            run_id=RUN,
            scenes_root=demo_root / "scenes",
            predictions_root=demo_root / "predictions",
            out=tmp_path / "scrub.cpbundle",
            title="zoe@node17.lab.example /home/zoe",
        )
    )
    with zipfile.ZipFile(out) as archive:
        text = archive.read("bundle.json").decode()
    assert "zoe" not in text
    assert "/home/" not in text


def test_scrubber_handles_free_text(monkeypatch: pytest.MonkeyPatch) -> None:
    import getpass
    import socket

    monkeypatch.setattr(getpass, "getuser", lambda: "zoe")
    monkeypatch.setattr(socket, "gethostname", lambda: "node17.lab.example")
    monkeypatch.setattr(socket, "getfqdn", lambda: "node17.lab.example")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/home/zoe")))
    text = scrub_text("user zoe at node17.lab.example, /home/zoe, ghp_aaaaaaaaaaaaaaaaaaaa")
    assert "zoe" not in text
    assert "node17" not in text
    assert "/home/" not in text
