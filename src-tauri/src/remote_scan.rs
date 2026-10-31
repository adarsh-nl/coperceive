// SPDX-License-Identifier: Apache-2.0
//! `coperceive remote scan` over the user's SSH connection.
//!
//! The scan is metadata only and runs on the cluster's login node, so the app
//! keeps it light: the Python command bounds the depth, the entries per folder,
//! the number of folders and the wall-clock time, and the UI shows when a bound
//! stopped it. This module only builds the argv, runs it over the same reused
//! SSH connection as the rest of the HPC workspace, and parses the JSON.
//!
//! Every value the user typed travels as its own argv element locally and is
//! POSIX-quoted for the one remote shell line SSH needs; nothing is ever
//! assembled into a local shell string.

use std::time::Duration;

use serde::{Deserialize, Serialize};

use crate::hpc::ServerProfile;
use crate::ssh::{classify_ssh_error, quote_remote_argv, run_ssh_command, SshTarget};

/// Limits mirrored from `coperceive.remote.scan`; kept in sync by
/// `tests/test_remote_scan.py` on the Python side and by the argv test here.
pub const DEFAULT_DEPTH: u32 = 2;
pub const MAX_DEPTH: u32 = 4;
pub const DEFAULT_TIMEOUT_S: f64 = 10.0;
pub const MAX_TIMEOUT_S: f64 = 30.0;
pub const DEFAULT_MAX_ENTRIES: u32 = 200;
pub const MAX_MAX_ENTRIES: u32 = 1000;
pub const DEFAULT_MAX_TOTAL: u32 = 2000;
pub const MAX_MAX_TOTAL: u32 = 10_000;

/// Slack on top of the scan's own budget for SSH setup, login shell and JSON.
const SSH_OVERHEAD: Duration = Duration::from_secs(8);

/// One folder described by the scan; paths are relative to `root`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct FolderInfo {
    #[serde(default)]
    pub path: String,
    #[serde(default)]
    pub depth: u32,
    #[serde(default)]
    pub is_scene: bool,
    #[serde(default)]
    pub scene_id: Option<String>,
    #[serde(default)]
    pub dataset: bool,
    #[serde(default)]
    pub scene_count: u32,
    #[serde(default)]
    pub has_manifest: bool,
    #[serde(default)]
    pub has_complete: bool,
    #[serde(default)]
    pub subfolder_count: u32,
    #[serde(default)]
    pub file_count: u32,
}

/// One detected coperceive scene (a folder with `manifest.json` and `COMPLETE.json`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct SceneInfo {
    #[serde(default)]
    pub path: String,
    #[serde(default)]
    pub scene_id: String,
    #[serde(default)]
    pub folder_name: String,
}

/// A non-scene folder with at least one scene somewhere below it.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct DatasetInfo {
    #[serde(default)]
    pub path: String,
    #[serde(default)]
    pub scene_count: u32,
}

/// A directory entry the scan did not follow, and why (plain words from Python).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct SkippedInfo {
    #[serde(default)]
    pub path: String,
    #[serde(default)]
    pub reason: String,
}

/// The JSON document printed by `coperceive remote scan`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct ScanDocument {
    #[serde(default)]
    pub status: String,
    #[serde(default)]
    pub root: String,
    #[serde(default)]
    pub path: String,
    #[serde(default)]
    pub relative_path: String,
    #[serde(default)]
    pub depth: u32,
    #[serde(default)]
    pub timeout_s: f64,
    #[serde(default)]
    pub max_entries: u32,
    #[serde(default)]
    pub max_total: u32,
    #[serde(default)]
    pub elapsed_ms: f64,
    #[serde(default)]
    pub truncated: bool,
    #[serde(default)]
    pub truncated_reason: Option<String>,
    #[serde(default)]
    pub folder_count: u32,
    #[serde(default)]
    pub scene_count: u32,
    #[serde(default)]
    pub dataset_count: u32,
    #[serde(default)]
    pub folders: Vec<FolderInfo>,
    #[serde(default)]
    pub scenes: Vec<SceneInfo>,
    #[serde(default)]
    pub datasets: Vec<DatasetInfo>,
    #[serde(default)]
    pub skipped: Vec<SkippedInfo>,
}

/// The error reply `coperceive remote scan` prints for a refused request.
#[derive(Debug, Clone, Deserialize, Default)]
struct ScanErrorReply {
    #[serde(default)]
    status: String,
    #[serde(default)]
    error: Option<String>,
}

/// The bounds of one scan, clamped to what the login-node command accepts.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct ScanLimits {
    pub depth: u32,
    pub timeout_s: f64,
    pub max_entries: u32,
    pub max_total: u32,
}

