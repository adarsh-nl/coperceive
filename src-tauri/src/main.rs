// SPDX-License-Identifier: Apache-2.0
//! Coperceive Desktop Application Entrypoint (Tauri v2)

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};

use tauri::Manager;

use coperceive_desktop::platform;
use coperceive_desktop::controller::{self, ControllerConfig};
use coperceive_desktop::detect::{self, DetectOptions, DetectionReport};
use coperceive_desktop::run_plan::{self, PipelineEntry, RunPlan, RunPlanRequest};
use coperceive_desktop::job_submit::{
    self as job_submit, JobSubmitOutcome, SubmissionPreview, SubmitOptions,
};
use coperceive_desktop::job_watch::{
    self as job_watch, JobCancelResult, JobLogTail, JobWatchStatus,
};
use coperceive_desktop::job_report::{
    self as job_report, ImportedPredictions, MetricsReport, PredictionTail,
};
use coperceive_desktop::job_history::{self as job_history, RunHistoryEntry};
use coperceive_desktop::server_profiles::{self, ServerEntry, SshHostSettings};
use coperceive_desktop::remote::{
    RemoteClient, RemoteSessionManager, RemoteStartOptions, RemoteStatusResponse,
};
use coperceive_desktop::sidecar::SidecarSession;
use coperceive_desktop::slurm_adapter::SshRunner;
use coperceive_desktop::state::{self, AppPersistentState};

pub struct AppState {
    pub sidecar: Mutex<Option<SidecarSession>>,
    pub remote: Mutex<Option<RemoteSessionManager>>,
    pub ctrl_port: Mutex<Option<u16>>,
    pub ctrl_secret: Mutex<Option<String>>,
    pub controller_config: Mutex<Option<ControllerConfig>>,
    pub viewer_origin: Mutex<Option<String>>,
    /// What the current remote session serves (see `viewer_session_key`).
    pub remote_key: Mutex<Option<String>>,
    /// The open cluster dataset's scenes and states, for the viewer's scene list.
    pub dataset_scenes: Arc<Mutex<serde_json::Value>>,
    /// What the local sidecar serves when it reads a cluster dataset over SSH
    /// (see `dataset_open`); None for a local folder.
    pub stream_key: Mutex<Option<String>>,
    pub current_root: Mutex<Option<String>>,
    pub startup_instant: Instant,
    /// One viewer window. A second open focuses the existing window.
    pub viewer_build: Mutex<()>,
    pub replacing_viewer: AtomicBool,
    pub pending_export: coperceive_desktop::export::PendingExportDestination,
    pub pending_update: Mutex<Option<tauri_plugin_updater::Update>>,
    pub pending_dataset_prepare:
        Mutex<Option<coperceive_desktop::dataset_jobs::PendingPrepare>>,
}

impl Default for AppState {
    fn default() -> Self {
        Self {
            sidecar: Mutex::new(None),
            remote: Mutex::new(None),
            ctrl_port: Mutex::new(None),
            ctrl_secret: Mutex::new(None),
            controller_config: Mutex::new(None),
            viewer_origin: Mutex::new(None),
            remote_key: Mutex::new(None),
            dataset_scenes: Arc::new(Mutex::new(serde_json::Value::Null)),
            stream_key: Mutex::new(None),
            current_root: Mutex::new(None),
            startup_instant: Instant::now(),
            viewer_build: Mutex::new(()),
            replacing_viewer: AtomicBool::new(false),
            pending_export: coperceive_desktop::export::PendingExportDestination::new(),
            pending_update: Mutex::new(None),
            pending_dataset_prepare: Mutex::new(None),
        }
    }
}

#[derive(Debug, Serialize, Deserialize)]
pub struct SessionPayload {
    pub origin: String,
    pub token: String,
    pub server_version: String,
    pub data_root: String,
    pub target_type: String,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct HealthPayload {
    pub healthy: bool,
    pub latency_ms: u64,
    pub server_version: Option<String>,
}

/// Resolves the bundled python-build-standalone interpreter from the Tauri resource directory.
/// Falls back to dev python if running unbundled.
fn resolve_python_binary(app: &tauri::AppHandle) -> PathBuf {
    use tauri::Manager;

    if let Ok(res_dir) = app.path().resource_dir() {
        // Pinned python-build-standalone inside Tauri resources
        let candidates = platform::resolve_python_candidates(&res_dir);
        for candidate in &candidates {
            if candidate.is_file() {
                return candidate.clone();
            }
        }
    }

    if let Ok(env_py) = std::env::var("COPERCEIVE_PYTHON") {
        return PathBuf::from(env_py);
    }

    #[cfg(target_os = "windows")]
    {
        PathBuf::from("python")
    }
    #[cfg(not(target_os = "windows"))]
    {
        PathBuf::from("python3")
    }
}

#[tauri::command]
async fn get_persistent_state() -> Result<AppPersistentState, String> {
    Ok(state::load_state())
}

#[tauri::command]
async fn is_remote_enabled() -> Result<bool, String> {
    Ok(std::env::var("COPERCEIVE_REMOTE").map(|v| v == "1" || v.to_lowercase() == "true").unwrap_or(false))
}

#[tauri::command]
async fn pick_data_root(app: tauri::AppHandle) -> Result<Option<String>, String> {
    // Test-only: e2e writes the answer into COPERCEIVE_MOCK_DIALOG_FILE so the
    // Move library flow can pick a temp folder without a native dialog. Inert
    // unless the app was launched with COPERCEIVE_TEST_ENV / MARKER_FILE.
    if let Some(path) = coperceive_desktop::test_seams::take_mock_dialog_path() {
        return Ok(Some(path));
    }

    use tauri_plugin_dialog::DialogExt;

    let folder = app.dialog().file().blocking_pick_folder();
    Ok(folder.and_then(|fp| fp.into_path().ok()).map(|p| p.to_string_lossy().into_owned()))
}

#[tauri::command]
async fn pick_save_path(
    app: tauri::AppHandle,
    state: tauri::State<'_, Arc<AppState>>,
    default_name: String,
    extension: String,
) -> Result<Option<String>, String> {
    if let Some(path) = coperceive_desktop::test_seams::take_mock_dialog_path() {
        state.pending_export.set(Some(PathBuf::from(&path)));
        return Ok(Some(path));
    }
    if coperceive_desktop::test_seams::test_env_active() {
        state.pending_export.set(None);
        return Err("No mock dialog file path provided in test environment (seed COPERCEIVE_MOCK_DIALOG_FILE)".into());
    }

    use tauri_plugin_dialog::DialogExt;

    let mut builder = app.dialog().file().set_file_name(&default_name);
    if !extension.is_empty() {
        builder = builder.add_filter("Export File", &[&extension]);
    }
    let choice = builder.blocking_save_file();
    let chosen_path = choice.and_then(|fp| fp.into_path().ok()).map(|p| p.to_string_lossy().into_owned());

    state.pending_export.set(chosen_path.as_deref().map(PathBuf::from));

    Ok(chosen_path)
}

#[tauri::command]
async fn save_exported_file(
    source_relative: Option<String>,
    bytes: Option<Vec<u8>>,
    state: tauri::State<'_, Arc<AppState>>,
) -> Result<String, String> {
    let dest_path = state.pending_export.take()?;

    let root_lock = state.current_root.lock().map_err(|e| e.to_string())?;
    let root_ref = root_lock.as_deref().map(Path::new);
    let bytes_ref = bytes.as_deref();
    let rel_ref = source_relative.as_deref();

    coperceive_desktop::export::write_export_file(
        root_ref,
        rel_ref,
        &dest_path,
        bytes_ref,
    )?;

    let path_str = dest_path.to_string_lossy().into_owned();
    state.pending_export.set_last_saved(dest_path);

    Ok(path_str)
}

/// Poll until no window has `label`, up to `limit`. Never call on the main
/// thread: closing a window needs the main event loop.
fn wait_for_window_gone(app: &tauri::AppHandle, label: &str, limit: Duration) -> bool {
    use tauri::Manager;
    let deadline = Instant::now() + limit;
    while app.get_webview_window(label).is_some() {
        if Instant::now() >= deadline {
            return false;
        }
        std::thread::sleep(Duration::from_millis(20));
    }
    true
}

fn open_viewer_window(
    app: &tauri::AppHandle,
    origin: &str,
    token: &str,
    data_root: &str,
    ctrl_port: u16,
    state: &AppState,
    query: Option<&str>,
) -> Result<(), String> {
    use tauri::Manager;

    if let Some(existing_viewer) = app.get_webview_window("viewer") {
        let _ = existing_viewer.close();
        // close() only asks the window to close. Until it is gone, the lookup
        // below finds it, "reuses" it and returns, and then it closes: no
        // viewer at all, and the hidden main window is left showing the scene.
        // Every caller runs off the main thread, so waiting here is safe.
        if !wait_for_window_gone(app, "viewer", Duration::from_secs(5)) {
            if let Some(stuck) = app.get_webview_window("viewer") {
                let _ = stuck.destroy();
            }
            if !wait_for_window_gone(app, "viewer", Duration::from_secs(2)) {
                return Err(
                    "The previous viewer window did not close. Close it and open the scene again."
                        .to_string(),
                );
            }
        }
    }

    if let Ok(mut lock) = state.viewer_origin.lock() {
        *lock = Some(origin.to_string());
    }

    let hash = match query {
        Some(query) if !query.is_empty() => format!("{}/#/viewer?{}", origin, query),
        _ => format!("{}/#/viewer", origin),
    };
    let viewer_url_str = hash;
    let parsed_url: tauri::Url = viewer_url_str
        .parse()
        .map_err(|e| format!("Failed to parse viewer URL '{}': {}", viewer_url_str, e))?;

    let ctrl_secret = state
        .ctrl_secret
        .lock()
        .ok()
        .and_then(|s| s.clone())
        .unwrap_or_default();
    let persistent = state::load_state();
    let initial_state_json = serde_json::to_string(&persistent).unwrap_or_else(|_| "{}".into());

    let _viewer_guard = state.viewer_build.lock().map_err(|err| err.to_string())?;
    if let Some(existing) = app.get_webview_window("viewer") {
        eprintln!("[viewer] reusing existing viewer window");
        let _ = existing.show();
        let _ = existing.set_focus();
        if let Some(main_window) = app.get_webview_window("main") {
            let _ = main_window.hide();
        }
        return Ok(());
    }

    let basename = Path::new(data_root)
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or(data_root);

    let escaped_origin = origin.replace('\\', "\\\\").replace('"', "\\\"");
    let escaped_token = token.replace('\\', "\\\\").replace('"', "\\\"");
    let escaped_secret = ctrl_secret.replace('\\', "\\\\").replace('"', "\\\"");
    let escaped_root = data_root.replace('\\', "\\\\").replace('"', "\\\"");
    let escaped_base = basename.replace('\\', "\\\\").replace('"', "\\\"");
    let test_env = coperceive_desktop::test_seams::test_env_active();
    let force_motion =
        test_env && std::env::var("COPERCEIVE_FORCE_MOTION").ok().as_deref() == Some("1");
    let update_url = std::env::var("COPERCEIVE_UPDATE_URL").unwrap_or_default();
    let escaped_update = update_url.replace('\\', "\\\\").replace('"', "\\\"");

    let init_script = format!(
        r#"
        (function() {{
            try {{
                window.__COPERCEIVE_DESKTOP__ = true;
                // Tauri injects its IPC object here too, but no command is
                // permitted for this window (capabilities: main only).
                window.__COPERCEIVE_VIEWER_WINDOW__ = true;
                window.__COPERCEIVE_TITLEBAR__ = "overlay";
                window.__COPERCEIVE_TEST_ENV__ = {test_env};
                window.__COPERCEIVE_FORCE_MOTION__ = {force_motion};
                window.__COPERCEIVE_UPDATE_URL__ = "{escaped_update}";
                window.__COPERCEIVE_CTRL_PORT__ = {ctrl_port};
                window.__COPERCEIVE_CTRL_SECRET__ = "{escaped_secret}";
                window.__COPERCEIVE_DATA_ROOT__ = "{escaped_root}";
                window.__COPERCEIVE_DATA_BASENAME__ = "{escaped_base}";
                window.__COPERCEIVE_INITIAL_STATE__ = {initial_state_json};
                if (!window.location.origin || window.location.origin === "null" || window.location.origin === "{escaped_origin}" || window.location.href.indexOf("{escaped_origin}") === 0) {{
                    try {{
                        sessionStorage.setItem("coperceive-token", "{escaped_token}");
                        sessionStorage.setItem("coperceive-origin", "{escaped_origin}");
                    }} catch (_) {{}}
                }}
            }} catch (e) {{
                console.error("Failed to initialize coperceive desktop state", e);
            }}
        }})();
        "#
    );

    let mut win_builder = tauri::WebviewWindowBuilder::new(
        app,
        "viewer",
        tauri::WebviewUrl::External(parsed_url),
    )
    .title(format!("coperceive — {}", basename))
    .min_inner_size(900.0, 600.0)
    .resizable(true)
    .initialization_script(&init_script);

    win_builder = platform::apply_viewer_window_chrome(win_builder);

    let monitors_info: Vec<state::MonitorInfo> = {
        let primary = app.primary_monitor().ok().flatten();
        let primary_name = primary.as_ref().and_then(|m| m.name().cloned());
        let primary_pos = primary.as_ref().map(|m| *m.position());

        let avail = app.available_monitors().unwrap_or_default();
        avail.into_iter().map(|m| {
            let scale = if m.scale_factor() > 0.0 { m.scale_factor() } else { 1.0 };
            let work = m.work_area();
            let (px, py, pw, ph) = if work.size.width > 0 && work.size.height > 0 {
                (work.position.x, work.position.y, work.size.width, work.size.height)
            } else {
                (m.position().x, m.position().y, m.size().width, m.size().height)
            };
            let is_primary = primary_name.as_ref() == m.name() && primary_pos == Some(*m.position());
            state::MonitorInfo {
                name: m.name().cloned(),
                x: px as f64 / scale,
                y: py as f64 / scale,
                width: pw as f64 / scale,
                height: ph as f64 / scale,
                scale_factor: scale,
                is_primary,
            }
        }).collect()
    };

    let resolved_ws = persistent.window_state.as_ref().map(|ws| {
        state::resolve_window_geometry(ws, &monitors_info)
    });

    if let Some(ref ws) = resolved_ws {
        win_builder = win_builder
            .inner_size(ws.width, ws.height)
            .position(ws.x, ws.y);
    } else {
        win_builder = win_builder.inner_size(1400.0, 900.0);
    }

    eprintln!("[viewer] opened viewer window");
    let viewer_window = win_builder
        .build()
        .map_err(|e| format!("Failed to create viewer window: {}", e))?;

    if let Some(ref ws) = resolved_ws {
        if ws.is_maximized {
            let _ = viewer_window.maximize();
        }
        if persistent.window_state.as_ref() != Some(ws) {
            let mut updated_persistent = persistent.clone();
            updated_persistent.window_state = Some(ws.clone());
            let _ = state::save_state(&updated_persistent);
        }
    } else {
        let scale = viewer_window.scale_factor().unwrap_or(1.0).max(0.1);
        let mon = viewer_window.current_monitor().ok().flatten();
        let mon_name = mon.as_ref().and_then(|m| m.name().cloned());
        let (pos_x, pos_y) = match viewer_window.outer_position() {
            Ok(pos) => {
                let logical: tauri::LogicalPosition<f64> = pos.to_logical(scale);
                (logical.x, logical.y)
            }
            Err(_) => (0.0, 0.0),
        };
        let (size_w, size_h) = match viewer_window.inner_size() {
            Ok(size) => {
                let logical: tauri::LogicalSize<f64> = size.to_logical(scale);
                (logical.width, logical.height)
            }
            Err(_) => (1400.0, 900.0),
        };

        let initial_ws = state::WindowState {
            width: size_w,
            height: size_h,
            x: pos_x,
            y: pos_y,
            is_maximized: false,
            monitor: mon_name,
            scale_factor: Some(scale),
        };
        let mut updated_persistent = persistent.clone();
        updated_persistent.window_state = Some(initial_ws);
        let _ = state::save_state(&updated_persistent);
    }

    if let Some(main_window) = app.get_webview_window("main") {
        let _ = main_window.hide();
    }

    Ok(())
}

async fn switch_data_root(
    app: &tauri::AppHandle,
    state: &AppState,
    new_path: PathBuf,
) -> Result<(), String> {
    use tauri::Manager;

    // 1. Inline validation: must contain manifest.json directly or in subdirectories
    if let Err(msg) = state::validate_dataset_root(&new_path) {
        if let Some(viewer) = app.get_webview_window("viewer") {
            let escaped = msg.replace('\\', "\\\\").replace('"', "\\\"");
            let _ = viewer.eval(&format!(
                "if (window.__COPERCEIVE_DATASET_ERROR__) window.__COPERCEIVE_DATASET_ERROR__(\"{escaped}\"); else if (window.__COPERCEIVE_SHOW_TOAST__) window.__COPERCEIVE_SHOW_TOAST__(\"{escaped}\", \"error\");"
            ));
        }
        return Err(msg);
    }

    let new_path_str = new_path.to_string_lossy().into_owned();
    let basename = new_path
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or(&new_path_str)
        .to_string();

    let current_viewer_origin = {
        let lock = state.viewer_origin.lock().map_err(|e| e.to_string())?;
        lock.as_ref().cloned().or_else(|| {
            state.sidecar.lock().ok().and_then(|s| s.as_ref().map(|sess| format!("http://127.0.0.1:{}", sess.port)))
        })
    };

    let python_bin = resolve_python_binary(app);
    let mut new_session = SidecarSession::spawn_with_maps(
        &python_bin,
        &new_path,
        library_maps_root().as_deref(),
        current_viewer_origin.as_deref(),
    )
        .map_err(|e| format!("Failed to spawn new sidecar: {}", e))?;

    let new_origin = format!("http://127.0.0.1:{}", new_session.port);
    let new_token = new_session.token.clone();

    // Poll until healthy
    if let Err(e) = reqwest_like_poll(&new_origin, &new_token, Duration::from_secs(60)) {
        new_session.preserve_log = true;
        let _ = new_session.stop();
        let err_msg = format!("Failed to start sidecar for new dataset: {}", e);
        if let Some(viewer) = app.get_webview_window("viewer") {
            let escaped = err_msg.replace('\\', "\\\\").replace('"', "\\\"");
            let _ = viewer.eval(&format!(
                "if (window.__COPERCEIVE_SHOW_TOAST__) window.__COPERCEIVE_SHOW_TOAST__(\"{escaped}\", \"error\");"
            ));
        }
        return Err(err_msg);
    }

    // Stop old session
    {
        let mut lock = state.sidecar.lock().map_err(|e| e.to_string())?;
        if let Some(mut old) = lock.take() {
            let _ = old.stop();
        }
        *lock = Some(new_session);
        if let Ok(mut current) = state.stream_key.lock() {
            *current = None;
        }
    }

    if let Ok(lock) = state.controller_config.lock() {
        if let Some(ref cfg) = *lock {
            cfg.set_allowed_origin(new_origin.clone());
        }
    }

    // Update state.json with new root and prepend to recent_roots
    {
        let mut persistent = state::load_state();
        persistent.last_data_root = Some(new_path_str.clone());
        persistent.last_scene_id = None;
        persistent.last_sample_index = None;
        persistent.last_timestamp_us = None;
        state::add_recent_root(&mut persistent, &new_path_str);
        let _ = state::save_state(&persistent);

        // Update application menu with refreshed recent roots (macOS)
        #[cfg(target_os = "macos")]
        if let Ok(menu) = build_app_menu(app, &persistent.recent_roots) {
            let _ = app.set_menu(menu);
        }
    }

    // Update window title and notify viewer
    if let Some(viewer) = app.get_webview_window("viewer") {
        let _ = viewer.set_title(&format!("coperceive — {}", basename));
        let escaped_origin = new_origin.replace('\\', "\\\\").replace('"', "\\\"");
        let escaped_token = new_token.replace('\\', "\\\\").replace('"', "\\\"");
        let escaped_basename = basename.replace('\\', "\\\\").replace('"', "\\\"");
        let escaped_root = new_path_str.replace('\\', "\\\\").replace('"', "\\\"");

        let update_script = format!(
            r#"
            (function() {{
                try {{
                    sessionStorage.setItem("coperceive-origin", "{escaped_origin}");
                    sessionStorage.setItem("coperceive-token", "{escaped_token}");
                    if (window.__COPERCEIVE_DATASET_CHANGED__) {{
                        window.__COPERCEIVE_DATASET_CHANGED__("{escaped_origin}", "{escaped_token}", "{escaped_basename}", "{escaped_root}");
                    }} else {{
                        window.location.href = "{escaped_origin}/#/viewer";
                    }}
                }} catch (e) {{
                    console.error("Failed to update viewer after dataset switch", e);
                }}
            }})();
            "#
        );
        let _ = viewer.eval(&update_script);
    }

    Ok(())
}

pub async fn trigger_open_dataset_dialog(app: &tauri::AppHandle, state: &AppState) {
    // Test-only: e2e answers the folder picker through COPERCEIVE_MOCK_DIALOG_FILE.
    // Inert unless the app was launched with COPERCEIVE_TEST_ENV / MARKER_FILE.
    if let Some(path_str) = coperceive_desktop::test_seams::take_mock_dialog_path() {
        let path = PathBuf::from(path_str);
        let _ = switch_data_root(app, state, path).await;
        return;
    }

    use tauri_plugin_dialog::DialogExt;
    let folder = app.dialog().file().blocking_pick_folder();
    if let Some(path) = folder.and_then(|fp| fp.into_path().ok()) {
        let _ = switch_data_root(app, state, path).await;
    }
}

fn copy_diagnostics_to_clipboard(app: &tauri::AppHandle, state: &AppState) {
    let version = coperceive_desktop::updater::app_version(&app.package_info().version);
    let commit = option_env!("GIT_COMMIT").unwrap_or("580691a");
    let macos_ver = std::process::Command::new("sw_vers")
        .arg("-productVersion")
        .output()
        .ok()
        .and_then(|o| String::from_utf8(o.stdout).ok())
        .map(|s| s.trim().to_string())
        .unwrap_or_else(|| "unknown".into());

    let data_root = state
        .current_root
        .lock()
        .ok()
        .and_then(|r| r.clone())
        .or_else(|| state::load_state().last_data_root)
        .unwrap_or_else(|| "none".into());

    let startup_ms = Instant::now()
        .duration_since(state.startup_instant)
        .as_millis();

    let log_path = {
        let lock = state.sidecar.lock().ok();
        lock.and_then(|s| s.as_ref().map(|sess| sess.log_path.clone()))
    };

    let mut log_lines = String::new();
    if let Some(ref path) = log_path {
        if let Ok(content) = std::fs::read_to_string(path) {
            let lines: Vec<&str> = content.lines().collect();
            let start = lines.len().saturating_sub(200);
            for l in &lines[start..] {
                let mut line_str = l.to_string();
                if let Some(idx) = line_str.find("Bearer ") {
                    line_str.replace_range(idx + 7.., "[REDACTED]");
                }
                log_lines.push_str(&line_str);
                log_lines.push('\n');
            }
        }
    }

    let diagnostics = format!(
        "Coperceive Diagnostics\n\
         =====================\n\
         Version: {}\n\
         Commit: {}\n\
         macOS: {}\n\
         Data Root: {}\n\
         Startup Duration: {} ms\n\
         Sidecar Log: {}\n\n\
         Recent Logs (Redacted):\n\
         {}\n",
        version,
        commit,
        macos_ver,
        data_root,
        startup_ms,
        log_path.map(|p| p.display().to_string()).unwrap_or_else(|| "none".into()),
        if log_lines.is_empty() { "No logs recorded." } else { &log_lines }
    );

    let copied = platform::copy_to_clipboard(&diagnostics);

    if let Some(viewer) = app.get_webview_window("viewer") {
        let msg = if copied {
            "Diagnostics copied to clipboard"
        } else {
            "Failed to copy diagnostics"
        };
        let _ = viewer.eval(&format!(
            "if (window.__COPERCEIVE_SHOW_TOAST__) window.__COPERCEIVE_SHOW_TOAST__(\"{msg}\", \"info\");"
        ));
    }
}

/// Bring the main window forward on its Library. The viewer window stays the
/// one viewer (opening a scene from the Library replaces it); it never renders
/// the Library itself, because it has no Tauri IPC.
fn show_library_window(app: &tauri::AppHandle) -> bool {
    use tauri::{Emitter, Manager};
    let Some(main) = app.get_webview_window("main") else {
        return false;
    };
    let _ = main.unminimize();
    let _ = main.show();
    let _ = main.set_focus();
    let _ = main.emit("navigate-library", ());
    main.is_visible().unwrap_or(false)
}

fn start_desktop_controller(app_handle: tauri::AppHandle, app_state: Arc<AppState>) -> Result<u16, String> {
    let secret = coperceive_desktop::token::generate_secure_token().map_err(|e| e.to_string())?;
    let mut cfg = ControllerConfig::new(0, secret.clone(), None);
    let handle_eval = app_handle.clone();
    cfg.eval_dispatcher = Some(Arc::new(move |script: String, target: Option<String>| {
        use tauri::Manager;
        let window = match target.as_deref() {
            Some(label) => handle_eval.get_webview_window(label),
            None => handle_eval
                .get_webview_window("viewer")
                .or_else(|| handle_eval.get_webview_window("main")),
        };
        if let Some(w) = window {
            w.eval(&script).map_err(|e| format!("w.eval failed: {e}"))
        } else {
            Err("No webview window found (neither 'viewer' nor 'main')".into())
        }
    }));
    let handle_library = app_handle.clone();
    cfg.library_shower = Some(Arc::new(move || show_library_window(&handle_library)));
    cfg.dataset_scenes = app_state.dataset_scenes.clone();
    let session_state = app_state.clone();
    cfg.viewer_session = Some(Arc::new(move || {
        let Ok(lock) = session_state.remote.lock() else {
            return serde_json::Value::Null;
        };
        let Some(session) = lock.as_ref() else {
            return serde_json::Value::Null;
        };
        match session.job_status() {
            Ok(status) => serde_json::json!({
                "job_id": status.job_id.or(session.job_id),
                "state": status.state,
                "status": status.status,
                "time_left": status.time_left,
                "time_left_seconds": status.time_left_seconds,
                "elapsed": status.elapsed,
            }),
            Err(_) => serde_json::json!({ "job_id": session.job_id, "status": "unknown" }),
        }
    }));
    let handle_title = app_handle.clone();
    cfg.viewer_titler = Some(Arc::new(move |scene: String| {
        use tauri::Manager;
        if let Some(viewer) = handle_title.get_webview_window("viewer") {
            let _ = viewer.set_title(&format!("coperceive — {scene}"));
        }
    }));
    let handle_prepare = app_handle.clone();
    cfg.scene_preparer = Some(Arc::new(move |scene_id: String| {
        use tauri::{Emitter, Manager};
        // The main window shows the dataset view's prepare preview for this scene;
        // the user confirms there (nothing is submitted from the viewer).
        let Some(main) = handle_prepare.get_webview_window("main") else {
            return false;
        };
        let _ = main.unminimize();
        let _ = main.show();
        let _ = main.set_focus();
        let _ = main.emit("prepare-scene", serde_json::json!({ "scene_id": scene_id }));
        main.is_visible().unwrap_or(false)
    }));
    let handle_close = app_handle.clone();
    cfg.window_closer = Some(Arc::new(move |label: &str| {
        use tauri::Manager;
        if let Some(w) = handle_close.get_webview_window(label) {
            let _ = w.close();
        }
    }));

    let picker_handle = app_handle.clone();
    let picker_state = app_state.clone();
    cfg.export_picker = Some(Arc::new(move |default_name: &str, extension: &str| {
        if let Some(path) = coperceive_desktop::test_seams::take_mock_dialog_path() {
            picker_state.pending_export.set(Some(PathBuf::from(&path)));
            return Ok(Some(path));
        }
        if coperceive_desktop::test_seams::test_env_active() {
            picker_state.pending_export.set(None);
            return Err("No mock dialog file path provided in test environment (seed COPERCEIVE_MOCK_DIALOG_FILE)".into());
        }
        {
            use tauri_plugin_dialog::DialogExt;
            let mut builder = picker_handle.dialog().file().set_file_name(default_name);
            if !extension.is_empty() {
                builder = builder.add_filter("Export File", &[extension]);
            }
            let choice = builder.blocking_save_file();
            let path = choice.and_then(|fp| fp.into_path().ok()).map(|p| p.to_string_lossy().into_owned());
            picker_state.pending_export.set(path.as_deref().map(PathBuf::from));
            Ok(path)
        }
    }));

    let saver_state = app_state.clone();
    cfg.export_saver = Some(Arc::new(move |source_relative: Option<&str>, bytes: Option<&[u8]>| {
        let dest_path = saver_state.pending_export.take()?;
        let root_lock = saver_state.current_root.lock().map_err(|e| e.to_string())?;
        let root_ref = root_lock.as_deref().map(Path::new);
        coperceive_desktop::export::write_export_file(
            root_ref,
            source_relative,
            &dest_path,
            bytes,
        )?;
        let path_str = dest_path.to_string_lossy().into_owned();
        saver_state.pending_export.set_last_saved(dest_path);
        Ok(path_str)
    }));

    let revealer_state = app_state.clone();
    cfg.finder_revealer = Some(Arc::new(move || {
        let dest_path = revealer_state.pending_export.last_saved()?;
        platform::reveal_path(Path::new(&dest_path))
    }));

    let check_handle = app_handle.clone();
    let check_state = app_state.clone();
    cfg.updater_checker = Some(Arc::new(move |_is_manual: bool, override_url_opt: Option<String>| {
        {
            use tauri_plugin_updater::UpdaterExt;
            let effective_url = coperceive_desktop::updater::resolve_update_endpoint_override(override_url_opt.as_deref());
            let builder = if let Some(ref override_url) = effective_url {
                if let Ok(parsed) = tauri::Url::parse(override_url) {
                    check_handle.updater_builder().endpoints(vec![parsed]).unwrap_or_else(|_| check_handle.updater_builder())
                } else {
                    check_handle.updater_builder()
                }
            } else {
                check_handle.updater_builder()
            };
            let updater = match builder.build() {
                Ok(u) => u,
                Err(err) => {
                    let outcome = coperceive_desktop::updater::UpdateCheckOutcome::Error {
                        error: err.to_string(),
                        is_network: false,
                    };
                    return Ok(serde_json::to_value(&outcome).unwrap_or_default());
                }
            };
            match tauri::async_runtime::block_on(updater.check()) {
                Ok(Some(update)) => {
                    let date_str = update.date.map(|d| d.to_string());
                    let outcome = coperceive_desktop::updater::UpdateCheckOutcome::Available {
                        version: update.version.clone(),
                        current_version: update.current_version.clone(),
                        notes: update.body.clone(),
                        pub_date: date_str,
                    };
                    if let Ok(mut lock) = check_state.pending_update.lock() {
                        *lock = Some(update);
                    }
                    Ok(serde_json::to_value(&outcome).unwrap_or_default())
                }
                Ok(None) => {
                    let outcome = coperceive_desktop::updater::UpdateCheckOutcome::UpToDate {
                        current_version: coperceive_desktop::updater::app_version(&check_handle.package_info().version),
                    };
                    Ok(serde_json::to_value(&outcome).unwrap_or_default())
                }
                Err(err) => {
                    match &err {
                        tauri_plugin_updater::Error::ReleaseNotFound => {
                            let outcome = coperceive_desktop::updater::UpdateCheckOutcome::UpToDate {
                                current_version: coperceive_desktop::updater::app_version(&check_handle.package_info().version),
                            };
                            Ok(serde_json::to_value(&outcome).unwrap_or_default())
                        }
                        _ => {
                            let is_network = matches!(
                                err,
                                tauri_plugin_updater::Error::Reqwest(_) | tauri_plugin_updater::Error::Network(_)
                            );
                            let outcome = coperceive_desktop::updater::UpdateCheckOutcome::Error {
                                error: err.to_string(),
                                is_network,
                            };
                            Ok(serde_json::to_value(&outcome).unwrap_or_default())
                        }
                    }
                }
            }
        }
    }));

    let install_handle = app_handle.clone();
    let install_state = app_state.clone();
    cfg.updater_installer = Some(Arc::new(move |override_url_opt: Option<String>| {
        {
            let update_opt = {
                let lock = install_state.pending_update.lock().map_err(|e| e.to_string())?;
                lock.clone()
            };
            let update = match update_opt {
                Some(u) => u,
                None => {
                    use tauri_plugin_updater::UpdaterExt;
                    let effective_url = coperceive_desktop::updater::resolve_update_endpoint_override(override_url_opt.as_deref());
                    let builder = if let Some(ref override_url) = effective_url {
                        if let Ok(parsed) = tauri::Url::parse(override_url) {
                            install_handle.updater_builder().endpoints(vec![parsed]).unwrap_or_else(|_| install_handle.updater_builder())
                        } else {
                            install_handle.updater_builder()
                        }
                    } else {
                        install_handle.updater_builder()
                    };
                    let updater = builder.build().map_err(|e| e.to_string())?;
                    let u = tauri::async_runtime::block_on(updater.check()).map_err(|e| e.to_string())?;
                    u.ok_or_else(|| "No update available to install".to_string())?
                }
            };

            if coperceive_desktop::test_seams::test_env_active() && update.signature.is_empty() {
                return Ok(());
            }

            let download_result = tauri::async_runtime::block_on(async {
                update.download(|_chunk, _total| {}, || {}).await
            });

            match download_result {
                Ok(bytes) => {
                    if coperceive_desktop::test_seams::test_env_active() {
                        return Ok(());
                    }
                    update.install(&bytes).map_err(|e| format!("Installation failed: {e}"))?;
                    install_handle.restart();
                    #[allow(unreachable_code)]
                    Ok(())
                }
                Err(err) => {
                    Err(format!("Update rejected: {err}"))
                }
            }
        }
    }));

    let cfg_clone = cfg.clone();
    let handle = app_handle.clone();
    let st = app_state.clone();

    let (port, _) = controller::spawn_controller(cfg, move || {
        let h = handle.clone();
        let s = st.clone();
        tauri::async_runtime::spawn(async move {
            trigger_open_dataset_dialog(&h, &s).await;
        });
    })?;

    if let Ok(mut lock) = app_state.ctrl_port.lock() {
        *lock = Some(port);
    }
    if let Ok(path) = std::env::var("COPERCEIVE_CTRL_INFO_FILE") {
        // Test hook only. The file holds the loopback secret; the measurement
        // script reads it and must not print it.
        if let Some(parent) = Path::new(&path).parent() {
            let _ = fs::create_dir_all(parent);
        }
        let _ = fs::write(&path, format!("{port}\n{secret}\n"));
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let _ = fs::set_permissions(&path, fs::Permissions::from_mode(0o600));
        }
    }
    if let Ok(mut lock) = app_state.ctrl_secret.lock() {
        *lock = Some(secret);
    }
    if let Ok(mut lock) = app_state.controller_config.lock() {
        *lock = Some(cfg_clone);
    }

