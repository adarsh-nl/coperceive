// SPDX-License-Identifier: Apache-2.0
//! SSH helpers: argument construction, POSIX quoting, bounded local execution.
//!
//! All remote commands are built as argv lists locally; the unavoidable remote
//! command line is a string that SSH hands to the remote login shell, so every
//! user-provided value in it is quoted with [`shell_quote`].

use std::io::Write;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

#[derive(Debug, PartialEq, Eq, Clone)]
pub enum SshErrorKind {
    /// Host unreachable or timed out (VPN down, DNS failure, no route).
    EduVpnOrNetworkUnreachable(String),
    /// Public key auth failed or passphrase required under BatchMode.
    PublicKeyAuthRequired(String),
    /// Host key verification failed.
    HostKeyMismatch(String),
    /// Generic or unrecognized SSH failure.
    Other(String),
}

impl std::fmt::Display for SshErrorKind {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::EduVpnOrNetworkUnreachable(msg) => write!(
                f,
                "We could not reach the cluster. Check that you are online and connected to the \
                 campus VPN (for example eduVPN), then try again. Details: {msg}"
            ),
            Self::PublicKeyAuthRequired(msg) => write!(
                f,
                "The cluster did not accept your SSH key. Open Terminal and run `ssh-add` to load \
                 your key, and check that ~/.ssh/config points at the right key for this host, then \
                 try again. Details: {msg}"
            ),
            Self::HostKeyMismatch(msg) => write!(
                f,
                "The cluster reported a different identity than last time. If you did not expect \
                 this, stop and ask your cluster administrators before continuing. Details: {msg}"
            ),
            Self::Other(msg) => write!(
                f,
                "Something went wrong while talking to the cluster. Try again, and if it keeps \
                 failing check that SSH works in Terminal. Details: {msg}"
            ),
        }
    }
}

impl std::error::Error for SshErrorKind {}

/// One ssh destination. An alias lets the user's `~/.ssh/config` own host, user,
/// port and jump hosts; explicit fields are only fallbacks.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct SshTarget {
    pub destination: String,
    pub port: Option<u16>,
    pub proxy_jump: Option<String>,
}

impl SshTarget {
    pub fn alias(alias: &str) -> Self {
        Self {
            destination: alias.to_string(),
            port: None,
            proxy_jump: None,
        }
    }
}

/// Quotes one argument for a POSIX shell. Single quotes are literal in every
/// POSIX shell; the `'` itself becomes `'\''`.
pub fn shell_quote(arg: &str) -> String {
    if arg.is_empty() {
        return "''".to_string();
    }
    if arg
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || "_-./:@%+=,".contains(c))
    {
        return arg.to_string();
    }
    format!("'{}'", arg.replace('\'', "'\\''"))
}

/// Joins argv into one remote shell line with every argument quoted.
pub fn quote_remote_argv(argv: &[String]) -> String {
    argv.iter()
        .map(|arg| shell_quote(arg))
        .collect::<Vec<_>>()
        .join(" ")
}

/// Ensures `~/.coperceive` exists so ssh ControlMaster can create its socket.
fn ensure_control_dir() {
    if let Some(home) = std::env::var_os("HOME") {
        let dir = PathBuf::from(home).join(".coperceive");
        let _ = std::fs::create_dir_all(&dir);
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let _ = std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o700));
        }
    }
}

/// Standard SSH arguments enforcing BatchMode, ControlMaster and an 8 s connect
/// timeout. BatchMode makes 2FA/passphrase prompts fail fast instead of hanging.
pub fn standard_ssh_args(target: &SshTarget) -> Vec<String> {
    let mut args = vec![
        "-o".to_string(),
        "BatchMode=yes".to_string(),
        "-o".to_string(),
        "ControlMaster=auto".to_string(),
        "-o".to_string(),
        "ControlPath=~/.coperceive/ssh-%C".to_string(),
        "-o".to_string(),
        "ControlPersist=10m".to_string(),
        "-o".to_string(),
        "ConnectTimeout=8".to_string(),
    ];
    if let Some(port) = target.port {
        args.push("-p".to_string());
        args.push(port.to_string());
    }
    if let Some(jump) = &target.proxy_jump {
        if !jump.trim().is_empty() && jump != "none" {
            args.push("-J".to_string());
            args.push(jump.clone());
        }
    }
    args.push(target.destination.clone());
    args
}

/// Builds the argument list for executing a remote command over SSH.
pub fn build_ssh_command(alias: &str, remote_cmd: &str) -> Vec<String> {
    build_ssh_command_for(&SshTarget::alias(alias), remote_cmd)
}

