// SPDX-License-Identifier: Apache-2.0
//! Export and import a lab server profile without secrets or one person's paths.
//!
//! The file is JSON with a schema version. Per-user pieces (SSH alias, and any
//! home, user name or concrete scratch path) are placeholders the importer fills
//! in. Nothing here is a cluster hostname, partition or path of our own: those
//! stay whatever the exported profile already said.

use super::ServerEntry;
use crate::hpc::{PipelineSpec, ServerProfile};

pub const LAB_PROFILE_SCHEMA: &str = "coperceive-lab-profile";
pub const LAB_PROFILE_VERSION: u32 = 1;

const PLACEHOLDER_USER: &str = "{{user}}";
const PLACEHOLDER_HOME: &str = "{{home}}";
const PLACEHOLDER_SCRATCH: &str = "{{scratch}}";
const MAX_DOCUMENT_BYTES: usize = 1_000_000;

/// What the importing person types. The SSH alias is required; the others are
/// required only when the file still has that placeholder.
#[derive(Debug, Clone, serde::Deserialize)]
pub struct LabImportIdentity {
    pub ssh_alias: String,
    #[serde(default)]
    pub user: String,
    #[serde(default)]
    pub home: String,
    #[serde(default)]
    pub scratch: String,
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct LabProfileDocument {
    schema: String,
    schema_version: u32,
    profile: ServerProfile,
    #[serde(default)]
    pipelines: Vec<PipelineSpec>,
}

/// Writes a secret-free lab profile. SSH alias, host and any stored user name
/// are removed; home directories and a concrete scratch path become placeholders.
pub fn export_lab_profile(entry: &ServerEntry, extra: &[PipelineSpec]) -> Result<String, String> {
    entry.validate()?;
    let mut pipelines = entry.shared_pipelines.clone();
    for pipeline in extra {
        pipeline
            .validate()
            .map_err(|error| format!("The pipeline registration cannot be shared: {error}"))?;
        if !pipelines
            .iter()
            .any(|have| have.name == pipeline.name && have.script == pipeline.script)
        {
            pipelines.push(pipeline.clone());
        }
    }
    let source = serde_json::json!({
        "profile": entry.profile,
        "pipelines": pipelines,
        "ssh_user": entry.ssh.as_ref().and_then(|ssh| ssh.user.clone()),
    });
    if let Some(found) = find_share_secret(&source) {
        return Err(format!(
            "Refusing to export this profile: it contains {found}. Remove passwords, tokens and \
             keys before sharing. Sign-in stays in each person's own SSH config."
        ));
    }
    let (homes, users) = personal_markers(&source, entry.profile.user.as_deref());
    let mut profile_value = serde_json::to_value(&entry.profile)
        .map_err(|error| format!("Could not prepare the profile for sharing: {error}"))?;
    if let Some(scratch) = profile_value
        .get("scratch_template")
        .and_then(|v| v.as_str())
    {
        if !scratch_has_per_job_token(scratch) {
            if let Some(object) = profile_value.as_object_mut() {
                object.insert(
                    "scratch_template".to_string(),
                    serde_json::Value::String(PLACEHOLDER_SCRATCH.to_string()),
                );
            }
        }
    }
    let original_user = entry
        .profile
        .user
        .as_deref()
        .map(str::trim)
        .filter(|user| !user.is_empty())
        .map(str::to_string);
    map_strings(&mut profile_value, &mut |text| {
        Ok(personalise(text, &homes, &users))
    })?;
    let mut profile: ServerProfile = serde_json::from_value(profile_value)
        .map_err(|error| format!("Could not prepare the profile for sharing: {error}"))?;
    // The alias and the host belong to the person who exports. The importer
    // types their own SSH alias; a shared file must not keep either.
    profile.ssh_alias = None;
    profile.host = None;
    if original_user.is_some() {
        profile.user = Some(PLACEHOLDER_USER.to_string());
    }
    let mut pipeline_value = serde_json::to_value(&pipelines)
        .map_err(|error| format!("Could not prepare the pipelines for sharing: {error}"))?;
    map_strings(&mut pipeline_value, &mut |text| {
        Ok(personalise(text, &homes, &users))
    })?;
    let shared: Vec<PipelineSpec> = serde_json::from_value(pipeline_value)
        .map_err(|error| format!("Could not prepare the pipelines for sharing: {error}"))?;
    for pipeline in &shared {
        pipeline
            .validate()
            .map_err(|error| format!("The pipeline registration cannot be shared: {error}"))?;
    }
    let document = LabProfileDocument {
        schema: LAB_PROFILE_SCHEMA.to_string(),
        schema_version: LAB_PROFILE_VERSION,
        profile,
        pipelines: shared,
    };
    let value = serde_json::to_value(&document)
        .map_err(|error| format!("Could not write the lab profile: {error}"))?;
    if let Some(found) = find_share_secret(&value) {
        return Err(format!(
            "Refusing to export this profile: it contains {found}. Remove passwords, tokens and \
             keys before sharing."
        ));
    }
    serde_json::to_string_pretty(&document)
        .map_err(|error| format!("Could not write the lab profile: {error}"))
}

/// Reads a lab profile, fills the importer's own SSH alias and placeholders,
/// and returns an entry that can be tested and saved. The file's alias, host
/// and any secret-shaped content are ignored or refused.
pub fn prepare_lab_import(
    document: &str,
    identity: &LabImportIdentity,
) -> Result<ServerEntry, String> {
    if document.len() > MAX_DOCUMENT_BYTES {
        return Err("This lab profile file is too large to import.".to_string());
    }
    let raw: serde_json::Value = serde_json::from_str(document)
        .map_err(|error| format!("This file is not JSON a lab profile can use: {error}"))?;
    if let Some(found) = find_share_secret(&raw) {
        return Err(format!(
            "Refusing this file: it contains {found}. A shared lab profile must not contain \
             passwords, tokens or keys."
        ));
    }
    let parsed: LabProfileDocument = serde_json::from_value(raw)
        .map_err(|error| format!("This file is not a coperceive lab profile: {error}"))?;
    if parsed.schema != LAB_PROFILE_SCHEMA || parsed.schema_version != LAB_PROFILE_VERSION {
        return Err(
            "This file is not a coperceive lab profile (schema coperceive-lab-profile version 1)."
                .to_string(),
        );
    }
    let alias = clean_alias(&identity.ssh_alias)?;
    reject_identity_value("user name", &identity.user)?;
    reject_identity_value("home directory", &identity.home)?;
    reject_identity_value("scratch folder", &identity.scratch)?;
    let mut profile_value = serde_json::to_value(&parsed.profile)
        .map_err(|error| format!("This lab profile could not be read: {error}"))?;
    map_strings(&mut profile_value, &mut |text| expand_text(text, identity))?;
    let mut profile: ServerProfile = serde_json::from_value(profile_value)
        .map_err(|error| format!("This lab profile could not be read: {error}"))?;
    profile.ssh_alias = Some(alias);
    profile.host = None;
    let mut pipeline_value = serde_json::to_value(&parsed.pipelines)
        .map_err(|error| format!("The shared pipelines could not be read: {error}"))?;
    map_strings(&mut pipeline_value, &mut |text| expand_text(text, identity))?;
    let pipelines: Vec<PipelineSpec> = serde_json::from_value(pipeline_value)
        .map_err(|error| format!("The shared pipelines could not be read: {error}"))?;
    profile
        .validate()
        .map_err(|error| format!("This lab profile is not usable yet: {error}"))?;
    let mut entry = ServerEntry::new(profile);
    entry.shared_pipelines = pipelines;
    entry
        .validate()
        .map_err(|error| format!("This lab profile is not usable yet: {error}"))?;
    let saved = serde_json::to_value(&entry.profile)
        .map_err(|error| format!("Could not check the imported profile: {error}"))?;
    if let Some(found) = find_share_secret(&saved) {
        return Err(format!(
            "Refusing to import this profile: it contains {found}."
        ));
    }
    Ok(entry)
}

pub(super) fn find_share_secret(value: &serde_json::Value) -> Option<String> {
    match value {
        serde_json::Value::Object(map) => {
            for (key, child) in map {
                if key_is_secret(key) {
                    return Some(format!("a credential-like field {key:?}"));
                }
                if let Some(found) = find_share_secret(child) {
                    return Some(found);
                }
            }
            None
        }
        serde_json::Value::Array(items) => items.iter().find_map(find_share_secret),
        serde_json::Value::String(text) if value_is_secret(text) => {
            Some("a credential-like value".to_string())
        }
        _ => None,
    }
}

fn key_is_secret(key: &str) -> bool {
    const WORDS: &[&str] = &[
        "password",
        "passwd",
        "passphrase",
        "secret",
        "token",
        "apikey",
        "privatekey",
        "credential",
        "credentials",
        "bearer",
        "authorization",
        "keyring",
    ];
    let normalized: String = key
        .chars()
        .filter(|ch| ch.is_ascii_alphanumeric())
        .map(|ch| ch.to_ascii_lowercase())
        .collect();
    let split = key
        .split(|ch: char| !ch.is_ascii_alphanumeric())
        .filter(|word| !word.is_empty())
        .any(|word| WORDS.contains(&word.to_ascii_lowercase().as_str()));
    split
        || WORDS
            .iter()
            .any(|word| normalized == *word || normalized.ends_with(word))
}

fn value_is_secret(value: &str) -> bool {
    let lower = value.to_ascii_lowercase();
    if lower.contains("-----begin ")
        || lower.starts_with("bearer ")
        || lower.contains("password=")
        || lower.contains("passwd=")
        || lower.contains("token=")
        || lower.contains("secret=")
        || lower.contains("api_key=")
        || lower.contains("private_key=")
        || lower.contains("keyring:")
    {
        return true;
    }
    let Some(rest) = lower.strip_prefix("sk-") else {
        return false;
    };
    rest.len() >= 20
        && rest
            .chars()
            .all(|ch| ch.is_ascii_alphanumeric() || ch == '-' || ch == '_')
}

fn personal_markers(
    source: &serde_json::Value,
    explicit_user: Option<&str>,
) -> (Vec<String>, Vec<String>) {
    let mut homes = Vec::new();
    collect_homes(source, &mut homes);
    let mut users = Vec::new();
    if let Some(user) = explicit_user
        .map(str::trim)
        .filter(|user| is_personal_name(user))
    {
        push_unique(&mut users, user.to_string());
    }
    if let Some(ssh_user) = source
        .get("ssh_user")
        .and_then(|value| value.as_str())
        .map(str::trim)
        .filter(|user| is_personal_name(user))
    {
        push_unique(&mut users, ssh_user.to_string());
    }
    for home in &homes {
        if let Some(name) = home
            .rsplit('/')
            .next()
            .filter(|name| is_personal_name(name))
        {
            push_unique(&mut users, name.to_string());
        }
    }
    (homes, users)
}

fn collect_homes(value: &serde_json::Value, homes: &mut Vec<String>) {
    match value {
        serde_json::Value::String(text) => push_homes(text, homes),
        serde_json::Value::Array(items) => {
            for item in items {
                collect_homes(item, homes);
            }
        }
        serde_json::Value::Object(map) => {
            for child in map.values() {
                collect_homes(child, homes);
            }
        }
        _ => {}
    }
}

fn push_homes(text: &str, homes: &mut Vec<String>) {
    for base in ["/home/", "/Users/"] {
        let mut search = text;
        while let Some(index) = search.find(base) {
            let after = &search[index + base.len()..];
            let name_len = after
                .find(|ch: char| {
                    !(ch.is_ascii_alphanumeric() || ch == '_' || ch == '-' || ch == '.')
                })
                .unwrap_or(after.len());
            let name = &after[..name_len];
            if is_personal_name(name) {
                push_unique(homes, format!("{base}{name}"));
            }
            if after.is_empty() {
                break;
            }
            search = &search[index + base.len()..];
        }
    }
}

fn is_personal_name(name: &str) -> bool {
    let length = name.chars().count();
    (2..=32).contains(&length)
        && name
            .chars()
            .all(|ch| ch.is_ascii_alphanumeric() || ch == '_' || ch == '-' || ch == '.')
        && !name.starts_with('.')
        && name != ".."
}

fn push_unique(items: &mut Vec<String>, value: String) {
    if !items.iter().any(|item| item == &value) {
        items.push(value);
    }
}

fn scratch_has_per_job_token(template: &str) -> bool {
    template.contains("$USER")
        || template.contains("${USER}")
        || template.contains("$SLURM_JOB_ID")
        || template.contains("${SLURM_JOB_ID}")
}

fn personalise(text: &str, homes: &[String], users: &[String]) -> String {
    let mut homes_sorted = homes.to_vec();
    homes_sorted.sort_by_key(|home| std::cmp::Reverse(home.len()));
    let mut out = text.to_string();
    for home in &homes_sorted {
        out = replace_bounded(&out, home, PLACEHOLDER_HOME);
    }
    let mut users_sorted = users.to_vec();
    users_sorted.sort_by_key(|user| std::cmp::Reverse(user.len()));
    for user in &users_sorted {
        out = replace_whole_word(&out, user, PLACEHOLDER_USER);
    }
    out
}

fn replace_bounded(text: &str, needle: &str, replacement: &str) -> String {
    let mut out = String::new();
    let mut rest = text;
    while let Some(index) = rest.find(needle) {
        let after = index + needle.len();
        let boundary = after == rest.len() || rest.as_bytes().get(after) == Some(&b'/');
        if boundary {
            out.push_str(&rest[..index]);
            out.push_str(replacement);
            rest = &rest[after..];
        } else {
            out.push_str(&rest[..index + 1]);
            rest = &rest[index + 1..];
        }
    }
    out.push_str(rest);
    out
}

fn replace_whole_word(text: &str, needle: &str, replacement: &str) -> String {
    let chars: Vec<char> = text.chars().collect();
    let word: Vec<char> = needle.chars().collect();
    if word.is_empty() {
        return text.to_string();
    }
    let mut out = String::new();
    let mut index = 0;
    while index < chars.len() {
        let matches = chars[index..].starts_with(&word);
        let before_ok = index == 0 || !is_name_char(chars[index - 1]);
        let after = index + word.len();
        let after_ok = after >= chars.len() || !is_name_char(chars[after]);
        // Leave the words inside {{home}}, {{user}} and {{scratch}} alone.
        let inside_placeholder = index >= 2
            && chars[index - 2] == '{'
            && chars[index - 1] == '{'
            && after + 1 < chars.len()
            && chars[after] == '}'
            && chars[after + 1] == '}';
        if matches && before_ok && after_ok && !inside_placeholder {
            out.push_str(replacement);
            index = after;
        } else {
            out.push(chars[index]);
            index += 1;
        }
    }
    out
}

fn is_name_char(ch: char) -> bool {
    ch.is_ascii_alphanumeric() || ch == '_' || ch == '-' || ch == '.'
}

fn expand_text(text: &str, identity: &LabImportIdentity) -> Result<String, String> {
    let mut out = text.to_string();
    if out.contains(PLACEHOLDER_USER) {
        let user = identity.user.trim();
        if !is_personal_name(user) {
            return Err(
                "Enter your user name on the cluster. This profile uses it in place of the \
                 person who shared it."
                    .to_string(),
            );
        }
        out = out.replace(PLACEHOLDER_USER, user);
    }
    if out.contains(PLACEHOLDER_HOME) {
        let home = identity.home.trim().trim_end_matches('/');
        if home.is_empty() || !(home.starts_with('/') || home.starts_with('~')) {
            return Err(
                "Enter your home directory on the cluster as an absolute path. This profile uses \
                 it in place of the person who shared it."
                    .to_string(),
            );
        }
        if home.split('/').any(|segment| segment == "..") {
            return Err("The home directory may not contain '..'.".to_string());
        }
        out = out.replace(PLACEHOLDER_HOME, home);
    }
    if out.contains(PLACEHOLDER_SCRATCH) {
        let scratch = identity.scratch.trim();
        if scratch.is_empty() {
            return Err(
                "Enter your scratch folder. The shared profile left that path for each person."
                    .to_string(),
            );
        }
        if scratch.split('/').any(|segment| segment == "..") {
            return Err("The scratch folder may not contain '..'.".to_string());
        }
        out = out.replace(PLACEHOLDER_SCRATCH, scratch);
    }
    if out.contains("{{") {
        return Err(
            "This profile still has a placeholder coperceive does not know how to fill."
                .to_string(),
        );
    }
    Ok(out)
}

fn clean_alias(alias: &str) -> Result<String, String> {
    let alias = alias.trim();
    if alias.is_empty() {
        return Err(
            "Enter your SSH alias. It is the Host name in your own SSH config, and it is not \
             copied from the shared file."
                .to_string(),
        );
    }
    if alias
        .chars()
        .any(|ch| ch.is_whitespace() || ch.is_control())
    {
        return Err("The SSH alias cannot contain spaces or control characters.".to_string());
    }
    if alias.split('/').any(|segment| segment == "..") {
        return Err("The SSH alias may not contain '..'.".to_string());
    }
    Ok(alias.to_string())
}

fn reject_identity_value(label: &str, value: &str) -> Result<(), String> {
    if value.contains("{{") || value.contains('\n') || value.contains('\0') || value.contains('\r')
    {
        return Err(format!(
            "The {label} cannot contain a placeholder or a line break."
        ));
    }
    Ok(())
}

fn map_strings(
    value: &mut serde_json::Value,
    map: &mut dyn FnMut(&str) -> Result<String, String>,
) -> Result<(), String> {
    match value {
        serde_json::Value::String(text) => {
            *text = map(text)?;
        }
        serde_json::Value::Array(items) => {
            for item in items {
                map_strings(item, map)?;
            }
        }
        serde_json::Value::Object(object) => {
            for child in object.values_mut() {
                map_strings(child, map)?;
            }
        }
        _ => {}
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::hpc::{ArgumentKind, ArgumentRole, ArgumentSpec, EnvironmentSetup, PipelineSpec};

    fn cluster_b() -> ServerEntry {
        serde_json::from_str(include_str!(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/tests/fixtures/hpc/cluster-b.json"
        )))
        .expect("cluster-b fixture")
    }

    fn student() -> LabImportIdentity {
        LabImportIdentity {
            ssh_alias: "student-cluster".to_string(),
            user: "sam".to_string(),
            home: "/home/sam".to_string(),
            scratch: "/scratch/$USER/$SLURM_JOB_ID".to_string(),
        }
    }

    fn pipeline() -> PipelineSpec {
        PipelineSpec {
            name: "lab-eval".to_string(),
            script: "/home/bob/pipelines/run.py".to_string(),
            interpreter: vec!["python3".to_string()],
            environment: EnvironmentSetup::default(),
            arguments: vec![ArgumentSpec {
                name: "config".to_string(),
                kind: ArgumentKind::Path,
                help: "shared config".to_string(),
                required: false,
                default: Some(serde_json::json!("/home/bob/pipelines/config.yaml")),
                choices: Vec::new(),
                multiple: false,
                role: ArgumentRole::Config,
            }],
            output_contract: "coperceive-pipeline-1".to_string(),
            description: "Shared evaluation script.".to_string(),
        }
    }

    #[test]
    fn cluster_b_round_trip_drops_the_admin_and_keeps_the_shared_setup() {
        let entry = cluster_b();
        let exported = export_lab_profile(&entry, &[pipeline()]).expect("export");
        assert!(!exported.contains("alice"), "{exported}");
        assert!(!exported.contains("bob"), "{exported}");
        assert!(!exported.contains("cluster-b"), "{exported}");
        assert!(exported.contains(PLACEHOLDER_HOME), "{exported}");
        assert!(exported.contains(PLACEHOLDER_SCRATCH), "{exported}");
        assert!(exported.contains("python/3.12"), "{exported}");
        assert!(exported.contains("/data/scenes"), "{exported}");
        let imported = prepare_lab_import(&exported, &student()).expect("import");
        let rendered = serde_json::to_string(&imported).expect("json");
        assert!(!rendered.contains("alice"), "{rendered}");
        assert!(!rendered.contains("bob"), "{rendered}");
        assert_eq!(
            imported.profile.ssh_alias.as_deref(),
            Some("student-cluster")
        );
        assert!(imported.profile.host.is_none());
        assert_eq!(
            imported.profile.scratch_template,
            "/scratch/$USER/$SLURM_JOB_ID"
        );
        assert_eq!(imported.profile.data_roots, entry.profile.data_roots);
        assert_eq!(imported.profile.code_roots, entry.profile.code_roots);
        assert_eq!(imported.profile.results_root, entry.profile.results_root);
        assert_eq!(imported.profile.name, entry.profile.name);
        assert_eq!(imported.profile.scheduler, entry.profile.scheduler);
        assert_eq!(imported.profile.defaults, entry.profile.defaults);
        assert_eq!(
            imported.profile.environment.modules,
            entry.profile.environment.modules
        );
        assert_eq!(
            imported.profile.environment.coperceive_command[0],
            "/home/sam/coperceive-app/.venv/bin/python"
        );
        assert_eq!(imported.shared_pipelines.len(), 1);
        assert_eq!(
            imported.shared_pipelines[0].script,
            "/home/sam/pipelines/run.py"
        );
        assert_eq!(
            imported.shared_pipelines[0].arguments[0].default,
            Some(serde_json::json!("/home/sam/pipelines/config.yaml"))
        );
        assert!(imported
            .profile
            .notes
            .unwrap_or_default()
            .contains("ProxyJump"));
    }

    #[test]
    fn a_secret_shaped_file_is_refused_on_export_and_import() {
        let mut entry = cluster_b();
        entry.profile.notes = Some("token=abcd".to_string());
        let error = export_lab_profile(&entry, &[]).expect_err("export secret");
        assert!(
            error.contains("credential") || error.contains("token") || error.contains("secret"),
            "{error}"
        );

        let clean = export_lab_profile(&cluster_b(), &[]).expect("export");
        let mut value: serde_json::Value = serde_json::from_str(&clean).unwrap();
        value
            .as_object_mut()
            .unwrap()
            .insert("password".to_string(), serde_json::json!("x"));
        let error = prepare_lab_import(&value.to_string(), &student()).expect_err("import key");
        assert!(
            error.contains("password") || error.contains("credential"),
            "{error}"
        );

        let mut notes = serde_json::from_str::<serde_json::Value>(&clean).unwrap();
        notes["profile"]["notes"] = serde_json::json!("see /data/tokenizer.json");
        let imported = prepare_lab_import(&notes.to_string(), &student()).expect("tokenizer path");
        assert_eq!(
            imported.profile.notes.as_deref(),
            Some("see /data/tokenizer.json")
        );
    }

    #[test]
    fn import_requires_an_ssh_alias_and_the_placeholders_the_file_uses() {
        let exported = export_lab_profile(&cluster_b(), &[]).expect("export");
        let mut missing_alias = student();
        missing_alias.ssh_alias = "  ".to_string();
        let error = prepare_lab_import(&exported, &missing_alias).expect_err("alias");
        assert!(error.contains("SSH alias"), "{error}");
        let mut missing_home = student();
        missing_home.home = String::new();
        let error = prepare_lab_import(&exported, &missing_home).expect_err("home");
        assert!(error.contains("home directory"), "{error}");
    }
}
