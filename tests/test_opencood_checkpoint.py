# SPDX-License-Identifier: Apache-2.0
"""The OpenCOOD plugin's checkpoint archive handling: hash, safe extraction, discovery."""

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

import pytest

from tests.opencood_plugin import config_bytes, load, write_checkpoint

checkpoint = load("checkpoint")


def test_md5_of_a_file_is_the_protocol_digest(tmp_path: Path) -> None:
    path = tmp_path / "cobevt_lidar.zip"
    path.write_bytes(b"PK\x03\x04 checkpoint bytes")
    assert checkpoint.md5_of(str(path)) == hashlib.md5(path.read_bytes()).hexdigest()  # noqa: S324
    with pytest.raises(OSError):
        checkpoint.md5_of(str(tmp_path / "missing.zip"))


def test_prepare_finds_the_one_config_and_the_one_weights_file(tmp_path: Path) -> None:
    archive = write_checkpoint(tmp_path / "cobevt_lidar.zip")
    config_path, weights_path, written = checkpoint.prepare(str(archive), str(tmp_path / "out"))
    assert config_path.endswith("cobevt-lidar/config.yaml")
    assert weights_path.endswith("cobevt-lidar/net_epoch_bestval_at_10.pth")
    assert written == [
        "cobevt-lidar/config.yaml",
        "cobevt-lidar/net_epoch_bestval_at_10.pth",
    ]
    assert Path(config_path).read_bytes() == config_bytes()


