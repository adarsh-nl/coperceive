// SPDX-License-Identifier: Apache-2.0

#[derive(Debug, PartialEq, Eq, Clone)]
pub enum SshErrorKind {
    /// Host unreachable or timed out (eduVPN down, DNS failure, no route).
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
            Self::EduVpnOrNetworkUnreachable(msg) => {
                write!(f, "Cannot reach HPC host ({}). Is eduVPN connected?", msg)
            }
            Self::PublicKeyAuthRequired(msg) => {
                write!(
                    f,
                    "SSH authentication failed ({}). Please ensure your key is loaded into ssh-agent (`ssh-add`).",
                    msg
                )
            }
            Self::HostKeyMismatch(msg) => write!(f, "SSH host key mismatch: {}", msg),
            Self::Other(msg) => write!(f, "SSH error: {}", msg),
        }
    }
}

impl std::error::Error for SshErrorKind {}

/// Standard SSH arguments enforcing ControlMaster, BatchMode, and 8-second connect timeout.
pub fn standard_ssh_args(alias: &str) -> Vec<String> {
    vec![
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
        alias.to_string(),
    ]
}

/// Builds the argument list for executing a remote command over SSH.
pub fn build_ssh_command(alias: &str, remote_cmd: &str) -> Vec<String> {
    let mut args = standard_ssh_args(alias);
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
    let mut args = standard_ssh_args(alias);
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
    {
        SshErrorKind::PublicKeyAuthRequired(stderr.trim().to_string())
    } else if lower.contains("no route to host")
        || lower.contains("could not resolve hostname")
        || lower.contains("connection timed out")
        || lower.contains("network is unreachable")
        || lower.contains("operation timed out")
        || lower.contains("connection refused")
    {
        SshErrorKind::EduVpnOrNetworkUnreachable(stderr.trim().to_string())
    } else if lower.contains("host key verification failed") || lower.contains("remote host identification has changed") {
        SshErrorKind::HostKeyMismatch(stderr.trim().to_string())
    } else {
        SshErrorKind::Other(stderr.trim().to_string())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_classify_public_key_denied() {
        let err = classify_ssh_error("nanjaiyalathaa@hpc-head1: Permission denied (publickey,gssapi-keyex,gssapi-with-mic).");
        match &err {
            SshErrorKind::PublicKeyAuthRequired(msg) => {
                assert!(msg.contains("Permission denied"));
                let formatted = format!("{}", err);
                assert!(formatted.contains("ssh-add"));
            }
            other => panic!("expected PublicKeyAuthRequired, got {:?}", other),
        }
    }

    #[test]
    fn test_classify_eduvpn_unreachable() {
        let cases = [
            "ssh: connect to host hpc-head1.ewi.utwente.nl port 22: No route to host",
            "ssh: Could not resolve hostname hpc-head1: Name or service not known",
            "ssh: connect to host hpc-head1.ewi.utwente.nl port 22: Connection timed out",
            "ssh: connect to host 130.89.6.254 port 22: Network is unreachable",
        ];
        for case in cases {
            let err = classify_ssh_error(case);
            match err {
                SshErrorKind::EduVpnOrNetworkUnreachable(_) => {
                    let formatted = format!("{}", err);
                    assert!(formatted.contains("Is eduVPN connected?"));
                }
                other => panic!("expected EduVpnOrNetworkUnreachable for {:?}, got {:?}", case, other),
            }
        }
    }

    #[test]
    fn test_build_ssh_command_flags() {
        let args = build_ssh_command("hpc-head1", "coperceive --version");
        assert!(args.contains(&"BatchMode=yes".to_string()));
        assert!(args.contains(&"ConnectTimeout=8".to_string()));
        assert_eq!(args.last(), Some(&"coperceive --version".to_string()));
    }

    #[test]
    fn test_build_ssh_tunnel_command_flags() {
        let args = build_ssh_tunnel_command("hpc-head1", 49152, "10.20.240.48", 8731);
        assert!(args.contains(&"-N".to_string()));
        assert!(args.contains(&"-L".to_string()));
        assert!(args.contains(&"127.0.0.1:49152:10.20.240.48:8731".to_string()));
    }
}
