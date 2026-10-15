"""Viser spike. Binds 127.0.0.1:8766 and writes ready.json once the first frame is up."""
import json, time, zipfile
from pathlib import Path
import numpy as np
import viser

META = Path("/tmp/griffin-extract/griffin_50scenes_25m/griffin-release")
LIDAR_ZIP = "/tmp/griffin-25m/datasets/griffin_50scenes_25m/vehicle_lidar.zip"

def RzRyRx(roll, pitch, yaw):
    r, p, y = np.deg2rad([roll, pitch, yaw])
    c, s = np.cos, np.sin
    Rx = np.array([[1, 0, 0], [0, c(r), -s(r)], [0, s(r), c(r)]])
    Ry = np.array([[c(p), 0, s(p)], [0, 1, 0], [-s(p), 0, c(p)]])
    Rz = np.array([[c(y), -s(y), 0], [s(y), c(y), 0], [0, 0, 1]])
    return Rz @ Ry @ Rx

def quat_wxyz(R):
    t = np.trace(R)
    if t > 0:
        s = np.sqrt(t + 1.0) * 2
        w, x, y, z = 0.25 * s, (R[2, 1] - R[1, 2]) / s, (R[0, 2] - R[2, 0]) / s, (R[1, 0] - R[0, 1]) / s
    else:
        # fallback via scipy-free shepperd branch used in viewers.py
        s = np.sqrt(max(1e-12, 1.0 + R[0, 0] - R[1, 1] - R[2, 2])) * 2
        w, x, y, z = (R[2, 1] - R[1, 2]) / s, 0.25 * s, (R[0, 1] + R[1, 0]) / s, (R[0, 2] + R[2, 0]) / s
    return (float(w), float(x), float(y), float(z))

def sensor_world(body_R, body_t, extrinsic):
    T = np.asarray(extrinsic, float)
    return body_R @ T[:3, :3], body_R @ T[:3, 3] + body_t

scene = json.loads((META / "vehicle-side" / "scene_infos.json").read_text())[0]
frames = [int(f) for f in scene["info"]["frames"]][::7]  # 22 frames, same budget as three.js
agents = {}
for side, name in (("vehicle-side", "vehicle"), ("drone-side", "drone")):
    poses = []
    for fid in frames:
        pose = json.loads((META / side / "pose" / f"{fid:06d}.json").read_text())
        poses.append((RzRyRx(pose["roll"], pose["pitch"], pose["yaw"]), np.array([pose["x"], pose["y"], pose["z"]], float)))
    calib = {p.stem: json.loads(p.read_text()) for p in (META / side / "calib").glob("*.json")}
    agents[name] = {"poses": poses, "calib": calib}
zf = zipfile.ZipFile(LIDAR_ZIP)
clouds = []
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
        clouds.append(np.loadtxt(fh, dtype=np.float32, max_rows=n)[:, :3])

server = viser.ViserServer(host="127.0.0.1", port=8766, verbose=False)
colors = {"vehicle": (230, 120, 40), "drone": (40, 120, 220)}
cams = {"vehicle": ["front", "back", "left", "right"], "drone": ["front", "back", "left", "right", "bottom"]}
pc = server.scene.add_point_cloud("vehicle/lidar", points=clouds[0], colors=colors["vehicle"], point_size=0.12)
frusta = []
for agent, names in cams.items():
    for cam in names:
        K = np.asarray(agents[agent]["calib"][cam]["intrinsic"], float)
        fov = float(2 * np.arctan(K[1, 2] / K[1, 1]))
        aspect = float((K[0, 2] * 2) / (K[1, 2] * 2))
        h = server.scene.add_camera_frustum(
            f"{agent}/{cam}", fov=fov, aspect=aspect, scale=2.5, color=colors[agent]
        )
        frusta.append((agent, cam, h))
slider = server.gui.add_slider("frame", min=0, max=len(frames) - 1, step=1, initial_value=0)
show_v = server.gui.add_checkbox("vehicle", initial_value=True)
show_d = server.gui.add_checkbox("drone", initial_value=True)

def apply(i):
    i = int(i)
    R, t = agents["vehicle"]["poses"][i]
    Rw, tw = sensor_world(R, t, agents["vehicle"]["calib"]["lidar_top"]["extrinsic"])
    world = (Rw @ clouds[i].T).T + tw
    pc.points = world.astype(np.float32)
    pc.visible = bool(show_v.value)
    for agent, cam, h in frusta:
        Rb, tb = agents[agent]["poses"][i]
        Rc, tc = sensor_world(Rb, tb, agents[agent]["calib"][cam]["extrinsic"])
        h.position = tc
        h.wxyz = np.array(quat_wxyz(Rc))
        h.visible = bool(show_v.value) if agent == "vehicle" else bool(show_d.value)

@slider.on_update
def _(_ev):
    apply(slider.value)

@show_v.on_update
def _(_ev):
    apply(slider.value)

@show_d.on_update
def _(_ev):
    apply(slider.value)

apply(0)
Path("/tmp/spikes/viewer/viser_ready.json").write_text(json.dumps({"port": 8766, "frames": len(frames), "frustums": len(frusta)}))
print("VISER_READY", flush=True)
while True:
    time.sleep(3600)
