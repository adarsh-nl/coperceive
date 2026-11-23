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
//!
//! The `refusals` of cases.json are inputs the app refuses: each must be refused with
//! exactly its `refused` message (`<stage>: <message>`), and has no render.
//!
//! A case whose profile has a `proxy_jump` is reached through that jump host: ProxyJump
//! is transport, so it must reach the SSH arguments and leave the script byte-identical.

use std::collections::BTreeMap;
use std::fs;
use std::path::PathBuf;
use std::sync::Mutex;
use std::time::Duration;

use coperceive_desktop::hpc::{DiscoveredPartition, EnvironmentKind, EnvironmentSetup, JobDefaults};
use coperceive_desktop::job_submit::{load_saved_plan, render_run_script, SubmitOptions};
use coperceive_desktop::run_plan::{build_run_plan, list_builtin_pipelines, save_run_plan, RunPlanRequest};
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

/// The case's code root: where the manifest lives and what PYTHONPATH names.
fn code_root<'a>(cases: &'a Value, case: &'a Value) -> &'a str {
    case["code_root"].as_str().unwrap_or_else(|| text(cases, "code_root"))
}

/// The case's profile, on top of the cluster-b fixture: its `kind` (modules or venv),
/// modules, interpreter and coperceive command. With `with_proxy`, a `proxy_jump` (and
/// `port`) on the case's profile become the entry's SSH settings, as the app reads them
/// from `~/.ssh/config`.
fn entry_for(cases: &Value, case: &Value, with_proxy: bool) -> ServerEntry {
    let profile = &case["profile"];
    let mut entry: ServerEntry = serde_json::from_str(CLUSTER_B).unwrap();
    entry.id = format!("fixture-plan-parity-{}", text(case, "name"));
    let p = &mut entry.profile;
    p.name = format!("plan parity {}", text(case, "name"));
    p.data_roots = vec![text(case, "root").to_string()];
    p.code_roots = vec![code_root(cases, case).to_string()];
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
    entry.ssh = match (with_proxy, profile["proxy_jump"].as_str()) {
        (true, Some(jump)) => Some(SshHostSettings {
            alias: "cluster-b".to_string(),
            hostname: Some("cluster-b.example.org".to_string()),
            user: Some("bob".to_string()),
            port: profile["port"].as_u64().map(|port| u16::try_from(port).expect("cases.json: port")),
            proxy_jump: Some(jump.to_string()),
            proxy_command: None,
        }),
        _ => None,
    };
    entry
}

