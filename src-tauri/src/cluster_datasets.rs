// SPDX-License-Identifier: Apache-2.0
//! Remote dataset detect/index commands.
//!
//! Both are read-only and metadata-only: they run the saved coperceive argv on
//! the login node (`coperceive dataset detect|index`) and return its JSON. No
//! scene data is read and nothing is written.
//!
//! When one of them fails, [`explain_failure`] asks the server's coperceive
//! what it supports (`coperceive capabilities`), so an older build gets "too old,
//! update it with …" instead of raw argparse output.

use std::time::Duration;

use serde_json::Value;

use crate::hpc::{coperceive_argv, ServerProfile};
use crate::slurm_adapter::RemoteRunner;
use crate::ssh::{quote_remote_argv, shell_quote};

pub const DETECT_SCHEMA: &str = "coperceive-cluster-detect-1";
pub const INDEX_SCHEMA: &str = "coperceive-cluster-index-1";
pub const PREVIEWS_SCHEMA: &str = "coperceive-cluster-previews-1";
const DETECT_TIMEOUT: Duration = Duration::from_secs(45);
const INDEX_TIMEOUT: Duration = Duration::from_secs(120);
pub const CAPABILITIES_SCHEMA: &str = "coperceive-capabilities-1";
/// What the dataset commands need from the server's coperceive (see `coperceive capabilities`).
pub const CLUSTER_DATASETS_CAPABILITY: &str = "cluster-datasets-5";
const PROBE_TIMEOUT: Duration = Duration::from_secs(20);
/// Where an update comes from when the install is not a git checkout.
/// Switch to `@main` once cluster datasets are released there.
const COPERCEIVE_SOURCE: &str = "git+https://github.com/adarsh-nl/coperceive@feat/m2-import";
const MISSING_MARKER: &str = "coperceive-capabilities-missing";

/// What the server's coperceive says it supports.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RemoteSupport {
    Supported { version: String },
    TooOld { found: String, update: String },
    /// The probe itself did not answer (ssh trouble, no coperceive at all).
    Unknown,
}

impl RemoteSupport {
    /// The user-facing sentence for a build that is too old, if it is.
    pub fn too_old_message(&self) -> Option<String> {
        match self {
            RemoteSupport::TooOld { found, update } => Some(format!(
                "coperceive on this server is too old for cluster datasets (found {found}, need \
                 a build with {CLUSTER_DATASETS_CAPABILITY}). Update it with: {update}"
            )),
            _ => None,
        }
    }
}

/// One fixed read-only script: `capabilities`, or for an older build its
/// `--version` line and, for `<python> -m coperceive`, where it is installed.
pub fn capability_probe_command(profile: &ServerProfile) -> String {
    let argv = coperceive_argv(profile, &[]);
    let quoted = argv.iter().map(|part| shell_quote(part)).collect::<Vec<_>>().join(" ");
    format!(
        r#"sh -c '# coperceive-check:capabilities
"$@" capabilities 2>/dev/null && exit 0
echo {MISSING_MARKER}
"$@" --version 2>&1 | head -n 1
if [ "$2" = "-m" ]; then
  "$1" -c "import coperceive, os; d = os.path.dirname(os.path.dirname(os.path.abspath(coperceive.__file__))); print(\"LOCATION=\" + d); print(\"GIT=1\" if os.path.isdir(os.path.join(d, \".git\")) else \"GIT=0\")" 2>/dev/null
fi
exit 0' sh {quoted}"#
    )
}

