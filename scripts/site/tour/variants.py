"""Capture-only: five synthetic scenes laid out like each catalog dataset's agents.

All bytes are generated here. Each scene copies only the *shape* of a dataset
(which agents, which sensors), never its data. Usage: python variants.py OUT KIND
KIND in opv2v, v2xset, dair, v2v4real, griffin.
"""

from __future__ import annotations

import itertools
import math
import random
import sys
from pathlib import Path

import coperceive.demo.generate as g
import numpy as np
import rich_demo as r  # noqa: F401  (patches the generator: lidar, camera look)
from coperceive.se3 import SE3

KIND = sys.argv[2]
g.FRAMES = 40
Obj = g._Object
_rng = random.Random(11)


def _rpy_for(R):
    """rpy (deg, multiples of 90) whose from_rpy_deg matches rotation matrix R."""
    best = None
    for rr, pp, yy in itertools.product(range(-180, 181, 90), repeat=3):
        T = SE3.from_rpy_deg(rr, pp, yy)
        M = np.array([np.array(T.apply(v)) for v in ((1, 0, 0), (0, 1, 0), (0, 0, 1))]).T
        err = float(np.abs(M - R).sum())
        if best is None or err < best[0]:
            best = (err, [rr, pp, yy])
    assert best[0] < 1e-6, best
    return best[1]


def _cam_rot(yaw_deg=0.0, down=False):
    if down:  # optical z -> body -z, x -> body -y
        R = np.array([[0, -1, 0], [-1, 0, 0], [0, 0, -1]], dtype=float).T
        return _rpy_for(R)
    base = SE3.from_rpy_deg(-90, 0, -90)
    yaw = SE3.from_rpy_deg(0, 0, yaw_deg)
    T = yaw.compose(base)
    M = np.array([np.array(T.apply(v)) for v in ((1, 0, 0), (0, 1, 0), (0, 0, 1))]).T
    return _rpy_for(M)


# ---------- worlds ----------
def junction_static():
    S = [b for b in r.STATIC if b[3] != "kerb"]
    for side in (-1, 1):  # long kerbs, few boxes
        for a, b in ((-72, -9), (9, 72)):
            S.append((((a + b) / 2, side * 9.2, 0.075), (b - a, 0.3, 0.15), 0.0, "kerb"))
            S.append(((side * 9.2, (a + b) / 2, 0.075), (0.3, b - a, 0.15), 0.0, "kerb"))
    return S


def highway_static():
    S = []
    for side in (-1, 1):
        S.append(((0, side * 9.6, 0.45), (400, 0.4, 0.9), 0.0, "kerb"))  # barriers
        for x in range(-180, 181, 14):
            S.append(((x + _rng.uniform(-3, 3), side * 16, 3.5), (0.5, 0.5, 7.0), 0.0, "tree"))
            S.append(((x, side * 16, 7.5), (4.0, 4.0, 3.5), 0.0, "crown"))
        for x in range(-170, 171, 40):
            S.append(((x, side * 10.5, 4.0), (0.3, 0.3, 8.0), 0.0, "pole"))
    S.append(((60, 0, 6.5), (1.0, 22, 0.8), 0.0, "pole"))  # sign gantry
    return S


if KIND == "v2v4real":
    STATIC = highway_static()
    moving = (
        Obj("car-1", "car", 4.6, 1.9, 1.5, 0.0, (-20.0, -1.8), (26.0, 0.0)),
        Obj("car-2", "car", 4.5, 1.8, 1.6, 0.0, (-55.0, -5.4), (24.0, 0.0)),
        Obj("truck-1", "car", 9.5, 2.6, 3.4, 0.0, (10.0, -5.4), (22.0, 0.0)),
        Obj("car-3", "car", 4.4, 1.8, 1.5, 0.0, (30.0, -1.8), (27.5, 0.0)),
        Obj("car-4", "car", 4.6, 1.9, 1.5, 180.0, (120.0, 1.8), (-27.0, 0.0)),
        Obj("car-5", "car", 4.4, 1.8, 1.5, 180.0, (60.0, 5.4), (-25.0, 0.0)),
        Obj("van-1", "car", 5.5, 2.1, 2.3, 180.0, (150.0, 5.4), (-24.0, 0.0)),
        Obj("car-6", "car", 4.5, 1.8, 1.5, 0.0, (-90.0, -1.8), (28.0, 0.0)),
    )
