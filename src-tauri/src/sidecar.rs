// SPDX-License-Identifier: Apache-2.0

use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicI32, Ordering};
use std::time::{Duration, Instant};

use crate::port::find_free_loopback_port;
use crate::token::{generate_secure_token, write_token_file};

pub static ACTIVE_SIDECAR_PID: AtomicI32 = AtomicI32::new(0);

#[cfg(unix)]
extern "C" fn sidecar_exit_handler() {
    let pid = ACTIVE_SIDECAR_PID.swap(0, Ordering::SeqCst);
    if pid > 0 {
        unsafe {
            libc::kill(pid, libc::SIGTERM);
            std::thread::sleep(Duration::from_millis(50));
            libc::kill(pid, libc::SIGKILL);
        }
    }
}

#[cfg(unix)]
extern "C" fn sidecar_signal_handler(_sig: libc::c_int) {
    sidecar_exit_handler();
    unsafe {
        libc::_exit(0);
    }
}

pub fn register_process_exit_cleaner() {
    #[cfg(unix)]
    {
        static ONCE: std::sync::Once = std::sync::Once::new();
        ONCE.call_once(|| unsafe {
            libc::atexit(sidecar_exit_handler);
            libc::signal(libc::SIGTERM, sidecar_signal_handler as *const () as usize);
            libc::signal(libc::SIGINT, sidecar_signal_handler as *const () as usize);
        });
    }
}

#[derive(Debug)]
pub struct SidecarSession {
    child: Child,
    pub port: u16,
    pub token: String,
    pub token_path: PathBuf,
    pub log_path: PathBuf,
    pub root: PathBuf,
    pub preserve_log: bool,
}

impl SidecarSession {
    /// Launches a local `coperceive serve` sidecar process.
    /// Allocates an ephemeral loopback port and a mode 0600 token file.
    pub fn spawn(
        python_bin: &Path,
        data_root: &Path,
        allow_origin: Option<&str>,
    ) -> io::Result<Self> {
        let port = find_free_loopback_port()?;
        let token = generate_secure_token()?;

        let token_dir = std::env::temp_dir().join(format!("coperceive-local-{}", std::process::id()));
        fs::create_dir_all(&token_dir)?;
        let token_path = token_dir.join(format!("token-{}.txt", port));
        write_token_file(&token_path, &token)?;

        let log_path = token_dir.join(format!("serve-{}.log", port));
        let log_file = fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&log_path)?;
        let err_file = log_file.try_clone()?;

        let mut cmd = Command::new(python_bin);
        cmd.arg("-m")
            .arg("coperceive")
            .arg("serve")
            .arg("--host")
            .arg("127.0.0.1")
            .arg("--port")
            .arg(port.to_string())
            .arg("--token-file")
            .arg(&token_path)
            .arg("--root")
            .arg(data_root);

        if let Some(origin) = allow_origin {
            cmd.arg("--allow-origin").arg(origin);
        }

        let child = cmd
            .stdout(Stdio::from(log_file))
            .stderr(Stdio::from(err_file))
            .spawn()?;

        register_process_exit_cleaner();
        let pid = child.id() as i32;
        ACTIVE_SIDECAR_PID.store(pid, Ordering::SeqCst);

        Ok(Self {
            child,
            port,
            token,
            token_path,
            log_path,
            root: data_root.to_path_buf(),
            preserve_log: false,
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
        if ACTIVE_SIDECAR_PID.load(Ordering::SeqCst) == pid {
            ACTIVE_SIDECAR_PID.store(0, Ordering::SeqCst);
        }
        // Send SIGTERM on Unix, or kill on Windows
        #[cfg(unix)]
        unsafe {
            libc::kill(pid, libc::SIGTERM);
        }
        #[cfg(not(unix))]
        {
            let _ = self.child.kill();
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

        // Clean up token file
        let _ = fs::remove_file(&self.token_path);
        // Only remove log file if preserve_log is false (kept on failed startup)
        if !self.preserve_log {
            let _ = fs::remove_file(&self.log_path);
            if let Some(parent) = self.token_path.parent() {
                let _ = fs::remove_dir(parent);
            }
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

    #[cfg(unix)]
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
