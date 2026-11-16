// SPDX-License-Identifier: Apache-2.0
//! Desktop Loopback HTTP Controller
//!
//! Provides a secure, authenticated loopback IPC bridge for the viewer webview:
//! - Per-launch random bearer token authentication (never exposed in URL).
//! - Strict CORS restricted exclusively to the active sidecar origin (`http://127.0.0.1:<port>`).
//! - Host header verification (anti-DNS-rebinding: must match `127.0.0.1:<ctrl_port>`).
//! - Enforced Content-Type `application/json` and exact Content-Length body streaming (capped at 64 KB).
//! - Rate limiting on `/open-dialog`.

use std::collections::HashMap;
use std::io::{Read, Write};
use std::net::TcpListener;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use crate::state::{self};

/// Maximum allowed request body size (64 KB).
pub const MAX_BODY_SIZE: usize = 65536;

/// Rate limit window for `/open-dialog` (1 second).
pub const OPEN_DIALOG_RATE_LIMIT: Duration = Duration::from_millis(1000);

pub type EvalSender = std::sync::mpsc::Sender<serde_json::Value>;
pub type ExportPicker = Arc<dyn Fn(&str, &str) -> Result<Option<String>, String> + Send + Sync>;
pub type ExportSaver = Arc<dyn Fn(Option<&str>, Option<&[u8]>) -> Result<String, String> + Send + Sync>;
pub type FinderRevealer = Arc<dyn Fn() -> Result<(), String> + Send + Sync>;
pub type UpdaterChecker = Arc<dyn Fn(bool, Option<String>) -> Result<serde_json::Value, String> + Send + Sync>;
pub type UpdaterInstaller = Arc<dyn Fn(Option<String>) -> Result<(), String> + Send + Sync>;
pub type EvalDispatcher = Arc<dyn Fn(String, Option<String>) -> Result<(), String> + Send + Sync>;

/// Configuration for the loopback controller.
#[derive(Clone)]
pub struct ControllerConfig {
    pub ctrl_port: u16,
    pub auth_secret: String,
    pub allowed_origin: Arc<Mutex<Option<String>>>,
    pub last_open_dialog: Arc<Mutex<Option<Instant>>>,
    pub eval_callbacks: Arc<Mutex<HashMap<u64, EvalSender>>>,
    pub eval_counter: Arc<Mutex<u64>>,
    /// Runs a script in a window: the viewer when there is one, else main; or
    /// the window the request names (`"window": "main"`).
    pub eval_dispatcher: Option<EvalDispatcher>,
    pub window_closer: Option<Arc<dyn Fn(&str) + Send + Sync>>,
    /// Shows and focuses the main window on its Library; returns whether it is visible.
    pub library_shower: Option<Arc<dyn Fn() -> bool + Send + Sync>>,
    pub export_picker: Option<ExportPicker>,
    pub export_saver: Option<ExportSaver>,
    pub finder_revealer: Option<FinderRevealer>,
    pub updater_checker: Option<UpdaterChecker>,
    pub updater_installer: Option<UpdaterInstaller>,
    /// The remote viewer job (id, state, time left), or null without one.
    pub viewer_session: Option<Arc<dyn Fn() -> serde_json::Value + Send + Sync>>,
    /// Every scene of the open cluster dataset with its state, as the main
    /// window last saw it, so the viewer can list scenes that are not ready.
    pub dataset_scenes: Arc<Mutex<serde_json::Value>>,
    /// Asks the main window to show the prepare preview for one scene.
    pub scene_preparer: Option<Arc<dyn Fn(String) -> bool + Send + Sync>>,
    /// Names the viewer window after the scene it shows.
    pub viewer_titler: Option<Arc<dyn Fn(String) + Send + Sync>>,
    /// Custom state file path override (useful for testing state persistence in isolation).
    pub state_path: Option<PathBuf>,
}

impl ControllerConfig {
    pub fn new(ctrl_port: u16, auth_secret: String, initial_origin: Option<String>) -> Self {
        Self {
            ctrl_port,
            auth_secret,
            allowed_origin: Arc::new(Mutex::new(initial_origin)),
            last_open_dialog: Arc::new(Mutex::new(None)),
            eval_callbacks: Arc::new(Mutex::new(HashMap::new())),
            eval_counter: Arc::new(Mutex::new(0)),
            eval_dispatcher: None,
            window_closer: None,
            library_shower: None,
            export_picker: None,
            export_saver: None,
            finder_revealer: None,
            updater_checker: None,
            updater_installer: None,
            viewer_session: None,
            dataset_scenes: Arc::new(Mutex::new(serde_json::Value::Null)),
            scene_preparer: None,
            viewer_titler: None,
            state_path: None,
        }
    }

    pub fn set_allowed_origin(&self, origin: String) {
        if let Ok(mut lock) = self.allowed_origin.lock() {
            *lock = Some(origin);
        }
    }
}

fn read_request_body<R: Read, W: Write>(
    reader: &mut R,
    writer: &mut W,
    headers: &HashMap<String, String>,
    header_buf: &[u8],
    header_pos: usize,
    cors_origin: Option<&str>,
    max_size: usize,
) -> Result<Option<Vec<u8>>, std::io::Error> {
    let content_type = headers.get("content-type").cloned().unwrap_or_default();
    if !content_type.starts_with("application/json") {
        write_response(
            writer,
            415,
            "Unsupported Media Type",
            cors_origin,
            b"Content-Type must be application/json",
        )?;
        return Ok(None);
    }

    let content_len: usize = match headers.get("content-length").and_then(|v| v.parse().ok()) {
        Some(l) => l,
        None => {
            write_response(
                writer,
                411,
                "Length Required",
                cors_origin,
                b"Content-Length header required",
            )?;
            return Ok(None);
        }
    };

    if content_len > max_size {
        write_response(
            writer,
            413,
            "Payload Too Large",
            cors_origin,
            b"Payload exceeds allowed size limit",
        )?;
        return Ok(None);
    }

    let body_start = header_pos + 4;
    let mut body = Vec::with_capacity(content_len);
    if header_buf.len() > body_start {
        let available = &header_buf[body_start..];
        let to_take = available.len().min(content_len);
        body.extend_from_slice(&available[..to_take]);
    }

    while body.len() < content_len {
        let remaining = content_len - body.len();
        let mut chunk = vec![0u8; remaining.min(4096)];
        let n = reader.read(&mut chunk)?;
        if n == 0 {
            break;
        }
        body.extend_from_slice(&chunk[..n]);
    }

    if body.len() != content_len {
        write_response(
            writer,
            400,
            "Bad Request",
            cors_origin,
            b"Incomplete request body",
        )?;
        return Ok(None);
    }

    Ok(Some(body))
}

/// Clamps a client-provided eval timeout (in seconds) to the allowed range [1, 60].
/// Defaults to 10 seconds if omitted or invalid.
pub fn parse_eval_timeout(val: &serde_json::Value) -> u64 {
    val.get("timeout")
        .and_then(|v| v.as_u64())
        .unwrap_or(10)
        .clamp(1, 60)
}