    Ok(port)
}

#[cfg(target_os = "macos")]
fn build_app_menu(
    app: &tauri::AppHandle,
    recent_roots: &[String],
) -> Result<tauri::menu::Menu<tauri::Wry>, tauri::Error> {
    use tauri::menu::*;

    // App Submenu
    let app_submenu = SubmenuBuilder::new(app, "Coperceive")
        .item(&MenuItem::with_id(app, "about_coperceive", "About Coperceive", true, None::<&str>)?)
        .separator()
        .item(&MenuItem::with_id(app, "settings", "Settings…", true, Some("CmdOrControl+,"))?)
        .separator()
        .quit()
        .build()?;

    // File Submenu
    let mut file_builder = SubmenuBuilder::new(app, "File")
        .item(&MenuItem::with_id(app, "open_dataset", "Open Dataset…", true, Some("CmdOrControl+O"))?)
        .item(&MenuItem::with_id(app, "open_hpc_dataset", "Open HPC Dataset…", true, None::<&str>)?)
        .item(&MenuItem::with_id(app, "get_data", "Get Data…", true, Some("CmdOrControl+G"))?)
        .item(&MenuItem::with_id(
            app,
            "import_folder",
            "Import from folder…",
            true,
            None::<&str>,
        )?);

    // Open Recent Submenu
    let mut recent_builder = SubmenuBuilder::new(app, "Open Recent");
    if recent_roots.is_empty() {
        recent_builder = recent_builder.item(&MenuItem::with_id(app, "no_recent", "No Recent Datasets", false, None::<&str>)?);
    } else {
        for (idx, r) in recent_roots.iter().enumerate() {
            let label = Path::new(r).file_name().and_then(|n| n.to_str()).unwrap_or(r);
            recent_builder = recent_builder.item(&MenuItem::with_id(app, format!("recent_{idx}"), label, true, None::<&str>)?);
        }
    }
    let recent_submenu = recent_builder.build()?;
    file_builder = file_builder.item(&recent_submenu)
        .separator()
        .close_window();
    let file_submenu = file_builder.build()?;

    // Edit Submenu
    let edit_submenu = SubmenuBuilder::new(app, "Edit")
        .item(&PredefinedMenuItem::undo(app, None::<&str>)?)
        .item(&PredefinedMenuItem::redo(app, None::<&str>)?)
        .separator()
        .item(&PredefinedMenuItem::cut(app, None::<&str>)?)
        .item(&PredefinedMenuItem::copy(app, None::<&str>)?)
        .item(&PredefinedMenuItem::paste(app, None::<&str>)?)
        .item(&PredefinedMenuItem::select_all(app, None::<&str>)?)
        .build()?;

    // View Submenu
    let view_submenu = SubmenuBuilder::new(app, "View")
        .item(&MenuItem::with_id(app, "view_library", "Library", true, None::<&str>)?)
        .item(&MenuItem::with_id(app, "view_get_data", "Get Data", true, None::<&str>)?)
        .item(&MenuItem::with_id(app, "view_open_folder", "Open Folder", true, None::<&str>)?)
        .item(&MenuItem::with_id(app, "view_hpc_workspace", "HPC Workspace", true, None::<&str>)?)
        .separator()
        .item(&MenuItem::with_id(app, "toggle_sidebar", "Toggle Sidebar", true, Some("CmdOrControl+\\"))?)
        .separator()
        .item(&MenuItem::with_id(app, "viewpoint_orbit", "Orbit", true, Some("1"))?)
        .item(&MenuItem::with_id(app, "viewpoint_chase", "Chase", true, Some("2"))?)
        .item(&MenuItem::with_id(app, "viewpoint_drone", "Drone", true, Some("3"))?)
        .item(&MenuItem::with_id(app, "viewpoint_bird", "Bird", true, Some("4"))?)
        .separator()
        .item(&MenuItem::with_id(app, "toggle_fullscreen", "Enter Full Screen", true, Some("F"))?)
        .separator()
        .item(&MenuItem::with_id(app, "toggle_theme", "Toggle Theme", true, Some("CmdOrControl+T"))?)
        .separator()
        .item(&MenuItem::with_id(app, "zoom_reset", "Actual Size", true, Some("CmdOrControl+0"))?)
        .item(&MenuItem::with_id(app, "zoom_in", "Zoom In", true, Some("CmdOrControl+="))?)
        .item(&MenuItem::with_id(app, "zoom_out", "Zoom Out", true, Some("CmdOrControl+-"))?)
        .build()?;

    // Playback Submenu
    let playback_submenu = SubmenuBuilder::new(app, "Playback")
        .item(&MenuItem::with_id(app, "playback_toggle", "Play / Pause", true, Some("Space"))?)
        .item(&MenuItem::with_id(app, "playback_step_forward", "Step Forward", true, Some("]"))?)
        .item(&MenuItem::with_id(app, "playback_step_backward", "Step Backward", true, Some("["))?)
        .separator()
        .item(&MenuItem::with_id(app, "playback_speed_05", "0.5x Speed", true, None::<&str>)?)
        .item(&MenuItem::with_id(app, "playback_speed_10", "1.0x Speed", true, None::<&str>)?)
        .item(&MenuItem::with_id(app, "playback_speed_20", "2.0x Speed", true, None::<&str>)?)
        .build()?;

    // Help Submenu
    let help_submenu = SubmenuBuilder::new(app, "Help")
        .item(&MenuItem::with_id(app, "check_updates", "Check for Updates…", true, None::<&str>)?)
        .separator()
        .item(&MenuItem::with_id(app, "help_logs", "Show Logs", true, None::<&str>)?)
        .item(&MenuItem::with_id(app, "help_diagnostics", "Copy Diagnostics", true, None::<&str>)?)
        .separator()
        .item(&MenuItem::with_id(app, "help_docs", "Documentation", true, None::<&str>)?)
        .item(&MenuItem::with_id(app, "help_repo", "GitHub Repository", true, None::<&str>)?)
        .item(&MenuItem::with_id(app, "help_issue", "Report Issue", true, None::<&str>)?)
        .build()?;

    MenuBuilder::new(app)
        .items(&[
            &app_submenu,
            &file_submenu,
            &edit_submenu,
            &view_submenu,
            &playback_submenu,
            &help_submenu,
        ])
        .build()
}

