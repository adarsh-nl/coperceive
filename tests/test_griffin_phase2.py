# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import socket
import zipfile
from pathlib import Path

import pytest
from coperceive.griffin.convert import ConvertError, convert_griffin
from coperceive.griffin.download import (
    ChecksumError,
    DiskSpaceError,
    DownloadReport,
    HfHub,
    PlannedFile,
    RemoteFile,
    download_griffin,
    required_free_bytes,
    resolve_hf_token,
)
from coperceive.griffin.licence import LicenceError
from coperceive.model import AgentType
from coperceive.store import read_scene

from tests.griffin_reference import load_scene
from tests.griffin_release import write_release as _release

_README = "---\nlicense: mit\n---\nGriffin\n"
_LICENSE = "MIT License\n\nCopyright (c) [2025] [Jiahao Wang]\nPermission is hereby granted\n"
_FOLDER_25 = "griffin_50scenes_25m"


@pytest.fixture(autouse=True)
def _offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("HF_TOKEN", raising=False)

    def fail(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("unit test opened a socket")

    monkeypatch.setattr(socket, "create_connection", fail)


class FakeHub:
    def __init__(
        self,
        files: dict[str, bytes],
        *,
        sizes: dict[str, int] | None = None,
        readme: str = _README,
        license_text: str = _LICENSE,
    ) -> None:
        self.files = files
        self.sizes = sizes or {path: len(data) for path, data in files.items()}
        self.readme = readme
        self.license_text = license_text
        self.downloads: list[str] = []

    def list_files(self, folder: str) -> list[RemoteFile]:
        prefix = f"datasets/{folder}/"
        return [
            RemoteFile(path=path, size=self.sizes[path])
            for path in sorted(self.files)
            if path.startswith(prefix)
        ]

    def download(self, filename: str, dest: Path) -> None:
        self.downloads.append(filename)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.files[filename])

    def read_dataset_card(self) -> str:
        return self.readme

    def read_toolkit_license(self) -> str:
        return self.license_text