/// Dispatches an incoming HTTP connection over any read/write stream.
pub fn handle_client<R: Read, W: Write, F: Fn() + Send + 'static>(
    reader: &mut R,
    writer: &mut W,
    config: &ControllerConfig,
    on_open_dialog: &F,
) -> Result<(), std::io::Error> {
    // 1. Read request headers until \r\n\r\n (capped at 8192 bytes)
    let mut header_buf = Vec::with_capacity(2048);
    let mut temp = [0u8; 1024];
    let mut header_end = None;

    while header_buf.len() < 8192 {
        let n = reader.read(&mut temp)?;
        if n == 0 {
            break;
        }
        header_buf.extend_from_slice(&temp[..n]);
        if let Some(pos) = find_subsequence(&header_buf, b"\r\n\r\n") {
            header_end = Some(pos);
            break;
        }
    }

    let header_pos = match header_end {
        Some(pos) => pos,
        None => {
            write_response(writer, 400, "Bad Request", None, b"Malformed HTTP headers")?;
            return Ok(());
        }
    };

    let header_str = String::from_utf8_lossy(&header_buf[..header_pos]);
    let mut lines = header_str.lines();
    let request_line = match lines.next() {
        Some(l) => l,
        None => {
            write_response(writer, 400, "Bad Request", None, b"Empty request line")?;
            return Ok(());
        }
    };

    let parts: Vec<&str> = request_line.split_whitespace().collect();
    if parts.len() < 2 {
        write_response(writer, 400, "Bad Request", None, b"Invalid request line")?;
        return Ok(());
    }
    let method = parts[0];
    let path = parts[1];

    // Parse headers into lowercase map
    let mut headers = HashMap::new();
    for line in lines {
        if let Some((k, v)) = line.split_once(':') {
            headers.insert(k.trim().to_ascii_lowercase(), v.trim().to_string());
        }
    }

    // 2. DNS Rebinding Protection: Host header must match 127.0.0.1:<ctrl_port>
    let expected_host = format!("127.0.0.1:{}", config.ctrl_port);
    let host_header = headers.get("host").cloned().unwrap_or_default();
    if host_header != expected_host {
        write_response(writer, 400, "Bad Request", None, b"Invalid Host header")?;
        return Ok(());
    }

    // 3. CORS Check: Only allow requests originating from allowed_origin or desktop webview
    let allowed_origin = config.allowed_origin.lock().ok().and_then(|l| l.clone());
    let req_origin = headers.get("origin").cloned();
    let cors_origin = match (&req_origin, &allowed_origin) {
        (Some(origin), Some(allowed)) if origin == allowed => Some(origin.clone()),
        (Some(origin), _)
            if origin == "tauri://localhost" || origin == "http://tauri.localhost" =>
        {
            Some(origin.clone())
        }
        _ => None,
    };

    // Handle OPTIONS preflight
    if method == "OPTIONS" {
        if let Some(ref origin) = cors_origin {
            let resp = format!(
                "HTTP/1.1 204 No Content\r\n\
                 Connection: close\r\n\
                 Access-Control-Allow-Origin: {}\r\n\
                 Access-Control-Allow-Methods: GET, POST, OPTIONS\r\n\
                 Access-Control-Allow-Headers: Authorization, Content-Type\r\n\
                 Access-Control-Max-Age: 86400\r\n\
                 Content-Length: 0\r\n\r\n",
                origin
            );
            writer.write_all(resp.as_bytes())?;
            return Ok(());
        } else {
            // Wrong or missing origin: reject with 403 and NO CORS headers
            write_response(writer, 403, "Forbidden", None, b"CORS origin rejected")?;
            return Ok(());
        }
    }

    // 4. Authenticate: Authorization: Bearer <secret>
    let expected_auth = format!("Bearer {}", config.auth_secret);
    let auth_header = headers.get("authorization").cloned().unwrap_or_default();
    if auth_header != expected_auth {
        write_response(
            writer,
            401,
            "Unauthorized",
            cors_origin.as_deref(),
            b"Missing or invalid bearer authorization",
        )?;
        return Ok(());
    }

    // 5. Route handling
    if method == "POST" && path == "/open-dialog" {
        // Rate-limit /open-dialog
        let mut rate_limit_lock = config.last_open_dialog.lock().unwrap();
        let now = Instant::now();
        if let Some(last) = *rate_limit_lock {
            if now.duration_since(last) < OPEN_DIALOG_RATE_LIMIT {
                write_response(
                    writer,
                    429,
                    "Too Many Requests",
                    cors_origin.as_deref(),
                    b"Rate limit exceeded for open-dialog",
                )?;
                return Ok(());
            }
        }
        *rate_limit_lock = Some(now);
        drop(rate_limit_lock);

        on_open_dialog();
        write_json_response(writer, 200, "OK", cors_origin.as_deref(), b"{\"status\":\"ok\"}")?;
        return Ok(());
    }

    if method == "POST" && path == "/close-window" {
        if let Some(ref closer) = config.window_closer {
            closer("viewer");
        }
        write_json_response(writer, 200, "OK", cors_origin.as_deref(), b"{\"closed\":true}")?;
        return Ok(());
    }

    // The viewer window has no Tauri IPC: its wordmark asks for the main
    // window's Library here instead of rendering the shell itself.
    if method == "POST" && path == "/show-library" {
        let started = std::time::Instant::now();
        let shown = config.library_shower.as_ref().map(|show| show()).unwrap_or(false);
        eprintln!(
            "[viewer] show-library -> shown={shown} ({} ms)",
            started.elapsed().as_millis()
        );
        let payload = serde_json::to_vec(&serde_json::json!({ "shown": shown }))
            .unwrap_or_else(|_| b"{}".to_vec());
        write_json_response(writer, 200, "OK", cors_origin.as_deref(), &payload)?;
        return Ok(());
    }

    if method == "GET" && path == "/viewer-session" {
        let session = config
            .viewer_session
            .as_ref()
            .map(|session| session())
            .unwrap_or(serde_json::Value::Null);
        let payload = serde_json::to_vec(&serde_json::json!({ "session": session }))
            .unwrap_or_else(|_| b"{}".to_vec());
        write_json_response(writer, 200, "OK", cors_origin.as_deref(), &payload)?;
        return Ok(());
    }

    if method == "GET" && path == "/dataset-scenes" {
        let scenes = config
            .dataset_scenes
            .lock()
            .map(|scenes| scenes.clone())
            .unwrap_or(serde_json::Value::Null);
        let payload = serde_json::to_vec(&serde_json::json!({ "dataset": scenes }))
            .unwrap_or_else(|_| b"{}".to_vec());
        write_json_response(writer, 200, "OK", cors_origin.as_deref(), &payload)?;
        return Ok(());
    }

    if method == "POST" && path == "/prepare-scene" {
        let body = match read_request_body(reader, writer, &headers, &header_buf, header_pos, cors_origin.as_deref(), MAX_BODY_SIZE)? {
            Some(b) => b,
            None => return Ok(()),
        };
        let scene_id = serde_json::from_slice::<serde_json::Value>(&body)
            .ok()
            .and_then(|value| value.get("scene_id").and_then(|id| id.as_str()).map(str::to_string))
            .filter(|id| !id.is_empty() && id.len() <= 256);
        let Some(scene_id) = scene_id else {
            write_json_response(writer, 400, "Bad Request", cors_origin.as_deref(), b"{\"error\":\"scene_id is required\"}")?;
            return Ok(());
        };
        let shown = config
            .scene_preparer
            .as_ref()
            .map(|prepare| prepare(scene_id))
            .unwrap_or(false);
        let payload = serde_json::to_vec(&serde_json::json!({ "shown": shown })).unwrap_or_else(|_| b"{}".to_vec());
        write_json_response(writer, 200, "OK", cors_origin.as_deref(), &payload)?;
        return Ok(());
    }

    if method == "POST" && path == "/viewer-title" {
        let body = match read_request_body(reader, writer, &headers, &header_buf, header_pos, cors_origin.as_deref(), MAX_BODY_SIZE)? {
            Some(b) => b,
            None => return Ok(()),
        };
        let scene = serde_json::from_slice::<serde_json::Value>(&body)
            .ok()
            .and_then(|value| value.get("scene").and_then(|scene| scene.as_str()).map(str::to_string))
            .map(|scene| scene.chars().filter(|c| !c.is_control()).take(200).collect::<String>())
            .filter(|scene| !scene.trim().is_empty());
        if let (Some(scene), Some(titler)) = (scene, config.viewer_titler.as_ref()) {
            titler(scene);
        }
        write_json_response(writer, 200, "OK", cors_origin.as_deref(), b"{\"status\":\"ok\"}")?;
        return Ok(());
    }

    if method == "POST" && path == "/eval-result" {
        let body = match read_request_body(reader, writer, &headers, &header_buf, header_pos, cors_origin.as_deref(), MAX_BODY_SIZE)? {
            Some(b) => b,
            None => return Ok(()),
        };
        if let Ok(val) = serde_json::from_slice::<serde_json::Value>(&body) {
            if let Some(req_id) = val.get("req_id").and_then(|v| v.as_u64()) {
                if let Some(tx) = config.eval_callbacks.lock().unwrap().remove(&req_id) {
                    let _ = tx.send(val);
                }
            }
        }
        write_json_response(writer, 200, "OK", cors_origin.as_deref(), b"{\"status\":\"ok\"}")?;
        return Ok(());
    }

    if method == "POST" && path == "/eval" {
        let body = match read_request_body(reader, writer, &headers, &header_buf, header_pos, cors_origin.as_deref(), MAX_BODY_SIZE)? {
            Some(b) => b,
            None => return Ok(()),
        };
        let val: serde_json::Value = match serde_json::from_slice(&body) {
            Ok(v) => v,
            Err(_) => {
                write_response(writer, 400, "Bad Request", cors_origin.as_deref(), b"Invalid JSON")?;
                return Ok(());
            }
        };
        let script = val.get("script").and_then(|v| v.as_str()).unwrap_or("");
        let target = val.get("window").and_then(|v| v.as_str()).map(str::to_string);
        let timeout_secs = parse_eval_timeout(&val);
        if let Some(ref dispatcher) = config.eval_dispatcher {
            let req_id = {
                let mut c = config.eval_counter.lock().unwrap();
                *c += 1;
                *c
            };
            let (tx, rx) = std::sync::mpsc::channel();
            config.eval_callbacks.lock().unwrap().insert(req_id, tx);

            let escaped_secret = config.auth_secret.replace('\\', "\\\\").replace('"', "\\\"");
            let script_json = serde_json::to_string(script).unwrap_or_else(|_| "\"\"".into());
            let wrapper = format!(
                r#"(async function() {{
                    let res = null;
                    let err = null;
                    try {{
                        res = await eval({});
                    }} catch(e) {{
                        err = String(e);
                    }}
                    try {{
                        fetch("http://127.0.0.1:{}/eval-result", {{
                            method: "POST",
                            keepalive: true,
                            headers: {{
                                "Authorization": "Bearer {}",
                                "Content-Type": "application/json"
                            }},
                            body: JSON.stringify({{ req_id: {}, result: res, error: err }})
                        }}).catch(function(){{}});
                    }} catch(_) {{}}
                }})();"#,
                script_json, config.ctrl_port, escaped_secret, req_id
            );

            if let Err(dispatch_err) = dispatcher(wrapper, target) {
                config.eval_callbacks.lock().unwrap().remove(&req_id);
                let err_payload = serde_json::to_vec(&serde_json::json!({
                    "status": "error",
                    "error": dispatch_err
                })).unwrap_or_else(|_| b"{}".to_vec());
                write_json_response(writer, 502, "Bad Gateway", cors_origin.as_deref(), &err_payload)?;
                return Ok(());
            }

            match rx.recv_timeout(Duration::from_secs(timeout_secs)) {
                Ok(result_val) => {
                    let payload = serde_json::to_vec(&result_val).unwrap_or_else(|_| b"{}".to_vec());
                    write_json_response(writer, 200, "OK", cors_origin.as_deref(), &payload)?;
                }
                Err(_) => {
                    config.eval_callbacks.lock().unwrap().remove(&req_id);
                    write_json_response(writer, 504, "Gateway Timeout", cors_origin.as_deref(), b"{\"status\":\"error\",\"error\":\"Evaluation timed out\"}")?;
                }
            }
            return Ok(());
        } else {
            write_json_response(writer, 501, "Not Implemented", cors_origin.as_deref(), b"{\"status\":\"error\",\"error\":\"No evaluation dispatcher registered\"}")?;
            return Ok(());
        }
    }

    if method == "POST" && path == "/state" {
        let body = match read_request_body(reader, writer, &headers, &header_buf, header_pos, cors_origin.as_deref(), MAX_BODY_SIZE)? {
            Some(b) => b,
            None => return Ok(()),
        };

        // Parse JSON and apply updates to state.json
        if let Ok(val) = serde_json::from_slice::<serde_json::Value>(&body) {
            let mut persistent = match &config.state_path {
                Some(p) => state::load_state_from_path(p),
                None => state::load_state(),
            };
            if state::apply_state_patch(&mut persistent, &val, state::StatePatchScope::Full).is_ok() {
                match &config.state_path {
                    Some(p) => {
                        let _ = state::save_state_to_path(&persistent, p);
                    }
                    None => {
                        let _ = state::save_state(&persistent);
                    }
                }
            }
        }

        write_json_response(writer, 200, "OK", cors_origin.as_deref(), b"{\"status\":\"ok\"}")?;
        return Ok(());
    }

    if method == "POST" && path == "/export-pick" {
        let body = match read_request_body(reader, writer, &headers, &header_buf, header_pos, cors_origin.as_deref(), MAX_BODY_SIZE)? {
            Some(b) => b,
            None => return Ok(()),
        };
        let val: serde_json::Value = match serde_json::from_slice(&body) {
            Ok(v) => v,
            Err(_) => {
                write_json_response(writer, 400, "Bad Request", cors_origin.as_deref(), b"{\"status\":\"error\",\"error\":\"Invalid JSON\"}")?;
                return Ok(());
            }
        };
        let default_name = val.get("default_name").and_then(|v| v.as_str()).unwrap_or("export");
        let extension = val.get("extension").and_then(|v| v.as_str()).unwrap_or("");
        if let Some(ref picker) = config.export_picker {
            match picker(default_name, extension) {
                Ok(chosen_path) => {
                    let resp = serde_json::to_vec(&serde_json::json!({
                        "status": "ok",
                        "path": chosen_path
                    })).unwrap_or_else(|_| b"{}".to_vec());
                    write_json_response(writer, 200, "OK", cors_origin.as_deref(), &resp)?;
                }
                Err(err) => {
                    let resp = serde_json::to_vec(&serde_json::json!({
                        "status": "error",
                        "error": err
                    })).unwrap_or_else(|_| b"{}".to_vec());
                    write_json_response(writer, 400, "Bad Request", cors_origin.as_deref(), &resp)?;
                }
            }
        } else {
            write_json_response(writer, 501, "Not Implemented", cors_origin.as_deref(), b"{\"status\":\"error\",\"error\":\"No export picker registered\"}")?;
        }
        return Ok(());
    }

    if method == "POST" && path == "/export-save" {
        let body = match read_request_body(reader, writer, &headers, &header_buf, header_pos, cors_origin.as_deref(), 15 * 1024 * 1024)? {
            Some(b) => b,
            None => return Ok(()),
        };
        let val: serde_json::Value = match serde_json::from_slice(&body) {
            Ok(v) => v,
            Err(_) => {
                write_json_response(writer, 400, "Bad Request", cors_origin.as_deref(), b"{\"status\":\"error\",\"error\":\"Invalid JSON\"}")?;
                return Ok(());
            }
        };
        let source_relative = val.get("source_relative").and_then(|v| v.as_str());
        let bytes_vec = if let Some(bytes_val) = val.get("bytes") {
            let arr = match bytes_val.as_array() {
                Some(a) => a,
                None => {
                    write_json_response(
                        writer,
                        400,
                        "Bad Request",
                        cors_origin.as_deref(),
                        b"{\"status\":\"error\",\"error\":\"Field 'bytes' must be an array of integers\"}",
                    )?;
                    return Ok(());
                }
            };
            if arr.len() > crate::export::MAX_EXPORT_BYTES {
                let err_msg = format!(
                    "Byte array exceeds maximum allowed export size of {} bytes (effective JSON body cap is 15 MB, got {} items)",
                    crate::export::MAX_EXPORT_BYTES,
                    arr.len()
                );
                let resp = serde_json::to_vec(&serde_json::json!({
                    "status": "error",
                    "error": err_msg
                })).unwrap_or_else(|_| b"{}".to_vec());
                write_json_response(writer, 400, "Bad Request", cors_origin.as_deref(), &resp)?;
                return Ok(());
            }
            let mut v = Vec::with_capacity(arr.len());
            for item in arr {
                let n = match item.as_u64() {
                    Some(num) if num <= 255 => num as u8,
                    _ => {
                        let resp = serde_json::to_vec(&serde_json::json!({
                            "status": "error",
                            "error": "Invalid byte value in export payload: must be integers between 0 and 255"
                        })).unwrap_or_else(|_| b"{}".to_vec());
                        write_json_response(writer, 400, "Bad Request", cors_origin.as_deref(), &resp)?;
                        return Ok(());
                    }
                };
                v.push(n);
            }
            Some(v)
        } else {
            None
        };
        if let Some(ref saver) = config.export_saver {
            match saver(source_relative, bytes_vec.as_deref()) {
                Ok(dest_path) => {
                    let resp = serde_json::to_vec(&serde_json::json!({
                        "status": "ok",
                        "path": dest_path
                    })).unwrap_or_else(|_| b"{}".to_vec());
                    write_json_response(writer, 200, "OK", cors_origin.as_deref(), &resp)?;
                }
                Err(err) => {
                    let resp = serde_json::to_vec(&serde_json::json!({
                        "status": "error",
                        "error": err
                    })).unwrap_or_else(|_| b"{}".to_vec());
                    write_json_response(writer, 400, "Bad Request", cors_origin.as_deref(), &resp)?;
                }
            }
        } else {
            write_json_response(writer, 501, "Not Implemented", cors_origin.as_deref(), b"{\"status\":\"error\",\"error\":\"No export saver registered\"}")?;
        }
        return Ok(());
    }

    if method == "POST" && path == "/reveal-in-finder" {
        let _ = match read_request_body(reader, writer, &headers, &header_buf, header_pos, cors_origin.as_deref(), MAX_BODY_SIZE)? {
            Some(b) => b,
            None => return Ok(()),
        };
        if let Some(ref revealer) = config.finder_revealer {
            match revealer() {
                Ok(()) => {
                    write_json_response(writer, 200, "OK", cors_origin.as_deref(), b"{\"status\":\"ok\"}")?;
                }
                Err(err) => {
                    let resp = serde_json::to_vec(&serde_json::json!({
                        "status": "error",
                        "error": err
                    })).unwrap_or_else(|_| b"{}".to_vec());
                    write_json_response(writer, 400, "Bad Request", cors_origin.as_deref(), &resp)?;
                }
            }
        } else {
            write_json_response(writer, 501, "Not Implemented", cors_origin.as_deref(), b"{\"status\":\"error\",\"error\":\"No finder revealer registered\"}")?;
        }
        return Ok(());
    }

    if method == "POST" && path == "/updater-check" {
        if let Some(ref checker) = config.updater_checker {
            let body = match read_request_body(reader, writer, &headers, &header_buf, header_pos, cors_origin.as_deref(), MAX_BODY_SIZE)? {
                Some(b) => b,
                None => return Ok(()),
            };
            let val: serde_json::Value = serde_json::from_slice(&body).unwrap_or(serde_json::Value::Null);
            let is_manual = val.get("is_manual").and_then(|v| v.as_bool()).unwrap_or(false);
            let raw_url = val.get("update_url").and_then(|v| v.as_str());
            let override_url = crate::updater::resolve_update_endpoint_override(raw_url);
            match checker(is_manual, override_url) {
                Ok(outcome) => {
                    let bytes = serde_json::to_vec(&outcome).unwrap_or_default();
                    write_json_response(writer, 200, "OK", cors_origin.as_deref(), &bytes)?;
                }
                Err(err) => {
                    let resp = serde_json::to_vec(&serde_json::json!({
                        "status": "error",
                        "error": err
                    })).unwrap_or_else(|_| b"{}".to_vec());
                    write_json_response(writer, 500, "Internal Server Error", cors_origin.as_deref(), &resp)?;
                }
            }
        } else {
            write_json_response(writer, 501, "Not Implemented", cors_origin.as_deref(), b"{\"status\":\"error\",\"error\":\"No updater checker registered\"}")?;
        }
        return Ok(());
    }

    if method == "POST" && path == "/updater-install" {
        if let Some(ref installer) = config.updater_installer {
            let body = match read_request_body(reader, writer, &headers, &header_buf, header_pos, cors_origin.as_deref(), MAX_BODY_SIZE)? {
                Some(b) => b,
                None => return Ok(()),
            };
            let val: serde_json::Value = serde_json::from_slice(&body).unwrap_or(serde_json::Value::Null);
            let raw_url = val.get("update_url").and_then(|v| v.as_str());
            let override_url = crate::updater::resolve_update_endpoint_override(raw_url);
            match installer(override_url) {
                Ok(()) => {
                    write_json_response(writer, 200, "OK", cors_origin.as_deref(), b"{\"status\":\"ok\"}")?;
                }
                Err(err) => {
                    let resp = serde_json::to_vec(&serde_json::json!({
                        "status": "error",
                        "error": err
                    })).unwrap_or_else(|_| b"{}".to_vec());
                    write_json_response(writer, 400, "Bad Request", cors_origin.as_deref(), &resp)?;
                }
            }
        } else {
            write_json_response(writer, 501, "Not Implemented", cors_origin.as_deref(), b"{\"status\":\"error\",\"error\":\"No updater installer registered\"}")?;
        }
        return Ok(());
    }

    if method == "GET" && path == "/state" {
        let persistent = state::load_state();
        let payload = serde_json::to_vec(&persistent).unwrap_or_else(|_| b"{}".to_vec());
        write_json_response(writer, 200, "OK", cors_origin.as_deref(), &payload)?;
        return Ok(());
    }

    write_response(writer, 404, "Not Found", cors_origin.as_deref(), b"Not Found")?;
    Ok(())
}