else:
    STATIC = junction_static()
    moving = r._objects()
r.STATIC[:] = STATIC
g._objects = lambda: moving
r._objects = lambda: moving

# ---------- agents ----------
FRONT = ("front", (1.8, 0.0, 1.2), _cam_rot(0))
FOUR = [
    FRONT,
    ("left", (0.5, 0.9, 1.4), _cam_rot(90)),
    ("right", (0.5, -0.9, 1.4), _cam_rot(-90)),
    ("back", (-1.9, 0.0, 1.3), _cam_rot(180)),
]
DOWN = ("bottom", (0.0, 0.0, -0.2), _cam_rot(down=True))


def lin(x0, y0, z, vx, vy, yaw):
    return lambda f: SE3.from_rpy_deg(0, 0, yaw).translation(x0 + vx * f / 10, y0 + vy * f / 10, z)


if KIND == "opv2v":
    SCENE = "opv2v-style-junction"
    AG = [
        ("cav-1", "vehicle", lin(-35, -1.75, 1.9, 10.5, 0, 0), True, FOUR),
        ("cav-2", "vehicle", lin(1.75, -35, 1.9, 0, 10.5, 90), True, FOUR),
        ("cav-3", "vehicle", lin(30, 1.75, 1.9, -9.5, 0, 180), True, FOUR),
    ]
elif KIND == "v2xset":
    SCENE = "v2xset-style-junction"
    AG = [
        ("cav-1", "vehicle", lin(-35, -1.75, 1.9, 10.5, 0, 0), True, [FRONT]),
        ("cav-2", "vehicle", lin(-1.75, 32, 1.9, 0, -10, -90), True, [FRONT]),
        ("rsu", "roadside unit", lambda f: SE3.identity().translation(10.5, -10.5, 6.0), True, []),
    ]
elif KIND == "dair":
    SCENE = "dair-style-intersection"
    AG = [
        ("vehicle", "vehicle", lin(-40, -1.75, 1.9, 9.0, 0, 0), True, [FRONT]),
        (
            "infrastructure",
            "roadside unit",
            lambda f: SE3.from_rpy_deg(0, 0, 180).translation(14.0, -10.5, 6.5),
            True,
            [("pole-camera", (0.0, 0.0, 0.0), _cam_rot(0))],
        ),
    ]
elif KIND == "v2v4real":
    SCENE = "v2v4real-style-highway"
    AG = [
        ("ego", "vehicle", lin(-40, -1.8, 1.9, 25, 0, 0), True, []),
        ("cav", "vehicle", lin(-62, -1.8, 1.9, 25, 0, 0), True, []),
    ]
elif KIND == "griffin":
    SCENE = "griffin-style-aerial"
    AG = [
        ("vehicle", "vehicle", lin(-35, -1.75, 1.9, 10.5, 0, 0), True, [FRONT]),
        ("drone", "drone", lin(-42, -6.0, 32.0, 10.5, 0, 0), False, [DOWN, FRONT]),
    ]
else:
    raise SystemExit("unknown kind")

g.SCENE_ID = SCENE
POSES = {a[0]: a[2] for a in AG}
g._agent_pose = lambda agent_id, frame: POSES[agent_id](frame)

_el_ground = np.radians(np.linspace(-24.0, 6.0, r.EL))
_el_pole = np.radians(np.linspace(-40.0, 4.0, r.EL))


def lidar(agent_id, frame, objects, rng):
    r._el = _el_pole if agent_id in ("rsu", "infrastructure") else _el_ground
    return r._lidar(agent_id, frame, objects, rng)


g._lidar = lidar
_orig_cam = r._camera_image


def camera(agent_id, frame, mount):
    tr, rpy = mount
    pose = g._agent_pose(agent_id, frame)
    real = g._agent_pose

    # render with this mount by temporarily wrapping the agent pose
    def pose_for(a, f):
        return pose.compose(SE3.from_rpy_deg(*rpy).translation(*tr)).compose(
            SE3.from_rpy_deg(-90, 0, -90).translation(1.8, 0.0, 1.2).inverse()
        )

    g._agent_pose = pose_for
    try:
        return _orig_cam(agent_id, frame, None)
    finally:
        g._agent_pose = real