/// The script the app renders for `entry` (from `entry_for(cases, case, _)`), or
/// `Err("<stage>: <message>")` when the app refuses the plan.
fn render(cases: &Value, case: &Value, entry: &ServerEntry) -> Result<String, String> {
    let root = text(case, "root");
    let scenes = format!("{root}/scenes");
    // The cluster's `model list --json` names the manifest under the case's code root.
    let list_name = case["model_list"].as_str().unwrap_or_else(|| text(cases, "model_list"));
    let model_list = fs::read_to_string(parity_dir().join(list_name))
        .unwrap()
        .replace(text(cases, "code_root"), code_root(cases, case));
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
    let pipelines = list_builtin_pipelines(&cluster, profile);
    let pipeline = match case["plugin"].as_str() {
        Some(name) => pipelines.into_iter().find(|item| item.name == name),
        None => pipelines.into_iter().next(),
    }
    .ok_or_else(|| "list_builtin_pipelines: no built-in model was listed".to_string())?;
    // The model's options, as the form's extras (the CLI's --option k=v).
    let mut values = BTreeMap::new();
    let extras = strings(&case["extras"]);
    if !extras.is_empty() {
        values.insert("extras".to_string(), serde_json::json!(extras));
    }
    // What NewEvaluation.tsx sends for a built-in model; Griffin takes no checkpoint.
    let request = RunPlanRequest {
        profile_id: entry.id.clone(),
        pipeline: pipeline.spec.clone(),
        pipeline_source: pipeline.source.clone(),
        registration_path: pipeline.registration_path.clone(),
        plugin_manifest: pipeline.plugin_manifest.clone(),
        values,
        checkpoint: case["checkpoint"].as_str().unwrap_or("").to_string(),
        split: scenes,
        model: text(case, "model").to_string(),
        // `"raw_root": false` leaves the field empty: a model with no inputs.raw.
        raw_root: if case["raw_root"].as_bool() == Some(false) { String::new() } else { format!("{root}/raw") },
        // `"media_root": false` leaves the field empty, as the form does when none is chosen.
        media_root: if case["media_root"].as_bool() == Some(false) { String::new() } else { format!("{root}/raw") },
        // Griffin declares ckpt_root without a default: every Griffin case gives one.
        // `"ckpt_root": false` is a model that does not declare the option.
        ckpt_root: if case["ckpt_root"].as_bool() == Some(false) {
            String::new()
        } else {
            case["ckpt_root"].as_str().map(str::to_string).unwrap_or_else(|| format!("{root}/ckpts"))
        },
        // The AP protocol the plan records; a legacy case also puts it in the run line.
        eval_protocol: case["eval_protocol"].as_str().unwrap_or("default").to_string(),
        // The plugin's interpreter as New evaluation sends it; none leaves the manifest's.
        plugin_python: case["plugin_python"].as_str().unwrap_or("").to_string(),
        environment_override: None,
    };
    let plan = build_run_plan(&cluster, entry, &request).map_err(|e| format!("build_run_plan: {e}"))?;
    let saved = save_run_plan(&cluster, entry, &plan, &request).map_err(|e| format!("save_run_plan: {e}"))?;
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

fn read_cases() -> Value {
    serde_json::from_str(&fs::read_to_string(parity_dir().join("cases.json")).unwrap()).unwrap()
}

#[test]
fn the_app_renders_each_griffin_parity_case_as_committed() {
    let cases = read_cases();
    let update = std::env::var("COPERCEIVE_UPDATE_PARITY").as_deref() == Ok("1");
    let mut failures = Vec::new();
    for case in cases["cases"].as_array().unwrap() {
        let name = text(case, "name");
        let rendered = match render(&cases, case, &entry_for(&cases, case, true)) {
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

#[test]
fn the_app_refuses_each_refusal_case_with_its_message() {
    let cases = read_cases();
    let refusals = cases["refusals"].as_array().expect("cases.json: refusals");
    assert!(!refusals.is_empty(), "cases.json: no refusal case");
    let mut failures = Vec::new();
    for case in refusals {
        let name = text(case, "name");
        let render_path = parity_dir().join("app").join(format!("{name}.sbatch"));
        if render_path.exists() {
            failures.push(format!("{name}: a refusal case has a committed render {}", render_path.display()));
        }
        match render(&cases, case, &entry_for(&cases, case, true)) {
            Err(refusal) if refusal == text(case, "refused") => {}
            Err(refusal) => failures.push(format!("{name}: refused with\n  {refusal}\nnot\n  {}", text(case, "refused"))),
            Ok(_) => failures.push(format!("{name}: the app no longer refuses it; make it a case")),
        }
    }
    assert!(failures.is_empty(), "{}", failures.join("\n"));
}

/// ProxyJump is transport: with the jump host the SSH arguments carry `-J <jump>` (and the
/// profile's port), without it they carry no `-J`, and the job script is byte-identical
/// either way, and equal to the case's committed render.
#[test]
fn proxy_jump_changes_the_ssh_arguments_and_not_the_script() {
    let cases = read_cases();
    let proxied: Vec<&Value> =
        cases["cases"].as_array().unwrap().iter().filter(|case| case["profile"]["proxy_jump"].is_string()).collect();
    assert!(!proxied.is_empty(), "cases.json: no case has a proxy_jump");
    for case in proxied {
        let name = text(case, "name");
        let jump = text(&case["profile"], "proxy_jump");
        let with = entry_for(&cases, case, true);
        let without = entry_for(&cases, case, false);

        let with_args = standard_ssh_args(&with.ssh_target().unwrap());
        let without_args = standard_ssh_args(&without.ssh_target().unwrap());
        let jumps = |args: &[String]| -> Vec<String> {
            args.windows(2).filter(|pair| pair[0] == "-J").map(|pair| pair[1].clone()).collect()
        };
        assert_eq!(jumps(&with_args), vec![jump.to_string()], "{name}: ProxyJump missing from {with_args:?}");
        assert!(
            !with_args.iter().any(|arg| arg.starts_with("-oProxyJump") || arg.starts_with("ProxyJump")),
            "{name}: {with_args:?}"
        );
        assert!(jumps(&without_args).is_empty() && !without_args.contains(&"-J".to_string()), "{name}: {without_args:?}");
        if let Some(port) = case["profile"]["port"].as_u64() {
            assert!(with_args.windows(2).any(|pair| pair[0] == "-p" && pair[1] == port.to_string()), "{name}: {with_args:?}");
        }
        assert_ne!(with_args, without_args, "{name}: the jump host changed nothing in the SSH arguments");

        let script_with = render(&cases, case, &with).unwrap_or_else(|e| panic!("{name}: {e}"));
        let script_without = render(&cases, case, &without).unwrap_or_else(|e| panic!("{name}: {e}"));
        assert_eq!(script_with.as_bytes(), script_without.as_bytes(), "{name}: ProxyJump changed the script");
        assert!(!script_with.contains(jump), "{name}: the jump host reached the script");
        let committed = fs::read_to_string(parity_dir().join("app").join(format!("{name}.sbatch")))
            .unwrap_or_else(|_| panic!("{name}: no committed render"));
        assert_eq!(script_with, committed, "{name}: the render is not the committed one");
    }
}
