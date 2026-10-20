// SPDX-License-Identifier: Apache-2.0

use std::net::TcpListener;

/// Finds an available loopback port by binding to 127.0.0.1:0 and immediately releasing it.
pub fn find_free_loopback_port() -> std::io::Result<u16> {
    let listener = TcpListener::bind("127.0.0.1:0")?;
    let port = listener.local_addr()?.port();
    drop(listener);
    Ok(port)
}

/// Checks whether a given TCP port on loopback is currently available.
pub fn is_port_available(port: u16) -> bool {
    TcpListener::bind(("127.0.0.1", port)).is_ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_find_free_loopback_port() {
        let port = find_free_loopback_port().expect("failed to find free port");
        assert!(port > 1024, "port should be non-privileged (>1024), got {}", port);
        assert!(is_port_available(port));
    }

    #[test]
    fn test_is_port_available_when_bound() {
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind listener");
        let port = listener.local_addr().unwrap().port();
        assert!(!is_port_available(port), "bound port should not report as available");
        drop(listener);
        assert!(is_port_available(port), "released port should report as available");
    }
}