impl Default for ScanLimits {
    fn default() -> Self {
        Self {
            depth: DEFAULT_DEPTH,
            timeout_s: DEFAULT_TIMEOUT_S,
            max_entries: DEFAULT_MAX_ENTRIES,
            max_total: DEFAULT_MAX_TOTAL,
        }
    }
}

impl ScanLimits {
    /// Clamps the UI's optional values into the range the Python command allows.
    ///
    /// Clamping instead of failing keeps a scan light on the login node even if
    /// a stale UI or a hand-edited value asks for more.
    pub fn resolve(
        depth: Option<u32>,
        timeout_s: Option<f64>,
        max_entries: Option<u32>,
        max_total: Option<u32>,
    ) -> Self {
        let mut limits = Self::default();
        if let Some(depth) = depth {
            limits.depth = depth.min(MAX_DEPTH);
        }
        if let Some(timeout_s) = timeout_s {
            if timeout_s.is_finite() && timeout_s > 0.0 {
                limits.timeout_s = timeout_s.min(MAX_TIMEOUT_S);
            }
        }
        if let Some(max_entries) = max_entries {
            limits.max_entries = max_entries.clamp(1, MAX_MAX_ENTRIES);
        }
        if let Some(max_total) = max_total {
            limits.max_total = max_total.clamp(1, MAX_MAX_TOTAL);
        }
        limits
    }

    /// The whole local budget for one scan: the command's own wall clock plus SSH.
    pub fn ssh_timeout(&self) -> Duration {
        Duration::from_secs_f64(self.timeout_s) + SSH_OVERHEAD
    }
}

fn normalized(path: &str) -> &str {
    path.trim().trim_end_matches('/')
}

/// Refuses a scan target that is not one of the server's chosen data folders,
/// or a relative path that could climb out of it.
///
/// The Python command enforces the same confinement with real paths and
/// symlinks; this is the cheap local check so a typo never leaves the app.
pub fn validate_target(profile: &ServerProfile, root: &str, path: &str) -> Result<(), String> {
    let root = normalized(root);
    if root.is_empty() {
        return Err("Choose a data folder on this server to browse.".to_string());
    }
    if root.contains('\n') || root.contains('\r') {
        return Err("The data folder contains a line break, which is not allowed.".to_string());
    }
    let inside_profile = profile
        .data_roots
        .iter()
        .any(|chosen| normalized(chosen) == root);
    if !inside_profile {
        return Err(format!(
            "{root:?} is not one of this server's data folders. Choose a data folder you added \
             to the server first."
        ));
    }

    let path = path.trim();
    if path.contains('\n') || path.contains('\r') {
        return Err("The folder name contains a line break, which is not allowed.".to_string());
    }
    if path.starts_with('/') || path.starts_with('~') {
        return Err(
            "Browse inside the data folder instead of typing an absolute path. The scan only \
             looks inside the folder you chose."
                .to_string(),
        );
    }
    if path.split('/').any(|part| part == "..") {
        return Err(format!("{path:?} contains '..' and could leave the data folder."));
    }
    Ok(())
}

/// Builds the full `coperceive remote scan ...` argv (program name first).
pub fn scan_argv(
    command: &[String],
    root: &str,
    path: &str,
    limits: ScanLimits,
) -> Vec<String> {
    let path = {
        let trimmed = path.trim();
        if trimmed.is_empty() {
            ".".to_string()
        } else {
            trimmed.to_string()
        }
    };
    let mut argv = command.to_vec();
    if argv.is_empty() {
        argv.push("coperceive".to_string());
    }
    argv.extend([
        "remote".to_string(),
        "scan".to_string(),
        "--root".to_string(),
        root.trim().to_string(),
        "--path".to_string(),
        path,
        "--depth".to_string(),
        limits.depth.to_string(),
        "--timeout".to_string(),
        format!("{}", limits.timeout_s),
        "--max-entries".to_string(),
        limits.max_entries.to_string(),
        "--max-total".to_string(),
        limits.max_total.to_string(),
    ]);
    argv
}

/// The one remote shell line SSH runs, every argument POSIX-quoted.
pub fn scan_command_line(
    command: &[String],
    root: &str,
    path: &str,
    limits: ScanLimits,
) -> String {
    quote_remote_argv(&scan_argv(command, root, path, limits))
}

