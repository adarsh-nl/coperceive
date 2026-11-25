// SPDX-License-Identifier: Apache-2.0
//! "Open predictions" over the stream path (the one `dataset_open` uses).
//!
//! A run's predictions are opened in the app's local viewer server, the same
//! `coperceive serve` that streams the run's dataset over SSH (no Slurm job, no
//! remote viewer session). This module holds the parts that do not need the app:
//!
//! - [`run_dataset`]: which prepared scenes (and raw root) a saved plan ran on, so
//!   the viewer streams the right dataset;
//! - [`run_store`]: where the run's prediction store is on the cluster;
//! - [`copy_run`]: a bounded, byte-exact copy of the files the viewer reads from
//!   `<store>/<run>` into the local predictions root the server was started with.
//!
//! A built-in model writes its run straight into the plan's output directory
//! (`model run --out <output_dir> --run-id <plan>`), which is already a prediction
//! store. A custom pipeline writes `predictions.jsonl`; it is imported on the
//! cluster against the open dataset into `<output_dir>/viewer-predictions`
//! ([`crate::job_report::import_predictions_into`]) and copied from there.

use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use crate::hpc::{is_under, normalise_path, ServerProfile};
use crate::run_plan::RunPlan;
use crate::ssh::{shell_quote, SshTarget};

/// The files of a run the viewer reads (`coperceive/evaluation/predictions.py`
/// `RUN_FILE`/`BOXES_FILE`, `coperceive/evaluation/evaluate.py` `METRICS_FILE`/
/// `MATCHES_FILE`). Nothing else under the run directory is copied.
pub const RUN_FILE: &str = "run.json";
pub const SCENE_FILES: [&str; 3] = ["boxes.parquet", "metrics.json", "matches.parquet"];

/// The largest copy Open predictions makes (512 MiB); a larger run is refused, not
/// truncated.
pub const COPY_LIMIT_BYTES: usize = 512 * 1024 * 1024;
pub const COPY_TIMEOUT: Duration = Duration::from_secs(300);

/// The marker the copy script starts with, so fixtures and logs can tell it apart.
pub const COPY_MARKER: &str = "# coperceive-predictions-copy";

