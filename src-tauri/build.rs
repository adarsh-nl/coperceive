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

    tauri_build::try_build(
        tauri_build::Attributes::new().plugin(
            "viewer-bridge",
            tauri_build::InlinedPlugin::new()
                .commands(&[
                    "viewer_get_dataset_scenes",
                    "viewer_set_title",
                    "viewer_prepare_scene",
                    "viewer_show_library",
                    "viewer_open_folder_dialog",
                    "viewer_pick_save_path",
                    "viewer_save_exported_file",
                    "viewer_reveal_last_export",
                    "viewer_get_preferences",
                    "viewer_update_preferences",
                    "viewer_check_update",
                    "viewer_install_update",
                ])
                .default_permission(tauri_build::DefaultPermissionRule::AllowAllCommands),
        ),
    )
    .expect("failed to configure tauri build");
}
