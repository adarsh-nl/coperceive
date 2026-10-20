// SPDX-License-Identifier: Apache-2.0

use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

use crate::port::find_free_loopback_port;
use crate::token::{generate_secure_token, write_token_file};

#[derive(Debug)]
pub struct SidecarSession {
    child: Child,
    pub port: u16,
    pub token: String,
    pub token_path: PathBuf,
    pub root: PathBuf,
}

impl SidecarSession {
    /// Launches a local `coperceive serve` sidecar process.
    /// Allocates an ephemeral loopback port and a mode 0600 token file.
    pub fn spawn(python_bin: &Path, data_root: &Path) -> io::Result<Self> {
        let port = find_free_loopback_port()?;
        let token = generate_secure_token()?;

        let token_dir = std::env::temp_dir().join(format!("coperceive-local-{}", std::process::id()));
        fs::create_dir_all(&token_dir)?;
        let token_path = token_dir.join(format!("token-{}.txt", port));
        write_token_file(&token_path, &token)?;

        let child = Command::new(python_bin)
            .arg("-m")
            .arg("coperceive.app.serve")
            .arg("--host")
            .arg("127.0.0.1")
            .arg("--port")
            .arg(port.to_string())
            .arg("--token-file")
            .arg(&token_path)
            .arg("--root")
            .arg(data_root)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()?;

        Ok(Self {
            child,
            port,
            token,
            token_path,
            root: data_root.to_path_buf(),
        })
    }

    /// Checks whether the child process is still running.
    pub fn is_alive(&mut self) -> bool {
        match self.child.try_wait() {
            Ok(None) => true,
            _ => false,
        }
    }

    /// Stops the sidecar process gracefully with SIGTERM, falling back to SIGKILL.
    pub fn stop(&mut self) -> io::Result<()> {
        let pid = self.child.id() as i32;
        // Send SIGTERM
        unsafe {
            libc::kill(pid, libc::SIGTERM);
        }

        let deadline = Instant::now() + Duration::from_millis(3000);
        loop {
            match self.child.try_wait()? {
                Some(_) => break,
                None => {
                    if Instant::now() > deadline {
                        let _ = self.child.kill();
                        let _ = self.child.wait();
                        break;
                    }
                    std::thread::sleep(Duration::from_millis(50));
                }
            }
        }

        // Clean up token file and directory
        let _ = fs::remove_file(&self.token_path);
        if let Some(parent) = self.token_path.parent() {
            let _ = fs::remove_dir(parent);
        }

        Ok(())
    }
}

impl Drop for SidecarSession {
    fn drop(&mut self) {
        let _ = self.stop();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_sidecar_lifecycle_mock() {
        // Test lifecycle with python -c 'import time; time.sleep(10)'
        let python = "python3";
        let dir = std::env::temp_dir().join(format!("test-sidecar-root-{}", std::process::id()));
        let _ = fs::create_dir_all(&dir);

        let mut child = Command::new(python)
            .arg("-c")
            .arg("import time; time.sleep(10)")
            .spawn()
            .expect("spawn mock");

        let pid = child.id() as i32;
        assert_eq!(child.try_wait().unwrap(), None, "child should be running");

        // Send SIGTERM
        unsafe {
            libc::kill(pid, libc::SIGTERM);
        }
        std::thread::sleep(Duration::from_millis(100));
        assert!(child.try_wait().unwrap().is_some(), "child should terminate on SIGTERM");

        let _ = fs::remove_dir_all(&dir);
    }
}
