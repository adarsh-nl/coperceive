# Viewer benchmarks at real scale

Phase 0 of [ADR 0020](../adr/0020-viewer-at-real-scale.md). These scripts measure; they change no product code. Fixtures are generated into a folder outside the repository and are never committed.

## Prerequisites

- The dev environment: `pip install -e '.[dev]'` (this installs Playwright).
- A Chromium build for Playwright: `python -m playwright install chromium`. On Linux CI, `python -m playwright install --with-deps chromium`.
- The web bundle: `bash scripts/build_web_static.sh` writes `coperceive/app/static`, which is not committed. To use another build, set `COPERCEIVE_WEB_DIST` to a vite build directory, as the browser tests do.

## Fixture: `scripts/bench/scale_fixture.py`

```bash
python scripts/bench/scale_fixture.py /tmp/coperceive-scale
# 4 agents x 160 000 points x 16 cameras x 100 samples at 10 Hz, seed 0
python scripts/bench/scale_fixture.py /tmp/small --agents 2 --points 2000 --cameras 2 --samples 3
```

The script writes one schema-1 scene through the store's own writer into `OUT/<scene id>/`, with `COMPLETE.json`, so `coperceive serve --root OUT` opens it.

- **Agents:** vehicles driving a loop with real rotating poses, so the server's sensor-to-world transform does real work.
- **Points:** P points per sweep in the sensor frame, on a ground plane, static buildings and the moving boxes.
- **Boxes:** six annotated boxes per agent per sample, with track ids shared between agents.
- **Cameras:** C cameras in all, round-robin over the agents. Each has one 160×120 PNG per sample, stamped with its frame number.

The same arguments and `--seed` write the same bytes. A target inside this repository is refused. The script prints the bytes written to stderr, and a JSON summary as the last line on stdout.

At the defaults the scene is about 1 GB: 981 MiB on disk, 14 s to write and 1.1 GB peak memory on an Apple M3 with 16 GB.

## Browser benchmark: `scripts/bench/viewer_bench.py`

```bash
python scripts/bench/viewer_bench.py                       # generate the default fixture, then measure
python scripts/bench/viewer_bench.py --quick               # CI shape: 4 x 160k x 16 x 30 samples, short window
python scripts/bench/viewer_bench.py --fixture /tmp/coperceive-scale --measure-s 7
python scripts/bench/viewer_bench.py --json-out /tmp/viewer-bench.json
```

The bench runs these steps:

1. It starts `coperceive serve` with a fresh bearer token (`COPERCEIVE_TOKEN`), a free loopback port, `--root` on the fixture and `--static` on the bundle.
2. It opens the viewer in headless Chromium and waits for the first drawn sample.
3. It presses Play at 1×, plays through the warm-up (`--warmup-s`, default 2 s) and measures a fixed window (`--measure-s`, default 7 s).

Without `--fixture`, it generates the scene into a temporary folder and removes it afterwards; `--keep-fixture` keeps it.

### What it reports

The JSON report goes to stdout (last line) and a table to stderr.

| Key | What | Source |
| --- | --- | --- |
| `measured.frame_time_ms` | p50/p95/max between animation frames | a `requestAnimationFrame` loop injected by the bench |
| `measured.samples` | samples drawn per second against the scene rate, and dropped samples | applied entries in `__COPERCEIVE_FRAME_STATS__` |
| `measured.phases_ms` | `load` (frame document), `points` (sweep fetch and decode), `draw`, `render`: p50/p95 | `__COPERCEIVE_FRAME_STATS__` |
| `measured.buffering` | "Buffering…" events and time after warm-up; stream-buffer stalls | `.clock .buffering`, `__COPERCEIVE_BUFFER_STATS__` |
| `measured.playhead` | samples the playhead advanced per second; whether it looped | `__COPERCEIVE_PLAYBACK_STATE__` |
| `measured.memory` | JS heap; GPU estimate: visible points × 24 B | `performance.memory`, `__COPERCEIVE_SCENE_COUNTS__` |
| `measured.network` | bytes received in the window, by points, frame and image | DevTools `Network.loadingFinished` |
| `browser.renderer` | the WebGL renderer string | `WEBGL_debug_renderer_info`, `UNMASKED_RENDERER_WEBGL` |

The app has no per-frame timing metric yet; ADR 0020 phase 2 adds one. Until then, the injected rAF loop is the frame-time source.

### Without WebGL

When the page cannot start WebGL, the viewer shows its `.webgl-missing` notice. The bench then prints `rendering skipped: no WebGL` and reports only the fetch side: load and points phases, buffering, network, heap. It reports no frame time, draw, render, samples drawn or GPU numbers.

`web/src/viewer/capabilities.ts` describes what a scene can show (viewpoints, cameras). It does not report WebGL.

### Graphics

`--gl auto` (the default) uses SwiftShader on Linux, as the browser tests and CI do. Elsewhere it uses the browser's own GL. `--gl swiftshader` forces software rendering. `--executable-path` runs a different Chromium or Chrome binary.

Read the renderer string before comparing runs: SwiftShader numbers measure the CPU, not the GPU.

### Exit codes

- `0`: measured.
- `3`: this machine cannot run it (no Playwright, no Chromium build, or no web bundle). The message says which and how to fix it.

## Streaming benchmark: `scripts/bench/stream_bench.py`

`--agents`, `--points` and `--cameras` set the scene's shape. The default is 2 × 56 000 points with no cameras, and `--smoke` keeps that shape for `tests/test_stream_bench.py`. With cameras, the player also fetches every camera's image at the strip's thumbnail width each frame.

```bash
python scripts/bench/stream_bench.py --agents 4 --points 160000 --cameras 16 --frames 20
```

## Tests

`tests/test_scale_fixture.py` checks the fixture: it reads back with the right counts, writes the same bytes for a seed and refuses a target in the repository. It also runs the bench `--quick` at a tiny size when Playwright, Chromium and a bundle are present. Otherwise that test is skipped, or fails when `COPERCEIVE_E2E_REQUIRED` is set, as with the browser tests.