/// The library's shared maps root, passed to the local server so maps installed
/// from the Library card (library_root/maps) are found even when the server root
/// is a single scene directory.
fn library_maps_root() -> Option<PathBuf> {
    let persistent = state::load_state();
    let root = persistent
        .library_root
        .map(PathBuf::from)
        .or_else(|| {
            let default = state::default_library_root();
            default.is_dir().then_some(default)
        })?;
    // Pass it even before the directory exists: the server resolves it lazily,
    // and an install during this session must be visible without a restart.
    Some(root.join("maps"))
}

async fn start_local_session(
    app: &tauri::AppHandle,
    state: &AppState,
    data_root: String,
) -> Result<SessionPayload, String> {
    let root_path = PathBuf::from(&data_root);
    if let Err(msg) = state::validate_dataset_root(&root_path) {
        return Err(msg);
    }

    // Stop existing session if one was running
    {
        let mut lock = state.sidecar.lock().map_err(|e| e.to_string())?;
        if let Some(mut existing) = lock.take() {
            let _ = existing.stop();
        }
        if let Ok(mut current) = state.stream_key.lock() {
            *current = None;
        }
    }

    let python_bin = resolve_python_binary(app);
    let mut session =
        SidecarSession::spawn_with_maps(&python_bin, &root_path, library_maps_root().as_deref(), None)
        .map_err(|e| format!("Failed to spawn local sidecar: {}", e))?;

    let origin = format!("http://127.0.0.1:{}", session.port);
    let token = session.token.clone();

    if let Ok(lock) = state.controller_config.lock() {
        if let Some(ref cfg) = *lock {
            cfg.set_allowed_origin(origin.clone());
        }
    }

    // Poll until healthy (up to 60 seconds)
    let start_time = Instant::now();
    if let Err(e) = reqwest_like_poll(&origin, &token, Duration::from_secs(60)) {
        // Prevent deletion of log on failed start
        session.preserve_log = true;
        let mut log_tail = String::new();
        if let Ok(content) = fs::read_to_string(&session.log_path) {
            let lines: Vec<&str> = content.lines().collect();
            let start = lines.len().saturating_sub(20);
            log_tail = lines[start..].join("\n");
        }
        // Copy serve-<port>.log to log dir
        {
            let log_dir = platform::get_log_dir();
            let _ = fs::create_dir_all(&log_dir);
            let dest_log = log_dir.join(format!("serve-{}.log", session.port));
            let _ = fs::copy(&session.log_path, &dest_log);
        }
        let tail_msg = if log_tail.trim().is_empty() {
            format!("(Log file {} was empty or could not be read)", session.log_path.display())
        } else {
            format!("Last 20 lines of server log ({}):\n{}", session.log_path.display(), log_tail)
        };
        let _ = session.stop();
        return Err(format!("Timed out waiting for local serve: {}\n\n{}", e, tail_msg));
    }

    let elapsed = start_time.elapsed();
    println!("[local-serve] Local server started and became healthy in {:.2?}", elapsed);
    {
        let log_dir = platform::get_log_dir();
        let _ = fs::create_dir_all(&log_dir);
        let log_file = log_dir.join("desktop.log");
        if let Ok(mut f) = fs::OpenOptions::new().create(true).append(true).open(&log_file) {
            use std::io::Write;
            let _ = writeln!(f, "[startup] Local server at {} became healthy in {:.2?}", origin, elapsed);
        }
    }

    // Persist new root to state.json and update recent_roots
    {
        let mut persistent = state::load_state();
        persistent.last_data_root = Some(data_root.clone());
        state::add_recent_root(&mut persistent, &data_root);
        let _ = state::save_state(&persistent);

        // Update application menu with refreshed recent roots (macOS)
        #[cfg(target_os = "macos")]
        if let Ok(menu) = build_app_menu(app, &persistent.recent_roots) {
            let _ = app.set_menu(menu);
        }
    }

    let ctrl_port = state.ctrl_port.lock().ok().and_then(|p| *p).unwrap_or(0);

    // Open viewer window at session.origin with initialization_script
    if let Err(e) = open_viewer_window(app, &origin, &token, &data_root, ctrl_port, state, None) {
        session.preserve_log = true;
        let _ = session.stop();
        return Err(e);
    }

    let payload = SessionPayload {
        origin,
        token,
        server_version: "0.0.0".to_string(),
        data_root: data_root.clone(),
        target_type: "local".to_string(),
    };

    {
        let mut lock = state.sidecar.lock().map_err(|e| e.to_string())?;
        *lock = Some(session);
    }
    {
        let mut lock = state.current_root.lock().map_err(|e| e.to_string())?;
        *lock = Some(data_root);
    }

    Ok(payload)
}

#[tauri::command]
async fn start_local(
    app: tauri::AppHandle,
    state: tauri::State<'_, Arc<AppState>>,
    data_root: String,
    start_fresh: Option<bool>,
) -> Result<SessionPayload, String> {
    if start_fresh.unwrap_or(false) {
        let mut persistent = state::load_state();
        persistent.last_scene_id = None;
        persistent.last_sample_index = None;
        persistent.last_timestamp_us = None;
        persistent.agent_visibility = None;
        persistent.layer_toggles = Some(state::LayerToggles::default());
        persistent.selected_camera = None;
        persistent.split_view = None;
        persistent.panel_collapse = Some(state::PanelCollapseState::default());
        let _ = state::save_state(&persistent);
    }
    start_local_session(&app, state.inner(), data_root).await
}

#[tauri::command]
async fn stop_local(state: tauri::State<'_, Arc<AppState>>) -> Result<(), String> {
    let mut lock = state.sidecar.lock().map_err(|e| e.to_string())?;
    if let Some(mut session) = lock.take() {
        session.stop().map_err(|e| format!("Failed to stop sidecar: {}", e))?;
    }
    Ok(())
}

static IMPORT_PID: Mutex<Option<u32>> = Mutex::new(None);

/// One background library validation at a time.
static VALIDATION_RUNNING: AtomicBool = AtomicBool::new(false);

#[tauri::command]
fn import_from_folder(
    app: tauri::AppHandle,
    source: String,
    dest: String,
    images: String,
    media_roots: Vec<String>,
    griffin_root: Option<String>,
) -> Result<(), String> {
    use coperceive_desktop::pack::scene_pack_args;
    use std::io::{BufRead, BufReader};
    use std::process::{Command, Stdio};
    use tauri::Emitter;

    {
        let slot = IMPORT_PID.lock().map_err(|err| err.to_string())?;
        if slot.is_some() {
            return Err("an import is already running".into());
        }
    }
    let effective_dest = if dest.trim().is_empty() {
        let p = state::load_state();
        p.library_root
            .unwrap_or_else(|| state::default_library_root().to_string_lossy().into_owned())
    } else {
        dest
    };
    let args = scene_pack_args(
        &source,
        &effective_dest,
        &images,
        &media_roots,
        griffin_root.as_deref(),
    )?;
    let python = resolve_python_binary(&app);
    let mut child = Command::new(&python)
        .args(&args)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|err| format!("failed to start scene pack: {err}"))?;
    let pid = child.id();
    {
        let mut slot = IMPORT_PID.lock().map_err(|err| err.to_string())?;
        *slot = Some(pid);
    }
    let stdout = child.stdout.take();
    let stderr = child.stderr.take();
    std::thread::spawn(move || {
        let app_out = app.clone();
        let stdout_thread = std::thread::spawn(move || {
            if let Some(pipe) = stdout {
                for line in BufReader::new(pipe).lines().map_while(Result::ok) {
                    let payload = serde_json::from_str::<serde_json::Value>(&line)
                        .unwrap_or_else(|_| serde_json::json!({"event": "log", "line": line}));
                    let _ = app_out.emit("import-progress", payload);
                }
            }
        });
        let app_err = app.clone();
        let stderr_thread = std::thread::spawn(move || {
            if let Some(pipe) = stderr {
                for line in BufReader::new(pipe).lines().map_while(Result::ok) {
                    let _ = app_err.emit(
                        "import-progress",
                        serde_json::json!({"event": "log", "line": line}),
                    );
                }
            }
        });
        let status = child.wait();
        let _ = stdout_thread.join();
        let _ = stderr_thread.join();
        if let Ok(mut slot) = IMPORT_PID.lock() {
            if *slot == Some(pid) {
                *slot = None;
            }
        }
        let code = status.ok().and_then(|value| value.code()).unwrap_or(1);
        let _ = app.emit("import-progress", serde_json::json!({"event": "exit", "code": code}));
    });
    Ok(())
}

#[tauri::command]
fn cancel_import() -> Result<(), String> {
    let pid = {
        let mut slot = IMPORT_PID.lock().map_err(|err| err.to_string())?;
        slot.take()
    };
    if let Some(pid) = pid {
        let _ = platform::terminate_process(pid);
    }
    Ok(())
}

static DATASET_JOB_PID: Mutex<Option<u32>> = Mutex::new(None);

#[tauri::command]
fn library_thumbnail(scene_path: String) -> Result<Option<Vec<u8>>, String> {
    let p = state::load_state();
    let root = p
        .library_root
        .unwrap_or_else(|| state::default_library_root().to_string_lossy().into_owned());
    coperceive_desktop::library::read_thumbnail_bytes(Path::new(&root), &scene_path)
}

/// Open Folder's "Folder check": what a folder holds, read from its manifests.
#[tauri::command]
fn inspect_folder(path: String) -> coperceive_desktop::library::FolderCheck {
    let library_root = state::load_state().library_root.map(PathBuf::from);
    coperceive_desktop::library::inspect_folder(Path::new(&path), library_root.as_deref())
}

#[tauri::command]
fn get_storage_info() -> Result<coperceive_desktop::library::StorageStats, String> {
    let p = state::load_state();
    let is_configured = p.library_root.is_some();
    let root = p
        .library_root
        .unwrap_or_else(|| state::default_library_root().to_string_lossy().into_owned());
    Ok(coperceive_desktop::library::get_storage_stats(Path::new(&root), is_configured))
}

#[tauri::command]
fn set_library_root(new_root: String) -> Result<coperceive_desktop::library::StorageStats, String> {
    let mut p = state::load_state();
    p.library_root = Some(new_root.clone());
    let _ = state::save_state(&p);
    Ok(coperceive_desktop::library::get_storage_stats(Path::new(&new_root), true))
}

#[tauri::command]
fn move_library_root(new_root: String) -> Result<coperceive_desktop::library::StorageStats, String> {
    let mut p = state::load_state();
    let old_root = p
        .library_root
        .clone()
        .unwrap_or_else(|| state::default_library_root().to_string_lossy().into_owned());
    coperceive_desktop::library::move_library_scenes(Path::new(&old_root), Path::new(&new_root))
        .map_err(|e| format!("Failed to move library: {e}"))?;
    p.library_root = Some(new_root.clone());
    let _ = state::save_state(&p);
    Ok(coperceive_desktop::library::get_storage_stats(Path::new(&new_root), true))
}

/// One-click install of the official Town03 OpenDRIVE for a library scene:
/// download (pinned mirror + SHA), ingest as a shared map, align to the scene
/// poses, store under library_root/maps. Progress events are streamed as
/// `town-map-progress` payloads.
#[tauri::command]
async fn download_town_map(
    app: tauri::AppHandle,
    scene_path: String,
) -> Result<serde_json::Value, String> {
    tauri::async_runtime::spawn_blocking(move || {
        use std::io::{BufRead, BufReader, Read};
        use std::process::{Command, Stdio};
        use tauri::Emitter;

        let persistent = state::load_state();
        let library_root = persistent
            .library_root
            .unwrap_or_else(|| state::default_library_root().to_string_lossy().into_owned());
        let library_root = PathBuf::from(library_root)
            .canonicalize()
            .map_err(|error| format!("Could not open the library root: {error}"))?;
        let scene = PathBuf::from(&scene_path)
            .canonicalize()
            .map_err(|error| format!("Could not open the scene: {error}"))?;
        if !scene.starts_with(&library_root) {
            return Err("The scene is not inside the library root.".to_string());
        }
        let map_name = coperceive_desktop::library::scene_map_name(&scene)
            .ok_or_else(|| "This scene does not declare a CARLA map name.".to_string())?;
        if map_name != "Carla/Maps/Town03" {
            return Err(format!(
                "One-click download is implemented for Town03 only; this scene names {map_name}."
            ));
        }
        let maps_root = library_root.join("maps");
        let python = resolve_python_binary(&app);
        let mut child = Command::new(&python)
            .args([
                "-m",
                "coperceive.cli",
                "maps",
                "install-town03",
                "--maps",
                &maps_root.to_string_lossy(),
                "--scenes",
                &library_root.to_string_lossy(),
                "--progress-json",
            ])
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|error| format!("Could not start the map installer: {error}"))?;
        let stderr = child.stderr.take().map(|stderr| {
            std::thread::spawn(move || {
                let mut text = String::new();
                let mut reader = BufReader::new(stderr);
                let _ = reader.read_to_string(&mut text);
                text
            })
        });
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| "Could not read the map installer output.".to_string())?;
        let mut report: Option<serde_json::Value> = None;
        for line in BufReader::new(stdout).lines() {
            let line = line.map_err(|error| format!("Could not read map progress: {error}"))?;
            if line.trim().is_empty() {
                continue;
            }
            let event: serde_json::Value = match serde_json::from_str(&line) {
                Ok(value) => value,
                Err(_) => {
                    let _ = app.emit(
                        "town-map-progress",
                        serde_json::json!({"event": "log", "message": line}),
                    );
                    continue;
                }
            };
            if event.get("event").and_then(|value| value.as_str()) == Some("result") {
                report = event.get("report").cloned();
            } else {
                let _ = app.emit("town-map-progress", event);
            }
        }
        let status = child
            .wait()
            .map_err(|error| format!("The map installer did not finish: {error}"))?;
        let stderr = stderr
            .and_then(|handle| handle.join().ok())
            .unwrap_or_default();
        if !status.success() {
            return Err(format!(
                "Town03 map install failed: {}",
                stderr.trim().lines().last().unwrap_or("unknown error")
            ));
        }
        report.ok_or_else(|| "The map installer returned no result.".to_string())
    })
    .await
    .map_err(|error| format!("Town03 map install failed: {error}"))?
}

#[tauri::command]
fn get_library_scenes(
    app: tauri::AppHandle,
) -> Result<Vec<coperceive_desktop::library::LibrarySceneInfo>, String> {
    let p = state::load_state();
    let root = p
        .library_root
        .unwrap_or_else(|| state::default_library_root().to_string_lossy().into_owned());
    let cache = p.validation_cache.unwrap_or_default();
    let scenes = coperceive_desktop::library::scan_library_scenes_with_cache(Path::new(&root), &cache);
    let pending = scenes
        .iter()
        .map(|scene| (scene.path.clone(), scene.is_valid.is_some()))
        .collect();
    spawn_library_validation(app, pending);
    Ok(scenes)
}

#[tauri::command]
fn delete_library_scene(scene_path: String) -> Result<(), String> {
    coperceive_desktop::library::delete_scene_to_trash(Path::new(&scene_path))
        .map_err(|e| format!("Failed to delete scene: {e}"))
}

#[tauri::command]
fn reveal_in_finder(path: String) -> Result<(), String> {
    platform::reveal_path(Path::new(&path))
}

#[tauri::command]
fn dataset_dry_run(
    app: tauri::AppHandle,
    dataset_id: String,
    preset_id: String,
) -> Result<serde_json::Value, String> {
    use std::process::Command;

    // Test-only: the e2e cannot wait for the Hugging Face tree listing, so the
    // deterministic fake job returns the same JSON shape without network access.
    // Inert unless COPERCEIVE_TEST_FAKE_DATASET_JOB is set in test mode.
    if coperceive_desktop::test_seams::fake_dataset_job_enabled() {
        return Ok(coperceive_desktop::test_seams::fake_dry_run_result(
            &dataset_id, &preset_id,
        ));
    }

    let p = state::load_state();
    let root = p
        .library_root
        .unwrap_or_else(|| state::default_library_root().to_string_lossy().into_owned());
    let python = resolve_python_binary(&app);
    let output = Command::new(&python)
        .args([
            "-m",
            "coperceive.cli",
            "dataset",
            "dry-run",
            &dataset_id,
            &preset_id,
            "--dest",
            &root,
        ])
        .output()
        .map_err(|e| format!("Failed to execute dry-run: {e}"))?;

    let stdout_str = String::from_utf8_lossy(&output.stdout);
    if let Some(start_idx) = stdout_str.find('{') {
        let json_part = &stdout_str[start_idx..];
        if let Ok(val) = serde_json::from_str::<serde_json::Value>(json_part) {
            return Ok(val);
        }
    }
    Err(format!(
        "Dry-run failed: {}",
        String::from_utf8_lossy(&output.stderr)
    ))
}