pub fn build_ssh_command_for(target: &SshTarget, remote_cmd: &str) -> Vec<String> {
    let mut args = standard_ssh_args(target);
    args.push(remote_cmd.to_string());
    args
}

/// Builds the argument list for establishing an SSH local port-forwarding tunnel.
/// Uses `-N` to forward ports without allocating a remote pseudo-terminal or running a shell.
pub fn build_ssh_tunnel_command(
    alias: &str,
    local_port: u16,
    remote_host: &str,
    remote_port: u16,
) -> Vec<String> {
    let mut args = standard_ssh_args(&SshTarget::alias(alias));
    args.push("-N".to_string());
    args.push("-L".to_string());
    args.push(format!("127.0.0.1:{}:{}:{}", local_port, remote_host, remote_port));
    args
}

/// Classifies stderr output from an SSH process exit.
pub fn classify_ssh_error(stderr: &str) -> SshErrorKind {
    let lower = stderr.to_lowercase();
    if lower.contains("permission denied (publickey")
        || lower.contains("permission denied")
        || lower.contains("passphrase")
        || lower.contains("sign_and_send_pubkey: signing failed")
        || lower.contains("no such identity")
    {
        SshErrorKind::PublicKeyAuthRequired(stderr.trim().to_string())
    } else if lower.contains("no route to host")
        || lower.contains("could not resolve hostname")
        || lower.contains("connection timed out")
        || lower.contains("network is unreachable")
        || lower.contains("operation timed out")
        || lower.contains("connection refused")
        || lower.contains("connection reset")
        || lower.contains("host is down")
    {
        SshErrorKind::EduVpnOrNetworkUnreachable(stderr.trim().to_string())
    } else if lower.contains("host key verification failed")
        || lower.contains("remote host identification has changed")
    {
        SshErrorKind::HostKeyMismatch(stderr.trim().to_string())
    } else {
        SshErrorKind::Other(stderr.trim().to_string())
    }
}

/// Result of a bounded local ssh invocation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommandResult {
    pub success: bool,
    pub stdout: String,
    pub stderr: String,
}

/// Runs `ssh <target> <remote_cmd>` with a hard local timeout.
///
/// The timeout bounds the whole login-node interaction, so a slow or hanging
/// cluster cannot stall the app. The child is killed on expiry.
pub fn run_ssh_command(
    ssh_bin: &str,
    target: &SshTarget,
    remote_cmd: &str,
    timeout: Duration,
    stdin: Option<&str>,
) -> std::io::Result<CommandResult> {
    run_local_command(
        ssh_bin,
        &build_ssh_command_for(target, remote_cmd),
        timeout,
        stdin,
    )
}

/// Runs `ssh -G <alias>` to read effective host settings locally. This never
/// contacts the cluster and never prints key material.
pub fn run_ssh_g(
    ssh_bin: &str,
    alias: &str,
    timeout: Duration,
) -> std::io::Result<CommandResult> {
    run_local_command(
        ssh_bin,
        &["-G".to_string(), alias.to_string()],
        timeout,
        None,
    )
}

