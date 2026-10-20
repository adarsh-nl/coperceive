# ADR 0014 — Desktop application architecture, execution targets, and packaging

Status: accepted

## Context

Coperceive provides a multi-agent cooperative perception 3D viewer (Three.js / React) and a local FastAPI backend (`coperceive serve`). Per ADR 0007, the web UI is strictly an HTTP client communicating over loopback with token authentication (`Authorization: Bearer <token>`), and the CLI remains fully functional independently.

Users require a standalone desktop application on macOS Apple Silicon (16 GB RAM primary workstation), with support for two primary operational modes:
1. **Local Mode**: Exploring local converted datasets using a bundled, self-contained Python runtime sidecar.
2. **HPC over SSH Mode**: Primary research workflow accessing large dataset repositories (`/datasets/eemcs/...`) and submitting GPU evaluations to Slurm on UTwente HPC (`hpc-head1.ewi.utwente.nl`).

Future platforms (Linux x86_64/arm64, Windows x64) must remain viable without redesigning core abstractions.

---

## Decisions

### 1. Shell: Tauri v2
We choose **Tauri v2** over Electron for the desktop wrapper.

- **Resource Footprint**: Tauri utilizes macOS `WKWebView`, consuming ~50–80 MB idle RAM compared to Electron’s ~300–450 MB Chromium baseline. On a 16 GB development machine, keeping memory available for 3D point cloud buffers, Arrow tables, and recurrent model tensors is critical.
- **Process & Lifecycle Control**: Tauri's Rust core provides native, deterministic process lifecycle hooks (`WindowEvent::Destroyed`, OS signal traps) to guarantee subprocess and SSH tunnel termination without leaving orphaned daemons.
- **Security & Decoupling**: Tauri isolates native shell capabilities (`tauri-plugin-dialog`, `tauri-plugin-shell`) behind type-safe IPC commands. The web UI remains an HTTP client targeting loopback `http://127.0.0.1:<port>`.
- **Blocker Evaluation**: No technical blockers exist in `WKWebView`. It fully supports ES modules, WebGL 2.0 (Three.js), Web Workers, and standard Fetch API with bearer headers.

### 2. ExecutionTarget Abstraction in Rust
All Process, SSH, and Slurm logic lives strictly in Rust (Tauri commands); TypeScript receives thin type bindings only.

The core abstraction decouples the frontend viewer and connection UI from the execution environment:

- **Connect**: Establishes the local sidecar or remote Slurm allocation and SSH tunnel, negotiating a secure ephemeral port and token. Returns active session parameters (`origin`, `token`, `serverVersion`, `dataRoot`).
- **Health**: Active heartbeat probe against `/api/health` with round-trip latency.
- **Disconnect / Teardown**: Cleanly terminates local sidecars, remote Slurm allocations, and SSH tunnels.
- **Event Streaming**: Emits typed events for target state (`connecting`, `connected`, `degraded`, `disconnected`, `error`) and raw diagnostic log lines.

### 3. Token Security: Never on Argv
Process arguments (`argv`) are visible to all users via `ps aux` on shared multi-user login and compute nodes. Therefore:
- The token is **never passed on argv**.
- `coperceive serve` accepts the authentication token via:
  1. The `COPERCEIVE_TOKEN` environment variable.
  2. The `--token-file <path>` CLI option, which **strictly enforces file mode 0600** (read/write by owner only; refusing to start if permissions are loose).
- The legacy `--token <value>` argument is preserved for backwards compatibility with a prominent deprecation warning.
- In both Local and Remote targets, the desktop app writes the token to a temporary 0600 token file and/or injects `COPERCEIVE_TOKEN` into the child process environment.

### 4. Execution Target A: Local
- **Runtime**: Bundled reproducible Python standalone environment (see Packaging).
- **Port Allocation**: Dynamically discovers an available loopback port by binding ephemeral port `0` in Rust before spawning.
- **Sidecar Process**: Spawns `python -m coperceive.app.serve --host 127.0.0.1 --port <port> --token-file <path> --root <selected_folder>`.
- **Token Delivery**: Injected directly into the webview's `sessionStorage.setItem("coperceive-token", token)` via Tauri's initialization script prior to page mounting. Never passed in URL parameters or query strings.
- **Teardown**: Tauri Rust backend tracks process PID, sending `SIGTERM` (escalating to `SIGKILL` after 3s) on window close.

### 5. Execution Target B: HPC over SSH
- **Zero Credential Storage**: No passwords, private keys, or passphrases are stored in code, config, OS keychain, or database. Subprocesses invoke the system `ssh` command, leveraging existing `~/.ssh/config`, `~/.ssh/known_hosts`, and active `ssh-agent`.
- **SSH Resilience**: Invoked with:
  `-o BatchMode=yes -o ControlMaster=auto -o ControlPath=~/.coperceive/ssh-%C -o ControlPersist=10m -o ConnectTimeout=8`