#[tauri::command]
fn start_dataset_job(
    app: tauri::AppHandle,
    dataset_id: String,
    preset_id: String,
    accept_terms: bool,
) -> Result<(), String> {
    use std::io::{BufRead, BufReader};
    use std::process::{Command, Stdio};
    use tauri::Emitter;

    {
        let slot = DATASET_JOB_PID.lock().map_err(|err| err.to_string())?;
        if slot.is_some() {
            return Err("a dataset job is already running".into());
        }
    }
    let fake_job = coperceive_desktop::test_seams::fake_dataset_job_enabled();
    let p = state::load_state();
    let root = p
        .library_root
        .unwrap_or_else(|| state::default_library_root().to_string_lossy().into_owned());
    if !fake_job {
        let mut persistent = state::load_state();
        persistent.pending_download = Some(state::PendingDownload {
            dataset_id: dataset_id.clone(),
            preset_id: preset_id.clone(),
        });
        let _ = state::save_state(&persistent);
    }
    let mut command = if fake_job {
        // Test-only: the e2e pauses, resumes and cancels a real child process,
        // but that child emits NDJSON locally instead of downloading Griffin.
        // Inert unless COPERCEIVE_TEST_FAKE_DATASET_JOB is set in test mode.
        let mut command = Command::new("/bin/sh");
        command
            .arg("-c")
            .arg(coperceive_desktop::test_seams::FAKE_DATASET_JOB_SCRIPT);
        command
    } else {
        let python = resolve_python_binary(&app);
        let mut cmd_args = vec![
            "-m".to_string(),
            "coperceive.cli".to_string(),
            "dataset".to_string(),
            "download".to_string(),
            dataset_id,
            preset_id,
            "--dest".to_string(),
            root,
        ];
        if accept_terms {
            cmd_args.push("--accept-terms".to_string());
        }
        let mut command = Command::new(&python);
        command.args(&cmd_args);
        command
    };

    let mut child = command
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|err| format!("failed to start dataset job: {err}"))?;
    let pid = child.id();
    {
        let mut slot = DATASET_JOB_PID.lock().map_err(|err| err.to_string())?;
        *slot = Some(pid);
    }
    let stdout = child.stdout.take();
    let stderr = child.stderr.take();
    std::thread::spawn(move || {
        let app_out = app.clone();
        let stdout_thread = std::thread::spawn(move || {
            if let Some(pipe) = stdout {
                for line in BufReader::new(pipe).lines().map_while(Result::ok) {
                    let payload = serde_json::from_str::<serde_json::Value>(&line)
                        .unwrap_or_else(|_| serde_json::json!({"event": "log", "line": line}));
                    if payload.get("event").and_then(|value| value.as_str()) == Some("complete") {
                        let mut persistent = state::load_state();
                        persistent.pending_download = None;
                        let _ = state::save_state(&persistent);
                    }
                    let _ = app_out.emit("dataset-progress", payload);
                }
            }
        });
        let app_err = app.clone();
        let stderr_thread = std::thread::spawn(move || {
            if let Some(pipe) = stderr {
                for line in BufReader::new(pipe).lines().map_while(Result::ok) {
                    let _ = app_err.emit(
                        "dataset-progress",
                        serde_json::json!({"event": "log", "line": line}),
                    );
                }
            }
        });
        let status = child.wait();
        let _ = stdout_thread.join();
        let _ = stderr_thread.join();
        if let Ok(mut slot) = DATASET_JOB_PID.lock() {
            if *slot == Some(pid) {
                *slot = None;
            }
        }
        let code = status.ok().and_then(|value| value.code()).unwrap_or(1);
        let _ = app.emit("dataset-progress", serde_json::json!({"event": "exit", "code": code}));
    });
    Ok(())
}

#[tauri::command]
fn cancel_dataset_job() -> Result<(), String> {
    let pid = {
        let mut slot = DATASET_JOB_PID.lock().map_err(|err| err.to_string())?;
        slot.take()
    };
    if let Some(pid) = pid {
        let _ = platform::terminate_process(pid);
    }
    Ok(())
}

#[tauri::command]
fn pause_dataset_job() -> Result<(), String> {
    let slot = DATASET_JOB_PID.lock().map_err(|err| err.to_string())?;
    if let Some(pid) = *slot {
        platform::pause_process(pid)
    } else {
        Err("No download is running.".into())
    }
}

#[tauri::command]
fn resume_dataset_job() -> Result<(), String> {
    let slot = DATASET_JOB_PID.lock().map_err(|err| err.to_string())?;
    if let Some(pid) = *slot {
        platform::resume_process(pid)
    } else {
        Err("No download is running.".into())
    }
}

#[tauri::command]
fn dataset_catalog(app: tauri::AppHandle) -> Result<serde_json::Value, String> {
    let python = resolve_python_binary(&app);
    let output = std::process::Command::new(&python)
        .args(["-m", "coperceive.cli", "dataset", "catalog"])
        .output()
        .map_err(|err| format!("Could not read the dataset list. Try again. {err}"))?;
    if !output.status.success() {
        return Err(format!(
            "Could not read the dataset list. {}",
            String::from_utf8_lossy(&output.stderr)
        ));
    }
    serde_json::from_slice(&output.stdout)
        .map_err(|err| format!("Could not read the dataset list. {err}"))
}

#[tauri::command]
fn set_auto_download_updates(enabled: bool) -> Result<(), String> {
    let mut persistent = state::load_state();
    persistent.auto_download_updates = enabled;
    state::save_state(&persistent)
}

fn run_scene_validation(
    python: &Path,
    scene_path: &str,
) -> Result<(bool, String, String, SceneTiming), String> {
    use std::process::Command;

    let report_dir = std::env::temp_dir().join(format!(
        "coperceive-validate-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|value| value.as_nanos())
            .unwrap_or(0)
    ));
    fs::create_dir_all(&report_dir)
        .map_err(|err| format!("Failed to create the validation report directory: {err}"))?;
    let json_path = report_dir.join("report.json");
    let markdown_path = report_dir.join("report.md");
    let output = Command::new(python)
        .args(["-m", "coperceive.cli", "validate", scene_path])
        .arg("--json")
        .arg(&json_path)
        .arg("--markdown")
        .arg(&markdown_path)
        .output()
        .map_err(|e| format!("Failed to validate scene: {e}"))?;
    let stdout = String::from_utf8_lossy(&output.stdout).into_owned();
    let stderr = String::from_utf8_lossy(&output.stderr).into_owned();
    if !output.status.success() {
        let _ = fs::remove_dir_all(&report_dir);
        return Err(format!(
            "Validation did not finish. It exited with {}. {}",
            output.status, stderr
        ));
    }
    let parsed: serde_json::Value = fs::read_to_string(&json_path)
        .ok()
        .and_then(|text| serde_json::from_str(&text).ok())
        .ok_or_else(|| "Validation finished but wrote no readable report.".to_string())?;
    let _ = fs::remove_dir_all(&report_dir);
    let aggregate = parsed.get("aggregate");
    let read = aggregate
        .and_then(|value| value.get("scenes_read"))
        .and_then(|value| value.as_u64())
        .unwrap_or(0);
    let failed = aggregate
        .and_then(|value| value.get("scenes_failed"))
        .and_then(|value| value.as_u64())
        .unwrap_or(1);
    let hard = aggregate
        .and_then(|value| value.get("hard_failure_count"))
        .and_then(|value| value.as_u64())
        .unwrap_or(1);
    Ok((read == 1 && failed == 0 && hard == 0, stdout, stderr, SceneTiming::from_report(&parsed)))
}

/// Frames per agent and pose time range of the one scene a validate report read.
#[derive(Default)]
struct SceneTiming {
    frames: Option<usize>,
    time_range_us: Option<(i64, i64)>,
}

impl SceneTiming {
    fn from_report(report: &serde_json::Value) -> Self {
        let scene = report.pointer("/scenes/0");
        let frames = scene
            .and_then(|scene| scene.get("frames"))
            .and_then(serde_json::Value::as_object)
            .and_then(|frames| frames.values().filter_map(serde_json::Value::as_u64).max())
            .map(|n| n as usize);
        let time_range_us = scene
            .and_then(|scene| scene.get("time_range_us"))
            .and_then(serde_json::Value::as_array)
            .and_then(|range| Some((range.first()?.as_i64()?, range.get(1)?.as_i64()?)));
        Self { frames, time_range_us }
    }
}

/// Remember a validation answer, keyed by the scene directory and manifest mtime.
fn remember_scene_validation(scene_path: &str, valid: bool, timing: SceneTiming) {
    let Some(mtime) = coperceive_desktop::library::manifest_mtime_ms(Path::new(scene_path)) else {
        return;
    };
    let mut persistent = state::load_state();
    let cache = persistent
        .validation_cache
        .get_or_insert_with(std::collections::HashMap::new);
    cache.insert(
        scene_path.to_string(),
        state::ValidationRecord {
            valid,
            manifest_mtime_ms: mtime,
            frames: timing.frames,
            time_range_us: timing.time_range_us,
        },
    );
    if let Err(err) = state::save_state(&persistent) {
        eprintln!("[validation] could not save the validation cache: {err}");
    }
}

/// Validate a scene in the background and remember the answer in `state.json`.
fn store_scene_validation(python: &Path, scene_path: &str) -> Result<bool, String> {
    let (valid, _, _, timing) = run_scene_validation(python, scene_path)?;
    remember_scene_validation(scene_path, valid, timing);
    Ok(valid)
}

/// Validate stale library scenes in the background. One run at a time.
fn spawn_library_validation(app: tauri::AppHandle, scenes: Vec<(String, bool)>) {
    if scenes.iter().all(|(_, fresh)| *fresh) {
        return;
    }
    if VALIDATION_RUNNING.swap(true, Ordering::SeqCst) {
        return;
    }
    std::thread::spawn(move || {
        let python = resolve_python_binary(&app);
        for (scene_path, fresh) in scenes {
            if fresh {
                continue;
            }
            let _ = store_scene_validation(&python, &scene_path);
        }
        VALIDATION_RUNNING.store(false, Ordering::SeqCst);
    });
}

#[tauri::command]
fn validate_scene(
    app: tauri::AppHandle,
    scene_path: String,
) -> Result<serde_json::Value, String> {
    let python = resolve_python_binary(&app);
    let (valid, stdout, stderr, timing) = run_scene_validation(&python, &scene_path)?;
    remember_scene_validation(&scene_path, valid, timing);
    Ok(serde_json::json!({
        "scene_path": scene_path,
        "valid": valid,
        "stdout": stdout,
        "stderr": stderr,
    }))
}

#[tauri::command]
async fn health(state: tauri::State<'_, Arc<AppState>>) -> Result<HealthPayload, String> {
    let mut lock = state.sidecar.lock().map_err(|e| e.to_string())?;
    if let Some(session) = lock.as_mut() {
        if session.is_alive() {
            return Ok(HealthPayload {
                healthy: true,
                latency_ms: 1,
                server_version: Some("0.0.0".to_string()),
            });
        }
    }
    Ok(HealthPayload {
        healthy: false,
        latency_ms: 0,
        server_version: None,
    })
}

/// Local ssh binary. `COPERCEIVE_SSH_BIN` is used by tests and the CI
/// packaged-app e2e harness; production uses the system ssh.
fn ssh_bin() -> String {
    std::env::var("COPERCEIVE_SSH_BIN").unwrap_or_else(|_| "ssh".to_string())
}

fn ssh_config_path() -> PathBuf {
    std::env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".ssh")
        .join("config")
}

/// Lists hosts from the user's `~/.ssh/config` so "Add server" can offer them.
/// Only Host aliases are read; no key material or options are returned.
#[tauri::command]
async fn list_ssh_config_hosts() -> Result<Vec<String>, String> {
    let path = ssh_config_path();
    let contents = match fs::read_to_string(&path) {
        Ok(contents) => contents,
        Err(_) => return Ok(Vec::new()),
    };
    Ok(server_profiles::parse_ssh_config_hosts(&contents))
}

/// Reads effective settings for a `~/.ssh/config` alias with `ssh -G`.
#[tauri::command]
async fn read_ssh_host_settings(alias: String) -> Result<SshHostSettings, String> {
    detect::probe_ssh_alias(&alias, &ssh_bin())
}

#[tauri::command]
async fn list_server_profiles() -> Result<Vec<ServerEntry>, String> {
    Ok(server_profiles::load_servers())
}

/// Saves a server profile locally. Rejects secret-like fields and unsafe paths.
#[tauri::command]
async fn save_server_profile(entry: ServerEntry) -> Result<ServerEntry, String> {
    let mut servers = server_profiles::load_servers();
    server_profiles::upsert_server(&mut servers, entry)
}

#[tauri::command]
async fn delete_server_profile(id: String) -> Result<(), String> {
    let mut servers = server_profiles::load_servers();
    server_profiles::remove_server(&mut servers, &id)
}

/// Runs the read-only "Test connection" checks off the async runtime, bounded by
/// per-check timeouts. The result can be applied to the draft profile in the UI.
#[tauri::command]
async fn test_server_connection(entry: ServerEntry) -> Result<DetectionReport, String> {
    tauri::async_runtime::spawn_blocking(move || {
        detect::detect_server(&entry, &DetectOptions { ssh_bin: ssh_bin() })
    })
    .await
    .map_err(|e| format!("The connection test could not finish: {e}"))
}

/// The saved server profile a plan/command belongs to.
fn find_server_entry(profile_id: &str) -> Result<ServerEntry, String> {
    server_profiles::load_servers()
        .into_iter()
        .find(|entry| entry.id == profile_id)
        .ok_or_else(|| "This server is no longer saved. Add it again, then retry.".to_string())
}

/// SSH runner for a saved profile, using the user's own ssh config and agent.
fn plan_runner(entry: &ServerEntry) -> Result<SshRunner, String> {
    Ok(SshRunner::new(ssh_bin(), entry.ssh_target()?))
}

/// Read-only dataset detection for a raw path on the cluster.
#[tauri::command]
async fn dataset_detect(
    profile_id: String,
    path: String,
) -> Result<serde_json::Value, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let entry = find_server_entry(&profile_id)?;
        let runner = plan_runner(&entry)?;
        coperceive_desktop::cluster_datasets::detect(
            &runner,
            &entry.profile,
            &path,
        )
    })
    .await
    .map_err(|error| format!("Dataset detection could not finish: {error}"))?
}

/// Read-only scene index for a detected raw dataset.
#[tauri::command]
async fn dataset_index(
    profile_id: String,
    adapter: String,
    raw_root: String,
    split: Option<String>,
) -> Result<serde_json::Value, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let entry = find_server_entry(&profile_id)?;
        let runner = plan_runner(&entry)?;
        coperceive_desktop::cluster_datasets::index(
            &runner,
            &entry.profile,
            &adapter,
            &raw_root,
            split.as_deref(),
        )
    })
    .await
    .map_err(|error| format!("Dataset indexing could not finish: {error}"))?
}

/// Installed model plugins on the cluster, offered as built-in pipelines. A
/// cluster without plugins returns an empty list; the UI also accepts a custom
/// `coperceive-pipeline.json` script path.
#[tauri::command]
async fn list_pipeline_options(profile_id: String) -> Result<Vec<PipelineEntry>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let entry = find_server_entry(&profile_id)?;
        let runner = plan_runner(&entry)?;
        Ok::<Vec<PipelineEntry>, String>(run_plan::list_builtin_pipelines(&runner, &entry.profile))
    })
    .await
    .map_err(|error| format!("Could not list pipelines: {error}"))?
}

/// Reads a custom pipeline registration (`coperceive-pipeline.json` next to the
/// script) read-only over SSH.
#[tauri::command]
async fn read_custom_pipeline(
    profile_id: String,
    script_path: String,
) -> Result<PipelineEntry, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let entry = find_server_entry(&profile_id)?;
        let runner = plan_runner(&entry)?;
        run_plan::load_pipeline_registration(&runner, &entry.profile, &script_path)
    })
    .await
    .map_err(|error| format!("Could not read the pipeline registration: {error}"))?
}

/// Builds and checks a run plan. Nothing is submitted: the plan only renders the
/// argv, environment, checks and the contract-check script for the user to see.
#[tauri::command]
async fn build_run_plan(
    profile_id: String,
    request: RunPlanRequest,
) -> Result<RunPlan, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let entry = find_server_entry(&profile_id)?;
        let runner = plan_runner(&entry)?;
        run_plan::build_run_plan(&runner, &entry, &request)
    })
    .await
    .map_err(|error| format!("Could not build the run plan: {error}"))?
}

/// Saves a checked plan under the profile's `results_root/plans/<id>.json`.
/// Nothing is submitted.
#[tauri::command]
async fn save_run_plan(
    profile_id: String,
    plan: RunPlan,
    request: RunPlanRequest,
) -> Result<RunPlan, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let entry = find_server_entry(&profile_id)?;
        let runner = plan_runner(&entry)?;
        run_plan::save_run_plan(&runner, &entry, &plan, &request)
    })
    .await
    .map_err(|error| format!("Could not save the run plan: {error}"))?
}

/// Renders the complete job script for a saved plan without submitting anything.
/// The UI shows this text and sends its `preview_hash` back on confirm.
#[tauri::command]
async fn preview_submission(
    profile_id: String,
    plan_id: String,
    options: SubmitOptions,
) -> Result<SubmissionPreview, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let entry = find_server_entry(&profile_id)?;
        let runner = plan_runner(&entry)?;
        let plan = job_submit::load_saved_plan(&runner, &entry.profile, &plan_id)?;
        job_submit::render_run_script(&entry.profile, &plan, &options)
    })
    .await
    .map_err(|error| format!("Could not render the submission script: {error}"))?
}

/// Submits a saved plan after the user explicitly confirmed the previewed
/// script. Unconfirmed calls and preview-hash mismatches submit nothing.
#[tauri::command]
async fn submit_run_plan(
    profile_id: String,
    plan_id: String,
    options: SubmitOptions,
    preview_hash: String,
    confirmed: bool,
) -> Result<JobSubmitOutcome, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let entry = find_server_entry(&profile_id)?;
        let runner = plan_runner(&entry)?;
        job_submit::submit_saved_plan(
            &runner,
            &entry.profile,
            &plan_id,
            &options,
            &preview_hash,
            confirmed,
        )
    })
    .await
    .map_err(|error| format!("Could not submit the run plan: {error}"))?
}

/// Live state, elapsed and remaining wall time for a run submitted by this app.
#[tauri::command]
async fn job_watch_status(
    profile_id: String,
    plan_id: String,
    job_id: String,
) -> Result<JobWatchStatus, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let entry = find_server_entry(&profile_id)?;
        let runner = plan_runner(&entry)?;
        job_watch::watch_status(&runner, &entry.profile, &plan_id, &job_id)
    })
    .await
    .map_err(|error| format!("Could not read the job state: {error}"))?
}

/// State of a dataset prepare job this app submitted (Queued -> Preparing ->
/// Ready in the dataset view); it must still carry the name the preview gave it.
#[tauri::command]
async fn dataset_prepare_job_status(
    profile_id: String,
    plan_id: String,
    job_id: String,
    job_name: String,
) -> Result<JobWatchStatus, String> {
    if !job_name.starts_with("coperceive-dataset-") {
        return Err("Not a dataset preparation job.".to_string());
    }
    tauri::async_runtime::spawn_blocking(move || {
        let entry = find_server_entry(&profile_id)?;
        let runner = plan_runner(&entry)?;
        job_watch::watch_named(&runner, &entry.profile, &plan_id, &job_id, &job_name)
    })
    .await
    .map_err(|error| format!("Could not read the job state: {error}"))?
}

/// A bounded tail (10..=200 lines) of the run's stdout or stderr log.
#[tauri::command]
async fn job_watch_logs(
    profile_id: String,
    plan_id: String,
    job_id: String,
    stream: String,
    lines: u32,
) -> Result<JobLogTail, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let entry = find_server_entry(&profile_id)?;
        let runner = plan_runner(&entry)?;
        job_watch::tail_log(&runner, &entry.profile, &plan_id, &job_id, &stream, lines)
    })
    .await
    .map_err(|error| format!("Could not read the job log: {error}"))?
}

/// Cancels a running/pending job this app submitted, after an explicit confirm.
#[tauri::command]
async fn cancel_run_job(
    profile_id: String,
    plan_id: String,
    job_id: String,
    confirmed: bool,
) -> Result<JobCancelResult, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let entry = find_server_entry(&profile_id)?;
        let runner = plan_runner(&entry)?;
        job_watch::cancel_job(&runner, &entry.profile, &plan_id, &job_id, confirmed)
    })
    .await
    .map_err(|error| format!("Could not cancel the job: {error}"))?
}

/// One bounded read of the streamed predictions file for a saved run plan.
#[tauri::command]
async fn prediction_stream(
    profile_id: String,
    plan_id: String,
    offset: u64,
    max_bytes: Option<u64>,
) -> Result<PredictionTail, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let entry = find_server_entry(&profile_id)?;
        let runner = plan_runner(&entry)?;
        let plan = job_submit::load_saved_plan(&runner, &entry.profile, &plan_id)?;
        job_report::tail_predictions(
            &runner,
            &entry.profile,
            &plan.output_dir,
            offset,
            max_bytes.unwrap_or(65536),
        )
    })
    .await
    .map_err(|error| format!("Could not read the prediction stream: {error}"))?
}

