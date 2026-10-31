// SPDX-License-Identifier: Apache-2.0
//! Remote HPC 5/6: watch a submitted run (state, bounded logs, cancel).
//!
//! Everything here is read-only except `cancel`, which requires an explicit
//! confirmation and only acts on a job whose name this app generated from the
//! saved plan (`coperceive-run-<plan-id>`). Job ids and log paths are validated
//! before they reach a shell; log paths must stay under the profile's
//! `results_root` (the same lexical containment rule the plan checks use).

use std::time::Duration;

use serde::{Deserialize, Serialize};

use crate::hpc::{is_under, normalise_path, JobState, ServerProfile};
use crate::job_submit::safe_job_name;
use crate::run_plan::validate_plan_id;
use crate::slurm_adapter::{map_slurm_state, RemoteRunner};
use crate::ssh::shell_quote;

const STATUS_TIMEOUT: Duration = Duration::from_secs(15);
const LOG_TIMEOUT: Duration = Duration::from_secs(15);
const CANCEL_TIMEOUT: Duration = Duration::from_secs(15);
/// Default and hard bounds for a log tail; a caller can ask for 10..=200 lines.
pub const DEFAULT_LOG_LINES: u32 = 50;
pub const MIN_LOG_LINES: u32 = 10;
pub const MAX_LOG_LINES: u32 = 200;
const MAX_LOG_BYTES: usize = 64 * 1024;

/// A live view of one submitted run.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct JobWatchStatus {
    pub job_id: String,
    pub plan_id: String,
    pub job_name: String,
    pub state: JobState,
    /// Slurm's queue reason while PENDING (e.g. "Resources").
    #[serde(default)]
    pub queue_reason: Option<String>,
    /// Elapsed wall time from Slurm (e.g. "00:12:00").
    #[serde(default)]
    pub elapsed: Option<String>,
    /// Wall time left from Slurm (e.g. "03:48:00").
    #[serde(default)]
    pub time_left: Option<String>,
    #[serde(default)]
    pub node: Option<String>,
}

/// A bounded tail of one of the run's log files.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct JobLogTail {
    pub job_id: String,
    pub stream: String,
    pub lines: u32,
    pub text: String,
    /// True when the requested line count was reached (there may be more).
    pub truncated: bool,
}

/// The scheduler's answer to an explicit cancel request.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct JobCancelResult {
    pub job_id: String,
    pub state: JobState,
    pub queue_reason: Option<String>,
}

fn validate_job_id(job_id: &str) -> Result<(), String> {
    let valid = !job_id.is_empty()
        && job_id.len() <= 20
        && job_id.chars().all(|ch| ch.is_ascii_digit());
    if !valid {
        return Err(format!(
            "{job_id:?} is not a Slurm job id; refusing to query or act on it."
        ));
    }
    Ok(())
}

fn expected_job_name(plan_id: &str) -> Result<String, String> {
    validate_plan_id(plan_id)?;
    Ok(safe_job_name(plan_id))
}

/// Job states that are still being watched (live polling is useful).
pub fn is_active(state: JobState) -> bool {
    matches!(state, JobState::Pending | JobState::Running)
}

fn expected_user(profile: &ServerProfile) -> Option<String> {
    profile.user.clone().or_else(|| {
        profile
            .ssh_target()
            .ok()
            .and_then(|target| target.rsplit_once('@').map(|(user, _)| user.to_string()))
    })
}

fn check_owner(
    raw_name: &str,
    raw_user: &str,
    job_id: &str,
    expected_name: &str,
    expected_user: Option<&str>,
) -> Result<(), String> {
    // Slurm truncates fixed-width fields with a trailing '+'. Even if the
    // prefix happens to match, a marked name is not proof of ownership.
    if raw_name.contains('+') || raw_name != expected_name {
        return Err(format!(
            "Job {job_id} is {raw_name:?}, not this app's job ({expected_name:?}); refusing to watch it."
        ));
    }
    if let (Some(expected), false) = (expected_user, raw_user.trim().is_empty()) {
        if raw_user.trim() != expected {
            return Err(format!(
                "Job {job_id} belongs to user {:?}, not {expected:?}; refusing to watch it.",
                raw_user.trim()
            ));
        }
    }
    Ok(())
}

