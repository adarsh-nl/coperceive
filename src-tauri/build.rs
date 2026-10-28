use std::process::Command;

fn main() {
    let git_describe = Command::new("git")
        .args(["describe", "--always", "--dirty", "--tags"])
        .output()
        .ok()
        .and_then(|o| String::from_utf8(o.stdout).ok())
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| "580691a".into());

    println!("cargo:rustc-env=GIT_DESCRIBE={}", git_describe);

    let build_date = Command::new("date")
        .args(["-u", "+%Y-%m-%d"])
        .output()
        .ok()
        .and_then(|o| String::from_utf8(o.stdout).ok())
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| "2026-10-21".into());

    println!("cargo:rustc-env=BUILD_DATE={}", build_date);

    #[cfg(target_os = "macos")]
    tauri_build::build();
}