/// The pipeline's own final metrics report, once it exists.
#[tauri::command]
async fn run_metrics(
    profile_id: String,
    plan_id: String,
) -> Result<Option<MetricsReport>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let entry = find_server_entry(&profile_id)?;
        let runner = plan_runner(&entry)?;
        let plan = job_submit::load_saved_plan(&runner, &entry.profile, &plan_id)?;
        job_report::read_metrics(&runner, &entry.profile, &plan.output_dir)
    })
    .await
    .map_err(|error| format!("Could not read the metrics report: {error}"))?
}

/// Imports the run's predictions as a stored run and opens the viewer on it.
#[tauri::command]
async fn open_predictions(
    app: tauri::AppHandle,
    state: tauri::State<'_, Arc<AppState>>,
    profile_id: String,
    plan_id: String,
) -> Result<ImportedPredictions, String> {
    // The remote session owns the data root the viewer/server are using; copy
    // what the blocking import needs, then open the window on this thread.
    let (origin, token, data_root) = {
        let lock = state.remote.lock().map_err(|e| e.to_string())?;
        let mgr = lock.as_ref().ok_or_else(|| {
            "Open predictions needs an active remote session; connect to the server first."
                .to_string()
        })?;
        (mgr.origin(), mgr.token.clone(), mgr.data_root.clone())
    };
    let ctrl_port = state.ctrl_port.lock().ok().and_then(|port| *port).unwrap_or(0);
    let import_root = data_root.clone();
    let imported = tauri::async_runtime::spawn_blocking(move || {
        let entry = find_server_entry(&profile_id)?;
        let runner = plan_runner(&entry)?;
        let plan = job_submit::load_saved_plan(&runner, &entry.profile, &plan_id)?;
        job_report::import_predictions(
            &runner,
            &entry.profile,
            &plan_id,
            &plan.output_dir,
            &import_root,
        )
    })
    .await
    .map_err(|error| format!("Could not import the predictions: {error}"))??;
    let mut imported = imported;
    imported.url = format!("{}/#/viewer?predictions={}", origin, imported.run_id);
    let query = format!("predictions={}", imported.run_id);
    open_viewer_window(
        &app,
        &origin,
        &token,
        &data_root,
        ctrl_port,
        state.inner(),
        Some(&query),
    )?;
    Ok(imported)
}

/// The app's submitted runs from Slurm accounting, newest first.
#[tauri::command]
async fn list_run_history(
    profile_id: String,
    limit: Option<u32>,
) -> Result<Vec<RunHistoryEntry>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let entry = find_server_entry(&profile_id)?;
        let runner = plan_runner(&entry)?;
        job_history::list_run_history(
            &runner,
            &entry.profile,
            limit.unwrap_or(job_history::DEFAULT_HISTORY_LIMIT),
        )
    })
    .await
    .map_err(|error| format!("Could not read the run history: {error}"))?
}

/// Render the CPU-only Slurm script that prepares selected dataset scenes.
#[tauri::command]
async fn dataset_prepare(
    state: tauri::State<'_, Arc<AppState>>,
    profile_id: String,
    adapter: String,
    raw_root: String,
    split: Option<String>,
    scenes: Option<serde_json::Value>,
    workers: Option<u32>,
    images: Option<String>,
) -> Result<serde_json::Value, String> {
    // Without an explicit choice the preview starts from the user's setting for
    // this server (else the app-wide one, else originals).
    let images = images
        .filter(|mode| !mode.trim().is_empty())
        .unwrap_or_else(|| state::load_state().camera_images(Some(&profile_id)).effective);
    let scenes: Option<Vec<String>> = match scenes {
        None => None,
        Some(serde_json::Value::String(text)) if text == "all" => None,
        Some(serde_json::Value::Array(items)) => Some(
            items
                .iter()
                .filter_map(serde_json::Value::as_str)
                .map(str::to_string)
                .collect(),
        ),
        Some(_) => return Err("scenes must be a list of scene ids or \"all\".".to_string()),
    };
    let (preview, pending) = tauri::async_runtime::spawn_blocking(move || {
        let entry = find_server_entry(&profile_id)?;
        let runner = plan_runner(&entry)?;
        let index = coperceive_desktop::cluster_datasets::index(
            &runner,
            &entry.profile,
            &adapter,
            &raw_root,
            split.as_deref(),
        )?;
        if let Some(split) = split.as_deref() {
            if index.get("split").and_then(serde_json::Value::as_str) != Some(split) {
                return Err("The dataset split does not match the raw root.".to_string());
            }
        }
        let cache_relative = index
            .get("cache")
            .and_then(|cache| cache.get("relative_path"))
            .and_then(serde_json::Value::as_str)
            .ok_or_else(|| "The dataset index did not return a cache path.".to_string())?
            .to_string();
        let estimated = index
            .get("estimated_cache_bytes")
            .and_then(serde_json::Value::as_u64)
            .unwrap_or(0);
        // One array task per selected scene; "all" is every scene the index lists.
        let scene_ids: Vec<String> = scenes.clone().unwrap_or_else(|| {
            index
                .get("scenes")
                .and_then(serde_json::Value::as_array)
                .map(|rows| {
                    rows.iter()
                        .filter_map(|row| row.get("scene_id").and_then(serde_json::Value::as_str))
                        .map(str::to_string)
                        .collect()
                })
                .unwrap_or_default()
        });
        let workers = coperceive_desktop::dataset_jobs::resolve_workers(workers, scene_ids.len());
        let request = coperceive_desktop::dataset_jobs::DatasetPrepareRequest {
            adapter: adapter.clone(),
            raw_root: raw_root.clone(),
            split: index
                .get("split")
                .and_then(serde_json::Value::as_str)
                .map(str::to_string),
            scenes: scenes.clone(),
            workers,
            images,
        };
        let preview_bytes =
            coperceive_desktop::dataset_jobs::preview_bytes_estimate(&index, scenes.as_deref());
        let timing =
            coperceive_desktop::dataset_jobs::read_timing_log(&runner, &entry.profile, &cache_relative);
        let estimate = coperceive_desktop::dataset_jobs::estimate_seconds(
            &index,
            &scene_ids,
            &request.images,
            request.workers.min(scene_ids.len().max(1) as u32),
            &timing,
        );
        let preview = coperceive_desktop::dataset_jobs::render_prepare_script_with_estimate(
            &entry.profile,
            &request,
            &scene_ids,
            &cache_relative,
            estimated,
            preview_bytes,
            estimate,
        )?;
        let pending = coperceive_desktop::dataset_jobs::PendingPrepare {
            profile_id: entry.id.clone(),
            runner,
            request,
            preview: preview.clone(),
            script: preview.script.clone(),
        };
        Ok((preview, pending))
    })
    .await
    .map_err(|error| format!("Dataset preparation preview could not finish: {error}"))??;
    if let Ok(mut lock) = state.pending_dataset_prepare.lock() {
        *lock = Some(pending);
    }
    Ok(serde_json::json!({
        "plan_id": preview.plan_id,
        "job_name": preview.job_name,
        "partition": preview.partition,
        "walltime": preview.walltime,
        "cpus_per_task": preview.cpus_per_task,
        "array_tasks": preview.array_tasks,
        "throttle": preview.throttle,
        "estimated_seconds": preview.estimated_seconds,
        "estimate_note": preview.estimate_note,
        "gres": preview.gres,
        "estimated_cache_bytes": preview.estimated_cache_bytes,
        "images": preview.images,
        "estimated_preview_bytes": preview.estimated_preview_bytes,
        "cache_root": preview.cache_root,
        "script": preview.script,
        "preview_hash": preview.preview_hash,
    }))
}

#[tauri::command]
async fn dataset_prepare_submit(
    state: tauri::State<'_, Arc<AppState>>,
    profile_id: String,
    preview_hash: String,
    confirmed: bool,
) -> Result<serde_json::Value, String> {
    let pending = state
        .pending_dataset_prepare
        .lock()
        .ok()
        .and_then(|mut lock| lock.take());
    let pending = pending.ok_or_else(|| {
        "Generate the dataset preparation preview first. Nothing was submitted.".to_string()
    })?;
    if pending.profile_id != profile_id {
        return Err("The preparation preview belongs to a different server profile.".to_string());
    }
    tauri::async_runtime::spawn_blocking(move || {
        let entry = find_server_entry(&profile_id)?;
        coperceive_desktop::dataset_jobs::submit_prepared(
            &pending.runner,
            &entry,
            &pending,
            &preview_hash,
            confirmed,
        )
    })
    .await
    .map_err(|error| format!("Dataset preparation submit could not finish: {error}"))?
}

#[tauri::command]
async fn dataset_status(
    profile_id: String,
    adapter: String,
    raw_root: String,
    split: Option<String>,
) -> Result<serde_json::Value, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let entry = find_server_entry(&profile_id)?;
        let runner = plan_runner(&entry)?;
        let cache = coperceive_desktop::dataset_jobs::cache_absolute(
            &entry.profile,
            &adapter,
            &raw_root,
            split.as_deref(),
        )?;
        let command = coperceive_desktop::ssh::quote_remote_argv(
            &coperceive_desktop::hpc::coperceive_argv(
                &entry.profile,
                &[
                    "dataset".to_string(),
                    "status".to_string(),
                    "--cache".to_string(),
                    cache,
                ],
            ),
        );
        let result = coperceive_desktop::slurm_adapter::RemoteRunner::run(
            &runner,
            &command,
            std::time::Duration::from_secs(45),
            None,
        )?;
        let text = result.stdout.trim();
        let failure = match serde_json::from_str::<serde_json::Value>(text) {
            // An error document also carries the status schema: check the error first.
            Ok(document) => match document.get("error").and_then(serde_json::Value::as_str) {
                Some(error) => error.to_string(),
                None if document.get("schema").and_then(serde_json::Value::as_str)
                    == Some("coperceive-cluster-scene-status-1") =>
                {
                    return Ok(document);
                }
                None => "The cluster returned an unexpected dataset status document.".to_string(),
            },
            Err(_) => "The cluster returned an unexpected dataset status document.".to_string(),
        };
        Err(coperceive_desktop::cluster_datasets::explain_failure(
            &runner,
            &entry.profile,
            failure,
        ))
    })
    .await
    .map_err(|error| format!("Dataset status could not finish: {error}"))?
}

/// The open cluster dataset's scenes (id, state) from the dataset view, so the
/// viewer's scene list can show the ones that are not prepared yet.
#[tauri::command]
fn set_viewer_dataset_scenes(state: tauri::State<'_, Arc<AppState>>, dataset: serde_json::Value) {
    if let Ok(mut scenes) = state.dataset_scenes.lock() {
        *scenes = dataset;
    }
}

/// Remember the cluster dataset open in the HPC workspace (or forget it on
/// Close), so the workspace reopens it after a restart.
#[tauri::command]
fn remember_cluster_dataset(profile_id: String, dataset: Option<serde_json::Value>) -> Result<(), String> {
    let mut persistent = state::load_state();
    match dataset {
        Some(dataset) => {
            persistent.last_cluster_datasets.insert(profile_id.clone(), dataset);
            persistent.last_cluster_profile = Some(profile_id);
        }
        None => {
            persistent.last_cluster_datasets.remove(&profile_id);
            if persistent.last_cluster_profile.as_deref() == Some(profile_id.as_str()) {
                persistent.last_cluster_profile = None;
            }
        }
    }
    state::save_state(&persistent)
}

/// The dataset last open in the HPC workspace: `{ profile_id, dataset }`, or null.
#[tauri::command]
fn last_cluster_dataset() -> serde_json::Value {
    let persistent = state::load_state();
    persistent
        .last_cluster_profile
        .as_ref()
        .and_then(|id| {
            persistent
                .last_cluster_datasets
                .get(id)
                .map(|dataset| serde_json::json!({ "profile_id": id, "dataset": dataset }))
        })
        .unwrap_or(serde_json::Value::Null)
}

/// Settings → Cluster datasets → "Camera images when preparing".
#[tauri::command]
fn get_camera_images_setting(profile_id: Option<String>) -> state::CameraImagesSetting {
    state::load_state().camera_images(profile_id.as_deref())
}

/// Changes only what the next prepare preview starts with; prepared scenes keep
/// their mode. `mode: null` clears the per-profile (or app-wide) choice.
#[tauri::command]
fn set_camera_images_setting(
    profile_id: Option<String>,
    mode: Option<String>,
) -> Result<state::CameraImagesSetting, String> {
    let mut persistent = state::load_state();
    persistent.set_camera_images(profile_id.as_deref(), mode.as_deref())?;
    state::save_state(&persistent).map_err(|error| format!("Could not save the setting: {error}"))?;
    Ok(persistent.camera_images(profile_id.as_deref()))
}

/// Disk used by camera previews in a profile's cache; `remove` deletes only the
/// previews (the UI asks for confirmation first).
#[tauri::command]
async fn dataset_previews(profile_id: String, remove: bool) -> Result<serde_json::Value, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let entry = find_server_entry(&profile_id)?;
        let runner = plan_runner(&entry)?;
        coperceive_desktop::cluster_datasets::previews(&runner, &entry.profile, remove)
    })
    .await
    .map_err(|error| format!("Could not read the camera previews: {error}"))?
}

/// Where viewing over SSH keeps its local files: per-dataset mirrors of the
/// scenes' small files and the size-capped cache of sweeps and images. This is
/// the app's own cache directory; nothing is written on the cluster.
fn remote_view_cache_dir() -> PathBuf {
    if let Some(dir) = std::env::var_os("COPERCEIVE_REMOTE_VIEW_CACHE") {
        return PathBuf::from(dir);
    }
    std::env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
        .join("Library/Caches/nl.adarsh.coperceive/remote-view")
}

fn directory_bytes(dir: &Path) -> u64 {
    fs::read_dir(dir)
        .map(|entries| {
            entries
                .flatten()
                .map(|entry| match entry.metadata() {
                    Ok(meta) if meta.is_dir() => directory_bytes(&entry.path()),
                    Ok(meta) => meta.len(),
                    Err(_) => 0,
                })
                .sum()
        })
        .unwrap_or(0)
}

/// Settings → Cluster datasets → "Viewing cache": its cap, what it uses, and
/// `clear` to empty it (sweeps and images only; they are fetched again).
#[tauri::command]
fn remote_view_cache(cap_bytes: Option<u64>, clear: Option<bool>) -> Result<serde_json::Value, String> {
    let mut persistent = state::load_state();
    if let Some(cap) = cap_bytes {
        persistent.remote_view_cache_bytes = cap.max(state::MIN_REMOTE_VIEW_CACHE_BYTES);
        state::save_state(&persistent).map_err(|error| format!("Could not save the setting: {error}"))?;
    }
    let blocks = remote_view_cache_dir().join("blocks");
    if clear.unwrap_or(false) {
        if let Ok(entries) = fs::read_dir(&blocks) {
            for entry in entries.flatten() {
                let _ = fs::remove_file(entry.path());
            }
        }
    }
    Ok(serde_json::json!({
        "cap_bytes": persistent.remote_view_cache_bytes,
        "used_bytes": directory_bytes(&blocks),
    }))
}

/// Opens a prepared cluster dataset in the viewer with no Slurm job: a local
/// `coperceive serve` reads the scenes over one long-lived SSH connection to a
/// read-only streamer confined to this dataset's cache split and raw root
/// (docs/datasets/cluster-datasets.md, "Viewing"). Opening another scene of
/// the same dataset reuses the server and its connection.
#[tauri::command]
async fn dataset_open(
    app: tauri::AppHandle,
    state: tauri::State<'_, Arc<AppState>>,
    profile_id: String,
    adapter: String,
    raw_root: String,
    split: Option<String>,
    scene_id: Option<String>,
) -> Result<SessionPayload, String> {
    let entry = find_server_entry(&profile_id)?;
    let cache = coperceive_desktop::dataset_jobs::cache_absolute(
        &entry.profile,
        &adapter,
        &raw_root,
        split.as_deref(),
    )?;
    let target = entry.ssh_target()?;
    let key = format!("stream|{profile_id}|{cache}|{raw_root}");
    let ctrl_port = state.ctrl_port.lock().ok().and_then(|p| *p).unwrap_or(0);
    let query = scene_id.as_deref().map(|scene| format!("scene={}", urlencoding_component(scene)));

    let reusable = {
        let mut lock = state.sidecar.lock().map_err(|e| e.to_string())?;
        let current = state.stream_key.lock().ok().and_then(|lock| lock.clone());
        match lock.as_mut() {
            Some(session) if current.as_deref() == Some(key.as_str()) => session
                .is_alive()
                .then(|| (format!("http://127.0.0.1:{}", session.port), session.token.clone())),
            _ => None,
        }
    };
    if let Some((origin, token)) = reusable {
        if !show_scene_in_viewer(&app, state.inner(), &origin, scene_id.as_deref()) {
            open_viewer_window(&app, &origin, &token, &cache, ctrl_port, state.inner(), query.as_deref())?;
        }
        return Ok(SessionPayload {
            origin,
            token,
            server_version: "0.0.0".to_string(),
            data_root: cache,
            target_type: "stream".to_string(),
        });
    }

    // Anything else stops: the previous local server and any viewer job.
    {
        let mut lock = state.sidecar.lock().map_err(|e| e.to_string())?;
        if let Some(mut existing) = lock.take() {
            let _ = existing.stop();
        }
        if let Ok(mut current) = state.stream_key.lock() {
            *current = None;
        }
    }
    {
        let mut lock = state.remote.lock().map_err(|e| e.to_string())?;
        if let Some(mut existing) = lock.take() {
            let _ = existing.disconnect();
        }
        if let Ok(mut current) = state.remote_key.lock() {
            *current = None;
        }
    }

    let stream = coperceive_desktop::cluster_datasets::stream_argv(
        &ssh_bin(),
        &target,
        &entry.profile,
        &cache,
        &raw_root,
    );
    let cache_dir = remote_view_cache_dir();
    let mirror = cache_dir.join("mirror").join(format!("{:016x}", {
        use std::hash::{Hash, Hasher};
        let mut hasher = std::collections::hash_map::DefaultHasher::new();
        key.hash(&mut hasher);
        hasher.finish()
    }));
    fs::create_dir_all(&mirror).map_err(|error| format!("Could not create the viewing cache: {error}"))?;
    let extra = vec![
        "--remote-stream-argv".to_string(),
        serde_json::to_string(&stream).map_err(|error| error.to_string())?,
        "--remote-cache-dir".to_string(),
        cache_dir.join("blocks").to_string_lossy().into_owned(),
        "--remote-cache-bytes".to_string(),
        state::load_state().remote_view_cache_bytes.to_string(),
    ];
    let python_bin = resolve_python_binary(&app);
    let mut session = SidecarSession::spawn_with_args(&python_bin, &mirror, None, None, &extra)
        .map_err(|e| format!("Could not start the viewer: {e}"))?;
    let origin = format!("http://127.0.0.1:{}", session.port);
    let token = session.token.clone();
    if let Ok(lock) = state.controller_config.lock() {
        if let Some(ref cfg) = *lock {
            cfg.set_allowed_origin(origin.clone());
        }
    }
    if let Err(error) = reqwest_like_poll(&origin, &token, Duration::from_secs(60)) {
        session.preserve_log = true;
        let tail = fs::read_to_string(&session.log_path)
            .map(|content| {
                let lines: Vec<&str> = content.lines().collect();
                lines[lines.len().saturating_sub(20)..].join("\n")
            })
            .unwrap_or_default();
        let _ = session.stop();
        return Err(format!("The viewer did not start: {error}\n\n{tail}"));
    }
    eprintln!("[viewer] reading {cache} over SSH, no Slurm job");
    if let Err(error) = open_viewer_window(&app, &origin, &token, &cache, ctrl_port, state.inner(), query.as_deref()) {
        let _ = session.stop();
        return Err(error);
    }
    {
        let mut lock = state.sidecar.lock().map_err(|e| e.to_string())?;
        *lock = Some(session);
    }
    if let Ok(mut current) = state.stream_key.lock() {
        *current = Some(key);
    }
    Ok(SessionPayload {
        origin,
        token,
        server_version: "0.0.0".to_string(),
        data_root: cache,
        target_type: "stream".to_string(),
    })
}