/// Prints, for `<store>` (`$1`) and `<run>` (`$2`), each file the viewer reads as a
/// header line `F <bytes> <run>/<path>` followed by exactly that many bytes, then
/// `END`. Exit 3: no run directory; exit 4: no `run.json` in it. The paths travel as
/// positional arguments, never inside the script text.
const COPY_SCRIPT: &str = "# coperceive-predictions-copy
cd -- \"$1\" 2>/dev/null || { echo \"no prediction store at $1\" >&2; exit 3; }
[ -d \"$2\" ] || { echo \"no run directory $2 in $1\" >&2; exit 3; }
[ -f \"$2/run.json\" ] || { echo \"no run.json in $1/$2\" >&2; exit 4; }
for f in \"$2/run.json\" \"$2\"/*/boxes.parquet \"$2\"/*/metrics.json \"$2\"/*/matches.parquet; do
  [ -f \"$f\" ] || continue
  n=$(wc -c < \"$f\" | tr -d ' ')
  printf 'F %s %s\\n' \"$n\" \"$f\"
  cat -- \"$f\"
done
printf 'END\\n'
";

/// The dataset a run's predictions belong to: the prepared scenes the run read and
/// the raw root its sensor data come from (empty when the plan has none).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RunDataset {
    pub scenes: String,
    pub raw_root: String,
}

/// Where a run's predictions are and what they are made of.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RunSource {
    /// A built-in model: the run is `<output_dir>/<plan>`, on this dataset.
    Builtin { dataset: RunDataset, store: String },
    /// A custom pipeline: `<output_dir>/predictions.jsonl`, which has to be imported
    /// against an open dataset first; the plan records no dataset for it.
    Custom { output_dir: String },
}

fn allowed_roots(profile: &ServerProfile) -> Vec<String> {
    let mut roots: Vec<String> = profile.data_roots.clone();
    roots.extend(profile.code_roots.iter().cloned());
    roots.push(profile.results_root.clone());
    roots
        .iter()
        .filter(|root| !root.trim().is_empty())
        .filter_map(|root| normalise_path(root, "profile root").ok())
        .collect()
}

/// A cluster path the stream may read: absolute, one line, no `..`, and inside the
/// profile's data, code or results folders.
pub fn check_stream_root(profile: &ServerProfile, path: &str, what: &str) -> Result<String, String> {
    if path.chars().any(char::is_control) {
        return Err(format!("The run's {what} path contains a control character."));
    }
    if !path.starts_with('/') {
        return Err(format!("The run's {what} path {path:?} is not an absolute path."));
    }
    if path.split('/').any(|part| part == "..") {
        return Err(format!("The run's {what} path {path:?} may not contain '..'."));
    }
    let normalised = normalise_path(path, what)?;
    if !allowed_roots(profile).iter().any(|root| is_under(&normalised, root)) {
        return Err(format!(
            "The run's {what} path {path:?} is outside this server's data, code and results \
             folders; Open predictions will not stream it."
        ));
    }
    Ok(normalised)
}

/// The run's output directory, validated as the job script uses it and inside
/// results_root.
fn checked_output_dir(profile: &ServerProfile, output_dir: &str) -> Result<String, String> {
    profile.validate_output_dir(output_dir)?;
    let output = normalise_path(output_dir, "run output directory")?;
    let root = normalise_path(&profile.results_root, "results_root")?;
    if !is_under(&output, &root) {
        return Err(format!(
            "The run output directory {output_dir:?} escapes results_root {root:?}."
        ));
    }
    Ok(output.trim_end_matches('/').to_string())
}

/// What the saved plan says about its predictions (see [`RunSource`]).
pub fn run_source(plan: &RunPlan, profile: &ServerProfile) -> Result<RunSource, String> {
    crate::run_plan::validate_plan_id(&plan.id)?;
    let output_dir = checked_output_dir(profile, &plan.output_dir)?;
    if plan.pipeline.source != "builtin" {
        return Ok(RunSource::Custom { output_dir });
    }
    Ok(RunSource::Builtin { dataset: run_dataset(plan, profile)?, store: output_dir })
}

/// The prepared scenes a built-in plan's run line reads (`model run … --scenes <dir>`)
/// and its raw root, both checked with [`check_stream_root`].
pub fn run_dataset(plan: &RunPlan, profile: &ServerProfile) -> Result<RunDataset, String> {
    let start = plan
        .argv
        .windows(2)
        .position(|pair| pair[0] == "model" && pair[1] == "run")
        .ok_or_else(|| "This saved built-in plan does not run coperceive model run.".to_string())?;
    let scenes = plan.argv[start + 2..]
        .windows(2)
        .find(|pair| pair[0] == "--scenes")
        .map(|pair| pair[1].clone())
        .ok_or_else(|| "This saved built-in plan's run line names no --scenes.".to_string())?;
    let scenes = check_stream_root(profile, &scenes, "scenes")?;
    let raw_root = if plan.raw_root.path.trim().is_empty() {
        String::new()
    } else {
        check_stream_root(profile, &plan.raw_root.path, "raw root")?
    };
    Ok(RunDataset { scenes, raw_root })
}

/// The remote command for the copy of `<store>/<run>`.
pub fn copy_command(store: &str, run_id: &str) -> String {
    format!("sh -c {} sh {} {}", shell_quote(COPY_SCRIPT), shell_quote(store), shell_quote(run_id))
}

/// A remote command's exit code and its output, read as bytes.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BulkOutput {
    pub code: Option<i32>,
    pub stdout: Vec<u8>,
    pub stderr: String,
}

/// Runs one remote command and returns its stdout as bytes, reading it while the
/// command runs (no pipe stall), up to `limit` bytes.
pub trait BulkRunner {
    fn run_bytes(&self, remote_cmd: &str, timeout: Duration, limit: usize) -> Result<BulkOutput, String>;
}

/// [`BulkRunner`] over the profile's ssh, with the same options (BatchMode, the shared
/// ControlMaster socket, the connect timeout) as every other cluster command.
pub struct SshBulkRunner {
    pub ssh_bin: String,
    pub target: SshTarget,
}

impl BulkRunner for SshBulkRunner {
    fn run_bytes(&self, remote_cmd: &str, timeout: Duration, limit: usize) -> Result<BulkOutput, String> {
        let args = crate::ssh::build_ssh_command_for(&self.target, remote_cmd);
        run_bytes_local(&self.ssh_bin, &args, timeout, limit)
    }
}

/// Runs `bin args…`, draining stdout (up to `limit`) and stderr on their own threads.
/// Over the limit or past the timeout, the child is killed and an error returned.
pub fn run_bytes_local(
    bin: &str,
    args: &[String],
    timeout: Duration,
    limit: usize,
) -> Result<BulkOutput, String> {
    crate::ssh::ensure_control_dir();
    let mut child = crate::platform::create_command(bin)
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|error| format!("Could not run ssh: {error}"))?;
    let over = Arc::new(AtomicBool::new(false));
    let mut stdout = child.stdout.take().ok_or("ssh gave no stdout")?;
    let mut stderr = child.stderr.take().ok_or("ssh gave no stderr")?;
    let over_reader = Arc::clone(&over);
    let out_thread = std::thread::spawn(move || {
        let mut buffer = Vec::new();
        let mut chunk = [0u8; 64 * 1024];
        loop {
            match stdout.read(&mut chunk) {
                Ok(0) | Err(_) => break,
                Ok(n) => {
                    if buffer.len() + n > limit {
                        over_reader.store(true, Ordering::SeqCst);
                        break;
                    }
                    buffer.extend_from_slice(&chunk[..n]);
                }
            }
        }
        buffer
    });
    let err_thread = std::thread::spawn(move || {
        let mut text = Vec::new();
        let _ = (&mut stderr).take(64 * 1024).read_to_end(&mut text);
        String::from_utf8_lossy(&text).to_string()
    });
    let deadline = Instant::now() + timeout;
    let status = loop {
        if over.load(Ordering::SeqCst) {
            let _ = child.kill();
            let _ = child.wait();
            let _ = out_thread.join();
            return Err(format!(
                "The run's predictions are larger than {} MiB; Open predictions copies at most that.",
                limit / (1024 * 1024)
            ));
        }
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) if Instant::now() >= deadline => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(format!(
                    "The cluster did not finish sending the predictions within {} seconds.",
                    timeout.as_secs()
                ));
            }
            Ok(None) => std::thread::sleep(Duration::from_millis(25)),
            Err(error) => return Err(format!("Could not wait for ssh: {error}")),
        }
    };
    let stdout = out_thread.join().map_err(|_| "The predictions reader failed.".to_string())?;
    if over.load(Ordering::SeqCst) {
        return Err(format!(
            "The run's predictions are larger than {} MiB; Open predictions copies at most that.",
            limit / (1024 * 1024)
        ));
    }
    let stderr = err_thread.join().unwrap_or_default();
    Ok(BulkOutput { code: status.code(), stdout, stderr })
}

