// SPDX-License-Identifier: Apache-2.0
//! Coperceive Desktop Application Entrypoint (Tauri v2)

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

#[cfg(target_os = "macos")]
use std::fs;
#[cfg(target_os = "macos")]
use std::path::{Path, PathBuf};
#[cfg(target_os = "macos")]
use std::sync::{Arc, Mutex};
#[cfg(target_os = "macos")]
use std::time::{Duration, Instant};

#[cfg(target_os = "macos")]
use serde::{Deserialize, Serialize};

#[cfg(target_os = "macos")]
use tauri::Manager;

#[cfg(target_os = "macos")]
use coperceive_desktop::controller::{self, ControllerConfig};
#[cfg(target_os = "macos")]
use coperceive_desktop::remote::{RemoteClient, RemoteSessionManager, RemoteStatusResponse};
#[cfg(target_os = "macos")]
use coperceive_desktop::sidecar::SidecarSession;
#[cfg(target_os = "macos")]
use coperceive_desktop::state::{self, AppPersistentState};

#[cfg(target_os = "macos")]
pub struct AppState {
    pub sidecar: Mutex<Option<SidecarSession>>,
    pub remote: Mutex<Option<RemoteSessionManager>>,
    pub ctrl_port: Mutex<Option<u16>>,
    pub ctrl_secret: Mutex<Option<String>>,
    pub controller_config: Mutex<Option<ControllerConfig>>,
    pub viewer_origin: Mutex<Option<String>>,
    pub current_root: Mutex<Option<String>>,
    pub startup_instant: Instant,
}

#[cfg(target_os = "macos")]
impl Default for AppState {
    fn default() -> Self {
        Self {
            sidecar: Mutex::new(None),
            remote: Mutex::new(None),
            ctrl_port: Mutex::new(None),
            ctrl_secret: Mutex::new(None),
            controller_config: Mutex::new(None),
            viewer_origin: Mutex::new(None),
            current_root: Mutex::new(None),
            startup_instant: Instant::now(),
        }
    }
}

#[cfg(target_os = "macos")]
#[derive(Debug, Serialize, Deserialize)]
pub struct SessionPayload {
    pub origin: String,
    pub token: String,
    pub server_version: String,
    pub data_root: String,
    pub target_type: String,
}

#[cfg(target_os = "macos")]
#[derive(Debug, Serialize, Deserialize)]
pub struct HealthPayload {
    pub healthy: bool,
    pub latency_ms: u64,
    pub server_version: Option<String>,
}

/// Resolves the bundled python-build-standalone interpreter from the Tauri resource directory.
/// Falls back to dev python if running unbundled.
#[cfg(target_os = "macos")]
fn resolve_python_binary(app: &tauri::AppHandle) -> PathBuf {
    use tauri::Manager;

    if let Ok(res_dir) = app.path().resource_dir() {
        // Pinned python-build-standalone inside Tauri resources
        let candidates = [
            res_dir.join("resources").join("python").join("bin").join("python3"),
            res_dir.join("python").join("bin").join("python3"),
            res_dir.join("python").join("install").join("bin").join("python3"),
        ];
        for candidate in &candidates {
            if candidate.is_file() {
                return candidate.clone();
            }
        }
    }

    if let Ok(env_py) = std::env::var("COPERCEIVE_PYTHON") {
        return PathBuf::from(env_py);
    }

    PathBuf::from("python3")
}

#[cfg(target_os = "macos")]
#[tauri::command]
async fn get_persistent_state() -> Result<AppPersistentState, String> {
    Ok(state::load_state())
}

#[cfg(target_os = "macos")]
#[tauri::command]
async fn is_remote_enabled() -> Result<bool, String> {
    Ok(std::env::var("COPERCEIVE_REMOTE").map(|v| v == "1" || v.to_lowercase() == "true").unwrap_or(false))
}

#[cfg(target_os = "macos")]
#[tauri::command]
async fn pick_data_root(app: tauri::AppHandle) -> Result<Option<String>, String> {
    use tauri_plugin_dialog::DialogExt;

    let folder = app.dialog().file().blocking_pick_folder();
    Ok(folder.and_then(|fp| fp.into_path().ok()).map(|p| p.to_string_lossy().into_owned()))
}

