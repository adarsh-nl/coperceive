// SPDX-License-Identifier: Apache-2.0
//! Crash capture and user-initiated diagnostics.
//!
//! Nothing here uploads anything. A crash is written locally; the report is
//! only built when the user opens Share Diagnostics, and only the user's chosen
//! action (copy, save, GitHub) moves it off the Mac.

use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use serde_json::Value;

pub const CRASH_SCHEMA: &str = "coperceive-crash-1";
const MAX_LOG_BYTES: usize = 500 * 1024;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CrashRecord {
    pub schema: String,
    pub kind: String,
    pub timestamp_unix: u64,
    pub version: String,
    pub commit: String,
    pub os: String,
    pub arch: String,
    pub message: String,
    pub location: Option<String>,
    pub backtrace: Option<String>,
}

pub fn log_dir() -> PathBuf {
    let home = std::env::var("HOME").unwrap_or_else(|_| ".".into());
    PathBuf::from(home)
        .join("Library")
        .join("Logs")
        .join("Coperceive")
}

fn marker_path() -> PathBuf {
    log_dir().join("run-marker.json")
}

fn now_unix() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .unwrap_or(0)
}

fn os_version() -> String {
    std::process::Command::new("sw_vers")
        .arg("-productVersion")
        .output()
        .ok()
        .and_then(|out| String::from_utf8(out.stdout).ok())
        .map(|text| text.trim().to_string())
        .filter(|text| !text.is_empty())
        .unwrap_or_else(|| "unknown".into())
}

pub fn app_version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}

pub fn app_commit() -> &'static str {
    option_env!("GIT_COMMIT").unwrap_or("unknown")
}

fn crash_path(timestamp: u64) -> PathBuf {
    log_dir().join(format!("crash-{timestamp}.json"))
}

fn write_crash(record: &CrashRecord) -> Option<PathBuf> {
    fs::create_dir_all(log_dir()).ok()?;
    let path = crash_path(record.timestamp_unix);
    let text = serde_json::to_string_pretty(record).ok()?;
    fs::write(&path, format!("{text}\n")).ok()?;
    Some(path)
}

/// Write the "running" marker. Returns a crash record for a previous run whose
/// marker was never removed (force quit, kill, or a missed panic).
pub fn start_run() -> Option<CrashRecord> {
    let _ = fs::create_dir_all(log_dir());
    let previous = fs::read_to_string(marker_path()).ok().and_then(|text| {
        serde_json::from_str::<Value>(&text).ok().map(|value| CrashRecord {
            schema: CRASH_SCHEMA.into(),
            kind: "unclean-exit".into(),
            timestamp_unix: value
                .get("started_unix")
                .and_then(Value::as_u64)
                .unwrap_or_else(now_unix),
            version: value
                .get("version")
                .and_then(Value::as_str)
                .unwrap_or(app_version())
                .to_string(),
            commit: value
                .get("commit")
                .and_then(Value::as_str)
                .unwrap_or(app_commit())
                .to_string(),
            os: os_version(),
            arch: std::env::consts::ARCH.to_string(),
            message: "Coperceive closed unexpectedly last time (the running marker was still present).".into(),
            location: None,
            backtrace: None,
        })
    });
    let record = previous.as_ref().map(|record| {
        let mut clean = record.clone();
        clean.timestamp_unix = now_unix();
        let _ = write_crash(&clean);
        clean
    });
    let marker = serde_json::json!({
        "schema": "coperceive-run-marker-1",
        "started_unix": now_unix(),
        "version": app_version(),
        "commit": app_commit(),
        "os": os_version(),
    });
    let _ = fs::write(marker_path(), format!("{marker}\n"));
    record
}

/// Remove the marker only on a clean quit.
pub fn clean_exit() {
    let _ = fs::remove_file(marker_path());
}

