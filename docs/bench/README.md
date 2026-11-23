# Viewer benchmarks at real scale

Phase 0 of [ADR 0020](../adr/0020-viewer-at-real-scale.md). These scripts measure; they change no product code. Fixtures are generated into a folder outside the repository and are never committed.

## Prerequisites

- The app and dev extras: `pip install -e '.[app,dev]'` (`coperceive serve` needs `app`; `dev` installs Playwright).
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
- **Cameras:** C cameras in all, round-robin over the agents. Each has one PNG per sample, stamped with its frame number: 160×120 unless `--image-size WxH` says otherwise. Real camera resolutions are larger; pass one to measure image cost.

The same arguments and `--seed` write the same bytes. A target inside this repository is refused. The script prints the bytes written to stderr, and a JSON summary as the last line on stdout.

At the defaults the points alone are 4 × 160,000 × 16 B × 100 = 1,024 MB on disk (xyz and intensity; arithmetic), of which the server sends 12 B per point. The script's JSON summary reports the bytes it wrote and its run time on your machine.

## Browser benchmark: `scripts/bench/viewer_bench.py`

```bash
python scripts/bench/viewer_bench.py                       # generate the default fixture, then measure
python scripts/bench/viewer_bench.py --quick               # CI shape: 4 x 160k x 16 x 30 samples, short window
python scripts/bench/viewer_bench.py --fixture /tmp/coperceive-scale --measure-s 7
python scripts/bench/viewer_bench.py --json-out /tmp/viewer-bench.json
python scripts/bench/viewer_bench.py --quick --headed       # on a Mac; check browser.renderer
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
| `measured.phases_ms` | `load` (frame document, all samples); over applied samples: `points` (wait for the sweeps), `draw` (that wait, colour fill, CPU buffer copy), `render` (GPU upload, both composer passes): p50/p95 | `__COPERCEIVE_FRAME_STATS__` |
| `measured.io_ms`, `measured.io_all_ms` | per scene route (`frame`, `points`, `image`): `wait` (request to response), `read` (body), `parse` (frame JSON); in the window, and over the whole run | the bench's init-script wrapper around `fetch` and `Response.json`/`arrayBuffer`/`blob` |
| `measured.decode_ms` | `frame_json_parse`; `image_decode` is null (the camera strip decodes in a Web Worker, which the wrapper cannot reach); `points` is null (raw float32) | from `io_ms` |
| `measured.buffering` | "Buffering…" events and time after warm-up; stream-buffer stalls | `.clock .buffering`, `__COPERCEIVE_BUFFER_STATS__` |
| `measured.playhead` | samples the playhead advanced per second; whether it looped | `__COPERCEIVE_PLAYBACK_STATE__` |
| `measured.memory` | JS heap; server peak RSS (`server_rss_peak_bytes`, Linux) or RSS at the end (`server_rss_end_bytes`, elsewhere); GPU **estimate**: visible points × 24 B | `performance.memory`, `/proc/<pid>/status` VmHWM or `ps`, `__COPERCEIVE_SCENE_COUNTS__` |
| `measured.network` | bytes received in the window, by points, frame and image | DevTools `Network.loadingFinished` |
| `browser.renderer` | the WebGL renderer string | `WEBGL_debug_renderer_info`, `UNMASKED_RENDERER_WEBGL` |
| `machine` | CPU model, logical CPUs, RAM, OS, Python; whether it ran on GitHub Actions. No host or user name. | `scripts/bench/bench_machine.py` |

The wrapper is the bench's own: the app has no I/O hook, and the wrapper returns what the original calls return.

The app has no per-frame timing metric yet; ADR 0020 phase 2 adds one. Until then, the injected rAF loop is the frame-time source.

### Without WebGL

When the page cannot start WebGL, the viewer shows its `.webgl-missing` notice. The bench then prints `rendering skipped: no WebGL` and reports only the fetch side: the `load` phase, the I/O, buffering, network and heap. The `points` phase stays empty (count 0), because the viewer fetches sweeps only to draw them. It reports no frame time, draw, render, samples drawn or GPU numbers.

`web/src/viewer/capabilities.ts` describes what a scene can show (viewpoints, cameras). It does not report WebGL.

### Graphics

`--gl auto` (the default) uses SwiftShader on Linux, as the browser tests and CI do. Elsewhere it uses the browser's own GL. `--gl swiftshader` forces software rendering. `--executable-path` runs a different Chromium or Chrome binary.

Read the renderer string before comparing runs: SwiftShader numbers measure the CPU, not the GPU.

### Exit codes

- `0`: measured.
- `3`: this machine cannot run it (no Playwright, no Chromium build, or no web bundle). The message says which and how to fix it.

## Streaming benchmark: `scripts/bench/stream_bench.py`

`--agents`, `--points` and `--cameras` set the scene's shape. Its report carries the same `machine` record. The default is 2 × 56 000 points with no cameras, and `--smoke` keeps that shape for `tests/test_stream_bench.py`. With cameras, the player also fetches every camera's image at the strip's thumbnail width each frame.

```bash
python scripts/bench/stream_bench.py --agents 4 --points 160000 --cameras 16 --frames 20
```

## Tests

`tests/test_scale_fixture.py` checks the fixture: it reads back with the right counts, writes the same bytes for a seed and refuses a target in the repository. It checks the bench's summary on a synthetic log without a browser (applied-only draw times, I/O by kind) and its machine record. It also runs the bench `--quick` at a tiny size when Playwright, Chromium and a bundle are present. Otherwise that test is skipped, or fails when `COPERCEIVE_E2E_REQUIRED` is set, as with the browser tests.