/// Runs a local command with a hard timeout and optional stdin.
pub fn run_local_command(
    bin: &str,
    args: &[String],
    timeout: Duration,
    stdin: Option<&str>,
) -> std::io::Result<CommandResult> {
    ensure_control_dir();
    let mut child = Command::new(bin)
        .args(args)
        .stdin(if stdin.is_some() {
            Stdio::piped()
        } else {
            Stdio::null()
        })
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;

    if let Some(input) = stdin {
        if let Some(mut pipe) = child.stdin.take() {
            pipe.write_all(input.as_bytes())?;
            // Dropping the pipe sends EOF, which sbatch needs to run.
        }
    }

    let deadline = Instant::now() + timeout;
    loop {
        match child.try_wait()? {
            Some(_status) => break,
            None => {
                if Instant::now() >= deadline {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Err(std::io::Error::new(
                        std::io::ErrorKind::TimedOut,
                        format!(
                            "The cluster did not answer within {} seconds.",
                            timeout.as_secs()
                        ),
                    ));
                }
                std::thread::sleep(Duration::from_millis(25));
            }
        }
    }

    let output = child.wait_with_output()?;
    Ok(CommandResult {
        success: output.status.success(),
        stdout: String::from_utf8_lossy(&output.stdout).to_string(),
        stderr: String::from_utf8_lossy(&output.stderr).to_string(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_classify_public_key_denied() {
        let err = classify_ssh_error(
            "alice@hpc-head1: Permission denied (publickey,gssapi-keyex,gssapi-with-mic).",
        );
        match &err {
            SshErrorKind::PublicKeyAuthRequired(msg) => {
                assert!(msg.contains("Permission denied"));
                let formatted = format!("{}", err);
                assert!(formatted.contains("ssh-add"));

                assert!(formatted.contains("did not accept your SSH key"));
            }
            other => panic!("expected PublicKeyAuthRequired, got {:?}", other),
        }
    }

    #[test]
    fn test_classify_eduvpn_unreachable() {
        let cases = [
            "ssh: connect to host cluster.example.org port 22: No route to host",
            "ssh: Could not resolve hostname cluster-a: Name or service not known",
            "ssh: connect to host cluster.example.org port 22: Connection timed out",
            "ssh: connect to host 192.0.2.10 port 22: Network is unreachable",
        ];
        for case in cases {
            let err = classify_ssh_error(case);
            match err {
                SshErrorKind::EduVpnOrNetworkUnreachable(_) => {
                    let formatted = format!("{}", err);
                    assert!(
                        formatted.contains("VPN"),
                        "plain-language hint must mention the VPN: {formatted}"
                    );
                }
                other => panic!(
                    "expected EduVpnOrNetworkUnreachable for {:?}, got {:?}",
                    case, other
                ),
            }
        }
    }

    #[test]
    fn test_build_ssh_command_flags() {
        let args = build_ssh_command("cluster-a", "coperceive --version");
        assert!(args.contains(&"BatchMode=yes".to_string()));
        assert!(args.contains(&"ConnectTimeout=8".to_string()));
        assert_eq!(args.last(), Some(&"coperceive --version".to_string()));
    }

    #[test]
    fn test_build_ssh_command_for_typed_host_with_port_and_jump() {
        let target = SshTarget {
            destination: "alice@cluster.example.org".to_string(),
            port: Some(2222),
            proxy_jump: Some("jump.example.org".to_string()),
        };
        let args = build_ssh_command_for(&target, "true");
        assert!(args.contains(&"-p".to_string()));
        assert!(args.contains(&"2222".to_string()));
        assert!(args.contains(&"-J".to_string()));
        assert!(args.contains(&"jump.example.org".to_string()));
        assert_eq!(args.last(), Some(&"true".to_string()));
    }

    #[test]
    fn test_build_ssh_tunnel_command_flags() {
        let args = build_ssh_tunnel_command("cluster-a", 49152, "10.20.240.48", 8731);
        assert!(args.contains(&"-N".to_string()));
        assert!(args.contains(&"-L".to_string()));
        assert!(args.contains(&"127.0.0.1:49152:10.20.240.48:8731".to_string()));
    }

    #[test]
    fn test_shell_quote_handles_spaces_quotes_and_semicolons() {
        assert_eq!(shell_quote("plain"), "plain");
        assert_eq!(shell_quote("/tmp/a b"), "'/tmp/a b'");
        assert_eq!(shell_quote("it's"), "'it'\\''s'");
        assert_eq!(shell_quote("x; touch /tmp/x"), "'x; touch /tmp/x'");
        assert_eq!(
            shell_quote("/data/weird name/\"quoted\"; touch /tmp/x"),
            "'/data/weird name/\"quoted\"; touch /tmp/x'"
        );
    }

    #[test]
    fn test_quote_remote_argv_keeps_each_argument_whole() {
        let argv = vec![
            "coperceive".to_string(),
            "remote".to_string(),
            "start".to_string(),
            "--root".to_string(),
            "/data/my scenes; touch /tmp/x".to_string(),
        ];
        let line = quote_remote_argv(&argv);
        assert_eq!(
            line,
            "coperceive remote start --root '/data/my scenes; touch /tmp/x'"
        );
    }

    #[test]
    fn test_run_local_command_returns_timed_out_error() {
        let dir = std::env::temp_dir().join(format!("ssh-timeout-{}", std::process::id()));
        std::fs::create_dir_all(&dir).expect("temp dir");
        let script = dir.join("slow.sh");
        std::fs::write(&script, "#!/bin/bash\nsleep 30\n").expect("write");
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&script, std::fs::Permissions::from_mode(0o755))
                .expect("chmod");
        }
        let err = run_local_command(
            script.to_str().unwrap(),
            &[],
            Duration::from_millis(150),
            None,
        )
        .expect_err("must time out");
        assert_eq!(err.kind(), std::io::ErrorKind::TimedOut);
        let _ = std::fs::remove_dir_all(&dir);
    }
}
