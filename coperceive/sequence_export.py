# SPDX-License-Identifier: Apache-2.0
"""Write camera images, or already-rendered frames, to MP4, GIF, or PNG.

Each recorded sample in the inclusive time range is one output frame. ``fps``
is how fast the file plays those frames. It does not drop or repeat samples.
On a 10 Hz recording, 10 fps is the recorded pace.

The camera path reads the files named in the scene and keeps them inside the
scene directory. The page can also post bitmaps of the live 3D view; those
are encoded by the same writer. MP4 needs ``ffmpeg`` on ``PATH``. ``shell``
is never true. GIF and PNG use Pillow.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image
from PIL.Image import Image as PilImage

from coperceive.model import Scene
from coperceive.viewdata import image_path, pose_times

MAX_FPS = 60
MAX_EDGE = 1920
MAX_FRAMES = 600
MAX_UPLOAD_FRAMES = 60
MAX_FRAME_BYTES = 8_000_000
FORMATS = frozenset({"mp4", "gif", "png"})


class SequenceExportError(Exception):
    """The sequence could not be written."""


class SequenceNotFound(SequenceExportError):
    """A sample in the range has no image file."""


@dataclass(frozen=True)
class SequenceExport:
    """What was written."""

    dest: Path
    frames: int
    width: int
    height: int
    fps: int
    format: str


def samples_in_range(
    times: tuple[int, ...] | list[int],
    start_us: int | None,
    end_us: int | None,
) -> list[int]:
    """Inclusive recorded times. An omitted bound uses the first or last sample."""
    if not times:
        raise SequenceExportError("the scene has no samples")
    start = times[0] if start_us is None else start_us
    end = times[-1] if end_us is None else end_us
    if type(start) is not int or type(end) is not int:
        raise SequenceExportError("start and end must be integers")
    if end < start:
        raise SequenceExportError("end is before start")
    chosen = [time_us for time_us in times if start <= time_us <= end]
    if not chosen:
        raise SequenceExportError("no samples in that range")
    if len(chosen) > MAX_FRAMES:
        raise SequenceExportError(f"a sequence can hold at most {MAX_FRAMES} frames")
    return chosen


def export_camera_sequence(
    scene: Scene,
    scene_dir: Path,
    dest: Path,
    *,
    agent_id: str,
    sensor_id: str,
    start_us: int | None,
    end_us: int | None,
    fps: int,
    width: int,
    height: int,
    format: str,
) -> SequenceExport:
    """Resize one camera's images and write ``dest``."""
    checked = _check_fps(fps)
    kind = _check_format(format)
    times = samples_in_range(list(pose_times(scene)), start_us, end_us)
    frames, out_w, out_h = _camera_frames(
        scene,
        scene_dir,
        agent_id,
        sensor_id,
        times,
        width,
        height,
    )
    _write(frames, dest, kind, checked)
    return SequenceExport(dest, len(frames), out_w, out_h, checked, kind)


def export_rendered_frames(
    frames: list[bytes],
    dest: Path,
    *,
    fps: int,
    format: str,
) -> SequenceExport:
    """Encode bitmaps the page already rendered. At most 60 frames."""
    checked = _check_fps(fps)
    kind = _check_format(format)
    if len(frames) == 0:
        raise SequenceExportError("no frames")
    if len(frames) > MAX_UPLOAD_FRAMES:
        raise SequenceExportError(f"this view accepts at most {MAX_UPLOAD_FRAMES} frames")
    prepared: list[bytes] = []
    size: tuple[int, int] | None = None
    for blob in frames:
        if len(blob) > MAX_FRAME_BYTES:
            raise SequenceExportError("a frame is larger than 8 MB")
        image = _open_rgb(blob)
        if image.width > MAX_EDGE or image.height > MAX_EDGE:
            raise SequenceExportError(f"a frame is larger than {MAX_EDGE} px")
        if size is None:
            size = _even_size(image.width, image.height)
        prepared.append(_png_bytes(image, size))
    if size is None:
        raise SequenceExportError("no frames")
    _write(prepared, dest, kind, checked)
    return SequenceExport(dest, len(prepared), size[0], size[1], checked, kind)