#[cfg(target_os = "macos")]
fn open_viewer_window(
    app: &tauri::AppHandle,
    origin: &str,
    token: &str,
    data_root: &str,
    ctrl_port: u16,
    state: &AppState,
) -> Result<(), String> {
    use tauri::Manager;

    if let Some(existing_viewer) = app.get_webview_window("viewer") {
        let _ = existing_viewer.close();
    }

    if let Ok(mut lock) = state.viewer_origin.lock() {
        *lock = Some(origin.to_string());
    }

    let viewer_url_str = format!("{}/#/viewer", origin);
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

    let basename = Path::new(data_root)
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or(data_root);

    let escaped_origin = origin.replace('\\', "\\\\").replace('"', "\\\"");
    let escaped_token = token.replace('\\', "\\\\").replace('"', "\\\"");
    let escaped_secret = ctrl_secret.replace('\\', "\\\\").replace('"', "\\\"");
    let escaped_root = data_root.replace('\\', "\\\\").replace('"', "\\\"");
    let escaped_base = basename.replace('\\', "\\\\").replace('"', "\\\"");
    let test_env = std::env::var("COPERCEIVE_TEST_ENV").is_ok()
        || std::env::var("COPERCEIVE_TEST_MARKER_FILE").is_ok();
    let force_motion =
        test_env && std::env::var("COPERCEIVE_FORCE_MOTION").ok().as_deref() == Some("1");

    let init_script = format!(
        r#"
        (function() {{
            try {{
                window.__COPERCEIVE_DESKTOP__ = true;
                window.__COPERCEIVE_TEST_ENV__ = {test_env};
                window.__COPERCEIVE_FORCE_MOTION__ = {force_motion};
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

#[cfg(target_os = "macos")]
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
    let mut new_session = SidecarSession::spawn(&python_bin, &new_path, current_viewer_origin.as_deref())
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

        // Update application menu with refreshed recent roots
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

#[cfg(target_os = "macos")]
pub async fn trigger_open_dataset_dialog(app: &tauri::AppHandle, state: &AppState) {
    if let Ok(mock_file) = std::env::var("COPERCEIVE_MOCK_DIALOG_FILE") {
        if let Ok(content) = std::fs::read_to_string(&mock_file) {
            let path_str = content.trim().to_string();
            let _ = std::fs::remove_file(&mock_file);
            let path = PathBuf::from(path_str);
            let _ = switch_data_root(app, state, path).await;
            return;
        }
    }

    use tauri_plugin_dialog::DialogExt;
    let folder = app.dialog().file().blocking_pick_folder();
    if let Some(path) = folder.and_then(|fp| fp.into_path().ok()) {
        let _ = switch_data_root(app, state, path).await;
    }
}

#[cfg(target_os = "macos")]
fn copy_diagnostics_to_clipboard(app: &tauri::AppHandle, state: &AppState) {
    let version = env!("CARGO_PKG_VERSION");
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

    let mut copied = false;
    if let Ok(mut child) = std::process::Command::new("pbcopy")
        .stdin(std::process::Stdio::piped())
        .spawn()
    {
        if let Some(mut stdin) = child.stdin.take() {
            let _ = std::io::Write::write_all(&mut stdin, diagnostics.as_bytes());
            copied = true;
        }
        let _ = child.wait();
    }

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

#[cfg(target_os = "macos")]
fn start_desktop_controller(app_handle: tauri::AppHandle, app_state: Arc<AppState>) -> Result<u16, String> {
    let secret = coperceive_desktop::token::generate_secure_token().map_err(|e| e.to_string())?;
    let mut cfg = ControllerConfig::new(0, secret.clone(), None);
    let handle_eval = app_handle.clone();
    cfg.eval_dispatcher = Some(Arc::new(move |script: String| {
        use tauri::Manager;
        if let Some(w) = handle_eval
            .get_webview_window("viewer")
            .or_else(|| handle_eval.get_webview_window("main"))
        {
            let _ = w.eval(&script);
        }
    }));
    let handle_close = app_handle.clone();
    cfg.window_closer = Some(Arc::new(move |label: &str| {
        use tauri::Manager;
        if let Some(w) = handle_close.get_webview_window(label) {
            let _ = w.close();
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

    // View Submenu
    let view_submenu = SubmenuBuilder::new(app, "View")
        .item(&MenuItem::with_id(app, "viewpoint_orbit", "Orbit", true, Some("1"))?)
        .item(&MenuItem::with_id(app, "viewpoint_chase", "Chase", true, Some("2"))?)
        .item(&MenuItem::with_id(app, "viewpoint_drone", "Drone", true, Some("3"))?)
        .item(&MenuItem::with_id(app, "viewpoint_bird", "Bird", true, Some("4"))?)
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
        .items(&[&app_submenu, &file_submenu, &view_submenu, &playback_submenu, &help_submenu])
        .build()
}

#[cfg(target_os = "macos")]
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
    }

    let python_bin = resolve_python_binary(app);
    let mut session = SidecarSession::spawn(&python_bin, &root_path, None)
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
        // Copy serve-<port>.log to ~/Library/Logs/Coperceive/
        if let Some(home) = std::env::var_os("HOME") {
            let log_dir = PathBuf::from(home).join("Library/Logs/Coperceive");
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
    if let Some(home) = std::env::var_os("HOME") {
        let log_dir = PathBuf::from(home).join("Library/Logs/Coperceive");
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

        // Update application menu with refreshed recent roots
        if let Ok(menu) = build_app_menu(app, &persistent.recent_roots) {
            let _ = app.set_menu(menu);
        }
    }

    let ctrl_port = state.ctrl_port.lock().ok().and_then(|p| *p).unwrap_or(0);

    // Open viewer window at session.origin with initialization_script
    if let Err(e) = open_viewer_window(app, &origin, &token, &data_root, ctrl_port, state) {
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

#[cfg(target_os = "macos")]
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

#[cfg(target_os = "macos")]
#[tauri::command]
async fn stop_local(state: tauri::State<'_, Arc<AppState>>) -> Result<(), String> {
    let mut lock = state.sidecar.lock().map_err(|e| e.to_string())?;
    if let Some(mut session) = lock.take() {
        session.stop().map_err(|e| format!("Failed to stop sidecar: {}", e))?;
    }
    Ok(())
}

#[cfg(target_os = "macos")]
static IMPORT_PID: Mutex<Option<u32>> = Mutex::new(None);

#[cfg(target_os = "macos")]
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

#[cfg(target_os = "macos")]
#[tauri::command]
fn cancel_import() -> Result<(), String> {
    let pid = {
        let mut slot = IMPORT_PID.lock().map_err(|err| err.to_string())?;
        slot.take()
    };
    if let Some(pid) = pid {
        // SAFETY: pid is the id of the scene-pack child this process spawned.
        unsafe {
            libc::kill(pid as i32, libc::SIGTERM);
        }
    }
    Ok(())
}

#[cfg(target_os = "macos")]
static DATASET_JOB_PID: Mutex<Option<u32>> = Mutex::new(None);

#[cfg(target_os = "macos")]
#[tauri::command]
fn get_storage_info() -> Result<coperceive_desktop::library::StorageStats, String> {
    let p = state::load_state();
    let is_configured = p.library_root.is_some();
    let root = p
        .library_root
        .unwrap_or_else(|| state::default_library_root().to_string_lossy().into_owned());
    Ok(coperceive_desktop::library::get_storage_stats(Path::new(&root), is_configured))
}

#[cfg(target_os = "macos")]
#[tauri::command]
fn set_library_root(new_root: String) -> Result<coperceive_desktop::library::StorageStats, String> {
    let mut p = state::load_state();
    p.library_root = Some(new_root.clone());
    let _ = state::save_state(&p);
    Ok(coperceive_desktop::library::get_storage_stats(Path::new(&new_root), true))
}

#[cfg(target_os = "macos")]
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

#[cfg(target_os = "macos")]
#[tauri::command]
fn get_library_scenes() -> Result<Vec<coperceive_desktop::library::LibrarySceneInfo>, String> {
    let p = state::load_state();
    let root = p
        .library_root
        .unwrap_or_else(|| state::default_library_root().to_string_lossy().into_owned());
    Ok(coperceive_desktop::library::scan_library_scenes(Path::new(&root)))
}

#[cfg(target_os = "macos")]
#[tauri::command]
fn delete_library_scene(scene_path: String) -> Result<(), String> {
    coperceive_desktop::library::delete_scene_to_trash(Path::new(&scene_path))
        .map_err(|e| format!("Failed to delete scene: {e}"))
}

#[cfg(target_os = "macos")]
#[tauri::command]
fn reveal_in_finder(path: String) -> Result<(), String> {
    std::process::Command::new("open")
        .arg("-R")
        .arg(&path)
        .spawn()
        .map_err(|e| format!("Failed to reveal in Finder: {e}"))?;
    Ok(())
}

#[cfg(target_os = "macos")]
#[tauri::command]
fn dataset_dry_run(
    app: tauri::AppHandle,
    dataset_id: String,
    preset_id: String,
) -> Result<serde_json::Value, String> {
    use std::process::Command;

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

#[cfg(target_os = "macos")]
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
    let p = state::load_state();
    let root = p
        .library_root
        .unwrap_or_else(|| state::default_library_root().to_string_lossy().into_owned());
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

    let mut child = Command::new(&python)
        .args(&cmd_args)
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

#[cfg(target_os = "macos")]
#[tauri::command]
fn cancel_dataset_job() -> Result<(), String> {
    let pid = {
        let mut slot = DATASET_JOB_PID.lock().map_err(|err| err.to_string())?;
        slot.take()
    };
    if let Some(pid) = pid {
        unsafe {
            libc::kill(pid as i32, libc::SIGTERM);
        }
    }
    Ok(())
}

#[cfg(target_os = "macos")]
#[tauri::command]
fn validate_scene(
    app: tauri::AppHandle,
    scene_path: String,
) -> Result<serde_json::Value, String> {
    use std::process::Command;

    let python = resolve_python_binary(&app);
    let output = Command::new(&python)
        .args([
            "-m",
            "coperceive.cli",
            "validate",
            &scene_path,
        ])
        .output()
        .map_err(|e| format!("Failed to validate scene: {e}"))?;

    let success = output.status.success();
    Ok(serde_json::json!({
        "scene_path": scene_path,
        "valid": success,
        "stdout": String::from_utf8_lossy(&output.stdout),
        "stderr": String::from_utf8_lossy(&output.stderr),
    }))
}

#[cfg(target_os = "macos")]
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

#[cfg(target_os = "macos")]
#[tauri::command]
async fn remote_connect(
    app: tauri::AppHandle,
    state: tauri::State<'_, Arc<AppState>>,
    alias: String,
    data_root: String,
    uuid: Option<String>,
) -> Result<SessionPayload, String> {
    // Disconnect existing session if one was active
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
    }

    let mgr = RemoteSessionManager::connect(&alias, &data_root, uuid.as_deref(), None)
        .map_err(|e| e.to_string())?;

    let origin = mgr.origin();
    let token = mgr.token.clone();
    let ctrl_port = state.ctrl_port.lock().ok().and_then(|p| *p).unwrap_or(0);

    open_viewer_window(&app, &origin, &token, &data_root, ctrl_port, state.inner())?;

    let payload = SessionPayload {
        origin,
        token,
        server_version: "0.0.0".to_string(),
        data_root,
        target_type: "remote".to_string(),
    };

    let mut lock = state.remote.lock().map_err(|e| e.to_string())?;
    *lock = Some(mgr);

    Ok(payload)
}

#[cfg(target_os = "macos")]
#[tauri::command]
async fn remote_status(
    alias: String,
    uuid: Option<String>,
    job_id: Option<u64>,
) -> Result<RemoteStatusResponse, String> {
    let client = RemoteClient::new(&alias);
    client.status(uuid.as_deref(), job_id).map_err(|e| e.to_string())
}

#[cfg(target_os = "macos")]
#[tauri::command]
async fn remote_disconnect(state: tauri::State<'_, Arc<AppState>>) -> Result<(), String> {
    let mut lock = state.remote.lock().map_err(|e| e.to_string())?;
    if let Some(mut mgr) = lock.take() {
        mgr.disconnect().map_err(|e| e.to_string())?;
    }
    Ok(())
}

/// Simple loopback HTTP polling using TcpStream without external HTTP client crate.
/// Reads until headers end (\r\n\r\n) rather than assuming a single buffer read.
#[cfg(target_os = "macos")]
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

#[cfg(target_os = "macos")]
fn handle_fatal_error(err_msg: &str) {
    eprintln!("Fatal startup error: {err_msg}");
    if let Some(home) = std::env::var_os("HOME") {
        let log_dir = std::path::PathBuf::from(home).join("Library/Logs/Coperceive");
        let _ = std::fs::create_dir_all(&log_dir);
        let log_file = log_dir.join("startup-error.log");
        if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(&log_file) {
            use std::io::Write;
            let now = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_secs())
                .unwrap_or(0);
            let _ = writeln!(f, "[{now}] Fatal startup error: {err_msg}");
        }
    }

    let escaped = err_msg.replace('\\', "\\\\").replace('"', "\\\"").replace('\n', "\\n");
    let script = format!(
        "display alert \"Coperceive Startup Error\" message \"{escaped}\" as critical giving up after 15"
    );
    let _ = std::process::Command::new("osascript")
        .arg("-e")
        .arg(script)
        .status();
}

#[cfg(target_os = "macos")]
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
            set_library_root,
            move_library_root,
            get_library_scenes,
            delete_library_scene,
            reveal_in_finder,
            dataset_dry_run,
            start_dataset_job,
            cancel_dataset_job,
            validate_scene,
            health,
            remote_connect,
            remote_status,
            remote_disconnect,
        ])
        .on_page_load(|webview, payload| {
            // The Connect window is declared in tauri.conf.json, so it does not
            // get the viewer initialization_script. This test hook is the only
            // way the measurement can force the animated backdrop.
            let test_env = std::env::var("COPERCEIVE_TEST_ENV").is_ok()
                || std::env::var("COPERCEIVE_TEST_MARKER_FILE").is_ok();
            let force = test_env
                && std::env::var("COPERCEIVE_FORCE_MOTION").ok().as_deref() == Some("1");
            if !force || webview.label() != "main" {
                return;
            }
            if payload.event() != tauri::webview::PageLoadEvent::Finished {
                return;
            }
            let _ = webview.eval(
                "window.__COPERCEIVE_TEST_ENV__ = true; window.__COPERCEIVE_FORCE_MOTION__ = true; window.dispatchEvent(new Event('coperceive-force-motion'));",
            );
        })
        .setup(|app| {
            use tauri::Manager;
            let state = app.state::<Arc<AppState>>().inner().clone();
            let handle = app.handle().clone();

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

            // 2. Set macOS native application menu
            let persistent = state::load_state();
            if let Ok(menu) = build_app_menu(&handle, &persistent.recent_roots) {
                let _ = app.set_menu(menu);
            }

            // 3. Auto-connect on launch if data root exists and is valid
            let auto_root = if persistent.reopen_last_session {
                std::env::var("COPERCEIVE_AUTOCONNECT_ROOT")
                    .or_else(|_| std::env::var("COPERCEIVE_DESKTOP_AUTOCONNECT_ROOT"))
                    .ok()
                    .or_else(|| persistent.last_data_root.clone())
            } else {
                std::env::var("COPERCEIVE_AUTOCONNECT_ROOT")
                    .or_else(|_| std::env::var("COPERCEIVE_DESKTOP_AUTOCONNECT_ROOT"))
                    .ok()
            };

            if let Some(root_str) = auto_root {
                let root_path = PathBuf::from(&root_str);
                if state::validate_dataset_root(&root_path).is_ok() {
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
                    let version = env!("CARGO_PKG_VERSION");
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
                        if let Ok(mut lock) = state_for_window.sidecar.lock() {
                            if let Some(mut s) = lock.take() {
                                let _ = s.stop();
                            }
                        }
                        let _ = window.destroy();
                        if let Some(main) = window.app_handle().get_webview_window("main") {
                            let _ = main.show();
                            let _ = main.set_focus();
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
                    use tauri::Manager;
                    let label = window.label().to_string();
                    if label == "viewer" {
                        if let Ok(mut lock) = state_for_window.sidecar.lock() {
                            if let Some(mut s) = lock.take() {
                                let _ = s.stop();
                            }
                        }
                        if let Some(main) = window.app_handle().get_webview_window("main") {
                            let _ = main.show();
                            let _ = main.set_focus();
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

    let app = match builder.build(tauri::generate_context!()) {
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

#[cfg(not(target_os = "macos"))]
fn main() {
    println!("coperceive-desktop: macOS Apple Silicon target build.");
}
