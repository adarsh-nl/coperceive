# Hero scene search

Read-only scan of the scenes on this machine, 2026-10-25. No frames, point clouds, or renders were copied. Nothing was built for the landing page.

## Decision

No scene that may appear on the public site has three or more recording agents. Task 1 stops here. The hero clip was not replaced.

Recording agents are platforms that store their own poses. Griffin `scene_infos.json` also has `num_vehicles` and `num_walkers`. Those counts are background traffic. The only id keys on every Griffin scene are `ego_vehicle_id` and `ego_drone_id`.

### (a) Best allowed two-agent scene

Griffin 40 m `Town03-005` on `Carla/Maps/Town03`. The vehicle and the drone both have poses. Only the vehicle has a lidar directory. The closest 10.0 s stretch is frames 12768–12868 (101 poses, 100 steps of 0.1 s). Mean horizontal distance 0.4053589977159965 m, minimum 0.03248961089103632 m, mean 3D distance 42.690537527256986 m. Horizontal distance is the ground gap. The 3D figure is larger because the drone is at its cruise altitude. This is the minimum mean horizontal distance over every 101-pose window in the 255 Griffin scenes below.

### (b) A three-agent dataset whose licence allows a public render

Not found.

OPV2V has 38 scenes with 3–7 vehicles, and V2XSet has 39 scenes with 3–5 agents including an infrastructure agent. Both are excluded from the public site. Neither publishes a data licence that allows redistributing a derived render. V2V4Real's code licence forbids transferring derivatives, and the local tree is two zip files, so no scene was measured. The DAIR-V2X-C directories on disk are empty, and the dataset README states no data licence. V2X-Sim's download page describes an RSU plus vehicles `id_1`–`id_5` and requires an access request. Its code repository is Apache-2.0. That is not a data grant to publish a render, and the dataset is not on this machine.

## Licences

| Dataset | What was checked | Public render |
| --- | --- | --- |
| Griffin | Dataset card `https://huggingface.co/datasets/wjh-svm/Griffin/raw/main/README.md` YAML `license: mit`, fetched 2026-10-25. Toolkit `https://raw.githubusercontent.com/wang-jh18-SVM/Griffin/main/LICENSE.txt` starts `MIT License` / `Copyright (c) [2025] [Jiahao Wang]`, fetched the same day. | Allowed. Derived renders of this dataset may be published. The page still must not print the dataset name. |
| OPV2V | `https://mobility-lab.seas.ucla.edu/opv2v/`, fetched 2026-10-25. The page says `Copyright © 2021 UCLA Mobility Lab. All Rights Reserved.` It states no licence. | Excluded. Do not export, commit, or show frames or derived point clouds. |
| V2XSet | `docs/datasets/opencood.md` records, as of 2026-10-18, that no data licence is published. The v2x-vit repository was not opened again. | Excluded, same rule as OPV2V. |
| V2V4Real | `https://raw.githubusercontent.com/ucla-mobility/V2V4Real/main/LICENSE`, fetched 2026-10-25. Academic software licence: nonprofit research use, and `you are prohibited from further transferring the Software — including any derivatives`. The README link in the catalog does not state a separate data licence. | Excluded. |
| DAIR-V2X | `https://raw.githubusercontent.com/AIR-THU/DAIR-V2X/main/README.md`, fetched 2026-10-25. The README gives download links and a citation. It does not state a data licence. The code repository's GitHub licence is Apache-2.0; that text was not treated as a data grant. | Not used. The local scene folders are empty. |

## How this was measured

Griffin. Four `scene_infos.json` files, vehicle side and drone side. Pose `x,y,z` were read from `pose/NNNNNN.json` for every listed frame. Lidar is whether `vehicle-side/lidar/lidar_top` and `drone-side/lidar` exist. They are dataset-level directories, not per scene. The 10 s window is 101 poses because the files step by 1 frame and the Griffin clock used in schema 1 is `time_us = frame_id * 100000` (10 Hz).

Jobs, partition `ps`:

| Job | What finished | Elapsed | Result |
| --- | --- | --- | --- |
| `640405` | first scan | `00:40:27` | `TIMEOUT`. No JSON. Directory stats were too slow. |
| `640446` | second scan | cancelled after a few minutes | Still one scene at a time. |
| `640454` | OPV2V and V2XSet, then Griffin | `00:20:27` | `TIMEOUT` during Griffin. OPV2V and V2XSet were written. Start `2026-10-25T21:59:33Z`. |
| `640464` | Griffin random | `00:14:21` | `COMPLETED`. End `2026-10-25T22:33:45Z`. 104 scenes. |
| `640481` | Griffin 25 m | `00:12:29` | `COMPLETED`. End `2026-10-25T22:52:53Z`. 47 scenes. |
| `640482` | Griffin 40 m | `00:13:31` | `COMPLETED`. End `2026-10-25T22:53:55Z`. 54 scenes. |
| `640483` | Griffin 55 m | `00:12:48` | `COMPLETED`. End `2026-10-25T22:53:12Z`. 50 scenes. |

OPV2V and V2XSet. One directory per agent under each scene. A lidar hit is a `lidar` directory or a `.pcd`/`.bin` name. The overlap is the intersection of numeric filename stems. `data_protocol.yaml` was searched for `town`, `map_name`, and `carla/maps`. None of the 131 files contain those. The schema-1 clock note says `time_us = stem * 50000`. That conversion is the note's assumption, not a new measurement, so the table keeps the stems.

Griffin scenes measured: 255. Every one has 2 recording agents. Scenes where the two sides do not list the same frames: Griffin-random Town06-011 (149 vehicle, 148 drone).

## Griffin scenes