def _camera_frames(
    scene: Scene,
    scene_dir: Path,
    agent_id: str,
    sensor_id: str,
    times: list[int],
    width: int,
    height: int,
) -> tuple[list[bytes], int, int]:
    frames: list[bytes] = []
    size: tuple[int, int] | None = None
    root = scene_dir.resolve()
    for time_us in times:
        relative = image_path(scene, time_us, agent_id, sensor_id)
        if relative is None:
            raise SequenceNotFound(f"no image at {time_us} µs")
        target = _confine(root, relative)
        if not target.is_file():
            raise SequenceNotFound(f"missing file at {time_us} µs")
        blob = target.read_bytes()
        image = _open_rgb(blob)
        if size is None:
            size = _resolve_size(image.width, image.height, width, height)
        frames.append(_png_bytes(image, size))
    if size is None:
        raise SequenceExportError("no frames")
    return frames, size[0], size[1]


def _write(frames: list[bytes], dest: Path, kind: str, fps: int) -> None:
    if kind == "png":
        _write_png(frames, dest)
    elif kind == "gif":
        _write_gif(frames, dest, fps)
    else:
        _write_mp4(frames, dest, fps)


def _write_png(frames: list[bytes], dest: Path) -> None:
    if dest.suffix.lower() == ".png":
        if len(frames) != 1:
            raise SequenceExportError(
                "a .png path holds one frame; pass a directory for a sequence"
            )
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(frames[0])
        return
    if dest.exists() and not dest.is_dir():
        raise SequenceExportError("png sequence destination is not a directory")
    dest.mkdir(parents=True, exist_ok=True)
    for leftover in dest.glob("frame_*.png"):
        leftover.unlink()
    for index, blob in enumerate(frames, start=1):
        (dest / f"frame_{index:06d}.png").write_bytes(blob)


def _write_gif(frames: list[bytes], dest: Path, fps: int) -> None:
    opened = [_open_rgb(blob) for blob in frames]
    dest.parent.mkdir(parents=True, exist_ok=True)
    duration = max(1, round(1000 / fps))
    first = opened[0]
    rest = opened[1:]
    first.save(
        dest,
        format="GIF",
        save_all=True,
        append_images=rest,
        duration=duration,
        loop=0,
    )
    for image in opened:
        image.close()


def _write_mp4(frames: list[bytes], dest: Path, fps: int) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise SequenceExportError("ffmpeg is not on PATH. GIF and PNG export do not need it.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="coperceive-seq-") as directory:
        folder = Path(directory)
        for index, blob in enumerate(frames):
            (folder / f"{index:06d}.png").write_bytes(blob)
        completed = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-framerate",
                str(fps),
                "-i",
                str(folder / "%06d.png"),
                "-an",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(dest),
            ],
            shell=False,
            check=False,
            capture_output=True,
            timeout=180,
        )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace")[-400:]
        raise SequenceExportError(f"ffmpeg failed: {detail}")


def _check_fps(fps: int) -> int:
    if type(fps) is not int or fps < 1 or fps > MAX_FPS:
        raise SequenceExportError(f"fps must be an integer from 1 to {MAX_FPS}")
    return fps


def _check_format(format: str) -> str:
    if format not in FORMATS:
        raise SequenceExportError("format must be mp4, gif, or png")
    return format


def _resolve_size(src_w: int, src_h: int, width: int, height: int) -> tuple[int, int]:
    if width == 0 and height == 0:
        width, height = src_w, src_h
    elif width <= 0 or height <= 0:
        raise SequenceExportError("set both width and height, or leave both at 0")
    if width > MAX_EDGE or height > MAX_EDGE or width < 2 or height < 2:
        raise SequenceExportError(f"width and height must be from 2 to {MAX_EDGE}")
    return _even_size(width, height)


def _even_size(width: int, height: int) -> tuple[int, int]:
    if width < 2:
        width = 2
    if height < 2:
        height = 2
    if width > MAX_EDGE or height > MAX_EDGE:
        raise SequenceExportError(f"width and height must be at most {MAX_EDGE}")
    if width % 2 == 1:
        width = width - 1 if width > 2 else 2
    if height % 2 == 1:
        height = height - 1 if height > 2 else 2
    return width, height


def _confine(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root):
        raise SequenceExportError(f"path escapes the scene: {relative}")
    return candidate


def _open_rgb(blob: bytes) -> PilImage:
    try:
        image = Image.open(io.BytesIO(blob))
        image.load()
        converted = image.convert("RGB")
    except Exception as exc:
        raise SequenceExportError("a frame is not an image Pillow can read") from exc
    if converted is not image:
        image.close()
    return converted


def _png_bytes(image: PilImage, size: tuple[int, int]) -> bytes:
    current = image
    if image.width != size[0] or image.height != size[1]:
        current = image.resize(size, Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    current.save(buffer, format="PNG")
    if current is not image:
        current.close()
    return buffer.getvalue()