/// Parses the command's stdout without ever replaying a large or secret reply.
///
/// A refused request (`{"status":"error",...}`) is the scan's own plain
/// explanation, for example "the path is a symlink that points outside the data
/// root"; it is passed through unchanged so the UI can show it.
pub fn parse_scan_output(
    stdout: &str,
    success: bool,
    stderr: &str,
) -> Result<ScanDocument, String> {
    let trimmed = stdout.trim();
    if trimmed.is_empty() {
        if success {
            return Err(
                "The cluster did not return a scan result. Check that coperceive is installed on \
                 the server, then try again."
                    .to_string(),
            );
        }
        return Err(classify_ssh_error(stderr).to_string());
    }

    if let Ok(reply) = serde_json::from_str::<ScanErrorReply>(trimmed) {
        if reply.status == "error" {
            let message = reply
                .error
                .filter(|text| !text.trim().is_empty())
                .unwrap_or_else(|| "The server refused to scan that folder.".to_string());
            return Err(message);
        }
    }

    match serde_json::from_str::<ScanDocument>(trimmed) {
        Ok(document) if document.status == "ok" => Ok(document),
        Ok(document) => Err(format!(
            "The server sent an unexpected scan reply (status {:?}). Check that coperceive is \
             installed and up to date on the server, then try again.",
            document.status
        )),
        Err(_) => Err(format!(
            "The server sent an unexpected reply to the scan request ({} bytes, not valid JSON). \
             Check that coperceive is installed and up to date on the server, then try again.",
            trimmed.len()
        )),
    }
}