def _md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def _zip_bytes(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def _md5_text(rows: list[tuple[str, bytes]]) -> bytes:
    lines = [f"{_md5(payload)}  ./{name}" for name, payload in rows]
    lines.append(f"{'0' * 32}  ./md5.txt")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _dev_hub(sizes: dict[str, int] | None = None) -> FakeHub:
    drone = _zip_bytes({"griffin-release/drone-side/keep.txt": b"drone"})
    vehicle = _zip_bytes({"vehicle-side/keep.txt": b"vehicle"})
    lidar = _zip_bytes({"nested/vehicle-side/lidar/lidar_top/000000.ply": b"ply"})
    folder = f"datasets/{_FOLDER_25}"
    files = {
        f"{folder}/drone_metadata.zip": drone,
        f"{folder}/vehicle_metadata.zip": vehicle,
        f"{folder}/vehicle_lidar.zip": lidar,
    }
    md5 = _md5_text(
        [
            ("drone_metadata.zip", drone),
            ("vehicle_metadata.zip", vehicle),
            ("vehicle_lidar.zip", lidar),
        ]
    )
    files[f"{folder}/md5.txt"] = md5
    return FakeHub(files, sizes=sizes)


def _download(tmp_path: Path, hub: FakeHub, **kwargs: object) -> DownloadReport:
    return download_griffin(
        tmp_path / "dest",
        subset="25m",
        preset="dev",
        accept_terms=True,
        hub=hub,
        **kwargs,  # type: ignore[arg-type]
    )


def test_required_free_bytes_counts_extract_and_reassembly() -> None:
    single = (
        PlannedFile("datasets/x/md5.txt", 10, None, "md5"),
        PlannedFile("datasets/x/a.zip", 100, "a", "zip"),
    )
    assert required_free_bytes(single, extract=False) == 110
    assert required_free_bytes(single, extract=True) == 210
    split = (
        PlannedFile("datasets/x/md5.txt", 10, None, "md5"),
        PlannedFile("datasets/x/a.z01", 50, "a", "part"),
        PlannedFile("datasets/x/a.zip", 50, "a", "zip"),
    )
    assert required_free_bytes(split, extract=True) == 310


def test_dry_run_prints_tree_sizes_and_writes_nothing(tmp_path: Path) -> None:
    hub = _dev_hub()
    hub.sizes = {path: 1000 + index for index, path in enumerate(sorted(hub.files))}
    distinctive = 1000
    report = download_griffin(
        tmp_path / "dest",
        subset="25m",
        preset="dev",
        dry_run=True,
        accept_terms=True,
        hub=hub,
    )
    assert report.dry_run
    assert {item.size for item in report.files} == set(hub.sizes.values())
    assert distinctive in {item.size for item in report.files}
    assert "mit" in report.notice
    assert not (tmp_path / "dest").exists()
    assert not (tmp_path / "xdg" / "coperceive" / "griffin-licence-accepted").exists()
    assert hub.downloads == []


def test_download_requires_acknowledgement_once(tmp_path: Path) -> None:
    hub = _dev_hub()
    with pytest.raises(LicenceError):
        download_griffin(tmp_path / "dest", subset="25m", preset="dev", hub=hub)
    assert hub.downloads == []
    assert not (tmp_path / "dest").exists()
    report = _download(tmp_path, hub)
    ack = tmp_path / "xdg" / "coperceive" / "griffin-licence-accepted"
    text = ack.read_text(encoding="utf-8")
    assert "MIT" in text
    assert "HF_TOKEN" not in text
    assert report.release_root is not None
    assert (report.release_root / "drone-side" / "keep.txt").read_bytes() == b"drone"
    ply = report.release_root / "vehicle-side" / "lidar" / "lidar_top" / "000000.ply"
    assert ply.read_bytes() == b"ply"
    note = (tmp_path / "dest" / _FOLDER_25 / "DOWNLOAD.txt").read_text(encoding="utf-8")
    assert "self-line" in note
    assert "HF_TOKEN" not in note


def test_refuses_a_non_mit_card(tmp_path: Path) -> None:
    hub = _dev_hub()
    hub.readme = "---\nlicense: apache-2.0\n---\n"
    with pytest.raises(LicenceError, match="apache-2.0"):
        _download(tmp_path, hub)


def test_disk_check_runs_before_download(tmp_path: Path) -> None:
    hub = _dev_hub()
    with pytest.raises(DiskSpaceError, match="need"):
        _download(tmp_path, hub, free_bytes=lambda _path: 0)
    assert hub.downloads == []


def test_resume_skips_matching_files_and_redownloads_a_bad_one(tmp_path: Path) -> None:
    hub = _dev_hub()
    _download(tmp_path, hub)
    hub.downloads.clear()
    again = _download(tmp_path, hub)
    assert hub.downloads == []
    assert again.skipped
    archive = tmp_path / "dest" / _FOLDER_25 / "archives" / "vehicle_lidar.zip"
    archive.write_bytes(b"corrupt")
    hub.downloads.clear()
    _download(tmp_path, hub)
    assert hub.downloads == [f"datasets/{_FOLDER_25}/vehicle_lidar.zip"]
    assert archive.read_bytes() == hub.files[f"datasets/{_FOLDER_25}/vehicle_lidar.zip"]


def test_md5_mismatch_on_the_downloaded_bytes(tmp_path: Path) -> None:
    hub = _dev_hub()
    hub.files[f"datasets/{_FOLDER_25}/vehicle_lidar.zip"] = b"not-the-listed-bytes"
    with pytest.raises(ChecksumError, match="vehicle_lidar.zip"):
        _download(tmp_path, hub)


def test_multipart_reassembly_and_zip_slip(tmp_path: Path) -> None:
    payload = _zip_bytes({"wrapper/griffin-release/vehicle-side/keep.txt": b"joined"})
    first, second = payload[: len(payload) // 2], payload[len(payload) // 2 :]
    folder = "datasets/griffin_100scenes_random"
    rows = [("camera.z01", first), ("camera.zip", second)]
    files = {
        f"{folder}/md5.txt": _md5_text(rows),
        f"{folder}/camera.z01": first,
        f"{folder}/camera.zip": second,
    }
    hub = FakeHub(files)
    report = download_griffin(
        tmp_path / "dest",
        subset="random",
        components="camera",
        accept_terms=True,
        hub=hub,
    )
    assert report.release_root is not None
    assert (report.release_root / "vehicle-side" / "keep.txt").read_bytes() == b"joined"
    reassembled = tmp_path / "dest" / "griffin_100scenes_random" / "reassembled" / "camera.zip"
    assert reassembled.read_bytes() == payload

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("../evil.txt", b"nope")
        archive.writestr("vehicle-side/ok.txt", b"ok")
    bad = buffer.getvalue()
    bad_folder = f"datasets/{_FOLDER_25}"
    bad_files = {
        f"{bad_folder}/md5.txt": _md5_text([("vehicle_metadata.zip", bad)]),
        f"{bad_folder}/vehicle_metadata.zip": bad,
    }
    with pytest.raises(ValueError, match="escapes"):
        download_griffin(
            tmp_path / "slip",
            subset="25m",
            components="vehicle_metadata",
            accept_terms=True,
            hub=FakeHub(bad_files),
        )
    assert not (tmp_path / "evil.txt").exists()


def test_token_resolution_and_hub_argument(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"keyring": False}

    def keyring_get() -> str:
        called["keyring"] = True
        return "from-keyring"

    assert resolve_hf_token(environ={"HF_TOKEN": "from-env"}, keyring_get=keyring_get) == "from-env"
    assert called["keyring"] is False
    assert resolve_hf_token(environ={}, keyring_get=keyring_get) == "from-keyring"

    def boom() -> str:
        raise RuntimeError("HF_TOKEN=from-env should not surface")

    assert resolve_hf_token(environ={}, keyring_get=boom) is False
    assert resolve_hf_token(environ={}, keyring_get=lambda: None) is False

    seen: dict[str, object] = {}

    class Recording:
        def __init__(self, token: str | bool) -> None:
            seen["token"] = token
            self.inner = _dev_hub()

        def list_files(self, folder: str) -> list[RemoteFile]:
            return self.inner.list_files(folder)

        def download(self, filename: str, dest: Path) -> None:
            self.inner.download(filename, dest)

        def read_dataset_card(self) -> str:
            return self.inner.read_dataset_card()

        def read_toolkit_license(self) -> str:
            return self.inner.read_toolkit_license()

    import coperceive.griffin.download as download_module

    monkeypatch.setattr(download_module, "HfHub", Recording)
    download_griffin(
        tmp_path / "dest",
        subset="25m",
        preset="dev",
        dry_run=True,
        token="test-token",
    )
    assert seen["token"] == "test-token"
    download_griffin(tmp_path / "other", subset="25m", preset="dev", dry_run=True, token=False)
    assert seen["token"] is False


def test_hf_hub_forwards_token_and_lfs_size(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, object] = {}

    class Lfs:
        size = 50

    class Entry:
        path = f"datasets/{_FOLDER_25}/md5.txt"
        size = 4
        lfs = Lfs()

    class Api:
        def list_repo_tree(self, *_args: object, **kwargs: object) -> list[Entry]:
            calls["list_token"] = kwargs.get("token")
            return [Entry()]

    def fake_download(**kwargs: object) -> str:
        calls["download_token"] = kwargs.get("token")
        blob = tmp_path / "blob"
        blob.write_bytes(b"abcd")
        return str(blob)

    def fake_urlopen(_url: object, timeout: int = 0) -> object:
        calls["license"] = True

        class Response:
            def read(self) -> bytes:
                return _LICENSE.encode("utf-8")

            def __enter__(self) -> Response:
                return self

            def __exit__(self, *_exc: object) -> None:
                return None

        return Response()

    import urllib.request

    import huggingface_hub

    monkeypatch.setattr(huggingface_hub, "HfApi", lambda: Api())
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", fake_download)
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    hub = HfHub("test-token")
    listed = hub.list_files(_FOLDER_25)
    assert listed[0].size == 50
    assert calls["list_token"] == "test-token"
    dest = tmp_path / "md5.txt"
    hub.download(listed[0].path, dest)
    assert dest.read_bytes() == b"abcd"
    assert calls["download_token"] == "test-token"
    assert "MIT License" in hub.read_toolkit_license()
    card = tmp_path / "README.md"
    card.write_text(_README, encoding="utf-8")

    def card_download(**kwargs: object) -> str:
        calls["card_token"] = kwargs.get("token")
        return str(card)

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", card_download)
    assert "license: mit" in hub.read_dataset_card()
    assert calls["card_token"] == "test-token"


def test_convert_is_idempotent_and_records_platform(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    _release(raw, {"Town03-000": [11185, 11186]})
    out = tmp_path / "out"
    report = convert_griffin(raw, out, subset="25m", workers=1)
    assert report.written == ("Town03-000",)
    assert report.label_fields == ((12, 4),)
    scene = read_scene(out / "Town03-000")
    assert {agent.agent_id for agent in scene.agents} == {"vehicle", "drone"}
    assert scene.agent("drone").agent_type is AgentType.UAV
    platform = dict(scene.agent("vehicle").platform)
    assert platform["subset"] == "25m"
    assert platform["map_name"] == "Carla/Maps/Town03"
    assert platform["cruise_altitude_m"] == "25.5"
    assert scene.point_clouds[0].count == 1
    stamp = (out / "Town03-000" / "manifest.json").stat().st_mtime_ns
    second = convert_griffin(raw, out, subset="25m", workers=1)
    assert second.skipped == ("Town03-000",)
    assert (out / "Town03-000" / "manifest.json").stat().st_mtime_ns == stamp


def test_convert_resumes_a_finished_partial_and_rebuilds_junk(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    _release(raw, {"Town03-000": [1, 2]})
    out = tmp_path / "out"
    convert_griffin(raw, out, subset="25m", workers=1)
    finished = out / "Town03-000"
    partial = out / ".partial" / "Town03-000"
    partial.parent.mkdir(exist_ok=True)
    finished.rename(partial)
    stamp = (partial / "manifest.json").stat().st_mtime_ns
    resumed = convert_griffin(raw, out, subset="25m", workers=1)
    assert resumed.resumed == ("Town03-000",)
    assert (out / "Town03-000" / "manifest.json").stat().st_mtime_ns == stamp
    assert not partial.exists()

    junk = out / ".partial" / "Town03-000"
    junk.mkdir(parents=True)
    (junk / "junk.txt").write_text("partial", encoding="utf-8")
    shutil.rmtree(out / "Town03-000")
    rebuilt = convert_griffin(raw, out, subset="25m", workers=1)
    assert rebuilt.written == ("Town03-000",)
    assert not (junk / "junk.txt").exists()
    assert (out / "Town03-000" / "COMPLETE.json").is_file()


def test_convert_keeps_successful_scenes_when_one_fails(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    _release(raw, {"Town03-000": [1], "Town03-001": [2]}, drop_pose=("drone-side", "Town03-001", 2))
    out = tmp_path / "out"
    with pytest.raises(ConvertError) as caught:
        convert_griffin(raw, out, subset="25m", workers=2)
    assert (out / "Town03-000" / "manifest.json").is_file()
    assert not (out / "Town03-001").exists()
    assert not (out / ".partial" / "Town03-001").exists()
    assert any(error.startswith("Town03-001:") for error in caught.value.report.errors)
    pose = {
        "x": 0.0,
        "y": 0.0,
        "z": 30.0,
        "roll": 0.0,
        "pitch": 0.0,
        "yaw": 0.0,
        "timestamp": 2,
    }
    (raw / "drone-side" / "pose" / "000002.json").write_text(json.dumps(pose), encoding="utf-8")
    report = convert_griffin(raw, out, subset="25m", workers=2)
    assert report.skipped == ("Town03-000",)
    assert report.written == ("Town03-001",)


def test_convert_requires_a_cloud_per_frame_when_lidar_exists(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    _release(raw, {"Town03-000": [1, 2]}, lidar_frames={1})
    with pytest.raises(ConvertError, match="missing"):
        convert_griffin(raw, tmp_path / "out", subset="25m", workers=1)
    raw_meta = tmp_path / "meta"
    _release(raw_meta, {"Town03-000": [3]}, lidar=False)
    report = convert_griffin(raw_meta, tmp_path / "meta-out", subset="25m", workers=1)
    assert report.written == ("Town03-000",)
    assert read_scene(tmp_path / "meta-out" / "Town03-000").point_clouds == ()


def test_cli_download_and_convert(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    hub = _dev_hub(sizes={path: 14384997 for path in _dev_hub().files})
    import coperceive.griffin.download as download_module
    from coperceive.cli import main

    monkeypatch.setattr(download_module, "HfHub", lambda _token: hub)
    main(
        [
            "download",
            "griffin",
            "--subset",
            "25m",
            "--preset",
            "dev",
            "--dest",
            str(tmp_path / "dest"),
            "--dry-run",
        ]
    )
    assert "14384997" in capsys.readouterr().out
    raw = tmp_path / "raw"
    _release(raw, {"Town03-000": [4]})
    main(
        [
            "convert",
            "griffin",
            "--subset",
            "25m",
            "--raw-root",
            str(raw),
            "--out",
            str(tmp_path / "out"),
            "--workers",
            "1",
        ]
    )
    assert (tmp_path / "out" / "Town03-000" / "manifest.json").is_file()


@pytest.mark.slow
def test_real_griffin_release_loads() -> None:
    raw = os.environ.get("COPERCEIVE_GRIFFIN_RAW")
    if not raw:
        pytest.skip("COPERCEIVE_GRIFFIN_RAW is not set")
    root = Path(raw)
    infos = json.loads((root / "vehicle-side" / "scene_infos.json").read_text(encoding="utf-8"))
    scene = load_scene(root, str(infos[0]["name"]))
    assert {agent.agent_id for agent in scene.agents} == {"vehicle", "drone"}
    assert scene.agent("drone").agent_type is AgentType.UAV
    assert scene.point_clouds
