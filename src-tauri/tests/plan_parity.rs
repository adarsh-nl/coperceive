//! Plan parity, the app's side (docs/adr/0018-plan-parity.md).
//!
//! For each case of `tests/fixtures/plan_parity/cases.json` this renders the job script
//! the app would submit, through the same calls the New evaluation screen makes:
//! `list_builtin_pipelines`, `build_run_plan`, `save_run_plan`, `load_saved_plan`,
//! `render_run_script`. A fake cluster answers the read-only checks. It panics on
//! `sbatch`, `scancel` or `squeue`, and nothing here opens an SSH connection.
//!
//! The render, or the refusal when the app refuses the plan, must equal the committed
//! file in `tests/fixtures/plan_parity/app/`. `tests/test_plan_parity.py` compares
//! those files with the CLI's script. Run with `COPERCEIVE_UPDATE_PARITY=1` to rewrite
//! them after a deliberate change, then review the diff.

use std::collections::BTreeMap;
use std::fs;
use std::path::PathBuf;
use std::sync::Mutex;
use std::time::Duration;

use coperceive_desktop::hpc::{DiscoveredPartition, EnvironmentKind, EnvironmentSetup, JobDefaults};
use coperceive_desktop::job_submit::{load_saved_plan, render_run_script, SubmitOptions};
use coperceive_desktop::run_plan::{
    build_run_plan, list_builtin_pipelines, save_run_plan, RunPlanRequest,
};
use coperceive_desktop::server_profiles::{DiscoveredServer, ServerEntry, SshHostSettings};
use coperceive_desktop::slurm_adapter::RemoteRunner;
use coperceive_desktop::ssh::{standard_ssh_args, CommandResult};
use serde_json::Value;

const CLUSTER_B: &str = include_str!("fixtures/hpc/cluster-b.json");

fn parity_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../tests/fixtures/plan_parity")
}

/// A cluster that answers the plan's read-only checks and refuses to run a job.
struct FakeCluster {
    model_list_row: String,
    /// Paths the cluster reports as directories, with no sha256 (as a real one does).
    directories: Vec<String>,
    written: Mutex<Option<String>>,
}

/// The path argument of a `sh -c '<script>' sh '<path>'` check, unquoted.
fn last_argument(command: &str) -> String {
    let rest = command.rsplit_once(" sh ").map(|(_, rest)| rest).unwrap_or("").trim();
    match rest.strip_prefix('\'').and_then(|inner| inner.strip_suffix('\'')) {
        Some(inner) => inner.replace("'\\''", "'"),
        None => rest.to_string(),
    }
}

fn answered(stdout: impl Into<String>) -> Result<CommandResult, String> {
    Ok(CommandResult { success: true, stdout: stdout.into(), stderr: String::new() })
}

impl RemoteRunner for FakeCluster {
    fn run(&self, command: &str, _timeout: Duration, stdin: Option<&str>) -> Result<CommandResult, String> {
        for forbidden in ["sbatch", "scancel", "squeue"] {
            assert!(!command.contains(forbidden), "plan parity must never run {forbidden}: {command}");
        }
        if command.contains("coperceive-check:model-list") {
            return answered(format!("{}\n", self.model_list_row));
        }
        if command.contains("coperceive-check:plan-path") {
            let path = last_argument(command);
            let (is_dir, sha) = if self.directories.contains(&path) {
                (1, String::new())
            } else {
                (0, "ab".repeat(32))
            };
            return answered(format!(
                "EXISTS=1\nREADABLE=1\nEXEC=0\nISDIR={is_dir}\nREALPATH={path}\nSHA256={sha}\n"
            ));
        }
        if command.contains("coperceive-check:plan-env") {
            return answered(
                "MODULE=/usr/share/lmod/lmod/libexec/lmod\nLMOD=/usr/share/lmod/lmod/libexec/lmod\n\
                 APPTAINER=\nSINGULARITY=\nPYTHON3=/usr/bin/python3\nCOPERCEIVE=\nHOME=/home/tester\n",
            );
        }
        if command.contains("--version") {
            return answered("coperceive 0.1.2\n");
        }
        if command.contains("coperceive-check:plan-folders") {
            return answered("SCRATCH_OK=1\nRESULTS_OK=1\n");
        }
        if command.contains("coperceive-check:plan-write") {
            *self.written.lock().unwrap() = stdin.map(str::to_string);
            return answered("");
        }
        if command.contains("cat -- \"$1\"") {
            return answered(self.written.lock().unwrap().clone().unwrap_or_default());
        }
        Ok(CommandResult {
            success: false,
            stdout: String::new(),
            stderr: format!("plan parity fake cluster: unsupported command {command}"),
        })
    }
}