/// A scene directory under a run, as `coperceive/evaluation/predictions.py`
/// `check_scene_id` allows it (not empty, no `/` or `\\`, not starting with `.`),
/// and on one line, as the copy's header lines need.
fn valid_scene_dir(name: &str) -> bool {
    !name.is_empty()
        && !name.starts_with('.')
        && !name.contains(['/', '\\'])
        && !name.chars().any(char::is_control)
}

/// One copied file: its path under the run directory (`run.json` or
/// `<scene>/<file>`) and its bytes.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CopiedFile {
    pub path: String,
    pub bytes: Vec<u8>,
}

/// Reads the copy script's output for `run_id`. Every path must be the run's
/// `run.json` or `<scene>/<one of SCENE_FILES>`; the stream must end with `END`.
pub fn parse_copy(data: &[u8], run_id: &str) -> Result<Vec<CopiedFile>, String> {
    let broken = |why: &str| format!("The predictions copy was cut short or changed while it ran ({why}); try again.");
    let mut files = Vec::new();
    let mut at = 0usize;
    loop {
        let end = data[at..]
            .iter()
            .position(|&b| b == b'\n')
            .map(|offset| at + offset)
            .ok_or_else(|| broken("no END line"))?;
        let line = std::str::from_utf8(&data[at..end]).map_err(|_| broken("a header is not text"))?;
        at = end + 1;
        if line == "END" {
            if at != data.len() {
                return Err(broken("bytes after END"));
            }
            break;
        }
        let rest = line.strip_prefix("F ").ok_or_else(|| broken("a header line is malformed"))?;
        let (size, path) = rest.split_once(' ').ok_or_else(|| broken("a header line is malformed"))?;
        let size: usize = size.parse().map_err(|_| broken("a file size is not a number"))?;
        let relative = path
            .strip_prefix(run_id)
            .and_then(|tail| tail.strip_prefix('/'))
            .ok_or_else(|| format!("The cluster sent a file outside run {run_id}: {path:?}."))?;
        let parts: Vec<&str> = relative.split('/').collect();
        let allowed = match parts.as_slice() {
            [name] => *name == RUN_FILE,
            [scene, name] => valid_scene_dir(scene) && SCENE_FILES.contains(name),
            _ => false,
        };
        if !allowed {
            return Err(format!("The cluster sent a file Open predictions does not read: {path:?}."));
        }
        if data.len() < at + size {
            return Err(broken("a file is shorter than its header says"));
        }
        files.push(CopiedFile { path: relative.to_string(), bytes: data[at..at + size].to_vec() });
        at += size;
    }
    if !files.iter().any(|file| file.path == RUN_FILE) {
        return Err(broken("no run.json"));
    }
    Ok(files)
}

