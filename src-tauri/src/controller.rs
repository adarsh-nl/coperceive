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
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use crate::state::{self, LayerToggles, PanelCollapseState, WindowState};

/// Maximum allowed request body size (64 KB).
pub const MAX_BODY_SIZE: usize = 65536;

/// Rate limit window for `/open-dialog` (1 second).
pub const OPEN_DIALOG_RATE_LIMIT: Duration = Duration::from_millis(1000);

pub type EvalSender = std::sync::mpsc::Sender<serde_json::Value>;

/// Configuration for the loopback controller.
#[derive(Clone)]
pub struct ControllerConfig {
    pub ctrl_port: u16,
    pub auth_secret: String,
    pub allowed_origin: Arc<Mutex<Option<String>>>,
    pub last_open_dialog: Arc<Mutex<Option<Instant>>>,
    pub eval_callbacks: Arc<Mutex<HashMap<u64, EvalSender>>>,
    pub eval_counter: Arc<Mutex<u64>>,
    pub eval_dispatcher: Option<Arc<dyn Fn(String) -> Result<(), String> + Send + Sync>>,
    pub window_closer: Option<Arc<dyn Fn(&str) + Send + Sync>>,
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

    if content_len > MAX_BODY_SIZE {
        write_response(
            writer,
            413,
            "Payload Too Large",
            cors_origin,
            b"Payload exceeds 64 KB limit",
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
            if origin == "tauri://localhost"
                || origin == "http://tauri.localhost"
                || (origin.starts_with("http://127.0.0.1:") && allowed_origin.is_some()) =>
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

    if method == "POST" && path == "/eval-result" {
        let body = match read_request_body(reader, writer, &headers, &header_buf, header_pos, cors_origin.as_deref())? {
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
        let body = match read_request_body(reader, writer, &headers, &header_buf, header_pos, cors_origin.as_deref())? {
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

            if let Err(dispatch_err) = dispatcher(wrapper) {
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
        let body = match read_request_body(reader, writer, &headers, &header_buf, header_pos, cors_origin.as_deref())? {
            Some(b) => b,
            None => return Ok(()),
        };

        // Parse JSON and apply updates to state.json
        if let Ok(val) = serde_json::from_slice::<serde_json::Value>(&body) {
            let mut persistent = state::load_state();
            if let Some(sc) = val.get("scene_id").and_then(|v| v.as_str()) {
                persistent.last_scene_id = Some(sc.to_string());
            }
            if let Some(idx) = val.get("sample_index").and_then(|v| v.as_i64()) {
                persistent.last_sample_index = Some(idx);
            }
            if let Some(ts) = val.get("timestamp_us").and_then(|v| v.as_i64()) {
                persistent.last_timestamp_us = Some(ts);
            } else if let Some(ts) = val.get("timestamp_ns").and_then(|v| v.as_i64()) {
                persistent.last_timestamp_us = Some(ts / 1000);
            }
            if let Some(vp) = val.get("viewpoint").and_then(|v| v.as_str()) {
                persistent.last_viewpoint = Some(vp.to_string());
            }
            if let Some(looping) = val.get("loop").and_then(|v| v.as_bool()) {
                persistent.last_loop = Some(looping);
            }
            if let Some(th) = val.get("theme").and_then(|v| v.as_str()) {
                persistent.last_theme = Some(th.to_string());
            }
            if let Some(reopen) = val.get("reopen_last_session").and_then(|v| v.as_bool()) {
                persistent.reopen_last_session = reopen;
            }
            if let Some(vis) = val.get("agent_visibility") {
                if let Ok(map) = serde_json::from_value::<HashMap<String, bool>>(vis.clone()) {
                    persistent.agent_visibility = Some(map);
                }
            }
            if let Some(layers) = val.get("layer_toggles") {
                if let Ok(lt) = serde_json::from_value::<LayerToggles>(layers.clone()) {
                    persistent.layer_toggles = Some(lt);
                }
            }
            if let Some(cam) = val.get("selected_camera").and_then(|v| v.as_str()) {
                persistent.selected_camera = Some(cam.to_string());
            }
            if let Some(split) = val.get("split_view").and_then(|v| v.as_bool()) {
                persistent.split_view = Some(split);
            }
            if let Some(panels) = val.get("panel_collapse") {
                if let Ok(pcs) = serde_json::from_value::<PanelCollapseState>(panels.clone()) {
                    persistent.panel_collapse = Some(pcs);
                }
            }
            if let Some(win) = val.get("window_state") {
                if let Ok(ws) = serde_json::from_value::<WindowState>(win.clone()) {
                    persistent.window_state = Some(ws);
                }
            }

            let _ = state::save_state(&persistent);
        }

        write_json_response(writer, 200, "OK", cors_origin.as_deref(), b"{\"status\":\"ok\"}")?;
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
        let config = make_test_config();
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

        let loaded = state::load_state();
        assert_eq!(loaded.last_scene_id, Some("synth_001".into()));
        assert_eq!(loaded.last_sample_index, Some(3));
        assert_eq!(loaded.last_timestamp_us, Some(300000));
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

        config.eval_dispatcher = Some(Arc::new(move |script: String| {
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

        let mut config = make_test_config();
        let port_holder = Arc::new(Mutex::new(0u16));
        let port_holder_clone = port_holder.clone();

        config.eval_dispatcher = Some(Arc::new(move |script: String| {
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
        config.eval_dispatcher = Some(Arc::new(move |_script: String| {
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
}