- **Error Diagnostics & BatchMode**:
  - `ConnectTimeout=8` and `BatchMode=yes` prevent UI hangs when off-campus or when eduVPN is disconnected.
  - Stderr pattern matching:
    - `Permission denied (publickey)` or passphrase prompt required under `BatchMode`: Instructs the user to load their key into ssh-agent (`ssh-add ~/.ssh/id_...`).
    - `No route to host`, `Connection timed out`, `Could not resolve hostname`: Alerts *"Cannot reach HPC host '<alias>'. Is eduVPN connected?"*
- **Cluster Policy Compliance & Partition Specification**:
  - *Login-Node Policy*: "Login-node commands stay under 1 minute. Heavy work goes through sbatch" (source: user handover rules & UTwente EEMCS HPC documentation at `https://hpc.wiki.utwente.nl/eemcs-hpc`). Persistent server daemons serving point cloud queries on `hpc-head1` are prohibited.
  - *Partitions*: `ps,main-cpu` verified via `sinfo -p ps,main-cpu` (yielding `hpc-node[11-12]` on `ps` and `spark-head[1-4],caserta,ctit087` on `main-cpu`).
- **Remote Bind Architecture & Evidence**:
  - *Cluster Evidence*: Probing direct SSH from the login node to compute nodes (`ssh -o BatchMode=yes hpc-node11`) returns `Permission denied (publickey,password,keyboard-interactive)`. Inspection of `/etc/pam.d/slurm` shows only `pam_localuser.so, pam_unix.so, pam_limits.so`; `pam_slurm_adopt` is not installed. Thus, direct user SSH to compute nodes (and SSH `ProxyJump`) is blocked on this cluster.
  - *Decision*: Launch `coperceive serve --host 0.0.0.0 --port <port> --token-file <path>` inside the Slurm compute job on its private internal IP (`10.20.240.x`), protected by mode 0600 token authentication. An SSH local port forward tunnels from macOS loopback through the login node (`hpc-head1`) to the allocated compute node:
    `ssh -L 127.0.0.1:<local_port>:<compute_node_ip>:<serve_port> <hpc_alias> -N`
- **Slurm Job Lifecycle Management**:
  - Submits an sbatch script with job name `coperceive-desktop-<uuid>` on `ps,main-cpu`, with a default walltime of 2 hours (configurable in connection profiles).
  - The job discovers its own free ephemeral port on the compute node, creates a mode 0600 credentials file containing `HOST=<ip>\nPORT=<port>\nTOKEN=<token>`, and signals readiness.
  - The UI parses `squeue` output and displays `PENDING` with queue reason (e.g. `Priority`, `Resources`) and elapsed time, alongside a `Cancel` button.
  - Displays a warning banner 10 minutes before the 2-hour walltime expires, with a one-click action to seamlessly relaunch/extend the session.
  - On network reconnect after a VPN drop, the app inspects active user jobs matching `coperceive-desktop-*` and automatically reattaches to the existing job without restarting.
  - On launch, the app queries `squeue` for leftover `coperceive-desktop-*` jobs (e.g. from an app crash or `kill -9`), presenting a prompt to re-attach or `scancel` orphaned jobs.
- **Remote Version Check**:
  - Queries `ssh <alias> "<coperceive_bin> --version"` upon handshake.
  - If version mismatches or command is missing, displays a warning with the exact copyable command (`pip install ...`), but **never runs remote installations without explicit user confirmation**.
- **Model Evaluation Integration**:
  - Slurm job status in the app is read-only via `squeue`.
  - `/api/jobs` remains the server's internal task queue. Remote model runs reuse `coperceive model run --slurm`.

### 6. Packaging & Distribution
- **Target**: macOS Apple Silicon (`aarch64-apple-darwin`), packaged as `.dmg`.
- **Python Bundling**: **python-build-standalone** (Astral/Szorc, Python 3.13 macOS arm64).
  - Environment is pinned with a strict lockfile and SHA-256 hashes.
  - Excludes `torch`, `torchvision`, `mmcv`, and viewer development extras to keep bundle compact.
  - Measured `.app` and `.dmg` bundle sizes are recorded and verified in CI.
- **Signing & Notarization**:
  - Initial releases: Unsigned `.dmg` produced via GitHub Actions on `macos-14` runners.
  - Infrastructure prepared for environment secrets (`APPLE_SIGNING_IDENTITY`, `APPLE_ID`, `APPLE_PASSWORD`).

---

## Consequences

- Tokens are strictly isolated from process argument listings.
- The desktop shell operates entirely through loopback forwarding to Slurm compute allocations, adhering strictly to HPC login-node rules.
- Network and VPN drops recover seamlessly without leaving unmanaged compute jobs.
- The web viewer continues to function as an independent HTTP client per ADR 0007.