fn write_response<W: Write>(
    writer: &mut W,
    code: u16,
    reason: &str,
    cors_origin: Option<&str>,
    body: &[u8],
) -> Result<(), std::io::Error> {
    let mut resp = format!("HTTP/1.1 {} {}\r\nConnection: close\r\n", code, reason);
    if let Some(origin) = cors_origin {
        resp.push_str(&format!("Access-Control-Allow-Origin: {}\r\n", origin));
        resp.push_str("Access-Control-Allow-Methods: GET, POST, OPTIONS\r\n");
        resp.push_str("Access-Control-Allow-Headers: Authorization, Content-Type\r\n");
    }
    resp.push_str("Content-Type: text/plain\r\n");
    resp.push_str(&format!("Content-Length: {}\r\n\r\n", body.len()));
    writer.write_all(resp.as_bytes())?;
    writer.write_all(body)?;
    Ok(())
}

fn write_json_response<W: Write>(
    writer: &mut W,
    code: u16,
    reason: &str,
    cors_origin: Option<&str>,
    body: &[u8],
) -> Result<(), std::io::Error> {
    let mut resp = format!("HTTP/1.1 {} {}\r\nConnection: close\r\n", code, reason);
    if let Some(origin) = cors_origin {
        resp.push_str(&format!("Access-Control-Allow-Origin: {}\r\n", origin));
        resp.push_str("Access-Control-Allow-Methods: GET, POST, OPTIONS\r\n");
        resp.push_str("Access-Control-Allow-Headers: Authorization, Content-Type\r\n");
    }
    resp.push_str("Content-Type: application/json\r\n");
    resp.push_str(&format!("Content-Length: {}\r\n\r\n", body.len()));
    writer.write_all(resp.as_bytes())?;
    writer.write_all(body)?;
    Ok(())
}

