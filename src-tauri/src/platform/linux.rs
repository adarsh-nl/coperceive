// SPDX-License-Identifier: Apache-2.0
//! Linux platform implementation.

use std::path::{Path, PathBuf};

pub fn apply_viewer_window_chrome<'a, R: tauri::Runtime, M: tauri::Manager<R>>(
    builder: tauri::WebviewWindowBuilder<'a, R, M>,
) -> tauri::WebviewWindowBuilder<'a, R, M> {
    // Native decorations on Linux
    builder
}

pub fn create_command(bin: &str) -> std::process::Command {
    std::process::Command::new(bin)
}

pub fn reveal_path(path: &Path) -> Result<(), String> {
    let target = if path.is_file() {
        path.parent().unwrap_or(path)
    } else {
        path
    };
    std::process::Command::new("xdg-open")
        .arg(target)
        .spawn()
        .map_err(|e| format!("Failed to reveal in file manager: {e}"))?;
    Ok(())
}

pub fn handle_fatal_error(err_msg: &str) {
    eprintln!("Fatal startup error: {err_msg}");
    let _ = std::io::Write::flush(&mut std::io::stderr());
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
        let _ = f.flush();
    }

    if std::env::var("CI").is_err() && !crate::test_seams::test_env_active() {
        let _ = std::process::Command::new("zenity")
            .args(["--error", "--text", err_msg, "--title", "Coperceive Startup Error", "--timeout=15"])
            .status();
    }
}

pub fn copy_to_clipboard(text: &str) -> bool {
    // Try wl-copy (Wayland) first, then xclip (X11)
    if let Ok(mut child) = std::process::Command::new("wl-copy")
        .stdin(std::process::Stdio::piped())
        .spawn()
    {
        if let Some(mut stdin) = child.stdin.take() {
            let _ = std::io::Write::write_all(&mut stdin, text.as_bytes());
            let _ = std::io::Write::flush(&mut stdin);
            drop(stdin);
            if let Ok(status) = child.wait() {
                if status.success() {
                    return true;
                }
            }
        }
    }
    if let Ok(mut child) = std::process::Command::new("xclip")
        .args(["-selection", "clipboard"])
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
    if let Some(state_home) = std::env::var_os("XDG_STATE_HOME") {
        PathBuf::from(state_home).join("coperceive")
    } else if let Some(home) = std::env::var_os("HOME") {
        PathBuf::from(home).join(".local/state/coperceive")
    } else {
        PathBuf::from("logs")
    }
}

pub fn default_library_root() -> PathBuf {
    if let Some(dir) = std::env::var_os("COPERCEIVE_STATE_DIR") {
        if !dir.is_empty() {
            return PathBuf::from(dir).join("coperceive-data");
        }
    }
    if let Ok(home) = std::env::var("HOME") {
        PathBuf::from(home).join("coperceive-data")
    } else {
        PathBuf::from("coperceive-data")
    }
}

pub fn get_state_file_path() -> PathBuf {
    if let Some(dir) = std::env::var_os("COPERCEIVE_STATE_DIR") {
        if !dir.is_empty() {
            return PathBuf::from(dir).join("state.json");
        }
    }
    if let Some(xdg) = std::env::var_os("XDG_DATA_HOME") {
        return PathBuf::from(xdg)
            .join("nl.adarsh.coperceive")
            .join("state.json");
    }
    if let Some(home) = std::env::var_os("HOME") {
        return PathBuf::from(home)
            .join(".local")
            .join("share")
            .join("nl.adarsh.coperceive")
            .join("state.json");
    }
    PathBuf::from(".coperceive-state.json")
}

pub fn get_servers_file_path() -> PathBuf {
    if let Some(dir) = std::env::var_os("COPERCEIVE_STATE_DIR") {
        return PathBuf::from(dir).join("servers.json");
    }
    if let Some(xdg) = std::env::var_os("XDG_DATA_HOME") {
        return PathBuf::from(xdg)
            .join("nl.adarsh.coperceive")
            .join("servers.json");
    }
    if let Some(home) = std::env::var_os("HOME") {
        return PathBuf::from(home)
            .join(".local")
            .join("share")
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