/// Read the probe's output. Pure, so the wording is unit-tested.
pub fn parse_capability_probe(profile: &ServerProfile, stdout: &str) -> RemoteSupport {
    let lines: Vec<&str> = stdout.lines().map(str::trim).filter(|l| !l.is_empty()).collect();
    for line in &lines {
        let Ok(document) = serde_json::from_str::<Value>(line) else {
            continue;
        };
        if document.get("schema").and_then(Value::as_str) != Some(CAPABILITIES_SCHEMA) {
            continue;
        }
        let version = document
            .get("version")
            .and_then(Value::as_str)
            .unwrap_or("unknown")
            .to_string();
        let supported = document
            .get("capabilities")
            .and_then(Value::as_array)
            .is_some_and(|list| {
                list.iter().any(|item| item.as_str() == Some(CLUSTER_DATASETS_CAPABILITY))
            });
        if supported {
            return RemoteSupport::Supported { version };
        }
        return RemoteSupport::TooOld {
            found: format!("coperceive {version} without {CLUSTER_DATASETS_CAPABILITY}"),
            update: update_command(profile, None),
        };
    }
    let Some(marker) = lines.iter().position(|line| *line == MISSING_MARKER) else {
        return RemoteSupport::Unknown;
    };
    let rest = &lines[marker + 1..];
    let version_line = rest
        .iter()
        .find(|line| !line.starts_with("LOCATION=") && !line.starts_with("GIT="))
        .copied()
        .unwrap_or("");
    let found = if version_line.starts_with("coperceive ")
        && !version_line.contains("error")
        && version_line.len() <= 80
    {
        format!("{version_line}, which has no capabilities command")
    } else {
        "a build without the capabilities command".to_string()
    };
    let location = rest
        .iter()
        .find_map(|line| line.strip_prefix("LOCATION="))
        .filter(|_| rest.contains(&"GIT=1"));
    RemoteSupport::TooOld {
        found,
        update: update_command(profile, location),
    }
}

/// The exact command to update the server's coperceive, for the message.
fn update_command(profile: &ServerProfile, git_checkout: Option<&str>) -> String {
    let argv = coperceive_argv(profile, &[]);
    let python_m = argv.len() >= 3 && argv[1] == "-m";
    match (git_checkout, python_m) {
        (Some(dir), true) => format!(
            "git -C {dir} pull --ff-only && {python} -m pip install -e {dir} --no-deps",
            dir = shell_quote(dir),
            python = shell_quote(&argv[0]),
        ),
        (_, true) => format!(
            "{python} -m pip install --upgrade {source}",
            python = shell_quote(&argv[0]),
            source = shell_quote(COPERCEIVE_SOURCE),
        ),
        _ => format!(
            "pip install --upgrade {source} (in the environment that provides {command})",
            source = shell_quote(COPERCEIVE_SOURCE),
            command = argv.first().map(String::as_str).unwrap_or("coperceive"),
        ),
    }
}

/// Ask the server's coperceive what it supports.
pub fn probe_support(runner: &dyn RemoteRunner, profile: &ServerProfile) -> RemoteSupport {
    match runner.run(&capability_probe_command(profile), PROBE_TIMEOUT, None) {
        Ok(result) => parse_capability_probe(profile, &result.stdout),
        Err(_) => RemoteSupport::Unknown,
    }
}

/// A failed dataset command's error, replaced by "too old, update with …" when
/// the server's coperceive cannot run cluster datasets at all.
pub fn explain_failure(runner: &dyn RemoteRunner, profile: &ServerProfile, error: String) -> String {
    probe_support(runner, profile).too_old_message().unwrap_or(error)
}

fn run_document(
    runner: &dyn RemoteRunner,
    profile: &ServerProfile,
    args: &[String],
    timeout: Duration,
    expected_schema: &str,
) -> Result<Value, String> {
    run_document_once(runner, profile, args, timeout, expected_schema)
        .map_err(|error| explain_failure(runner, profile, error))
}

fn run_document_once(
    runner: &dyn RemoteRunner,
    profile: &ServerProfile,
    args: &[String],
    timeout: Duration,
    expected_schema: &str,
) -> Result<Value, String> {
    let command = quote_remote_argv(&coperceive_argv(profile, args));
    let result = runner.run(&command, timeout, None)?;
    let stdout = result.stdout.trim();
    if !stdout.is_empty() {
        if let Ok(document) = serde_json::from_str::<Value>(stdout) {
            if document.get("status").and_then(Value::as_str) == Some("error") {
                return Err(document
                    .get("error")
                    .or_else(|| document.get("reason"))
                    .and_then(Value::as_str)
                    .filter(|text| !text.trim().is_empty())
                    .unwrap_or("The cluster refused the dataset request.")
                    .to_string());
            }
            if document.get("schema").and_then(Value::as_str) == Some(expected_schema) {
                return Ok(document);
            }
        }
    }
    if !result.success {
        let detail = if result.stderr.trim().is_empty() {
            "the cluster command failed"
        } else {
            result.stderr.trim()
        };
        return Err(format!("Dataset command failed: {detail}"));
    }
    Err("The cluster returned an unexpected dataset document. Update coperceive on the server and try again.".to_string())
}