def write_agents(root: Path, objects, seed):
    ts = [i * 100_000 for i in range(g.FRAMES)]
    (root / "agents").mkdir(parents=True)
    agents_yaml = []
    for aid, role, posef, has_lidar, cams in AG:
        ar = root / "agents" / aid
        ar.mkdir(parents=True)
        sensors = []
        fixed = role == "roadside unit"
        if not fixed:
            (ar / "boxes.csv").write_text(
                "timestamp_us,track_id,category,x,y,z,length,width,height,yaw_deg\n"
                + "".join(
                    f"{t},{o.track_id},{o.category},"
                    f"{g._actor_at(o, t / 1e6)[0][0]},{g._actor_at(o, t / 1e6)[0][1]},"
                    f"{g._actor_at(o, t / 1e6)[0][2]},{o.length},{o.width},{o.height},{o.yaw_deg}\n"
                    for t in ts
                    for o in objects
                ),
                encoding="utf-8",
            )
            lines = []
            for i in range(g.FRAMES):
                p = posef(i)
                yaw = math.degrees(math.atan2(p.qz, p.qw) * 2.0)
                lines.append(f"{i * 100_000},{p.tx},{p.ty},{p.tz},0,0,{yaw}\n")
            (ar / "poses.csv").write_text(
                "timestamp_us,x,y,z,roll_deg,pitch_deg,yaw_deg\n" + "".join(lines), encoding="utf-8"
            )
        if has_lidar:
            (ar / "lidar").mkdir()
            sensors.append(
                {
                    "id": "lidar",
                    "modality": "lidar",
                    "files": f"agents/{aid}/lidar/{{frame:06d}}.bin",
                }
            )
        for cid, tr, rpy in cams:
            (ar / cid).mkdir()
            sensors.append(
                {
                    "id": cid,
                    "modality": "camera",
                    "files": f"agents/{aid}/{cid}/{{frame:06d}}.png",
                    "intrinsic": [g.FX, g.FY, g.CX, g.CY],
                    "image_size": [g.WIDTH, g.HEIGHT],
                    "body_from_sensor": {"translation_m": list(tr), "rotation_rpy_deg": rpy},
                }
            )
        for f in range(g.FRAMES):
            rng = random.Random(seed * 1_000_003 + f * 17 + sum(ord(c) for c in aid))
            if has_lidar:
                (ar / "lidar" / f"{f:06d}.bin").write_bytes(lidar(aid, f, objects, rng).tobytes())
            for cid, tr, rpy in cams:
                camera(aid, f, (tr, rpy)).save(ar / cid / f"{f:06d}.png")
        entry = {"id": aid, "role": role, "sensors": sensors}
        if fixed:
            p = posef(0)
            yaw = math.degrees(math.atan2(p.qz, p.qw) * 2.0)
            entry["fixed_pose"] = {
                "translation_m": [p.tx, p.ty, p.tz],
                "rotation_rpy_deg": [0, 0, round(yaw, 6)],
            }
        else:
            entry["poses"] = f"agents/{aid}/poses.csv"
            entry["boxes"] = f"agents/{aid}/boxes.csv"
        agents_yaml.append(entry)
    g._write_yaml(
        root / "scene.yaml",
        {
            "schema": "coperceive-generic-1",
            "scene_id": SCENE,
            "handedness": "right",
            "frame_rate_hz": g.RATE_HZ,
            "frame_count": g.FRAMES,
            "agents": agents_yaml,
        },
    )


g._write_agents = write_agents
# predictions: skip roadside agents and the drone's camera-only rows are fine
if __name__ == "__main__":
    res = g.create(Path(sys.argv[1]), seed=5)
    print(
        KIND,
        SCENE,
        "bytes",
        res.bytes,
        "s",
        round(res.seconds, 1),
        "mAP@0.5",
        round(res.map_50, 3),
        "(simulated)",
    )
