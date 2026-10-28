// SPDX-License-Identifier: Apache-2.0
//! Remote HPC and SSH session manager for Coperceive Desktop.

use std::io::Read;
use std::process::{Child, Command, Stdio};
use std::time::Duration;

use serde::{Deserialize, Serialize};

use crate::port::find_free_loopback_port;
use crate::ssh::{build_ssh_command, build_ssh_tunnel_command, classify_ssh_error, SshErrorKind};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RemoteStartResponse {
    pub status: String,
    #[serde(default)]
    pub uuid: Option<String>,
    #[serde(default)]
    pub job_id: Option<u64>,
    #[serde(default)]
    pub node: Option<String>,
    #[serde(default)]
    pub ip: Option<String>,
    #[serde(default)]
    pub port: Option<u16>,
    #[serde(default)]
    pub token: Option<String>,
    #[serde(default)]
    pub reason: Option<String>,
    #[serde(default)]
    pub error: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RemoteStatusResponse {
    pub status: String,
    #[serde(default)]
    pub state: Option<String>,
    #[serde(default)]
    pub uuid: Option<String>,
    #[serde(default)]
    pub job_id: Option<u64>,
    #[serde(default)]
    pub node: Option<String>,
    #[serde(default)]
    pub ip: Option<String>,
    #[serde(default)]
    pub port: Option<u16>,
    #[serde(default)]
    pub elapsed: Option<String>,
    #[serde(default)]
    pub time_left: Option<String>,
    #[serde(default)]
    pub time_left_seconds: Option<u64>,
    #[serde(default)]
    pub healthy: Option<bool>,
    #[serde(default)]
    pub reason: Option<String>,
    #[serde(default)]
    pub warning: Option<String>,
    #[serde(default)]
    pub stopped_reason: Option<String>,
    #[serde(default)]
    pub last_heartbeat_age_s: Option<f64>,
    #[serde(default)]
    pub error: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RemoteStopResponse {
    pub status: String,
    #[serde(default)]
    pub uuid: Option<String>,
    #[serde(default)]
    pub job_id: Option<u64>,
    #[serde(default)]
    pub cancelled: Option<bool>,
    #[serde(default)]
    pub error: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RemoteHeartbeatResponse {
    pub status: String,
    #[serde(default)]
    pub uuid: Option<String>,
    #[serde(default)]
    pub timestamp: Option<f64>,
    #[serde(default)]
    pub error: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RemoteSessionInfo {
    pub uuid: String,
    #[serde(default)]
    pub job_id: Option<u64>,
    #[serde(default)]
    pub job_name: Option<String>,
    #[serde(default)]
    pub state: Option<String>,
    #[serde(default)]
    pub node: Option<String>,
    #[serde(default)]
    pub ip: Option<String>,
    #[serde(default)]
    pub port: Option<u16>,
    #[serde(default)]
    pub elapsed: Option<String>,
    #[serde(default)]
    pub time_left: Option<String>,
    #[serde(default)]
    pub time_left_seconds: Option<u64>,
    #[serde(default)]
    pub data_root: Option<String>,
    #[serde(default)]
    pub last_heartbeat_age_s: Option<f64>,
    #[serde(default)]
    pub is_orphan: Option<bool>,
    #[serde(default)]
    pub orphan_reason: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RemoteListResponse {
    #[serde(default)]
    pub sessions: Vec<RemoteSessionInfo>,
}

#[derive(Debug, Clone)]
pub struct RemoteClient {
    pub alias: String,
    pub ssh_bin: String,
}

impl RemoteClient {
    pub fn new(alias: &str) -> Self {
        Self {
            alias: alias.to_string(),
            ssh_bin: "ssh".to_string(),
        }
    }

    pub fn with_ssh_bin(alias: &str, ssh_bin: &str) -> Self {
        Self {
            alias: alias.to_string(),
            ssh_bin: ssh_bin.to_string(),
        }
    }

    fn run_remote_cmd(&self, args: &[&str]) -> Result<String, SshErrorKind> {
        let remote_cmd = format!("coperceive remote {}", args.join(" "));
        let ssh_args = build_ssh_command(&self.alias, &remote_cmd);

        let output = Command::new(&self.ssh_bin)
            .args(&ssh_args)
            .output()
            .map_err(|e| SshErrorKind::Other(format!("Failed to execute ssh: {}", e)))?;

        let stderr = String::from_utf8_lossy(&output.stderr).to_string();
        if !output.status.success() {
            return Err(classify_ssh_error(&stderr));
        }

        let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
        Ok(stdout)
    }

    pub fn start(
        &self,
        data_root: &str,
        partition: Option<&str>,
        walltime: Option<&str>,
        uuid: Option<&str>,
    ) -> Result<RemoteStartResponse, SshErrorKind> {
        let mut args = vec!["start", "--root", data_root];
        if let Some(p) = partition {
            args.extend(["--partition", p]);
        }
        if let Some(w) = walltime {
            args.extend(["--time", w]);
        }
        if let Some(u) = uuid {
            args.extend(["--uuid", u]);
        }

        let stdout = self.run_remote_cmd(&args)?;
        let parsed: RemoteStartResponse = serde_json::from_str(&stdout).map_err(|e| {
            SshErrorKind::Other(format!("Invalid start JSON: {} (output: {})", e, stdout))
        })?;
        Ok(parsed)
    }

    pub fn status(
        &self,
        uuid: Option<&str>,
        job_id: Option<u64>,
    ) -> Result<RemoteStatusResponse, SshErrorKind> {
        let mut args = vec!["status"];
        let job_id_str;
        if let Some(u) = uuid {
            args.extend(["--uuid", u]);
        }
        if let Some(j) = job_id {
            job_id_str = j.to_string();
            args.extend(["--job-id", &job_id_str]);
        }

        let stdout = self.run_remote_cmd(&args)?;
        let parsed: RemoteStatusResponse = serde_json::from_str(&stdout).map_err(|e| {
            SshErrorKind::Other(format!("Invalid status JSON: {} (output: {})", e, stdout))
        })?;
        Ok(parsed)
    }

    pub fn stop(
        &self,
        uuid: Option<&str>,
        job_id: Option<u64>,
    ) -> Result<RemoteStopResponse, SshErrorKind> {
        let mut args = vec!["stop"];
        let job_id_str;
        if let Some(u) = uuid {
            args.extend(["--uuid", u]);
        }
        if let Some(j) = job_id {
            job_id_str = j.to_string();
            args.extend(["--job-id", &job_id_str]);
        }

        let stdout = self.run_remote_cmd(&args)?;
        let parsed: RemoteStopResponse = serde_json::from_str(&stdout).map_err(|e| {
            SshErrorKind::Other(format!("Invalid stop JSON: {} (output: {})", e, stdout))
        })?;
        Ok(parsed)
    }

    pub fn heartbeat(&self, uuid: &str) -> Result<RemoteHeartbeatResponse, SshErrorKind> {
        let args = vec!["heartbeat", "--uuid", uuid];
        let stdout = self.run_remote_cmd(&args)?;
        let parsed: RemoteHeartbeatResponse = serde_json::from_str(&stdout).map_err(|e| {
            SshErrorKind::Other(format!("Invalid heartbeat JSON: {} (output: {})", e, stdout))
        })?;
        Ok(parsed)
    }

    pub fn list(&self) -> Result<RemoteListResponse, SshErrorKind> {
        let args = vec!["list"];
        let stdout = self.run_remote_cmd(&args)?;
        let parsed: RemoteListResponse = serde_json::from_str(&stdout).map_err(|e| {
            SshErrorKind::Other(format!("Invalid list JSON: {} (output: {})", e, stdout))
        })?;
        Ok(parsed)
    }
}

pub struct SshTunnel {
    pub child: Option<Child>,
    pub local_port: u16,
    pub remote_ip: String,
    pub remote_port: u16,
    pub alias: String,
}

impl SshTunnel {
    pub fn open(
        alias: &str,
        local_port: u16,
        remote_ip: &str,
        remote_port: u16,
        ssh_bin: Option<&str>,
    ) -> Result<Self, SshErrorKind> {
        let bin = ssh_bin.unwrap_or("ssh");
        let args = build_ssh_tunnel_command(alias, local_port, remote_ip, remote_port);

        let mut child = Command::new(bin)
            .args(&args)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|e| SshErrorKind::Other(format!("Failed to spawn ssh tunnel: {}", e)))?;

        // Give ssh tunnel a brief moment to detect immediate handshake failures
        std::thread::sleep(Duration::from_millis(150));
        if let Ok(Some(_status)) = child.try_wait() {
            let mut stderr = String::new();
            if let Some(mut err_pipe) = child.stderr.take() {
                let _ = err_pipe.read_to_string(&mut stderr);
            }
            return Err(classify_ssh_error(&stderr));
        }

        Ok(Self {
            child: Some(child),
            local_port,
            remote_ip: remote_ip.to_string(),
            remote_port,
            alias: alias.to_string(),
        })
    }

    pub fn is_alive(&mut self) -> bool {
        if let Some(child) = &mut self.child {
            matches!(child.try_wait(), Ok(None))
        } else {
            false
        }
    }

    pub fn close(&mut self) -> Result<(), std::io::Error> {
        if let Some(mut child) = self.child.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
        Ok(())
    }
}

impl Drop for SshTunnel {
    fn drop(&mut self) {
        let _ = self.close();
    }
}

pub struct RemoteSessionManager {
    pub client: RemoteClient,
    pub tunnel: Option<SshTunnel>,
    pub uuid: String,
    pub local_port: u16,
    pub remote_ip: String,
    pub remote_port: u16,
    pub token: String,
    pub data_root: String,
}

impl RemoteSessionManager {
    pub fn connect(
        alias: &str,
        data_root: &str,
        existing_uuid: Option<&str>,
        ssh_bin: Option<&str>,
    ) -> Result<Self, SshErrorKind> {
        let client = match ssh_bin {
            Some(bin) => RemoteClient::with_ssh_bin(alias, bin),
            None => RemoteClient::new(alias),
        };

        let local_port = find_free_loopback_port()
            .map_err(|e| SshErrorKind::Other(format!("Failed to find free local port: {}", e)))?;

        // Reconnect / reattach or start fresh
        let (uuid, remote_ip, remote_port, token) = if let Some(u) = existing_uuid {
            let st = client.status(Some(u), None)?;
            if st.status != "running" {
                return Err(SshErrorKind::Other(format!(
                    "Cannot reattach: session {} is not running (state: {:?})",
                    u, st.state
                )));
            }
            let ip = st.ip.ok_or_else(|| SshErrorKind::Other("No IP in status".to_string()))?;
            let port =
                st.port.ok_or_else(|| SshErrorKind::Other("No port in status".to_string()))?;
            (u.to_string(), ip, port, "".to_string())
        } else {
            let resp = client.start(data_root, None, None, None)?;
            if resp.status != "running" {
                return Err(SshErrorKind::Other(format!(
                    "Failed to start remote session: {:?} (error: {:?})",
                    resp.status, resp.error
                )));
            }
            let u = resp
                .uuid
                .ok_or_else(|| SshErrorKind::Other("No UUID in start response".to_string()))?;
            let ip = resp
                .ip
                .ok_or_else(|| SshErrorKind::Other("No IP in start response".to_string()))?;
            let port = resp
                .port
                .ok_or_else(|| SshErrorKind::Other("No port in start response".to_string()))?;
            let tok = resp.token.unwrap_or_default();
            (u, ip, port, tok)
        };

        let tunnel = SshTunnel::open(alias, local_port, &remote_ip, remote_port, ssh_bin)?;

        Ok(Self {
            client,
            tunnel: Some(tunnel),
            uuid,
            local_port,
            remote_ip,
            remote_port,
            token,
            data_root: data_root.to_string(),
        })
    }

    pub fn origin(&self) -> String {
        format!("http://127.0.0.1:{}", self.local_port)
    }

    pub fn disconnect(&mut self) -> Result<(), SshErrorKind> {
        if let Some(mut t) = self.tunnel.take() {
            let _ = t.close();
        }
        self.client.stop(Some(&self.uuid), None)?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::os::unix::fs::PermissionsExt;
    use std::path::PathBuf;

    struct FakeSshScript {
        path: PathBuf,
    }

    static SCRIPT_COUNTER: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(1);

    impl FakeSshScript {
        fn create() -> Self {
            let id = SCRIPT_COUNTER.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            let tmp = std::env::temp_dir().join(format!(
                "fake_ssh_{}_{}_{}.sh",
                std::process::id(),
                std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap_or_default()
                    .as_nanos(),
                id
            ));
            let script = r#"#!/usr/bin/env bash
args="$*"

# Error injection tests
if [[ "$args" == *"--fail-unreachable"* ]]; then
    echo "ssh: connect to host hpc-test port 22: No route to host" >&2
    exit 255
fi

if [[ "$args" == *"--fail-auth"* ]]; then
    echo "Permission denied (publickey)." >&2
    exit 255
fi

# Tunnel invocation
if [[ "$args" == *"-N -L"* ]]; then
    # Keep running until killed
    while true; do
        sleep 1
    done
    exit 0
fi

# Remote start
if [[ "$args" == *"coperceive remote start"* ]]; then
    echo '{"status": "running", "uuid": "test-uuid-99", "job_id": 5555, "node": "hpc-node11", "ip": "10.20.240.48", "port": 52657, "token": "tok12345"}'
    exit 0
fi

# Remote status
if [[ "$args" == *"coperceive remote status"* ]]; then
    if [[ "$args" == *"test-pending"* ]]; then
        echo '{"status": "pending", "state": "PENDING", "uuid": "test-pending", "job_id": 5556, "reason": "ReqNodeNotAvail"}'
    elif [[ "$args" == *"test-timeout"* ]]; then
        echo '{"status": "stopped", "state": "TIMEOUT", "stopped_reason": "timeout", "uuid": "test-timeout", "job_id": 5557, "last_heartbeat_age_s": 650.0}'
    else
        echo '{"status": "running", "state": "RUNNING", "uuid": "test-uuid-99", "job_id": 5555, "node": "hpc-node11", "ip": "10.20.240.48", "port": 52657, "healthy": true, "last_heartbeat_age_s": 12.5}'
    fi
    exit 0
fi

# Remote stop
if [[ "$args" == *"coperceive remote stop"* ]]; then
    echo '{"status": "stopped", "uuid": "test-uuid-99", "job_id": 5555, "cancelled": true}'
    exit 0
fi

# Remote heartbeat
if [[ "$args" == *"coperceive remote heartbeat"* ]]; then
    echo '{"status": "ok", "uuid": "test-uuid-99", "timestamp": 1792500000.0}'
    exit 0
fi

# Remote list
if [[ "$args" == *"coperceive remote list"* ]]; then
    echo '{"sessions": [{"uuid": "test-uuid-99", "job_id": 5555, "state": "RUNNING", "ip": "10.20.240.48", "port": 52657, "is_orphan": false}]}'
    exit 0
fi

echo "unknown command: $args" >&2
exit 1
"#;
            fs::write(&tmp, script).expect("write fake_ssh");
            let mut perms = fs::metadata(&tmp).expect("metadata").permissions();
            perms.set_mode(0o755);
            fs::set_permissions(&tmp, perms).expect("set_permissions");
            Self { path: tmp }
        }
    }

    impl Drop for FakeSshScript {
        fn drop(&mut self) {
            let _ = fs::remove_file(&self.path);
        }
    }

    #[test]
    fn test_remote_start_and_status_parsing() {
        let fake = FakeSshScript::create();
        let client = RemoteClient::with_ssh_bin("hpc-alias", fake.path.to_str().unwrap());

        let start_res = client.start("/path/to/scenes", None, None, None).expect("start");
        assert_eq!(start_res.status, "running");
        assert_eq!(start_res.uuid.as_deref(), Some("test-uuid-99"));
        assert_eq!(start_res.job_id, Some(5555));
        assert_eq!(start_res.ip.as_deref(), Some("10.20.240.48"));
        assert_eq!(start_res.port, Some(52657));
        assert_eq!(start_res.token.as_deref(), Some("tok12345"));

        // Running status
        let status_res = client.status(Some("test-uuid-99"), None).expect("status running");
        assert_eq!(status_res.status, "running");
        assert_eq!(status_res.state.as_deref(), Some("RUNNING"));
        assert_eq!(status_res.healthy, Some(true));
        assert_eq!(status_res.last_heartbeat_age_s, Some(12.5));

        // Pending status
        let pending_res = client.status(Some("test-pending"), None).expect("status pending");
        assert_eq!(pending_res.status, "pending");
        assert_eq!(pending_res.state.as_deref(), Some("PENDING"));
        assert_eq!(pending_res.reason.as_deref(), Some("ReqNodeNotAvail"));

        // Timeout status
        let timeout_res = client.status(Some("test-timeout"), None).expect("status timeout");
        assert_eq!(timeout_res.status, "stopped");
        assert_eq!(timeout_res.state.as_deref(), Some("TIMEOUT"));
        assert_eq!(timeout_res.stopped_reason.as_deref(), Some("timeout"));
    }

    #[test]
    fn test_remote_heartbeat_and_stop() {
        let fake = FakeSshScript::create();
        let client = RemoteClient::with_ssh_bin("hpc-alias", fake.path.to_str().unwrap());

        let hb = client.heartbeat("test-uuid-99").expect("heartbeat");
        assert_eq!(hb.status, "ok");
        assert_eq!(hb.uuid.as_deref(), Some("test-uuid-99"));
        assert_eq!(hb.timestamp, Some(1792500000.0));

        let stop = client.stop(Some("test-uuid-99"), None).expect("stop");
        assert_eq!(stop.status, "stopped");
        assert_eq!(stop.cancelled, Some(true));
    }

    #[test]
    fn test_remote_ssh_errors_unreachable_and_publickey() {
        let fake = FakeSshScript::create();

        // Unreachable host error
        let client_unreachable =
            RemoteClient::with_ssh_bin("--fail-unreachable", fake.path.to_str().unwrap());
        let err = client_unreachable.start("/root", None, None, None).unwrap_err();
        assert!(matches!(err, SshErrorKind::EduVpnOrNetworkUnreachable(_)));

        // Publickey denied error
        let client_auth = RemoteClient::with_ssh_bin("--fail-auth", fake.path.to_str().unwrap());
        let err_auth = client_auth.start("/root", None, None, None).unwrap_err();
        assert!(matches!(err_auth, SshErrorKind::PublicKeyAuthRequired(_)));
    }

    #[test]
    fn test_ssh_tunnel_lifecycle() {
        let fake = FakeSshScript::create();
        let mut tunnel = SshTunnel::open(
            "hpc-alias",
            19876,
            "10.20.240.48",
            52657,
            Some(fake.path.to_str().unwrap()),
        )
        .expect("open tunnel");

        assert!(tunnel.is_alive());
        tunnel.close().expect("close tunnel");
        assert!(!tunnel.is_alive());
    }

    #[test]
    fn test_remote_session_manager_connect_and_reattach() {
        let fake = FakeSshScript::create();

        // Fresh start
        let mut mgr = RemoteSessionManager::connect(
            "hpc-alias",
            "/data/scenes",
            None,
            Some(fake.path.to_str().unwrap()),
        )
        .expect("connect fresh");

        assert_eq!(mgr.uuid, "test-uuid-99");
        assert_eq!(mgr.remote_ip, "10.20.240.48");
        assert_eq!(mgr.remote_port, 52657);
        assert!(mgr.origin().starts_with("http://127.0.0.1:"));
        mgr.disconnect().expect("disconnect");

        // Reattach to existing session
        let mgr_reattach = RemoteSessionManager::connect(
            "hpc-alias",
            "/data/scenes",
            Some("test-uuid-99"),
            Some(fake.path.to_str().unwrap()),
        )
        .expect("reattach");

        assert_eq!(mgr_reattach.uuid, "test-uuid-99");
        assert_eq!(mgr_reattach.remote_ip, "10.20.240.48");
        assert_eq!(mgr_reattach.remote_port, 52657);
    }
}