fn parse_status_line(
    line: &str,
    plan_id: &str,
    job_id: &str,
    expected_name: &str,
    expected_user: Option<&str>,
) -> Result<JobWatchStatus, String> {
    let fields: Vec<&str> = line.split('|').collect();
    let raw_name = fields.first().copied().unwrap_or("").trim();
    let raw_user = fields.get(6).copied().unwrap_or("").trim();
    check_owner(raw_name, raw_user, job_id, expected_name, expected_user)?;
    let raw_state = fields.get(1).copied().unwrap_or("").trim();
    if raw_state.is_empty() {
        return Err("Slurm returned no state for the job.".to_string());
    }
    let state = map_slurm_state(raw_state.split_whitespace().next().unwrap_or(raw_state));
    let reason = fields.get(2).copied().unwrap_or("").trim();
    let elapsed = fields.get(3).copied().unwrap_or("").trim();
    let time_left = fields.get(4).copied().unwrap_or("").trim();
    let node = fields.get(5).copied().unwrap_or("").trim();
    Ok(JobWatchStatus {
        job_id: job_id.to_string(),
        plan_id: plan_id.to_string(),
        job_name: expected_name.to_string(),
        state,
        queue_reason: if reason.is_empty() || reason == "None" || reason == "(null)" {
            None
        } else {
            Some(reason.to_string())
        },
        elapsed: if elapsed.is_empty() || elapsed == "0:00" {
            None
        } else {
            Some(elapsed.to_string())
        },
        time_left: if time_left.is_empty() || time_left == "N/A" || time_left == "UNLIMITED" {
            None
        } else {
            Some(time_left.to_string())
        },
        node: if node.is_empty() || node == "(null)" {
            None
        } else {
            Some(node.to_string())
        },
    })
}

fn status_from_accounting(
    line: &str,
    plan_id: &str,
    job_id: &str,
    expected_name: &str,
    expected_user: Option<&str>,
) -> Result<Option<JobWatchStatus>, String> {
    let trimmed = line.trim();
    if trimmed.is_empty() {
        return Ok(None);
    }
    let fields: Vec<&str> = trimmed.split_whitespace().collect();
    let raw_name = fields.first().copied().unwrap_or("");
    let raw_user = fields.get(4).copied().unwrap_or("");
    check_owner(raw_name, raw_user, job_id, expected_name, expected_user)?;
    let raw_state = fields.get(1).copied().unwrap_or("");
    let state = map_slurm_state(raw_state.split_whitespace().next().unwrap_or(raw_state));
    let elapsed = fields.get(2).copied().unwrap_or("");
    Ok(Some(JobWatchStatus {
        job_id: job_id.to_string(),
        plan_id: plan_id.to_string(),
        job_name: expected_name.to_string(),
        state,
        queue_reason: None,
        elapsed: if elapsed.is_empty() || elapsed == "00:00:00" {
            None
        } else {
            Some(elapsed.to_string())
        },
        time_left: None,
        node: None,
    }))
}

/// Reads the current state, elapsed time and remaining wall time of a run.
pub fn watch_status(
    runner: &dyn RemoteRunner,
    profile: &ServerProfile,
    plan_id: &str,
    job_id: &str,
) -> Result<JobWatchStatus, String> {
    validate_job_id(job_id)?;
    let expected_name = expected_job_name(plan_id)?;
    let expected_user = expected_user(profile);
    let squeue = format!(
        "squeue -h -j {job_id} -o '%.256j|%T|%r|%M|%L|%N|%u' 2>/dev/null"
    );
    if let Ok(result) = runner.run(&squeue, STATUS_TIMEOUT, None) {
        if result.success {
            if let Some(line) = result.stdout.lines().find(|line| !line.trim().is_empty()) {
                return parse_status_line(
                    line,
                    plan_id,
                    job_id,
                    &expected_name,
                    expected_user.as_deref(),
                );
            }
        }
    }
    let sacct = format!(
        "sacct -j {job_id} -n -X -o JobName%256,State%20,Elapsed%20,TimeLimit%20,User%32 2>/dev/null"
    );
    if let Ok(result) = runner.run(&sacct, STATUS_TIMEOUT, None) {
        if result.success {
            if let Some(line) = result.stdout.lines().find(|line| !line.trim().is_empty()) {
                if let Some(status) =
                    status_from_accounting(
                        line,
                        plan_id,
                        job_id,
                        &expected_name,
                        expected_user.as_deref(),
                    )?
                {
                    return Ok(status);
                }
            }
        }
    }
    Err(format!(
        "Slurm has no record of job {job_id} for plan {plan_id}. It may have been purged from the \
         accounting database; the run is not running any more."
    ))
}

