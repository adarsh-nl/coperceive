# ADR 0020 — The viewer at real dataset scale

Status: proposed on 2026-11-16, revised on 2026-11-24 for `release/v0.1.3` (PR #174).
Code is cited from `release/v0.1.3` at `17b4ea0`. Sizes below are arithmetic from the
formats unless they say measured. No viewer behaviour changes until this is approved.

## Context

Task 6: play a real-size scene at 1× (4 agents × about 160k points, 16 cameras) with
p95 frame time under 100 ms, no buffering after warm-up, and bounded memory.

What the code does today:

- **Points** (`coperceive/app/server.py:730-760`):
  - raw float32 xyz in the world frame, 12 B per point, no header;
  - 1.9 MB per sweep, 7.7 MB per 4-agent sample, about 77 MB/s at 10 Hz.
  - The sensor-to-world transform runs per request, uncached (`viewdata.py:181-228`), on
    the event loop (`server.py:754`).
- **Frame documents** are JSON, rebuilt per request by linear scans (`viewdata.py:141-178`).
- **Requests:** a sample costs 1 frame document, 4 points requests and up to 16 images, with
  at most 6 in flight (`streamBuffer.ts`).
- **Server memory:** a local scene holds every sweep from open (`store.py:211, 391-416`).
  That is about 10 MB per sample, or 10 GB for 1000 samples.
- **Client, main thread, per sample:**
  - two new Float32Arrays of about 7.7 MB and a per-point colour loop (`Viewer.tsx:867-891`);
  - GPU buffers rebuilt when the count changes (`replaceColored`, `Viewer.tsx:581-601`);
  - two renders (`Viewer.tsx:3480, 3538`), each through two composers (`bloom.ts`);
  - several state updates of a 5,973-line component.
- **Budget:** the frame cache counts CPU bytes only. Its per-sample size is read once per
  configure (`Viewer.tsx:2173`, `cache.ts:442-448`). GPU memory, images and textures are
  not counted.
- **Tests:** CI playback runs at demo scale, at most 1024 points per sweep
  (`demo/generate.py:51-52`). Linux browser tests render with SwiftShader, which uses the CPU.

**Ownership:**
- `web/src/viewer/*` and the settings (`state.rs`, `main.rs`) are **agy's**.
- Server code and `scripts/bench/*` are unowned.

## Decision

Measure first, then change one thing at a time. Each phase reports benchmark numbers
before and after. A change that moves no budgeted number is dropped.

### Phase 0: fixtures and benchmark (bench files only)

- **`scripts/bench/scale_fixture.py`:**
  - writes an N agents × P points × C cameras × F samples scene with the store's writer;
  - writes only outside the repository: a repo target is refused, and nothing is
    committed;
  - defaults to 4 × 160,000 × 16 × 100 at 10 Hz, about 0.8 GB of points;
  - camera PNGs are 160×120 by default (`--image-size`).
- **`scripts/bench/viewer_bench.py`** plays the fixture in headless Chromium against
  `coperceive serve`. It reports (details in `docs/bench/README.md`):
  - frame time p50/p95/max;
  - samples drawn per second;
  - **load**, **points** wait, **draw** and **render**, from the app's
    `__COPERCEIVE_FRAME_STATS__`. Draw and render count applied samples only.
  - **I/O and decode** per request kind, from a bench-only wrapper around the page's
    `fetch`, body reads and `createImageBitmap`. Points need no decode (raw float32), and
    their colour fill and upload sit inside draw.
  - **memory:** JS heap, server peak RSS, and **estimated** GPU bytes;
  - **the machine:** CPU, cores, RAM, OS and WebGL renderer.

  Without WebGL it reports no render numbers and says so.
- **`scripts/bench/stream_bench.py`:** server-side streaming at a chosen shape, with the same
  machine record.
- **Baseline:** the first measured numbers go into the PR before any phase 1 change. None
  are recorded yet; see DECISION (Adarsh) 1.
- **DECISION (Adarsh) 1: where phase 0 is measured.**
  - (a) A report-only step in the CI `e2e` job runs `viewer_bench.py --quick` (4 × 160k × 16
    × 30) and prints the table to the job summary. That is GitHub's Linux runner and
    SwiftShader, so render numbers measure the CPU. It needs a `ci.yml` change. It also
    lists `tests/test_scale_fixture.py` there, so the bench test stops skipping in CI.
  - (b) A CPU job on the cluster that you submit, with the fixture in `/local`. This is
    also SwiftShader.
  - (c) A run on your Mac, the only option with hardware WebGL.
  - Recommended: (a) for every PR, and (c) once for the hardware number.
- **DECISION (Adarsh) 2: a measurement-only hook in `Viewer.tsx`.**
  - Splitting the colour fill and the GPU upload out of `draw` needs timing marks in agy's
    file.
  - Without it, `draw` stays one number.

### Phase 1: server (unowned files)

1. **Batched binary sample:** `GET /api/scenes/{id}/sample?time_us&sensors=…` returns one
   body, a fixed header (magic, version, counts, offsets) plus every sensor's float32 xyz.
   `/points` stays.
2. **The world transform** moves off the event loop, into an LRU keyed by scene, agent,
   sensor, time and level of detail, under a server byte budget.
3. **Frame documents** use per-time indexes built once per scene.
4. **Level of detail:** `?max_points=` decimates with a fixed stride, so it is deterministic
   and cacheable.
5. **Local scenes** read sweeps by byte range with a bounded cache, as remote scenes do.

- **DECISION (Adarsh) 3:** the `/sample` format and stride decimation, before phase 1 is
  built.

### Phase 2: client (agy's files; each step routed by the PM)

| Step | Files | What |
| --- | --- | --- |
| Frame-time metric | `Viewer.tsx` | per-rAF durations and p95 in `__COPERCEIVE_PLAYBACK_PERF__` |
| Worker decoding | new `pointWorker.ts`, `cache.ts` | parse the batched body and fill colours in a worker |
| GPU ring buffer | `Viewer.tsx` | fixed attributes per agent, `addUpdateRange`/`setDrawRange` |
| One render per sample | `Viewer.tsx`, `bloom.ts` | one requested render per sample |
| React churn | `Viewer.tsx` | per-sample values in refs; the UI updates a few times a second |
| Shared budget | `cache.ts`, `streamBuffer.ts`, `cameras.tsx`, `frustumImages.ts` | one byte account for cache, buffers, GPU and textures; admission from real bytes per sample |
| Level of detail | `Viewer.tsx`, `streamBuffer.ts` | ask for `max_points` from frame-time headroom; full detail when paused |

- **DECISION (Adarsh, with the PM) 4:** phase 2 on this branch step by step, or routed to agy
  with the phase 0 and 1 numbers.
- **DECISION (Adarsh) 5:** one shared byte budget that includes GPU memory and textures.

### Budgets in CI

- **SwiftShader runs report and assert nothing about speed.** A software-rendering gate
  would measure the runner, not the viewer.
- They may still assert what does not depend on the GPU:
  - no page errors;
  - the bench completes at real shape.
- **DECISION (Adarsh) 6: where "p95 under 100 ms" is asserted.**
  - (a) a hardware-backed macOS runner, if its WebGL is (unverified);
  - (b) a recorded run on your Mac per phase;
  - (c) transport and decode budgets only.
- **DECISION (Adarsh) 7: the target rate and image size.**
  - 10 Hz is assumed.
  - Fixture cameras are 160×120. Real camera resolutions are unverified here.

## Consequences

- One request per sample instead of 5–21, and no per-sample allocation of about 15 MB on the
  main thread.
- New viewer code in agy's files, gated on the PM's routing.
- Full-size fixtures are about 0.8 GB of points plus small images, generated at run time
  and never committed.