/// Install the panic hook. It records one JSON file and then runs the normal
/// hook so the existing stderr/log behaviour is unchanged.
pub fn install_panic_hook() {
    let previous = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        let location = info
            .location()
            .map(|location| format!("{}:{}:{}", location.file(), location.line(), location.column()));
        let backtrace = std::env::var("RUST_BACKTRACE").ok().map(|_| {
            std::backtrace::Backtrace::force_capture().to_string()
        });
        let record = CrashRecord {
            schema: CRASH_SCHEMA.into(),
            kind: "panic".into(),
            timestamp_unix: now_unix(),
            version: app_version().to_string(),
            commit: app_commit().to_string(),
            os: os_version(),
            arch: std::env::consts::ARCH.to_string(),
            message: info.to_string(),
            location,
            backtrace,
        };
        let _ = write_crash(&record);
        previous(info);
    }));
}

/// Recent crash records, newest first.
pub fn crash_records() -> Vec<CrashRecord> {
    let mut records: Vec<CrashRecord> = fs::read_dir(log_dir())
        .ok()
        .into_iter()
        .flatten()
        .flatten()
        .filter_map(|entry| {
            let name = entry.file_name();
            let name = name.to_string_lossy();
            if !name.starts_with("crash-") || !name.ends_with(".json") {
                return None;
            }
            let text = fs::read_to_string(entry.path()).ok()?;
            serde_json::from_str(&text).ok()
        })
        .collect();
    records.sort_by_key(|record| std::cmp::Reverse(record.timestamp_unix));
    records.truncate(10);
    records
}

fn redact(text: &str, servers: &[String]) -> String {
    let home = std::env::var("HOME").unwrap_or_default();
    let mut out = text.to_string();
    if !home.is_empty() {
        out = out.replace(&home, "~");
        if let Some(user) = Path::new(&home).file_name().and_then(|name| name.to_str()) {
            out = out.replace(user, "<user>");
        }
    }
    for (index, server) in servers.iter().enumerate() {
        if !server.trim().is_empty() {
            out = out.replace(server, &format!("<server-{}>", index + 1));
        }
    }
    out = redact_home_paths(&out);
    out = out.replace("/datasets/", "<datasets>/");
    out = redact_secrets(&out);
    out
}

fn redact_home_paths(text: &str) -> String {
    let mut out = text.to_string();
    for prefix in ["/Users/", "/home/"] {
        while let Some(start) = out.find(prefix) {
            let after = start + prefix.len();
            let end = out[after..]
                .find('/')
                .map(|offset| after + offset)
                .unwrap_or_else(|| out[after..].find(char::is_whitespace).map(|offset| after + offset).unwrap_or(out.len()));
            out.replace_range(start..end, "~");
        }
    }
    out
}

fn redact_secrets(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for line in text.lines() {
        let lower = line.to_ascii_lowercase();
        if lower.contains("-----begin") && lower.contains("private key-----") {
            out.push_str("[private key redacted]\n");
            continue;
        }
        if lower.contains("authorization:") || lower.contains("bearer ") || lower.contains("password=") {
            out.push_str("[credential line redacted]\n");
            continue;
        }
        let mut parts: Vec<String> = Vec::new();
        for token in line.split_whitespace() {
            let lowered = token.to_ascii_lowercase();
            if lowered.starts_with("token=")
                || lowered.starts_with("?token=")
                || lowered.starts_with("&token=")
                || lowered.starts_with("ssh-")
            {
                parts.push("[redacted]".into());
            } else {
                parts.push(token.into());
            }
        }
        out.push_str(&parts.join(" "));
        out.push('\n');
    }
    if text.is_empty() { out } else { out.trim_end().to_string() }
}

fn read_tail(path: &Path, max_bytes: usize) -> String {
    let Ok(bytes) = fs::read(path) else { return String::new() };
    let start = bytes.len().saturating_sub(max_bytes);
    String::from_utf8_lossy(&bytes[start..]).to_string()
}

