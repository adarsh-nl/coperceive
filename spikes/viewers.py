"""Viewer spike on Griffin-25m scene Town03-000. No images (not downloaded)."""
from __future__ import annotations

import json
import time
import zipfile
from pathlib import Path

import numpy as np
import rerun as rr

META = Path("/tmp/griffin-extract/griffin_50scenes_25m/griffin-release")
LIDAR_ZIP = "/tmp/griffin-25m/datasets/griffin_50scenes_25m/vehicle_lidar.zip"
OUT = Path("/tmp/spikes/viewer")
OUT.mkdir(parents=True, exist_ok=True)


def RzRyRx(roll, pitch, yaw):
    r, p, y = np.deg2rad([roll, pitch, yaw])
    c, s = np.cos, np.sin
    Rx = np.array([[1, 0, 0], [0, c(r), -s(r)], [0, s(r), c(r)]])
    Ry = np.array([[c(p), 0, s(p)], [0, 1, 0], [-s(p), 0, c(p)]])
    Rz = np.array([[c(y), -s(y), 0], [s(y), c(y), 0], [0, 0, 1]])
    return Rz @ Ry @ Rx


def quat_wxyz(R):
    # Shepperd
    t = np.trace(R)
    if t > 0:
        s = np.sqrt(t + 1.0) * 2
        w = 0.25 * s
        x = (R[2, 1] - R[1, 2]) / s
        y = (R[0, 2] - R[2, 0]) / s
        z = (R[1, 0] - R[0, 1]) / s
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        s = np.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2]) * 2
        w = (R[2, 1] - R[1, 2]) / s
        x = 0.25 * s
        y = (R[0, 1] + R[1, 0]) / s
        z = (R[0, 2] + R[2, 0]) / s
    elif R[1, 1] > R[2, 2]:
        s = np.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2]) * 2
        w = (R[0, 2] - R[2, 0]) / s
        x = (R[0, 1] + R[1, 0]) / s
        y = 0.25 * s
        z = (R[1, 2] + R[2, 1]) / s
    else:
        s = np.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1]) * 2
        w = (R[1, 0] - R[0, 1]) / s
        x = (R[0, 2] + R[2, 0]) / s
        y = (R[1, 2] + R[2, 1]) / s
        z = 0.25 * s
    return np.array([w, x, y, z], dtype=np.float64)


def load():
    scene = json.loads((META / "vehicle-side" / "scene_infos.json").read_text())[0]
    frames = [int(f) for f in scene["info"]["frames"]]
    agents = {}
    for side, name in (("vehicle-side", "vehicle"), ("drone-side", "drone")):
        poses = []
        for fid in frames:
            pose = json.loads((META / side / "pose" / f"{fid:06d}.json").read_text())
            R = RzRyRx(pose["roll"], pose["pitch"], pose["yaw"])
            t = np.array([pose["x"], pose["y"], pose["z"]], dtype=np.float64)
            poses.append((R, t))
        calib = {}
        for path in (META / side / "calib").glob("*.json"):
            calib[path.stem] = json.loads(path.read_text())
        agents[name] = {"poses": poses, "calib": calib}
    zf = zipfile.ZipFile(LIDAR_ZIP)
    clouds = []
    t0 = time.perf_counter()
    for fid in frames:
        name = f"griffin_50scenes_25m/griffin-release/vehicle-side/lidar/lidar_top/{fid:06d}.ply"
        with zf.open(name) as fh:
            n = None
            while True:
                line = fh.readline()
                if line.startswith(b"element vertex"):
                    n = int(line.split()[-1])
                if line.strip() == b"end_header":
                    break
            clouds.append(np.loadtxt(fh, dtype=np.float32, max_rows=n))
    return scene["name"], frames, agents, clouds, time.perf_counter() - t0


def sensor_world(body_R, body_t, extrinsic):
    T = np.asarray(extrinsic, dtype=np.float64)
    R = body_R @ T[:3, :3]
    t = body_R @ T[:3, 3] + body_t
    return R, t


def fov_y(intrinsic):
    K = np.asarray(intrinsic, dtype=np.float64)
    fy = K[1, 1]
    cy = K[1, 2]
    # principal point is at height/2 in this release (540), so height = 2*cy
    return float(2 * np.arctan(cy / fy)), float(2 * cy), float(K[0, 2] * 2)


