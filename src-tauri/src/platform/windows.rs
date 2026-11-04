// SPDX-License-Identifier: Apache-2.0
//! Windows platform implementation.

use std::path::{Path, PathBuf};

pub fn apply_viewer_window_chrome<'a, R: tauri::Runtime, M: tauri::Manager<R>>(
    builder: tauri::WebviewWindowBuilder<'a, R, M>,
) -> tauri::WebviewWindowBuilder<'a, R, M> {
    // Native decorations on Windows
    builder
}

pub fn reveal_path(path: &Path) -> Result<(), String> {
    std::process::Command::new("explorer")
        .arg(format!("/select,{}", path.display()))
        .spawn()
        .map_err(|e| format!("Failed to reveal in Explorer: {e}"))?;
    Ok(())
}

pub fn handle_fatal_error(err_msg: &str) {
    eprintln!("Fatal startup error: {err_msg}");
    let log_dir = get_log_dir();
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

pub fn copy_to_clipboard(text: &str) -> bool {
    if let Ok(mut child) = std::process::Command::new("clip")
        .stdin(std::process::Stdio::piped())
        .spawn()
    {
        if let Some(mut stdin) = child.stdin.take() {
            let _ = std::io::Write::write_all(&mut stdin, text.as_bytes());
            let _ = std::io::Write::flush(&mut stdin);
            drop(stdin);
            if let Ok(status) = child.wait() {
                return status.success();
            }
        }
    }
    false
}

pub fn terminate_process(pid: u32) -> Result<(), String> {
    let _ = std::process::Command::new("taskkill")
        .args(["/PID", &pid.to_string(), "/F"])
        .status();
    Ok(())
}

pub fn pause_process(_pid: u32) -> Result<(), String> {
    Err("Pausing dataset jobs is not supported on Windows yet".into())
}

pub fn resume_process(_pid: u32) -> Result<(), String> {
    Err("Resuming dataset jobs is not supported on Windows yet".into())
}

pub fn get_log_dir() -> PathBuf {
    if let Some(local_app) = std::env::var_os("LOCALAPPDATA") {
        PathBuf::from(local_app).join("Coperceive").join("logs")
    } else if let Some(profile) = std::env::var_os("USERPROFILE") {
        PathBuf::from(profile).join(".coperceive").join("logs")
    } else {
        PathBuf::from("logs")
    }
}

pub fn default_library_root() -> PathBuf {
    if let Ok(profile) = std::env::var("USERPROFILE") {
        PathBuf::from(profile).join("coperceive-data")
    } else if let Ok(home) = std::env::var("HOME") {
        PathBuf::from(home).join("coperceive-data")
    } else {
        PathBuf::from("coperceive-data")
    }
}

pub fn get_state_file_path() -> PathBuf {
    if let Some(dir) = std::env::var_os("COPERCEIVE_STATE_DIR") {
        return PathBuf::from(dir).join("state.json");
    }
    if let Some(local_app) = std::env::var_os("LOCALAPPDATA") {
        return PathBuf::from(local_app)
            .join("Coperceive")
            .join("state")
            .join("state.json");
    }
    if let Some(profile) = std::env::var_os("USERPROFILE") {
        return PathBuf::from(profile)
            .join(".coperceive")
            .join("state.json");
    }
    PathBuf::from(".coperceive-state.json")
}

pub fn get_servers_file_path() -> PathBuf {
    if let Some(dir) = std::env::var_os("COPERCEIVE_STATE_DIR") {
        return PathBuf::from(dir).join("servers.json");
    }
    if let Some(local_app) = std::env::var_os("LOCALAPPDATA") {
        return PathBuf::from(local_app)
            .join("Coperceive")
            .join("servers.json");
    }
    if let Some(profile) = std::env::var_os("USERPROFILE") {
        return PathBuf::from(profile)
            .join(".coperceive")
            .join("servers.json");
    }
    PathBuf::from(".coperceive-servers.json")
}

pub fn resolve_python_candidates(res_dir: &Path) -> Vec<PathBuf> {
    vec![
        res_dir.join("resources").join("python").join("python.exe"),
        res_dir.join("python").join("python.exe"),
        res_dir.join("python").join("install").join("python.exe"),
        res_dir.join("resources").join("python").join("bin").join("python3.exe"),
        res_dir.join("python").join("bin").join("python3.exe"),
    ]
}