def test_ambiguous_configs_are_reported_with_candidates(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("a/one.yaml", config_bytes())
        bundle.writestr("b/other.yaml", config_bytes())
        bundle.writestr("weights.pth", b"x")
    archive = tmp_path / "ambiguous.zip"
    archive.write_bytes(buffer.getvalue())
    with pytest.raises(checkpoint.CheckpointError, match="checkpoint_config"):
        checkpoint.prepare(str(archive), str(tmp_path / "out"))
    # The hint picks one; a hint that is not in the archive is an error.
    config_path, _, _ = checkpoint.prepare(
        str(archive), str(tmp_path / "out"), config_hint="b/other.yaml"
    )
    assert config_path.endswith("b/other.yaml")
    with pytest.raises(checkpoint.CheckpointError, match="not in the archive"):
        checkpoint.prepare(str(archive), str(tmp_path / "out"), config_hint="nope.yaml")


def test_the_preferred_config_name_wins_without_a_hint(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("dataset.yaml", "name: x\n")
        bundle.writestr("config.yaml", config_bytes())
        bundle.writestr("latest.pth", b"x")
    archive = tmp_path / "two-yamls.zip"
    archive.write_bytes(buffer.getvalue())
    config_path, weights_path, _ = checkpoint.prepare(str(archive), str(tmp_path / "out"))
    assert config_path.endswith("config.yaml")
    assert weights_path.endswith("latest.pth")


def test_missing_or_ambiguous_weights_are_errors(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("config.yaml", config_bytes())
    archive = tmp_path / "no-weights.zip"
    archive.write_bytes(buffer.getvalue())
    with pytest.raises(checkpoint.CheckpointError, match="no weights"):
        checkpoint.prepare(str(archive), str(tmp_path / "out"))

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("config.yaml", config_bytes())
        bundle.writestr("a.pth", b"x")
        bundle.writestr("b.pth", b"x")
    archive = tmp_path / "two-weights.zip"
    archive.write_bytes(buffer.getvalue())
    with pytest.raises(checkpoint.CheckpointError, match="checkpoint_weights"):
        checkpoint.prepare(str(archive), str(tmp_path / "out"))


def test_archive_members_cannot_escape_the_destination(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("../escaped.txt", b"no")
    archive = tmp_path / "evil.zip"
    archive.write_bytes(buffer.getvalue())
    with pytest.raises(checkpoint.CheckpointError, match="refusing archive member"):
        checkpoint.extract(str(archive), str(tmp_path / "out"))
    assert not (tmp_path / "escaped.txt").exists()


def test_a_non_zip_checkpoint_says_what_to_check(tmp_path: Path) -> None:
    path = tmp_path / "cobevt_lidar.tar"
    path.write_bytes(b"not a zip")
    with pytest.raises(checkpoint.CheckpointError, match="unzip -l"):
        checkpoint.prepare(str(path), str(tmp_path / "out"))
    directory = tmp_path / "extracted"
    directory.mkdir()
    with pytest.raises(checkpoint.CheckpointError, match="not a file"):
        checkpoint.prepare(str(directory), str(tmp_path / "out"))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w"):
        pass
    empty = tmp_path / "empty.zip"
    empty.write_bytes(buffer.getvalue())
    with pytest.raises(checkpoint.CheckpointError, match="no files"):
        checkpoint.prepare(str(empty), str(tmp_path / "out"))


def _archive_with(tmp_path: Path, name: str, members: dict[str, bytes]) -> Path:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for member, payload in members.items():
            bundle.writestr(member, payload)
    archive = tmp_path / name
    archive.write_bytes(buffer.getvalue())
    return archive


def test_a_config_not_named_config_yaml_is_refused_with_a_hint(tmp_path: Path) -> None:
    """No guessing a differently named YAML: the error lists the YAMLs and asks for the hint."""
    archive = _archive_with(
        tmp_path,
        "no-config-name.zip",
        {"cobevt/cobevt.yaml": config_bytes(), "cobevt/best.pth": b"weights"},
    )
    with pytest.raises(checkpoint.CheckpointError, match="not named config.yaml") as caught:
        checkpoint.prepare(str(archive), str(tmp_path / "out"))
    message = str(caught.value)
    assert "cobevt/cobevt.yaml" in message
    assert "--option checkpoint_config=" in message
    # The explicit override accepts it (and only it: it is still a regular, non-empty file).
    config_path, weights_path, _ = checkpoint.prepare(
        str(archive), str(tmp_path / "out2"), config_hint="cobevt/cobevt.yaml"
    )
    assert config_path.endswith("cobevt/cobevt.yaml")
    assert weights_path.endswith("cobevt/best.pth")


def test_symlinked_and_empty_checkpoint_files_are_refused(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "config.yaml").write_bytes(config_bytes())
    real_weights = tmp_path / "real.pth"
    real_weights.write_bytes(b"weights")
    (root / "weights.pth").symlink_to(real_weights)
    with pytest.raises(checkpoint.CheckpointError, match="symbolic link"):
        checkpoint.discover(str(root))

    root = tmp_path / "root2"
    root.mkdir()
    real_config = tmp_path / "real-config.yaml"
    real_config.write_bytes(config_bytes())
    (root / "config.yaml").symlink_to(real_config)
    (root / "weights.pth").write_bytes(b"weights")
    with pytest.raises(checkpoint.CheckpointError, match="symbolic link"):
        checkpoint.discover(str(root))

    root = tmp_path / "root3"
    root.mkdir()
    (root / "config.yaml").write_bytes(config_bytes())
    (root / "weights.pth").write_bytes(b"")
    with pytest.raises(checkpoint.CheckpointError, match="is empty"):
        checkpoint.discover(str(root))

    directory = tmp_path / "weights-dir"
    directory.mkdir()
    with pytest.raises(checkpoint.CheckpointError, match="not a regular file"):
        checkpoint._regular_file(str(directory), "weights")  # noqa: SLF001 - the guard itself


def test_engine_weights_loader_defensively_refuses_bad_files(tmp_path: Path) -> None:
    engine = load("engine")
    empty = tmp_path / "empty.pth"
    empty.write_bytes(b"")
    real = tmp_path / "real.pth"
    real.write_bytes(b"weights")
    link = tmp_path / "link.pth"
    link.symlink_to(real)
    with pytest.raises(engine.EngineError, match="is empty"):
        engine._load_weights(object(), str(empty))  # noqa: SLF001 - the guards run before torch
    with pytest.raises(engine.EngineError, match="symbolic link"):
        engine._load_weights(object(), str(link))  # noqa: SLF001
    with pytest.raises(engine.EngineError, match="not a regular file"):
        engine._load_weights(object(), str(tmp_path / "missing.pth"))  # noqa: SLF001
