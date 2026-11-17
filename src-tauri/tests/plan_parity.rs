// SPDX-License-Identifier: Apache-2.0
//! Plan parity, the app's side (ADR 0018, ported to release/v0.1.1).
//!
//! For each case of `tests/fixtures/plan_parity/cases.json` this renders the job script
//! the app would submit for Griffin vehicle-side, through the calls the New evaluation
//! screen makes: `list_builtin_pipelines`, `build_run_plan`, `save_run_plan`,
//! `load_saved_plan`, `render_run_script`. A fake cluster answers the read-only checks
//! (its `coperceive model list --json` is `model_list.json`, which
//! `tests/test_plan_parity.py` holds to the real CLI's output). It panics on `sbatch`,
//! `scancel` or `squeue`; nothing here opens an SSH connection.
//!
//! The render must equal the committed `tests/fixtures/plan_parity/app/<case>.sbatch`
//! byte for byte, which `tests/test_plan_parity.py` compares with `coperceive model run
//! --slurm`. Python cannot run this renderer, so this test owns staleness: any change to
//! what the app renders fails here until the files are rewritten. Run with
//! `COPERCEIVE_UPDATE_PARITY=1` to rewrite them after a deliberate change, then review the
//! diff.

use std::collections::BTreeMap;
use std::fs;
use std::path::PathBuf;
use std::sync::Mutex;
use std::time::Duration;

use coperceive_desktop::hpc::{DiscoveredPartition, EnvironmentKind, EnvironmentSetup, JobDefaults};
use coperceive_desktop::job_submit::{load_saved_plan, render_run_script, SubmitOptions};
use coperceive_desktop::run_plan::{build_run_plan, list_builtin_pipelines, save_run_plan, RunPlanRequest};
use coperceive_desktop::server_profiles::{DiscoveredServer, ServerEntry};
use coperceive_desktop::slurm_adapter::RemoteRunner;
use coperceive_desktop::ssh::CommandResult;
use serde_json::Value;

const CLUSTER_B: &str = include_str!("fixtures/hpc/cluster-b.json");

fn parity_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../tests/fixtures/plan_parity")
}