fn find_subsequence(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    haystack.windows(needle.len()).position(|window| window == needle)
}

/// Returns `true` only in test mode. Production sessions must never start
/// the loopback HTTP controller (which includes `/eval`).
pub fn controller_enabled(test_env: bool) -> bool {
    test_env
}

/// Spawns the loopback controller listener thread on `127.0.0.1:0` ONLY when
/// `test_env` is `true`. Without test mode, returns `Ok(None)` and binds no socket.
pub fn spawn_controller_if_enabled<F: Fn() + Send + Sync + 'static>(
    test_env: bool,
    config: ControllerConfig,
    on_open_dialog: F,
) -> Result<Option<(u16, std::thread::JoinHandle<()>)>, String> {
    if !controller_enabled(test_env) {
        return Ok(None);
    }
    spawn_controller(config, on_open_dialog).map(Some)
}

/// Returns the JavaScript snippet that injects `window.__COPERCEIVE_CTRL_PORT__`
/// and `window.__COPERCEIVE_CTRL_SECRET__` ONLY when `test_env` is `true`.
/// In production launches (`test_env == false`), returns an empty string so both
/// globals stay `undefined`.
pub fn ctrl_window_init_snippet(
    test_env: bool,
    port: Option<u16>,
    secret: Option<&str>,
) -> String {
    if !controller_enabled(test_env) {
        return String::new();
    }
    match (port, secret) {
        (Some(p), Some(s)) if p > 0 && !s.is_empty() => {
            let escaped_secret = s.replace('\\', "\\\\").replace('"', "\\\"");
            format!(
                "window.__COPERCEIVE_CTRL_PORT__ = {p};\n                window.__COPERCEIVE_CTRL_SECRET__ = \"{escaped_secret}\";"
            )
        }
        _ => String::new(),
    }
}

/// Synchronizes the tracked viewer origin and the test controller's allowed
/// CORS origin whenever the viewer window opens on a new session origin
/// (local sidecar, dataset stream, or remote SSH tunnel).
pub fn sync_viewer_origin(
    viewer_origin: &Mutex<Option<String>>,
    controller_config: &Mutex<Option<ControllerConfig>>,
    origin: &str,
) {
    if let Ok(mut lock) = viewer_origin.lock() {
        *lock = Some(origin.to_string());
    }
    if let Ok(lock) = controller_config.lock() {
        if let Some(ref cfg) = *lock {
            cfg.set_allowed_origin(origin.to_string());
        }
    }
}

/// Parameters required to build the viewer window's bootstrap script.
pub struct ViewerInitScriptParams<'a> {
    pub os_name: &'a str,
    pub titlebar_mode: &'a str,
    pub test_env: bool,
    pub test_repro: bool,
    pub force_motion: bool,
    pub update_url: &'a str,
    pub origin: &'a str,
    pub token: &'a str,
    pub data_root: &'a str,
    pub data_basename: &'a str,
    pub initial_state_json: &'a str,
    pub ctrl_port: Option<u16>,
    pub ctrl_secret: Option<&'a str>,
}

fn escape_js_string(raw: &str) -> String {
    raw.replace('\\', "\\\\").replace('"', "\\\"")
}

/// Returns `true` only when `target_url` stays strictly on `expected_origin`.
pub fn is_allowed_viewer_navigation(target_url: &str, expected_origin: &str) -> bool {
    let origin = expected_origin.trim_end_matches('/');
    if origin.is_empty() || !target_url.starts_with(origin) {
        return false;
    }
    let after_scheme = origin.split("://").nth(1).unwrap_or("");
    if after_scheme.is_empty() || after_scheme.contains('@') {
        return false;
    }
    let rest = &target_url[origin.len()..];
    rest.is_empty() || rest.starts_with('/') || rest.starts_with('?') || rest.starts_with('#')
}

/// Builds the viewer initialization script.
///
/// Sidecar credentials (`coperceive-token`) and test-only controller globals are
/// gated strictly on `window.location.origin === "<origin>"` (never a URL prefix
/// or `"null"` origin), and controller globals are omitted outside test mode.
pub fn build_viewer_init_script(params: &ViewerInitScriptParams<'_>) -> String {
    let escaped_origin = escape_js_string(params.origin);
    let escaped_token = escape_js_string(params.token);
    let escaped_root = escape_js_string(params.data_root);
    let escaped_base = escape_js_string(params.data_basename);
    let escaped_update = escape_js_string(params.update_url);
    let ctrl_snippet =
        ctrl_window_init_snippet(params.test_env, params.ctrl_port, params.ctrl_secret);
    format!(
        r#"
        (function() {{
            try {{
                window.__COPERCEIVE_DESKTOP__ = true;
                window.__COPERCEIVE_OS__ = "{os_name}";
                // Tauri injects its IPC object here, bounded to the viewer-window
                // capability (viewer-bridge + drag/maximize only).
                window.__COPERCEIVE_VIEWER_WINDOW__ = true;
                window.__COPERCEIVE_TITLEBAR__ = "{titlebar_mode}";
                window.__COPERCEIVE_TEST_ENV__ = {test_env};
                window.__COPERCEIVE_TEST_REPRO__ = {test_repro};
                window.__COPERCEIVE_FORCE_MOTION__ = {force_motion};
                window.__COPERCEIVE_UPDATE_URL__ = "{escaped_update}";
                window.__COPERCEIVE_DATA_ROOT__ = "{escaped_root}";
                window.__COPERCEIVE_DATA_BASENAME__ = "{escaped_base}";
                window.__COPERCEIVE_INITIAL_STATE__ = {initial_state_json};
                if (window.location.origin === "{escaped_origin}") {{
                    {ctrl_snippet}
                    try {{
                        sessionStorage.setItem("coperceive-token", "{escaped_token}");
                        sessionStorage.setItem("coperceive-origin", "{escaped_origin}");
                    }} catch (_) {{}}
                }}
            }} catch (e) {{
                console.error("Failed to initialize coperceive desktop state", e);
            }}
        }})();
        "#,
        os_name = params.os_name,
        titlebar_mode = params.titlebar_mode,
        test_env = params.test_env,
        test_repro = params.test_repro,
        force_motion = params.force_motion,
        initial_state_json = params.initial_state_json,
    )
}

