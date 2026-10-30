// SPDX-License-Identifier: Apache-2.0
//! Runtime-gated test seams for the real-app macOS e2e harness.
//!
//! `scripts/desktop/e2e_mac.sh` launches the built `.app` with
//! `COPERCEIVE_TEST_ENV=1` and drives its real UI through the loopback
//! controller. Two gestures cannot be driven from a headless runner: the
//! native folder picker, and a full Griffin download (slow and network-bound).
//! These helpers let the harness answer the picker with a temp folder and run a
//! deterministic local dataset job instead.
//!
//! Every seam is inert unless the app was started in test mode
//! (`COPERCEIVE_TEST_ENV` or `COPERCEIVE_TEST_MARKER_FILE` set, which the
//! production launcher never does). In production the folder pickers still open
//! the native dialog and dataset jobs still run
//! `coperceive.cli dataset download` with the bundled Python.

use std::path::Path;

/// True when the app was launched by the real-app e2e harness.
pub fn test_env_active() -> bool {
    std::env::var_os("COPERCEIVE_TEST_ENV").is_some()
        || std::env::var_os("COPERCEIVE_TEST_MARKER_FILE").is_some()
}

/// Reads the path a test wrote into `COPERCEIVE_MOCK_DIALOG_FILE` and removes
/// the file, so one answer can only be consumed once.
///
/// Returns `None` outside test mode, when the variable is unset, or when the
/// file is missing or blank; the caller then shows the normal native dialog.
pub fn take_mock_dialog_path() -> Option<String> {
    if !test_env_active() {
        return None;
    }
    let mock_file = std::env::var("COPERCEIVE_MOCK_DIALOG_FILE").ok()?;
    take_dialog_path_from_file(Path::new(&mock_file))
}

/// One-shot dialog protocol: a test writes a path into a file, the app reads it
/// and deletes the file. A blank or missing file changes nothing.
pub fn take_dialog_path_from_file(path: &Path) -> Option<String> {
    let content = std::fs::read_to_string(path).ok()?;
    let answer = content.trim();
    if answer.is_empty() {
        return None;
    }
    let _ = std::fs::remove_file(path);
    Some(answer.to_string())
}

/// True when the harness asked for the deterministic local dataset job
/// (`COPERCEIVE_TEST_FAKE_DATASET_JOB=1`, test mode only).
pub fn fake_dataset_job_enabled() -> bool {
    test_env_active()
        && std::env::var("COPERCEIVE_TEST_FAKE_DATASET_JOB")
            .ok()
            .as_deref()
            == Some("1")
}

/// Shell script for the fake dataset job.
///
/// It writes the child pid for the harness when
/// `COPERCEIVE_TEST_FAKE_DATASET_PID_FILE` is set, then emits the same NDJSON
/// event shape as `coperceive.cli dataset download`: one `stage` plus one `log`
/// line every 5 s. The long cadence keeps the e2e's transient "Download
/// resumed." note on screen after a resume instead of being replaced by the
/// next progress event within a few milliseconds. It never reads or writes any
/// dataset path.
pub const FAKE_DATASET_JOB_SCRIPT: &str = r#"
if [ -n "${COPERCEIVE_TEST_FAKE_DATASET_PID_FILE:-}" ]; then
    printf '%s\n' "$$" > "$COPERCEIVE_TEST_FAKE_DATASET_PID_FILE"
fi
i=0
while [ "$i" -lt 94 ]; do
    i=$((i + 1))
    percent=$((i + 5))
    printf '{"event":"stage","stage":"download","percent":%d,"message":"Test download in progress (%d%%)."}\n' "$percent" "$percent"
    printf '{"event":"log","line":"test download tick %d"}\n' "$i"
    sleep 5
done
"#;

/// Test-only request for the packaged-app HPC connection check.
///
/// `COPERCEIVE_TEST_HPC_SERVER` points at a committed `ServerEntry` JSON fixture
/// and `COPERCEIVE_TEST_HPC_RESULT` at the file the report is written to. Only
/// read inside test mode; production launches never set these.
pub fn hpc_probe_request() -> Option<(std::path::PathBuf, std::path::PathBuf)> {
    if !test_env_active() {
        return None;
    }
    let server = std::env::var("COPERCEIVE_TEST_HPC_SERVER").ok()?;
    let result = std::env::var("COPERCEIVE_TEST_HPC_RESULT").ok()?;
    if server.trim().is_empty() || result.trim().is_empty() {
        return None;
    }
    Some((
        std::path::PathBuf::from(server),
        std::path::PathBuf::from(result),
    ))
}

