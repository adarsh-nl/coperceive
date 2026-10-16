//! Tauri shell. The page stays an HTTP client of `coperceive serve`.

use std::io::{Read, Write};
use std::net::TcpListener;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::sync::atomic::{AtomicI32, Ordering};
use std::time::{Duration, Instant};

use tauri::path::BaseDirectory;
use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder, WindowEvent};

/// Process-group id of the sidecar. The PyInstaller bootloader re-execs, so
/// killing only the direct child can leave the server running.
static SIDECAR_PGID: AtomicI32 = AtomicI32::new(0);

struct Sidecar(Mutex<Option<Child>>);

fn free_port() -> u16 {
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind a free loopback port");
    listener.local_addr().expect("read the free port").port()
}

fn bearer_token() -> String {
    let mut bytes = [0u8; 16];
    getrandom::getrandom(&mut bytes).expect("draw a bearer token");
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(32);
    for byte in bytes {
        out.push(HEX[(byte >> 4) as usize] as char);
        out.push(HEX[(byte & 0x0f) as usize] as char);
    }
    out
}

fn sidecar_name() -> &'static str {
    if cfg!(windows) {
        "coperceive-server.exe"
    } else {
        "coperceive-server"
    }
}

fn sidecar_path(app: &tauri::AppHandle) -> PathBuf {
    let file = sidecar_name();
    let relatives = [format!("binaries/{file}"), file.to_string()];
    for relative in &relatives {
        if let Ok(path) = app.path().resolve(relative, BaseDirectory::Resource) {
            if path.is_file() {
                return path;
            }
        }
    }
    let mut extras = Vec::new();
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            extras.push(dir.join(file));
            extras.push(dir.join("binaries").join(file));
            extras.push(dir.join("../../binaries").join(file));
            extras.push(dir.join("../../../binaries").join(file));
        }
    }
    for path in extras {
        if path.is_file() {
            return path;
        }
    }
    PathBuf::from(file)
}

fn wait_healthy(port: u16, token: &str) -> bool {
    let deadline = Instant::now() + Duration::from_secs(45);
    let request = format!(
        "GET /api/health HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nAuthorization: Bearer {token}\r\nConnection: close\r\n\r\n"
    );
    while Instant::now() < deadline {
        if let Ok(mut stream) = std::net::TcpStream::connect(("127.0.0.1", port)) {
            stream.set_read_timeout(Some(Duration::from_secs(2))).ok();
            stream.set_write_timeout(Some(Duration::from_secs(2))).ok();
            if stream.write_all(request.as_bytes()).is_ok() {
                let mut body = String::new();
                let _ = stream.read_to_string(&mut body);
                if body.contains("\"ok\"") {
                    return true;
                }
            }
        }
        std::thread::sleep(Duration::from_millis(250));
    }
    false
}

fn note_sidecar(pid: u32) {
    SIDECAR_PGID.store(pid as i32, Ordering::SeqCst);
}

#[cfg(unix)]
fn install_stop_on_signal() {
    extern "C" fn handle(_sig: i32) {
        let pid = SIDECAR_PGID.load(Ordering::SeqCst);
        if pid > 0 {
            unsafe {
                libc::kill(-pid, libc::SIGKILL);
            }
        }
        unsafe {
            libc::_exit(0);
        }
    }
    unsafe {
        libc::signal(libc::SIGTERM, handle as *const () as libc::sighandler_t);
        libc::signal(libc::SIGINT, handle as *const () as libc::sighandler_t);
    }
}

fn stop_sidecar(app: &tauri::AppHandle) {
    let pid = SIDECAR_PGID.swap(0, Ordering::SeqCst);
    #[cfg(unix)]
    if pid > 0 {
        unsafe {
            libc::kill(-pid, libc::SIGTERM);
        }
    }
    if let Some(state) = app.try_state::<Sidecar>() {
        if let Ok(mut guard) = state.0.lock() {
            if let Some(mut child) = guard.take() {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    }
}

fn start_server(app: &tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    let port = free_port();
    let token = bearer_token();
    let root = app.path().app_data_dir()?;
    std::fs::create_dir_all(&root)?;
    let bin = sidecar_path(app.handle());
    if !bin.is_file() {
        return Err(format!("sidecar missing: {}", bin.display()).into());
    }
    let log_path = root.join("sidecar.log");
    let log = std::fs::File::create(&log_path)?;
    let port_s = port.to_string();
    let root_s = root.to_string_lossy().into_owned();
    let mut command = Command::new(&bin);
    command
        .args([
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            &port_s,
            "--token",
            &token,
            "--root",
            &root_s,
        ])
        .env("MPLBACKEND", "Agg")
        .stdin(Stdio::null())
        .stdout(Stdio::from(log.try_clone()?))
        .stderr(Stdio::from(log));
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        command.process_group(0);
    }
    let child = command.spawn()?;
    note_sidecar(child.id());
    app.state::<Sidecar>().0.lock().expect("sidecar lock").replace(child);
    if !wait_healthy(port, &token) {
        return Err(format!("sidecar did not serve /api/health; see {}", log_path.display()).into());
    }
    // The token is not a query parameter. The page reads sessionStorage.
    let origin = format!("http://127.0.0.1:{port}/");
    let script = format!("sessionStorage.setItem('coperceive-token','{token}');");
    WebviewWindowBuilder::new(
        app.handle(),
        "main",
        WebviewUrl::External(origin.parse()?),
    )
    .title("coperceive")
    .inner_size(1280.0, 800.0)
    .initialization_script(script)
    .build()?;
    Ok(())
}

fn main() {
    tauri::Builder::default()
        .manage(Sidecar(Mutex::new(None)))
        .setup(|app| {
            start_server(app)?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("build the coperceive window")
        .run(|app, event| match event {
            RunEvent::Ready => {
                #[cfg(unix)]
                install_stop_on_signal();
            }
            RunEvent::ExitRequested { .. } | RunEvent::Exit => stop_sidecar(app),
            RunEvent::WindowEvent {
                event: WindowEvent::Destroyed,
                ..
            } => stop_sidecar(app),
            _ => {}
        });
}