/// Spawns the loopback controller listener thread on port 127.0.0.1:0.
pub fn spawn_controller<F: Fn() + Send + Sync + 'static>(
    config: ControllerConfig,
    on_open_dialog: F,
) -> Result<(u16, std::thread::JoinHandle<()>), String> {
    let listener = TcpListener::bind("127.0.0.1:0")
        .map_err(|e| format!("Failed to bind loopback controller: {}", e))?;
    let port = listener
        .local_addr()
        .map_err(|e| e.to_string())?
        .port();

    let mut cfg = config;
    cfg.ctrl_port = port;
    let on_dialog = Arc::new(on_open_dialog);

    let handle = std::thread::spawn(move || {
        for stream in listener.incoming() {
            if let Ok(s) = stream {
                let cfg_clone = cfg.clone();
                let cb = on_dialog.clone();
                std::thread::spawn(move || {
                    let _ = s.set_read_timeout(Some(Duration::from_millis(5000)));
                    let mut reader = s.try_clone().unwrap_or_else(|_| s.try_clone().unwrap());
                    let mut writer = s;
                    let _ = handle_client(&mut reader, &mut writer, &cfg_clone, &move || cb());
                });
            }
        }
    });

    Ok((port, handle))
}

#[cfg(test)]
pub mod tests {
    use super::*;
    use std::io::Cursor;

    fn make_test_config() -> ControllerConfig {
        ControllerConfig::new(
            51234,
            "secret-token-test-123".into(),
            Some("http://127.0.0.1:8000".into()),
        )
    }

    #[test]
    fn test_missing_or_invalid_token_returns_401() {
        let config = make_test_config();
        let req = b"GET /state HTTP/1.1\r\nHost: 127.0.0.1:51234\r\nOrigin: http://127.0.0.1:8000\r\n\r\n";
        let mut reader = Cursor::new(req.to_vec());
        let mut writer = Vec::new();

        handle_client(&mut reader, &mut writer, &config, &|| {}).unwrap();
        let resp = String::from_utf8(writer).unwrap();

        assert!(resp.starts_with("HTTP/1.1 401 Unauthorized"), "Expected 401, got: {}", resp);
        assert!(resp.contains("Access-Control-Allow-Origin: http://127.0.0.1:8000"));
    }

    #[test]
    fn test_wrong_origin_omits_cors_headers() {
        let config = make_test_config();
        let req = b"OPTIONS /state HTTP/1.1\r\nHost: 127.0.0.1:51234\r\nOrigin: http://evil.com\r\n\r\n";
        let mut reader = Cursor::new(req.to_vec());
        let mut writer = Vec::new();

        handle_client(&mut reader, &mut writer, &config, &|| {}).unwrap();
        let resp = String::from_utf8(writer).unwrap();

        assert!(resp.starts_with("HTTP/1.1 403 Forbidden"), "Expected 403, got: {}", resp);
        assert!(!resp.contains("Access-Control-Allow-Origin"), "Expected NO CORS headers, got: {}", resp);
    }

    #[test]
    fn test_wrong_host_returns_400_dns_rebinding() {
        let config = make_test_config();
        // Attack attempt via DNS rebinding (Host header evil.com or rebind.local)
        let req = b"POST /state HTTP/1.1\r\nHost: evil.attacker.com\r\nAuthorization: Bearer secret-token-test-123\r\n\r\n";
        let mut reader = Cursor::new(req.to_vec());
        let mut writer = Vec::new();

        handle_client(&mut reader, &mut writer, &config, &|| {}).unwrap();
        let resp = String::from_utf8(writer).unwrap();

        assert!(resp.starts_with("HTTP/1.1 400 Bad Request"), "Expected 400, got: {}", resp);
        assert!(!resp.contains("Access-Control-Allow-Origin"));
    }

    struct ChunkedReader {
        chunks: Vec<Vec<u8>>,
        chunk_idx: usize,
    }

    impl Read for ChunkedReader {
        fn read(&mut self, buf: &mut [u8]) -> std::io::Result<usize> {
            if self.chunk_idx >= self.chunks.len() {
                return Ok(0);
            }
            let chunk = &self.chunks[self.chunk_idx];
            let to_copy = chunk.len().min(buf.len());
            buf[..to_copy].copy_from_slice(&chunk[..to_copy]);
            self.chunk_idx += 1;
            Ok(to_copy)
        }
    }

    #[test]
    fn test_body_split_across_packets_saved() {
        let temp_dir = std::env::temp_dir().join(format!("cp-state-test-{}", std::process::id()));
        let _ = std::fs::create_dir_all(&temp_dir);
        let state_file = temp_dir.join("state.json");

        let mut config = make_test_config();
        config.state_path = Some(state_file.clone());

        let payload = r#"{"scene_id":"synth_001","sample_index":3,"timestamp_us":300000}"#;
        let header = format!(
            "POST /state HTTP/1.1\r\n\
             Host: 127.0.0.1:51234\r\n\
             Origin: http://127.0.0.1:8000\r\n\
             Authorization: Bearer secret-token-test-123\r\n\
             Content-Type: application/json\r\n\
             Content-Length: {}\r\n\r\n",
            payload.len()
        );

        // Split header and body across 3 simulated TCP packet chunks
        let p1 = header.as_bytes().to_vec();
        let p2 = payload.as_bytes()[..20].to_vec();
        let p3 = payload.as_bytes()[20..].to_vec();

        let mut reader = ChunkedReader {
            chunks: vec![p1, p2, p3],
            chunk_idx: 0,
        };
        let mut writer = Vec::new();

        handle_client(&mut reader, &mut writer, &config, &|| {}).unwrap();
        let resp = String::from_utf8(writer).unwrap();

        assert!(resp.starts_with("HTTP/1.1 200 OK"), "Expected 200 OK, got: {}", resp);
        assert!(resp.contains("Access-Control-Allow-Origin: http://127.0.0.1:8000"));

        let loaded = state::load_state_from_path(&state_file);
        assert_eq!(loaded.last_scene_id, Some("synth_001".into()));
        assert_eq!(loaded.last_sample_index, Some(3));
        assert_eq!(loaded.last_timestamp_us, Some(300000));

        let _ = std::fs::remove_dir_all(&temp_dir);
    }

    #[test]
    fn test_rate_limit_open_dialog() {
        let config = make_test_config();
        let req = b"POST /open-dialog HTTP/1.1\r\n\
                    Host: 127.0.0.1:51234\r\n\
                    Origin: http://127.0.0.1:8000\r\n\
                    Authorization: Bearer secret-token-test-123\r\n\
                    Content-Length: 0\r\n\r\n";

        let mut r1 = Cursor::new(req.to_vec());
        let mut w1 = Vec::new();
        handle_client(&mut r1, &mut w1, &config, &|| {}).unwrap();
        let resp1 = String::from_utf8(w1).unwrap();
        assert!(resp1.starts_with("HTTP/1.1 200 OK"));

        // Immediate subsequent request must be rate-limited (429)
        let mut r2 = Cursor::new(req.to_vec());
        let mut w2 = Vec::new();
        handle_client(&mut r2, &mut w2, &config, &|| {}).unwrap();
        let resp2 = String::from_utf8(w2).unwrap();
        assert!(resp2.starts_with("HTTP/1.1 429 Too Many Requests"), "Expected 429, got: {}", resp2);
    }

    #[test]
    fn test_payload_too_large_rejected() {
        let config = make_test_config();
        let req = format!(
            "POST /state HTTP/1.1\r\n\
             Host: 127.0.0.1:51234\r\n\
             Origin: http://127.0.0.1:8000\r\n\
             Authorization: Bearer secret-token-test-123\r\n\
             Content-Type: application/json\r\n\
             Content-Length: {}\r\n\r\n",
            MAX_BODY_SIZE + 100
        );
        let mut reader = Cursor::new(req.into_bytes());
        let mut writer = Vec::new();

        handle_client(&mut reader, &mut writer, &config, &|| {}).unwrap();
        let resp = String::from_utf8(writer).unwrap();

        assert!(resp.starts_with("HTTP/1.1 413 Payload Too Large"), "Expected 413, got: {}", resp);
    }

