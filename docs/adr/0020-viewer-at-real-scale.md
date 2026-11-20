# ADR 0020 — The viewer at real dataset scale

Status: proposed on 2026-11-16, for PM review before the build. Branch `agent/claude-viewer-scale`, PR into `release/v0.1.2`. Nothing outside `docs/` changes in this commit.

## Context

Task 6 asks the viewer to play a real-size scene at 1×: 4 agents of about 160k points each, 16 cameras. The CI budgets are:
- p95 frame time under 100 ms;
- no buffering after warm-up;
- bounded memory.

The work items named are:
- real-size fixtures, generated and not committed;
- a benchmark with per-phase timings;
- binary point transport;
- worker decoding;
- a GPU ring buffer;
- level of detail;
- a byte budget shared with the #150 stream cache.

Each change must earn its place in the benchmark. Where WebGL is unavailable, the benchmark skips rendering, shows a visible note and reports no numbers for it.

### What the code does today

Read on `release/v0.1.2` at `1d2d9c1`. The numbers are arithmetic from the formats, not measurements.

- **Transport.**
  - **Points:** `GET /api/scenes/{id}/points` (`coperceive/app/server.py:611-639`) returns raw little-endian float32 xyz in the world frame, 12 B per point, with no header and no caching headers (`coperceive/viewdata.py:181-228`). At scale that is 1.9 MB per sweep and 7.7 MB per 4-agent sample, about 77 MB/s at 10 Hz.
  - **Server work:** the server transforms each sweep from the sensor frame to the world frame on every request, in float64, uncached. For a local scene this runs on the event-loop thread (`server.py:634-637`).
  - **Frame document:** `/frame` is a JSON document rebuilt per request by linear scans of every annotation, cloud and image (`viewdata.py:141-178`).
  - **Requests per sample:** 1 frame document + 4 points + up to 16 images, all over HTTP/1.1 with at most 6 in flight (`streamBuffer.ts:531`).
- **Server memory.** A local scene is loaded with every sweep in memory (`store.py:211, 391-416`), about 10 MB per sample at this scale, so about 10 GB for a 1000-sample scene.
- **Client, on the main thread:**
  - per sample, two new Float32Arrays of about 7.7 MB each (positions and colours) and a per-point colour loop (`Viewer.tsx:862-886`);
  - GPU buffers rebuilt whenever the point count changes (`replaceColored`, `Viewer.tsx:576-596`), and a bounding sphere computed over every point;
  - two renders per sample, each through two composers (`Viewer.tsx:3500, 3558`; `bloom.ts:59, 66`);
  - several state updates of a 6,000-line component per sample (`Viewer.tsx:3052-3070, 3442, 3600`);
  - with frustum images on, up to 16 new textures per sample (`frustumImages.ts:50-56`).
- **Budget.** The #150 frame cache (`cache.ts`) counts CPU bytes only: frame JSON, re-serialised to measure it, and point buffers. The budget is 256/512/1024 MB.
  - **Admission:** it starts from a 2 MB-per-sample guess that is not refreshed (`cache.ts:445`, `Viewer.tsx:2184`), so it admits about 4× too many samples.
  - **Eviction:** an eviction on insert does not tell the scheduler (`cache.ts:187-205`).
  - **Not counted:** GPU memory, image caches and textures.
- **Measurement.**
  - `__COPERCEIVE_FRAME_STATS__` times the application of each sample, and `__COPERCEIVE_PLAYBACK_PERF__` gives average and max, with no p95 and no per-rAF frame time (`Viewer.tsx:947-1006`).
  - `scripts/bench/stream_bench.py` times server fetches for 2 agents × 56k points with no cameras.
  - Every CI playback gate runs at demo scale: at most 512 points per agent (`coperceive/demo/generate.py:37-44`), or 64 per sweep in the browser tests.
  - The Linux browser tests render with SwiftShader (software). `macos-15-intel` has no usable WebGL.

### Ownership

Most of the client work is in `web/src/viewer/*`, which the brief assigns to **agy**. The settings live in `src-tauri/src/state.rs` and `main.rs`, also agy's. The server side (`coperceive/app/*`, `coperceive/viewdata.py`, `coperceive/remote_view/*`) and the bench scripts are unowned. The demo generator is DeepSeek's and is not changed here. This ADR names every viewer file it would touch, so the PM can approve each phase for this branch or route it.

## Decision

Measure first, then change one thing at a time. Every phase reports its numbers from the benchmark before and after, and a change that does not move a budgeted number is dropped.

### Phase 0: fixtures and benchmark (unowned files; no product change)

