#!/usr/bin/env python3
"""Generate synthetic scenes, capture every tour frame, encode site/public/tour."""

from __future__ import annotations

import getpass
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = Path(__file__).resolve().parent
SPEC_PATH = ROOT / "site" / "src" / "tour" / "tour_spec.json"
PUBLIC = ROOT / "site" / "public" / "tour"
WORK = Path(os.environ.get("TOUR_WORK", "/tmp/coperceive-tour-capture"))
PY = Path(sys.executable)
NODE = shutil.which("node") or "node"
BUDGET = 8 * 1024 * 1024
FIRST_BUDGET = int(1.5 * 1024 * 1024)

procs: list[tuple[subprocess.Popen[bytes], object]] = []


def log(msg: str) -> None:
    print(msg, flush=True)


def run(cmd: list[str], cwd: Path, env: dict[str, str] | None = None, check: bool = True) -> int:
    log("+ " + " ".join(cmd))
    result = subprocess.run(cmd, cwd=cwd, env=env)
    if check and result.returncode != 0:
        raise SystemExit(result.returncode)
    return result.returncode


def start_server(args: list[str], log_path: Path) -> None:
    log("+ " + " ".join(args))
    handle = log_path.open("w")
    proc = subprocess.Popen(args, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True)
    procs.append((proc, handle))


def stop_servers() -> None:
    for proc, handle in procs:
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        handle.close()
    procs.clear()


def wait_http(port: int, timeout: float = 120) -> None:
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{port}/"
    while time.time() < deadline:
        for proc, _handle in procs:
            if proc.poll() is not None:
                raise SystemExit(f"a server exited ({proc.returncode}). See {WORK}/serve-*.log")
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status < 500:
                    log(f"server {port} ready")
                    return
        except Exception:
            time.sleep(0.4)
    raise SystemExit(f"server on port {port} did not become ready")


