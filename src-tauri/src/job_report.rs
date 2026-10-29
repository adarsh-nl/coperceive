// SPDX-License-Identifier: Apache-2.0
//! Remote HPC 5/6b: the run's streamed predictions, final metrics report and
//! "Open predictions".
//!
//! The pipeline contract (`docs/spec/pipeline-output.md`) streams
//! `predictions.jsonl` into the run's output dir while the job runs and writes
//! `metrics.json` once at the end. This module reads both with bounded, quoted
//! SSH commands (byte-offset tail, capped reads), imports the finished file into
//! a stored predictions run, and never runs anything heavy or unbounded on the
//! login node.

use std::time::Duration;

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::hpc::{is_under, normalise_path, ServerProfile};
use crate::slurm_adapter::RemoteRunner;
use crate::ssh::shell_quote;

const READ_TIMEOUT: Duration = Duration::from_secs(15);
const IMPORT_TIMEOUT: Duration = Duration::from_secs(120);
const MAX_PREDICTION_BYTES: u64 = 1024 * 1024;
const MAX_METRICS_BYTES: usize = 256 * 1024;
pub const MIN_PREDICTION_BYTES: u64 = 64 * 1024;

/// Latest progress counters from the pipeline's `{"type":"progress"}` lines.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct PredictionProgress {
    #[serde(default)]
    pub scenes_done: Option<u64>,
    #[serde(default)]
    pub scenes_total: Option<u64>,
    #[serde(default)]
    pub frames_done: Option<u64>,
    #[serde(default)]
    pub frames_total: Option<u64>,
}

/// One bounded read of the streamed predictions file.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PredictionTail {
    /// Byte offset to send on the next poll (last complete line).
    pub offset: u64,
    /// Current file size in bytes.
    pub size: u64,
    /// New complete prediction lines in this chunk.
    pub boxes: u64,
    /// Per `source` counts in this chunk (ordered by source name).
    pub sources: Vec<PredictionSourceCount>,
    /// Latest progress line in this chunk, if the pipeline wrote one.
    #[serde(default)]
    pub progress: Option<PredictionProgress>,
    /// True when no newline-terminated data was available.
    pub idle: bool,
    /// The last prediction line in this chunk, for a small "latest box" readout.
    #[serde(default)]
    pub latest: Option<LatestPrediction>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct LatestPrediction {
    pub agent_id: String,
    pub source: String,
    pub time_us: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PredictionSourceCount {
    pub source: String,
    pub boxes: u64,
}

/// The pipeline's own report (never the evaluation score, see
/// `docs/spec/pipeline-output.md`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct MetricsReport {
    pub run: Value,
    pub outputs: Value,
    pub metrics: Value,
    #[serde(default)]
    pub notes: Option<String>,
    /// Pretty JSON, for the "show the raw report" affordance.
    pub raw: String,
}

/// What `Open predictions` imported and the viewer link it would open.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ImportedPredictions {
    pub run_id: String,
    pub scenes: Vec<String>,
    pub predictions_root: String,
    pub url: String,
}