fn log_path(
    profile: &ServerProfile,
    plan_id: &str,
    job_id: &str,
    stream: &str,
) -> Result<String, String> {
    let extension = match stream {
        "stdout" | "out" => "out",
        "stderr" | "err" => "err",
        other => {
            return Err(format!(
                "{other:?} is not a log stream; use stdout or stderr."
            ))
        }
    };
    let name = expected_job_name(plan_id)?;
    let root = normalise_path(&profile.results_root, "results_root")?;
    let path = format!(
        "{}/{}-{job_id}.{extension}",
        root.trim_end_matches('/'),
        name
    );
    let normalised = normalise_path(&path, "job log path")?;
    if !is_under(&normalised, &root) {
        return Err(format!(
            "The job log path {normalised:?} escapes the profile's results_root {root:?}; refusing to read it."
        ));
    }
    Ok(normalised)
}

fn truncate_utf8(text: &str, limit: usize) -> String {
    if text.len() <= limit {
        return text.to_string();
    }
    let mut end = limit;
    while end > 0 && !text.is_char_boundary(end) {
        end -= 1;
    }
    format!("{}\n… (truncated)", &text[..end])
}

/// Tails the run's stdout or stderr log over SSH.
pub fn tail_log(
    runner: &dyn RemoteRunner,
    profile: &ServerProfile,
    plan_id: &str,
    job_id: &str,
    stream: &str,
    lines: u32,
) -> Result<JobLogTail, String> {
    validate_job_id(job_id)?;
    if !(MIN_LOG_LINES..=MAX_LOG_LINES).contains(&lines) {
        return Err(format!(
            "Ask for between {MIN_LOG_LINES} and {MAX_LOG_LINES} log lines, got {lines}."
        ));
    }
    let path = log_path(profile, plan_id, job_id, stream)?;
    let command = format!(
        "tail -n {lines} -- {} 2>/dev/null",
        shell_quote(&path)
    );
    let result = runner
        .run(&command, LOG_TIMEOUT, None)
        .map_err(|error| format!("Could not read the job log: {error}"))?;
    if !result.success {
        return Err(
            "No log lines yet. The scheduler may not have created the file; try again once the \
             job starts."
                .to_string(),
        );
    }
    let text = truncate_utf8(&result.stdout, MAX_LOG_BYTES);
    Ok(JobLogTail {
        job_id: job_id.to_string(),
        stream: if path.ends_with(".err") { "stderr" } else { "stdout" }.to_string(),
        lines,
        truncated: result.stdout.lines().count() >= lines as usize,
        text,
    })
}