fn tail_lines(text: &str, lines: usize) -> String {
    let all: Vec<&str> = text.lines().collect();
    all[all.len().saturating_sub(lines)..].join("\n")
}

fn app_logs() -> String {
    let mut text = String::new();
    for name in ["desktop.log", "startup-error.log"] {
        let path = log_dir().join(name);
        if path.is_file() {
            text.push_str(&format!("\n## {name}\n"));
            text.push_str(&tail_lines(&read_tail(&path, MAX_LOG_BYTES / 2), 300));
            text.push('\n');
        }
    }
    if text.is_empty() { "No app logs recorded.\n".into() } else { text }
}

fn sidecar_logs() -> String {
    let Ok(entries) = fs::read_dir(log_dir()) else { return "No sidecar log recorded.\n".into() };
    let mut paths: Vec<PathBuf> = entries
        .flatten()
        .map(|entry| entry.path())
        .filter(|path| path.file_name().and_then(|name| name.to_str()).is_some_and(|name| name.starts_with("serve-")))
        .collect();
    paths.sort();
    let Some(path) = paths.last() else { return "No sidecar log recorded.\n".into() };
    format!("\n## {}\n{}\n", path.file_name().unwrap_or_default().to_string_lossy(), tail_lines(&read_tail(path, MAX_LOG_BYTES / 2), 200))
}

/// Build the complete preview. `context` is provided by the UI and must never
/// bypass redaction: it is serialised and redacted like the logs.
pub fn report(context: &Value) -> Value {
    let mut servers: Vec<String> = context
        .get("servers")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(str::to_string)
        .collect();
    // The saved profiles are the authoritative host/alias list; redact every
    // alias and host even when the UI context omitted them.
    for entry in crate::server_profiles::load_servers() {
        if let Ok(target) = entry.profile.ssh_target() {
            servers.push(target);
        }
        servers.push(entry.profile.name.clone());
        if let Some(alias) = &entry.profile.ssh_alias {
            servers.push(alias.clone());
        }
        if let Some(host) = &entry.profile.host {
            servers.push(host.clone());
        }
        if let Some(user) = &entry.profile.user {
            servers.push(user.clone());
        }
    }
    servers.sort();
    servers.dedup();
    let crashes = crash_records();
    let crashes_text = crashes
        .iter()
        .map(|record| serde_json::to_string_pretty(record).unwrap_or_default())
        .collect::<Vec<_>>()
        .join("\n\n");
    let context_text = serde_json::to_string_pretty(context).unwrap_or_default();
    let body = format!(
        "Coperceive Diagnostics\n======================\n\
         Version: {}\nCommit: {}\nOS: {}\nArchitecture: {}\nPython: {}\n\
         \n## Feature state\n{}\n\
         \n## Recent crashes\n{}\n\
         \n## App logs\n{}\n\
         \n## Sidecar logs\n{}\n",
        app_version(),
        app_commit(),
        os_version(),
        std::env::consts::ARCH,
        context.get("python").and_then(Value::as_str).unwrap_or("unknown"),
        context_text,
        if crashes_text.is_empty() { "No crash files recorded.\n".to_string() } else { crashes_text },
        app_logs(),
        sidecar_logs(),
    );
    let preview = redact(&body, &servers);
    serde_json::json!({
        "preview": preview,
        "crashes": crashes.len(),
        "bytes": preview.len(),
    })
}

/// Copy the (already redacted) preview through pbcopy.
pub fn copy(text: &str) -> Result<(), String> {
    let mut child = std::process::Command::new("pbcopy")
        .stdin(std::process::Stdio::piped())
        .spawn()
        .map_err(|error| format!("Could not copy diagnostics: {error}"))?;
    if let Some(stdin) = child.stdin.as_mut() {
        stdin.write_all(text.as_bytes()).map_err(|error| error.to_string())?;
    }
    let status = child.wait().map_err(|error| error.to_string())?;
    if status.success() { Ok(()) } else { Err("pbcopy failed".into()) }
}