fn validate_output_dir(profile: &ServerProfile, output_dir: &str) -> Result<String, String> {
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

fn output_path(profile: &ServerProfile, output_dir: &str, name: &str) -> Result<String, String> {
    let output = validate_output_dir(profile, output_dir)?;
    let path = format!("{output}/{name}");
    let normalised = normalise_path(&path, "run output file")?;
    let root = normalise_path(&profile.results_root, "results_root")?;
    if !is_under(&normalised, &root) {
        return Err(format!("The run output {path:?} escapes results_root."));
    }
    Ok(normalised)
}

fn parse_progress(value: &Value) -> Option<PredictionProgress> {
    if value.get("type").and_then(Value::as_str) != Some("progress") {
        return None;
    }
    let counter = |key: &str| value.get(key).and_then(Value::as_u64);
    Some(PredictionProgress {
        scenes_done: counter("scenes_done"),
        scenes_total: counter("scenes_total"),
        frames_done: counter("frames_done"),
        frames_total: counter("frames_total"),
    })
}

/// Tails `predictions.jsonl` from `offset` with a hard byte cap.
///
/// Only complete, newline-terminated JSON lines are consumed; a partially
/// written last line is left for the next poll. A shrunk file (rotation or a
/// reused output dir) is read from the beginning again.
pub fn tail_predictions(
    runner: &dyn RemoteRunner,
    profile: &ServerProfile,
    output_dir: &str,
    offset: u64,
    max_bytes: u64,
) -> Result<PredictionTail, String> {
    let cap = max_bytes.clamp(MIN_PREDICTION_BYTES, MAX_PREDICTION_BYTES);
    let path = output_path(profile, output_dir, "predictions.jsonl")?;
    let command = format!(
        "sh -c 'size=$(stat -c %s -- \"$1\" 2>/dev/null || wc -c < \"$1\"); \
         printf \"%s\\n\" \"$size\"; \
         tail -c +$(( $2 + 1 )) -- \"$1\" | head -c $3' sh {path} {offset} {cap}",
        path = shell_quote(&path)
    );
    let result = runner
        .run(&command, READ_TIMEOUT, None)
        .map_err(|error| format!("Could not tail predictions.jsonl: {error}"))?;
    if !result.success {
        return Err(
            "No predictions.jsonl yet. The pipeline has not written its first prediction."
                .to_string(),
        );
    }
    let (size_line, data) = result
        .stdout
        .split_once('\n')
        .ok_or_else(|| "The predictions tailer returned an unexpected reply.".to_string())?;
    let size: u64 = size_line
        .trim()
        .parse()
        .map_err(|_| "The predictions tailer returned no file size.".to_string())?;
    let start = if size < offset { 0 } else { offset };
    let payload = data.as_bytes();
    let complete_len = payload
        .iter()
        .rposition(|byte| *byte == b'\n')
        .map(|index| index + 1)
        .unwrap_or(0);
    let complete = &data[..complete_len];
    let mut boxes = 0_u64;
    let mut sources: std::collections::BTreeMap<String, u64> = std::collections::BTreeMap::new();
    let mut progress: Option<PredictionProgress> = None;
    let mut latest: Option<LatestPrediction> = None;
    for (index, line) in complete.lines().enumerate() {
        if line.trim().is_empty() {
            continue;
        }
        let value: Value = serde_json::from_str(line).map_err(|error| {
            format!(
                "predictions.jsonl line {} is not JSON: {error}. The pipeline must only write \
                 the documented objects.",
                index + 1
            )
        })?;
        if let Some(next) = parse_progress(&value) {
            progress = Some(next);
            continue;
        }
        let source = value
            .get("source")
            .and_then(Value::as_str)
            .ok_or_else(|| {
                format!(
                    "predictions.jsonl line {} has no \"source\" string; the pipeline output \
                     does not meet the contract.",
                    index + 1
                )
            })?;
        boxes += 1;
        *sources.entry(source.to_string()).or_insert(0) += 1;
        if let (Some(agent_id), Some(time_us)) = (
            value.get("agent_id").and_then(Value::as_str),
            value.get("time_us").and_then(Value::as_u64),
        ) {
            latest = Some(LatestPrediction {
                agent_id: agent_id.to_string(),
                source: source.to_string(),
                time_us,
            });
        }
    }
    Ok(PredictionTail {
        offset: start + complete_len as u64,
        size,
        boxes,
        sources: sources
            .into_iter()
            .map(|(source, boxes)| PredictionSourceCount { source, boxes })
            .collect(),
        progress,
        idle: complete_len == 0,
        latest,
    })
}

/// Reads and validates the pipeline's `metrics.json`, or `None` when it does not
/// exist yet (the run is still going, or it failed before the end).
pub fn read_metrics(
    runner: &dyn RemoteRunner,
    profile: &ServerProfile,
    output_dir: &str,
) -> Result<Option<MetricsReport>, String> {
    let path = output_path(profile, output_dir, "metrics.json")?;
    let command = format!(
        "sh -c 'head -c {MAX_METRICS_BYTES} -- \"$1\"' sh {path}",
        path = shell_quote(&path)
    );
    let result = runner
        .run(&command, READ_TIMEOUT, None)
        .map_err(|error| format!("Could not read metrics.json: {error}"))?;
    if !result.success || result.stdout.trim().is_empty() {
        return Ok(None);
    }
    let value: Value = serde_json::from_str(&result.stdout)
        .map_err(|error| format!("metrics.json is not valid JSON: {error}"))?;
    if value.get("schema").and_then(Value::as_str) != Some("coperceive-pipeline-metrics") {
        return Err(
            "metrics.json does not declare the coperceive-pipeline-metrics schema; the pipeline \
             output does not meet the contract."
                .to_string(),
        );
    }
    if value.get("version").and_then(Value::as_u64) != Some(1) {
        return Err("metrics.json does not declare version 1.".to_string());
    }
    let raw = serde_json::to_string_pretty(&value).unwrap_or_default();
    Ok(Some(MetricsReport {
        run: value.get("run").cloned().unwrap_or(Value::Null),
        outputs: value.get("outputs").cloned().unwrap_or(Value::Null),
        metrics: value.get("metrics").cloned().unwrap_or(Value::Null),
        notes: value
            .get("notes")
            .and_then(Value::as_str)
            .map(str::to_string),
        raw,
    }))
}

fn run_scenes(metrics: &MetricsReport) -> Vec<String> {
    metrics
        .run
        .get("scenes")
        .and_then(Value::as_array)
        .map(|scenes| {
            scenes
                .iter()
                .filter_map(Value::as_str)
                .map(str::to_string)
                .collect()
        })
        .unwrap_or_default()
}

fn first_line_has_scene_id(line: &str) -> bool {
    serde_json::from_str::<Value>(line)
        .ok()
        .and_then(|value| value.get("scene_id").and_then(Value::as_str).map(str::to_string))
        .is_some()
}

fn validate_data_root(profile: &ServerProfile, data_root: &str) -> Result<String, String> {
    let root = normalise_path(data_root, "data root")?;
    let known = profile
        .data_roots
        .iter()
        .filter_map(|candidate| normalise_path(candidate, "profile data root").ok())
        .any(|candidate| is_under(&root, &candidate));
    if !known {
        return Err(format!(
            "{data_root:?} is not one of this profile's data roots; refusing to import \
             predictions there."
        ));
    }
    Ok(root)
}

/// Imports the finished/streaming `predictions.jsonl` as a stored prediction run
/// under `<data_root>/predictions`, then returns the link for the viewer.
///
/// The importer is the documented coperceive command (the profile's
/// `coperceive_command`); it runs on the login node with a bounded timeout and
/// only touches the run output and the predictions root.
pub fn import_predictions(
    runner: &dyn RemoteRunner,
    profile: &ServerProfile,
    plan_id: &str,
    output_dir: &str,
    data_root: &str,
) -> Result<ImportedPredictions, String> {
    crate::run_plan::validate_plan_id(plan_id)?;
    let data_root = validate_data_root(profile, data_root)?;
    let predictions_file = output_path(profile, output_dir, "predictions.jsonl")?;
    let predictions_root = format!("{}/predictions", data_root.trim_end_matches('/'));
    let predictions_root = normalise_path(&predictions_root, "predictions root")?;
    if !is_under(&predictions_root, &data_root) {
        return Err("The predictions root escapes the data root.".to_string());
    }
    let nonempty = runner
        .run(
            &format!("test -s -- {}", shell_quote(&predictions_file)),
            READ_TIMEOUT,
            None,
        )
        .map_err(|error| format!("Could not check predictions.jsonl: {error}"))?;
    if !nonempty.success {
        return Err(
            "predictions.jsonl is empty or missing. Wait for the run to write its first \
             predictions, then try again."
                .to_string(),
        );
    }
    let head = runner
        .run(
            &format!("head -n 1 -- {}", shell_quote(&predictions_file)),
            READ_TIMEOUT,
            None,
        )
        .map_err(|error| format!("Could not read predictions.jsonl: {error}"))?;
    let scene_id_row = head.success && first_line_has_scene_id(head.stdout.trim_end());
    let metrics = read_metrics(runner, profile, output_dir)?;
    let scenes = metrics.as_ref().map(run_scenes).unwrap_or_default();
    let mut command: Vec<String> = profile.environment.coperceive_command.clone();
    command.extend([
        "predictions".to_string(),
        "import".to_string(),
        predictions_file.clone(),
        "--run".to_string(),
        plan_id.to_string(),
        "--root".to_string(),
        data_root.clone(),
        "--predictions".to_string(),
        predictions_root.clone(),
        "--force".to_string(),
        "--model".to_string(),
        "coperceive-desktop".to_string(),
        "--score-note".to_string(),
        "pipeline metrics report; imported by coperceive desktop".to_string(),
    ]);
    if !scene_id_row {
        match scenes.as_slice() {
            [scene] => {
                command.push("--scene".to_string());
                command.push(scene.clone());
            }
            [] => {
                return Err(
                    "predictions.jsonl has no scene_id and metrics.json is not written yet. \
                     Wait for the run to finish, then open predictions."
                        .to_string(),
                )
            }
            _ => {
                return Err(
                    "predictions.jsonl has no scene_id and the run covers several scenes. \
                     Ask the pipeline to include a scene_id key; coperceive will not guess."
                        .to_string(),
                )
            }
        }
    }
    let line = command
        .iter()
        .map(|part| shell_quote(part))
        .collect::<Vec<_>>()
        .join(" ");
    let imported = runner
        .run(&line, IMPORT_TIMEOUT, None)
        .map_err(|error| format!("Could not import the predictions: {error}"))?;
    if !imported.success {
        let detail = if imported.stderr.trim().is_empty() {
            imported.stdout.trim()
        } else {
            imported.stderr.trim()
        };
        return Err(format!(
            "coperceive predictions import failed: {detail}"
        ));
    }
    Ok(ImportedPredictions {
        run_id: plan_id.to_string(),
        scenes,
        predictions_root,
        url: String::new(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::hpc::{EnvironmentKind, EnvironmentSetup};
    use crate::ssh::CommandResult;
    use std::sync::Mutex;

    const CLUSTER_B: &str = include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/fixtures/hpc/cluster-b.json"
    ));

    fn profile() -> ServerProfile {
        let entry: crate::server_profiles::ServerEntry =
            serde_json::from_str(CLUSTER_B).expect("cluster-b fixture");
        let mut profile = entry.profile;
        profile.environment = EnvironmentSetup {
            kind: EnvironmentKind::Modules,
            modules: vec!["python/3.12".to_string()],
            coperceive_command: vec!["/apps/coperceive/bin/coperceive".to_string()],
            ..EnvironmentSetup::default()
        };
        profile
    }

    type Calls = std::sync::Arc<Mutex<Vec<String>>>;

    #[derive(Default)]
    struct FakeRunner {
        calls: Calls,
        responses: Mutex<Vec<(String, CommandResult)>>,
    }

    impl FakeRunner {
        fn calls(&self) -> Calls {
            std::sync::Arc::clone(&self.calls)
        }

        fn with(self, needle: &str, stdout: &str) -> Self {
            self.responses.lock().unwrap().push((
                needle.to_string(),
                CommandResult {
                    success: true,
                    stdout: stdout.to_string(),
                    stderr: String::new(),
                },
            ));
            self
        }

        fn fail(self, needle: &str) -> Self {
            self.responses.lock().unwrap().push((
                needle.to_string(),
                CommandResult {
                    success: false,
                    stdout: String::new(),
                    stderr: "missing".to_string(),
                },
            ));
            self
        }
    }

    impl RemoteRunner for FakeRunner {
        fn run(
            &self,
            remote_cmd: &str,
            _timeout: Duration,
            _stdin: Option<&str>,
        ) -> Result<CommandResult, String> {
            self.calls.lock().unwrap().push(remote_cmd.to_string());
            let responses = self.responses.lock().unwrap();
            for (needle, response) in responses.iter().rev() {
                if remote_cmd.contains(needle) {
                    return Ok(response.clone());
                }
            }
            Ok(CommandResult {
                success: false,
                stdout: String::new(),
                stderr: "no fake response".to_string(),
            })
        }
    }

    const PLAN_ID: &str = "plan-1793222864-c5af819dbd2b";

    fn predictions_file() -> String {
        format!("{}/runs/{PLAN_ID}/predictions.jsonl", profile().results_root)
    }

    #[test]
    fn a_tail_consumes_complete_lines_and_keeps_the_partial_one() {
        let payload = "{\"source\":\"bev\",\"agent_id\":\"vehicle\",\"time_us\":0}\n\
                       {\"source\":\"bev\",\"agent_id\":\"vehicle\",\"time_us\":1}\n\
                       {\"source\":\"lidar\",\"agent_id\":\"drone\",\"time_us\":1}\n\
                       {\"type\":\"progress\",\"frames_done\":3,\"frames_total\":10}\n\
                       {\"source\":\"bev\"";
        let complete = payload.rfind('\n').expect("complete line") + 1;
        let runner = FakeRunner::default().with(
            "predictions.jsonl",
            &format!("{}\n{}", 40 + complete, payload),
        );
        let tail = tail_predictions(&runner, &profile(), "/data/results/runs/plan-1793222864-c5af819dbd2b", 0, 1024)
            .expect("tail");
        assert_eq!(tail.boxes, 3);
        assert_eq!(tail.offset, complete as u64);
        assert_eq!(tail.progress.unwrap().frames_done, Some(3));
        assert_eq!(tail.sources.len(), 2);
        let latest = tail.latest.expect("latest box");
        assert_eq!(latest.agent_id, "drone");
        assert_eq!(latest.source, "lidar");
        assert_eq!(latest.time_us, 1);
        assert!(!tail.idle);
    }

    #[test]
    fn a_tail_reports_idle_and_a_shrunk_file_as_a_new_start() {
        let runner = FakeRunner::default().with("predictions.jsonl", "5\nabc");
        let tail = tail_predictions(&runner, &profile(), "/data/results/runs/plan-1793222864-c5af819dbd2b", 99, 1024)
            .expect("tail");
        assert_eq!(tail.size, 5);
        assert_eq!(tail.offset, 0);
        assert!(tail.idle);
    }

    #[test]
    fn malformed_prediction_lines_are_refused() {
        let runner = FakeRunner::default().with(
            "predictions.jsonl",
            "10\n{\"source\":\"bev\",\"agent_id\":\"vehicle\"}\nnot json\n",
        );
        let error = tail_predictions(&runner, &profile(), "/data/results/runs/plan-1793222864-c5af819dbd2b", 0, 1024)
            .expect_err("bad json");
        assert!(error.contains("line 2"), "{error}");
    }

    #[test]
    fn output_paths_must_stay_under_results_root() {
        let runner = FakeRunner::default();
        let error = tail_predictions(&runner, &profile(), "/tmp/elsewhere", 0, 1024)
            .expect_err("outside results_root");
        assert!(error.contains("outside results_root") || error.contains("scratch"), "{error}");
    }

    #[test]
    fn metrics_are_validated_and_absent_metrics_are_none() {
        let good = r#"{"schema":"coperceive-pipeline-metrics","version":1,"run":{"model":"bev","scenes":["Town03-000"]},"outputs":[],"metrics":{"latency_ms_per_frame":41.2}}"#;
        let runner = FakeRunner::default().with("metrics.json", good);
        let report = read_metrics(&runner, &profile(), "/data/results/runs/plan-1793222864-c5af819dbd2b")
            .expect("metrics")
            .expect("some");
        assert_eq!(report.metrics["latency_ms_per_frame"], 41.2);
        assert_eq!(report.run["model"], "bev");
        assert!(report.raw.contains("coperceive-pipeline-metrics"));

        let runner = FakeRunner::default().fail("metrics.json");
        assert!(read_metrics(&runner, &profile(), "/data/results/runs/plan-1793222864-c5af819dbd2b")
            .expect("none")
            .is_none());

        let runner = FakeRunner::default().with("metrics.json", r#"{"version":1}"#);
        let error = read_metrics(&runner, &profile(), "/data/results/runs/plan-1793222864-c5af819dbd2b")
            .expect_err("bad schema");
        assert!(error.contains("schema"), "{error}");
    }

    #[test]
    fn imports_a_single_scene_predictions_file_with_the_profile_command() {
        let runner = FakeRunner::default()
            .with("test -s", "")
            .with("head -n 1", r#"{"source":"bev","scene_id":"Town03-000"}"#)
            .with("metrics.json", r#"{"schema":"coperceive-pipeline-metrics","version":1,"run":{"scenes":["Town03-000"]},"outputs":[],"metrics":{}}"#)
            .with("predictions import", "");
        let imported = import_predictions(
            &runner,
            &profile(),
            PLAN_ID,
            "/data/results/runs/plan-1793222864-c5af819dbd2b",
            "/data/scenes",
        )
        .expect("import");
        assert_eq!(imported.run_id, PLAN_ID);
        assert_eq!(imported.predictions_root, "/data/scenes/predictions");
        let calls = runner.calls();
        let calls = calls.lock().unwrap();
        let command = calls.iter().find(|cmd| cmd.contains("predictions import")).expect("import call");
        assert!(command.contains("/apps/coperceive/bin/coperceive"), "{command}");
        assert!(command.contains("--predictions /data/scenes/predictions"), "{command}");
        assert!(command.contains("--run plan-1"), "{command}");
        assert!(!command.contains("--scene"), "scene_id rows need no --scene: {command}");
    }

    #[test]
    fn imports_without_scene_id_only_when_metrics_name_one_scene() {
        let runner = FakeRunner::default()
            .with("test -s", "")
            .with("head -n 1", r#"{"source":"bev","agent_id":"vehicle"}"#)
            .with("metrics.json", r#"{"schema":"coperceive-pipeline-metrics","version":1,"run":{"scenes":["Town03-000"]},"outputs":[],"metrics":{}}"#)
            .with("predictions import", "");
        let imported = import_predictions(
            &runner,
            &profile(),
            PLAN_ID,
            "/data/results/runs/plan-1793222864-c5af819dbd2b",
            "/data/scenes",
        )
        .expect("import");
        assert_eq!(imported.scenes, vec!["Town03-000".to_string()]);
        let calls = runner.calls();
        let calls = calls.lock().unwrap();
        let command = calls.iter().find(|cmd| cmd.contains("predictions import")).expect("import call");
        assert!(command.contains("--scene Town03-000"), "{command}");
    }

    #[test]
    fn a_prediction_run_id_and_data_root_are_validated() {
        let runner = FakeRunner::default();
        let error = import_predictions(
            &runner,
            &profile(),
            "bad/plan",
            "/data/results/runs/plan-1793222864-c5af819dbd2b",
            "/data/scenes",
        )
        .expect_err("bad plan id");
        assert!(error.contains("plan"), "{error}");
        let error = import_predictions(
            &runner,
            &profile(),
            PLAN_ID,
            "/data/results/runs/plan-1793222864-c5af819dbd2b",
            "/etc",
        )
        .expect_err("bad data root");
        assert!(error.contains("data root"), "{error}");
    }

    #[test]
    fn an_empty_predictions_file_is_refused() {
        let runner = FakeRunner::default().fail("test -s");
        let error = import_predictions(
            &runner,
            &profile(),
            PLAN_ID,
            "/data/results/runs/plan-1793222864-c5af819dbd2b",
            "/data/scenes",
        )
        .expect_err("empty");
        assert!(error.contains("empty"), "{error}");
    }

    #[test]
    fn a_failed_import_reports_the_remote_message() {
        let runner = FakeRunner::default()
            .with("test -s", "")
            .with("head -n 1", r#"{"source":"bev","scene_id":"Town03-000"}"#)
            .with("metrics.json", r#"{"schema":"coperceive-pipeline-metrics","version":1,"run":{"scenes":["Town03-000"]},"outputs":[],"metrics":{}}"#)
            .with("predictions import", "");
        // Override the import response with a failure by adding a later needle.
        let runner = runner.fail("predictions import");
        let error = import_predictions(
            &runner,
            &profile(),
            PLAN_ID,
            "/data/results/runs/plan-1793222864-c5af819dbd2b",
            "/data/scenes",
        )
        .expect_err("failed import");
        assert!(error.contains("failed"), "{error}");
    }

    #[test]
    fn the_tail_command_is_bounded_and_quoted() {
        let runner = FakeRunner::default().with("predictions.jsonl", "0\n");
        let _ = tail_predictions(&runner, &profile(), "/data/results/runs/plan-1793222864-c5af819dbd2b", 0, 10);
        let calls = runner.calls();
        let calls = calls.lock().unwrap();
        // 10 is clamped up to the 64 KiB minimum, never 10 (a tight per-line call).
        assert!(calls[0].contains("head -c $3"), "{}", calls[0]);
        assert!(calls[0].contains(" 0 65536"), "{}", calls[0]);
    }
}
