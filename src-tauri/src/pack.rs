// SPDX-License-Identifier: Apache-2.0
//! Argument list for `python -m coperceive scene pack`.
//!
//! The desktop app spawns this. It does not reimplement the copy.

/// Build the argv after the Python executable.
pub fn scene_pack_args(
    source: &str,
    dest: &str,
    images: &str,
    media_roots: &[String],
    griffin_root: Option<&str>,
) -> Result<Vec<String>, String> {
    if images != "full" && images != "preview" && images != "none" {
        return Err("images must be full, preview, or none".into());
    }
    let mut args = vec![
        "-m".to_string(),
        "coperceive".to_string(),
        "scene".to_string(),
        "pack".to_string(),
        source.to_string(),
        dest.to_string(),
        "--images".to_string(),
        images.to_string(),
    ];
    for root in media_roots {
        args.push("--media-root".to_string());
        args.push(root.clone());
    }
    if let Some(root) = griffin_root {
        if !root.is_empty() {
            args.push("--griffin-root".to_string());
            args.push(root.to_string());
        }
    }
    Ok(args)
}

#[cfg(test)]
mod tests {
    use super::scene_pack_args;

    #[test]
    fn pack_args_include_the_image_mode_and_roots() {
        let args = scene_pack_args(
            "/tmp/scene",
            "/tmp/library",
            "preview",
            &["/tmp/media".to_string()],
            Some("/tmp/griffin"),
        )
        .expect("args");
        assert_eq!(
            args,
            vec![
                "-m",
                "coperceive",
                "scene",
                "pack",
                "/tmp/scene",
                "/tmp/library",
                "--images",
                "preview",
                "--media-root",
                "/tmp/media",
                "--griffin-root",
                "/tmp/griffin",
            ]
        );
    }

    #[test]
    fn pack_args_reject_an_unknown_image_mode() {
        assert!(scene_pack_args("/s", "/d", "original", &[], None).is_err());
    }
}
