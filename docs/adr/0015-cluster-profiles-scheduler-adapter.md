# ADR 0015 — Cluster profiles, the scheduler adapter, and pipeline registration

Status: proposed (Adarsh reviews)

Amends ADR 0011 decision 5: the Slurm partition and resources of a model run come
from the user's server profile, not from constants such as `ps`/`ps,main-gpu`
(`coperceive/models/slurm.py:297` at the time of writing). ADR 0011's scheduling
policy still holds — scripts are emitted and shown before submission, heavy work
goes through the scheduler, the login node runs nothing heavy.

## Context

coperceive is becoming a product: people connect their own clusters. Today the
remote path carries one lab's values — partition `ps`, node hostnames, UT paths,
GPU partition names — in defaults and fixtures
(`coperceive/remote/manager.py:17`, `scripts/desktop/remote_serve.sbatch`,
`coperceive/models/slurm.py:297`). Those are exactly the values a different user
cannot use. The desktop shell also has a remote "Connect" flow that is not wired
to the web UI and joins argv into a remote shell string.

The product rules are fixed:

- nothing site-specific in product code or defaults; hosts, users, roots,
  scratch, partition, account, QoS, GRES/GPU type, time and memory live in a
  per-server profile the user chooses;
- partitions and GPU types are discovered from the scheduler **read-only** and
  offered as choices; the app never invents a default partition;
- Slurm first, behind an interface, so PBS, LSF or a scheduler-less ("none")
  server can be added later;
- heavy work always goes through the scheduler; login-node work stays light and
  time-bounded;
- environments are detected and overridable (modules, conda/venv, container);
  compute nodes may have no internet, so job scripts install and download
  nothing unless the profile says otherwise;
- login uses the user's own `ssh-agent` and `~/.ssh/config`; the app never asks
  for or stores a password or private key;
- any model can be registered as a pipeline: a script plus a typed argument
  schema, with a small open output contract so results stream into the viewer;
- every job script is shown in full and confirmed by the user before submission;
- multi-user safety: per-user scratch, no shared-path assumptions, every app job
  named with the `coperceive-` prefix, and the app manages only jobs it recorded.

## Decision

### 1. Server profiles (no secrets, validated, user-owned)

A **server profile** is the single place where a cluster's values live. It is
stored locally by the desktop app (`servers.json` in the app config directory,
mode 0600, parent 0700) and contains no credentials. The JSON shape is
`docs/spec/cluster-profile.schema.json`; the Python model is
`coperceive/hpc/profile.py` and the Rust serde mirror is
`src-tauri/src/hpc.rs` (same field names, `deny_unknown_fields`).

| Field | Meaning |
|---|---|
| `name` | label the user recognises |
| `ssh_alias` | a `~/.ssh/config` Host the user already uses (ProxyJump, 2FA, agent) |
| `host`, `user` | alternative when there is no ssh alias; never a password |
| `data_roots` | directories the app may read on the server; paths must be absolute or `~` |
| `scratch_template` | per-user scratch for job work, e.g. `/scratch/$USER/$SLURM_JOB_ID` |
| `scheduler` | `slurm` or `none` |
| `defaults` | optional `partition`, `account`, `qos`, `gres`, `time_limit`, `memory`, `cpus_per_task` — user-confirmed, never shipped |
| `environment` | `kind` (`none`/`modules`/`conda`/`venv`/`container`), `modules`, `interpreter`, `container_image`, `coperceive_command`, `wheelhouse`, `compute_has_internet` |
| `notes` | free text for the user |

Rules enforced by validation:

- exactly one of `ssh_alias` and `host`; at least one data root; a scratch
  template; environment pieces required by its kind (modules need modules,
  conda/venv need the python path, container needs the image);
- credential-looking keys are refused by the strict JSON reader, so a
  hand-edited profile cannot smuggle a token in;
- secrets for a job are read from the job's environment on the server, never
  from the profile and never from argv.

The UI detects what it can (scheduler kind, partitions, GPU types, module system,
conda/venv, apptainer/singularity, coperceive path and version, scratch
writability, quota) and **offers** those values; everything is overridable.
Detection is read-only and time-bounded (`sinfo`, `scontrol show partition`,
`module avail`, `command -v`, small directory probes). A missing value is
explained in plain words with the next action.

### 2. Scheduler adapters

`coperceive/hpc/scheduler.py` defines the interface every scheduler implements:

```
discover() -> Discovery                     # read-only facts, no side effects
render_job_script(profile, request) -> str  # complete script for the user to confirm
submit(script) -> job_id
status(job_id) -> JobState
cancel(job_id) -> None
logs(job_id) -> str
list_own_jobs() -> [(job_id, JobState)]     # only jobs this app recorded
```

`Discovery` returns partitions with name, nodes, state, max time, GRES and GPU
types, plus the detected GPU types. Two Slurm output shapes are parsed and merged
(pipe `sinfo -o` and the default columns; `scontrol show partition -o` and the
multi-line form) because Slurm versions differ; fixtures live in
`tests/fixtures/hpc/`.