/// Test-only request for the packaged-app "New evaluation" plan check.
///
/// `COPERCEIVE_TEST_HPC_PLAN_REQUEST` points at a JSON document
/// `{"server": <ServerEntry fixture>, "request": <RunPlanRequest>, "save": bool}`
/// and `COPERCEIVE_TEST_HPC_PLAN_RESULT` at the file the result is written to.
/// Only read inside test mode; production launches never set these.
pub fn run_plan_probe_request() -> Option<(std::path::PathBuf, std::path::PathBuf)> {
    if !test_env_active() {
        return None;
    }
    let request = std::env::var("COPERCEIVE_TEST_HPC_PLAN_REQUEST").ok()?;
    let result = std::env::var("COPERCEIVE_TEST_HPC_PLAN_RESULT").ok()?;
    if request.trim().is_empty() || result.trim().is_empty() {
        return None;
    }
    Some((
        std::path::PathBuf::from(request),
        std::path::PathBuf::from(result),
    ))
}

/// Test-only request for the packaged-app submission step: read a payload
/// `{"server": <ServerEntry fixture path>, "plan_id": ..., "options": SubmitOptions,
/// "confirmed": bool}` and write the preview/submit result for the HPC e2e.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HpcSubmitProbe {
    pub request: std::path::PathBuf,
    pub result: std::path::PathBuf,
}

/// `COPERCEIVE_TEST_HPC_SUBMIT_REQUEST` + `_RESULT`, read only in test mode.
pub fn hpc_submit_probe_request() -> Option<HpcSubmitProbe> {
    if !test_env_active() {
        return None;
    }
    let request = std::env::var("COPERCEIVE_TEST_HPC_SUBMIT_REQUEST").ok()?;
    let result = std::env::var("COPERCEIVE_TEST_HPC_SUBMIT_RESULT").ok()?;
    if request.trim().is_empty() || result.trim().is_empty() {
        return None;
    }
    Some(HpcSubmitProbe {
        request: std::path::PathBuf::from(request),
        result: std::path::PathBuf::from(result),
    })
}

/// Test-only request for the packaged-app watch step: read
/// `{"server": <ServerEntry fixture>, "plan_id", "job_id", "action":
/// "status"|"logs"|"cancel", "stream", "lines", "confirmed"}` and write the
/// real status/log/cancel result for the HPC e2e.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HpcWatchProbe {
    pub request: std::path::PathBuf,
    pub result: std::path::PathBuf,
}

/// `COPERCEIVE_TEST_HPC_WATCH_REQUEST` + `_RESULT`, read only in test mode.
pub fn hpc_watch_probe_request() -> Option<HpcWatchProbe> {
    if !test_env_active() {
        return None;
    }
    let request = std::env::var("COPERCEIVE_TEST_HPC_WATCH_REQUEST").ok()?;
    let result = std::env::var("COPERCEIVE_TEST_HPC_WATCH_RESULT").ok()?;
    if request.trim().is_empty() || result.trim().is_empty() {
        return None;
    }
    Some(HpcWatchProbe {
        request: std::path::PathBuf::from(request),
        result: std::path::PathBuf::from(result),
    })
}

/// Test-only server save/reload/edit round trip for the HPC e2e.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HpcServerSaveProbe {
    pub server: std::path::PathBuf,
    pub result: std::path::PathBuf,
}

/// `COPERCEIVE_TEST_HPC_SERVER` (a committed ServerEntry fixture) plus
/// `COPERCEIVE_TEST_HPC_SAVE_RESULT`, read only in test mode.
pub fn hpc_server_save_probe_request() -> Option<HpcServerSaveProbe> {
    if !test_env_active() {
        return None;
    }
    let server = std::env::var("COPERCEIVE_TEST_HPC_SERVER").ok()?;
    let result = std::env::var("COPERCEIVE_TEST_HPC_SAVE_RESULT").ok()?;
    if server.trim().is_empty() || result.trim().is_empty() {
        return None;
    }
    Some(HpcServerSaveProbe {
        server: std::path::PathBuf::from(server),
        result: std::path::PathBuf::from(result),
    })
}

/// Test-only request for the packaged-app Browse step: run one real
/// `coperceive remote scan` against the committed fake-ssh profile and write the
/// parsed result for `scripts/desktop/e2e_hpc_fake_ssh.sh`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HpcScanProbe {
    pub profile: std::path::PathBuf,
    pub result: std::path::PathBuf,
    pub root: String,
    pub path: String,
}

/// `COPERCEIVE_TEST_HPC_SCAN_PROFILE` + `_RESULT` + `_ROOT` + `_PATH`, read only
/// in test mode. Production launches never set these.
pub fn hpc_scan_probe_request() -> Option<HpcScanProbe> {
    if !test_env_active() {
        return None;
    }
    let profile = std::env::var("COPERCEIVE_TEST_HPC_SCAN_PROFILE").ok()?;
    let result = std::env::var("COPERCEIVE_TEST_HPC_SCAN_RESULT").ok()?;
    let root = std::env::var("COPERCEIVE_TEST_HPC_SCAN_ROOT").ok()?;
    let path = std::env::var("COPERCEIVE_TEST_HPC_SCAN_PATH").ok()?;
    if [&profile, &result, &root, &path]
        .iter()
        .any(|value| value.trim().is_empty())
    {
        return None;
    }
    Some(HpcScanProbe {
        profile: std::path::PathBuf::from(profile),
        result: std::path::PathBuf::from(result),
        root,
        path,
    })
}