/// Runs the test-only dataset flow (see `test_seams::DatasetFlowProbe`): saves
/// the fixture server, then calls the real `dataset_*` commands in order and
/// records each response or error. A failing step does not stop the flow; the
/// e2e asserts on every recorded outcome.
fn run_dataset_flow_probe(
    app: &tauri::AppHandle,
    request_path: &Path,
) -> Result<serde_json::Value, String> {
    use tauri::Manager;
    let request_text = fs::read_to_string(request_path)
        .map_err(|error| format!("Could not read the dataset flow request: {error}"))?;
    let payload: serde_json::Value = serde_json::from_str(&request_text)
        .map_err(|error| format!("Dataset flow request is not JSON: {error}"))?;
    let server_path = payload
        .get("server")
        .and_then(serde_json::Value::as_str)
        .ok_or_else(|| "Dataset flow request needs a server fixture path.".to_string())?;
    let server_text = fs::read_to_string(server_path)
        .map_err(|error| format!("Could not read the server fixture: {error}"))?;
    let entry: ServerEntry = serde_json::from_str(&server_text)
        .map_err(|error| format!("Server fixture is not valid: {error}"))?;
    let connection_entry = entry.clone();
    let mut servers = server_profiles::load_servers();
    server_profiles::upsert_server(&mut servers, entry)?;
    let steps = payload
        .get("steps")
        .and_then(serde_json::Value::as_array)
        .cloned()
        .ok_or_else(|| "Dataset flow request needs a steps list.".to_string())?;

    fn arg_text(args: &serde_json::Value, key: &str) -> Result<String, String> {
        args.get(key)
            .and_then(serde_json::Value::as_str)
            .map(str::to_string)
            .ok_or_else(|| format!("Step argument {key} is missing."))
    }
    fn optional_text(args: &serde_json::Value, key: &str) -> Option<String> {
        args.get(key).and_then(serde_json::Value::as_str).map(str::to_string)
    }

    let mut last_preview_hash: Option<String> = None;
    let mut records = Vec::new();
    for step in steps {
        let command = step
            .get("command")
            .and_then(serde_json::Value::as_str)
            .unwrap_or_default()
            .to_string();
        let args = step.get("args").cloned().unwrap_or(serde_json::json!({}));
        let state = app.state::<Arc<AppState>>();
        let outcome: Result<serde_json::Value, String> = (|| {
            match command.as_str() {
                "test_server_connection" => {
                    tauri::async_runtime::block_on(test_server_connection(connection_entry.clone()))
                        .and_then(|report| {
                            serde_json::to_value(report).map_err(|error| error.to_string())
                        })
                }
                "dataset_detect" => tauri::async_runtime::block_on(dataset_detect(
                    arg_text(&args, "profileId")?,
                    arg_text(&args, "path")?,
                )),
                "dataset_index" => tauri::async_runtime::block_on(dataset_index(
                    arg_text(&args, "profileId")?,
                    arg_text(&args, "adapter")?,
                    arg_text(&args, "rawRoot")?,
                    optional_text(&args, "split"),
                )),
                "dataset_prepare" => tauri::async_runtime::block_on(dataset_prepare(
                    state,
                    arg_text(&args, "profileId")?,
                    arg_text(&args, "adapter")?,
                    arg_text(&args, "rawRoot")?,
                    optional_text(&args, "split"),
                    args.get("scenes").cloned(),
                    args.get("workers")
                        .and_then(serde_json::Value::as_u64)
                        .map(|workers| workers as u32),
                    optional_text(&args, "images"),
                )),
                "dataset_prepare_submit" => {
                    let mut hash = arg_text(&args, "previewHash")?;
                    if hash == coperceive_desktop::test_seams::LAST_PREVIEW_HASH {
                        hash = last_preview_hash.clone().unwrap_or_default();
                    }
                    tauri::async_runtime::block_on(dataset_prepare_submit(
                        state,
                        arg_text(&args, "profileId")?,
                        hash,
                        args.get("confirmed")
                            .and_then(serde_json::Value::as_bool)
                            .unwrap_or(false),
                    ))
                }
                "dataset_status" => tauri::async_runtime::block_on(dataset_status(
                    arg_text(&args, "profileId")?,
                    arg_text(&args, "adapter")?,
                    arg_text(&args, "rawRoot")?,
                    optional_text(&args, "split"),
                )),
                "get_camera_images_setting" => serde_json::to_value(get_camera_images_setting(
                    optional_text(&args, "profileId"),
                ))
                .map_err(|error| error.to_string()),
                "set_camera_images_setting" => set_camera_images_setting(
                    optional_text(&args, "profileId"),
                    optional_text(&args, "mode"),
                )
                .and_then(|value| serde_json::to_value(value).map_err(|error| error.to_string())),
                "dataset_previews" => tauri::async_runtime::block_on(dataset_previews(
                    arg_text(&args, "profileId")?,
                    args.get("remove").and_then(serde_json::Value::as_bool).unwrap_or(false),
                )),
                // A remote viewer session as the evaluation viewer opens one.
                "remote_connect" => {
                    let entry = find_server_entry(&arg_text(&args, "profileId")?)?;
                    let alias = entry.profile.ssh_target()?;
                    tauri::async_runtime::block_on(remote_connect(
                        app.clone(),
                        state,
                        alias,
                        arg_text(&args, "dataRoot")?,
                        None,
                        Some(entry.id.clone()),
                        None,
                        None,
                    ))
                    .and_then(|payload| serde_json::to_value(payload).map_err(|error| error.to_string()))
                }
                "dataset_open" => tauri::async_runtime::block_on(dataset_open(
                    app.clone(),
                    state,
                    arg_text(&args, "profileId")?,
                    arg_text(&args, "adapter")?,
                    arg_text(&args, "rawRoot")?,
                    optional_text(&args, "split"),
                    optional_text(&args, "sceneId"),
                ))
                .and_then(|payload| {
                    serde_json::to_value(payload).map_err(|error| error.to_string())
                }),
                other => Err(format!("{other:?} is not a dataset command.")),
            }
        })();
        if command == "dataset_prepare" {
            if let Ok(preview) = &outcome {
                last_preview_hash = preview
                    .get("preview_hash")
                    .and_then(serde_json::Value::as_str)
                    .map(str::to_string);
            }
        }
        records.push(match outcome {
            Ok(value) => serde_json::json!({"command": command, "ok": true, "value": value}),
            Err(error) => serde_json::json!({"command": command, "ok": false, "error": error}),
        });
    }
    Ok(serde_json::json!({"ok": true, "steps": records}))
}

/// What a remote viewer session serves: reused only for exactly the same thing.
fn viewer_session_key(alias: &str, profile_id: Option<&str>, data_root: &str, media_roots: &[String]) -> String {
    format!("{alias}|{}|{data_root}|{}", profile_id.unwrap_or(""), media_roots.join("\n"))
}

/// Point the open viewer at another scene of the same session, without a new
/// window: the viewer reads `scene=` from its hash.
fn show_scene_in_viewer(app: &tauri::AppHandle, state: &AppState, origin: &str, scene_id: Option<&str>) -> bool {
    use tauri::Manager;
    let same_origin = state
        .viewer_origin
        .lock()
        .ok()
        .and_then(|lock| lock.clone())
        .is_some_and(|current| current == origin);
    let Some(viewer) = app.get_webview_window("viewer").filter(|_| same_origin) else {
        return false;
    };
    if let Some(scene) = scene_id {
        let hash = serde_json::to_string(&format!("#/viewer?scene={}", urlencoding_component(scene)))
            .unwrap_or_else(|_| "\"#/viewer\"".to_string());
        let _ = viewer.eval(&format!("window.location.hash = {hash};"));
    }
    let _ = viewer.show();
    let _ = viewer.set_focus();
    if let Some(main) = app.get_webview_window("main") {
        let _ = main.hide();
    }
    true
}

fn urlencoding_component(value: &str) -> String {
    value
        .bytes()
        .map(|byte| match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => (byte as char).to_string(),
            other => format!("%{other:02X}"),
        })
        .collect()
}

#[tauri::command]
#[allow(clippy::too_many_arguments)]
async fn remote_connect(
    app: tauri::AppHandle,
    state: tauri::State<'_, Arc<AppState>>,
    alias: String,
    data_root: String,
    uuid: Option<String>,
    profile_id: Option<String>,
    media_roots: Option<Vec<String>>,
    scene_id: Option<String>,
) -> Result<SessionPayload, String> {
    use tauri::Emitter;
    let media_roots = media_roots.unwrap_or_default();
    let key = viewer_session_key(&alias, profile_id.as_deref(), &data_root, &media_roots);

    // The same dataset is already being served: reuse that job and its viewer.
    // Viewing must not submit a Slurm job per Open (docs/datasets/cluster-datasets.md).
    let reusable = {
        let mut lock = state.remote.lock().map_err(|e| e.to_string())?;
        let current_key = state.remote_key.lock().ok().and_then(|lock| lock.clone());
        match lock.as_mut() {
            Some(existing) if current_key.as_deref() == Some(key.as_str()) => {
                if existing.tunnel_alive() {
                    existing
                        .job_status()
                        .ok()
                        .filter(|status| status.status == "running")
                        .map(|_| (existing.origin(), existing.token.clone()))
                } else {
                    None
                }
            }
            _ => None,
        }
    };
    if let Some((origin, token)) = reusable {
        if !show_scene_in_viewer(&app, state.inner(), &origin, scene_id.as_deref()) {
            let ctrl_port = state.ctrl_port.lock().ok().and_then(|p| *p).unwrap_or(0);
            let query = scene_id.as_deref().map(|scene| format!("scene={}", urlencoding_component(scene)));
            open_viewer_window(&app, &origin, &token, &data_root, ctrl_port, state.inner(), query.as_deref())?;
        }
        eprintln!("[viewer] reused the running viewer job for {data_root}");
        return Ok(SessionPayload {
            origin,
            token,
            server_version: "0.0.0".to_string(),
            data_root,
            target_type: "remote".to_string(),
        });
    }

    // Anything else is served by a new (or reattached) session: stop the old one.
    {
        let mut lock = state.sidecar.lock().map_err(|e| e.to_string())?;
        if let Some(mut existing) = lock.take() {
            let _ = existing.stop();
        }
    }
    {
        let mut lock = state.remote.lock().map_err(|e| e.to_string())?;
        if let Some(mut existing) = lock.take() {
            let _ = existing.disconnect();
        }
        if let Ok(mut current) = state.remote_key.lock() {
            *current = None;
        }
    }

    // Resources come from the selected server profile, never from a product
    // default. Without a profile the remote job cannot choose a partition.
    let profile = profile_id.as_deref().and_then(|id| {
        server_profiles::load_servers()
            .into_iter()
            .find(|entry| entry.id == id)
    });
    // The HPC checkout no longer ships coperceive/app/static (it is built, not
    // committed). Upload this app's built viewer to a per-version cache dir on
    // the login home and point the remote server at it; no Node on the login node.
    let static_dir = coperceive_desktop::remote_static::prepare_remote_static(
        &alias,
        env!("CARGO_PKG_VERSION"),
    )
    .map_err(|error| format!("Could not prepare the remote viewer page: {error}"))?;

    // A session started for this dataset before a restart may still be running.
    let saved = uuid.clone().or_else(|| state::load_state().viewer_sessions.get(&key).cloned());
    let options = RemoteStartOptions {
        data_root: data_root.clone(),
        partition: profile
            .as_ref()
            .and_then(|p| p.profile.defaults.partition.clone()),
        walltime: profile
            .as_ref()
            .and_then(|p| p.profile.defaults.time_limit.clone()),
        cpus: profile
            .as_ref()
            .and_then(|p| p.profile.defaults.cpus_per_task),
        mem: profile
            .as_ref()
            .and_then(|p| p.profile.defaults.memory.clone()),
        python: profile
            .as_ref()
            .and_then(|p| p.profile.environment.interpreter.clone()),
        uuid: None,
        static_dir: Some(static_dir),
        coperceive_command: profile
            .as_ref()
            .map(|p| p.profile.environment.coperceive_command.clone())
            .unwrap_or_default(),
        media_roots,
        // Return quickly with the job id while it queues; we poll and report.
        wait_timeout: Some(5),
    };

    let progress_app = app.clone();
    let alias_for_job = alias.clone();
    let mgr = tauri::async_runtime::spawn_blocking(move || {
        let progress = |job: &coperceive_desktop::remote::ViewerJobProgress| {
            let _ = progress_app.emit("viewer-job", job);
        };
        if let Some(saved) = saved {
            // Waits while that job still starts; only one that ended falls through.
            match RemoteSessionManager::reattach_with_progress(
                &alias_for_job,
                &options,
                Some(&ssh_bin()),
                &saved,
                &progress,
                Duration::from_secs(3),
                Duration::from_secs(15 * 60),
            ) {
                Ok(mgr) => {
                    eprintln!("[viewer] reattached to the running viewer job");
                    return Ok(mgr);
                }
                Err(error) => eprintln!("[viewer] could not reattach ({error:?}); starting a new viewer job"),
            }
        }
        RemoteSessionManager::connect_with_progress(
            &alias_for_job,
            &options,
            Some(&ssh_bin()),
            &progress,
            Duration::from_secs(3),
            Duration::from_secs(15 * 60),
        )
    })
    .await
    .map_err(|error| format!("The viewer job could not start: {error}"))?
    .map_err(|e| e.to_string())?;
    eprintln!("[viewer] started viewer job {:?} for {data_root}", mgr.job_id);

    let origin = mgr.origin();
    let token = mgr.token.clone();
    let ctrl_port = state.ctrl_port.lock().ok().and_then(|p| *p).unwrap_or(0);

    let query = scene_id.as_deref().map(|scene| format!("scene={}", urlencoding_component(scene)));
    open_viewer_window(&app, &origin, &token, &data_root, ctrl_port, state.inner(), query.as_deref())?;

    let payload = SessionPayload {
        origin,
        token,
        server_version: "0.0.0".to_string(),
        data_root,
        target_type: "remote".to_string(),
    };

    let mut persistent = state::load_state();
    persistent.viewer_sessions.insert(key.clone(), mgr.uuid.clone());
    let _ = state::save_state(&persistent);
    if let Ok(mut current) = state.remote_key.lock() {
        *current = Some(key);
    }
    let mut lock = state.remote.lock().map_err(|e| e.to_string())?;
    *lock = Some(mgr);

    Ok(payload)
}

#[tauri::command]
async fn remote_status(
    alias: String,
    uuid: Option<String>,
    job_id: Option<u64>,
) -> Result<RemoteStatusResponse, String> {
    let command = server_profiles::load_servers()
        .into_iter()
        .find(|entry| {
            entry.profile.ssh_alias.as_deref() == Some(alias.as_str())
                || entry.profile.ssh_target().ok().as_deref() == Some(alias.as_str())
        })
        .map(|entry| entry.profile.environment.coperceive_command)
        .unwrap_or_else(|| vec!["coperceive".to_string()]);
    let client = RemoteClient::new(&alias).with_coperceive_command(command);
    client.status(uuid.as_deref(), job_id).map_err(|e| e.to_string())
}

#[tauri::command]
async fn remote_disconnect(state: tauri::State<'_, Arc<AppState>>) -> Result<(), String> {
    let mut lock = state.remote.lock().map_err(|e| e.to_string())?;
    if let Some(mut mgr) = lock.take() {
        mgr.disconnect().map_err(|e| e.to_string())?;
    }
    Ok(())
}

/// Describes one folder inside a server's chosen data folder, over the same
/// reused SSH connection as the rest of the HPC workspace.
///
/// Metadata only: the remote `coperceive remote scan` reads folder entries and
/// scene manifests, never scene data, and it bounds depth, entries and time so
/// the login node stays light. The result is the command's own JSON.
#[tauri::command]
async fn remote_scan(
    profile_id: String,
    root: String,
    path: String,
    depth: Option<u32>,
    timeout_s: Option<f64>,
    max_entries: Option<u32>,
    max_total: Option<u32>,
) -> Result<coperceive_desktop::remote_scan::ScanDocument, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let entry = server_profiles::load_servers()
            .into_iter()
            .find(|entry| entry.id == profile_id)
            .ok_or_else(|| "Choose a server before browsing its folders.".to_string())?;
        let target = entry.ssh_target()?;
        coperceive_desktop::remote_scan::validate_target(&entry.profile, &root, &path)?;
        let limits =
            coperceive_desktop::remote_scan::ScanLimits::resolve(depth, timeout_s, max_entries, max_total);
        coperceive_desktop::remote_scan::run_scan(
            &ssh_bin(),
            &target,
            &entry.profile.environment.coperceive_command,
            &root,
            &path,
            limits,
        )
    })
    .await
    .map_err(|e| format!("The folder scan could not finish: {e}"))?
}

/// Simple loopback HTTP polling using TcpStream without external HTTP client crate.
/// Reads until headers end (\r\n\r\n) rather than assuming a single buffer read.
fn reqwest_like_poll(origin: &str, token: &str, timeout: Duration) -> Result<(), String> {
    use std::io::{Read, Write};
    use std::net::TcpStream;

    let port_str = origin.split(':').last().unwrap_or("8731");
    let port: u16 = port_str.parse().unwrap_or(8731);
    let deadline = Instant::now() + timeout;

    while Instant::now() < deadline {
        if let Ok(mut stream) = TcpStream::connect(("127.0.0.1", port)) {
            let _ = stream.set_read_timeout(Some(Duration::from_millis(800)));
            let _ = stream.set_write_timeout(Some(Duration::from_millis(800)));
            let req = format!(
                "GET /api/health HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nAuthorization: Bearer {}\r\nConnection: close\r\n\r\n",
                port, token
            );
            if stream.write_all(req.as_bytes()).is_ok() {
                let mut resp_bytes = Vec::new();
                let mut chunk = [0u8; 1024];
                while let Ok(n) = stream.read(&mut chunk) {
                    if n == 0 {
                        break;
                    }
                    resp_bytes.extend_from_slice(&chunk[..n]);
                    if resp_bytes.windows(4).any(|w| w == b"\r\n\r\n") {
                        break;
                    }
                }
                let resp = String::from_utf8_lossy(&resp_bytes);
                if resp.contains("200 OK") || resp.contains("HTTP/1.1 200") || resp.contains("HTTP/1.0 200") {
                    return Ok(());
                }
            }
        }
        std::thread::sleep(Duration::from_millis(150));
    }
    Err("Health check probe timed out".to_string())
}

fn handle_fatal_error(err_msg: &str) {
    platform::handle_fatal_error(err_msg);
}

