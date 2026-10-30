// SPDX-License-Identifier: Apache-2.0
//! Remote HPC 6/6: the run history (cancel and re-run act on its entries).
//!
//! The history is the scheduler's own accounting database filtered to jobs this
//! app names (`coperceive-run-<plan-id>`), plus whether the saved plan still
//! exists under the profile's `results_root/plans`. Nothing is stored locally:
//! reconnecting to the same server shows the same runs.

use std::collections::BTreeSet;
use std::time::Duration;

use serde::{Deserialize, Serialize};

use crate::hpc::{is_under, normalise_path, JobState, ServerProfile};
use crate::job_watch::is_active;
use crate::run_plan::validate_plan_id;
use crate::slurm_adapter::{map_slurm_state, RemoteRunner};
use crate::ssh::shell_quote;

const HISTORY_TIMEOUT: Duration = Duration::from_secs(20);
pub const DEFAULT_HISTORY_LIMIT: u32 = 50;
pub const MAX_HISTORY_LIMIT: u32 = 200;
const JOB_PREFIX: &str = "coperceive-run-";

/// One submitted run in the history table.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RunHistoryEntry {
    pub job_id: String,
    pub plan_id: String,
    pub state: JobState,
    /// True while the job can still be cancelled.
    pub active: bool,
    #[serde(default)]
    pub elapsed: Option<String>,
    #[serde(default)]
    pub time_limit: Option<String>,
    #[serde(default)]
    pub partition: Option<String>,
    #[serde(default)]
    pub started: Option<String>,
    #[serde(default)]
    pub ended: Option<String>,
    /// The saved plan still exists under results_root/plans, so re-run works.
    pub plan_available: bool,
}

fn plans_dir(profile: &ServerProfile) -> Result<String, String> {
    let root = normalise_path(&profile.results_root, "results_root")?;
    let plans = normalise_path(&format!("{}/plans", root.trim_end_matches('/')), "plans dir")?;
    if !is_under(&plans, &root) {
        return Err("The plans directory escapes results_root.".to_string());
    }
    Ok(plans)
}

fn parse_plan_ids(stdout: &str) -> BTreeSet<String> {
    stdout
        .lines()
        .map(str::trim)
        .filter(|name| !name.is_empty() && !name.contains('/') && !name.contains('\0'))
        .filter_map(|name| {
            let id = name.strip_suffix(".json")?;
            if validate_plan_id(id).is_ok() {
                Some(id.to_string())
            } else {
                None
            }
        })
        .collect()
}

fn parse_sacct_line(line: &str, plan_ids: &BTreeSet<String>) -> Option<RunHistoryEntry> {
    let mut fields = line.split('|');
    let job_id = fields.next().unwrap_or("").trim();
    let job_name = fields.next().unwrap_or("").trim();
    let raw_state = fields.next().unwrap_or("").trim();
    let elapsed = fields.next().unwrap_or("").trim();
    let time_limit = fields.next().unwrap_or("").trim();
    let partition = fields.next().unwrap_or("").trim();
    let started = fields.next().unwrap_or("").trim();
    let ended = fields.next().unwrap_or("").trim();
    if job_id.is_empty() || !job_id.chars().all(|ch| ch.is_ascii_digit()) || job_id.len() > 20 {
        return None;
    }
    let plan_id = job_name.strip_prefix(JOB_PREFIX)?;
    if validate_plan_id(plan_id).is_err() {
        return None;
    }
    let state = map_slurm_state(raw_state.split_whitespace().next().unwrap_or(raw_state));
    let optional = |value: &str| {
        let trimmed = value.trim();
        if trimmed.is_empty() || trimmed == "Unknown" || trimmed == "None" || trimmed == "(null)" {
            None
        } else {
            Some(trimmed.to_string())
        }
    };
    Some(RunHistoryEntry {
        job_id: job_id.to_string(),
        plan_id: plan_id.to_string(),
        state,
        active: is_active(state),
        elapsed: optional(elapsed),
        time_limit: optional(time_limit),
        partition: optional(partition),
        started: optional(started),
        ended: optional(ended),
        plan_available: plan_ids.contains(plan_id),
    })
}