| Dataset | Scene | Map | Agents with poses | Lidar | Overlapping frames | Licence |
| --- | --- | --- | --- | --- | --- | --- |
| Griffin-25m | Town03-000 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 11185–11333 (149 shared frames) | MIT |
| Griffin-25m | Town03-001 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 11471–11619 (149 shared frames) | MIT |
| Griffin-25m | Town03-002 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 11757–11905 (149 shared frames) | MIT |
| Griffin-25m | Town03-003 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 12043–12191 (149 shared frames) | MIT |
| Griffin-25m | Town03-004 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 12329–12477 (149 shared frames) | MIT |
| Griffin-25m | Town03-005 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 12615–12763 (149 shared frames) | MIT |
| Griffin-25m | Town03-006 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 12901–13049 (149 shared frames) | MIT |
| Griffin-25m | Town03-007 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 13187–13335 (149 shared frames) | MIT |
| Griffin-25m | Town03-008 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 13473–13621 (149 shared frames) | MIT |
| Griffin-25m | Town03-010 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 14046–14193 (148 shared frames) | MIT |
| Griffin-25m | Town03-011 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 14331–14479 (149 shared frames) | MIT |
| Griffin-25m | Town03-012 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 14617–14765 (149 shared frames) | MIT |
| Griffin-25m | Town03-013 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 14903–15051 (149 shared frames) | MIT |
| Griffin-25m | Town06-000 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 15521–15669 (149 shared frames) | MIT |
| Griffin-25m | Town06-001 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 15807–15955 (149 shared frames) | MIT |
| Griffin-25m | Town06-002 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 16093–16241 (149 shared frames) | MIT |
| Griffin-25m | Town06-003 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 16379–16527 (149 shared frames) | MIT |
| Griffin-25m | Town06-004 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 16665–16813 (149 shared frames) | MIT |
| Griffin-25m | Town06-005 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 16951–17099 (149 shared frames) | MIT |
| Griffin-25m | Town06-006 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 17237–17385 (149 shared frames) | MIT |
| Griffin-25m | Town06-007 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 17523–17671 (149 shared frames) | MIT |
| Griffin-25m | Town06-009 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 18095–18243 (149 shared frames) | MIT |
| Griffin-25m | Town06-010 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 18381–18529 (149 shared frames) | MIT |
| Griffin-25m | Town06-011 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 18667–18815 (149 shared frames) | MIT |
| Griffin-25m | Town06-012 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 18953–19101 (149 shared frames) | MIT |
| Griffin-25m | Town07-000 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 19856–20004 (149 shared frames) | MIT |
| Griffin-25m | Town07-001 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 20142–20290 (149 shared frames) | MIT |
| Griffin-25m | Town07-002 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 20428–20576 (149 shared frames) | MIT |
| Griffin-25m | Town07-003 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 20714–20862 (149 shared frames) | MIT |
| Griffin-25m | Town07-007 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 21858–22006 (149 shared frames) | MIT |
| Griffin-25m | Town07-009 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 22430–22578 (149 shared frames) | MIT |
| Griffin-25m | Town07-010 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 22716–22864 (149 shared frames) | MIT |
| Griffin-25m | Town07-011 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 23002–23150 (149 shared frames) | MIT |
| Griffin-25m | Town07-012 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 23288–23436 (149 shared frames) | MIT |
| Griffin-25m | Town07-013 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 23574–23722 (149 shared frames) | MIT |
| Griffin-25m | Town10HD-001 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 24478–24626 (149 shared frames) | MIT |
| Griffin-25m | Town10HD-002 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 24764–24912 (149 shared frames) | MIT |
| Griffin-25m | Town10HD-003 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 25050–25198 (149 shared frames) | MIT |
| Griffin-25m | Town10HD-004 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 25336–25484 (149 shared frames) | MIT |
| Griffin-25m | Town10HD-005 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 25622–25770 (149 shared frames) | MIT |
| Griffin-25m | Town10HD-007 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 26194–26342 (149 shared frames) | MIT |
| Griffin-25m | Town10HD-009 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 26768–26914 (147 shared frames) | MIT |
| Griffin-25m | Town10HD-010 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 27052–27200 (149 shared frames) | MIT |
| Griffin-25m | Town10HD-011 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 27338–27486 (149 shared frames) | MIT |
| Griffin-25m | Town10HD-012 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 27624–27772 (149 shared frames) | MIT |
| Griffin-25m | Town10HD-013 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 27910–28058 (149 shared frames) | MIT |
| Griffin-25m | Town10HD-014 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 28196–28344 (149 shared frames) | MIT |
| Griffin-40m | Town03-000 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 11306–11454 (149 shared frames) | MIT |
| Griffin-40m | Town03-001 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 11592–11740 (149 shared frames) | MIT |
| Griffin-40m | Town03-002 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 11878–12026 (149 shared frames) | MIT |
| Griffin-40m | Town03-003 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 12164–12312 (149 shared frames) | MIT |
| Griffin-40m | Town03-004 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 12450–12598 (149 shared frames) | MIT |
| Griffin-40m | Town03-005 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 12736–12884 (149 shared frames) | MIT |
| Griffin-40m | Town03-006 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 13022–13170 (149 shared frames) | MIT |
| Griffin-40m | Town03-007 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 13308–13456 (149 shared frames) | MIT |
| Griffin-40m | Town03-008 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 13594–13742 (149 shared frames) | MIT |
| Griffin-40m | Town03-009 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 13880–14028 (149 shared frames) | MIT |
| Griffin-40m | Town03-010 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 14166–14314 (149 shared frames) | MIT |
| Griffin-40m | Town03-011 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 14452–14600 (149 shared frames) | MIT |
| Griffin-40m | Town03-012 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 14738–14886 (149 shared frames) | MIT |
| Griffin-40m | Town03-013 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 15024–15172 (149 shared frames) | MIT |
| Griffin-40m | Town03-014 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 15310–15458 (149 shared frames) | MIT |
| Griffin-40m | Town06-000 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 15643–15791 (149 shared frames) | MIT |
| Griffin-40m | Town06-001 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 15929–16077 (149 shared frames) | MIT |
| Griffin-40m | Town06-002 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 16215–16363 (149 shared frames) | MIT |
| Griffin-40m | Town06-003 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 16501–16649 (149 shared frames) | MIT |
| Griffin-40m | Town06-004 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 16787–16935 (149 shared frames) | MIT |
| Griffin-40m | Town06-006 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 17359–17507 (149 shared frames) | MIT |
| Griffin-40m | Town06-007 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 17645–17793 (149 shared frames) | MIT |
| Griffin-40m | Town06-008 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 17931–18079 (149 shared frames) | MIT |
| Griffin-40m | Town06-010 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 18503–18651 (149 shared frames) | MIT |
| Griffin-40m | Town06-012 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 19075–19223 (149 shared frames) | MIT |
| Griffin-40m | Town06-013 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 19361–19509 (149 shared frames) | MIT |
| Griffin-40m | Town07-000 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 19979–20127 (149 shared frames) | MIT |
| Griffin-40m | Town07-002 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 20551–20699 (149 shared frames) | MIT |
| Griffin-40m | Town07-003 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 20837–20985 (149 shared frames) | MIT |
| Griffin-40m | Town07-004 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 21123–21271 (149 shared frames) | MIT |
| Griffin-40m | Town07-005 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 21409–21557 (149 shared frames) | MIT |
| Griffin-40m | Town07-006 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 21695–21843 (149 shared frames) | MIT |
| Griffin-40m | Town07-007 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 21981–22129 (149 shared frames) | MIT |
| Griffin-40m | Town07-008 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 22267–22415 (149 shared frames) | MIT |
| Griffin-40m | Town07-009 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 22553–22701 (149 shared frames) | MIT |
| Griffin-40m | Town07-010 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 22839–22987 (149 shared frames) | MIT |
| Griffin-40m | Town07-011 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 23125–23273 (149 shared frames) | MIT |
| Griffin-40m | Town07-012 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 23411–23559 (149 shared frames) | MIT |
| Griffin-40m | Town07-013 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 23697–23845 (149 shared frames) | MIT |
| Griffin-40m | Town07-014 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 23983–24131 (149 shared frames) | MIT |
| Griffin-40m | Town10HD-000 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 24431–24579 (149 shared frames) | MIT |
| Griffin-40m | Town10HD-001 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 24717–24865 (149 shared frames) | MIT |
| Griffin-40m | Town10HD-003 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 25289–25437 (149 shared frames) | MIT |
| Griffin-40m | Town10HD-004 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 25575–25723 (149 shared frames) | MIT |
| Griffin-40m | Town10HD-005 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 25861–26009 (149 shared frames) | MIT |
| Griffin-40m | Town10HD-006 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 26147–26295 (149 shared frames) | MIT |
| Griffin-40m | Town10HD-007 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 26433–26581 (149 shared frames) | MIT |
| Griffin-40m | Town10HD-008 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 26719–26867 (149 shared frames) | MIT |
| Griffin-40m | Town10HD-009 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 27005–27153 (149 shared frames) | MIT |
| Griffin-40m | Town10HD-010 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 27291–27439 (149 shared frames) | MIT |
| Griffin-40m | Town10HD-011 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 27577–27725 (149 shared frames) | MIT |
| Griffin-40m | Town10HD-012 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 27863–28011 (149 shared frames) | MIT |
| Griffin-40m | Town10HD-013 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 28149–28297 (149 shared frames) | MIT |
| Griffin-40m | Town10HD-014 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 28435–28583 (149 shared frames) | MIT |
| Griffin-55m | Town03-002 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 1287–1435 (149 shared frames) | MIT |
| Griffin-55m | Town03-003 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 1573–1721 (149 shared frames) | MIT |
| Griffin-55m | Town03-004 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 1859–2007 (149 shared frames) | MIT |
| Griffin-55m | Town03-009 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 3289–3437 (149 shared frames) | MIT |
| Griffin-55m | Town03-010 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 3575–3723 (149 shared frames) | MIT |
| Griffin-55m | Town03-011 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 3861–4009 (149 shared frames) | MIT |
| Griffin-55m | Town03-012 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 4147–4295 (149 shared frames) | MIT |
| Griffin-55m | Town03-014 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 4719–4867 (149 shared frames) | MIT |
| Griffin-55m | Town03-015 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 5005–5153 (149 shared frames) | MIT |
| Griffin-55m | Town03-017 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 5577–5725 (149 shared frames) | MIT |
| Griffin-55m | Town03-018 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 5863–6011 (149 shared frames) | MIT |
| Griffin-55m | Town06-000 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 6480–6628 (149 shared frames) | MIT |
| Griffin-55m | Town06-001 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 6766–6914 (149 shared frames) | MIT |
| Griffin-55m | Town06-002 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 7052–7200 (149 shared frames) | MIT |
| Griffin-55m | Town06-003 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 7338–7486 (149 shared frames) | MIT |
| Griffin-55m | Town06-004 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 7624–7772 (149 shared frames) | MIT |
| Griffin-55m | Town06-007 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 8482–8630 (149 shared frames) | MIT |
| Griffin-55m | Town06-008 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 8768–8916 (149 shared frames) | MIT |
| Griffin-55m | Town06-009 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 9054–9202 (149 shared frames) | MIT |
| Griffin-55m | Town06-010 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 9340–9488 (149 shared frames) | MIT |
| Griffin-55m | Town06-011 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 9626–9774 (149 shared frames) | MIT |
| Griffin-55m | Town06-012 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 9912–10060 (149 shared frames) | MIT |
| Griffin-55m | Town06-013 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 10198–10346 (149 shared frames) | MIT |
| Griffin-55m | Town07-000 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 10816–10964 (149 shared frames) | MIT |
| Griffin-55m | Town07-001 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 11102–11250 (149 shared frames) | MIT |
| Griffin-55m | Town07-002 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 11388–11536 (149 shared frames) | MIT |
| Griffin-55m | Town07-004 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 11960–12108 (149 shared frames) | MIT |
| Griffin-55m | Town07-005 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 12246–12394 (149 shared frames) | MIT |
| Griffin-55m | Town07-006 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 12532–12680 (149 shared frames) | MIT |
| Griffin-55m | Town07-007 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 12818–12966 (149 shared frames) | MIT |
| Griffin-55m | Town07-008 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 13104–13252 (149 shared frames) | MIT |
| Griffin-55m | Town07-009 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 13390–13538 (149 shared frames) | MIT |
| Griffin-55m | Town07-010 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 13676–13824 (149 shared frames) | MIT |
| Griffin-55m | Town07-011 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 13962–14110 (149 shared frames) | MIT |
| Griffin-55m | Town07-012 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 14248–14396 (149 shared frames) | MIT |
| Griffin-55m | Town07-013 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 14534–14682 (149 shared frames) | MIT |
| Griffin-55m | Town07-014 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 14820–14968 (149 shared frames) | MIT |
| Griffin-55m | Town10HD-001 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 15554–15702 (149 shared frames) | MIT |
| Griffin-55m | Town10HD-002 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 15840–15988 (149 shared frames) | MIT |
| Griffin-55m | Town10HD-003 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 16126–16274 (149 shared frames) | MIT |
| Griffin-55m | Town10HD-004 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 16412–16560 (149 shared frames) | MIT |
| Griffin-55m | Town10HD-005 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 16698–16846 (149 shared frames) | MIT |
| Griffin-55m | Town10HD-006 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 16984–17132 (149 shared frames) | MIT |
| Griffin-55m | Town10HD-007 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 17270–17418 (149 shared frames) | MIT |
| Griffin-55m | Town10HD-008 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 17556–17704 (149 shared frames) | MIT |
| Griffin-55m | Town10HD-009 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 17842–17990 (149 shared frames) | MIT |
| Griffin-55m | Town10HD-010 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 18128–18276 (149 shared frames) | MIT |
| Griffin-55m | Town10HD-011 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 18414–18562 (149 shared frames) | MIT |
| Griffin-55m | Town10HD-012 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 18700–18848 (149 shared frames) | MIT |
| Griffin-55m | Town10HD-013 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 18986–19134 (149 shared frames) | MIT |
| Griffin-random | Town03-000 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 29622–29770 (149 shared frames) | MIT |
| Griffin-random | Town03-001 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 29908–30056 (149 shared frames) | MIT |
| Griffin-random | Town03-002 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 30194–30342 (149 shared frames) | MIT |
| Griffin-random | Town03-003 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 30480–30628 (149 shared frames) | MIT |
| Griffin-random | Town03-004 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 30766–30914 (149 shared frames) | MIT |
| Griffin-random | Town03-005 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 31052–31200 (149 shared frames) | MIT |
| Griffin-random | Town03-006 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 31338–31486 (149 shared frames) | MIT |
| Griffin-random | Town03-007 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 31624–31772 (149 shared frames) | MIT |
| Griffin-random | Town03-008 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 31910–32058 (149 shared frames) | MIT |
| Griffin-random | Town03-009 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 32196–32344 (149 shared frames) | MIT |
| Griffin-random | Town03-010 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 32482–32630 (149 shared frames) | MIT |
| Griffin-random | Town03-011 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 32768–32916 (149 shared frames) | MIT |
| Griffin-random | Town03-012 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 33054–33202 (149 shared frames) | MIT |
| Griffin-random | Town03-013 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 33340–33488 (149 shared frames) | MIT |
| Griffin-random | Town03-014 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 33626–33774 (149 shared frames) | MIT |
| Griffin-random | Town03-015 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 33912–34060 (149 shared frames) | MIT |
| Griffin-random | Town03-016 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 34198–34346 (149 shared frames) | MIT |
| Griffin-random | Town03-017 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 34484–34632 (149 shared frames) | MIT |
| Griffin-random | Town03-018 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 34770–34918 (149 shared frames) | MIT |
| Griffin-random | Town03-019 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 35056–35204 (149 shared frames) | MIT |
| Griffin-random | Town03-020 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 35342–35490 (149 shared frames) | MIT |
| Griffin-random | Town03-021 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 35628–35776 (149 shared frames) | MIT |
| Griffin-random | Town03-022 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 35914–36062 (149 shared frames) | MIT |
| Griffin-random | Town03-023 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 36200–36348 (149 shared frames) | MIT |
| Griffin-random | Town03-024 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 36486–36634 (149 shared frames) | MIT |
| Griffin-random | Town03-025 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 36772–36920 (149 shared frames) | MIT |
| Griffin-random | Town03-026 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 37058–37206 (149 shared frames) | MIT |
| Griffin-random | Town03-027 | Carla/Maps/Town03 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 37344–37492 (149 shared frames) | MIT |
| Griffin-random | Town06-000 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 37793–37941 (149 shared frames) | MIT |
| Griffin-random | Town06-001 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 38079–38227 (149 shared frames) | MIT |
| Griffin-random | Town06-002 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 38365–38513 (149 shared frames) | MIT |
| Griffin-random | Town06-003 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 38651–38799 (149 shared frames) | MIT |
| Griffin-random | Town06-004 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 38937–39085 (149 shared frames) | MIT |
| Griffin-random | Town06-005 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 39223–39371 (149 shared frames) | MIT |
| Griffin-random | Town06-006 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 39509–39657 (149 shared frames) | MIT |
| Griffin-random | Town06-007 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 39795–39943 (149 shared frames) | MIT |
| Griffin-random | Town06-008 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 40081–40229 (149 shared frames) | MIT |
| Griffin-random | Town06-009 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 40367–40515 (149 shared frames) | MIT |
| Griffin-random | Town06-010 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 40653–40801 (149 shared frames) | MIT |
| Griffin-random | Town06-011 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 40939–41086 (148 shared frames; vehicle 149, drone 148) | MIT |
| Griffin-random | Town06-013 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 41511–41659 (149 shared frames) | MIT |
| Griffin-random | Town06-014 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 41797–41945 (149 shared frames) | MIT |
| Griffin-random | Town06-015 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 42083–42231 (149 shared frames) | MIT |
| Griffin-random | Town06-016 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 42369–42517 (149 shared frames) | MIT |
| Griffin-random | Town06-017 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 42655–42803 (149 shared frames) | MIT |
| Griffin-random | Town06-018 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 42941–43089 (149 shared frames) | MIT |
| Griffin-random | Town06-019 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 43227–43375 (149 shared frames) | MIT |
| Griffin-random | Town06-020 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 43513–43661 (149 shared frames) | MIT |
| Griffin-random | Town06-025 | Carla/Maps/Town06 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 44943–45091 (149 shared frames) | MIT |
| Griffin-random | Town07-000 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 45847–45995 (149 shared frames) | MIT |
| Griffin-random | Town07-001 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 46133–46281 (149 shared frames) | MIT |
| Griffin-random | Town07-002 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 46419–46567 (149 shared frames) | MIT |
| Griffin-random | Town07-003 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 46705–46853 (149 shared frames) | MIT |
| Griffin-random | Town07-004 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 46991–47139 (149 shared frames) | MIT |
| Griffin-random | Town07-005 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 47277–47425 (149 shared frames) | MIT |
| Griffin-random | Town07-006 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 47563–47711 (149 shared frames) | MIT |
| Griffin-random | Town07-007 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 47849–47997 (149 shared frames) | MIT |
| Griffin-random | Town07-008 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 48135–48283 (149 shared frames) | MIT |
| Griffin-random | Town07-009 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 48421–48569 (149 shared frames) | MIT |
| Griffin-random | Town07-010 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 48707–48855 (149 shared frames) | MIT |
| Griffin-random | Town07-011 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 48993–49141 (149 shared frames) | MIT |
| Griffin-random | Town07-012 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 49279–49427 (149 shared frames) | MIT |
| Griffin-random | Town07-013 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 49565–49713 (149 shared frames) | MIT |
| Griffin-random | Town07-014 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 49851–49999 (149 shared frames) | MIT |
| Griffin-random | Town07-015 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 50137–50285 (149 shared frames) | MIT |
| Griffin-random | Town07-016 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 50423–50571 (149 shared frames) | MIT |
| Griffin-random | Town07-017 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 50709–50857 (149 shared frames) | MIT |
| Griffin-random | Town07-018 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 50995–51143 (149 shared frames) | MIT |
| Griffin-random | Town07-019 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 51281–51429 (149 shared frames) | MIT |
| Griffin-random | Town07-020 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 51567–51715 (149 shared frames) | MIT |
| Griffin-random | Town07-021 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 51853–52001 (149 shared frames) | MIT |
| Griffin-random | Town07-022 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 52139–52287 (149 shared frames) | MIT |
| Griffin-random | Town07-023 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 52425–52573 (149 shared frames) | MIT |
| Griffin-random | Town07-024 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 52711–52859 (149 shared frames) | MIT |
| Griffin-random | Town07-025 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 52997–53145 (149 shared frames) | MIT |
| Griffin-random | Town07-026 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 53283–53431 (149 shared frames) | MIT |
| Griffin-random | Town07-027 | Carla/Maps/Town07 | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 53569–53717 (149 shared frames) | MIT |
| Griffin-random | Town10HD-000 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 53901–54049 (149 shared frames) | MIT |
| Griffin-random | Town10HD-001 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 54187–54335 (149 shared frames) | MIT |
| Griffin-random | Town10HD-002 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 54473–54621 (149 shared frames) | MIT |
| Griffin-random | Town10HD-003 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 54759–54907 (149 shared frames) | MIT |
| Griffin-random | Town10HD-004 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 55045–55193 (149 shared frames) | MIT |
| Griffin-random | Town10HD-005 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 55331–55479 (149 shared frames) | MIT |
| Griffin-random | Town10HD-006 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 55617–55765 (149 shared frames) | MIT |
| Griffin-random | Town10HD-007 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 55903–56051 (149 shared frames) | MIT |
| Griffin-random | Town10HD-008 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 56189–56337 (149 shared frames) | MIT |
| Griffin-random | Town10HD-009 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 56475–56623 (149 shared frames) | MIT |
| Griffin-random | Town10HD-010 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 56761–56909 (149 shared frames) | MIT |
| Griffin-random | Town10HD-011 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 57047–57195 (149 shared frames) | MIT |
| Griffin-random | Town10HD-012 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 57333–57481 (149 shared frames) | MIT |
| Griffin-random | Town10HD-013 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 57619–57767 (149 shared frames) | MIT |
| Griffin-random | Town10HD-014 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 57905–58053 (149 shared frames) | MIT |
| Griffin-random | Town10HD-015 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 58191–58339 (149 shared frames) | MIT |
| Griffin-random | Town10HD-016 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 58477–58625 (149 shared frames) | MIT |
| Griffin-random | Town10HD-017 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 58763–58911 (149 shared frames) | MIT |
| Griffin-random | Town10HD-019 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 59335–59483 (149 shared frames) | MIT |
| Griffin-random | Town10HD-020 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 59621–59769 (149 shared frames) | MIT |
| Griffin-random | Town10HD-021 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 59907–60055 (149 shared frames) | MIT |
| Griffin-random | Town10HD-022 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 60193–60341 (149 shared frames) | MIT |
| Griffin-random | Town10HD-023 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 60479–60627 (149 shared frames) | MIT |
| Griffin-random | Town10HD-024 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 60765–60913 (149 shared frames) | MIT |
| Griffin-random | Town10HD-025 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 61051–61199 (149 shared frames) | MIT |
| Griffin-random | Town10HD-026 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 61337–61485 (149 shared frames) | MIT |
| Griffin-random | Town10HD-027 | Carla/Maps/Town10HD | 2: vehicle, drone | vehicle lidar directory; drone has no lidar directory | 61623–61771 (149 shared frames) | MIT |

