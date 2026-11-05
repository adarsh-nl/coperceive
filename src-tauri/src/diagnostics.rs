// SPDX-License-Identifier: Apache-2.0
//! Crash capture and user-initiated diagnostics.
//!
//! Nothing here uploads anything. A crash is written locally; the report is
//! only built when the user opens Share Diagnostics, and only the user's chosen
//! action (copy, save, GitHub) moves it off the Mac.

use std::fs;
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

pub fn resolve_log_dir(env_active: bool, test_log_env: Option<&str>) -> PathBuf {
    if env_active {
        if let Some(path) = test_log_env {
            if !path.trim().is_empty() {
                return PathBuf::from(path);
            }
        }
    }
    crate::platform::get_log_dir()
}

pub fn log_dir() -> PathBuf {
    resolve_log_dir(
        crate::test_seams::test_env_active(),
        std::env::var("COPERCEIVE_TEST_LOG_DIR").ok().as_deref(),
    )
}

pub fn resolve_test_save_path(env_active: bool, save_path_env: Option<&str>) -> Option<PathBuf> {
    if !env_active {
        return None;
    }
    save_path_env
        .filter(|path| !path.trim().is_empty())
        .map(PathBuf::from)
}

/// Test-only destination for the save command; `None` means show the native
/// dialog. Inert unless the app was launched by the e2e harness.
pub fn test_save_path() -> Option<PathBuf> {
    resolve_test_save_path(
        crate::test_seams::test_env_active(),
        std::env::var("COPERCEIVE_TEST_DIAGNOSTICS_SAVE_PATH").ok().as_deref(),
    )
}