/// A cluster that answers the plan's read-only checks and refuses to run a job.
struct FakeCluster {
    model_list: String,
    scenes: String,
    /// The case's `pyvenv.cfg`: every interpreter here is a venv on /software/python.
    pyvenv_cfg: String,
    /// What the run's python reports (`coperceive-check:plan-python`): the case's
    /// coperceive root and its scenes' samples.
    python_facts: String,
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
            return answered(self.model_list.clone());
        }
        if command.contains("coperceive-check:plan-python") {
            return answered(self.python_facts.clone());
        }
        if command.contains("coperceive-check:plan-path") {
            let path = last_argument(command);
            return answered(if path == self.scenes {
                // A scenes folder: no sha256, a listing fingerprint instead.
                format!(
                    "EXISTS=1\nREADABLE=1\nEXEC=1\nISDIR=1\nREALPATH={path}\nSHA256=\nFILES=10\nLISTING_SHA256={}\n",
                    "1f".repeat(32)
                )
            } else {
                // An interpreter is executable; any other path is a plain file.
                let exec = if path.ends_with("/bin/python") { 1 } else { 0 };
                format!("EXISTS=1\nREADABLE=1\nEXEC={exec}\nISDIR=0\nREALPATH={path}\nSHA256={}\n", "ab".repeat(32))
            });
        }
        if command.contains("coperceive-check:plan-interpreter") {
            // `sh -c '<script>' sh '<program>' '<mode>'`: a console script has its python
            // beside it; that python is a venv whose pyvenv.cfg the case gives.
            let words: Vec<&str> = command.rsplit_once("' sh ").map(|(_, rest)| rest).unwrap_or("").split(' ').collect();
            let program = words.first().map(|word| word.trim_matches('\'')).unwrap_or("").to_string();
            let mode = words.get(1).map(|word| word.trim_matches('\'')).unwrap_or("");
            let python = if mode == "script" {
                format!("{}python", program.trim_end_matches("coperceive"))
            } else {
                program.clone()
            };
            let venv = python.trim_end_matches("/bin/python");
            let mut out = String::new();
            if mode == "script" {
                out.push_str(&format!("BESIDE={python}\n"));
            }
            out.push_str(&format!("PYTHON={python}\nPYVENV_CFG={venv}/pyvenv.cfg\n"));
            for line in self.pyvenv_cfg.lines().filter(|line| line.starts_with("home") || line.starts_with("version")) {
                out.push_str(&format!("PYVENV_LINE={line}\n"));
            }
            return answered(out);
        }
        if command.contains("coperceive-check:plan-env") {
            return answered(
                "MODULE=/usr/share/lmod/lmod/libexec/lmod\nLMOD=/usr/share/lmod/lmod/libexec/lmod\n\
                 APPTAINER=\nSINGULARITY=\nPYTHON3=/usr/bin/python3\nCOPERCEIVE=\nHOME=/home/tester\n",
            );
        }
        if command.contains("--version") {
            return answered("coperceive 0.1.1\n");
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

/// The case's profile, on top of the cluster-b fixture: its `kind` (modules or venv),
/// modules, interpreter and coperceive command.
fn entry_for(cases: &Value, case: &Value) -> ServerEntry {
    let profile = &case["profile"];
    let mut entry: ServerEntry = serde_json::from_str(CLUSTER_B).unwrap();
    entry.id = format!("fixture-plan-parity-{}", text(case, "name"));
    let p = &mut entry.profile;
    p.name = format!("plan parity {}", text(case, "name"));
    p.data_roots = vec![text(case, "root").to_string()];
    p.code_roots = vec![text(cases, "code_root").to_string()];
    p.code_roots.extend(strings(&profile["extra_code_roots"]));
    p.results_root = text(profile, "results_root").to_string();
    p.scratch_template = text(profile, "scratch_template").to_string();
    p.defaults = JobDefaults { partition: Some(text(profile, "partition").to_string()), ..JobDefaults::default() };
    p.environment = EnvironmentSetup {
        kind: match text(profile, "kind") {
            "modules" => EnvironmentKind::Modules,
            "venv" => EnvironmentKind::Venv,
            other => panic!("cases.json: profile kind {other:?} is not modules or venv"),
        },
        modules: strings(&profile["modules"]),
        interpreter: profile["interpreter"].as_str().map(str::to_string),
        coperceive_command: strings(&profile["coperceive_command"]),
        ..EnvironmentSetup::default()
    };
    entry.discovered = profile["discovered_gpu_types"].as_array().map(|_| {
        let gpu_types = strings(&profile["discovered_gpu_types"]);
        DiscoveredServer {
            partitions: vec![DiscoveredPartition {
                name: text(profile, "partition").to_string(),
                nodes: Some(4),
                state: Some("up".to_string()),
                max_time: None,
                gres: gpu_types.first().map(|gpu| format!("gpu:{gpu}:4")),
                gpu_types: gpu_types.clone(),
            }],
            gpu_types,
            ..Default::default()
        }
    });
    entry.ssh = None;
    entry
}

/// The script the app renders for `case`.
fn render(cases: &Value, case: &Value) -> Result<String, String> {
    let entry = entry_for(cases, case);
    let root = text(case, "root");
    let scenes = format!("{root}/scenes");
    let model_list = fs::read_to_string(parity_dir().join(text(cases, "model_list"))).unwrap();
    let cluster = FakeCluster {
        model_list,
        scenes: scenes.clone(),
        pyvenv_cfg: text(case, "pyvenv_cfg").to_string(),
        python_facts: format!(
            "ROOT={}\nSAMPLES={}\n",
            text(case, "coperceive_root"),
            case["samples"].as_u64().expect("cases.json: samples must be a number")
        ),
        written: Mutex::new(None),
    };
    let profile = &entry.profile;
    let pipeline = list_builtin_pipelines(&cluster, profile)
        .into_iter()
        .next()
        .ok_or_else(|| "list_builtin_pipelines: no built-in model was listed".to_string())?;
    // What NewEvaluation.tsx sends for a built-in model; Griffin takes no checkpoint.
    let request = RunPlanRequest {
        profile_id: entry.id.clone(),
        pipeline: pipeline.spec.clone(),
        pipeline_source: pipeline.source.clone(),
        registration_path: pipeline.registration_path.clone(),
        plugin_manifest: pipeline.plugin_manifest.clone(),
        values: BTreeMap::new(),
        checkpoint: String::new(),
        split: scenes,
        model: text(case, "model").to_string(),
        raw_root: format!("{root}/raw"),
        media_root: format!("{root}/raw"),
        environment_override: None,
    };
    let plan = build_run_plan(&cluster, &entry, &request).map_err(|e| format!("build_run_plan: {e}"))?;
    let saved = save_run_plan(&cluster, &entry, &plan, &request).map_err(|e| format!("save_run_plan: {e}"))?;
    let loaded = load_saved_plan(&cluster, profile, &saved.id).map_err(|e| format!("load_saved_plan: {e}"))?;
    // NewEvaluation.tsx's defaults: the plan's time estimate, the plan's GRES.
    let options = SubmitOptions {
        walltime: loaded.time_estimate.clone().unwrap_or_default(),
        cpus_per_task: None,
        account: None,
        qos: None,
        gres: Some(loaded.gres.clone().unwrap_or_default()),
    };
    let preview = render_run_script(profile, &loaded, &options).map_err(|e| format!("render_run_script: {e}"))?;
    // The plan id is random (run_plan.rs, new_plan_id); the case fixes it.
    Ok(preview.script.replace(&loaded.id, text(cases, "run_id")))
}

#[test]
fn the_app_renders_each_griffin_parity_case_as_committed() {
    let cases: Value =
        serde_json::from_str(&fs::read_to_string(parity_dir().join("cases.json")).unwrap()).unwrap();
    let update = std::env::var("COPERCEIVE_UPDATE_PARITY").as_deref() == Ok("1");
    let mut failures = Vec::new();
    for case in cases["cases"].as_array().unwrap() {
        let name = text(case, "name");
        let rendered = match render(&cases, case) {
            Ok(script) => script,
            Err(refusal) => {
                failures.push(format!("{name}: the app refused the plan: {refusal}"));
                continue;
            }
        };
        let path = parity_dir().join("app").join(format!("{name}.sbatch"));
        if update {
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(&path, &rendered).unwrap();
            continue;
        }
        match fs::read_to_string(&path) {
            Ok(committed) if committed == rendered => {}
            Ok(_) => failures.push(format!(
                "{name}: the app's render changed; rerun with COPERCEIVE_UPDATE_PARITY=1 and review the diff"
            )),
            Err(_) => failures.push(format!("{name}: no committed render; run with COPERCEIVE_UPDATE_PARITY=1")),
        }
    }
    assert!(failures.is_empty(), "{}", failures.join("\n"));
}