/// Cancels a run this app submitted, after an explicit confirmation. A finished
/// job is refused instead of being sent to `scancel`.
pub fn cancel_job(
    runner: &dyn RemoteRunner,
    profile: &ServerProfile,
    plan_id: &str,
    job_id: &str,
    confirmed: bool,
) -> Result<JobCancelResult, String> {
    if !confirmed {
        return Err(
            "Tick the confirmation before cancelling the job. Nothing was cancelled.".to_string(),
        );
    }
    let status = watch_status(runner, profile, plan_id, job_id)?;
    if !is_active(status.state) {
        return Err(format!(
            "Job {job_id} is {:?}, so there is nothing to cancel.",
            status.state
        ));
    }
    let command = format!("scancel {job_id}");
    let result = runner
        .run(&command, CANCEL_TIMEOUT, None)
        .map_err(|error| format!("Could not cancel the job: {error}"))?;
    if !result.success {
        let detail = if result.stderr.trim().is_empty() {
            result.stdout.trim()
        } else {
            result.stderr.trim()
        };
        return Err(format!("Slurm refused the cancellation: {detail}"));
    }
    Ok(JobCancelResult {
        job_id: job_id.to_string(),
        state: JobState::Cancelled,
        queue_reason: None,
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
        profile.defaults.partition = Some("ps".to_string());
        profile.environment = EnvironmentSetup {
            kind: EnvironmentKind::Modules,
            modules: vec!["python/3.12".to_string()],
            ..EnvironmentSetup::default()
        };
        profile
    }

    fn plan_id() -> &'static str {
        "plan-1793222864-c5af819dbd2b"
    }

    type Calls = std::sync::Arc<Mutex<Vec<(String, Option<String>)>>>;

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

        fn without(self, needle: &str) -> Self {
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
            stdin: Option<&str>,
        ) -> Result<CommandResult, String> {
            self.calls
                .lock()
                .unwrap()
                .push((remote_cmd.to_string(), stdin.map(str::to_string)));
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

    fn expected_name() -> String {
        safe_job_name(plan_id())
    }

    #[test]
    fn a_pending_job_reports_the_queue_reason_and_times() {
        let runner = FakeRunner::default().with(
            "squeue -h -j 424242",
            &format!(
                "{}|PENDING|Resources|00:00:10|03:59:50|(null)\n",
                expected_name()
            ),
        );
        let status = watch_status(&runner, &profile(), plan_id(), "424242").expect("status");
        assert_eq!(status.state, JobState::Pending);
        assert_eq!(status.queue_reason.as_deref(), Some("Resources"));
        assert_eq!(status.elapsed.as_deref(), Some("00:00:10"));
        assert_eq!(status.time_left.as_deref(), Some("03:59:50"));
    }

    #[test]
    fn a_running_job_reports_elapsed_time_left_and_node() {
        let runner = FakeRunner::default().with(
            "squeue -h -j 424242",
            &format!(
                "{}|RUNNING|None|00:12:00|03:48:00|node11\n",
                expected_name()
            ),
        );
        let status = watch_status(&runner, &profile(), plan_id(), "424242").expect("status");
        assert_eq!(status.state, JobState::Running);
        assert_eq!(status.queue_reason, None);
        assert_eq!(status.elapsed.as_deref(), Some("00:12:00"));
        assert_eq!(status.time_left.as_deref(), Some("03:48:00"));
        assert_eq!(status.node.as_deref(), Some("node11"));
    }

    #[test]
    fn completed_failed_and_cancelled_jobs_fall_back_to_accounting() {
        for (raw, expected) in [
            ("COMPLETED", JobState::Completed),
            ("FAILED", JobState::Failed),
            ("CANCELLED", JobState::Cancelled),
        ] {
            let runner = FakeRunner::default()
                .without("squeue -h -j 424242")
                .with(
                    "sacct -j 424242",
                    &format!(
                        "{} {} 04:00:00 04:00:00\n",
                        expected_name(),
                        raw
                    ),
                );
            let status = watch_status(&runner, &profile(), plan_id(), "424242").expect("status");
            assert_eq!(status.state, expected, "raw {raw}");
            assert_eq!(status.elapsed.as_deref(), Some("04:00:00"));
        }
    }

    #[test]
    fn full_name_formats_are_requested_and_a_plus_marker_never_matches() {
        let runner = FakeRunner::default().with(
            "squeue -h -j 424242",
            &format!("{}|RUNNING|None|00:01:00|03:00:00|node1|bob\n", expected_name()),
        );
        let status = watch_status(&runner, &profile(), plan_id(), "424242").expect("status");
        assert_eq!(status.state, JobState::Running);
        let squeue = runner.calls().lock().unwrap()[0].0.clone();
        assert!(squeue.contains("%.256j"), "{squeue}");
        assert!(squeue.contains("%u"), "{squeue}");

        let truncated = format!(
            "{}+|RUNNING|None|00:01:00|03:00:00|node1|bob\n",
            &expected_name()[..40]
        );
        let runner = FakeRunner::default().with("squeue -h -j 424242", &truncated);
        let error = watch_status(&runner, &profile(), plan_id(), "424242").expect_err("truncated");
        assert!(error.contains("not this app's job"), "{error}");

        let runner = FakeRunner::default().without("squeue -h -j 424242").with(
            "sacct -j 424242",
            &format!("{} COMPLETED 00:01:00 04:00:00 bob\n", expected_name()),
        );
        let status = watch_status(&runner, &profile(), plan_id(), "424242").expect("sacct");
        assert_eq!(status.state, JobState::Completed);
        let calls = runner.calls();
        let calls = calls.lock().unwrap();
        assert!(
            calls.iter().any(|(command, _)| command.contains("JobName%256")),
            "{calls:?}"
        );
    }

    #[test]
    fn a_different_user_is_refused_when_the_profile_names_one() {
        let mut profile = profile();
        profile.user = Some("alice".to_string());
        let runner = FakeRunner::default().with(
            "squeue -h -j 424242",
            &format!("{}|RUNNING|None|00:01:00|03:00:00|node1|bob\n", expected_name()),
        );
        let error = watch_status(&runner, &profile, plan_id(), "424242").expect_err("user");
        assert!(error.contains("belongs to user"), "{error}");
    }

    #[test]
    fn a_foreign_or_missing_job_is_refused() {
        let runner = FakeRunner::default().with(
            "squeue -h -j 424242",
            "someone-elses-job|RUNNING|None|00:01:00|03:00:00|node1\n",
        );
        let error = watch_status(&runner, &profile(), plan_id(), "424242").expect_err("foreign");
        assert!(error.contains("not this app's job"), "{error}");

        let runner = FakeRunner::default();
        let error = watch_status(&runner, &profile(), plan_id(), "424242").expect_err("missing");
        assert!(error.contains("no record of job"), "{error}");
    }

    #[test]
    fn job_and_plan_ids_are_validated_before_any_command() {
        let runner = FakeRunner::default();
        for bad in ["", "abc", "424242; rm -rf /", "1 2"] {
            assert!(watch_status(&runner, &profile(), plan_id(), bad).is_err());
        }
        assert!(watch_status(&runner, &profile(), "bad/plan", "424242").is_err());
        assert!(runner.calls().lock().unwrap().is_empty());
    }

    #[test]
    fn logs_are_bounded_on_the_expected_path() {
        let runner = FakeRunner::default().with(
            "tail -n 50 --",
            "starting run\nwrote predictions\n",
        );
        let tail = tail_log(&runner, &profile(), plan_id(), "424242", "stdout", 50).expect("tail");
        assert!(tail.text.contains("wrote predictions"));
        assert_eq!(tail.lines, 50);
        let calls = runner.calls();
        let calls = calls.lock().unwrap();
        let command = &calls[0].0;
        assert!(command.starts_with("tail -n 50 -- "), "{command}");
        assert!(command.contains("-424242.out"), "{command}");
        assert!(command.contains(&profile().results_root), "{command}");
    }

    #[test]
    fn log_streams_and_line_counts_are_bounded() {
        let runner = FakeRunner::default();
        let error = tail_log(&runner, &profile(), plan_id(), "424242", "stdout", 1)
            .expect_err("too few lines");
        assert!(error.contains("between 10 and 200"), "{error}");
        let error = tail_log(&runner, &profile(), plan_id(), "424242", "stdout", 500)
            .expect_err("too many lines");
        assert!(error.contains("between 10 and 200"), "{error}");
        let error = tail_log(&runner, &profile(), plan_id(), "424242", "bogus", 50)
            .expect_err("bogus stream");
        assert!(error.contains("stdout or stderr"), "{error}");
    }

    #[test]
    fn a_finished_job_is_not_sent_to_scancel() {
        let runner = FakeRunner::default()
            .without("squeue -h -j 424242")
            .with(
                "sacct -j 424242",
                &format!("{} COMPLETED 04:00:00 04:00:00\n", expected_name()),
            );
        let error = cancel_job(&runner, &profile(), plan_id(), "424242", true)
            .expect_err("finished");
        assert!(error.contains("nothing to cancel"), "{error}");
        assert!(!runner
            .calls()
            .lock()
            .unwrap()
            .iter()
            .any(|(command, _)| command.contains("scancel")));
    }

    #[test]
    fn cancel_requires_confirmation_and_calls_scancel_once() {
        let runner = FakeRunner::default().with(
            "squeue -h -j 424242",
            &format!("{}|RUNNING|None|00:01:00|03:59:00|node1\n", expected_name()),
        );
        let error = cancel_job(&runner, &profile(), plan_id(), "424242", false)
            .expect_err("unconfirmed");
        assert!(error.contains("confirmation"), "{error}");
        assert!(!runner
            .calls()
            .lock()
            .unwrap()
            .iter()
            .any(|(command, _)| command.contains("scancel")));

        let runner = runner.with("scancel 424242", "");
        let result = cancel_job(&runner, &profile(), plan_id(), "424242", true).expect("cancel");
        assert_eq!(result.state, JobState::Cancelled);
        let calls = runner.calls();
        let calls = calls.lock().unwrap();
        assert_eq!(
            calls
                .iter()
                .filter(|(command, _)| command == "scancel 424242")
                .count(),
            1
        );
    }
}