fn marker_path_in(dir: &Path) -> PathBuf {
    dir.join("run-marker.json")
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

fn crash_path_in(dir: &Path, timestamp: u64) -> PathBuf {
    dir.join(format!("crash-{timestamp}.json"))
}

pub fn write_crash_in(dir: &Path, record: &CrashRecord) -> Option<PathBuf> {
    fs::create_dir_all(dir).ok()?;
    let path = crash_path_in(dir, record.timestamp_unix);
    let text = serde_json::to_string_pretty(record).ok()?;
    fs::write(&path, format!("{text}\n")).ok()?;
    Some(path)
}

pub fn write_crash(record: &CrashRecord) -> Option<PathBuf> {
    write_crash_in(&log_dir(), record)
}

/// Write the "running" marker into `dir`. Returns a crash record for a previous
/// run whose marker was never removed (force quit, kill, or a missed panic).
pub fn start_run_in(dir: &Path) -> Option<CrashRecord> {
    let _ = fs::create_dir_all(dir);
    let previous = fs::read_to_string(marker_path_in(dir)).ok().and_then(|text| {
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
        let _ = write_crash_in(dir, &clean);
        clean
    });
    let marker = serde_json::json!({
        "schema": "coperceive-run-marker-1",
        "started_unix": now_unix(),
        "version": app_version(),
        "commit": app_commit(),
        "os": os_version(),
    });
    let _ = fs::write(marker_path_in(dir), format!("{marker}\n"));
    record
}

/// Write the "running" marker. Returns a crash record for a previous run whose
/// marker was never removed (force quit, kill, or a missed panic).
pub fn start_run() -> Option<CrashRecord> {
    start_run_in(&log_dir())
}

/// Remove the marker only on a clean quit.
pub fn clean_exit_in(dir: &Path) {
    let _ = fs::remove_file(marker_path_in(dir));
}

/// Remove the marker only on a clean quit.
pub fn clean_exit() {
    clean_exit_in(&log_dir());
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

/// Record a local server that exited without the app asking it to. The stderr
/// tail is redacted before it is written, like every other shared artifact.
pub fn record_sidecar_exit_in(dir: &Path, exit_code: i32, log_path: &Path) {
    let tail = tail_lines(&read_tail(log_path, MAX_LOG_BYTES / 2), 200);
    let servers = servers_from(&Value::Null);
    let record = CrashRecord {
        schema: CRASH_SCHEMA.into(),
        kind: "sidecar-exit".into(),
        timestamp_unix: now_unix(),
        version: app_version().to_string(),
        commit: app_commit().to_string(),
        os: os_version(),
        arch: std::env::consts::ARCH.to_string(),
        message: format!("The local Coperceive server exited unexpectedly (exit code {exit_code})."),
        location: Some(log_path.display().to_string()),
        backtrace: Some(redact(&tail, &servers)),
    };
    let _ = write_crash_in(dir, &record);
}

pub fn record_sidecar_exit(exit_code: i32, log_path: &Path) {
    record_sidecar_exit_in(&log_dir(), exit_code, log_path);
}

/// Recent crash records from `dir`, newest first.
pub fn crash_records_in(dir: &Path) -> Vec<CrashRecord> {
    let mut records: Vec<CrashRecord> = fs::read_dir(dir)
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

/// Recent crash records, newest first.
pub fn crash_records() -> Vec<CrashRecord> {
    crash_records_in(&log_dir())
}

struct Bundle {
    preview: String,
    files: Vec<(String, String)>,
}

fn server_pattern(server: &str) -> regex::Regex {
    regex::Regex::new(&format!(
        r"(?i)(^|[^A-Za-z0-9._-])({})([^A-Za-z0-9._-]|$)",
        regex::escape(server)
    ))
    .expect("server regex")
}

fn redact(text: &str, servers: &[String]) -> String {
    use regex::Regex;
    let mut out = text.to_string();
    let full_key = Regex::new(r"(?is)-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----")
        .expect("private key regex");
    out = full_key.replace_all(&out, "[private key redacted]").into_owned();
    // A tail may start or end inside a key block. Without this, the surviving
    // base64 body would leak.
    let begin_key = Regex::new(r"(?is)-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*$")
        .expect("begin key regex");
    out = begin_key.replace_all(&out, "[private key redacted]").into_owned();
    let end_key = Regex::new(r"(?is)^.*-----END [A-Z0-9 ]*PRIVATE KEY-----")
        .expect("end key regex");
    out = end_key.replace_all(&out, "[private key redacted]").into_owned();
    let quoted_header = Regex::new(
        r#"(?i)(["']?(?:authorization|x-coperceive-token|set-cookie|cookie)["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;}]+)"#,
    )
    .expect("quoted header regex");
    out = quoted_header.replace_all(&out, "$1[redacted]").into_owned();
    let inline_header = Regex::new(r"(?i)\b(?:authorization|x-coperceive-token|set-cookie|cookie)\b\s*:\s*[^\n,}]*")
        .expect("inline header regex");
    out = inline_header.replace_all(&out, "[credential header redacted]").into_owned();
    let bearer = Regex::new(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+").expect("bearer regex");
    out = bearer.replace_all(&out, "Bearer [redacted]").into_owned();
    // Quoted values run to their closing quote, so spaces inside a secret are
    // removed too; unquoted values stop at the usual delimiters.
    let quoted_value = Regex::new(
        r#"(?i)(["']?(?:token|password|passwd|secret|api[_-]?key)["']?\s*[:=]\s*)("[^"]*"|'[^']*')"#,
    )
    .expect("quoted value regex");
    out = quoted_value.replace_all(&out, "$1[redacted]").into_owned();
    let assignment = Regex::new(
        r#"(?i)(["']?(?:token|password|passwd|secret|api[_-]?key)["']?\s*[:=]\s*)(["']?)[^"'\s,;&}]+"#,
    )
    .expect("assignment regex");
    out = assignment.replace_all(&out, "$1[redacted]").into_owned();
    let ssh_host = Regex::new(r"(?i)\bssh\s+(?:-[^\s]+\s+)*[A-Za-z0-9._-]+").expect("ssh host regex");
    out = ssh_host.replace_all(&out, "ssh <server>").into_owned();
    let ssh_key = Regex::new(r"(?i)\bssh-(?:rsa|ed25519|dss|ecdsa)\s+[A-Za-z0-9+/=]+")
        .expect("ssh key regex");
    out = ssh_key.replace_all(&out, "[ssh key redacted]").into_owned();
    let home_path = Regex::new(r#"(?m)(?:/Users|/home)/[^/\s"']+"#).expect("home path regex");
    out = home_path.replace_all(&out, "~").into_owned();
    if let Ok(home) = std::env::var("HOME") {
        if let Some(user) = Path::new(&home).file_name().and_then(|name| name.to_str()) {
            if !user.is_empty() {
                let user_regex = Regex::new(&format!(r"\b{}\b", regex::escape(user))).expect("user regex");
                out = user_regex.replace_all(&out, "<user>").into_owned();
            }
        }
    }
    for (index, server) in servers.iter().enumerate() {
        if !server.trim().is_empty() {
            out = server_pattern(server)
                .replace_all(&out, format!("${{1}}<server-{}>${{3}}", index + 1))
                .into_owned();
        }
    }
    out.replace("/datasets/", "<datasets>/")
}

fn servers_from(context: &Value) -> Vec<String> {
    let mut servers: Vec<String> = context
        .get("servers")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(str::to_string)
        .collect();
    for entry in crate::server_profiles::load_servers() {
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
    servers
}

/// One redacted bundle: the preview and every file put in the zip are built
/// from this same data, so the user sees exactly what is saved.
fn build_bundle_in(dir: &Path, context: &Value) -> Bundle {
    let servers = servers_from(context);
    let mut files: Vec<(String, String)> = Vec::new();
    files.push((
        "feature-state.json".into(),
        redact(&serde_json::to_string_pretty(context).unwrap_or_default(), &servers),
    ));
    for record in crash_records_in(dir) {
        let name = format!("crash-{}.json", record.timestamp_unix);
        files.push((
            name,
            redact(&serde_json::to_string_pretty(&record).unwrap_or_default(), &servers),
        ));
    }
    for name in ["desktop.log", "startup-error.log"] {
        let path = dir.join(name);
        if path.is_file() {
            files.push((
                name.into(),
                redact(&tail_lines(&read_tail(&path, MAX_LOG_BYTES / 2), 300), &servers),
            ));
        }
    }
    if let Ok(entries) = fs::read_dir(dir) {
        let mut serve: Vec<PathBuf> = entries
            .flatten()
            .map(|entry| entry.path())
            .filter(|path| path.file_name().and_then(|name| name.to_str()).is_some_and(|name| name.starts_with("serve-")))
            .collect();
        serve.sort();
        if let Some(path) = serve.last() {
            files.push((
                path.file_name().unwrap_or_default().to_string_lossy().to_string(),
                redact(&tail_lines(&read_tail(path, MAX_LOG_BYTES / 2), 200), &servers),
            ));
        }
    }
    let header = format!(
        "Coperceive Diagnostics\n======================\nVersion: {}\nCommit: {}\nOS: {}\nArchitecture: {}\nPython: {}\n",
        app_version(),
        app_commit(),
        os_version(),
        std::env::consts::ARCH,
        context.get("python").and_then(Value::as_str).unwrap_or("unknown"),
    );
    let mut preview = header;
    for (name, text) in &files {
        preview.push_str(&format!("\n## {name}\n{text}\n"));
    }
    Bundle { preview, files }
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

pub fn report_in(dir: &Path, context: &Value) -> Value {
    let bundle = build_bundle_in(dir, context);
    serde_json::json!({
        "preview": bundle.preview,
        "crashes": crash_records_in(dir).len(),
        "bytes": bundle.preview.len(),
    })
}

/// Build the complete preview. Everything shared is generated from one
/// redacted bundle.
pub fn report(context: &Value) -> Value {
    report_in(&log_dir(), context)
}

pub fn copy_text_in(dir: &Path, context: &Value, note: &str) -> String {
    let bundle = build_bundle_in(dir, context);
    let servers = servers_from(context);
    let note = note.trim();
    if note.is_empty() {
        bundle.preview
    } else {
        format!("{}\n\n## What I was doing\n{}\n", bundle.preview, redact(note, &servers))
    }
}

/// The exact text that will be copied: the redacted preview plus the redacted
/// user note. Built in Rust so the webview cannot substitute unredacted text.
pub fn copy_text(context: &Value, note: &str) -> String {
    copy_text_in(&log_dir(), context, note)
}

/// Copy that Rust-built text through platform clipboard.
pub fn copy(context: &Value, note: &str) -> Result<(), String> {
    let text = copy_text(context, note);
    if crate::platform::copy_to_clipboard(&text) {
        Ok(())
    } else {
        Err("Could not copy diagnostics: clipboard command failed".into())
    }
}

pub fn save_zip_in(dir: &Path, destination: &Path, context: &Value, note: &str) -> Result<PathBuf, String> {
    use std::io::Write;
    use zip::write::SimpleFileOptions;
    use zip::CompressionMethod;

    let bundle = build_bundle_in(dir, context);
    let report_text = copy_text_in(dir, context, note);
    let parent = destination.parent().ok_or_else(|| "Choose a folder for diagnostics.".to_string())?;
    fs::create_dir_all(parent).map_err(|error| error.to_string())?;

    let mut zip_path = destination.to_path_buf();
    if zip_path.extension().and_then(|ext| ext.to_str()) != Some("zip") {
        zip_path.set_extension("zip");
    }

    let file = fs::File::create(&zip_path).map_err(|error| format!("Could not create zip: {error}"))?;
    let mut zip = zip::ZipWriter::new(file);
    let options = SimpleFileOptions::default().compression_method(CompressionMethod::Deflated);

    zip.start_file("report.txt", options).map_err(|error| error.to_string())?;
    zip.write_all(report_text.as_bytes()).map_err(|error| error.to_string())?;

    for (name, text) in &bundle.files {
        zip.start_file(name, options).map_err(|error| error.to_string())?;
        zip.write_all(text.as_bytes()).map_err(|error| error.to_string())?;
    }

    zip.finish().map_err(|error| format!("Could not finish zip: {error}"))?;
    Ok(zip_path)
}

/// Save the exact redacted bundle as a zip: report.txt plus one file per
/// preview section. Nothing unredacted is copied. Pure Rust zip implementation.
pub fn save_zip(destination: &Path, context: &Value, note: &str) -> Result<PathBuf, String> {
    save_zip_in(&log_dir(), destination, context, note)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_home() -> PathBuf {
        use std::sync::atomic::{AtomicU64, Ordering};
        static COUNTER: AtomicU64 = AtomicU64::new(1);
        let id = COUNTER.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!("coperceive-diag-{}-{}", std::process::id(), id));
        let _ = fs::remove_dir_all(&path);
        let _ = fs::create_dir_all(&path);
        path
    }

    #[test]
    fn planted_secrets_are_removed() {
        let input = "Authorization: Bearer abc123\nviewer ?token=sekret\n-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----\nserver alice@hpc.example.org /Users/alice /datasets/eemcs/x\nssh-ed25519 AAAA";
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
        let path = write_crash_in(&home, &record).expect("crash file");
        assert!(path.exists());
        assert!(crash_records_in(&home).iter().any(|item| item.message == "boom"));
        let _ = fs::remove_dir_all(&home);
    }

    #[test]
    fn every_shared_file_is_redacted_and_the_zip_matches_the_preview() {
        use std::io::Read;

        let home = temp_home();
        let logs = &home;
        let key = "-----BEGIN OPENSSH PRIVATE KEY-----\nbody-secret-base64\n-----END OPENSSH PRIVATE KEY-----";
        let log = format!(
            "viewer http://127.0.0.1:5000/viewer?token=url-secret\n\
             {{\"token\": \"json-secret\", \"password\": \"json-password\"}}\n\
             Authorization: Bearer auth-secret\n\
             Cookie: session=cookie-secret\n\
             {key}\n\
             path /Users/alice/project and user alice\n\
             alias hpc-alias host hpc.example.org\n\
             normal text: shadow bad road\n"
        );
        fs::write(logs.join("desktop.log"), &log).unwrap();
        fs::write(logs.join("serve-123.log"), "sidecar token=sidecar-secret\n").unwrap();
        let crash = CrashRecord {
            schema: CRASH_SCHEMA.into(),
            kind: "panic".into(),
            timestamp_unix: 999,
            version: "0.1.0".into(),
            commit: "abc".into(),
            os: "test".into(),
            arch: "arm64".into(),
            message: "boom at /Users/alice/project".into(),
            location: Some("src/main.rs:1".into()),
            backtrace: None,
        };
        let _ = write_crash_in(&home, &crash);
        let context = serde_json::json!({"servers": ["hpc-alias", "hpc.example.org"]});
        let report = report_in(&home, &context);
        let preview = report["preview"].as_str().unwrap().to_string();
        for secret in [
            "url-secret", "json-secret", "json-password", "auth-secret", "cookie-secret",
            "body-secret-base64", "BEGIN OPENSSH", "/Users/alice", "hpc-alias", "hpc.example.org",
            "sidecar-secret",
        ] {
            assert!(!preview.contains(secret), "{secret} leaked into preview:\n{preview}");
        }
        assert!(preview.contains("shadow bad road"), "normal text was mangled");
        let zip = save_zip_in(&home, &home.join("report.zip"), &context, "").unwrap();
        let archive_file = std::fs::File::open(&zip).unwrap();
        let mut archive = zip::ZipArchive::new(archive_file).unwrap();
        assert!(!archive.is_empty());
        let mut all = String::new();
        let mut names = String::new();
        for i in 0..archive.len() {
            let mut file = archive.by_index(i).unwrap();
            names.push_str(&file.name().unwrap());
            names.push('\n');
            let mut content = String::new();
            file.read_to_string(&mut content).unwrap();
            all.push_str(&content);
            all.push('\n');
        }
        for secret in [
            "url-secret", "json-secret", "json-password", "auth-secret", "cookie-secret",
            "body-secret-base64", "BEGIN OPENSSH", "/Users/alice", "hpc-alias", "hpc.example.org",
            "sidecar-secret",
        ] {
            assert!(!all.contains(secret), "{secret} leaked into the zip:\n{all}");
        }
        assert!(names.contains("report.txt"), "{names}");
        assert!(names.contains("crash-"), "{names}");
        assert!(names.contains("desktop.log"), "{names}");
        let _ = fs::remove_dir_all(&home);
    }

    #[test]
    fn sidecar_exit_record_keeps_code_and_redacts_stderr() {
        let home = temp_home();
        let log = home.join("serve-9.log");
        fs::write(&log, "Authorization: Bearer sidecar-auth\nuri /Users/alice/x\n").unwrap();
        record_sidecar_exit_in(&home, 42, &log);
        let record = crash_records_in(&home).into_iter().find(|item| item.kind == "sidecar-exit").expect("sidecar crash");
        assert!(record.message.contains("42"));
        let backtrace = record.backtrace.unwrap_or_default();
        assert!(!backtrace.contains("sidecar-auth"), "{backtrace}");
        assert!(!backtrace.contains("/Users/alice"), "{backtrace}");
        let _ = fs::remove_dir_all(&home);
    }

    #[test]
    fn truncated_keys_quoted_values_short_servers_and_inline_headers_are_redacted() {
        let begin = "prefix\n-----BEGIN OPENSSH PRIVATE KEY-----\ncut-body-secret";
        let end = "cut-body-other\n-----END OPENSSH PRIVATE KEY-----\nsuffix";
        for text in [begin, end] {
            let out = redact(text, &[]);
            assert!(!out.contains("cut-body"), "{out}");
        }
        let quoted = redact(r#"{"password": "my secret pass", "token": "a b c"}"#, &[]);
        assert!(!quoted.contains("secret pass"), "{quoted}");
        assert!(!quoted.contains("a b c"), "{quoted}");
        let short = redact("maps ps output and ps", &["ps".to_string()]);
        assert!(!short.contains(" ps "), "{short}");
        assert!(short.contains("maps"), "{short}");
        let headers = redact(
            r#"request headers: Cookie: a=b, then {"Authorization": "Bearer hidden"}"#,
            &[],
        );
        assert!(!headers.contains("a=b"), "{headers}");
        assert!(!headers.contains("hidden"), "{headers}");
    }

    #[test]
    fn copied_text_is_the_preview_plus_a_redacted_note() {
        let home = temp_home();
        let context = serde_json::json!({"servers": ["hpc"]});
        let text = copy_text_in(&home, &context, "token=note-secret /Users/alice/x password=pass-secret");
        assert!(text.starts_with("Coperceive Diagnostics"));
        assert!(!text.contains("note-secret"), "{text}");
        assert!(!text.contains("pass-secret"), "{text}");
        assert!(!text.contains("/Users/alice"), "{text}");
        let _ = fs::remove_dir_all(&home);
    }

    #[test]
    fn log_and_save_seams_only_apply_in_test_mode() {
        let log = PathBuf::from("/tmp/test-log");
        let save = PathBuf::from("/tmp/test-log/diag.zip");

        // In test mode: seams are honored
        assert_eq!(resolve_log_dir(true, Some("/tmp/test-log")), log);
        assert_eq!(resolve_test_save_path(true, Some("/tmp/test-log/diag.zip")), Some(save.clone()));

        // Outside test mode: seams are ignored, returns default platform log dir and None for save
        assert_ne!(resolve_log_dir(false, Some("/tmp/test-log")), log);
        assert_eq!(resolve_test_save_path(false, Some("/tmp/test-log/diag.zip")), None);
    }

    #[test]
    fn start_run_reads_and_writes_the_overridden_log_dir() {
        // The e2e crash launch and relaunch both use COPERCEIVE_TEST_LOG_DIR:
        // the marker the first run leaves and the record the second reads must
        // live there, and nothing may be written under HOME.
        let home = temp_home();
        let log = home.join("override-logs");

        assert!(start_run_in(&log).is_none(), "no marker yet: no crash");
        assert!(log.join("run-marker.json").is_file());

        // The marker was not removed (no clean_exit): the next start reports it.
        let record = start_run_in(&log).expect("the leftover marker is an unclean exit");
        assert_eq!(record.kind, "unclean-exit");
        assert!(record.message.contains("closed unexpectedly"), "{}", record.message);
        let crashes: Vec<_> = fs::read_dir(&log)
            .unwrap()
            .filter_map(Result::ok)
            .filter(|entry| entry.file_name().to_string_lossy().starts_with("crash-"))
            .collect();
        assert_eq!(crashes.len(), 1);
        assert!(log.join("run-marker.json").is_file(), "a fresh marker for this run");

        clean_exit_in(&log);
        assert!(start_run_in(&log).is_none(), "a clean exit leaves no crash");
        let _ = fs::remove_dir_all(&home);
    }
}
