# Desktop settings

The **Settings** modal (`web/src/desktop/SettingsView.tsx`) stores app-wide preferences in `state.json` and per-server settings for cluster workflows.

> **Version note:** This page describes Settings as implemented on `release/v0.1.2`. Per-setting `Default:` hints, **Reset to defaults** buttons, and global **Theme**, **Camera view**, and **Point size** controls (`PR #249`, following the `docs/design/settings-inventory.md` inventory from `PR #208`) landed on `release/v0.1.3`. On `release/v0.1.2`, point size, colour-by, and theme toggle remain on the viewer toolbar (`localStorage` `coperceive-view` and `theme_override`).

## General

- **Reopen last session on launch** (`reopen_last_session`, default: **on**): `"When unchecked, Coperceive starts at the Connect screen."`
- **Local library folder**: displays the current `library_root` with **Show in Finder** / **Show in Explorer** / **Open folder** (`getRevealLabel()`). Changing or moving the folder lives in the **Library** screen.

## Viewer

- **Open the viewer when the first scene is ready** (`open_first_ready_scene`, default: **on**): `"After preparing a cluster dataset, show its first ready scene; the rest appear in the viewer's scene menu as they finish."`
- **Continue to the next scene automatically** (`auto_next_scene`, default: **off**): `"When a scene ends, play the next prepared one. Off: a note offers it instead."`
- **Style** (`viewer_style`, default: **Classic**; `ViewerStyleSettings.tsx`):
  - **Classic**: `"Today's layout. Light and dark follow the app theme."`
  - **Studio**: `"Quiet pro tool. Dark chrome, tighter type, a closer orbit."`
  - **Instrument**: `"Heads-up display. Monospace readouts over the scene."`
  - **Cinema**: `"The scene fills the window. Glass panels float over it."` Enables **Cinema camera drift** (`"The camera turns slowly while the scene is playing."` or `"Off while the system asks for reduced motion."`).
- **Camera strip** (`camera_strip`, default: **All cameras (scroll)**):
  - **All cameras (scroll)**: `"Every agent's cameras in one filmstrip under the 3D view; scroll it sideways with the trackpad or Shift and the wheel."`
  - **One agent at a time**: `"One agent's cameras, larger, with Switch agent to move to the next."`
- **Streaming buffer** (`buffer_target_seconds`, default: **3 s ahead**; choices: `1 s ahead`, `3 s ahead`, `5 s ahead`):
  - `3 s ahead`: `"Default. Playback pauses and says Buffering… rather than stuttering when the link falls behind."`
  - `1 s ahead`: `"Less memory for buffering; playback rebuffers sooner on a slow link."`
  - `5 s ahead`: `"Extra headroom for a slow link; fills for longer before playback starts."`
- **Client memory limit** (`client_memory_limit_bytes`, default: **512 MB**; choices: `256 MB`, `512 MB`, `1024 MB`):
  - `512 MB`: `"Default. Cached frame documents and point clouds are evicted oldest-first to stay under this."`
  - `256 MB`: `"Less memory; the buffer target may not be reached on dense scenes."`
  - `1024 MB`: `"More memory for the buffer on dense scenes."`
- **Show streaming stats** (`show_stream_stats`, default: **off**): `"A small line under the status line: buffer ahead, MB/s and frame-load p50/p95."`

## Datasets

- **Group scenes** (`dataset_grouping`, default: **None**):
  - **None**: `"One list, sorted by scene name."`
  - **By state**: `"Failed, stale, preparing, queued, ready, then not prepared."`
  - **By recording day**: `"From scene names that hold a date (YYYY_MM_DD_HH_MM_SS): the day the data was recorded."`

## Cluster datasets

Shown in the main window (`ClusterDatasetSettings.tsx`):

- **Camera images when preparing**: select **Every server** or a specific server profile (**Same as for every server**, **Read originals from the dataset** *(default)*, or **Save previews in my results folder**). Note: `"Applies to the next prepare; prepared scenes keep theirs. You can still change it in each prepare preview."` When a server is selected, also shows existing preview disk usage and **Remove previews…**.
- **Viewing cache** (default: **2 GB**; choices: `1 GB`, `2 GB`, `5 GB`, `10 GB`, `20 GB`): local cache of sweeps and images streamed over SSH, with **Clear cache**.

## Model runs

Shown in the main window when at least one server profile exists (`ModelRunSettings.tsx`, added in `PR #244` alongside `PR #213`):

- **Plugin interpreter for built-in models**: choose a **Server** and enter an absolute interpreter path on that server (placeholder `/path/to/envs/<env>/bin/python`).
  - Default hint: `"A full path on this server, passed to coperceive model run as --plugin-python. Empty: the model's manifest environment. Each New evaluation can change it."`
  - Validation (shared with `run_plan.rs` and `coperceive model run --plugin-python`): must start with `/` and contain no line break or control characters; otherwise shows `"--plugin-python: give a full path (got <path>)"` or `"--plugin-python: the interpreter path contains a line break or another control character."` and disables **Save**.
  - **Save** shows `"Saved. New evaluations on this server start with it."` **Clear** resets to empty and shows `"Cleared: the model's manifest decides."`

## Maps

- **CARLA road maps** (`CarlaMapsSettings.tsx`): install or remove SHA-256-verified CARLA 0.9.15 road maps cached under the library's `maps` folder.

## Updates

- **Automatically download updates** (`auto_download_updates`, default: **on**): `"Prompt when new signed releases are available."` See [Desktop updates](updates.md).

## More detail

- [Evaluate a model on the HPC from the app](../guides/evaluate-on-hpc.md)
- [Desktop HPC and viewer](hpc-and-viewer.md)
- [Desktop updates](updates.md)