/// Identify the built-in adapter and split for a raw path on the server.
pub fn detect(
    runner: &dyn RemoteRunner,
    profile: &ServerProfile,
    path: &str,
) -> Result<Value, String> {
    if path.trim().is_empty() {
        return Err("Choose a raw dataset path on the cluster.".to_string());
    }
    run_document(
        runner,
        profile,
        &[
            "dataset".to_string(),
            "detect".to_string(),
            "--path".to_string(),
            path.trim().to_string(),
        ],
        DETECT_TIMEOUT,
        DETECT_SCHEMA,
    )
}

/// The command line of the read-only streamer for viewing a prepared dataset
/// over SSH (no Slurm job): one long-lived `ssh` to `coperceive dataset stream`,
/// allowed to read the cache split directory and the dataset's raw root. Every
/// remote argument is shell-quoted; file paths later travel only on stdin.
pub fn stream_argv(
    ssh_bin: &str,
    target: &crate::ssh::SshTarget,
    profile: &ServerProfile,
    cache_root: &str,
    raw_root: &str,
) -> Vec<String> {
    let remote = crate::hpc::coperceive_argv(
        profile,
        &[
            "dataset".to_string(),
            "stream".to_string(),
            "--root".to_string(),
            cache_root.to_string(),
            "--root".to_string(),
            raw_root.to_string(),
        ],
    );
    let mut argv = vec![ssh_bin.to_string()];
    argv.extend(crate::ssh::build_ssh_command_for(target, &quote_remote_argv(&remote)));
    argv
}

/// Disk used by camera previews in this profile's cache; with `remove`, delete
/// them (only them). Called with `remove` only after the user confirms.
pub fn previews(
    runner: &dyn RemoteRunner,
    profile: &ServerProfile,
    remove: bool,
) -> Result<Value, String> {
    let results = crate::hpc::normalise_path(&profile.results_root, "results_root")?;
    let mut args = vec![
        "dataset".to_string(),
        "previews".to_string(),
        "--cache-root".to_string(),
        format!("{}/coperceive-cache", results.trim_end_matches('/')),
    ];
    if remove {
        args.push("--remove".to_string());
    }
    run_document(runner, profile, &args, INDEX_TIMEOUT, PREVIEWS_SCHEMA)
}

/// List every scene of one split of a raw dataset. Metadata only; writes
/// nothing. `split` is the one `detect` returned; without it a root holding
/// several splits is refused by the server rather than guessed.
pub fn index(
    runner: &dyn RemoteRunner,
    profile: &ServerProfile,
    adapter: &str,
    raw_root: &str,
    split: Option<&str>,
) -> Result<Value, String> {
    let adapter = adapter.trim();
    if !matches!(adapter, "griffin" | "opv2v" | "v2xset" | "v2v4real" | "dair-v2x-c") {
        return Err(format!("{adapter:?} is not a built-in cluster adapter."));
    }
    if raw_root.trim().is_empty() {
        return Err("Choose the raw dataset root on the cluster.".to_string());
    }
    let mut args = vec![
        "dataset".to_string(),
        "index".to_string(),
        "--adapter".to_string(),
        adapter.to_string(),
        "--raw-root".to_string(),
        raw_root.trim().to_string(),
    ];
    if let Some(split) = split.map(str::trim).filter(|split| !split.is_empty()) {
        args.push("--split".to_string());
        args.push(split.to_string());
    }
    run_document(runner, profile, &args, INDEX_TIMEOUT, INDEX_SCHEMA)
}

#[cfg(test)]
mod tests {
    use std::sync::Mutex;

    use super::*;
    use crate::ssh::CommandResult;

    struct FakeRunner {
        calls: Mutex<Vec<String>>,
        output: String,
        success: bool,
    }

    impl FakeRunner {
        fn new(output: &str) -> Self {
            Self {
                calls: Mutex::new(Vec::new()),
                output: output.to_string(),
                success: true,
            }
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
            Ok(CommandResult {
                success: self.success,
                stdout: self.output.clone(),
                stderr: String::new(),
            })
        }
    }