    #[test]
    fn test_eval_and_eval_result_flow() {
        let mut config = make_test_config();
        let config_for_dispatcher = config.clone();

        config.eval_dispatcher = Some(Arc::new(move |script: String, _window: Option<String>| {
            if let Some(pos) = script.find("req_id: ") {
                let rest = &script[pos + 8..];
                let end = rest.find(',').unwrap_or(rest.len());
                let req_id: u64 = rest[..end].trim().parse().unwrap();

                let cfg = config_for_dispatcher.clone();
                std::thread::spawn(move || {
                    let payload = format!("{{\"req_id\":{},\"result\":42,\"error\":null}}", req_id);
                    let req = format!(
                        "POST /eval-result HTTP/1.1\r\n\
                         Host: 127.0.0.1:51234\r\n\
                         Origin: http://127.0.0.1:8000\r\n\
                         Authorization: Bearer secret-token-test-123\r\n\
                         Content-Type: application/json\r\n\
                         Content-Length: {}\r\n\r\n{}",
                        payload.len(),
                        payload
                    );
                    let mut reader = Cursor::new(req.into_bytes());
                    let mut writer = Vec::new();
                    let _ = handle_client(&mut reader, &mut writer, &cfg, &|| {});
                });
            }
            Ok(())
        }));

        let eval_payload = r#"{"script":"21 * 2"}"#;
        let req = format!(
            "POST /eval HTTP/1.1\r\n\
             Host: 127.0.0.1:51234\r\n\
             Origin: http://127.0.0.1:8000\r\n\
             Authorization: Bearer secret-token-test-123\r\n\
             Content-Type: application/json\r\n\
             Content-Length: {}\r\n\r\n{}",
            eval_payload.len(),
            eval_payload
        );

        let mut reader = Cursor::new(req.into_bytes());
        let mut writer = Vec::new();
        handle_client(&mut reader, &mut writer, &config, &|| {}).unwrap();
        let resp = String::from_utf8(writer).unwrap();

        assert!(resp.starts_with("HTTP/1.1 200 OK"), "Expected 200 OK, got: {}", resp);
        assert!(resp.contains("\"result\":42"));
    }

    #[test]
    fn test_spawn_controller_eval_real_socket() {
        use std::net::TcpStream;

        let _port_guard = crate::test_seams::PORT_TEST_LOCK
            .lock()
            .unwrap_or_else(|p| p.into_inner());
        let mut config = make_test_config();
        let port_holder = Arc::new(Mutex::new(0u16));
        let port_holder_clone = port_holder.clone();

        config.eval_dispatcher = Some(Arc::new(move |script: String, _window: Option<String>| {
            let port = *port_holder_clone.lock().unwrap();
            if let Some(pos) = script.find("req_id: ") {
                let rest = &script[pos + 8..];
                let end = rest.find(',').unwrap_or(rest.len());
                let req_id: u64 = rest[..end].trim().parse().unwrap();

                std::thread::spawn(move || {
                    let mut stream = TcpStream::connect(format!("127.0.0.1:{}", port)).unwrap();
                    let payload = format!("{{\"req_id\":{},\"result\":100,\"error\":null}}", req_id);
                    let req = format!(
                        "POST /eval-result HTTP/1.1\r\n\
                         Host: 127.0.0.1:{}\r\n\
                         Origin: http://127.0.0.1:8000\r\n\
                         Authorization: Bearer secret-token-test-123\r\n\
                         Content-Type: application/json\r\n\
                         Content-Length: {}\r\n\r\n{}",
                        port,
                        payload.len(),
                        payload
                    );
                    stream.write_all(req.as_bytes()).unwrap();
                });
            }
            Ok(())
        }));

        let (port, _handle) = spawn_controller(config, || {}).unwrap();
        *port_holder.lock().unwrap() = port;

        let mut stream = TcpStream::connect(format!("127.0.0.1:{}", port)).unwrap();
        let eval_payload = r#"{"script":"50 * 2"}"#;
        let req = format!(
            "POST /eval HTTP/1.1\r\n\
             Host: 127.0.0.1:{}\r\n\
             Origin: http://127.0.0.1:8000\r\n\
             Authorization: Bearer secret-token-test-123\r\n\
             Content-Type: application/json\r\n\
             Content-Length: {}\r\n\r\n{}",
            port,
            eval_payload.len(),
            eval_payload
        );
        stream.write_all(req.as_bytes()).unwrap();

        let mut resp_buf = Vec::new();
        stream.set_read_timeout(Some(Duration::from_secs(3))).unwrap();
        let _ = stream.read_to_end(&mut resp_buf);
        let resp = String::from_utf8_lossy(&resp_buf);

        assert!(resp.starts_with("HTTP/1.1 200 OK"), "Expected 200 OK, got: {}", resp);
        assert!(resp.contains("\"result\":100"));
    }

    #[test]
    fn test_eval_dispatcher_error_returns_502() {
        let mut config = make_test_config();
        config.eval_dispatcher = Some(Arc::new(move |_script: String, _window: Option<String>| {
            Err("Window not found".into())
        }));

        let eval_payload = r#"{"script":"21 * 2"}"#;
        let req = format!(
            "POST /eval HTTP/1.1\r\n\
             Host: 127.0.0.1:51234\r\n\
             Origin: http://127.0.0.1:8000\r\n\
             Authorization: Bearer secret-token-test-123\r\n\
             Content-Type: application/json\r\n\
             Content-Length: {}\r\n\r\n{}",
            eval_payload.len(),
            eval_payload
        );

        let mut reader = Cursor::new(req.into_bytes());
        let mut writer = Vec::new();
        handle_client(&mut reader, &mut writer, &config, &|| {}).unwrap();
        let resp = String::from_utf8(writer).unwrap();

        assert!(resp.starts_with("HTTP/1.1 502 Bad Gateway"), "Expected 502 Bad Gateway, got: {}", resp);
        assert!(resp.contains("Window not found"));
    }

    #[test]
    fn test_eval_timeout_clamping() {
        // Default when missing or null
        assert_eq!(parse_eval_timeout(&serde_json::json!({})), 10);
        assert_eq!(parse_eval_timeout(&serde_json::json!({"timeout": null})), 10);
        // Valid in-range values
        assert_eq!(parse_eval_timeout(&serde_json::json!({"timeout": 1})), 1);
        assert_eq!(parse_eval_timeout(&serde_json::json!({"timeout": 15})), 15);
        assert_eq!(parse_eval_timeout(&serde_json::json!({"timeout": 60})), 60);
        // Out-of-range low clamped to 1
        assert_eq!(parse_eval_timeout(&serde_json::json!({"timeout": 0})), 1);
        // Out-of-range high clamped to 60
        assert_eq!(parse_eval_timeout(&serde_json::json!({"timeout": 61})), 60);
        assert_eq!(parse_eval_timeout(&serde_json::json!({"timeout": 100})), 60);
        assert_eq!(parse_eval_timeout(&serde_json::json!({"timeout": 999999})), 60);
        // Negative or non-integer types fall back to default
        assert_eq!(parse_eval_timeout(&serde_json::json!({"timeout": -5})), 10);
        assert_eq!(parse_eval_timeout(&serde_json::json!({"timeout": "fast"})), 10);
        assert_eq!(parse_eval_timeout(&serde_json::json!({"timeout": false})), 10);
    }

    #[test]
    fn test_export_pick_endpoint() {
        let mut config = make_test_config();
        config.export_picker = Some(Arc::new(|default_name: &str, ext: &str| {
            if default_name == "fail" {
                Err("Picker failed".into())
            } else {
                Ok(Some(format!("/tmp/{}.{}", default_name, ext)))
            }
        }));

        // Success case
        let payload = r#"{"default_name":"test_clip","extension":"mp4"}"#;
        let req = format!(
            "POST /export-pick HTTP/1.1\r\n\
             Host: 127.0.0.1:51234\r\n\
             Origin: http://127.0.0.1:8000\r\n\
             Authorization: Bearer secret-token-test-123\r\n\
             Content-Type: application/json\r\n\
             Content-Length: {}\r\n\r\n{}",
            payload.len(),
            payload
        );
        let mut reader = Cursor::new(req.into_bytes());
        let mut writer = Vec::new();
        handle_client(&mut reader, &mut writer, &config, &|| {}).unwrap();
        let resp = String::from_utf8(writer).unwrap();
        assert!(resp.starts_with("HTTP/1.1 200 OK"), "Expected 200 OK, got: {}", resp);
        assert!(resp.contains("/tmp/test_clip.mp4"));

        // Error case
        let err_payload = r#"{"default_name":"fail","extension":"mp4"}"#;
        let err_req = format!(
            "POST /export-pick HTTP/1.1\r\n\
             Host: 127.0.0.1:51234\r\n\
             Origin: http://127.0.0.1:8000\r\n\
             Authorization: Bearer secret-token-test-123\r\n\
             Content-Type: application/json\r\n\
             Content-Length: {}\r\n\r\n{}",
            err_payload.len(),
            err_payload
        );
        let mut reader = Cursor::new(err_req.into_bytes());
        let mut writer = Vec::new();
        handle_client(&mut reader, &mut writer, &config, &|| {}).unwrap();
        let resp = String::from_utf8(writer).unwrap();
        assert!(resp.starts_with("HTTP/1.1 400 Bad Request"), "Expected 400, got: {}", resp);
        assert!(resp.contains("Picker failed"));
    }