/// Scenes of the copied run that have predictions (`<scene>/boxes.parquet`), sorted.
pub fn copied_scenes(files: &[CopiedFile]) -> Vec<String> {
    let mut scenes: Vec<String> = files
        .iter()
        .filter_map(|file| file.path.strip_suffix("/boxes.parquet"))
        .map(str::to_string)
        .collect();
    scenes.sort();
    scenes.dedup();
    scenes
}

/// Replaces `<local_root>/<run_id>` with the copied files, through a staging folder
/// renamed into place, so the viewer never sees half a run.
pub fn write_run(local_root: &Path, run_id: &str, files: &[CopiedFile]) -> Result<PathBuf, String> {
    crate::run_plan::validate_plan_id(run_id)?;
    std::fs::create_dir_all(local_root)
        .map_err(|error| format!("Could not create the local predictions folder: {error}"))?;
    let staging = local_root.join(format!(".{run_id}.partial"));
    let destination = local_root.join(run_id);
    let _ = std::fs::remove_dir_all(&staging);
    for file in files {
        let path = staging.join(&file.path);
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)
                .map_err(|error| format!("Could not write the predictions: {error}"))?;
        }
        std::fs::write(&path, &file.bytes).map_err(|error| format!("Could not write the predictions: {error}"))?;
    }
    if destination.exists() {
        std::fs::remove_dir_all(&destination)
            .map_err(|error| format!("Could not replace the earlier copy of run {run_id}: {error}"))?;
    }
    std::fs::rename(&staging, &destination)
        .map_err(|error| format!("Could not move the predictions into place: {error}"))?;
    Ok(destination)
}