fn text<'a>(value: &'a Value, key: &str) -> &'a str {
    value[key].as_str().unwrap_or_else(|| panic!("cases.json: {key} must be a string"))
}

fn strings(value: &Value) -> Vec<String> {
    value
        .as_array()
        .map(|items| items.iter().map(|item| item.as_str().unwrap().to_string()).collect())
        .unwrap_or_default()
}

/// The case's profile, on top of the cluster-b fixture (a modules profile).
fn entry_for(cases: &Value, case: &Value, with_proxy: bool) -> ServerEntry {
    let profile = &case["profile"];
    let mut entry: ServerEntry = serde_json::from_str(CLUSTER_B).unwrap();
    entry.id = format!("fixture-plan-parity-{}", text(case, "name"));
    let p = &mut entry.profile;
    p.name = format!("plan parity {}", text(case, "name"));
    p.data_roots = vec![text(case, "root").to_string()];
    p.code_roots = vec![text(cases, "code_root").to_string()];
    p.results_root = text(profile, "results_root").to_string();
    p.scratch_template = text(profile, "scratch_template").to_string();
    p.defaults = JobDefaults {
        partition: Some(text(profile, "partition").to_string()),
        ..JobDefaults::default()
    };
    p.environment = EnvironmentSetup {
        kind: EnvironmentKind::Modules,
        modules: strings(&profile["modules"]),
        coperceive_command: strings(&profile["coperceive_command"]),
        ..EnvironmentSetup::default()
    };
    let gpu_types = strings(&profile["gpu_types"]);
    entry.discovered = if gpu_types.is_empty() {
        None
    } else {
        Some(DiscoveredServer {
            partitions: vec![DiscoveredPartition {
                name: text(profile, "partition").to_string(),
                nodes: Some(4),
                state: Some("up".to_string()),
                max_time: None,
                gres: profile["partition_gres"].as_str().map(str::to_string),
                gpu_types: gpu_types.clone(),
            }],
            gpu_types,
            ..Default::default()
        })
    };
    entry.ssh = match (with_proxy, profile["proxy_jump"].as_str()) {
        (true, Some(jump)) => Some(SshHostSettings {
            alias: "cluster-b".to_string(),
            hostname: Some("cluster-b.example.org".to_string()),
            user: Some("bob".to_string()),
            port: profile["port"].as_u64().map(|port| port as u16),
            proxy_jump: Some(jump.to_string()),
            proxy_command: None,
        }),
        _ => None,
    };
    entry
}

