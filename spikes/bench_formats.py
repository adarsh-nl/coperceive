"""Time-boxed on-disk format spike for one Griffin-25m scene plus full-subset point size.

Reads only /tmp extracts. Writes results to /tmp/spikes/format_results.json.
"""
from __future__ import annotations

import json
import os
import shutil
import time
import zipfile
from multiprocessing import get_context
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.ipc as ipc
import pyarrow.parquet as pq

ROOT = Path("/tmp/spikes/formats")
META = Path("/tmp/griffin-extract/griffin_50scenes_25m/griffin-release")
LIDAR_ZIP = Path("/tmp/griffin-25m/datasets/griffin_50scenes_25m/vehicle_lidar.zip")
OUT = Path("/tmp/spikes/format_results.json")

SCENE_INDEX = 0  # Town03-000


def now():
    return time.perf_counter()


def load_scene():
    scenes = json.loads((META / "vehicle-side" / "scene_infos.json").read_text())
    scene = scenes[SCENE_INDEX]
    frames = [int(f) for f in scene["info"]["frames"]]
    pose_rows = []
    box_rows = []
    for side, agent in (("vehicle-side", "vehicle"), ("drone-side", "drone")):
        for fid in frames:
            pose = json.loads((META / side / "pose" / f"{fid:06d}.json").read_text())
            pose_rows.append(
                {
                    "agent": agent,
                    "frame": fid,
                    "time_us": fid * 100_000,  # paper 10 Hz; file stores the frame index
                    "x": pose["x"],
                    "y": pose["y"],
                    "z": pose["z"],
                    "roll_deg": pose["roll"],
                    "pitch_deg": pose["pitch"],
                    "yaw_deg": pose["yaw"],
                }
            )
            for line in (META / side / "label" / f"{fid:06d}.txt").read_text().splitlines():
                p = line.split()
                if len(p) != 12:
                    continue
                box_rows.append(
                    {
                        "agent": agent,
                        "frame": fid,
                        "time_us": fid * 100_000,
                        "category": p[0],
                        "x": float(p[1]),
                        "y": float(p[2]),
                        "z": float(p[3]),
                        "length": float(p[4]),
                        "width": float(p[5]),
                        "height": float(p[6]),
                        "roll_deg": float(p[7]),
                        "pitch_deg": float(p[8]),
                        "yaw_deg": float(p[9]),
                        "track_id": int(float(p[10])),
                        "visibility": float(p[11]),
                    }
                )
    # image path references only (bytes not downloaded)
    image_rows = []
    cams = {
        "vehicle": ["front", "back", "left", "right"],
        "drone": ["front", "back", "left", "right", "bottom"],
    }
    for agent, names in cams.items():
        for fid in frames:
            for cam in names:
                image_rows.append(
                    {
                        "agent": agent,
                        "sensor": cam,
                        "frame": fid,
                        "path": f"griffin_50scenes_25m/griffin-release/{agent}-side/camera/{cam}/{fid:06d}.png",
                    }
                )
    zf = zipfile.ZipFile(LIDAR_ZIP)
    clouds = []
    t0 = now()
    for fid in frames:
        name = (
            "griffin_50scenes_25m/griffin-release/vehicle-side/lidar/lidar_top/"
            f"{fid:06d}.ply"
        )
        with zf.open(name) as fh:
            n = None
            while True:
                line = fh.readline()
                if line.startswith(b"element vertex"):
                    n = int(line.split()[-1])
                if line.strip() == b"end_header":
                    break
            arr = np.loadtxt(fh, dtype=np.float32, max_rows=n)
        clouds.append(arr)
    load_s = now() - t0
    return {
        "scene_id": scene["name"],
        "frames": frames,
        "poses": pose_rows,
        "boxes": box_rows,
        "images": image_rows,
        "clouds": clouds,
        "ply_load_s": load_s,
    }


def dir_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            total += p.stat().st_size
    return total