/// Copies `<store>/<run_id>` from the cluster into `<local_root>/<run_id>` and returns
/// the run's scenes. A missing run directory or `run.json` is a plain error naming
/// the path.
pub fn copy_run(
    runner: &dyn BulkRunner,
    store: &str,
    run_id: &str,
    local_root: &Path,
) -> Result<Vec<String>, String> {
    crate::run_plan::validate_plan_id(run_id)?;
    let output = runner.run_bytes(&copy_command(store, run_id), COPY_TIMEOUT, COPY_LIMIT_BYTES)?;
    let at = format!("{}/{run_id}", store.trim_end_matches('/'));
    match output.code {
        Some(0) => {}
        Some(3) => {
            return Err(format!(
                "This run has no predictions on the cluster: {at} does not exist. The job may \
                 not have started writing yet, or its output was removed."
            ))
        }
        Some(4) => {
            return Err(format!(
                "This run's predictions folder {at} has no run.json yet, so it is not a finished \
                 prediction run."
            ))
        }
        _ => {
            let detail = output.stderr.trim();
            return Err(format!(
                "Could not copy the predictions from {at}: {}",
                if detail.is_empty() { "ssh failed" } else { detail }
            ));
        }
    }
    let files = parse_copy(&output.stdout, run_id)?;
    let scenes = copied_scenes(&files);
    write_run(local_root, run_id, &files)?;
    Ok(scenes)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::run_plan::PlanArtifact;
    use std::cell::RefCell;

    fn profile() -> ServerProfile {
        let entry: crate::server_profiles::ServerEntry =
            serde_json::from_str(include_str!("../tests/fixtures/hpc/cluster-b.json")).unwrap();
        let mut profile = entry.profile;
        profile.data_roots = vec!["/data/scenes".to_string(), "/data/raw".to_string()];
        profile.code_roots = vec!["/data/code".to_string()];
        profile.results_root = "/data/results".to_string();
        profile
    }

    fn plan(source: &str, argv: &[&str], raw: &str) -> RunPlan {
        let mut plan: RunPlan = serde_json::from_value(serde_json::json!({
            "schema": "coperceive-run-plan/1",
            "id": "plan-1790000000-aabbccddeeff",
            "created_unix": 1790000000u64,
            "profile_id": "cluster-b",
            "profile_name": "cluster b",
            "pipeline": {"id": "griffin", "name": "Griffin", "source": source},
            "argv": argv,
            "environment": {"kind": "none"},
            "output_dir": "/data/results/runs/plan-1790000000-aabbccddeeff",
            "scratch_dir": "/work/alice",
            "checks": [],
            "contract_check_script": "",
            "nothing_submitted": true
        }))
        .unwrap();
        plan.raw_root = PlanArtifact { path: raw.to_string(), ..PlanArtifact::default() };
        plan
    }

    const RUN: &str = "plan-1790000000-aabbccddeeff";

    fn builtin_argv(scenes: &str) -> Vec<&str> {
        vec!["/opt/venv/bin/python", "-m", "coperceive", "model", "run", "--manifest", "/m.yaml", "--scenes", scenes, "--out", "/data/results/runs/x", "--run-id", RUN]
    }

    #[test]
    fn a_builtin_run_streams_the_scenes_and_raw_root_its_run_line_read() {
        let p = profile();
        let source = run_source(&plan("builtin", &builtin_argv("/data/scenes/griffin"), "/data/raw/griffin"), &p).unwrap();
        assert_eq!(
            source,
            RunSource::Builtin {
                dataset: RunDataset { scenes: "/data/scenes/griffin".into(), raw_root: "/data/raw/griffin".into() },
                store: format!("/data/results/runs/{RUN}"),
            }
        );
        // A prepared cache under results_root is a scenes root too; no raw root is allowed.
        let cached = run_dataset(&plan("builtin", &builtin_argv("/data/results/coperceive-cache/opv2v/0123456789ab/test"), ""), &p).unwrap();
        assert_eq!(cached.raw_root, "");
        // A custom pipeline has no dataset in its plan.
        assert_eq!(
            run_source(&plan("custom", &["python", "infer.py"], ""), &p).unwrap(),
            RunSource::Custom { output_dir: format!("/data/results/runs/{RUN}") }
        );
    }

    #[test]
    fn a_plan_that_would_stream_outside_the_profile_is_refused() {
        let p = profile();
        for scenes in ["/etc", "/data/scenes/../../etc", "relative/scenes", "/data/scenes/a\nb"] {
            assert!(run_dataset(&plan("builtin", &builtin_argv(scenes), ""), &p).is_err(), "{scenes:?}");
        }
        assert!(run_dataset(&plan("builtin", &builtin_argv("/data/scenes/g"), "/home/other/raw"), &p).is_err());
        let error = run_dataset(&plan("builtin", &["python", "-m", "coperceive", "model", "run", "--manifest", "/m.yaml"], ""), &p).unwrap_err();
        assert!(error.contains("no --scenes"), "{error}");
    }

    struct FakeBulk {
        reply: BulkOutput,
        seen: RefCell<Vec<String>>,
    }

    impl BulkRunner for FakeBulk {
        fn run_bytes(&self, remote_cmd: &str, _timeout: Duration, _limit: usize) -> Result<BulkOutput, String> {
            self.seen.borrow_mut().push(remote_cmd.to_string());
            Ok(self.reply.clone())
        }
    }

    fn stream(files: &[(&str, &[u8])]) -> Vec<u8> {
        let mut out = Vec::new();
        for (path, bytes) in files {
            out.extend(format!("F {} {path}\n", bytes.len()).into_bytes());
            out.extend_from_slice(bytes);
        }
        out.extend(b"END\n");
        out
    }

    fn scratch(label: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("coperceive-run-predictions-{label}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        dir
    }

    #[test]
    fn a_run_is_copied_byte_for_byte_and_replaces_an_earlier_copy() {
        // Binary bytes, including newlines and "END\n" inside a file, survive the framing.
        let parquet: &[u8] = b"PAR1\n\x00\xffEND\nF 3 x\n\x01";
        let body = stream(&[
            (&format!("{RUN}/run.json"), b"{\"run_id\": 1}"),
            (&format!("{RUN}/Town03-000/boxes.parquet"), parquet),
            (&format!("{RUN}/Town03-000/metrics.json"), b"{}"),
            // Any name check_scene_id allows, spaces and brackets included.
            (&format!("{RUN}/Town 05 (rain)/boxes.parquet"), b"PAR1"),
        ]);
        let runner = FakeBulk { reply: BulkOutput { code: Some(0), stdout: body, stderr: String::new() }, seen: RefCell::default() };
        let root = scratch("copy");
        std::fs::create_dir_all(root.join(RUN).join("stale-scene")).unwrap();
        let scenes = copy_run(&runner, "/data/results/runs/x", RUN, &root).unwrap();
        assert_eq!(scenes, vec!["Town 05 (rain)".to_string(), "Town03-000".to_string()]);
        assert_eq!(std::fs::read(root.join(RUN).join("Town03-000/boxes.parquet")).unwrap(), parquet);
        assert_eq!(std::fs::read(root.join(RUN).join("run.json")).unwrap(), b"{\"run_id\": 1}");
        assert!(!root.join(RUN).join("stale-scene").exists(), "the earlier copy is replaced, not merged");
        assert!(!root.join(format!(".{RUN}.partial")).exists());
        // The paths are arguments of a fixed script, quoted, never spliced into it.
        let command = &runner.seen.borrow()[0];
        assert!(command.starts_with("sh -c '# coperceive-predictions-copy"), "{command}");
        assert!(command.ends_with(&format!(" sh /data/results/runs/x {RUN}")), "{command}");
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn a_missing_run_directory_is_a_plain_error_and_writes_nothing() {
        let root = scratch("missing");
        for (code, wanted) in [(3, "has no predictions on the cluster: /data/results/runs/x/"), (4, "has no run.json yet")] {
            let runner = FakeBulk {
                reply: BulkOutput { code: Some(code), stdout: Vec::new(), stderr: "no run directory".into() },
                seen: RefCell::default(),
            };
            let error = copy_run(&runner, "/data/results/runs/x", RUN, &root).unwrap_err();
            assert!(error.contains(wanted), "{error}");
        }
        assert!(!root.exists());
        let runner = FakeBulk {
            reply: BulkOutput { code: Some(255), stdout: Vec::new(), stderr: "Permission denied (publickey).".into() },
            seen: RefCell::default(),
        };
        assert!(copy_run(&runner, "/s", RUN, &root).unwrap_err().contains("Permission denied"));
    }

    #[test]
    fn a_stream_with_foreign_paths_or_a_cut_end_is_refused() {
        let ok = format!("{RUN}/run.json");
        for (body, wanted) in [
            (stream(&[(&ok, b"{}"), ("other-run/run.json", b"{}")]), "outside run"),
            (stream(&[(&ok, b"{}"), (&format!("{RUN}/../x/boxes.parquet"), b"1")]), "does not read"),
            (stream(&[(&ok, b"{}"), (&format!("{RUN}/s/secrets.txt"), b"1")]), "does not read"),
            (stream(&[(&ok, b"{}"), (&format!("{RUN}/a/b/boxes.parquet"), b"1")]), "does not read"),
            (stream(&[(&ok, b"{}"), (&format!("{RUN}/.hidden/boxes.parquet"), b"1")]), "does not read"),
            (stream(&[(&ok, b"{}"), (&format!("{RUN}/a\\b/boxes.parquet"), b"1")]), "does not read"),
            (stream(&[(&format!("{RUN}/s/boxes.parquet"), b"1")]), "no run.json"),
            (format!("F 10 {ok}\n{{}}").into_bytes(), "cut short"),
            (stream(&[(&ok, b"{}")])[..10].to_vec(), "cut short"),
            ([stream(&[(&ok, b"{}")]), b"junk".to_vec()].concat(), "bytes after END"),
        ] {
            let error = parse_copy(&body, RUN).unwrap_err();
            assert!(error.contains(wanted), "{wanted}: {error}");
        }
    }

    #[test]
    #[cfg(unix)]
    fn the_copy_script_sends_exactly_the_viewer_files_and_fails_plainly_without_a_run() {
        // The real script, run by sh against a local folder standing in for the store.
        let store = scratch("script");
        let run = store.join(RUN);
        std::fs::create_dir_all(run.join("Town03-000")).unwrap();
        std::fs::create_dir_all(run.join("work")).unwrap();
        std::fs::write(run.join("run.json"), b"{}").unwrap();
        std::fs::write(run.join("Town03-000/boxes.parquet"), b"PAR1\n\x00\xff").unwrap();
        std::fs::write(run.join("Town03-000/notes.txt"), b"not copied").unwrap();
        std::fs::write(run.join("work/boxes.tmp"), b"not copied").unwrap();
        let script = || {
            vec!["-c".to_string(), COPY_SCRIPT.to_string(), "sh".to_string(), store.to_string_lossy().into_owned(), RUN.to_string()]
        };
        let output = run_bytes_local("sh", &script(), Duration::from_secs(20), COPY_LIMIT_BYTES).unwrap();
        assert_eq!(output.code, Some(0), "{}", output.stderr);
        let files = parse_copy(&output.stdout, RUN).unwrap();
        let paths: Vec<&str> = files.iter().map(|file| file.path.as_str()).collect();
        assert_eq!(paths, vec!["run.json", "Town03-000/boxes.parquet"]);
        assert_eq!(files[1].bytes, b"PAR1\n\x00\xff");
        // Over the limit: refused, not truncated.
        let small = run_bytes_local("sh", &script(), Duration::from_secs(20), 8).unwrap_err();
        assert!(small.contains("larger than"), "{small}");
        std::fs::remove_file(run.join("run.json")).unwrap();
        assert_eq!(run_bytes_local("sh", &script(), Duration::from_secs(20), COPY_LIMIT_BYTES).unwrap().code, Some(4));
        std::fs::remove_dir_all(&run).unwrap();
        assert_eq!(run_bytes_local("sh", &script(), Duration::from_secs(20), COPY_LIMIT_BYTES).unwrap().code, Some(3));
        let _ = std::fs::remove_dir_all(&store);
    }

    #[test]
    #[cfg(unix)]
    fn a_large_answer_does_not_stall_the_reader() {
        // 4 MiB through the pipe, far past its buffer, within a short timeout.
        let args = vec!["-c".to_string(), "head -c 4194304 /dev/zero".to_string()];
        let output = run_bytes_local("sh", &args, Duration::from_secs(20), COPY_LIMIT_BYTES).unwrap();
        assert_eq!(output.stdout.len(), 4 * 1024 * 1024);
    }
}