def main():
    scene_id, frames, agents, clouds, parse_s = load()
    results = {"scene_id": scene_id, "frames": len(frames), "ply_parse_s": parse_s, "images": "not downloaded"}

    # --- Rerun ---
    rrd = OUT / "town03-000.rrd"
    t0 = time.perf_counter()
    rr.init("griffin-town03-000", spawn=False)
    rr.save(rrd)
    cams = {
        "vehicle": ["front", "back", "left", "right"],
        "drone": ["front", "back", "left", "right", "bottom"],
    }
    colors = {"vehicle": [230, 120, 40], "drone": [40, 120, 220]}
    for i, fid in enumerate(frames):
        rr.set_time_seconds("griffin", fid / 10.0)
        for agent, color in colors.items():
            R, t = agents[agent]["poses"][i]
            q = quat_wxyz(R)
            rr.log(
                f"agents/{agent}",
                rr.Transform3D(translation=t, quaternion=rr.Quaternion(xyzw=[q[1], q[2], q[3], q[0]])),
            )
            if agent == "vehicle":
                pts = clouds[i][:, :3]
                # sensor -> world
                ext = np.asarray(agents["vehicle"]["calib"]["lidar_top"]["extrinsic"], dtype=np.float64)
                Rw, tw = sensor_world(R, t, ext)
                world = (Rw @ pts.T).T + tw
                rr.log(
                    "agents/vehicle/lidar_top",
                    rr.Points3D(world, colors=np.tile(np.array(color, dtype=np.uint8), (len(world), 1))),
                )
            for cam in cams[agent]:
                cal = agents[agent]["calib"][cam]
                Rw, tw = sensor_world(R, t, cal["extrinsic"])
                q = quat_wxyz(Rw)
                K = np.asarray(cal["intrinsic"], dtype=np.float64)
                fov, height, width = fov_y(cal["intrinsic"])
                rr.log(
                    f"agents/{agent}/cam_{cam}",
                    rr.Transform3D(translation=tw, quaternion=rr.Quaternion(xyzw=[q[1], q[2], q[3], q[0]])),
                    rr.Pinhole(
                        image_from_camera=K,
                        resolution=[width, height],
                        camera_xyz=rr.ViewCoordinates.RDF,
                        image_plane_distance=3.0,
                    ),
                )
    # save() was called before log; rerun streams into the file opened by save.
    # Flush by disconnecting the sink: init a new recording.
    rr.disconnect()
    results["rerun"] = {
        "version": rr.__version__,
        "log_seconds": time.perf_counter() - t0,
        "rrd_bytes": rrd.stat().st_size if rrd.exists() else 0,
        "rrd": str(rrd),
    }
    print("rerun", results["rerun"], flush=True)

    # three.js payload: all frames, float32 xyz world points + frustum poses
    payload = {"frames": [], "agents": ["vehicle", "drone"]}
    for i, fid in enumerate(frames):
        R, t = agents["vehicle"]["poses"][i]
        ext = np.asarray(agents["vehicle"]["calib"]["lidar_top"]["extrinsic"], dtype=np.float64)
        Rw, tw = sensor_world(R, t, ext)
        world = (Rw @ clouds[i][:, :3].T).T + tw
        fr = []
        for agent in ("vehicle", "drone"):
            Rb, tb = agents[agent]["poses"][i]
            for cam in cams[agent]:
                cal = agents[agent]["calib"][cam]
                Rc, tc = sensor_world(Rb, tb, cal["extrinsic"])
                q = quat_wxyz(Rc)
                fov, height, width = fov_y(cal["intrinsic"])
                fr.append(
                    {
                        "agent": agent,
                        "sensor": cam,
                        "position": tc.tolist(),
                        "wxyz": q.tolist(),
                        "fov_y": fov,
                        "aspect": width / height,
                    }
                )
        payload["frames"].append(
            {
                "frame": fid,
                "points": world.astype(np.float32).reshape(-1).tolist(),
                "frustums": fr,
                "vehicle": agents["vehicle"]["poses"][i][1].tolist(),
                "drone": agents["drone"]["poses"][i][1].tolist(),
            }
        )
    # This JSON is large (149 * ~3000 * 3 floats as text). Write binary instead.
    # Keep a 20-frame JSON for the browser spike.
    step = max(1, len(frames) // 20)
    small = {"frames": payload["frames"][::step]}
    blob = OUT / "frames.json"
    t0 = time.perf_counter()
    blob.write_text(json.dumps(small))
    results["three_payload"] = {
        "frames": len(small["frames"]),
        "bytes": blob.stat().st_size,
        "write_s": time.perf_counter() - t0,
        "points_frame0": len(small["frames"][0]["points"]) // 3,
        "frustums": len(small["frames"][0]["frustums"]),
    }
    print("three payload", results["three_payload"], flush=True)
    (OUT / "summary_partial.json").write_text(json.dumps(results, indent=2))
    print("PARTIAL_OK", flush=True)


if __name__ == "__main__":
    main()