    #[test]
    fn test_export_save_endpoint() {
        let mut config = make_test_config();
        config.export_saver = Some(Arc::new(|src_rel: Option<&str>, bytes: Option<&[u8]>| {
            if let Some(src) = src_rel {
                if src == "missing.png" {
                    return Err("Source file missing".into());
                }
                return Ok(format!("/saved/dest/{}", src));
            }
            if let Some(b) = bytes {
                return Ok(format!("/saved/bytes/len_{}", b.len()));
            }
            Err("No export source".into())
        }));

        let payload = r#"{"source_relative":"clip.mp4"}"#;
        let req = format!(
            "POST /export-save HTTP/1.1\r\n\
             Host: 127.0.0.1:51234\r\n\
             Origin: http://127.0.0.1:8000\r\n\
             Authorization: Bearer secret-token-test-123\r\n\
             Content-Type: application/json\r\n\
             Content-Length: {}\r\n\r\n{}",
            payload.len(),
            payload
        );
        let mut reader = Cursor::new(req.into_bytes());
        let mut writer = Vec::new();
        handle_client(&mut reader, &mut writer, &config, &|| {}).unwrap();
        let resp = String::from_utf8(writer).unwrap();
        assert!(resp.starts_with("HTTP/1.1 200 OK"), "Expected 200 OK, got: {}", resp);
        assert!(resp.contains("/saved/dest/clip.mp4"));

        // Bytes case
        let bytes_payload = r#"{"bytes":[1,2,3,4,5]}"#;
        let bytes_req = format!(
            "POST /export-save HTTP/1.1\r\n\
             Host: 127.0.0.1:51234\r\n\
             Origin: http://127.0.0.1:8000\r\n\
             Authorization: Bearer secret-token-test-123\r\n\
             Content-Type: application/json\r\n\
             Content-Length: {}\r\n\r\n{}",
            bytes_payload.len(),
            bytes_payload
        );
        let mut reader = Cursor::new(bytes_req.into_bytes());
        let mut writer = Vec::new();
        handle_client(&mut reader, &mut writer, &config, &|| {}).unwrap();
        let resp = String::from_utf8(writer).unwrap();
        assert!(resp.starts_with("HTTP/1.1 200 OK"), "Expected 200 OK, got: {}", resp);
        assert!(resp.contains("/saved/bytes/len_5"));
    }

    #[test]
    fn test_export_save_byte_validation() {
        let mut config = make_test_config();
        config.export_saver = Some(Arc::new(|_, _| Ok("/saved".into())));

        // Value above 255 rejected
        let invalid_val_payload = r#"{"bytes":[0, 100, 256, 1]}"#;
        let req = format!(
            "POST /export-save HTTP/1.1\r\n\
             Host: 127.0.0.1:51234\r\n\
             Origin: http://127.0.0.1:8000\r\n\
             Authorization: Bearer secret-token-test-123\r\n\
             Content-Type: application/json\r\n\
             Content-Length: {}\r\n\r\n{}",
            invalid_val_payload.len(),
            invalid_val_payload
        );
        let mut reader = Cursor::new(req.into_bytes());
        let mut writer = Vec::new();
        handle_client(&mut reader, &mut writer, &config, &|| {}).unwrap();
        let resp = String::from_utf8(writer).unwrap();
        assert!(resp.starts_with("HTTP/1.1 400 Bad Request"), "Expected 400, got: {}", resp);
        assert!(resp.contains("Invalid byte value in export payload: must be integers between 0 and 255"));

        // Negative value rejected
        let negative_val_payload = r#"{"bytes":[0, -5, 20]}"#;
        let req = format!(
            "POST /export-save HTTP/1.1\r\n\
             Host: 127.0.0.1:51234\r\n\
             Origin: http://127.0.0.1:8000\r\n\
             Authorization: Bearer secret-token-test-123\r\n\
             Content-Type: application/json\r\n\
             Content-Length: {}\r\n\r\n{}",
            negative_val_payload.len(),
            negative_val_payload
        );
        let mut reader = Cursor::new(req.into_bytes());
        let mut writer = Vec::new();
        handle_client(&mut reader, &mut writer, &config, &|| {}).unwrap();
        let resp = String::from_utf8(writer).unwrap();
        assert!(resp.starts_with("HTTP/1.1 400 Bad Request"), "Expected 400, got: {}", resp);
        assert!(resp.contains("Invalid byte value in export payload"));
    }

    #[test]
    fn test_reveal_in_finder_endpoint() {
        let mut config = make_test_config();
        let reveal_called = Arc::new(Mutex::new(false));
        let called_clone = reveal_called.clone();
        config.finder_revealer = Some(Arc::new(move || {
            *called_clone.lock().unwrap() = true;
            Ok(())
        }));

        let payload = r#"{}"#;
        let req = format!(
            "POST /reveal-in-finder HTTP/1.1\r\n\
             Host: 127.0.0.1:51234\r\n\
             Origin: http://127.0.0.1:8000\r\n\
             Authorization: Bearer secret-token-test-123\r\n\
             Content-Type: application/json\r\n\
             Content-Length: {}\r\n\r\n{}",
            payload.len(),
            payload
        );
        let mut reader = Cursor::new(req.clone().into_bytes());
        let mut writer = Vec::new();
        handle_client(&mut reader, &mut writer, &config, &|| {}).unwrap();
        let resp = String::from_utf8(writer).unwrap();
        assert!(resp.starts_with("HTTP/1.1 200 OK"), "Expected 200 OK, got: {}", resp);
        assert!(*reveal_called.lock().unwrap());

        // Error case (e.g. no export saved yet)
        config.finder_revealer = Some(Arc::new(|| Err("No exported file has been saved yet".into())));
        let mut reader = Cursor::new(req.into_bytes());
        let mut writer = Vec::new();
        handle_client(&mut reader, &mut writer, &config, &|| {}).unwrap();
        let resp = String::from_utf8(writer).unwrap();
        assert!(resp.starts_with("HTTP/1.1 400 Bad Request"), "Expected 400, got: {}", resp);
        assert!(resp.contains("No exported file has been saved yet"));
    }

    #[test]
    fn test_viewer_endpoints_report_the_session_dataset_scenes_and_ask_to_prepare() {
        let mut config = ControllerConfig::new(51234, "secret".to_string(), Some("http://127.0.0.1:8000".to_string()));
        let asked = Arc::new(Mutex::new(Vec::<String>::new()));
        let record = asked.clone();
        config.scene_preparer = Some(Arc::new(move |scene: String| {
            record.lock().unwrap().push(scene);
            true
        }));
        config.viewer_session = Some(Arc::new(|| serde_json::json!({"job_id": 7, "time_left": "3:59:00"})));
        *config.dataset_scenes.lock().unwrap() = serde_json::json!({"scenes": [{"scene_id": "a", "state": "ready"}]});
        let call = |request: String| {
            let mut writer = Vec::new();
            let mut reader = Cursor::new(request.into_bytes());
            handle_client(&mut reader, &mut writer, &config, &|| {}).unwrap();
            String::from_utf8(writer).unwrap()
        };
        let get = |path: &str, auth: &str| {
            format!("GET {path} HTTP/1.1\r\nHost: 127.0.0.1:51234\r\nOrigin: http://127.0.0.1:8000\r\nAuthorization: Bearer {auth}\r\n\r\n")
        };
        assert!(call(get("/viewer-session", "wrong")).starts_with("HTTP/1.1 401"));
        assert!(call(get("/viewer-session", "secret")).contains("\"time_left\":\"3:59:00\""));
        assert!(call(get("/dataset-scenes", "secret")).contains("\"scene_id\":\"a\""));
        let post = |body: &str| {
            format!(
                "POST /prepare-scene HTTP/1.1\r\nHost: 127.0.0.1:51234\r\nOrigin: http://127.0.0.1:8000\r\nAuthorization: Bearer secret\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{body}",
                body.len()
            )
        };
        assert!(call(post("{}")).starts_with("HTTP/1.1 400"));
        assert!(call(post("{\"scene_id\":\"scene_0002\"}")).contains("\"shown\":true"));
        assert_eq!(*asked.lock().unwrap(), vec!["scene_0002".to_string()]);
    }