- **`scripts/bench/scale_fixture.py`:** writes a schema-1 scene with N agents × P points × C cameras × F samples, using the store's own writer. It is generated into a temporary or user-chosen folder and never committed. The defaults are 4 × 160,000 × 16 × 100 at 10 Hz. The PNG images are small and stamped with their frame index, so that image fetches cost what the format costs.
- **`scripts/bench/viewer_bench.py`:** starts `coperceive serve` on that fixture and drives the viewer in a browser (Playwright, as the browser tests already do). It plays for a fixed time after a warm-up and reports:
  - **per phase:** fetch, decode, upload, render and React, from `FRAME_STATS` plus a per-rAF frame-time log added in phase 2;
  - p50/p95/max frame time;
  - samples drawn per second against 10 Hz;
  - buffering events after warm-up;
  - JS heap and an estimate of GPU bytes from attribute and texture sizes.

  When the page reports no WebGL (`capabilities.ts`), it prints "rendering skipped: no WebGL" and reports transport and decode only, never render numbers.
- **`scripts/bench/stream_bench.py`:** gains `--agents`, `--points` and `--cameras`, keeping its `--smoke` shape for the existing test.
- **Baseline:** the first numbers go into the PR before any change.

### Phase 1: server (unowned files)

1. **Batched binary sample.** `GET /api/scenes/{id}/sample?time_us&sensors=…` returns one body: a small fixed header (magic, version, count per sensor, offsets) followed by every requested sensor's float32 xyz. That replaces 4 requests per sample with 1. The existing `/points` stays for compatibility.
2. **The world-frame transform** moves off the event loop and into an LRU keyed by scene version, agent, sensor, time and level of detail. Its bytes count against a server-side budget.
3. **Frame documents** use per-time indexes built once per scene, instead of linear scans per request.
4. **Level of detail on the server:** `?max_points=` decimates with a fixed stride, so it is deterministic and cache-friendly, before sending. The client asks for what its budget and frame time allow.
5. **Remote camera thumbnails** are cached; the local thumbnail cache grows beyond 256 entries.
6. **Local scenes** stop loading every sweep at open. They read sweeps by byte range from `points.bin` with a bounded LRU, as the remote path already does.

### Phase 2: client (agy's files; each step needs the PM's go-ahead)

| Step | Files | What |
| --- | --- | --- |
| Frame-time metric | `Viewer.tsx` (`recordFrameStat`, `summarizePlaybackPerf`) | Per-rAF frame durations and p50/p95 in `__COPERCEIVE_PLAYBACK_PERF__` |
| Worker decoding | new `web/src/viewer/pointWorker.ts`, `cache.ts` (`loadPoints`) | Decode the batched body and fill colours in a worker; transfer the arrays |
| GPU ring buffer | `Viewer.tsx` (`replaceColored`, stage points) | Fixed-capacity attributes per agent, updated with `addUpdateRange` and `setDrawRange`; no reallocation or bounding sphere per sample |
| One render per sample | `Viewer.tsx:3449-3558`, `bloom.ts` | Collapse to one requested render; frustum geometry reused, materials disposed |
| React churn | `Viewer.tsx` playback loop | Per-sample values moved to refs; the UI is updated at most a few times a second |
| Shared budget | `cache.ts`, `streamBuffer.ts`, `cameras.tsx`, `frustumImages.ts` | One byte account for the frame cache, decoded buffers, GPU ring, images and textures; an eviction callback to the scheduler; admission from the real bytes per sample (`pointSensors[].count × 12`) |
| Level of detail | `Viewer.tsx`, `streamBuffer.ts` | Request `max_points` from the frame-time headroom and the budget; full detail when paused |

### CI budgets

- **New job step (Linux, SwiftShader):** runs `viewer_bench.py` on a reduced-but-real-shape fixture: 4 agents × 160k points, 16 cameras, 50 samples. Assertions:
  - ≥ 0.9 × 10 Hz samples drawn;
  - no buffering after a 2 s warm-up;
  - JS heap and accounted bytes under the budget.
- **p95 frame time under 100 ms** is asserted where WebGL is hardware-backed. On SwiftShader it is reported, not asserted, since software rendering would make the gate measure the runner, not the viewer. Where to assert it is open question 2.
- **No WebGL** (`macos-15-intel`): the benchmark reports transport and decode only and says so.

## Consequences

- One request per sample instead of 5–21, and no per-sample allocation of about 15 MB on the main thread. Playback memory has one budget that includes the GPU.
- New code in the viewer, which is agy's. Phase 2 cannot start without the PM's routing.
- The fixtures are a few GB when generated at full size. They are never committed, and the CI variant is smaller.

## Open questions for the PM

1. **Phase 2 is in agy's files.** Approve it for this branch, step by step, or route it to agy with phase 0 and 1 numbers as the brief.
2. **Where should "p95 under 100 ms" be asserted?** Software rendering on Linux CI cannot meet it honestly at 640k points. The options:
   - (a) the macOS job's GPU, if its WKWebView is hardware-backed;
   - (b) a manual benchmark on Adarsh's Mac, recorded in the PR;
   - (c) assert it on the transport and decode phases only in CI.
3. **The target sample rate** is assumed to be 10 Hz. Real datasets may differ (V2V4Real: 10 Hz; OPV2V: 10 Hz).