    fn profile() -> ServerProfile {
        let mut profile: ServerProfile = serde_json::from_str(include_str!(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/tests/fixtures/hpc/cluster-b.json"
        )))
        .map(|entry: crate::server_profiles::ServerEntry| entry.profile)
        .expect("fixture");
        profile.environment.coperceive_command = vec![
            "/home/u/venv/bin/python".to_string(),
            "-m".to_string(),
            "coperceive".to_string(),
        ];
        profile
    }

    #[test]
    fn detect_uses_the_saved_argv_and_parses_its_document() {
        let runner = FakeRunner::new(
            r#"{"schema":"coperceive-cluster-detect-1","status":"ok","adapter":"opv2v"}"#,
        );
        let result = detect(&runner, &profile(), "/data/opv2v/test").expect("detect");
        assert_eq!(result["adapter"], "opv2v");
        let calls = runner.calls.lock().unwrap();
        assert_eq!(calls.len(), 1);
        assert!(
            calls[0].starts_with("/home/u/venv/bin/python -m coperceive dataset detect "),
            "{}",
            calls[0]
        );
        assert!(calls[0].contains("/data/opv2v/test"), "{}", calls[0]);
    }

    #[test]
    fn index_quotes_the_adapter_and_root() {
        let runner = FakeRunner::new(
            r#"{"schema":"coperceive-cluster-index-1","scene_count":0,"scenes":[]}"#,
        );
        index(
            &runner,
            &profile(),
            "griffin",
            "/data/raw griffin; touch /tmp/x",
            None,
        )
        .expect("index");
        let calls = runner.calls.lock().unwrap();
        assert!(calls[0].contains("--adapter griffin"), "{}", calls[0]);
        assert!(
            calls[0].contains("'/data/raw griffin; touch /tmp/x'"),
            "{}",
            calls[0]
        );
        assert!(!calls[0].contains("--split"), "{}", calls[0]);
    }

    #[test]
    fn index_passes_the_split_quoted() {
        let runner = FakeRunner::new(
            r#"{"schema":"coperceive-cluster-index-1","scene_count":0,"scenes":[]}"#,
        );
        index(&runner, &profile(), "opv2v", "/data/opv2v", Some("test; id")).expect("index");
        index(&runner, &profile(), "opv2v", "/data/opv2v", Some("  ")).expect("index");
        let calls = runner.calls.lock().unwrap();
        assert!(
            calls[0].ends_with("--raw-root /data/opv2v --split 'test; id'"),
            "{}",
            calls[0]
        );
        assert!(!calls[1].contains("--split"), "{}", calls[1]);
    }

    #[test]
    fn the_stream_command_quotes_every_remote_argument() {
        let target = crate::ssh::SshTarget::alias("cluster-b");
        let argv = stream_argv("/usr/bin/ssh", &target, &profile(), "/r/cache dir; rm -rf ~", "/d/opv2v");
        assert_eq!(argv[0], "/usr/bin/ssh");
        assert!(argv.contains(&"BatchMode=yes".to_string()), "{argv:?}");
        let remote = argv.last().unwrap();
        assert!(remote.contains("dataset stream --root '/r/cache dir; rm -rf ~' --root /d/opv2v"), "{remote}");
        assert!(argv[argv.len() - 2] == "cluster-b", "{argv:?}");
    }

    #[test]
    fn an_unknown_adapter_is_refused_locally() {
        let runner = FakeRunner::new("{}");
        assert!(index(&runner, &profile(), "foo", "/data", None).is_err());
        assert!(runner.calls.lock().unwrap().is_empty());
    }

    #[test]
    fn a_plain_text_error_is_reported() {
        let mut runner = FakeRunner::new("not json");
        runner.success = false;
        let error = detect(&runner, &profile(), "/data/nope").expect_err("error");
        assert!(error.contains("Dataset command failed"), "{error}");
    }

    /// Answers the capability probe and the dataset command separately.
    struct ScriptedRunner {
        calls: Mutex<Vec<String>>,
        probe: String,
        command: CommandResult,
    }

    impl RemoteRunner for ScriptedRunner {
        fn run(
            &self,
            remote_cmd: &str,
            _timeout: Duration,
            _stdin: Option<&str>,
        ) -> Result<CommandResult, String> {
            self.calls.lock().unwrap().push(remote_cmd.to_string());
            if remote_cmd.contains("coperceive-check:capabilities") {
                return Ok(CommandResult {
                    success: true,
                    stdout: self.probe.clone(),
                    stderr: String::new(),
                });
            }
            Ok(self.command.clone())
        }
    }

    fn old_cli(probe: &str) -> ScriptedRunner {
        ScriptedRunner {
            calls: Mutex::new(Vec::new()),
            probe: probe.to_string(),
            command: CommandResult {
                success: false,
                stdout: String::new(),
                stderr: "usage: coperceive dataset [-h] {catalog,dry-run,download} ...\n\
                         coperceive dataset: error: argument dataset_command: invalid choice: \
                         'detect' (choose from 'catalog', 'dry-run', 'download')"
                    .to_string(),
            },
        }
    }

    #[test]
    fn an_old_cli_is_reported_as_too_old_with_the_update_command() {
        let runner = old_cli(
            "coperceive-capabilities-missing\ncoperceive 0.0.0\nLOCATION=/home/u/coperceive\nGIT=1\n",
        );
        let error = detect(&runner, &profile(), "/data/opv2v/test").expect_err("too old");
        assert_eq!(
            error,
            "coperceive on this server is too old for cluster datasets (found coperceive 0.0.0, \
             which has no capabilities command, need a build with cluster-datasets-5). Update it \
             with: git -C /home/u/coperceive pull --ff-only && /home/u/venv/bin/python -m pip \
             install -e /home/u/coperceive --no-deps"
        );
        assert!(!error.contains("invalid choice"), "{error}");
        let calls = runner.calls.lock().unwrap();
        assert_eq!(calls.len(), 2, "{calls:?}");
        assert!(calls[1].starts_with("sh -c '# coperceive-check:capabilities"), "{}", calls[1]);
        assert!(
            calls[1].ends_with("' sh /home/u/venv/bin/python -m coperceive"),
            "{}",
            calls[1]
        );
    }

    #[test]
    fn a_build_without_the_capability_is_too_old_and_not_a_checkout_gets_pip() {
        let runner = old_cli(
            r#"{"schema":"coperceive-capabilities-1","version":"0.1.0","capabilities":["cluster-datasets-2"]}"#,
        );
        let error = index(&runner, &profile(), "opv2v", "/data/opv2v", Some("test")).expect_err("too old");
        assert!(
            error.contains("(found coperceive 0.1.0 without cluster-datasets-5, need"),
            "{error}"
        );
        assert!(
            error.ends_with(
                "Update it with: /home/u/venv/bin/python -m pip install --upgrade \
                 git+https://github.com/adarsh-nl/coperceive@feat/m2-import"
            ),
            "{error}"
        );
    }

    #[test]
    fn a_supported_build_keeps_the_original_error() {
        let mut runner = old_cli(
            r#"{"schema":"coperceive-capabilities-1","version":"0.1.0","capabilities":["cluster-datasets-2","cluster-datasets-3","cluster-datasets-4","cluster-datasets-5"]}"#,
        );
        runner.command = CommandResult {
            success: false,
            stdout: r#"{"schema":"coperceive-cluster-index-1","status":"error","error":"raw root is not a directory: /x"}"#.to_string(),
            stderr: String::new(),
        };
        let error = index(&runner, &profile(), "opv2v", "/x", None).expect_err("error");
        assert_eq!(error, "raw root is not a directory: /x");
        assert_eq!(
            parse_capability_probe(&profile(), &runner.probe),
            RemoteSupport::Supported { version: "0.1.0".to_string() }
        );
    }

    #[test]
    fn an_unanswered_probe_is_unknown_and_a_bare_command_gets_plain_pip() {
        assert_eq!(parse_capability_probe(&profile(), ""), RemoteSupport::Unknown);
        let mut bare = profile();
        bare.environment.coperceive_command = vec!["coperceive".to_string()];
        let support = parse_capability_probe(
            &bare,
            "coperceive-capabilities-missing\nusage: coperceive [-h]\n",
        );
        let message = support.too_old_message().expect("too old");
        assert!(message.contains("(found a build without the capabilities command,"), "{message}");
        assert!(
            message.contains("pip install --upgrade git+https://github.com/adarsh-nl/coperceive@feat/m2-import (in the environment that provides coperceive)"),
            "{message}"
        );
    }
}