/// Canned dry-run answer used only in fake job mode. Mirrors the JSON shape of
/// `coperceive.catalog.runner.dry_run_preset` so the Get data UI renders the
/// size panel and enables "Start Download" without contacting the dataset host.
pub fn fake_dry_run_result(dataset_id: &str, preset_id: &str) -> serde_json::Value {
    const MIB: u64 = 1024 * 1024;
    serde_json::json!({
        "event": "dry_run",
        "dataset_id": dataset_id,
        "preset_id": preset_id,
        "download_bytes": 256 * MIB,
        "disk_bytes": 512 * MIB,
        "free_bytes": 2 * 1024 * MIB,
        "total_bytes": 4 * 1024 * MIB,
        "has_headroom": true,
        "requires_terms": true,
        "licence": "MIT",
        "licence_url": "https://raw.githubusercontent.com/wang-jh18-SVM/Griffin/main/LICENSE.txt",
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn temp_dir(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "coperceive-test-seam-{}-{}",
            tag,
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).expect("create temp dir");
        dir
    }

    #[test]
    fn take_dialog_path_reads_trims_and_removes_the_file() {
        let dir = temp_dir("dialog");
        let file = dir.join("dialog.txt");
        std::fs::write(&file, "  /tmp/e2e-library\n").expect("write answer");

        assert_eq!(
            take_dialog_path_from_file(&file),
            Some("/tmp/e2e-library".to_string())
        );
        assert!(!file.exists(), "the answer file must be consumed once");
        assert_eq!(
            take_dialog_path_from_file(&file),
            None,
            "a consumed answer must not be replayed"
        );

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn take_dialog_path_keeps_spaces_and_brackets_in_the_answer() {
        let dir = temp_dir("dialog-spaces");
        let file = dir.join("dialog with spaces.txt");
        let answer = "/tmp/Coperceive E2E Library (a)";
        std::fs::write(&file, format!("{answer}\n")).expect("write answer");

        assert_eq!(take_dialog_path_from_file(&file), Some(answer.to_string()));
        assert!(!file.exists(), "the answer file must be consumed once");

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn take_dialog_path_ignores_a_blank_or_missing_answer() {
        let dir = temp_dir("dialog-blank");
        let blank = dir.join("blank.txt");
        std::fs::write(&blank, "   \n").expect("write blank answer");

        assert_eq!(take_dialog_path_from_file(&blank), None);
        assert!(
            blank.exists(),
            "a blank answer must keep the file for a retry"
        );
        assert_eq!(take_dialog_path_from_file(&dir.join("missing.txt")), None);

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn fake_dry_run_result_fills_the_ui_contract() {
        let value = fake_dry_run_result("griffin", "dev");
        assert_eq!(value["event"], "dry_run");
        assert_eq!(value["dataset_id"], "griffin");
        assert_eq!(value["preset_id"], "dev");
        assert!(value["download_bytes"].as_u64().unwrap() > 0);
        assert!(value["disk_bytes"].as_u64().unwrap() > 0);
        assert!(value["free_bytes"].as_u64().unwrap() > value["disk_bytes"].as_u64().unwrap());
        assert_eq!(value["has_headroom"], true);
        assert_eq!(value["requires_terms"], true);
    }

    #[test]
    fn fake_dataset_job_script_streams_ndjson_and_reports_its_pid() {
        assert!(FAKE_DATASET_JOB_SCRIPT.contains("\"event\":\"stage\""));
        assert!(FAKE_DATASET_JOB_SCRIPT.contains("\"percent\""));
        assert!(FAKE_DATASET_JOB_SCRIPT.contains("\"event\":\"log\""));
        assert!(FAKE_DATASET_JOB_SCRIPT.contains("COPERCEIVE_TEST_FAKE_DATASET_PID_FILE"));
        assert!(FAKE_DATASET_JOB_SCRIPT.contains("sleep 5"));
        // The fake job must never touch a dataset name or a real path.
        for forbidden in ["griffin", "dataset", "/Users/", "/Volumes/"] {
            assert!(
                !FAKE_DATASET_JOB_SCRIPT.contains(forbidden),
                "fake job script must not mention {forbidden}"
            );
        }
    }
    #[test]
    fn hpc_probe_request_is_inert_outside_test_mode() {
        // The parent test process does not set test mode, so the seam is inert.
        assert!(hpc_probe_request().is_none());
    }
}
