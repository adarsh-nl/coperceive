# ADR 0022 — Drop macOS Intel (`x86_64`) builds from v0.1.3

Status: accepted (Adarsh's decision, 2026-11-23). Numbered `0022` because `0019` (`0019-security-review.md`) and `0021` (`0021-app-model-runs-follow-the-cli-slurm-job.md`) already exist.

## Context

Through `v0.1.2`, CI and releases cross-compiled an `x86_64-apple-darwin` build on `desktop-macos`, checked it under Rosetta 2, and ran a follow-up smoke test on `macos-15-intel` (`desktop-macos-intel`) with WebGL checks skipped (`COPERCEIVE_INTEL_VM_NO_WEBGL=1`). Maintaining Intel macOS added dedicated build/smoke steps without bare-metal Intel hardware verification.

## Decision

From `v0.1.3` on, Coperceive ships macOS binaries for Apple Silicon (`aarch64-apple-darwin`) only. `v0.1.2` is the last Intel release and stays intact.

- Remove the `x86_64-apple-darwin` target, Intel build/Rosetta steps, and `desktop-macos-intel` job from `.github/workflows/ci.yml` (superseding the `desktop-macos-intel` CI note in [ADR 0018](0018-plan-parity.md)).
- Remove Intel build, signing, and upload steps from `.github/workflows/release.yml`, along with `scripts/desktop/requirements-darwin-x86_64.lock` and Intel-only branches in `scripts/desktop/bundle_python.py` and `scripts/desktop/smoke_test.sh`.
- Publish `latest.json` (`scripts/desktop/write_latest_json.py`) for `darwin-aarch64`, `windows-x86_64`, and `linux-x86_64` only, leaving `darwin-x86_64` absent.
- Point Intel Mac website visitors to the `v0.1.2` GitHub Release page rather than a `latest` release asset (`site/src/download/assets.ts`).
- On `v0.1.2` (`agent/agy-intel-last-release-msg`), map `tauri-plugin-updater` `Error::TargetNotFound` / `Error::TargetsNotFound` on `x86_64` macOS to a plain status message (`Coperceive v0.1.2 is the last version for Intel Macs.`) instead of surfacing a check error.

## Consequences

- `desktop-macos` tests and ships Apple Silicon (`aarch64-apple-darwin`) without waiting on cross-compilation or the `macos-15-intel` VM runner.
- Future updates never serve `darwin-x86_64` archives, and `v0.1.2` remains available on GitHub Releases for Intel Macs.
