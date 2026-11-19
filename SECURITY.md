# Security Policy

## Reporting a Vulnerability

We take the security of Coperceive seriously. If you discover a security vulnerability, please report it privately through **GitHub's Private Vulnerability Reporting**:

- **Submit a Security Advisory**: [Report a vulnerability](https://github.com/adarsh-nl/coperceive/security/advisories/new)

Please **do not** open public GitHub issues or discussions for security vulnerabilities. Private reporting allows us to assess, reproduce, and resolve issues before public disclosure.

When reporting an issue, please include:
- A description of the vulnerability and its potential impact.
- Clear steps to reproduce the issue or a minimal proof-of-concept.
- The version of Coperceive and operating system environment tested.

We will acknowledge receipt within 48 hours and coordinate a coordinated disclosure schedule.

---

## Supported Versions

| Version | Supported          |
| :------ | :----------------- |
| 0.1.x   | :white_check_mark: |
| < 0.1.0 | :x:                |

We provide security updates for the current minor release series (`0.1.x`). Users are encouraged to run the latest available release.

---

## Scope

The following components are in scope for security vulnerability reports:

- **Desktop Application Shell**: Tauri IPC commands, permissions, window handling, and sandbox boundaries.
- **Application Updater**: Verification of release metadata (`latest.json`), miniisign cryptographic signatures on updater archives, and HTTPS transport integrity.
- **SSH Credentials & Multiplexing**: Handling of SSH configurations, authentication agents, identity files, and command execution boundaries.
- **Diagnostics Redaction**: Automated redaction of sensitive credentials, authentication tokens, private keys, hostnames, and private filesystem paths in the diagnostics preview, clipboard copy, and zip export.
- **Dataset File Boundaries**: Path traversal prevention and symlink escaping protections when indexing or streaming raw datasets.

The following are **out of scope**:
- Vulnerabilities requiring physical access or root/administrator compromise of the host machine.
- Denial of service via intentionally corrupted local dataset files loaded by the local user.
- Social engineering or attacks targeting third-party dataset hosting mirrors.