`SlurmAdapter` renders a `#!/bin/bash` script that contains **only profile/user
values**: `#SBATCH --job-name` (must start with `coperceive-`), partition, optional
account/QoS/GRES/time/memory/cpus, log paths, `set -euo pipefail`, the profile's
modules or container, and the command as a POSIX-quoted argv list. It refuses to
render without a partition: *"Choose a partition for this job. coperceive does not
assume one; pick one from the partitions detected on the server."*

`NullAdapter` is the `none` scheduler: discovery is empty and every heavy
operation refuses with a plain explanation; the app may still read data over SSH,
and a heavy command needs the user's explicit consent in a terminal session.

PBS and LSF are **extension points only**: implement the same interface (their
discovery commands differ, so discovery parsing is adapter-local). No PBS/LSF code
is added by this ADR.

The interface is intentionally free of cluster values. The caller builds command
argv lists; a remote command line is only used for the small login-node probes,
and there every element goes through `quote_remote_argv` (POSIX single-quote
escaping) or the argument list is passed to `ssh` directly. Job scripts never
contain credentials.

### 3. Pipeline registration

Any model becomes a pipeline: a script plus a typed argument schema. JSON shape:
`docs/spec/pipeline.schema.json`; Python model `coperceive/hpc/pipeline.py`; Rust
mirror in `src-tauri/src/hpc.rs`.

```
PipelineSpec  { name, script, interpreter[], environment, arguments[], output_contract }
ArgumentSpec  { name, kind: str|int|float|bool|path|enum, help, required, default, choices[], multiple }
```

The script path must stay inside the profile's data roots (relative, no `..`);
values are validated per kind and passed **only as argv elements** — never
formatted into a shell string. Argument names that look like credentials
(`token`, `password`, `secret`, `api_key`, `private_key`) are refused. A run is
`<interpreter...> <script> --<arg> <value> ... --output-dir <dir>`; `bool` true is
a flag, false is omitted; `multiple` repeats the flag. Secrets come from the
environment on the server.

A pipeline writes the open output contract in `docs/spec/pipeline-output.md`
(per-frame JSON lines using the box columns of `docs/spec/predictions.md`, a
progress line, a final `metrics.json`). `coperceive predictions import` converts
that output into a stored run, so the viewer/streaming path is not tied to any
module codebase. There is no coupling to OpenCOOD, Griffin or any one repository.

### 4. Where heavy work runs

- Slurm: `sbatch` with the rendered script, after the user has seen and confirmed
  it. Every job is named `coperceive-<kind>-<id>`; `list_own_jobs` filters on that
  prefix and intersects with jobs this app recorded.
- none: the app refuses to submit; the user can run the confirmed command in a
  terminal on the server, and the app reads results.
- Login node: only discovery and small probes, each with a timeout, so any
  cluster's login-node policy is respected.

## Alternatives considered

- **Keep site defaults and override them.** Rejected: a product cannot ship one
  lab's partition/node/path defaults; a wrong partition is an immediate failure
  for every other user.
- **One config file per data set instead of a server profile.** Rejected: host,
  scheduler and environment belong to the server, not to the data.
- **Write a general scheduler DSL now.** Rejected: Slurm plus an interface, with
  PBS/LSF documented, is the smallest thing that proves the abstraction.
- **Let the app store tokens to simplify reattach.** Rejected by the security
  rules: tokens stay in memory (or the OS keyring) and are fetched read-only over
  SSH when needed.

## Consequences

- Slice 1 (server profiles + detection + connection test) builds on these
  interfaces; until it lands, the pre-existing remote path still contains the old
  site defaults listed in "Known legacy defaults to remove".
- Tests must use more than one fake cluster profile (different partitions and GRES
  names, module+venv setups, a ProxyJump alias) and a `none` scheduler profile.
- `tests/test_hpc_portability.py` fails if a site value (partition `ps`, an
  `hpc-node` hostname, a university host or path) appears in the new HPC layer or
  in a rendered job script, or if a default partition/host constant is added.
- The desktop UI keeps `ServerProfile` free of UI-only data (detected values,
  window state); those live in a wrapper beside it.

## Known legacy defaults to remove in slice 1

These are the site values the product currently contains; slice 1 replaces them
with profile values and the portability test's scope then widens to them:

- `coperceive/remote/manager.py:17` `DEFAULT_PARTITION = "ps"` (and the CLI help
  in `coperceive/remote/commands.py:37`).
- `scripts/desktop/remote_serve.sbatch` `#SBATCH --partition=ps`, resources, and
  the paths it writes under `$HOME`.
- `coperceive/models/slurm.py:297` and its docstring `:8` (partition from the
  profile; this decision amends ADR 0011 decision 5).
- Rust test fixtures in `src-tauri/src/{remote,slurm,ssh}.rs` (test-only data,
  not defaults; replace with generic names when the code changes).