def take_scenes(src: Path, dest: Path) -> None:
    """Move scene folders into one root.

    The server rejects symlinks, and a second copy does not fit.
    """
    scenes = src / "scenes"
    if not scenes.is_dir():
        raise SystemExit(f"no scenes in {src}")
    dest.mkdir(parents=True, exist_ok=True)
    for child in sorted(scenes.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if not (child / "manifest.json").is_file():
            continue
        target = dest / child.name
        if target.exists():
            continue
        shutil.move(child, target)
    raw = src / "raw"
    if raw.is_dir():
        shutil.rmtree(raw)


def encode_still(src: Path, dest: Path, quality: int) -> None:
    from PIL import Image

    image = Image.open(src).convert("RGB")
    if image.size != (2880, 1800):
        raise SystemExit(f"{src} is {image.size}, expected 2880x1800")
    dest.parent.mkdir(parents=True, exist_ok=True)
    image.save(dest, "WEBP", quality=quality, method=6)


def encode_loop(frames: Path, stem: Path, crf_mp4: str, crf_webm: str) -> None:
    import imageio_ffmpeg
    from PIL import Image

    pngs = sorted(frames.glob("f*.png"))
    if len(pngs) != 80:
        raise SystemExit(f"{frames} has {len(pngs)} frames, expected 80")
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    even = "scale=trunc(iw/2)*2:trunc(ih/2)*2"
    subprocess.check_call(
        [
            ffmpeg,
            "-y",
            "-framerate",
            "10",
            "-i",
            str(frames / "f%03d.png"),
            "-vf",
            even,
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-crf",
            crf_mp4,
            "-an",
            "-movflags",
            "+faststart",
            str(stem.with_suffix(".mp4")),
        ]
    )
    subprocess.check_call(
        [
            ffmpeg,
            "-y",
            "-framerate",
            "10",
            "-i",
            str(frames / "f%03d.png"),
            "-vf",
            even,
            "-c:v",
            "libvpx-vp9",
            "-b:v",
            "0",
            "-crf",
            crf_webm,
            "-an",
            str(stem.with_suffix(".webm")),
        ]
    )
    Image.open(pngs[0]).convert("RGB").save(stem.with_suffix(".webp"), "WEBP", quality=80, method=6)


def file_bytes() -> int:
    return sum(path.stat().st_size for path in PUBLIC.iterdir() if path.is_file())


def main() -> None:
    static = ROOT / "coperceive" / "app" / "static" / "index.html"
    if not static.is_file():
        raise SystemExit("web build missing. Run scripts/site/tour/capture.sh")
    spec = json.loads(SPEC_PATH.read_text())
    first = {step["screen"] for step in spec["steps"] if step.get("ch") == 0 and step.get("screen")}
    WORK.mkdir(parents=True, exist_ok=True)
    if PUBLIC.exists():
        shutil.rmtree(PUBLIC)
    PUBLIC.mkdir(parents=True)

    rich = WORK / "rich"
    merged = WORK / "all" / "scenes"
    if merged.exists() and not (merged / "demo-junction" / "manifest.json").is_file():
        shutil.rmtree(merged)
    if (rich / "scenes" / "demo-junction" / "manifest.json").is_file() or (
        merged / "demo-junction" / "manifest.json"
    ).is_file():
        log("reusing the dense junction")
    else:
        log("generating the dense junction")
        run([str(PY), str(SCRIPTS / "rich_demo.py"), str(rich)], SCRIPTS)
    if not (rich / "predictions" / "baseline").exists():
        run([str(PY), str(SCRIPTS / "second_run.py"), str(rich)], SCRIPTS)
    scene_ids = {
        "opv2v": "opv2v-style-junction",
        "v2xset": "v2xset-style-junction",
        "dair": "dair-style-intersection",
        "v2v4real": "v2v4real-style-highway",
        "griffin": "griffin-style-aerial",
    }
    take_scenes(rich, merged)
    for kind, scene_id in scene_ids.items():
        out = WORK / kind
        if (merged / scene_id / "manifest.json").is_file():
            log(f"reusing {kind}")
            continue
        if (out / "scenes" / scene_id / "manifest.json").is_file():
            log(f"moving {kind}")
            take_scenes(out, merged)
            shutil.rmtree(out / "predictions", ignore_errors=True)
            continue
        if out.exists():
            shutil.rmtree(out)
        code = run([str(PY), str(SCRIPTS / "variants.py"), str(out), kind], SCRIPTS, check=False)
        ready = (out / "scenes" / scene_id / "manifest.json").is_file()
        if code != 0 and kind == "v2v4real" and ready:
            log("v2v4real reported a harmless mAP gap; the scene is on disk")
        elif code != 0:
            raise SystemExit(code)
        if not ready:
            raise SystemExit(f"{kind} did not write {scene_id}")
        take_scenes(out, merged)
        shutil.rmtree(out / "predictions", ignore_errors=True)

    catalog = WORK / "realcatalog.json"
    catalog.write_bytes(
        subprocess.check_output([str(PY), "-m", "coperceive", "dataset", "catalog"], cwd=ROOT)
    )

    node_env = os.environ.copy()
    node_env["TOUR_HOSTNAME"] = socket.gethostname()
    node_env["TOUR_USER"] = getpass.getuser()
    node_env["TOUR_HOME"] = str(Path.home())
    node_env["TOUR_TEXT_LOG"] = str(WORK / "page_text.log")
    node_env["CATALOG"] = str(catalog)
    node_env.pop("PLAYWRIGHT_BROWSERS_PATH", None)

    stream_root = WORK / "stream-root"
    stream_cache = WORK / "stream-cache"
    stream_root.mkdir(exist_ok=True)
    stream_cache.mkdir(exist_ok=True)
    stream_argv = json.dumps(
        [str(PY), "-m", "coperceive", "dataset", "stream", "--root", str(merged)]
    )

    try:
        start_server(
            [
                str(PY),
                "-m",
                "coperceive",
                "serve",
                "--root",
                str(merged),
                "--predictions",
                str(rich / "predictions"),
                "--port",
                "8733",
            ],
            WORK / "serve-8733.log",
        )
        desk_root = WORK / "desk-scenes"
        desk_scene = desk_root / "demo-junction"
        if not (desk_scene / "manifest.json").is_file():
            desk_root.mkdir(parents=True, exist_ok=True)
            subprocess.check_call(["cp", "-cR", str(merged / "demo-junction"), str(desk_scene)])
        start_server(
            [str(PY), "-m", "coperceive", "serve", "--root", str(desk_root), "--port", "8732"],
            WORK / "serve-8732.log",
        )
        start_server(
            [str(PY), "-m", "coperceive", "serve", "--root", str(merged), "--port", "8735"],
            WORK / "serve-8735.log",
        )
        start_server(
            [
                str(PY),
                "-m",
                "coperceive",
                "serve",
                "--root",
                str(stream_root),
                "--port",
                "8734",
                "--remote-stream-argv",
                stream_argv,
                "--remote-cache-dir",
                str(stream_cache),
            ],
            WORK / "serve-8734.log",
        )
        for port in (8733, 8732, 8735, 8734):
            wait_http(port)

        orbit_frames = list((WORK / "vw" / "loop_orbit").glob("f*.png"))
        chase_frames = list((WORK / "vw" / "loop_chase").glob("f*.png"))
        if (
            (WORK / "vw" / "orbit.png").is_file()
            and len(orbit_frames) == 80
            and len(chase_frames) == 80
        ):
            log("reusing viewer captures")
        else:
            viewer_env = dict(node_env, PORT="8733")
            run([NODE, str(SCRIPTS / "viewer.js")], WORK, env=viewer_env)
        desk_out = WORK / "desk"
        desk_needed = ["library", "openfolder", "dataset", "neweval", "history"]
        if all((desk_out / f"{name}.png").is_file() for name in desk_needed):
            log("reusing desktop captures")
        else:
            desk_env = dict(node_env, PORT="8732", OUT=str(desk_out))
            run([NODE, str(SCRIPTS / "desk.js")], WORK, env=desk_env)
        data_needed = [
            "getdata.png",
            "getdata_opv2v.png",
            "getdata_v2xset.png",
            "getdata_dair-v2x-c.png",
            "getdata_v2v4real.png",
            "view_opv2v.png",
            "view_v2xset.png",
            "view_dair-v2x-c.png",
            "view_v2v4real.png",
            "view_griffin.png",
        ]
        if all((WORK / "ds" / name).is_file() for name in data_needed):
            log("reusing dataset captures")
        else:
            data_env = dict(node_env, DESK="8732", VIEW="8735")
            run([NODE, str(SCRIPTS / "datasets.js")], WORK, env=data_env)
        if (WORK / "vw" / "stream.png").is_file():
            log("reusing the stream capture")
        else:
            stream_env = dict(node_env, PORT="8734")
            run([NODE, str(SCRIPTS / "stream.js")], WORK, env=stream_env)
    finally:
        stop_servers()

    stills: list[tuple[str, Path, bool]] = []
    loops: list[tuple[str, Path]] = []
    for screen in spec["screens"]:
        key = screen["key"]
        output = screen["capture_output"]
        if "f*.png" in output:
            loops.append((key, WORK / "vw" / key))
            continue
        stills.append((key, WORK / output, key in first))

    for key, src, _eager in stills:
        if not src.is_file():
            raise SystemExit(f"missing capture {src}")
        encode_still(src, PUBLIC / f"{key}.webp", 84)
    for key, frames in loops:
        encode_loop(frames, PUBLIC / key, "32", "42")

    first_bytes = sum((PUBLIC / f"{key}.webp").stat().st_size for key in sorted(first))
    quality = 84
    while first_bytes > FIRST_BUDGET and quality > 60:
        quality -= 6
        log(f"opening chapter is {first_bytes} bytes; re-encoding at quality {quality}")
        for key, src, eager in stills:
            if eager:
                encode_still(src, PUBLIC / f"{key}.webp", quality)
        first_bytes = sum((PUBLIC / f"{key}.webp").stat().st_size for key in sorted(first))
    if first_bytes > FIRST_BUDGET:
        raise SystemExit(f"opening chapter is {first_bytes} bytes, over {FIRST_BUDGET}")

    crf_mp4, crf_webm = 32, 42
    while file_bytes() > BUDGET and int(crf_mp4) < 40:
        crf_mp4 = str(int(crf_mp4) + 3)
        crf_webm = str(int(crf_webm) + 4)
        log(f"media is over 8 MB; re-encoding loops at crf {crf_mp4}/{crf_webm}")
        for key, frames in loops:
            encode_loop(frames, PUBLIC / key, crf_mp4, crf_webm)
    still_q = 84
    while file_bytes() > BUDGET and still_q > 60:
        still_q -= 6
        log(f"media is over 8 MB; re-encoding later stills at quality {still_q}")
        for key, src, eager in stills:
            if not eager:
                encode_still(src, PUBLIC / f"{key}.webp", still_q)
    if file_bytes() > BUDGET:
        raise SystemExit(f"site/public/tour is {file_bytes()} bytes, over {BUDGET}")

    log("media")
    for path in sorted(PUBLIC.iterdir()):
        if path.is_file():
            log(f"  {path.name:28} {path.stat().st_size:8}  {path.stat().st_size / 1024:.1f} KB")
    log(f"  {'TOTAL':28} {file_bytes():8}  {file_bytes() / 1024:.1f} KB")
    log(f"  opening chapter {first_bytes} bytes ({', '.join(sorted(first))})")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        stop_servers()
        raise