/// The script the app renders for `case`, or `Err("<stage>: <message>")` when it refuses.
fn render(cases: &Value, case: &Value, entry: &ServerEntry) -> Result<String, String> {
    let root = text(case, "root");
    let scenes = format!("{root}/scenes");
    let model_list_row = case["app_model_list_row"]
        .as_str()
        .map(str::to_string)
        .unwrap_or_else(|| format!("griffin  entry-point  {}", text(cases, "manifest")));
    let directories = if text(case, "app_scenes") == "directory" { vec![scenes.clone()] } else { vec![] };
    let cluster = FakeCluster { model_list_row, directories, written: Mutex::new(None) };
    let profile = &entry.profile;
    let pipeline = list_builtin_pipelines(&cluster, profile)
        .into_iter()
        .next()
        .ok_or_else(|| "list_builtin_pipelines: no built-in model was listed".to_string())?;
    let mut values = BTreeMap::new();
    let extras = strings(&case["extras"]);
    if !extras.is_empty() {
        values.insert("extras".to_string(), serde_json::json!(extras));
    }
    // What NewEvaluation.tsx sends for a built-in model.
    let request = RunPlanRequest {
        profile_id: entry.id.clone(),
        pipeline: pipeline.spec.clone(),
        pipeline_source: pipeline.source.clone(),
        registration_path: pipeline.registration_path.clone(),
        plugin_manifest: pipeline.plugin_manifest.clone(),
        values,
        checkpoint: format!("{root}/checkpoints/vehicle.pth"),
        split: scenes,
        model: text(case, "model").to_string(),
        raw_root: format!("{root}/raw"),
        media_root: if case["media_root"].as_bool() == Some(true) {
            format!("{root}/raw")
        } else {
            String::new()
        },
        environment_override: None,
    };
    let plan = build_run_plan(&cluster, entry, &request).map_err(|e| format!("build_run_plan: {e}"))?;
    let saved =
        save_run_plan(&cluster, entry, &plan, &request).map_err(|e| format!("save_run_plan: {e}"))?;
    let loaded = load_saved_plan(&cluster, profile, &saved.id).map_err(|e| format!("load_saved_plan: {e}"))?;
    // NewEvaluation.tsx's defaults: walltime 04:00:00, no cpus, account or qos, the plan's GRES.
    let options = SubmitOptions {
        walltime: "04:00:00".to_string(),
        cpus_per_task: None,
        account: None,
        qos: None,
        gres: Some(loaded.gres.clone().unwrap_or_default()),
    };
    let preview = render_run_script(profile, &loaded, &options).map_err(|e| format!("render_run_script: {e}"))?;
    // The plan id is random (run_plan.rs, new_plan_id); the case fixes it.
    Ok(preview.script.replace(&loaded.id, text(cases, "run_id")))
}

fn check_or_update(name: &str, rendered: &str, failures: &mut Vec<String>) {
    let path = parity_dir().join("app").join(name);
    if std::env::var("COPERCEIVE_UPDATE_PARITY").as_deref() == Ok("1") {
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(&path, rendered).unwrap();
        return;
    }
    match fs::read_to_string(&path) {
        Ok(committed) if committed == rendered => {}
        Ok(_) => failures.push(format!("{name}: the app's render changed; rerun with COPERCEIVE_UPDATE_PARITY=1 and review the diff")),
        Err(_) => failures.push(format!("{name}: no committed render; run with COPERCEIVE_UPDATE_PARITY=1")),
    }
}

#[test]
fn the_app_renders_each_parity_case_as_committed() {
    let cases: Value =
        serde_json::from_str(&fs::read_to_string(parity_dir().join("cases.json")).unwrap()).unwrap();
    let mut failures = Vec::new();
    for case in cases["cases"].as_array().unwrap() {
        let name = text(case, "name");
        let entry = entry_for(&cases, case, true);
        let (file, rendered) = match render(&cases, case, &entry) {
            Ok(script) => (format!("{name}.sbatch"), script),
            Err(refusal) => (format!("{name}.refused.txt"), format!("{refusal}\n")),
        };
        // A case renders either a script or a refusal, never both.
        let other = if file.ends_with(".sbatch") { format!("{name}.refused.txt") } else { format!("{name}.sbatch") };
        if std::env::var("COPERCEIVE_UPDATE_PARITY").as_deref() == Ok("1") {
            let _ = fs::remove_file(parity_dir().join("app").join(&other));
        } else if parity_dir().join("app").join(&other).exists() {
            failures.push(format!("{name}: expected {other}, the app now gives {file}"));
        }
        check_or_update(&file, &rendered, &mut failures);
    }
    assert!(failures.is_empty(), "{}", failures.join("\n"));
}

/// Case (c): ProxyJump is transport. It reaches the SSH arguments and never the script.
#[test]
fn proxy_jump_changes_the_ssh_arguments_and_not_the_script() {
    let cases: Value =
        serde_json::from_str(&fs::read_to_string(parity_dir().join("cases.json")).unwrap()).unwrap();
    let case = cases["cases"]
        .as_array()
        .unwrap()
        .iter()
        .find(|case| case["profile"]["proxy_jump"].is_string())
        .expect("a case with a ProxyJump");
    let jump = text(&case["profile"], "proxy_jump");
    let with = entry_for(&cases, case, true);
    let without = entry_for(&cases, case, false);
    let args = standard_ssh_args(&with.ssh_target().unwrap());
    assert!(
        args.windows(2).any(|pair| pair[0] == "-J" && pair[1] == jump),
        "ProxyJump missing from {args:?}"
    );
    assert_eq!(render(&cases, case, &with), render(&cases, case, &without));
}