/// Runs one scan over `ssh_bin` and returns the parsed document or a
/// plain-language error. The local timeout always bounds the whole interaction.
pub fn run_scan(
    ssh_bin: &str,
    target: &SshTarget,
    command: &[String],
    root: &str,
    path: &str,
    limits: ScanLimits,
) -> Result<ScanDocument, String> {
    let remote_cmd = scan_command_line(command, root, path, limits);
    let result = run_ssh_command(ssh_bin, target, &remote_cmd, limits.ssh_timeout(), None)
        .map_err(|error| {
            if error.kind() == std::io::ErrorKind::TimedOut {
                format!(
                    "The cluster did not finish the folder scan in time ({} s). Try a smaller \
                     folder or raise the time limit, then try again.",
                    limits.timeout_s
                )
            } else {
                format!("The scan could not start because SSH failed locally: {error}.")
            }
        })?;
    parse_scan_output(&result.stdout, result.success, &result.stderr)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::os::unix::fs::PermissionsExt;
    use std::path::{Path, PathBuf};
    use std::process::Command;

    use crate::hpc::{EnvironmentSetup, JobDefaults, SchedulerKind};

    fn profile(data_roots: &[&str]) -> ServerProfile {
        ServerProfile {
            name: "Cluster A".to_string(),
            ssh_alias: Some("cluster-a".to_string()),
            host: None,
            user: None,
            data_roots: data_roots.iter().map(|root| root.to_string()).collect(),
            scratch_template: "/scratch/$USER".to_string(),
            results_root: "/data/projects/runs".to_string(),
            code_roots: Vec::new(),
            scheduler: SchedulerKind::Slurm,
            defaults: JobDefaults::default(),
            environment: EnvironmentSetup::default(),
            notes: None,
        }
    }

    fn temp_dir(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "coperceive-scan-{}-{}",
            tag,
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).expect("temp dir");
        dir
    }

    fn fake_ssh(dir: &Path, script: &str) -> String {
        let bin = dir.join("ssh");
        fs::write(&bin, script).expect("write fake ssh");
        let mut perms = fs::metadata(&bin).expect("metadata").permissions();
        perms.set_mode(0o755);
        fs::set_permissions(&bin, perms).expect("chmod fake ssh");
        bin.display().to_string()
    }

    const FOLDER_JSON: &str = r#"{
        "status": "ok",
        "root": "/data/scenes",
        "path": "/data/scenes",
        "relative_path": ".",
        "depth": 2,
        "timeout_s": 10.0,
        "max_entries": 200,
        "max_total": 2000,
        "elapsed_ms": 12.5,
        "truncated": true,
        "truncated_reason": "entry limit",
        "folder_count": 2,
        "scene_count": 1,
        "dataset_count": 1,
        "folders": [
            {"path": ".", "depth": 0, "is_scene": false, "scene_id": null, "dataset": true,
             "scene_count": 1, "has_manifest": false, "has_complete": false,
             "subfolder_count": 2, "file_count": 0},
            {"path": "Town03-000", "depth": 1, "is_scene": true, "scene_id": "Town03-000",
             "dataset": false, "scene_count": 0, "has_manifest": true, "has_complete": true,
             "subfolder_count": 0, "file_count": 7}
        ],
        "scenes": [
            {"path": "Town03-000", "scene_id": "Town03-000", "folder_name": "Town03-000"}
        ],
        "datasets": [{"path": ".", "scene_count": 1}],
        "skipped": [{"path": "escape-link", "reason": "symlink target is outside the data root"}]
    }"#;

    #[test]
    fn test_scan_argv_is_argv_only_and_keeps_each_value_whole() {
        let root = "/data/my scenes; touch /tmp/x";
        let limits = ScanLimits::default();
        let argv = scan_argv(&["coperceive".to_string()], root, "sub folder", limits);
        assert_eq!(
            argv,
            vec![
                "coperceive",
                "remote",
                "scan",
                "--root",
                "/data/my scenes; touch /tmp/x",
                "--path",
                "sub folder",
                "--depth",
                "2",
                "--timeout",
                "10",
                "--max-entries",
                "200",
                "--max-total",
                "2000",
            ]
            .into_iter()
            .map(str::to_string)
            .collect::<Vec<String>>()
        );
        let line = scan_command_line(&["coperceive".to_string()], root, "sub folder", limits);
        assert!(
            line.contains("'/data/my scenes; touch /tmp/x'"),
            "the root must be one quoted argument: {line}"
        );
    }

    #[test]
    fn test_scan_command_line_uses_the_saved_coperceive_argv() {
        let limits = ScanLimits::default();
        let command = vec![
            "/home/u/venv/bin/python".to_string(),
            "-m".to_string(),
            "coperceive".to_string(),
        ];
        let line = scan_command_line(&command, "/data/scenes", "", limits);
        assert!(line.starts_with("/home/u/venv/bin/python -m coperceive remote scan"), "{line}");
    }

    #[test]
    fn test_scan_command_line_survives_spaces_quotes_and_semicolons() {
        let dir = temp_dir("injection");
        let bin_dir = dir.join("bin");
        fs::create_dir_all(&bin_dir).expect("bin dir");
        let fake = bin_dir.join("coperceive");
        fs::write(
            &fake,
            "#!/bin/bash\nprintf '%s\\n' \"$@\" > \"$FAKE_COPER_LOG\"\n",
        )
        .expect("write fake coperceive");
        let mut perms = fs::metadata(&fake).expect("metadata").permissions();
        perms.set_mode(0o755);
        fs::set_permissions(&fake, perms).expect("chmod");

        let marker = dir.join("pwned");
        let log = dir.join("argv.log");
        let malicious_root = format!(
            "/data/my scenes/\"quoted\"; touch /tmp/x; touch {}",
            marker.display()
        );
        // A value that would be a scan target if validation were skipped.
        let limits = ScanLimits::default();
        let line = scan_command_line(&["coperceive".to_string()], &malicious_root, "it's", limits);
        assert_eq!(
            line,
            format!(
                "coperceive remote scan --root '{}' --path 'it'\\''s' --depth 2 --timeout 10 \
                 --max-entries 200 --max-total 2000",
                malicious_root
            )
        );

        let path_env = format!(
            "{}:{}",
            bin_dir.display(),
            std::env::var("PATH").unwrap_or_default()
        );
        let status = Command::new("sh")
            .arg("-c")
            .arg(&line)
            .env("PATH", path_env)
            .env("FAKE_COPER_LOG", &log)
            .status()
            .expect("run scan line locally");
        assert!(status.success());
        assert!(
            !marker.exists(),
            "shell injection executed: the malicious root created {}",
            marker.display()
        );
        let received = fs::read_to_string(&log).expect("argv log");
        assert_eq!(
            received.lines().collect::<Vec<&str>>(),
            vec![
                "remote",
                "scan",
                "--root",
                malicious_root.as_str(),
                "--path",
                "it's",
                "--depth",
                "2",
                "--timeout",
                "10",
                "--max-entries",
                "200",
                "--max-total",
                "2000",
            ]
        );
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_parse_scan_output_reads_listings_truncation_and_skips() {
        let document = parse_scan_output(FOLDER_JSON, true, "").expect("scan parses");
        assert_eq!(document.status, "ok");
        assert_eq!(document.folder_count, 2);
        assert_eq!(document.scene_count, 1);
        assert_eq!(document.dataset_count, 1);
        assert!(document.truncated);
        assert_eq!(document.truncated_reason.as_deref(), Some("entry limit"));
        assert_eq!(document.scenes[0].scene_id, "Town03-000");
        assert_eq!(document.datasets[0].path, ".");
        assert_eq!(document.skipped[0].path, "escape-link");
        assert!(document.skipped[0].reason.contains("outside the data root"));
        assert!(document.folders[1].is_scene);
    }

    #[test]
    fn test_parse_scan_output_passes_the_path_refusal_through() {
        let refusal = r#"{"status": "error", "error": "the path is a symlink that points outside the data root: escape-link -> /etc"}"#;
        let error = parse_scan_output(refusal, false, "").expect_err("refusal is an error");
        assert!(error.contains("symlink that points outside the data root"), "{error}");
        assert!(error.contains("escape-link"), "{error}");
    }

    #[test]
    fn test_parse_scan_output_maps_ssh_failures_and_bad_json_to_plain_words() {
        let denied = parse_scan_output("", false, "Permission denied (publickey).")
            .expect_err("auth failure is an error");
        assert!(denied.contains("ssh-add"), "{denied}");

        let broken = parse_scan_output("not json at all", false, "")
            .expect_err("invalid JSON is an error");
        assert!(broken.contains("not valid JSON"), "{broken}");

        let empty_ok = parse_scan_output("", true, "").expect_err("empty reply is an error");
        assert!(empty_ok.contains("did not return a scan result"), "{empty_ok}");
    }

    #[test]
    fn test_validate_target_confines_the_scan_to_chosen_data_roots() {
        let profile = profile(&["/data/scenes", "/data/more/"]);

        assert!(validate_target(&profile, "/data/scenes", ".").is_ok());
        assert!(validate_target(&profile, "/data/more/", "sub").is_ok());
        assert!(validate_target(&profile, "/data/more", ".").is_ok());

        let outside = validate_target(&profile, "/etc", ".").expect_err("outside root refused");
        assert!(outside.contains("not one of this server's data folders"), "{outside}");

        let escape = validate_target(&profile, "/data/scenes", "../escape").expect_err(".. refused");
        assert!(escape.contains("'..'"), "{escape}");

        let absolute =
            validate_target(&profile, "/data/scenes", "/etc/passwd").expect_err("absolute refused");
        assert!(absolute.contains("inside the data folder"), "{absolute}");

        let empty = validate_target(&profile, "", ".").expect_err("empty root refused");
        assert!(empty.contains("Choose a data folder"), "{empty}");
    }

    #[test]
    fn test_resolve_limits_clamps_every_bound() {
        let requested = ScanLimits::resolve(Some(99), Some(3600.0), Some(0), Some(99_999));
        assert_eq!(requested.depth, MAX_DEPTH);
        assert_eq!(requested.timeout_s, MAX_TIMEOUT_S);
        assert_eq!(requested.max_entries, 1);
        assert_eq!(requested.max_total, MAX_MAX_TOTAL);

        let defaults = ScanLimits::resolve(None, None, None, None);
        assert_eq!(defaults, ScanLimits::default());
        assert_eq!(defaults.depth, DEFAULT_DEPTH);
        assert_eq!(defaults.timeout_s, DEFAULT_TIMEOUT_S);

        let bad_timeout = ScanLimits::resolve(None, Some(-3.0), None, None);
        assert_eq!(bad_timeout.timeout_s, DEFAULT_TIMEOUT_S);
        let infinite = ScanLimits::resolve(None, Some(f64::INFINITY), None, None);
        assert_eq!(infinite.timeout_s, DEFAULT_TIMEOUT_S);
    }

    #[test]
    fn test_run_scan_uses_fake_ssh_and_parses_the_listing() {
        let dir = temp_dir("run");
        let root = "/data/scenes";
        let limits = ScanLimits::default();
        let script = format!(
            "#!/bin/bash\nexec cat <<'JSON'\n{}\nJSON\n",
            FOLDER_JSON.trim()
        );
        let ssh = fake_ssh(&dir, &script);
        let target = SshTarget::alias("cluster-a");
        let document = run_scan(&ssh, &target, &["coperceive".to_string()], root, ".", limits).expect("fake scan works");
        assert_eq!(document.scene_count, 1);
        assert!(document.truncated);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_run_scan_reports_a_refused_path_from_the_server() {
        let dir = temp_dir("refused");
        let refusal = r#"{"status": "error", "error": "the path is outside the data root: /etc"}"#;
        let ssh = fake_ssh(&dir, &format!("#!/bin/bash\nprintf '%s' '{}'\nexit 1\n", refusal));
        let target = SshTarget::alias("cluster-a");
        let error = run_scan(&ssh, &target, &["coperceive".to_string()], "/data/scenes", "escape", ScanLimits::default())
            .expect_err("refusal is an error");
        assert!(error.contains("outside the data root"), "{error}");
        let _ = fs::remove_dir_all(&dir);
    }
}
