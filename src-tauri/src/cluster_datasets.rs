// SPDX-License-Identifier: Apache-2.0
//! Remote dataset detect/index commands.
//!
//! Both are read-only and metadata-only: they run the saved coperceive argv on
//! the login node (`coperceive dataset detect|index`) and return its JSON. No
//! scene data is read and nothing is written.

use std::time::Duration;

use serde_json::Value;

use crate::hpc::{coperceive_argv, ServerProfile};
use crate::slurm_adapter::RemoteRunner;
use crate::ssh::quote_remote_argv;

pub const DETECT_SCHEMA: &str = "coperceive-cluster-detect-1";
pub const INDEX_SCHEMA: &str = "coperceive-cluster-index-1";
const DETECT_TIMEOUT: Duration = Duration::from_secs(45);
const INDEX_TIMEOUT: Duration = Duration::from_secs(120);

fn run_document(
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

/// List every scene of a raw dataset. Metadata only; writes nothing.
pub fn index(
    runner: &dyn RemoteRunner,
    profile: &ServerProfile,
    adapter: &str,
    raw_root: &str,
) -> Result<Value, String> {
    let adapter = adapter.trim();
    if !matches!(adapter, "griffin" | "opv2v" | "v2xset") {
        return Err(format!("{adapter:?} is not a built-in cluster adapter."));
    }
    if raw_root.trim().is_empty() {
        return Err("Choose the raw dataset root on the cluster.".to_string());
    }
    run_document(
        runner,
        profile,
        &[
            "dataset".to_string(),
            "index".to_string(),
            "--adapter".to_string(),
            adapter.to_string(),
            "--raw-root".to_string(),
            raw_root.trim().to_string(),
        ],
        INDEX_TIMEOUT,
        INDEX_SCHEMA,
    )
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
        )
        .expect("index");
        let calls = runner.calls.lock().unwrap();
        assert!(calls[0].contains("--adapter griffin"), "{}", calls[0]);
        assert!(
            calls[0].contains("'/data/raw griffin; touch /tmp/x'"),
            "{}",
            calls[0]
        );
    }

    #[test]
    fn an_unknown_adapter_is_refused_locally() {
        let runner = FakeRunner::new("{}");
        assert!(index(&runner, &profile(), "foo", "/data").is_err());
        assert!(runner.calls.lock().unwrap().is_empty());
    }

    #[test]
    fn a_plain_text_error_is_reported() {
        let mut runner = FakeRunner::new("not json");
        runner.success = false;
        let error = detect(&runner, &profile(), "/data/nope").expect_err("error");
        assert!(error.contains("Dataset command failed"), "{error}");
    }
}
