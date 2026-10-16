# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import io
import shutil
from pathlib import Path
from typing import cast

import pytest
from coperceive.cli import main
from coperceive.model import (
    SCHEMA_VERSION,
    Agent,
    AgentType,
    Clock,
    ImageRef,
    Modality,
    Scene,
    Sensor,
    StampedPose,
)
from coperceive.se3 import SE3
from coperceive.sequence_export import (
    SequenceExportError,
    SequenceNotFound,
    export_camera_sequence,
    export_rendered_frames,
    samples_in_range,
)
from coperceive.store import read_scene, write_scene
from PIL import Image


def _png(color: tuple[int, int, int], size: tuple[int, int] = (16, 16)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def _scene(root: Path, times: tuple[int, ...] = (0, 100_000)) -> Path:
    poses = tuple(StampedPose(time_us, SE3.identity()) for time_us in times)
    agent = Agent("vehicle", AgentType.VEHICLE, poses)
    camera = Sensor(
        "front",
        "vehicle",
        Modality.CAMERA,
        SE3.identity(),
        16,
        16,
        8.0,
        8.0,
        8.0,
        8.0,
    )
    images = tuple(
        ImageRef("vehicle", "front", time_us, f"camera/front/{time_us}.png") for time_us in times
    )
    scene = Scene(
        schema_version=SCHEMA_VERSION,
        scene_id="desk",
        world_frame="ENU",
        clock=Clock(unit="us", scale_note="sequence fixture"),
        agents=(agent,),
        sensors=(camera,),
        annotations=(),
        objects=(),
        images=images,
    )
    scene_dir = root / "desk"
    write_scene(scene, scene_dir)
    folder = scene_dir / "camera" / "front"
    folder.mkdir(parents=True)
    for index, time_us in enumerate(times):
        (folder / f"{time_us}.png").write_bytes(_png((20 + index, 40, 80)))
    return scene_dir


def test_samples_in_range_is_inclusive() -> None:
    assert samples_in_range([0, 100_000, 200_000], 100_000, 200_000) == [100_000, 200_000]
    assert samples_in_range([0, 100_000], None, None) == [0, 100_000]
    with pytest.raises(SequenceExportError, match="end is before start"):
        samples_in_range([0, 100_000], 100_000, 0)


def test_png_sequence_and_gif(tmp_path: Path) -> None:
    scene_dir = _scene(tmp_path)
    scene = read_scene(scene_dir)
    directory = tmp_path / "frames"
    png = export_camera_sequence(
        scene,
        scene_dir,
        directory,
        agent_id="vehicle",
        sensor_id="front",
        start_us=0,
        end_us=100_000,
        fps=10,
        width=8,
        height=8,
        format="png",
    )
    assert png.frames == 2
    assert png.width == 8
    assert (directory / "frame_000001.png").is_file()
    assert (directory / "frame_000002.png").is_file()
    gif_path = tmp_path / "clip.gif"
    gif = export_camera_sequence(
        scene,
        scene_dir,
        gif_path,
        agent_id="vehicle",
        sensor_id="front",
        start_us=None,
        end_us=None,
        fps=10,
        width=0,
        height=0,
        format="gif",
    )
    assert gif.frames == 2
    assert gif.width == 16
    image = cast(Image.Image, Image.open(gif_path))
    try:
        count = 0
        try:
            while True:
                image.seek(count)
                count += 1
        except EOFError:
            pass
        assert count == 2
        assert image.info["duration"] == 100
    finally:
        image.close()


def test_single_png_file_and_missing_image(tmp_path: Path) -> None:
    scene_dir = _scene(tmp_path, (0,))
    scene = read_scene(scene_dir)
    dest = tmp_path / "still.png"
    result = export_camera_sequence(
        scene,
        scene_dir,
        dest,
        agent_id="vehicle",
        sensor_id="front",
        start_us=0,
        end_us=0,
        fps=10,
        width=16,
        height=16,
        format="png",
    )
    assert result.frames == 1
    assert dest.is_file()
    longer = _scene(tmp_path / "two", (0, 100_000))
    held = read_scene(longer)
    with pytest.raises(SequenceExportError, match="one frame"):
        export_camera_sequence(
            held,
            longer,
            tmp_path / "many.png",
            agent_id="vehicle",
            sensor_id="front",
            start_us=None,
            end_us=None,
            fps=10,
            width=16,
            height=16,
            format="png",
        )
    (longer / "camera" / "front" / "100000.png").unlink()
    with pytest.raises(SequenceNotFound, match="missing file"):
        export_camera_sequence(
            held,
            longer,
            tmp_path / "out.gif",
            agent_id="vehicle",
            sensor_id="front",
            start_us=None,
            end_us=None,
            fps=10,
            width=16,
            height=16,
            format="gif",
        )


def test_image_path_cannot_leave_the_scene(tmp_path: Path) -> None:
    scene_dir = _scene(tmp_path, (0,))
    outside = tmp_path / "secret.png"
    outside.write_bytes(_png((1, 2, 3)))
    leaked = Scene(
        schema_version=SCHEMA_VERSION,
        scene_id="desk",
        world_frame="ENU",
        clock=Clock(unit="us", scale_note="sequence fixture"),
        agents=(Agent("vehicle", AgentType.VEHICLE, (StampedPose(0, SE3.identity()),)),),
        sensors=(
            Sensor("front", "vehicle", Modality.CAMERA, SE3.identity(), 16, 16, 8.0, 8.0, 8.0, 8.0),
        ),
        annotations=(),
        objects=(),
        images=(ImageRef("vehicle", "front", 0, "../secret.png"),),
    )
    with pytest.raises(SequenceExportError, match="escapes"):
        export_camera_sequence(
            leaked,
            scene_dir,
            tmp_path / "leak.png",
            agent_id="vehicle",
            sensor_id="front",
            start_us=0,
            end_us=0,
            fps=10,
            width=0,
            height=0,
            format="png",
        )


def test_mp4_needs_ffmpeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _no_ffmpeg(_name: str) -> str | None:
        return None

    monkeypatch.setattr(shutil, "which", _no_ffmpeg)
    with pytest.raises(SequenceExportError, match="ffmpeg"):
        export_rendered_frames([_png((9, 9, 9))], tmp_path / "clip.mp4", fps=10, format="mp4")


def test_mp4_writes_when_ffmpeg_is_present(tmp_path: Path) -> None:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is not on PATH")
    dest = tmp_path / "clip.mp4"
    result = export_rendered_frames(
        [_png((200, 10, 10)), _png((10, 10, 200))],
        dest,
        fps=10,
        format="mp4",
    )
    assert result.frames == 2
    assert dest.is_file()
    assert dest.stat().st_size > 100


def test_rendered_view_rejects_too_many_frames(tmp_path: Path) -> None:
    frames = [_png((1, 1, 1)) for _ in range(61)]
    with pytest.raises(SequenceExportError, match="60"):
        export_rendered_frames(frames, tmp_path / "too.gif", fps=10, format="gif")


def test_cli_writes_a_gif(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    scene_dir = _scene(tmp_path)
    dest = tmp_path / "clip.gif"
    main(
        [
            "export",
            "sequence",
            str(scene_dir),
            "--out",
            str(dest),
            "--agent",
            "vehicle",
            "--sensor",
            "front",
            "--fps",
            "5",
        ]
    )
    captured = capsys.readouterr()
    assert "wrote" in captured.out
    assert "frames 2" in captured.out
    assert "fps 5" in captured.out
    assert dest.is_file()