def timed(fn, repeats=30):
    fn()
    samples = []
    for _ in range(repeats):
        t0 = now()
        out = fn()
        samples.append((now() - t0) * 1e3)
    samples.sort()
    return {
        "median_ms": samples[len(samples) // 2],
        "p95_ms": samples[int(len(samples) * 0.95) - 1],
        "n": out if isinstance(out, int) else None,
    }


def write_tables(base: Path, data, kind: str):
    base.mkdir(parents=True, exist_ok=True)
    poses = pa.Table.from_pylist(data["poses"])
    boxes = pa.Table.from_pylist(data["boxes"])
    images = pa.Table.from_pylist(data["images"])
    # points as flat columns plus frame index
    frames_col = []
    xyz = []
    for fid, cloud in zip(data["frames"], data["clouds"]):
        frames_col.append(np.full(len(cloud), fid, dtype=np.int32))
        xyz.append(cloud)
    frame_idx = np.concatenate(frames_col)
    pts = np.concatenate(xyz)
    points = pa.table(
        {
            "frame": frame_idx,
            "x": pts[:, 0],
            "y": pts[:, 1],
            "z": pts[:, 2],
            "intensity": pts[:, 3],
        }
    )
    t0 = now()
    if kind == "parquet":
        pq.write_table(poses, base / "poses.parquet")
        pq.write_table(boxes, base / "boxes.parquet")
        pq.write_table(images, base / "images.parquet")
        pq.write_table(points, base / "points.parquet", compression="zstd")
    elif kind == "arrow":
        for name, table in (
            ("poses.arrow", poses),
            ("boxes.arrow", boxes),
            ("images.arrow", images),
            ("points.arrow", points),
        ):
            with ipc.new_file(base / name, table.schema) as writer:
                writer.write_table(table)
    else:
        raise AssertionError(kind)
    return now() - t0, points.num_rows


def query_arrow_like(base: Path, data, kind: str):
    mid = data["frames"][len(data["frames"]) // 2]
    if kind == "parquet":
        def all_agents():
            poses = pq.read_table(base / "poses.parquet", filters=[("frame", "==", mid)])
            boxes = pq.read_table(base / "boxes.parquet", filters=[("frame", "==", mid)])
            pts = pq.read_table(base / "points.parquet", filters=[("frame", "==", mid)])
            return poses.num_rows + boxes.num_rows + pts.num_rows

        def one_sensor():
            pts = pq.read_table(base / "points.parquet", columns=["x", "y", "z", "intensity"])
            return pts.num_rows
    else:
        def all_agents():
            with pa.memory_map(str(base / "poses.arrow"), "r") as src:
                poses = ipc.open_file(src).read_all().filter(pa.compute.field("frame") == mid)
            with pa.memory_map(str(base / "boxes.arrow"), "r") as src:
                boxes = ipc.open_file(src).read_all().filter(pa.compute.field("frame") == mid)
            with pa.memory_map(str(base / "points.arrow"), "r") as src:
                pts = ipc.open_file(src).read_all().filter(pa.compute.field("frame") == mid)
            return poses.num_rows + boxes.num_rows + pts.num_rows

        def one_sensor():
            with pa.memory_map(str(base / "points.arrow"), "r") as src:
                pts = ipc.open_file(src).read_all()
            return pts.num_rows
    return timed(all_agents), timed(one_sensor, repeats=10)


def write_zarr(base: Path, data):
    import zarr

    if base.exists():
        shutil.rmtree(base)
    t0 = now()
    root = zarr.open_group(str(base), mode="w")
    max_n = max(len(c) for c in data["clouds"])
    padded = np.zeros((len(data["clouds"]), max_n, 4), dtype=np.float32)
    counts = np.zeros(len(data["clouds"]), dtype=np.int32)
    for i, cloud in enumerate(data["clouds"]):
        padded[i, : len(cloud)] = cloud
        counts[i] = len(cloud)
    root.create_array("points", data=padded, chunks=(1, max_n, 4))
    root.create_array("counts", data=counts)
    root.create_array("frames", data=np.array(data["frames"], dtype=np.int32))
    # poses as parallel arrays
    agents = np.array([1 if r["agent"] == "drone" else 0 for r in data["poses"]], dtype=np.int8)
    pose_f = np.array([r["frame"] for r in data["poses"]], dtype=np.int32)
    pose_xyz = np.array([[r["x"], r["y"], r["z"], r["roll_deg"], r["pitch_deg"], r["yaw_deg"]] for r in data["poses"]], dtype=np.float64)
    root.create_array("pose_agent", data=agents)
    root.create_array("pose_frame", data=pose_f)
    root.create_array("pose_xyzrpy", data=pose_xyz)
    return now() - t0, max_n


def query_zarr(base: Path, data):
    import zarr

    root = zarr.open_group(str(base), mode="r")
    mid_i = len(data["frames"]) // 2

    def all_agents():
        cloud = root["points"][mid_i]
        poses = root["pose_xyzrpy"][:]
        frames = root["pose_frame"][:]
        return int(cloud.shape[0] + np.count_nonzero(frames == data["frames"][mid_i]))

    def one_sensor():
        pts = root["points"][:]
        return int(pts.shape[0] * pts.shape[1])

    return timed(all_agents), timed(one_sensor, repeats=10)


def write_hdf5(base: Path, data):
    import h5py

    path = base / "scene.h5"
    base.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    max_n = max(len(c) for c in data["clouds"])
    padded = np.zeros((len(data["clouds"]), max_n, 4), dtype=np.float32)
    counts = np.zeros(len(data["clouds"]), dtype=np.int32)
    for i, cloud in enumerate(data["clouds"]):
        padded[i, : len(cloud)] = cloud
        counts[i] = len(cloud)
    t0 = now()
    with h5py.File(path, "w") as h:
        h.create_dataset("points", data=padded, chunks=(1, max_n, 4), compression="gzip")
        h.create_dataset("counts", data=counts)
        h.create_dataset("frames", data=np.array(data["frames"], dtype=np.int32))
        h.create_dataset(
            "pose_frame",
            data=np.array([r["frame"] for r in data["poses"]], dtype=np.int32),
        )
        h.create_dataset(
            "pose_xyzrpy",
            data=np.array(
                [[r["x"], r["y"], r["z"], r["roll_deg"], r["pitch_deg"], r["yaw_deg"]] for r in data["poses"]],
                dtype=np.float64,
            ),
        )
    return now() - t0


def query_hdf5(path: Path, data):
    import h5py

    mid_i = len(data["frames"]) // 2

    def all_agents():
        with h5py.File(path, "r") as h:
            cloud = h["points"][mid_i]
            frames = h["pose_frame"][:]
            return int(cloud.shape[0] + np.count_nonzero(frames == data["frames"][mid_i]))

    def one_sensor():
        with h5py.File(path, "r") as h:
            pts = h["points"][:]
            return int(pts.shape[0])

    return timed(all_agents), timed(one_sensor, repeats=10)


def write_mcap(path: Path, data):
    from mcap.writer import Writer

    path.parent.mkdir(parents=True, exist_ok=True)
    t0 = now()
    with path.open("wb") as fh:
        writer = Writer(fh)
        writer.start()
        schema = writer.register_schema(
            name="lidar",
            encoding="raw",
            data=b"float32 x,y,z,intensity little-endian",
        )
        chan = writer.register_channel(
            topic="/vehicle/lidar_top",
            message_encoding="raw",
            schema_id=schema,
        )
        pose_schema = writer.register_schema(name="poses", encoding="json", data=b"{}")
        pose_chan = writer.register_channel(
            topic="/agents/poses",
            message_encoding="json",
            schema_id=pose_schema,
        )
        for fid, cloud, in zip(data["frames"], data["clouds"]):
            t = fid * 100_000
            writer.add_message(chan, log_time=t, data=cloud.tobytes(), publish_time=t)
            payload = json.dumps([r for r in data["poses"] if r["frame"] == fid]).encode()
            writer.add_message(pose_chan, log_time=t, data=payload, publish_time=t)
        writer.finish()
    return now() - t0


def query_mcap(path: Path, data):
    from mcap.reader import make_reader

    mid = data["frames"][len(data["frames"]) // 2]
    t = mid * 100_000

    def all_agents():
        n = 0
        with path.open("rb") as fh:
            reader = make_reader(fh)
            for _schema, channel, message in reader.iter_messages(start_time=t, end_time=t):
                n += 1 if channel.topic == "/agents/poses" else len(message.data) // 16
        return n

    def one_sensor():
        n = 0
        with path.open("rb") as fh:
            reader = make_reader(fh)
            for _schema, channel, message in reader.iter_messages(topics=["/vehicle/lidar_top"]):
                n += len(message.data) // 16
        return n

    return timed(all_agents), timed(one_sensor, repeats=10)


def write_hybrid(base: Path, data):
    base.mkdir(parents=True, exist_ok=True)
    t0 = now()
    pq.write_table(pa.Table.from_pylist(data["poses"]), base / "poses.parquet")
    pq.write_table(pa.Table.from_pylist(data["boxes"]), base / "boxes.parquet")
    pq.write_table(pa.Table.from_pylist(data["images"]), base / "images.parquet")
    offsets = []
    blob = base / "lidar_top.bin"
    with blob.open("wb") as fh:
        cursor = 0
        for fid, cloud in zip(data["frames"], data["clouds"]):
            raw = np.ascontiguousarray(cloud, dtype=np.float32).tobytes()
            fh.write(raw)
            offsets.append({"frame": fid, "offset": cursor, "nbytes": len(raw), "count": len(cloud)})
            cursor += len(raw)
    pq.write_table(pa.Table.from_pylist(offsets), base / "lidar_index.parquet")
    return now() - t0


def query_hybrid(base: Path, data):
    mid = data["frames"][len(data["frames"]) // 2]
    index = pq.read_table(base / "lidar_index.parquet").to_pylist()
    by_frame = {row["frame"]: row for row in index}

    def all_agents():
        poses = pq.read_table(base / "poses.parquet", filters=[("frame", "==", mid)])
        boxes = pq.read_table(base / "boxes.parquet", filters=[("frame", "==", mid)])
        row = by_frame[mid]
        with (base / "lidar_top.bin").open("rb") as fh:
            fh.seek(row["offset"])
            buf = fh.read(row["nbytes"])
        cloud = np.frombuffer(buf, dtype=np.float32).reshape(-1, 4)
        return poses.num_rows + boxes.num_rows + len(cloud)

    def one_sensor():
        mm = np.memmap(base / "lidar_top.bin", dtype=np.float32, mode="r")
        return int(mm.shape[0] // 4)

    return timed(all_agents), timed(one_sensor, repeats=10)


def _worker_read(args):
    base, frames = args
    index = pq.read_table(Path(base) / "lidar_index.parquet").to_pylist()
    by_frame = {row["frame"]: row for row in index}
    total = 0
    for fid in frames:
        row = by_frame[int(fid)]
        with open(Path(base) / "lidar_top.bin", "rb") as fh:
            fh.seek(row["offset"])
            total += len(fh.read(row["nbytes"]))
        _ = pq.read_table(Path(base) / "poses.parquet", filters=[("frame", "==", int(fid))]).num_rows
    return total


def worker_test(base: Path, data):
    frames = data["frames"]
    chunks = [frames[i::4] for i in range(4)]
    ctx = get_context("spawn")
    t0 = now()
    with ctx.Pool(4) as pool:
        parts = pool.map(_worker_read, [(str(base), chunk) for chunk in chunks])
    return {"seconds": now() - t0, "bytes": sum(parts), "workers": 4, "errors": 0}


def schema_evolution(base: Path):
    """Add a column to a new file and read the old file with the new schema."""
    old = pq.read_table(base / "poses.parquet")
    new_schema = old.schema.append(pa.field("quality", pa.float32()))
    # reading old data: missing column is not automatic in pyarrow read
    loaded = pq.read_table(base / "poses.parquet")
    added = loaded.append_column("quality", pa.nulls(loaded.num_rows, pa.float32()))
    evolved = base / "poses_v2.parquet"
    pq.write_table(added, evolved)
    # old reader ignores unknown columns when a column subset is requested
    subset = pq.read_table(evolved, columns=["agent", "frame", "x"])
    return {
        "old_columns": old.num_columns,
        "new_columns": pq.read_schema(evolved).names,
        "old_reader_subset_rows": subset.num_rows,
        "note": "PyArrow reads a declared column subset from a file that has extra columns. A missing column is not filled unless the caller appends it.",
    }


def full_subset_binary_size():
    """Stream every Griffin-25m lidar PLY into one float32 blob. Size only."""
    zf = zipfile.ZipFile(LIDAR_ZIP)
    names = sorted(n for n in zf.namelist() if n.endswith(".ply"))
    out = Path("/tmp/spikes/all_lidar.bin")
    t0 = now()
    n_pts = 0
    with out.open("wb") as fh:
        for name in names:
            with zf.open(name) as src:
                n = None
                while True:
                    line = src.readline()
                    if line.startswith(b"element vertex"):
                        n = int(line.split()[-1])
                    if line.strip() == b"end_header":
                        break
                arr = np.loadtxt(src, dtype=np.float32, max_rows=n)
            fh.write(np.ascontiguousarray(arr, dtype=np.float32).tobytes())
            n_pts += len(arr)
    return {
        "files": len(names),
        "points": n_pts,
        "seconds": now() - t0,
        "bin_bytes": out.stat().st_size,
        "zip_bytes": LIDAR_ZIP.stat().st_size,
    }


def main():
    if ROOT.exists():
        shutil.rmtree(ROOT)
    ROOT.mkdir(parents=True)
    print("loading scene", flush=True)
    data = load_scene()
    summary = {
        "scene_id": data["scene_id"],
        "frames": len(data["frames"]),
        "poses": len(data["poses"]),
        "boxes": len(data["boxes"]),
        "image_refs": len(data["images"]),
        "points": int(sum(len(c) for c in data["clouds"])),
        "ply_parse_seconds": data["ply_load_s"],
        "formats": {},
    }
    print(json.dumps({k: summary[k] for k in summary if k != "formats"}), flush=True)

    for kind in ("parquet", "arrow"):
        base = ROOT / kind
        seconds, nrows = write_tables(base, data, kind)
        q1, q2 = query_arrow_like(base, data, kind)
        summary["formats"][kind] = {
            "write_seconds": seconds,
            "bytes": dir_size(base),
            "point_rows": nrows,
            "all_agents_at_t": q1,
            "one_sensor_across_scene": q2,
        }
        print(kind, summary["formats"][kind]["bytes"], q1["median_ms"], q2["median_ms"], flush=True)

    zbase = ROOT / "zarr"
    zs, max_n = write_zarr(zbase, data)
    q1, q2 = query_zarr(zbase, data)
    summary["formats"]["zarr"] = {
        "write_seconds": zs,
        "bytes": dir_size(zbase),
        "padded_max_points": max_n,
        "all_agents_at_t": q1,
        "one_sensor_across_scene": q2,
    }
    print("zarr", summary["formats"]["zarr"]["bytes"], flush=True)

    hbase = ROOT / "hdf5"
    hs = write_hdf5(hbase, data)
    q1, q2 = query_hdf5(hbase / "scene.h5", data)
    summary["formats"]["hdf5"] = {
        "write_seconds": hs,
        "bytes": dir_size(hbase),
        "all_agents_at_t": q1,
        "one_sensor_across_scene": q2,
    }
    print("hdf5", summary["formats"]["hdf5"]["bytes"], flush=True)

    mpath = ROOT / "scene.mcap"
    ms = write_mcap(mpath, data)
    q1, q2 = query_mcap(mpath, data)
    summary["formats"]["mcap"] = {
        "write_seconds": ms,
        "bytes": mpath.stat().st_size,
        "all_agents_at_t": q1,
        "one_sensor_across_scene": q2,
    }
    print("mcap", summary["formats"]["mcap"]["bytes"], flush=True)

    hy = ROOT / "hybrid"
    hys = write_hybrid(hy, data)
    q1, q2 = query_hybrid(hy, data)
    summary["formats"]["hybrid_parquet_bin"] = {
        "write_seconds": hys,
        "bytes": dir_size(hy),
        "all_agents_at_t": q1,
        "one_sensor_across_scene": q2,
        "image_reference_bytes": (hy / "images.parquet").stat().st_size,
    }
    print("hybrid", summary["formats"]["hybrid_parquet_bin"], flush=True)

    summary["worker_spawn_hybrid"] = worker_test(hy, data)
    summary["schema_evolution_parquet"] = schema_evolution(hy)
    print("workers", summary["worker_spawn_hybrid"], flush=True)
    print("full subset binary...", flush=True)
    summary["full_lidar_bin"] = full_subset_binary_size()
    OUT.write_text(json.dumps(summary, indent=2))
    print("WROTE", OUT, flush=True)


if __name__ == "__main__":
    main()