fn main() {
    std::panic::set_hook(Box::new(|info| {
        let msg = format!("Panic occurred: {info}");
        handle_fatal_error(&msg);
    }));

    let app_state = Arc::new(AppState::default());
    let state_for_menu = Arc::clone(&app_state);
    let state_for_dnd = Arc::clone(&app_state);
    let state_for_window = Arc::clone(&app_state);
    let state_for_exit = Arc::clone(&app_state);

    let builder = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(app_state)
        .invoke_handler(tauri::generate_handler![
            get_persistent_state,
            is_remote_enabled,
            pick_data_root,
            start_local,
            stop_local,
            import_from_folder,
            cancel_import,
            get_storage_info,
            inspect_folder,
            set_library_root,
            move_library_root,
            get_library_scenes,
            download_town_map,
            library_thumbnail,
            delete_library_scene,
            reveal_in_finder,
            dataset_dry_run,
            start_dataset_job,
            cancel_dataset_job,
            pause_dataset_job,
            resume_dataset_job,
            dataset_catalog,
            set_auto_download_updates,
            validate_scene,
            health,
            list_ssh_config_hosts,
            read_ssh_host_settings,
            list_server_profiles,
            save_server_profile,
            delete_server_profile,
            test_server_connection,
            list_pipeline_options,
            read_custom_pipeline,
            dataset_detect,
            dataset_index,
            dataset_prepare,
            dataset_prepare_submit,
            dataset_status,
            dataset_open,
            get_camera_images_setting,
            set_camera_images_setting,
            set_viewer_dataset_scenes,
            remember_cluster_dataset,
            last_cluster_dataset,
            dataset_prepare_job_status,
            dataset_previews,
            build_run_plan,
            save_run_plan,
            preview_submission,
            submit_run_plan,
            job_watch_status,
            job_watch_logs,
            cancel_run_job,
            prediction_stream,
            run_metrics,
            open_predictions,
            list_run_history,
            remote_connect,
            remote_view_cache,
            remote_status,
            remote_disconnect,
            remote_scan,
            pick_save_path,
            save_exported_file,
        ])
        .on_page_load(|webview, payload| {
            // The Connect window is declared in tauri.conf.json, so it does not
            // get the viewer initialization_script. This hook gives it the
            // loopback controller and the test-only import path.
            if webview.label() != "main" {
                return;
            }
            if payload.event() != tauri::webview::PageLoadEvent::Finished {
                return;
            }
            let test_env = coperceive_desktop::test_seams::test_env_active();
            let force = test_env
                && std::env::var("COPERCEIVE_FORCE_MOTION").ok().as_deref() == Some("1");
            let import_source = std::env::var("COPERCEIVE_TEST_IMPORT_SOURCE").unwrap_or_default();
            let update_url = std::env::var("COPERCEIVE_UPDATE_URL").unwrap_or_default();
            let app = webview.app_handle();
            let state = app.state::<Arc<AppState>>();
            let port = state.ctrl_port.lock().ok().and_then(|slot| *slot).unwrap_or(0);
            let secret = state
                .ctrl_secret
                .lock()
                .ok()
                .and_then(|slot| slot.clone())
                .unwrap_or_default()
                .replace('\\', "\\\\")
                .replace('"', "\\\"");
            let import_js = import_source.replace('\\', "\\\\").replace('"', "\\\"");
            let update_js = update_url.replace('\\', "\\\\").replace('"', "\\\"");
            let _ = webview.eval(&format!(
                r#"window.__COPERCEIVE_TITLEBAR__ = "overlay";
                window.dispatchEvent(new Event("coperceive-titlebar"));
                window.__COPERCEIVE_CTRL_PORT__ = {port};
                window.__COPERCEIVE_CTRL_SECRET__ = "{secret}";
                window.__COPERCEIVE_TEST_ENV__ = {test_env};
                window.__COPERCEIVE_FORCE_MOTION__ = {force};
                window.__COPERCEIVE_TEST_IMPORT_SOURCE__ = "{import_js}";
                window.__COPERCEIVE_UPDATE_URL__ = "{update_js}";
                window.dispatchEvent(new Event("coperceive-test-import"));
                if ({force}) window.dispatchEvent(new Event("coperceive-force-motion"));"#,
            ));
        })
        .setup(|app| {
            use tauri::Manager;
            let state = app.state::<Arc<AppState>>().inner().clone();
            let handle = app.handle().clone();

            // Test-only seam for the CI packaged-app HPC check: run the real
            // "Test connection" code against a committed fake-ssh profile and
            // write the report for `scripts/desktop/e2e_hpc_fake_ssh.sh`.
            if let Some((request_path, result_path)) =
                coperceive_desktop::test_seams::run_plan_probe_request()
            {
                std::thread::spawn(move || {
                    let result = (|| -> Result<serde_json::Value, String> {
                        let text = fs::read_to_string(&request_path)
                            .map_err(|error| format!("Could not read the plan request: {error}"))?;
                        let payload: serde_json::Value = serde_json::from_str(&text)
                            .map_err(|error| format!("Plan request is not JSON: {error}"))?;
                        let server_path = payload
                            .get("server")
                            .and_then(serde_json::Value::as_str)
                            .ok_or_else(|| "Plan request needs a server fixture path.".to_string())?;
                        let server_text = fs::read_to_string(server_path).map_err(|error| {
                            format!("Could not read the server fixture: {error}")
                        })?;
                        let entry: ServerEntry = serde_json::from_str(&server_text)
                            .map_err(|error| format!("Server fixture is not valid: {error}"))?;
                        let request: RunPlanRequest = serde_json::from_value(
                            payload
                                .get("request")
                                .cloned()
                                .ok_or_else(|| "Plan request needs a request object.".to_string())?,
                        )
                        .map_err(|error| format!("Plan request is not valid: {error}"))?;
                        let runner = plan_runner(&entry)?;
                        let built = run_plan::build_run_plan(&runner, &entry, &request)?;
                        let save = payload
                            .get("save")
                            .and_then(serde_json::Value::as_bool)
                            .unwrap_or(false);
                        let plan = if save {
                            run_plan::save_run_plan(&runner, &entry, &built, &request)?
                        } else {
                            built
                        };
                        Ok(serde_json::json!({"ok": true, "plan": plan}))
                    })();
                    let document = match result {
                        Ok(value) => value,
                        Err(error) => serde_json::json!({"ok": false, "error": error}),
                    };
                    let _ = fs::write(
                        &result_path,
                        serde_json::to_string_pretty(&document).unwrap_or_default(),
                    );
                });
            }

            // Test-only seam for the submit step: preview and (only when the
            // payload says confirmed) submit a saved plan against the fake ssh.
            if let Some(probe) = coperceive_desktop::test_seams::hpc_submit_probe_request() {
                std::thread::spawn(move || {
                    let result = (|| -> Result<serde_json::Value, String> {
                        let text = fs::read_to_string(&probe.request)
                            .map_err(|error| format!("Could not read the submit request: {error}"))?;
                        let payload: serde_json::Value = serde_json::from_str(&text)
                            .map_err(|error| format!("Submit request is not JSON: {error}"))?;
                        let server_path = payload
                            .get("server")
                            .and_then(serde_json::Value::as_str)
                            .ok_or_else(|| "Submit request needs a server fixture path.".to_string())?;
                        let server_text = fs::read_to_string(server_path)
                            .map_err(|error| format!("Could not read the server fixture: {error}"))?;
                        let entry: ServerEntry = serde_json::from_str(&server_text)
                            .map_err(|error| format!("Server fixture is not valid: {error}"))?;
                        let plan_id = payload
                            .get("plan_id")
                            .and_then(serde_json::Value::as_str)
                            .ok_or_else(|| "Submit request needs a plan_id.".to_string())?
                            .to_string();
                        let options: SubmitOptions = payload
                            .get("options")
                            .cloned()
                            .map(serde_json::from_value)
                            .transpose()
                            .map_err(|error| format!("Submit options are not valid: {error}"))?
                            .unwrap_or_default();
                        let confirmed = payload
                            .get("confirmed")
                            .and_then(serde_json::Value::as_bool)
                            .unwrap_or(false);
                        let runner = plan_runner(&entry)?;
                        let plan = job_submit::load_saved_plan(&runner, &entry.profile, &plan_id)?;
                        let preview =
                            job_submit::render_run_script(&entry.profile, &plan, &options)?;
                        let submitted = job_submit::submit_saved_plan(
                            &runner,
                            &entry.profile,
                            &plan_id,
                            &options,
                            &preview.preview_hash,
                            confirmed,
                        );
                        Ok(match submitted {
                            Ok(outcome) => serde_json::json!({
                                "ok": true,
                                "confirmed": confirmed,
                                "preview_hash": preview.preview_hash,
                                "gres": preview.gres,
                                "script": preview.script,
                                "outcome": outcome,
                            }),
                            Err(error) => serde_json::json!({
                                "ok": false,
                                "confirmed": confirmed,
                                "preview_hash": preview.preview_hash,
                                "gres": preview.gres,
                                "script": preview.script,
                                "error": error,
                            }),
                        })
                    })();
                    let document = match result {
                        Ok(value) => value,
                        Err(error) => serde_json::json!({"ok": false, "error": error}),
                    };
                    let _ = fs::write(
                        &probe.result,
                        serde_json::to_string_pretty(&document).unwrap_or_default(),
                    );
                });
            }

            // Test-only seam for the watch step: run the real status/log/cancel
            // functions against the fake ssh and write the result for the e2e.
            if let Some(probe) = coperceive_desktop::test_seams::hpc_watch_probe_request() {
                std::thread::spawn(move || {
                    let result = (|| -> Result<serde_json::Value, String> {
                        let text = fs::read_to_string(&probe.request)
                            .map_err(|error| format!("Could not read the watch request: {error}"))?;
                        let payload: serde_json::Value = serde_json::from_str(&text)
                            .map_err(|error| format!("Watch request is not JSON: {error}"))?;
                        let server_path = payload
                            .get("server")
                            .and_then(serde_json::Value::as_str)
                            .ok_or_else(|| "Watch request needs a server fixture path.".to_string())?;
                        let server_text = fs::read_to_string(server_path)
                            .map_err(|error| format!("Could not read the server fixture: {error}"))?;
                        let entry: ServerEntry = serde_json::from_str(&server_text)
                            .map_err(|error| format!("Server fixture is not valid: {error}"))?;
                        let plan_id = payload
                            .get("plan_id")
                            .and_then(serde_json::Value::as_str)
                            .ok_or_else(|| "Watch request needs a plan_id.".to_string())?
                            .to_string();
                        let job_id = payload
                            .get("job_id")
                            .and_then(serde_json::Value::as_str)
                            .ok_or_else(|| "Watch request needs a job_id.".to_string())?
                            .to_string();
                        let action = payload
                            .get("action")
                            .and_then(serde_json::Value::as_str)
                            .unwrap_or("status");
                        let runner = plan_runner(&entry)?;
                        match action {
                            "status" => {
                                let status = job_watch::watch_status(
                                    &runner,
                                    &entry.profile,
                                    &plan_id,
                                    &job_id,
                                )?;
                                Ok(serde_json::json!({"ok": true, "status": status}))
                            }
                            "logs" => {
                                let stream = payload
                                    .get("stream")
                                    .and_then(serde_json::Value::as_str)
                                    .unwrap_or("stdout");
                                let lines = payload
                                    .get("lines")
                                    .and_then(serde_json::Value::as_u64)
                                    .unwrap_or(50)
                                    as u32;
                                let tail = job_watch::tail_log(
                                    &runner,
                                    &entry.profile,
                                    &plan_id,
                                    &job_id,
                                    stream,
                                    lines,
                                )?;
                                Ok(serde_json::json!({"ok": true, "logs": tail}))
                            }
                            "cancel" => {
                                let confirmed = payload
                                    .get("confirmed")
                                    .and_then(serde_json::Value::as_bool)
                                    .unwrap_or(false);
                                let cancelled = job_watch::cancel_job(
                                    &runner,
                                    &entry.profile,
                                    &plan_id,
                                    &job_id,
                                    confirmed,
                                )?;
                                Ok(serde_json::json!({"ok": true, "cancel": cancelled}))
                            }
                            "tail" => {
                                let plan =
                                    job_submit::load_saved_plan(&runner, &entry.profile, &plan_id)?;
                                let offset = payload
                                    .get("offset")
                                    .and_then(serde_json::Value::as_u64)
                                    .unwrap_or(0);
                                let tail = job_report::tail_predictions(
                                    &runner,
                                    &entry.profile,
                                    &plan.output_dir,
                                    offset,
                                    payload
                                        .get("max_bytes")
                                        .and_then(serde_json::Value::as_u64)
                                        .unwrap_or(65536),
                                )?;
                                Ok(serde_json::json!({"ok": true, "tail": tail}))
                            }
                            "metrics" => {
                                let plan =
                                    job_submit::load_saved_plan(&runner, &entry.profile, &plan_id)?;
                                let metrics = job_report::read_metrics(
                                    &runner,
                                    &entry.profile,
                                    &plan.output_dir,
                                )?;
                                Ok(serde_json::json!({"ok": true, "metrics": metrics}))
                            }
                            "history" => {
                                let limit = payload
                                    .get("limit")
                                    .and_then(serde_json::Value::as_u64)
                                    .unwrap_or(50) as u32;
                                let entries =
                                    job_history::list_run_history(&runner, &entry.profile, limit)?;
                                Ok(serde_json::json!({"ok": true, "history": entries}))
                            }
                            "import" => {
                                let plan =
                                    job_submit::load_saved_plan(&runner, &entry.profile, &plan_id)?;
                                let data_root = payload
                                    .get("data_root")
                                    .and_then(serde_json::Value::as_str)
                                    .map(str::to_string)
                                    .or_else(|| entry.profile.data_roots.first().cloned())
                                    .ok_or_else(|| {
                                        "The profile has no data root to import into.".to_string()
                                    })?;
                                let imported = job_report::import_predictions(
                                    &runner,
                                    &entry.profile,
                                    &plan_id,
                                    &plan.output_dir,
                                    &data_root,
                                )?;
                                Ok(serde_json::json!({"ok": true, "imported": imported}))
                            }
                            other => Err(format!("Unknown watch action {other:?}.")),
                        }
                    })();
                    let document = match result {
                        Ok(value) => value,
                        Err(error) => serde_json::json!({"ok": false, "error": error}),
                    };
                    let _ = fs::write(
                        &probe.result,
                        serde_json::to_string_pretty(&document).unwrap_or_default(),
                    );
                });
            }

            // The same seam also runs one real folder scan for the Browse step.
            if let Some(request) = coperceive_desktop::test_seams::hpc_scan_probe_request() {
                std::thread::spawn(move || {
                    let result = (|| -> serde_json::Value {
                        let json = match fs::read_to_string(&request.profile) {
                            Ok(json) => json,
                            Err(error) => {
                                return serde_json::json!({"ok": false, "error": error.to_string()});
                            }
                        };
                        let entry: ServerEntry = match serde_json::from_str(&json) {
                            Ok(entry) => entry,
                            Err(error) => {
                                return serde_json::json!({"ok": false, "error": error.to_string()});
                            }
                        };
                        let target = match entry.ssh_target() {
                            Ok(target) => target,
                            Err(error) => {
                                return serde_json::json!({"ok": false, "error": error});
                            }
                        };
                        if let Err(error) = coperceive_desktop::remote_scan::validate_target(
                            &entry.profile,
                            &request.root,
                            &request.path,
                        ) {
                            return serde_json::json!({"ok": false, "error": error});
                        }
                        let limits = coperceive_desktop::remote_scan::ScanLimits::default();
                        match coperceive_desktop::remote_scan::run_scan(
                            &ssh_bin(),
                            &target,
                            &entry.profile.environment.coperceive_command,
                            &request.root,
                            &request.path,
                            limits,
                        ) {
                            Ok(document) => serde_json::json!({"ok": true, "scan": document}),
                            Err(error) => serde_json::json!({"ok": false, "error": error}),
                        }
                    })();
                    let _ = fs::write(
                        &request.result,
                        serde_json::to_string_pretty(&result).unwrap_or_default(),
                    );
                });
            }

            if let Some((profile_path, result_path)) =
                coperceive_desktop::test_seams::hpc_probe_request()
            {
                std::thread::spawn(move || {
                    let result = match fs::read_to_string(&profile_path) {
                        Ok(json) => match serde_json::from_str::<ServerEntry>(&json) {
                            Ok(entry) => {
                                let report = detect::detect_server(
                                    &entry,
                                    &DetectOptions { ssh_bin: ssh_bin() },
                                );
                                serde_json::json!({"ok": true, "report": report})
                            }
                            Err(error) => {
                                serde_json::json!({"ok": false, "error": error.to_string()})
                            }
                        },
                        Err(error) => serde_json::json!({"ok": false, "error": error.to_string()}),
                    };
                    let _ = fs::write(
                        &result_path,
                        serde_json::to_string_pretty(&result).unwrap_or_default(),
                    );
                });
            }

            // Test-only cluster-dataset flow: call the same Tauri commands the
            // Dataset view invokes, in the listed order, against the fake cluster.
            if let Some(probe) = coperceive_desktop::test_seams::dataset_flow_probe_request() {
                let handle = handle.clone();
                std::thread::spawn(move || {
                    let document = match run_dataset_flow_probe(&handle, &probe.request) {
                        Ok(value) => value,
                        Err(error) => serde_json::json!({"ok": false, "error": error}),
                    };
                    let _ = fs::write(
                        &probe.result,
                        serde_json::to_string_pretty(&document).unwrap_or_default(),
                    );
                });
            }

            // Test-only server save/reload/edit round trip for the HPC e2e. Uses
            // COPERCEIVE_STATE_DIR (set by the harness) so the real store is untouched.
            if let Some(probe) =
                coperceive_desktop::test_seams::hpc_server_save_probe_request()
            {
                std::thread::spawn(move || {
                    let result = (|| -> Result<serde_json::Value, String> {
                        let json = fs::read_to_string(&probe.server)
                            .map_err(|error| format!("Could not read the server fixture: {error}"))?;
                        let mut entry: ServerEntry = serde_json::from_str(&json)
                            .map_err(|error| format!("Server fixture is not valid: {error}"))?;
                        entry.id = String::new();
                        let mut typed_interpreter = None;
                        let mut detected_scheduler = None;
                        let mut detected_time_limit = None;
                        let mut detected_command: Vec<String> = Vec::new();
                        if let Some(interpreter) = &probe.typed_interpreter {
                            // E2E-only minimal projection of the web's
                            // applyDiscovery (the source of truth). It reproduces
                            // the order "type interpreter, then detected
                            // settings" for this fixture: detection now supplies
                            // the exact coperceive argv when it succeeded through
                            // that interpreter, so the seam only applies the
                            // detected scheduler/partition/walltime and command.
                            entry.profile.environment.interpreter = Some(interpreter.clone());
                            typed_interpreter = Some(interpreter.clone());
                            let report = detect::detect_server(
                                &entry,
                                &DetectOptions { ssh_bin: ssh_bin() },
                            );
                            detected_scheduler = Some(report.scheduler);
                            entry.profile.scheduler = report.scheduler;
                            let up = report
                                .partitions
                                .iter()
                                .find(|partition| partition.state.as_deref() != Some("down"))
                                .or_else(|| report.partitions.first());
                            if entry.profile.defaults.partition.is_none() {
                                entry.profile.defaults.partition =
                                    up.map(|partition| partition.name.clone());
                            }
                            if entry.profile.defaults.time_limit.is_none() {
                                detected_time_limit = up
                                    .and_then(|partition| partition.max_time.as_deref())
                                    .map(str::trim)
                                    .filter(|text| {
                                        !text.is_empty()
                                            && !["infinite", "unlimited", "unknown", "n/a"]
                                                .iter()
                                                .any(|word| text.eq_ignore_ascii_case(word))
                                    })
                                    .map(str::to_string);
                                entry.profile.defaults.time_limit = detected_time_limit.clone();
                            }
                            entry.profile.environment.coperceive_command =
                                report.environment.coperceive_command.clone();
                            detected_command =
                                entry.profile.environment.coperceive_command.clone();
                        }
                        let mut servers = server_profiles::load_servers();
                        let saved = server_profiles::upsert_server(&mut servers, entry)?;
                        let listed = server_profiles::load_servers();
                        let listed_ids: Vec<String> =
                            listed.iter().map(|item| item.id.clone()).collect();
                        let mut edited = listed
                            .iter()
                            .find(|item| item.id == saved.id)
                            .cloned()
                            .ok_or_else(|| "Saved server is missing after reload.".to_string())?;
                        edited.profile.name = format!("{} (edited)", edited.profile.name);
                        edited.profile.defaults.cpus_per_task = Some(8);
                        let mut servers = server_profiles::load_servers();
                        let edited_saved = server_profiles::upsert_server(&mut servers, edited)?;
                        let after = server_profiles::load_servers();
                        let persisted = after
                            .iter()
                            .find(|item| item.id == saved.id)
                            .cloned()
                            .ok_or_else(|| "Edited server is missing after reload.".to_string())?;
                        Ok(serde_json::json!({
                            "ok": true,
                            "saved_id": saved.id,
                            "listed_ids": listed_ids,
                            "listed_before_edit": listed.iter().any(|item| item.id == saved.id),
                            "edited_name": edited_saved.profile.name,
                            "persisted_name": persisted.profile.name,
                            "persisted_cpus": persisted.profile.defaults.cpus_per_task,
                            "typed_interpreter": typed_interpreter,
                            "detected_scheduler": detected_scheduler,
                            "detected_time_limit": detected_time_limit,
                            "detected_command": detected_command,
                            "persisted_time_limit": persisted.profile.defaults.time_limit,
                            "persisted_command": persisted.profile.environment.coperceive_command,
                            "store_path": server_profiles::servers_file_path().display().to_string(),
                        }))
                    })();
                    let document = match result {
                        Ok(value) => value,
                        Err(error) => serde_json::json!({"ok": false, "error": error}),
                    };
                    let _ = fs::write(
                        &probe.result,
                        serde_json::to_string_pretty(&document).unwrap_or_default(),
                    );
                });
            }

            // 1. Start loopback desktop controller
            match start_desktop_controller(handle.clone(), state.clone()) {
                Ok(port) => {
                    if let Ok(mut lock) = state.ctrl_port.lock() {
                        *lock = Some(port);
                    }
                    println!("[desktop-ctrl] Loopback controller listening on port {}", port);
                }
                Err(e) => {
                    eprintln!("[desktop-ctrl] Failed to start loopback controller: {}", e);
                }
            }

            let persistent = state::load_state();

            // 2. Set macOS native application menu
            #[cfg(target_os = "macos")]
            if let Ok(menu) = build_app_menu(&handle, &persistent.recent_roots) {
                let _ = app.set_menu(menu);
            }

            // 3. Auto-connect on launch if data root exists and is valid.
            // A restored session must also name a scene that is still in this
            // library; otherwise fall back to the Library window instead of
            // opening a viewer that would show "scene not found".
            let env_auto_root = std::env::var("COPERCEIVE_AUTOCONNECT_ROOT")
                .or_else(|_| std::env::var("COPERCEIVE_DESKTOP_AUTOCONNECT_ROOT"))
                .ok();
            let (auto_root, from_saved_state) = match env_auto_root {
                Some(root) => (Some(root), false),
                None if persistent.reopen_last_session => {
                    (persistent.last_data_root.clone(), true)
                }
                None => (None, false),
            };

            if let Some(root_str) = auto_root {
                let root_path = PathBuf::from(&root_str);
                let restored_scene_ok = !from_saved_state
                    || persistent
                        .last_scene_id
                        .as_deref()
                        .map_or(true, |scene| state::restored_scene_exists(&root_path, scene));
                if !restored_scene_ok {
                    eprintln!(
                        "[auto-connect] the last scene is gone from {}; opening the Library",
                        root_path.display()
                    );
                    let mut cleared = state::load_state();
                    cleared.last_scene_id = None;
                    cleared.last_sample_index = None;
                    cleared.last_timestamp_us = None;
                    cleared._legacy_timestamp_ns = None;
                    let _ = state::save_state(&cleared);
                    if let Some(main) = app.get_webview_window("main") {
                        let _ = main.show();
                    }
                } else if state::validate_dataset_root(&root_path).is_ok() {
                    let h = handle.clone();
                    let st = state.clone();
                    std::thread::spawn(move || {
                        std::thread::sleep(Duration::from_millis(300));
                        tauri::async_runtime::block_on(async move {
                            match start_local_session(&h, &st, root_str).await {
                                Ok(payload) => {
                                    println!("[auto-connect] Successfully connected: origin={}", payload.origin);
                                    if let Ok(marker_path) = std::env::var("COPERCEIVE_TEST_MARKER_FILE") {
                                        let log_path = {
                                            let lock = st.sidecar.lock().ok();
                                            lock.and_then(|s| s.as_ref().map(|sess| sess.log_path.clone()))
                                                .unwrap_or_default()
                                        };
                                        let ctrl_port = st.ctrl_port.lock().ok().and_then(|p| *p).unwrap_or(0);
                                        let ctrl_secret = st.ctrl_secret.lock().ok().and_then(|s| s.clone()).unwrap_or_default();
                                        let content = format!(
                                            "{}\n{}\n{}\n{}\n{}\n",
                                            payload.origin,
                                            payload.token,
                                            log_path.display(),
                                            ctrl_port,
                                            ctrl_secret,
                                        );
                                        let _ = std::fs::write(&marker_path, content);
                                    }
                                }
                                Err(err) => {
                                    eprintln!("[auto-connect] Error connecting to last root: {err}");
                                    if let Some(main) = h.get_webview_window("main") {
                                        let _ = main.show();
                                    }
                                }
                            }
                        });
                    });
                } else {
                    if let Some(main) = app.get_webview_window("main") {
                        let _ = main.show();
                    }
                }
            } else {
                if let Some(main) = app.get_webview_window("main") {
                    let _ = main.show();
                }
            }

            Ok(())
        })
        .on_menu_event(move |app, event| {
            let id = event.id().as_ref();
            match id {
                "about_coperceive" => {
                    let version = coperceive_desktop::updater::app_version(&app.package_info().version);
                    let commit = option_env!("GIT_DESCRIBE")
                        .or(option_env!("GIT_COMMIT"))
                        .unwrap_or("580691a");
                    let build_date = option_env!("BUILD_DATE").unwrap_or("2026-10-21");
                    let about_text = format!(
                        "Coperceive v{}\n\nCommit: {}\nBuild Date: {}\nLicence: Apache-2.0\n\nCooperative perception evaluation and dataset workbench.",
                        version, commit, build_date
                    );
                    use tauri_plugin_dialog::DialogExt;
                    app.dialog()
                        .message(&about_text)
                        .title("About Coperceive")
                        .show(|_| {});
                }
                "settings" => {
                    if let Some(v) = app.get_webview_window("viewer") {
                        let _ = v.eval("const s = document.querySelector('details.settings'); if (s) s.open = !s.open;");
                    }
                }
                "open_dataset" => {
                    let handle = app.clone();
                    let st = state_for_menu.clone();
                    tauri::async_runtime::spawn(async move {
                        trigger_open_dataset_dialog(&handle, &st).await;
                    });
                }
                "open_hpc_dataset" => {
                    use tauri::Emitter;
                    if let Some(main) = app.get_webview_window("main") {
                        let _ = main.emit("navigate-hpc-workspace", ());
                        let _ = main.show();
                        let _ = main.set_focus();
                    }
                }
                "view_library" => {
                    use tauri::Emitter;
                    if let Some(main) = app.get_webview_window("main") {
                        let _ = main.emit("navigate-library", ());
                        let _ = main.show();
                        let _ = main.set_focus();
                    }
                }
                "view_get_data" => {
                    use tauri::Emitter;
                    if let Some(main) = app.get_webview_window("main") {
                        let _ = main.emit("navigate-get-data", ());
                        let _ = main.show();
                        let _ = main.set_focus();
                    }
                }
                "view_open_folder" => {
                    use tauri::Emitter;
                    if let Some(main) = app.get_webview_window("main") {
                        let _ = main.emit("navigate-open-folder", ());
                        let _ = main.show();
                        let _ = main.set_focus();
                    }
                }
                "view_hpc_workspace" => {
                    use tauri::Emitter;
                    if let Some(main) = app.get_webview_window("main") {
                        let _ = main.emit("navigate-hpc-workspace", ());
                        let _ = main.show();
                        let _ = main.set_focus();
                    }
                }
                "toggle_sidebar" => {
                    use tauri::Emitter;
                    if let Some(main) = app.get_webview_window("main") {
                        let _ = main.emit("toggle-sidebar", ());
                    }
                }
                "import_folder" => {
                    use tauri::Emitter;
                    if let Some(main) = app.get_webview_window("main") {
                        let _ = main.emit("import-request", ());
                    }
                }
                "get_data" => {
                    use tauri::Emitter;
                    if let Some(main) = app.get_webview_window("main") {
                        let _ = main.emit("navigate-get-data", ());
                        // The main window is hidden while a viewer is open.
                        let _ = main.show();
                        let _ = main.set_focus();
                    }
                }
                "check_updates" => {
                    use tauri::Emitter;
                    if let Some(main) = app.get_webview_window("main") {
                        let _ = main.emit("check-updates", ());
                    }
                    if let Some(v) = app.get_webview_window("viewer") {
                        let _ = v.emit("check-updates", ());
                    }
                }
                id if id.starts_with("recent_") => {
                    if let Ok(idx) = id.trim_start_matches("recent_").parse::<usize>() {
                        let persistent = state::load_state();
                        if let Some(path_str) = persistent.recent_roots.get(idx) {
                            let path = PathBuf::from(path_str);
                            let handle = app.clone();
                            let st = state_for_menu.clone();
                            tauri::async_runtime::spawn(async move {
                                let _ = switch_data_root(&handle, &st, path).await;
                            });
                        }
                    }
                }
                "viewpoint_orbit" => {
                    if let Some(v) = app.get_webview_window("viewer") {
                        let _ = v.eval("if (window.__COPERCEIVE_SET_VIEWPOINT__) window.__COPERCEIVE_SET_VIEWPOINT__('orbit');");
                    }
                }
                "viewpoint_chase" => {
                    if let Some(v) = app.get_webview_window("viewer") {
                        let _ = v.eval("if (window.__COPERCEIVE_SET_VIEWPOINT__) window.__COPERCEIVE_SET_VIEWPOINT__('chase');");
                    }
                }
                "viewpoint_drone" => {
                    if let Some(v) = app.get_webview_window("viewer") {
                        let _ = v.eval("if (window.__COPERCEIVE_SET_VIEWPOINT__) window.__COPERCEIVE_SET_VIEWPOINT__('drone');");
                    }
                }
                "viewpoint_bird" => {
                    if let Some(v) = app.get_webview_window("viewer") {
                        let _ = v.eval("if (window.__COPERCEIVE_SET_VIEWPOINT__) window.__COPERCEIVE_SET_VIEWPOINT__('bird');");
                    }
                }
                "toggle_fullscreen" => {
                    if let Some(v) = app.get_webview_window("viewer") {
                        let _ = v.eval("if (window.__COPERCEIVE_TOGGLE_FULLSCREEN__) window.__COPERCEIVE_TOGGLE_FULLSCREEN__();");
                    }
                }
                "toggle_theme" => {
                    if let Some(v) = app.get_webview_window("viewer") {
                        let _ = v.eval("if (window.__COPERCEIVE_TOGGLE_THEME__) window.__COPERCEIVE_TOGGLE_THEME__();");
                    }
                }
                "playback_toggle" => {
                    if let Some(v) = app.get_webview_window("viewer") {
                        let _ = v.eval("if (window.__COPERCEIVE_PLAYBACK__) window.__COPERCEIVE_PLAYBACK__('toggle');");
                    }
                }
                "playback_step_forward" => {
                    if let Some(v) = app.get_webview_window("viewer") {
                        let _ = v.eval("if (window.__COPERCEIVE_PLAYBACK__) window.__COPERCEIVE_PLAYBACK__('step_forward');");
                    }
                }
                "playback_step_backward" => {
                    if let Some(v) = app.get_webview_window("viewer") {
                        let _ = v.eval("if (window.__COPERCEIVE_PLAYBACK__) window.__COPERCEIVE_PLAYBACK__('step_backward');");
                    }
                }
                "playback_speed_05" => {
                    if let Some(v) = app.get_webview_window("viewer") {
                        let _ = v.eval("if (window.__COPERCEIVE_PLAYBACK__) window.__COPERCEIVE_PLAYBACK__('speed', 0.5);");
                    }
                }
                "playback_speed_10" => {
                    if let Some(v) = app.get_webview_window("viewer") {
                        let _ = v.eval("if (window.__COPERCEIVE_PLAYBACK__) window.__COPERCEIVE_PLAYBACK__('speed', 1.0);");
                    }
                }
                "playback_speed_20" => {
                    if let Some(v) = app.get_webview_window("viewer") {
                        let _ = v.eval("if (window.__COPERCEIVE_PLAYBACK__) window.__COPERCEIVE_PLAYBACK__('speed', 2.0);");
                    }
                }
                "help_logs" => {
                    if let Some(home) = std::env::var_os("HOME") {
                        let log_dir = PathBuf::from(home).join("Library").join("Logs").join("Coperceive");
                        let _ = std::fs::create_dir_all(&log_dir);
                        let _ = std::process::Command::new("open").arg(&log_dir).spawn();
                    }
                }
                "help_diagnostics" => {
                    copy_diagnostics_to_clipboard(app, &state_for_menu);
                }
                "help_docs" => {
                    let _ = std::process::Command::new("open")
                        .arg("https://github.com/adarsh-nl/coperceive#readme")
                        .spawn();
                }
                "help_repo" => {
                    let _ = std::process::Command::new("open")
                        .arg("https://github.com/adarsh-nl/coperceive")
                        .spawn();
                }
                "help_issue" => {
                    let _ = std::process::Command::new("open")
                        .arg("https://github.com/adarsh-nl/coperceive/issues")
                        .spawn();
                }
                _ => {}
            }
        })
        .on_window_event(move |window, event| {
            match event {
                tauri::WindowEvent::DragDrop(tauri::DragDropEvent::Drop { paths, .. }) => {
                    if let Some(path) = paths.first() {
                        let handle = window.app_handle().clone();
                        let st = state_for_dnd.clone();
                        let p = path.clone();
                        tauri::async_runtime::spawn(async move {
                            let _ = switch_data_root(&handle, &st, p).await;
                        });
                    }
                }
                tauri::WindowEvent::Resized(size) => {
                    if window.label() == "viewer" {
                        let scale = window.scale_factor().unwrap_or(1.0).max(0.1);
                        let logical_size: tauri::LogicalSize<f64> = size.to_logical(scale);
                        let mon = window.current_monitor().ok().flatten();
                        let mon_name = mon.as_ref().and_then(|m| m.name().cloned());
                        let is_max = window.is_maximized().unwrap_or(false);

                        let mut persistent = state::load_state();
                        let ws = persistent.window_state.get_or_insert_with(|| state::WindowState {
                            width: logical_size.width,
                            height: logical_size.height,
                            x: 0.0,
                            y: 0.0,
                            is_maximized: is_max,
                            monitor: mon_name.clone(),
                            scale_factor: Some(scale),
                        });
                        if !is_max {
                            ws.width = logical_size.width;
                            ws.height = logical_size.height;
                        }
                        ws.is_maximized = is_max;
                        if mon_name.is_some() {
                            ws.monitor = mon_name;
                        }
                        ws.scale_factor = Some(scale);
                        let _ = state::save_state(&persistent);
                    }
                }
                tauri::WindowEvent::Moved(pos) => {
                    if window.label() == "viewer" {
                        let scale = window.scale_factor().unwrap_or(1.0).max(0.1);
                        let logical_pos: tauri::LogicalPosition<f64> = pos.to_logical(scale);
                        let mon = window.current_monitor().ok().flatten();
                        let mon_name = mon.as_ref().and_then(|m| m.name().cloned());
                        let is_max = window.is_maximized().unwrap_or(false);

                        let mut persistent = state::load_state();
                        let ws = persistent.window_state.get_or_insert_with(|| state::WindowState {
                            width: 1400.0,
                            height: 900.0,
                            x: logical_pos.x,
                            y: logical_pos.y,
                            is_maximized: is_max,
                            monitor: mon_name.clone(),
                            scale_factor: Some(scale),
                        });
                        if !is_max {
                            ws.x = logical_pos.x;
                            ws.y = logical_pos.y;
                        }
                        ws.is_maximized = is_max;
                        if mon_name.is_some() {
                            ws.monitor = mon_name;
                        }
                        ws.scale_factor = Some(scale);
                        let _ = state::save_state(&persistent);
                    }
                }
                tauri::WindowEvent::ScaleFactorChanged { scale_factor, new_inner_size, .. } => {
                    if window.label() == "viewer" {
                        let scale = if *scale_factor > 0.0 { *scale_factor } else { 1.0 };
                        let logical_size: tauri::LogicalSize<f64> = new_inner_size.to_logical(scale);
                        let mon = window.current_monitor().ok().flatten();
                        let mon_name = mon.as_ref().and_then(|m| m.name().cloned());
                        let is_max = window.is_maximized().unwrap_or(false);

                        let mut persistent = state::load_state();
                        if let Some(ref mut ws) = persistent.window_state {
                            if !is_max {
                                ws.width = logical_size.width;
                                ws.height = logical_size.height;
                            }
                            ws.scale_factor = Some(scale);
                            if mon_name.is_some() {
                                ws.monitor = mon_name;
                            }
                            let _ = state::save_state(&persistent);
                        }
                    }
                }
                tauri::WindowEvent::CloseRequested { .. } => {
                    use tauri::Manager;
                    let label = window.label().to_string();
                    if label == "viewer" {
                        if state_for_window.replacing_viewer.load(Ordering::SeqCst) {
                            let _ = window.destroy();
                        } else {
                            if let Ok(mut lock) = state_for_window.sidecar.lock() {
                                if let Some(mut s) = lock.take() {
                                    let _ = s.stop();
                                }
                            }
                            let _ = window.destroy();
                        }
                    } else if label == "main" {
                        if let Ok(mut lock) = state_for_window.sidecar.lock() {
                            if let Some(mut s) = lock.take() {
                                let _ = s.stop();
                            }
                        }
                        if let Ok(mut lock) = state_for_window.remote.lock() {
                            if let Some(mut r) = lock.take() {
                                let _ = r.disconnect();
                            }
                        }
                        window.app_handle().exit(0);
                    }
                }
                tauri::WindowEvent::Destroyed => {
                    use tauri::{Emitter, Manager};
                    let label = window.label().to_string();
                    if label == "viewer" {
                        // The jobs indicator shows whether a dataset's viewer is open.
                        let _ = window.app_handle().emit("viewer-closed", ());
                        if !state_for_window.replacing_viewer.load(Ordering::SeqCst) {
                            if let Ok(mut lock) = state_for_window.sidecar.lock() {
                                if let Some(mut s) = lock.take() {
                                    let _ = s.stop();
                                }
                            }
                            if window.app_handle().get_webview_window("viewer").is_none() {
                                if let Some(main) = window.app_handle().get_webview_window("main") {
                                    let _ = main.show();
                                    let _ = main.set_focus();
                                }
                            }
                        }
                    } else if label == "main" {
                        if let Ok(mut lock) = state_for_window.sidecar.lock() {
                            if let Some(mut s) = lock.take() {
                                let _ = s.stop();
                            }
                        }
                        if let Ok(mut lock) = state_for_window.remote.lock() {
                            if let Some(mut r) = lock.take() {
                                let _ = r.disconnect();
                            }
                        }
                    }
                }
                _ => {}
            }
        });

    let mut context = tauri::generate_context!();
    if coperceive_desktop::test_seams::test_env_active() {
        if let Some(updater_cfg) = context.config_mut().plugins.0.get_mut("updater") {
            if let Some(map) = updater_cfg.as_object_mut() {
                map.insert(
                    "dangerousInsecureTransportProtocol".to_string(),
                    serde_json::Value::Bool(true),
                );
            }
        }
    }

    let app = match builder.build(context) {
        Ok(app) => app,
        Err(err) => {
            let msg = format!("Failed to initialize Coperceive application: {err}");
            handle_fatal_error(&msg);
            std::process::exit(101);
        }
    };

    app.run(move |_app_handle, event| {
        match event {
            tauri::RunEvent::ExitRequested { .. } | tauri::RunEvent::Exit => {
                if let Ok(mut lock) = state_for_exit.sidecar.lock() {
                    if let Some(mut s) = lock.take() {
                        let _ = s.stop();
                    }
                }
                if let Ok(mut lock) = state_for_exit.remote.lock() {
                    if let Some(mut r) = lock.take() {
                        let _ = r.disconnect();
                    }
                }
            }
            _ => {}
        }
    });
}

