// SPDX-License-Identifier: Apache-2.0
//! Windows platform implementation.

use std::path::{Path, PathBuf};

pub fn apply_viewer_window_chrome<'a, R: tauri::Runtime, M: tauri::Manager<R>>(
    builder: tauri::WebviewWindowBuilder<'a, R, M>,
) -> tauri::WebviewWindowBuilder<'a, R, M> {
    // Native decorations on Windows
    builder.decorations(true)
}

pub fn create_command(bin: &str) -> std::process::Command {
    if bin.ends_with(".sh") {
        let sh_bin = find_sh();
        let mut cmd = std::process::Command::new(sh_bin);
        cmd.arg(bin);
        return cmd;
    }
    std::process::Command::new(bin)
}

fn find_sh() -> String {
    for candidate in [
        "sh",
        "bash",
        r"C:\Program Files\Git\bin\sh.exe",
        r"C:\Program Files\Git\usr\bin\sh.exe",
        r"C:\Program Files (x86)\Git\bin\sh.exe",
    ] {
        if let Ok(status) = std::process::Command::new(candidate).arg("-c").arg("exit 0").status() {
            if status.success() {
                return candidate.to_string();
            }
        }
    }
    "sh".to_string()
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

// Windows native process suspend/resume via NtSuspendProcess and NtResumeProcess in ntdll.dll
#[link(name = "kernel32")]
extern "system" {
    fn OpenProcess(dwDesiredAccess: u32, bInheritHandle: i32, dwProcessId: u32) -> *mut std::ffi::c_void;
    fn CloseHandle(hObject: *mut std::ffi::c_void) -> i32;
    fn GetProcAddress(hModule: *mut std::ffi::c_void, lpProcName: *const u8) -> *const std::ffi::c_void;
    fn GetModuleHandleA(lpModuleName: *const u8) -> *mut std::ffi::c_void;
}

type NtSuspendProcessFn = unsafe extern "system" fn(*mut std::ffi::c_void) -> i32;
type NtResumeProcessFn = unsafe extern "system" fn(*mut std::ffi::c_void) -> i32;

pub fn pause_process(pid: u32) -> Result<(), String> {
    unsafe {
        let handle = OpenProcess(0x0800 | 0x0002, 0, pid); // PROCESS_SUSPEND_RESUME | PROCESS_TERMINATE
        if handle.is_null() {
            return Err(format!("Failed to open process {pid} for suspend"));
        }
        let ntdll = GetModuleHandleA(b"ntdll.dll\0".as_ptr());
        if ntdll.is_null() {
            CloseHandle(handle);
            return Err("Failed to get ntdll.dll handle".into());
        }
        let func_ptr = GetProcAddress(ntdll, b"NtSuspendProcess\0".as_ptr());
        if func_ptr.is_null() {
            CloseHandle(handle);
            return Err("NtSuspendProcess not found in ntdll.dll".into());
        }
        let nt_suspend: NtSuspendProcessFn = std::mem::transmute(func_ptr);
        let status = nt_suspend(handle);
        CloseHandle(handle);
        if status != 0 {
            return Err(format!("NtSuspendProcess failed with status {status:#x}"));
        }
    }
    Ok(())
}

pub fn resume_process(pid: u32) -> Result<(), String> {
    unsafe {
        let handle = OpenProcess(0x0800 | 0x0002, 0, pid);
        if handle.is_null() {
            return Err(format!("Failed to open process {pid} for resume"));
        }
        let ntdll = GetModuleHandleA(b"ntdll.dll\0".as_ptr());
        if ntdll.is_null() {
            CloseHandle(handle);
            return Err("Failed to get ntdll.dll handle".into());
        }
        let func_ptr = GetProcAddress(ntdll, b"NtResumeProcess\0".as_ptr());
        if func_ptr.is_null() {
            CloseHandle(handle);
            return Err("NtResumeProcess not found in ntdll.dll".into());
        }
        let nt_resume: NtResumeProcessFn = std::mem::transmute(func_ptr);
        let status = nt_resume(handle);
        CloseHandle(handle);
        if status != 0 {
            return Err(format!("NtResumeProcess failed with status {status:#x}"));
        }
    }
    Ok(())
}

/// Restricts file ACLs to the current user only using `icacls`.
pub fn protect_file_current_user(path: &Path) -> Result<(), String> {
    let username = std::env::var("USERNAME").unwrap_or_else(|_| "%USERNAME%".to_string());
    let grant = format!("{username}:(F)");
    let output = std::process::Command::new("icacls")
        .arg(path)
        .arg("/inheritance:r")
        .arg("/grant:r")
        .arg(&grant)
        .output()
        .map_err(|e| format!("Failed to run icacls: {e}"))?;
    if !output.status.success() {
        let err = String::from_utf8_lossy(&output.stderr);
        return Err(format!("icacls failed: {err}"));
    }
    Ok(())
}

/// Verifies that the file ACLs do not grant read access to Everyone or Authenticated Users.
pub fn verify_file_current_user(path: &Path) -> Result<(), String> {
    let output = std::process::Command::new("icacls")
        .arg(path)
        .output()
        .map_err(|e| format!("Failed to run icacls: {e}"))?;
    if !output.status.success() {
        let err = String::from_utf8_lossy(&output.stderr);
        return Err(format!("icacls check failed: {err}"));
    }
    let stdout = String::from_utf8_lossy(&output.stdout);
    let forbidden = ["everyone", "builtin\\users", "nt authority\\authenticated users"];
    for group in forbidden {
        if stdout.to_lowercase().contains(group) {
            return Err(format!("File has loose permissions allowing {group}"));
        }
    }
    Ok(())
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
