// SPDX-License-Identifier: Apache-2.0
//! macOS platform implementation.

use std::path::{Path, PathBuf};

pub fn apply_viewer_window_chrome<'a, R: tauri::Runtime, M: tauri::Manager<R>>(
    builder: tauri::WebviewWindowBuilder<'a, R, M>,
) -> tauri::WebviewWindowBuilder<'a, R, M> {
    builder
        .title_bar_style(tauri::TitleBarStyle::Overlay)
        .hidden_title(true)
        .traffic_light_position(tauri::LogicalPosition::new(16.0, 18.0))
}

pub fn create_command(bin: &str) -> std::process::Command {
    std::process::Command::new(bin)
}

pub fn reveal_path(path: &Path) -> Result<(), String> {
    std::process::Command::new("open")
        .arg("-R")
        .arg(path)
        .spawn()
        .map_err(|e| format!("Failed to reveal in Finder: {e}"))?;
    Ok(())
}

pub fn handle_fatal_error(err_msg: &str) {
    eprintln!("Fatal startup error: {err_msg}");
    if let Some(home) = std::env::var_os("HOME") {
        let log_dir = PathBuf::from(home).join("Library/Logs/Coperceive");
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

pub fn copy_to_clipboard(text: &str) -> bool {
    if let Ok(mut child) = std::process::Command::new("pbcopy")
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
    unsafe {
        libc::kill(pid as i32, libc::SIGTERM);
    }
    Ok(())
}

pub fn pause_process(pid: u32) -> Result<(), String> {
    unsafe {
        libc::kill(pid as i32, libc::SIGSTOP);
    }
    Ok(())
}

pub fn resume_process(pid: u32) -> Result<(), String> {
    unsafe {
        libc::kill(pid as i32, libc::SIGCONT);
    }
    Ok(())
}

pub fn get_log_dir() -> PathBuf {
    if let Some(home) = std::env::var_os("HOME") {
        PathBuf::from(home).join("Library/Logs/Coperceive")
    } else {
        PathBuf::from("logs")
    }
}

pub fn default_library_root() -> PathBuf {
    if let Ok(home) = std::env::var("HOME") {
        PathBuf::from(home).join("coperceive-data")
    } else {
        PathBuf::from("coperceive-data")
    }
}

pub fn get_state_file_path() -> PathBuf {
    if let Some(dir) = std::env::var_os("COPERCEIVE_STATE_DIR") {
        return PathBuf::from(dir).join("state.json");
    }
    if let Some(home) = std::env::var_os("HOME") {
        return PathBuf::from(home)
            .join("Library")
            .join("Application Support")
            .join("nl.adarsh.coperceive")
            .join("state.json");
    }
    PathBuf::from(".coperceive-state.json")
}

pub fn get_servers_file_path() -> PathBuf {
    if let Some(dir) = std::env::var_os("COPERCEIVE_STATE_DIR") {
        return PathBuf::from(dir).join("servers.json");
    }
    if let Some(home) = std::env::var_os("HOME") {
        return PathBuf::from(home)
            .join("Library")
            .join("Application Support")
            .join("nl.adarsh.coperceive")
            .join("servers.json");
    }
    PathBuf::from(".coperceive-servers.json")
}

pub fn resolve_python_candidates(res_dir: &Path) -> Vec<PathBuf> {
    vec![
        res_dir.join("resources").join("python").join("bin").join("python3"),
        res_dir.join("python").join("bin").join("python3"),
        res_dir.join("python").join("install").join("bin").join("python3"),
    ]
}