    #[test]
    fn test_show_library_endpoint_needs_the_secret_and_calls_the_shower() {
        let mut config = make_test_config();
        let calls = Arc::new(Mutex::new(0));
        let counter = calls.clone();
        config.library_shower = Some(Arc::new(move || {
            *counter.lock().unwrap() += 1;
            true
        }));
        let request = |auth: &str| {
            format!(
                "POST /show-library HTTP/1.1\r\n\
                 Host: 127.0.0.1:51234\r\n\
                 Origin: http://127.0.0.1:8000\r\n\
                 Authorization: Bearer {auth}\r\n\
                 Content-Length: 0\r\n\r\n"
            )
        };

        let mut writer = Vec::new();
        let mut reader = Cursor::new(request("wrong").into_bytes());
        handle_client(&mut reader, &mut writer, &config, &|| {}).unwrap();
        assert!(String::from_utf8(writer).unwrap().starts_with("HTTP/1.1 401"));
        assert_eq!(*calls.lock().unwrap(), 0);

        let mut writer = Vec::new();
        let mut reader = Cursor::new(request("secret-token-test-123").into_bytes());
        handle_client(&mut reader, &mut writer, &config, &|| {}).unwrap();
        let resp = String::from_utf8(writer).unwrap();
        assert!(resp.starts_with("HTTP/1.1 200 OK"), "{resp}");
        assert!(resp.ends_with(r#"{"shown":true}"#), "{resp}");
        assert_eq!(*calls.lock().unwrap(), 1);
    }

    #[test]
    fn test_controller_does_not_start_and_no_secret_injected_without_test_mode() {
        assert!(!controller_enabled(false));
        assert!(controller_enabled(true));

        let result = spawn_controller_if_enabled(false, make_test_config(), || {});
        assert!(matches!(result, Ok(None)), "controller must not bind without test mode");

        let snippet_prod = ctrl_window_init_snippet(false, Some(45678), Some("top-secret-token"));
        assert!(snippet_prod.is_empty(), "production init snippet must be empty: {snippet_prod}");
        assert!(!snippet_prod.contains("__COPERCEIVE_CTRL_PORT__"));
        assert!(!snippet_prod.contains("__COPERCEIVE_CTRL_SECRET__"));
        assert!(!snippet_prod.contains("top-secret-token"));

        let snippet_test = ctrl_window_init_snippet(true, Some(45678), Some("top-secret-token"));
        assert!(snippet_test.contains("window.__COPERCEIVE_CTRL_PORT__ = 45678;"));
        assert!(snippet_test.contains("window.__COPERCEIVE_CTRL_SECRET__ = \"top-secret-token\";"));

        let main = include_str!("main.rs");
        assert!(
            !main.contains("__COPERCEIVE_CTRL_PORT__"),
            "main.rs must only inject controller globals via controller::ctrl_window_init_snippet"
        );
        assert!(
            !main.contains("__COPERCEIVE_CTRL_SECRET__"),
            "main.rs must only inject controller secret via controller::ctrl_window_init_snippet"
        );
        assert!(
            main.contains("controller::controller_enabled(")
                && main.contains("controller::spawn_controller_if_enabled("),
            "main.rs must gate controller startup through controller_enabled and spawn_controller_if_enabled"
        );
    }

    #[test]
    fn test_security_review_reproducer_eval_and_origin_gates() {
        // Mirrors steps [1], [3], [4] of docs/security/review-2026-11.md's reproducer:
        let config = ControllerConfig::new(
            51234,
            "secret-token-test-123".into(),
            Some("http://127.0.0.1:4700".into()),
        );

        // [1] Missing bearer returns 401
        let no_auth = "POST /eval HTTP/1.1\r\nHost: 127.0.0.1:51234\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}";
        let mut r = Cursor::new(no_auth.as_bytes().to_vec());
        let mut w = Vec::new();
        handle_client(&mut r, &mut w, &config, &|| {}).unwrap();
        assert!(String::from_utf8(w).unwrap().starts_with("HTTP/1.1 401"));

        // [4] Another loopback port MUST NOT pass CORS preflight even after allowed_origin is set!
        let other_loopback_preflight = "OPTIONS /eval HTTP/1.1\r\n\
             Host: 127.0.0.1:51234\r\n\
             Origin: http://127.0.0.1:5555\r\n\
             Access-Control-Request-Method: POST\r\n\r\n";
        let mut r = Cursor::new(other_loopback_preflight.as_bytes().to_vec());
        let mut w = Vec::new();
        handle_client(&mut r, &mut w, &config, &|| {}).unwrap();
        let resp = String::from_utf8(w).unwrap();
        assert!(
            resp.starts_with("HTTP/1.1 403"),
            "unrelated loopback origin must be rejected after session active, got: {resp}"
        );
        assert!(
            !resp.contains("Access-Control-Allow-Origin"),
            "unrelated loopback origin must receive no ACAO header, got: {resp}"
        );

        // FOLD-12 / viewer init script and navigation pinning against crafted URLs
        assert!(is_allowed_viewer_navigation("http://127.0.0.1:4700/#/viewer", "http://127.0.0.1:4700"));
        assert!(!is_allowed_viewer_navigation("http://127.0.0.1:5555/", "http://127.0.0.1:4700"));
        assert!(!is_allowed_viewer_navigation("http://evil.example/", "http://127.0.0.1:4700"));
        assert!(!is_allowed_viewer_navigation("http://127.0.0.1:47000/", "http://127.0.0.1:4700"));
        assert!(!is_allowed_viewer_navigation("http://127.0.0.1:4700@evil.example/", "http://127.0.0.1:4700"));

        let prod_script = build_viewer_init_script(&ViewerInitScriptParams {
            os_name: "macos",
            titlebar_mode: "overlay",
            test_env: false,
            test_repro: false,
            force_motion: false,
            update_url: "",
            origin: "http://127.0.0.1:4700",
            token: "sidecar-tok",
            data_root: "/tmp/demo",
            data_basename: "demo",
            initial_state_json: "{}",
            ctrl_port: Some(51234),
            ctrl_secret: Some("ctrl-sec"),
        });
        assert!(!prod_script.contains("ctrl-sec"));
        assert!(!prod_script.contains("__COPERCEIVE_CTRL_PORT__"));
        assert!(!prod_script.contains("__COPERCEIVE_CTRL_SECRET__"));
        assert!(!prod_script.contains("indexOf"));
        assert!(!prod_script.contains("=== \"null\""));
        assert!(prod_script.contains("window.location.origin === \"http://127.0.0.1:4700\""));
    }

    #[test]
    fn test_open_viewer_window_updates_controller_allowed_origin_for_remote_connect() {
        let local_origin = "http://127.0.0.1:50681";
        let remote_origin = "http://127.0.0.1:50726";
        let secret = "secret-token-flow-f";
        let config = ControllerConfig::new(51234, secret.into(), Some(local_origin.to_string()));
        let viewer_origin = Mutex::new(Some(local_origin.to_string()));
        let controller_config = Mutex::new(Some(config.clone()));

        // Switch from local viewer to remote SSH tunnel viewer: CORS allowed_origin must move to remote_origin.
        sync_viewer_origin(&viewer_origin, &controller_config, remote_origin);
        assert_eq!(
            viewer_origin.lock().unwrap().as_deref(),
            Some(remote_origin)
        );

        let preflight = |req_origin: &str| -> String {
            let req = format!(
                "OPTIONS /eval-result HTTP/1.1\r\n\
                 Host: 127.0.0.1:51234\r\n\
                 Origin: {req_origin}\r\n\
                 Access-Control-Request-Method: POST\r\n\
                 Access-Control-Request-Headers: authorization, content-type\r\n\r\n"
            );
            let mut r = Cursor::new(req.into_bytes());
            let mut w = Vec::new();
            handle_client(&mut r, &mut w, &config, &|| {}).unwrap();
            String::from_utf8(w).unwrap()
        };

        let remote_preflight = preflight(remote_origin);
        assert!(
            remote_preflight.starts_with("HTTP/1.1 204 No Content"),
            "remote viewer origin preflight must succeed, got: {remote_preflight}"
        );
        assert!(
            remote_preflight.contains(&format!("Access-Control-Allow-Origin: {remote_origin}")),
            "remote viewer origin must receive ACAO header, got: {remote_preflight}"
        );

        let stale_local_preflight = preflight(local_origin);
        assert!(
            stale_local_preflight.starts_with("HTTP/1.1 403 Forbidden"),
            "superseded local origin must be rejected after remote_connect, got: {stale_local_preflight}"
        );

        // Verify POST /eval-result from remote_origin unblocks /eval waiting callback.
        let (tx, rx) = std::sync::mpsc::channel();
        config.eval_callbacks.lock().unwrap().insert(42, tx);
        let payload = r#"{"req_id":42,"result":{"ok":true},"error":null}"#;
        let eval_result_req = format!(
            "POST /eval-result HTTP/1.1\r\n\
             Host: 127.0.0.1:51234\r\n\
             Origin: {remote_origin}\r\n\
             Authorization: Bearer {secret}\r\n\
             Content-Type: application/json\r\n\
             Content-Length: {}\r\n\r\n{}",
            payload.len(),
            payload
        );
        let mut r = Cursor::new(eval_result_req.into_bytes());
        let mut w = Vec::new();
        handle_client(&mut r, &mut w, &config, &|| {}).unwrap();
        let post_resp = String::from_utf8(w).unwrap();
        assert!(post_resp.starts_with("HTTP/1.1 200 OK"), "{post_resp}");
        let delivered = rx
            .recv_timeout(Duration::from_millis(200))
            .expect("eval-result from remote viewer should resolve waiting /eval");
        assert_eq!(delivered.get("result").and_then(|v| v.get("ok")).and_then(|v| v.as_bool()), Some(true));

        // Verify main.rs open_viewer_window synchronizes both viewer_origin and controller allowed_origin
        // so callers like remote_connect cannot leave allowed_origin stale on a prior session's port.
        let main = include_str!("main.rs");
        let start = main
            .find("fn open_viewer_window(")
            .expect("open_viewer_window exists in main.rs");
        let end = main[start..]
            .find("fn build_viewer_bridge_plugin(")
            .or_else(|| main[start..].find("async fn switch_data_root("))
            .map(|rel| start + rel)
            .expect("end of open_viewer_window block");
        let open_fn = &main[start..end];
        assert!(
            open_fn.contains("controller::sync_viewer_origin("),
            "open_viewer_window must call controller::sync_viewer_origin so remote_connect and other callers update controller allowed_origin"
        );
    }
}
