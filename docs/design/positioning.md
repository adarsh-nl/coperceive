# Positioning

Checked 2026-10-15. Figures in the related-work table come from the GitHub API, repository licence files, and README lines fetched that day. They will go stale.

## Problem

Cooperative perception data has more than one agent in a scene. A vehicle and a drone can share a world frame and a clock and still keep their own sensors, poses, and boxes. Tools built for one ego vehicle make that second agent a special case: a second copy of the log, a naming convention, or a script that is not part of the data model.

Griffin is the first dataset this library reads, because it is an aerial-ground set with both agents in one scene and no HD map in the release. The library is not a Griffin-only parser. The scene model has N agents.

## Users

- A PhD researcher on JupyterHub or a shared GPU server. They open a notebook or a browser through the hub proxy or an SSH tunnel. They need a path prefix, a token when the bind is not loopback, and a process that does not require a cluster account for anyone else.
- An engineer at a workstation who wants the same library and the same local web page, without a hub in front of it.

Both users install one package. The web page is a local view of that package, not a hosted service.

## What this project is for

- A multi-agent scene model: one world frame, one integer microsecond clock, N agents, sensors on those agents, per-agent boxes, and a scene-level object only when a track id is actually shared.
- Cooperative checks that report time offset and shared ids. They do not fuse detections.
- A local web app for the jobs and files that today are terminal commands.

## What this project is not

- Training, detectors, or trackers.
- A fusion algorithm.
- Cloud hosting, accounts, or more than one user.
- An annotation editor.
- A simulator of communication delay, packet loss, or localisation noise. Griffin’s paper studies those. This library does not synthesise them.

## Related work

Overlap with [py123d](https://github.com/kesai-labs/py123d) is real: both aim at multi-modal driving data. This repository was written without installing, importing, or reading that project’s code, docs, or schema. The row below uses only the public GitHub description and licence metadata. Feature parity was not audited.

| Project | What it says it is | Licence evidence on 2026-10-15 | Stars |
| --- | --- | --- | --- |
| [Griffin toolkit](https://github.com/wang-jh18-SVM/Griffin) | GitHub description: “Griffin: Aerial-Ground Cooperative Detection and Tracking Benchmark”. | `LICENSE.txt` is MIT, copyright 2025 Jiahao Wang. API SPDX `MIT`. Pushed 2025-08-26. | 125 |
| [nuScenes devkit](https://github.com/nutonomy/nuscenes-devkit) | GitHub description: “The devkit of the nuScenes dataset.” | `LICENSE.txt` begins “Copyright 2021 Motional” and says Apache-2.0. API SPDX is `NOASSERTION` because the file is a short pointer. | 2814 |
| [OpenCOOD](https://github.com/DerrickXuNu/OpenCOOD) | GitHub description: “[ICRA 2022] An opensource framework for cooperative detection. Official implementation for OPV2V.” | `LICENSE` is an Academic Software License, © 2021 UCLA Mobility Lab. Academic or nonprofit research only. Commercial use asks for contact. API SPDX `NOASSERTION`. | 835 |
| [UniV2X](https://github.com/AIR-THU/UniV2X) | README title: “End-to-End Autonomous Driving through V2X Cooperation”. README highlight: “UniV2X is the first cooperative autonomous driving framework that seamlessly integrates all key driving modules across diverse driving views into a unified network.” Linked paper: arXiv 2404.00717. | API SPDX `Apache-2.0`. | 175 |
| [py123d](https://github.com/kesai-labs/py123d) | GitHub description: “123D: A Unified Library for Multi-Modal Autonomous Driving Data”. Homepage listed by the API: `https://kesai.eu/py123d/`. | API SPDX `Apache-2.0`. Pushed 2026-10-14. | 402 |
| [Foxglove Studio](https://github.com/foxglove/studio) | GitHub description: “Robotics visualization and debugging”. | Repository archived, API `archived: true`, pushed 2024-07-18, licence `null`. The 2024-03-10 post [Foxglove 2.0](https://foxglove.dev/blog/foxglove-2-0-unifying-robotics-observability) says they “discontinue development of the open source edition of Foxglove Studio” and that MCAP is unaffected. | 92 |
| [MCAP](https://github.com/foxglove/mcap) | GitHub description: “MCAP is a modular, performant, and serialization-agnostic container file format, useful for pub/sub and robotics applications.” | API SPDX `MIT`. | 1113 |
| [Rerun](https://github.com/rerun-io/rerun) | GitHub description: “Visualize, query, and stream to train on multimodal robotics data.” README title fetched the same day: “The data layer for physical AI”. README line: “Rerun uses an open-core model. Everything in this repository will stay open source and free (both as in beer and as in freedom).” | `Cargo.toml` has `license = "MIT OR Apache-2.0"`. README badges link `LICENSE-MIT` and `LICENSE-APACHE`. API SPDX lists only `Apache-2.0`. | 11595 |
| [viser](https://github.com/viser-project/viser) | GitHub description: “Web-based 3D visualization in Python”. The API returned `viser-project/viser`. | API SPDX `Apache-2.0`. | 2817 |

OpenCOOD’s academic-only licence is not a model for this project. Foxglove’s hosted product is not a substitute for a library Adarsh owns. Rerun and viser are viewers this project can call. They are not the data model.