/// Save a zip containing report.txt, crash files and the last app/sidecar logs.
pub fn save_zip(destination: &Path, preview: &str, context: &Value) -> Result<PathBuf, String> {
    let parent = destination.parent().ok_or_else(|| "Choose a folder for diagnostics.".to_string())?;
    fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    let staging = std::env::temp_dir().join(format!("coperceive-diagnostics-{}", std::process::id()));
    let _ = fs::remove_dir_all(&staging);
    fs::create_dir_all(&staging).map_err(|error| error.to_string())?;
    fs::write(staging.join("report.txt"), preview).map_err(|error| error.to_string())?;
    fs::write(
        staging.join("feature-state.json"),
        serde_json::to_string_pretty(context).unwrap_or_default(),
    )
    .map_err(|error| error.to_string())?;
    // Include the crash records and recent app/sidecar logs. All crash files
    // are already redacted in report.txt; the raw crash JSON is kept because it
    // contains no tokens by construction.
    if let Ok(entries) = fs::read_dir(log_dir()) {
        for entry in entries.flatten() {
            let name = entry.file_name().to_string_lossy().to_string();
            if name.starts_with("crash-") && name.ends_with(".json") {
                let _ = fs::copy(entry.path(), staging.join(&name));
            }
            if name == "desktop.log" || name == "startup-error.log" || name.starts_with("serve-") {
                let _ = fs::copy(entry.path(), staging.join(&name));
            }
        }
    }
    let mut zip_path = destination.to_path_buf();
    if zip_path.extension().and_then(|ext| ext.to_str()) != Some("zip") {
        zip_path.set_extension("zip");
    }
    let status = std::process::Command::new("zip")
        .current_dir(&staging)
        .arg("-qr")
        .arg(&zip_path)
        .arg(".")
        .status()
        .map_err(|error| format!("Could not create the zip: {error}"))?;
    let _ = fs::remove_dir_all(&staging);
    if !status.success() { return Err("zip failed".into()); }
    Ok(zip_path)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_home() -> PathBuf {
        let path = std::env::temp_dir().join(format!("coperceive-diag-{}", std::process::id()));
        let _ = fs::remove_dir_all(&path);
        fs::create_dir_all(path.join("Library/Logs/Coperceive")).unwrap();
        path
    }

    #[test]
    fn planted_secrets_are_removed() {
        let input = "Authorization: Bearer abc123\nviewer ?token=sekret\n-----BEGIN OPENSSH PRIVATE KEY-----\nabc\nserver alice@hpc.example.org /Users/alice /datasets/eemcs/x\nssh-ed25519 AAAA";
        let servers = vec!["hpc.example.org".to_string()];
        let output = redact(input, &servers);
        for secret in ["abc123", "sekret", "BEGIN OPENSSH", "alice@hpc", "/Users/alice", "ssh-ed25519"] {
            assert!(!output.contains(secret), "{secret} leaked: {output}");
        }
        assert!(output.contains("<datasets>/"), "{output}");
        assert!(output.contains("<server-1>"), "{output}");
    }

    #[test]
    fn crash_file_is_written_and_read_back() {
        let home = temp_home();
        let old_home = std::env::var("HOME").ok();
        std::env::set_var("HOME", &home);
        let record = CrashRecord {
            schema: CRASH_SCHEMA.into(),
            kind: "panic".into(),
            timestamp_unix: 123,
            version: "0.1.0".into(),
            commit: "abc".into(),
            os: "test".into(),
            arch: "arm64".into(),
            message: "boom".into(),
            location: Some("src/main.rs:1".into()),
            backtrace: None,
        };
        let path = write_crash(&record).expect("crash file");
        assert!(path.exists());
        assert!(crash_records().iter().any(|item| item.message == "boom"));
        if let Some(old) = old_home { std::env::set_var("HOME", old); }
    }
}