## OPV2V and V2XSet scenes

These rows are the exclusion list. They are not candidates for the page.

OPV2V: 73 scenes, agents per scene {2: 35, 3: 24, 4: 7, 5: 4, 6: 1, 7: 2}. V2XSet: 58 scenes, agents per scene {2: 19, 3: 25, 4: 10, 5: 4}. Every listed agent had lidar. Overlap stems: OPV2V 48–746, V2XSet 48–1038.

| Dataset | Scene | Map | Agents with poses | Lidar | Overlapping stems | Licence |
| --- | --- | --- | --- | --- | --- | --- |
| OPV2V test | 2021_08_18_19_48_05 | not in data_protocol.yaml | 2: 1054 vehicle, 1045 vehicle | every listed agent has lidar | stems 68–422 (178 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V test | 2021_08_20_21_10_24 | not in data_protocol.yaml | 3: 2005 vehicle, 1996 vehicle, 2014 vehicle | every listed agent has lidar | stems 69–263 (98 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V test | 2021_08_21_09_28_12 | not in data_protocol.yaml | 3: 623 vehicle, 632 vehicle, 641 vehicle | every listed agent has lidar | stems 69–361 (147 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V test | 2021_08_22_07_52_02 | not in data_protocol.yaml | 5: 3513 vehicle, 3477 vehicle, 3504 vehicle, 3486 vehicle, 3495 vehicle | every listed agent has lidar | stems 71–403 (167 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V test | 2021_08_22_09_08_29 | not in data_protocol.yaml | 2: 5933 vehicle, 5942 vehicle | every listed agent has lidar | stems 68–368 (151 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V test | 2021_08_22_21_41_24 | not in data_protocol.yaml | 3: 886 vehicle, 895 vehicle, 904 vehicle | every listed agent has lidar | stems 69–209 (71 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V test | 2021_08_23_12_58_19 | not in data_protocol.yaml | 2: 366 vehicle, 357 vehicle | every listed agent has lidar | stems 68–274 (104 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V test | 2021_08_23_15_19_19 | not in data_protocol.yaml | 2: 8699 vehicle, 8690 vehicle | every listed agent has lidar | stems 68–400 (167 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V test | 2021_08_23_16_06_26 | not in data_protocol.yaml | 3: 252 vehicle, 243 vehicle, 261 vehicle | every listed agent has lidar | stems 69–335 (134 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V test | 2021_08_23_17_22_47 | not in data_protocol.yaml | 3: 592 vehicle, 583 vehicle, 574 vehicle | every listed agent has lidar | stems 69–271 (102 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V test | 2021_08_23_21_07_10 | not in data_protocol.yaml | 3: 178 vehicle, 160 vehicle, 169 vehicle | every listed agent has lidar | stems 69–349 (141 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V test | 2021_08_23_21_47_19 | not in data_protocol.yaml | 3: 225 vehicle, 234 vehicle, 243 vehicle | every listed agent has lidar | stems 69–221 (77 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V test | 2021_08_24_07_45_41 | not in data_protocol.yaml | 4: 130 vehicle, 139 vehicle, 148 vehicle, 121 vehicle | every listed agent has lidar | stems 70–338 (135 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V test | 2021_08_24_11_37_54 | not in data_protocol.yaml | 3: 310 vehicle, 319 vehicle, 301 vehicle | every listed agent has lidar | stems 69–275 (104 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V test | 2021_08_24_20_09_18 | not in data_protocol.yaml | 2: 3174 vehicle, 3183 vehicle | every listed agent has lidar | stems 68–506 (220 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V test | 2021_08_24_20_49_54 | not in data_protocol.yaml | 2: 207 vehicle, 216 vehicle | every listed agent has lidar | stems 68–414 (174 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V test_culver_city | 2021_09_03_09_32_17 | not in data_protocol.yaml | 2: 302 vehicle, 311 vehicle | every listed agent has lidar | stems 6220–6398 (90 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V test_culver_city | 2021_09_03_10_53_32 | not in data_protocol.yaml | 2: 1773 vehicle, 1782 vehicle | every listed agent has lidar | stems 2751–2985 (118 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V test_culver_city | 2021_09_03_20_08_37 | not in data_protocol.yaml | 3: 1872 vehicle, 1854 vehicle, 1845 vehicle | every listed agent has lidar | stems 3464–3732 (135 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V test_culver_city | 2021_09_03_22_43_57 | not in data_protocol.yaml | 3: 832 vehicle, 841 vehicle, 823 vehicle | every listed agent has lidar | stems 3507–3919 (207 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V train | 2021_08_16_22_26_54 | not in data_protocol.yaml | 3: 641 vehicle, 659 vehicle, 650 vehicle | every listed agent has lidar | stems 69–905 (419 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V train | 2021_08_18_09_02_56 | not in data_protocol.yaml | 2: 440 vehicle, 449 vehicle | every listed agent has lidar | stems 68–214 (74 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V train | 2021_08_18_18_33_56 | not in data_protocol.yaml | 2: 702 vehicle, 693 vehicle | every listed agent has lidar | stems 68–224 (79 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V train | 2021_08_18_19_11_02 | not in data_protocol.yaml | 4: 1206 vehicle, 1215 vehicle, 1188 vehicle, 1197 vehicle | every listed agent has lidar | stems 70–312 (122 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V train | 2021_08_18_21_38_28 | not in data_protocol.yaml | 2: 1188 vehicle, 1179 vehicle | every listed agent has lidar | stems 68–550 (242 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V train | 2021_08_18_22_16_12 | not in data_protocol.yaml | 3: 1313 vehicle, 1322 vehicle, 1331 vehicle | every listed agent has lidar | stems 69–601 (267 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V train | 2021_08_18_23_23_19 | not in data_protocol.yaml | 2: 1456 vehicle, 1465 vehicle | every listed agent has lidar | stems 68–264 (99 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V train | 2021_08_19_15_07_39 | not in data_protocol.yaml | 2: 1590 vehicle, 1599 vehicle | every listed agent has lidar | stems 68–162 (48 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V train | 2021_08_20_16_20_46 | not in data_protocol.yaml | 2: 1729 vehicle, 1738 vehicle | every listed agent has lidar | stems 68–320 (127 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V train | 2021_08_20_20_39_00 | not in data_protocol.yaml | 3: 1234 vehicle, 1243 vehicle, 1252 vehicle | every listed agent has lidar | stems 69–287 (110 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V train | 2021_08_20_21_00_19 | not in data_protocol.yaml | 2: 1876 vehicle, 1867 vehicle | every listed agent has lidar | stems 68–274 (104 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V train | 2021_08_21_09_09_41 | not in data_protocol.yaml | 4: 485 vehicle, 476 vehicle, 503 vehicle, 494 vehicle | every listed agent has lidar | stems 70–416 (174 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V train | 2021_08_21_15_41_04 | not in data_protocol.yaml | 2: 4163 vehicle, 4172 vehicle | every listed agent has lidar | stems 68–390 (162 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V train | 2021_08_21_16_08_42 | not in data_protocol.yaml | 2: 7908 vehicle, 7917 vehicle | every listed agent has lidar | stems 68–454 (194 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V train | 2021_08_21_17_00_32 | not in data_protocol.yaml | 2: 2340 vehicle, 2331 vehicle | every listed agent has lidar | stems 68–356 (145 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V train | 2021_08_21_21_35_56 | not in data_protocol.yaml | 2: 2836 vehicle, 2845 vehicle | every listed agent has lidar | stems 68–316 (125 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V train | 2021_08_21_22_21_37 | not in data_protocol.yaml | 3: 2987 vehicle, 3005 vehicle, 2996 vehicle | every listed agent has lidar | stems 69–335 (134 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V train | 2021_08_22_06_43_37 | not in data_protocol.yaml | 4: 3152 vehicle, 3179 vehicle, 3170 vehicle, 3161 vehicle | every listed agent has lidar | stems 70–430 (181 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V train | 2021_08_22_07_24_12 | not in data_protocol.yaml | 4: 3323 vehicle, 3332 vehicle, 3314 vehicle, 3341 vehicle | every listed agent has lidar | stems 70–448 (190 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V train | 2021_08_22_08_39_02 | not in data_protocol.yaml | 2: 3873 vehicle, 3864 vehicle | every listed agent has lidar | stems 68–386 (160 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V train | 2021_08_22_09_43_53 | not in data_protocol.yaml | 3: 6863 vehicle, 6854 vehicle, 6872 vehicle | every listed agent has lidar | stems 69–243 (88 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V train | 2021_08_22_10_10_40 | not in data_protocol.yaml | 3: 7673 vehicle, 7655 vehicle, 7664 vehicle | every listed agent has lidar | stems 69–271 (102 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V train | 2021_08_22_10_46_58 | not in data_protocol.yaml | 3: 3652 vehicle, 3643 vehicle, 3661 vehicle | every listed agent has lidar | stems 69–321 (127 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V train | 2021_08_22_11_29_38 | not in data_protocol.yaml | 2: 354 vehicle, 345 vehicle | every listed agent has lidar | stems 68–248 (91 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V train | 2021_08_22_22_30_58 | not in data_protocol.yaml | 3: 1259 vehicle, 1250 vehicle, 1241 vehicle | every listed agent has lidar | stems 69–259 (96 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V train | 2021_08_23_10_47_16 | not in data_protocol.yaml | 2: 5606 vehicle, 5597 vehicle | every listed agent has lidar | stems 68–368 (151 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V train | 2021_08_23_11_06_41 | not in data_protocol.yaml | 2: 2476 vehicle, 2485 vehicle | every listed agent has lidar | stems 68–194 (64 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V train | 2021_08_23_11_22_46 | not in data_protocol.yaml | 3: 6721 vehicle, 6730 vehicle, 6712 vehicle | every listed agent has lidar | stems 69–379 (156 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V train | 2021_08_23_12_13_48 | not in data_protocol.yaml | 2: 209 vehicle, 200 vehicle | every listed agent has lidar | stems 68–230 (82 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V train | 2021_08_23_13_10_47 | not in data_protocol.yaml | 2: 524 vehicle, 533 vehicle | every listed agent has lidar | stems 68–202 (68 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V train | 2021_08_23_16_42_39 | not in data_protocol.yaml | 2: 10719 vehicle, 10710 vehicle | every listed agent has lidar | stems 68–248 (91 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V train | 2021_08_23_17_07_55 | not in data_protocol.yaml | 3: 407 vehicle, 425 vehicle, 416 vehicle | every listed agent has lidar | stems 69–239 (86 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V train | 2021_08_23_19_27_57 | not in data_protocol.yaml | 2: 12607 vehicle, 12598 vehicle | every listed agent has lidar | stems 68–204 (69 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V train | 2021_08_23_20_47_11 | not in data_protocol.yaml | 2: 109 vehicle, 118 vehicle | every listed agent has lidar | stems 68–242 (88 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V train | 2021_08_23_22_31_01 | not in data_protocol.yaml | 4: 966 vehicle, 939 vehicle, 957 vehicle, 948 vehicle | every listed agent has lidar | stems 70–294 (113 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V train | 2021_08_23_23_08_17 | not in data_protocol.yaml | 3: 56 vehicle, 65 vehicle, 47 vehicle | every listed agent has lidar | stems 69–273 (103 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V train | 2021_08_24_09_25_42 | not in data_protocol.yaml | 2: 1693 vehicle, 1702 vehicle | every listed agent has lidar | stems 68–214 (74 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V train | 2021_08_24_09_58_32 | not in data_protocol.yaml | 2: 727 vehicle, 718 vehicle | every listed agent has lidar | stems 68–306 (120 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V train | 2021_08_24_12_19_30 | not in data_protocol.yaml | 3: 4075 vehicle, 4066 vehicle, 4084 vehicle | every listed agent has lidar | stems 69–727 (330 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V train | 2021_08_24_21_29_28 | not in data_protocol.yaml | 3: 4805 vehicle, 4796 vehicle, 4814 vehicle | every listed agent has lidar | stems 69–329 (131 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V train | 2021_09_09_13_20_58 | not in data_protocol.yaml | 4: 1252 vehicle, 1260 vehicle, 1268 vehicle, 1244 vehicle | every listed agent has lidar | stems 70–850 (391 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V train | 2021_09_09_22_21_11 | not in data_protocol.yaml | 6: 6273 vehicle, 6309 vehicle, 6300 vehicle, 6264 vehicle, 6291 vehicle, 6282 vehicle | every listed agent has lidar | stems 72–378 (154 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V train | 2021_09_09_23_21_21 | not in data_protocol.yaml | 5: 6880 vehicle, 6889 vehicle, 6862 vehicle, 6871 vehicle, 6853 vehicle | every listed agent has lidar | stems 71–971 (451 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V train | 2021_09_10_12_07_11 | not in data_protocol.yaml | 5: 207 vehicle, 225 vehicle, 243 vehicle, 234 vehicle, 216 vehicle | every listed agent has lidar | stems 71–835 (383 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V validate | 2021_08_20_21_48_35 | not in data_protocol.yaml | 5: 2167 vehicle, 2158 vehicle, 2185 vehicle, 2149 vehicle, 2176 vehicle | every listed agent has lidar | stems 71–293 (112 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V validate | 2021_08_21_17_30_41 | not in data_protocol.yaml | 3: 2506 vehicle, 2488 vehicle, 2497 vehicle | every listed agent has lidar | stems 69–381 (157 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V validate | 2021_08_22_13_37_16 | not in data_protocol.yaml | 2: 488 vehicle, 479 vehicle | every listed agent has lidar | stems 68–336 (135 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V validate | 2021_08_22_22_01_17 | not in data_protocol.yaml | 2: 1724 vehicle, 1715 vehicle | every listed agent has lidar | stems 68–470 (202 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V validate | 2021_08_23_10_51_24 | not in data_protocol.yaml | 2: 209 vehicle, 200 vehicle | every listed agent has lidar | stems 68–194 (64 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V validate | 2021_08_23_13_17_21 | not in data_protocol.yaml | 2: 7228 vehicle, 7219 vehicle | every listed agent has lidar | stems 68–162 (48 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V validate | 2021_08_23_19_42_07 | not in data_protocol.yaml | 2: 12843 vehicle, 12834 vehicle | every listed agent has lidar | stems 68–180 (57 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V validate | 2021_09_09_19_27_35 | not in data_protocol.yaml | 7: 2341 vehicle, 2314 vehicle, 2332 vehicle, 2323 vehicle, 2359 vehicle, 2368 vehicle, 2350 vehicle | every listed agent has lidar | stems 73–989 (459 stems in every agent) | excluded; no data licence on the dataset page |
| OPV2V validate | 2021_09_11_00_33_16 | not in data_protocol.yaml | 7: 1016 vehicle, 989 vehicle, 980 vehicle, 1007 vehicle, 998 vehicle, 971 vehicle, 962 vehicle | every listed agent has lidar | stems 73–1563 (746 stems in every agent) | excluded; no data licence on the dataset page |
| V2XSet test | 2021_08_18_19_48_05 | not in data_protocol.yaml | 2: 3444 vehicle, 3435 vehicle | every listed agent has lidar | stems 68–422 (178 stems in every agent) | excluded; no data licence recorded |
| V2XSet test | 2021_08_20_21_48_35 | not in data_protocol.yaml | 3: 2975 vehicle, 2984 vehicle, -1 infrastructure | every listed agent has lidar | stems 70–274 (103 stems in every agent) | excluded; no data licence recorded |
| V2XSet test | 2021_08_21_09_28_12 | not in data_protocol.yaml | 3: 9353 vehicle, 9371 vehicle, 9362 vehicle | every listed agent has lidar | stems 69–243 (88 stems in every agent) | excluded; no data licence recorded |
| V2XSet test | 2021_08_22_07_52_02 | not in data_protocol.yaml | 5: 5428 vehicle, 5455 vehicle, 5446 vehicle, 5464 vehicle, 5437 vehicle | every listed agent has lidar | stems 71–403 (167 stems in every agent) | excluded; no data licence recorded |
| V2XSet test | 2021_08_22_09_08_29 | not in data_protocol.yaml | 2: 8173 vehicle, 8182 vehicle | every listed agent has lidar | stems 68–178 (56 stems in every agent) | excluded; no data licence recorded |
| V2XSet test | 2021_08_22_21_41_24 | not in data_protocol.yaml | 2: -1 infrastructure, 5887 vehicle | every listed agent has lidar | stems 69–223 (78 stems in every agent) | excluded; no data licence recorded |
| V2XSet test | 2021_08_23_12_58_19 | not in data_protocol.yaml | 3: -1 infrastructure, 7520 vehicle, 7529 vehicle | every listed agent has lidar | stems 68–274 (104 stems in every agent) | excluded; no data licence recorded |
| V2XSet test | 2021_08_23_15_19_19 | not in data_protocol.yaml | 3: 9798 vehicle, 9789 vehicle, -1 infrastructure | every listed agent has lidar | stems 68–328 (131 stems in every agent) | excluded; no data licence recorded |
| V2XSet test | 2021_08_23_17_22_47 | not in data_protocol.yaml | 3: -1 infrastructure, 10675 vehicle, 10684 vehicle | every listed agent has lidar | stems 68–260 (97 stems in every agent) | excluded; no data licence recorded |
| V2XSet test | 2021_08_23_21_07_10 | not in data_protocol.yaml | 4: 56 vehicle, -1 infrastructure, 65 vehicle, 47 vehicle | every listed agent has lidar | stems 69–359 (146 stems in every agent) | excluded; no data licence recorded |
| V2XSet test | 2021_08_23_21_47_19 | not in data_protocol.yaml | 4: -1 infrastructure, 160 vehicle, 142 vehicle, 151 vehicle | every listed agent has lidar | stems 69–221 (77 stems in every agent) | excluded; no data licence recorded |
| V2XSet test | 2021_08_24_07_45_41 | not in data_protocol.yaml | 4: 648 vehicle, 657 vehicle, 639 vehicle, 630 vehicle | every listed agent has lidar | stems 70–1008 (470 stems in every agent) | excluded; no data licence recorded |
| V2XSet test | 2021_08_24_11_37_54 | not in data_protocol.yaml | 3: 13249 vehicle, 13240 vehicle, 13231 vehicle | every listed agent has lidar | stems 69–275 (104 stems in every agent) | excluded; no data licence recorded |
| V2XSet test | 2021_08_24_20_09_18 | not in data_protocol.yaml | 2: 13973 vehicle, 13982 vehicle | every listed agent has lidar | stems 68–564 (249 stems in every agent) | excluded; no data licence recorded |
| V2XSet test | 2021_08_24_20_49_54 | not in data_protocol.yaml | 2: 14133 vehicle, 14142 vehicle | every listed agent has lidar | stems 68–298 (116 stems in every agent) | excluded; no data licence recorded |
| V2XSet test | 2021_09_03_09_32_17 | not in data_protocol.yaml | 2: 42 vehicle, 33 vehicle | every listed agent has lidar | stems 996–1266 (136 stems in every agent) | excluded; no data licence recorded |
| V2XSet test | 2021_09_03_10_53_32 | not in data_protocol.yaml | 3: 105 vehicle, -1 infrastructure, 114 vehicle | every listed agent has lidar | stems 293–593 (151 stems in every agent) | excluded; no data licence recorded |
| V2XSet test | 2021_09_09_23_21_21 | not in data_protocol.yaml | 2: -1 infrastructure, 12485 vehicle | every listed agent has lidar | stems 70–552 (242 stems in every agent) | excluded; no data licence recorded |
| V2XSet test | 2021_10_26_22_12_49 | not in data_protocol.yaml | 5: 377 vehicle, -1 infrastructure, 359 vehicle, 368 vehicle, 386 vehicle | every listed agent has lidar | stems 385–665 (141 stems in every agent) | excluded; no data licence recorded |
| V2XSet train | 2021_08_18_19_11_02 | not in data_protocol.yaml | 4: 3260 vehicle, 3242 vehicle, 3251 vehicle, 3269 vehicle | every listed agent has lidar | stems 70–494 (213 stems in every agent) | excluded; no data licence recorded |
| V2XSet train | 2021_08_18_21_38_28 | not in data_protocol.yaml | 2: 8795 vehicle, 8786 vehicle | every listed agent has lidar | stems 68–550 (242 stems in every agent) | excluded; no data licence recorded |
| V2XSet train | 2021_08_18_22_16_12 | not in data_protocol.yaml | 3: 8938 vehicle, 8920 vehicle, 8929 vehicle | every listed agent has lidar | stems 69–1301 (617 stems in every agent) | excluded; no data licence recorded |
| V2XSet train | 2021_08_18_23_23_19 | not in data_protocol.yaml | 2: 1144 vehicle, 1153 vehicle | every listed agent has lidar | stems 68–434 (184 stems in every agent) | excluded; no data licence recorded |
| V2XSet train | 2021_08_20_16_20_46 | not in data_protocol.yaml | 2: 1392 vehicle, 1401 vehicle | every listed agent has lidar | stems 68–226 (80 stems in every agent) | excluded; no data licence recorded |
| V2XSet train | 2021_08_20_20_39_00 | not in data_protocol.yaml | 2: 1752 vehicle, -1 infrastructure | every listed agent has lidar | stems 67–817 (376 stems in every agent) | excluded; no data licence recorded |
| V2XSet train | 2021_08_20_21_00_19 | not in data_protocol.yaml | 2: 2244 vehicle, 2253 vehicle | every listed agent has lidar | stems 68–354 (144 stems in every agent) | excluded; no data licence recorded |
| V2XSet train | 2021_08_21_09_09_41 | not in data_protocol.yaml | 4: 9215 vehicle, 9206 vehicle, 9233 vehicle, 9224 vehicle | every listed agent has lidar | stems 70–472 (202 stems in every agent) | excluded; no data licence recorded |
| V2XSet train | 2021_08_21_15_41_04 | not in data_protocol.yaml | 2: 3833 vehicle, 3842 vehicle | every listed agent has lidar | stems 68–236 (85 stems in every agent) | excluded; no data licence recorded |
| V2XSet train | 2021_08_21_17_00_32 | not in data_protocol.yaml | 2: 4122 vehicle, 4131 vehicle | every listed agent has lidar | stems 68–268 (101 stems in every agent) | excluded; no data licence recorded |
| V2XSet train | 2021_08_21_21_35_56 | not in data_protocol.yaml | 3: 4573 vehicle, 4564 vehicle, -1 infrastructure | every listed agent has lidar | stems 68–538 (236 stems in every agent) | excluded; no data licence recorded |
| V2XSet train | 2021_08_21_22_21_37 | not in data_protocol.yaml | 3: 4740 vehicle, -1 infrastructure, 4722 vehicle | every listed agent has lidar | stems 69–1001 (467 stems in every agent) | excluded; no data licence recorded |
| V2XSet train | 2021_08_22_07_24_12 | not in data_protocol.yaml | 4: 5283 vehicle, 5274 vehicle, 5292 vehicle, 5265 vehicle | every listed agent has lidar | stems 70–448 (190 stems in every agent) | excluded; no data licence recorded |
| V2XSet train | 2021_08_22_08_39_02 | not in data_protocol.yaml | 2: 8032 vehicle, 8041 vehicle | every listed agent has lidar | stems 68–292 (113 stems in every agent) | excluded; no data licence recorded |
| V2XSet train | 2021_08_22_09_43_53 | not in data_protocol.yaml | 3: 8314 vehicle, 8332 vehicle, 8323 vehicle | every listed agent has lidar | stems 69–239 (86 stems in every agent) | excluded; no data licence recorded |
| V2XSet train | 2021_08_22_10_10_40 | not in data_protocol.yaml | 3: 8473 vehicle, 8464 vehicle, 8482 vehicle | every listed agent has lidar | stems 69–271 (102 stems in every agent) | excluded; no data licence recorded |
| V2XSet train | 2021_08_22_22_30_58 | not in data_protocol.yaml | 4: 6432 vehicle, 6441 vehicle, 6450 vehicle, -1 infrastructure | every listed agent has lidar | stems 69–275 (104 stems in every agent) | excluded; no data licence recorded |
| V2XSet train | 2021_08_23_10_47_16 | not in data_protocol.yaml | 3: 9634 vehicle, 9643 vehicle, -1 infrastructure | every listed agent has lidar | stems 68–216 (75 stems in every agent) | excluded; no data licence recorded |
| V2XSet train | 2021_08_23_11_06_41 | not in data_protocol.yaml | 3: 7084 vehicle, -1 infrastructure, 7075 vehicle | every listed agent has lidar | stems 68–194 (64 stems in every agent) | excluded; no data licence recorded |
| V2XSet train | 2021_08_23_11_22_46 | not in data_protocol.yaml | 4: 10066 vehicle, 10075 vehicle, -1 infrastructure, 10084 vehicle | every listed agent has lidar | stems 69–433 (183 stems in every agent) | excluded; no data licence recorded |
| V2XSet train | 2021_08_23_12_13_48 | not in data_protocol.yaml | 3: -1 infrastructure, 7365 vehicle, 7356 vehicle | every listed agent has lidar | stems 68–230 (82 stems in every agent) | excluded; no data licence recorded |
| V2XSet train | 2021_08_23_13_10_47 | not in data_protocol.yaml | 3: 7694 vehicle, 7703 vehicle, -1 infrastructure | every listed agent has lidar | stems 68–202 (68 stems in every agent) | excluded; no data licence recorded |
| V2XSet train | 2021_08_23_16_42_39 | not in data_protocol.yaml | 2: 10354 vehicle, -1 infrastructure | every listed agent has lidar | stems 67–233 (84 stems in every agent) | excluded; no data licence recorded |
| V2XSet train | 2021_08_23_17_07_55 | not in data_protocol.yaml | 2: -1 infrastructure, 10506 vehicle | every listed agent has lidar | stems 67–297 (116 stems in every agent) | excluded; no data licence recorded |
| V2XSet train | 2021_08_23_19_27_57 | not in data_protocol.yaml | 3: 47 vehicle, -1 infrastructure, 56 vehicle | every listed agent has lidar | stems 68–204 (69 stems in every agent) | excluded; no data licence recorded |
| V2XSet train | 2021_08_23_20_47_11 | not in data_protocol.yaml | 3: -1 infrastructure, 418 vehicle, 409 vehicle | every listed agent has lidar | stems 68–242 (88 stems in every agent) | excluded; no data licence recorded |
| V2XSet train | 2021_08_23_22_31_01 | not in data_protocol.yaml | 5: 270 vehicle, -1 infrastructure, 252 vehicle, 279 vehicle, 261 vehicle | every listed agent has lidar | stems 70–294 (113 stems in every agent) | excluded; no data licence recorded |
| V2XSet train | 2021_08_23_23_08_17 | not in data_protocol.yaml | 3: 574 vehicle, 556 vehicle, 565 vehicle | every listed agent has lidar | stems 69–273 (103 stems in every agent) | excluded; no data licence recorded |
| V2XSet train | 2021_08_24_09_25_42 | not in data_protocol.yaml | 2: 12963 vehicle, 12954 vehicle | every listed agent has lidar | stems 68–214 (74 stems in every agent) | excluded; no data licence recorded |
| V2XSet train | 2021_08_24_09_58_32 | not in data_protocol.yaml | 2: 13090 vehicle, 13099 vehicle | every listed agent has lidar | stems 68–306 (120 stems in every agent) | excluded; no data licence recorded |
| V2XSet train | 2021_08_24_12_19_30 | not in data_protocol.yaml | 3: 13409 vehicle, 13391 vehicle, 13400 vehicle | every listed agent has lidar | stems 69–875 (404 stems in every agent) | excluded; no data licence recorded |
| V2XSet train | 2021_09_09_13_20_58 | not in data_protocol.yaml | 4: 11340 vehicle, 11356 vehicle, 11364 vehicle, 11348 vehicle | every listed agent has lidar | stems 70–1010 (471 stems in every agent) | excluded; no data licence recorded |
| V2XSet train | 2021_09_09_19_27_35 | not in data_protocol.yaml | 4: 15027 vehicle, -1 infrastructure, 15018 vehicle, 15009 vehicle | every listed agent has lidar | stems 69–2143 (1038 stems in every agent) | excluded; no data licence recorded |
| V2XSet validate | 2021_08_21_17_30_41 | not in data_protocol.yaml | 3: 4297 vehicle, 4279 vehicle, 4288 vehicle | every listed agent has lidar | stems 69–403 (168 stems in every agent) | excluded; no data licence recorded |
| V2XSet validate | 2021_08_22_22_01_17 | not in data_protocol.yaml | 3: 6178 vehicle, -1 infrastructure, 6169 vehicle | every listed agent has lidar | stems 68–280 (107 stems in every agent) | excluded; no data licence recorded |
| V2XSet validate | 2021_08_23_10_51_24 | not in data_protocol.yaml | 3: 6763 vehicle, -1 infrastructure, 6754 vehicle | every listed agent has lidar | stems 68–194 (64 stems in every agent) | excluded; no data licence recorded |
| V2XSet validate | 2021_08_23_13_17_21 | not in data_protocol.yaml | 3: 7863 vehicle, -1 infrastructure, 7872 vehicle | every listed agent has lidar | stems 68–162 (48 stems in every agent) | excluded; no data licence recorded |
| V2XSet validate | 2021_08_23_19_42_07 | not in data_protocol.yaml | 3: 142 vehicle, -1 infrastructure, 133 vehicle | every listed agent has lidar | stems 68–202 (68 stems in every agent) | excluded; no data licence recorded |
| V2XSet validate | 2021_09_09_22_21_11 | not in data_protocol.yaml | 5: 15553 vehicle, 15571 vehicle, 15562 vehicle, 15589 vehicle, 15580 vehicle | every listed agent has lidar | stems 71–377 (154 stems in every agent) | excluded; no data licence recorded |

## Not a scene directory

- V2V4Real at `/datasets/eemcs/ps/cv/opencood/v2v4real-from-ucla-box/v2vreal-20240806T195152Z-003/v2vreal` contains `Day20_test.zip` and `opv2v_pointpillar.zip` only. No scene was opened.
- DAIR-V2X-C at `/datasets/eemcs/ps/cv/air-thu/dair-v2x-c_raw/DAIR-V2X (CVPR2022)/DAIR-V2X-C` has `Example` and `Full Dataset (train&val)`, both empty directories.

## Schema-1 copies already converted

These are conversions of recordings above, not extra agents. `results/627093/scenes` and `results/628385/scenes` are the same ten Griffin scene ids. Point rows in `results/628385` are the vehicle only (149 rows each). Both agents have 149 pose rows on Town03-000, from 1118500000 µs through 1133300000 µs. Packed copies of that scene also sit at `results/import-test/637715/{full,preview}/Town03-000` and `results/reference-stepb-20261020/staged-scenes/Town03-000`.

The two OpenCOOD conversions, measured from `poses.parquet` and `point_index.parquet` in `results/629223/scenes`:

| Scene | Agents | Lidar point rows | Pose overlap |
| --- | --- | --- | --- |
| `2021_08_20_21_10_24` | vehicles 1996, 2005, 2014 | all three, 295 rows | 98 times, 3450000–13150000 µs |
| `2021_08_20_20_39_00` | infrastructure `-1`, vehicle `1752` | both, 752 rows | 376 times, 3350000–40850000 µs |

`results/629196` and `results/629227` hold the same two scene ids. Where a converted manifest stores `map_name`, it is `Carla/Maps/Town06`. The `629196` copy of `2021_08_20_20_39_00` stores only `split: train` on both agents.

## Own CARLA recording, checked 2026-10-26

Adarsh asked for a scene recorded in CARLA 0.9.15. The simulator is not on this cluster, so no recorder was written and no GPU job was submitted.

Checked on the login node:

- `module avail` has no CARLA or Unreal module (Modules 5.0.1).
- `import carla` in `/home/nanjaiyalathaa/coperceive/.venv` (CPython 3.13.14) raises `ModuleNotFoundError`.
- `~/wheelhouse` has no file whose name contains `carla`.
- No `CarlaUE4` binary, CARLA tarball, or `.sif` under `/home/nanjaiyalathaa` (depth 2), or under `/opt`, `/usr/local`, `/software`, `/apps`, or `/datasets/eemcs/ps` (depth 3).
- `~/maps-src/carla-0.9.15/` has OpenDRIVE files only (`Town03.xodr` and the other towns). That is not the simulator.
- The repo has no `scripts/sim` recorder. Matches for `carla` are the map tools and dataset notes.
- Partition `ps` allows account `ps` and has Lovelace GPUs: 4 on `hpc-node11` and `hpc-node12`, 3 on `hpc-node14` (`sinfo`, `scontrol show partition ps`). `nvidia-smi` is not installed on the login node. Off-screen mode was not run.

Off-screen mode is documented, not tested here. The 0.9.15 rendering page, fetched 2026-10-26 from `https://carla.readthedocs.io/en/0.9.15/adv_rendering_options/`, says `./CarlaUE4.sh -RenderOffScreen` for 0.9.12 and later, and that GPU sensors return data in that mode. The same page says no-rendering mode returns empty data for GPU sensors.

### Licences, fetched 2026-10-26

| What | Source | Exact terms |
| --- | --- | --- |
| Simulator code | `https://github.com/carla-simulator/carla/blob/0.9.15/LICENSE` and the Licenses section of `https://github.com/carla-simulator/carla/blob/0.9.15/README.md` | The `LICENSE` file is the MIT licence, copyright 2017 Computer Vision Center (CVC) at the Universitat Autònoma de Barcelona (UAB). The README says: "CARLA specific code is distributed under MIT License." |
| Assets, including towns and maps | The same README Licenses section | "CARLA specific assets are distributed under CC-BY License." It does not say CC-BY 4.0, and the git `LICENSE` file is the MIT text only. A 2024-07-31 maintainer reply at `https://github.com/carla-simulator/carla/discussions/7984` repeats "CC-BY" with no version. |

The Python wheels named on `https://pypi.org/simple/carla/`, fetched 2026-10-26, for 0.9.15 on Linux are `cp27`, `cp37`, `cp38`, `cp39`, and `cp310`. There is no `cp313` wheel. None of them is in `~/wheelhouse`. They were not installed. Compute nodes are not to use PyPI, and a client wheel without `CarlaUE4.sh` cannot record a scene.

