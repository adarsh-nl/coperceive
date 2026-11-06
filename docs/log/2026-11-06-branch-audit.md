# Branch audit, 2026-11-06

Scope: the branches named in the request. Checks were `git log --oneline
origin/release/v0.1.1..origin/<branch>`, `git diff --stat
origin/release/v0.1.1...origin/<branch>`, path/content searches for the files
and functions each branch adds, and a binary-file check. Nothing was deleted,
pushed, rebased or merged.

## Important: nine of the ten named refs do not exist

`git ls-remote --heads origin`, `gh api repos/adarsh-nl/coperceive/branches`
and the full `git branch -r` list contain only **one** of the named branches:
`feat/site-hero-multi`. These have no remote ref (and no open PR):

- `agent/antigravity-native-export`
- `agent/antigravity-panel-states`
- `agent/antigravity-theme-a11y`
- `agent/deepseek-cluster-datasets`
- `agent/deepseek-hpc-job-submit`
- `agent/deepseek-image-thumbnails`
- `feat/landing`
- `chore/post-step-b`
- `cursor/handover-roadmap-0452`
- `cursor/phase7-release-0452`

They cannot be audited from this repository: there is no commit or diff to
inspect. If they still exist in someone's local clone or were intended to be
un-deleted, they need to be restored first. Otherwise no action is needed.
Their entries below are marked **ask Adarsh** for that reason, not because the
work itself is unclear.

## Table

| Branch | Unique commits | Verdict | Reason | Superseded by |
| --- | ---: | --- | --- | --- |
| `feat/site-hero-multi` | 6 | **keep** | Unique CARLA hero-recording tooling: capture writer, CARLA recording job scripts, SLURM wrappers, scene-search/hero design docs, and `tests/test_carla_capture.py` (+1,615/-0 across 12 files). None of it is in `release/v0.1.1` under any path or name. It contains **no binary files, no `.mp4`, no `.png` frames, and no CARLA/dataset bytes**; only source, docs and tests. Keep for the hero-scene work; outputs must stay out of the repo. | — |
| `agent/antigravity-native-export` | — | **ask Adarsh** | No remote branch/ref exists; cannot audit. | — |
| `agent/antigravity-panel-states` | — | **ask Adarsh** | No remote branch/ref exists; cannot audit. | — |
| `agent/antigravity-theme-a11y` | — | **ask Adarsh** | No remote branch/ref exists; cannot audit. | — |
| `agent/deepseek-cluster-datasets` | — | **ask Adarsh** | No remote branch/ref exists; cannot audit. | — |
| `agent/deepseek-hpc-job-submit` | — | **ask Adarsh** | No remote branch/ref exists; cannot audit. | — |
| `agent/deepseek-image-thumbnails` | — | **ask Adarsh** | No remote branch/ref exists; cannot audit. | — |
| `feat/landing` | — | **ask Adarsh** | No remote branch/ref exists; cannot audit. | — |
| `chore/post-step-b` | — | **ask Adarsh** | No remote branch/ref exists; cannot audit. | — |
| `cursor/handover-roadmap-0452` | — | **ask Adarsh** | No remote branch/ref exists; cannot audit. | — |
| `cursor/phase7-release-0452` | — | **ask Adarsh** | No remote branch/ref exists; cannot audit. | — |

## `feat/site-hero-multi` details

Commit list (oldest first):

- `e43a34d` docs(site): record that no publishable scene has three agents
- `f54a49b` docs(site): record that CARLA 0.9.15 is not on the cluster
- `1efb941` feat(sim): record four lidar collaborators in CARLA
- `d7a4597` feat(sim): give the three cars different four-wheel blueprints
- `5444c99` fix(sim): put the repo on PYTHONPATH for the CARLA recording job
- `0dd1cec` fix(sim): move the drone spectator instead of the listening lidar

Files: `HANDOVER.md`, `docs/design/{carla-scratch,hero-carla-recording,hero-scene-search}.md`,
`scripts/sim/{capture_writer,record_carla_scene}.py`, `scripts/slurm/carla-{fetch,py310,record,smoke}.sh`,
`site/FACTS.md`, `tests/test_carla_capture.py`.

CARLA/dataset-byte check: `git diff --numstat` shows text additions only; no
binary entries. The tree adds no media files or binary assets. Keep the branch,
but never commit recorded CARLA output.

## Recommendation

- Keep `feat/site-hero-multi` (no PR opened; this audit is report-only).
- Restore or drop the nine missing refs outside this repository; they have no
  reviewable commits here. No branch was touched.

*This audit is report-only. No branch was deleted, rebased, pushed to, or
merged.*