/// Lists the app's submitted runs from Slurm accounting, newest first.
pub fn list_run_history(
    runner: &dyn RemoteRunner,
    profile: &ServerProfile,
    limit: u32,
) -> Result<Vec<RunHistoryEntry>, String> {
    let limit = limit.clamp(1, MAX_HISTORY_LIMIT);
    let plans = plans_dir(profile)?;
    let list_command = format!("ls -1 -- {} 2>/dev/null", shell_quote(&plans));
    let plan_ids = runner
        .run(&list_command, HISTORY_TIMEOUT, None)
        .map(|result| {
            if result.success {
                parse_plan_ids(&result.stdout)
            } else {
                BTreeSet::new()
            }
        })
        .unwrap_or_default();
    let command = "sacct -u \"$USER\" -n -X -S now-30days -P \
                   -o JobID%20,JobName%80,State%30,Elapsed%20,Timelimit%20,Partition%40,Start%25,End%25 \
                   2>/dev/null"
        .to_string();
    let result = runner
        .run(&command, HISTORY_TIMEOUT, None)
        .map_err(|error| format!("Could not read the run history: {error}"))?;
    if !result.success {
        return Err(
            "The scheduler did not return the run history. Check that sacct is available on this \
             server."
                .to_string(),
        );
    }
    let mut entries: Vec<RunHistoryEntry> = result
        .stdout
        .lines()
        .filter_map(|line| parse_sacct_line(line, &plan_ids))
        .collect();
    entries.sort_by(|left, right| {
        right
            .started
            .cmp(&left.started)
            .then_with(|| right.job_id.cmp(&left.job_id))
    });
    entries.truncate(limit as usize);
    Ok(entries)
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
            ..EnvironmentSetup::default()
        };
        profile
    }

    fn job_name(plan_id: &str) -> String {
        format!("{JOB_PREFIX}{plan_id}")
    }

    const PLAN_A: &str = "plan-1793222864-c5af819dbd2b";
    const PLAN_B: &str = "plan-1793222900-1122334455aa";

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

    #[test]
    fn lists_only_app_jobs_with_plan_availability_and_newest_first() {
        let runner = FakeRunner::default()
            .with("ls -1", &format!("{PLAN_A}.json\nREADME.txt\n{PLAN_B}.json\n"))
            .with(
                "sacct -u",
                &format!(
                    "424242|{}|COMPLETED|01:02:03|04:00:00|main-cpu|2026-10-28T10:00:00|2026-10-28T11:02:03\n\
                     424243|{}|RUNNING|00:10:00|04:00:00|gpu-h100|2026-10-29T09:00:00|Unknown\n\
                     424244|someone-elses-job|RUNNING|00:01:00|03:00:00|main-cpu|2026-10-29T09:00:00|Unknown\n\
                     424245|{}|FAILED|2-00:00:00|2-00:00:00|main-cpu|2026-10-29T08:00:00|2026-10-31T08:00:00\n",
                    job_name(PLAN_A),
                    job_name(PLAN_B),
                    job_name("not-a-plan")
                ),
            );
        let entries = list_run_history(&runner, &profile(), 50).expect("history");
        // Newest first; the foreign job and the invalid plan id are ignored.
        assert_eq!(entries.len(), 2);
        assert_eq!(entries[0].job_id, "424243");
        assert_eq!(entries[0].state, JobState::Running);
        assert!(entries[0].active);
        assert!(entries[0].plan_available);
        assert_eq!(entries[0].started.as_deref(), Some("2026-10-29T09:00:00"));
        assert_eq!(entries[0].ended, None);
        assert_eq!(entries[1].job_id, "424242");
        assert_eq!(entries[1].state, JobState::Completed);
        assert!(!entries[1].active);
        assert!(entries[1].plan_available);
        assert_eq!(entries[1].elapsed.as_deref(), Some("01:02:03"));
    }

    #[test]
    fn plan_availability_comes_from_the_plans_directory() {
        let runner = FakeRunner::default()
            .with("ls -1", &format!("{PLAN_A}.json\n../escape\nbad plan\n"))
            .with(
                "sacct -u",
                &format!(
                    "1|{}|COMPLETED|00:01:00|01:00:00|main|2026-10-28T10:00:00|2026-10-28T10:01:00\n",
                    job_name(PLAN_A)
                ),
            );
        let entries = list_run_history(&runner, &profile(), 50).expect("history");
        assert!(entries[0].plan_available);

        let runner = FakeRunner::default()
            .with("ls -1", "something-else.json\n")
            .with(
                "sacct -u",
                &format!(
                    "1|{}|COMPLETED|00:01:00|01:00:00|main|2026-10-28T10:00:00|2026-10-28T10:01:00\n",
                    job_name(PLAN_A)
                ),
            );
        let entries = list_run_history(&runner, &profile(), 50).expect("history");
        assert!(!entries[0].plan_available);
    }

    #[test]
    fn the_limit_is_clamped_and_applied_after_sorting() {
        let lines: String = (0..5)
            .map(|index| {
                format!(
                    "{}|{}|COMPLETED|00:01:00|01:00:00|main|2026-10-2{}T10:00:00|2026-10-2{}T10:01:00\n",
                    index + 1,
                    job_name(PLAN_A),
                    index,
                    index
                )
            })
            .collect();
        let runner = FakeRunner::default()
            .with("ls -1", &format!("{PLAN_A}.json\n"))
            .with("sacct -u", &lines);
        let entries = list_run_history(&runner, &profile(), 2).expect("history");
        assert_eq!(entries.len(), 2);
        assert_eq!(entries[0].job_id, "5");
        assert_eq!(entries[1].job_id, "4");
    }

    #[test]
    fn bad_job_and_plan_names_are_ignored() {
        let runner = FakeRunner::default()
            .with("ls -1", "")
            .with(
                "sacct -u",
                "abc|x|RUNNING|00:01:00|01:00:00|main|2026-10-29T09:00:00|Unknown\n\
                 12|someone|RUNNING|00:01:00|01:00:00|main|2026-10-29T09:00:00|Unknown\n\
                 13|coperceive-run-bad/plan|RUNNING|00:01:00|01:00:00|main|2026-10-29T09:00:00|Unknown\n",
            );
        assert!(list_run_history(&runner, &profile(), 50).expect("history").is_empty());
    }

    #[test]
    fn a_scheduler_without_sacct_reports_the_error() {
        let runner = FakeRunner::default()
            .with("ls -1", "")
            .fail("sacct -u");
        let error = list_run_history(&runner, &profile(), 50).expect_err("no sacct");
        assert!(error.contains("sacct"), "{error}");
    }

    #[test]
    fn the_history_command_is_fixed_and_read_only() {
        let runner = FakeRunner::default().with("ls -1", "").with("sacct -u", "");
        let _ = list_run_history(&runner, &profile(), 50);
        let calls = runner.calls();
        let calls = calls.lock().unwrap();
        assert!(calls.iter().any(|call| call.contains("sacct -u \"$USER\"")));
        assert!(calls.iter().any(|call| call.starts_with("ls -1 -- ")));
        assert!(!calls.iter().any(|call| call.contains("sbatch") || call.contains("scancel")));
    }
}
