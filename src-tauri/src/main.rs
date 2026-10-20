// SPDX-License-Identifier: Apache-2.0
//! Coperceive Desktop Application Entrypoint (Tauri v2)

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

#[cfg(target_os = "macos")]
use std::path::{Path, PathBuf};
#[cfg(target_os = "macos")]
use std::sync::{Arc, Mutex};
#[cfg(target_os = "macos")]
use std::time::{Duration, Instant};

#[cfg(target_os = "macos")]
use serde::{Deserialize, Serialize};

#[cfg(target_os = "macos")]
use coperceive_desktop::remote::{RemoteClient, RemoteSessionManager, RemoteStatusResponse};
#[cfg(target_os = "macos")]
use coperceive_desktop::sidecar::SidecarSession;

#[cfg(target_os = "macos")]
#[derive(Default)]
pub struct AppState {
    pub sidecar: Mutex<Option<SidecarSession>>,
    pub remote: Mutex<Option<RemoteSessionManager>>,
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
async fn pick_data_root(app: tauri::AppHandle) -> Result<Option<String>, String> {
    use tauri_plugin_dialog::DialogExt;

    let folder = app.dialog().file().blocking_pick_folder();
    Ok(folder.and_then(|fp| fp.into_path().ok()).map(|p| p.to_string_lossy().into_owned()))
}

#[cfg(target_os = "macos")]
#[tauri::command]
async fn start_local(
    app: tauri::AppHandle,
    state: tauri::State<'_, Arc<AppState>>,
    data_root: String,
) -> Result<SessionPayload, String> {
    let root_path = PathBuf::from(&data_root);
    if !root_path.is_dir() {
        return Err(format!("Data root directory does not exist: {}", data_root));
    }

    // Stop existing session if one was running
    {
        let mut lock = state.sidecar.lock().map_err(|e| e.to_string())?;
        if let Some(mut existing) = lock.take() {
            let _ = existing.stop();
        }
    }

    let python_bin = resolve_python_binary(&app);
    let mut session = SidecarSession::spawn(&python_bin, &root_path)
        .map_err(|e| format!("Failed to spawn local sidecar: {}", e))?;

    let origin = format!("http://127.0.0.1:{}", session.port);
    let token = session.token.clone();

    // Poll until healthy (up to 20 seconds)
    reqwest_like_poll(&origin, &token, Duration::from_secs(20))
        .map_err(|e| format!("Timed out waiting for local serve: {}", e))?;

    let payload = SessionPayload {
        origin,
        token,
        server_version: "0.0.0".to_string(),
        data_root,
        target_type: "local".to_string(),
    };

    let mut lock = state.sidecar.lock().map_err(|e| e.to_string())?;
    *lock = Some(session);

    Ok(payload)
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
#[cfg(target_os = "macos")]
fn reqwest_like_poll(origin: &str, token: &str, timeout: Duration) -> Result<(), String> {
    use std::io::{Read, Write};
    use std::net::TcpStream;

    let port_str = origin.split(':').last().unwrap_or("8731");
    let port: u16 = port_str.parse().unwrap_or(8731);
    let deadline = Instant::now() + timeout;

    while Instant::now() < deadline {
        if let Ok(mut stream) = TcpStream::connect(("127.0.0.1", port)) {
            let _ = stream.set_read_timeout(Some(Duration::from_millis(500)));
            let _ = stream.set_write_timeout(Some(Duration::from_millis(500)));
            let req = format!(
                "GET /api/health HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nAuthorization: Bearer {}\r\nConnection: close\r\n\r\n",
                port, token
            );
            if stream.write_all(req.as_bytes()).is_ok() {
                let mut buf = [0u8; 512];
                if let Ok(n) = stream.read(&mut buf) {
                    let resp = String::from_utf8_lossy(&buf[..n]);
                    if resp.contains("200 OK") {
                        return Ok(());
                    }
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
    let state_for_window = Arc::clone(&app_state);
    let state_for_exit = Arc::clone(&app_state);

    let builder = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(app_state)
        .invoke_handler(tauri::generate_handler![
            pick_data_root,
            start_local,
            stop_local,
            health,
            remote_connect,
            remote_status,
            remote_disconnect,
        ])
        .on_window_event(move |_window, event| {
            if let tauri::WindowEvent::Destroyed = event {
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
        if let tauri::RunEvent::Exit = event {
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
    });
}

#[cfg(not(target_os = "macos"))]
fn main() {
    println!("coperceive-desktop: macOS Apple Silicon target build.");
}
