// SPDX-License-Identifier: Apache-2.0
//! Coperceive Desktop Rust Core
//!
//! Provides native process, port discovery, token generation, SSH tunneling,
//! and Slurm session management for the Coperceive Tauri desktop application.

pub mod controller;
pub mod detect;
pub mod export;
pub mod hpc;
pub mod job_submit;
pub mod job_history;
pub mod job_report;
pub mod job_watch;
pub mod library;
pub mod pack;
pub mod port;
pub mod remote;
pub mod remote_scan;
pub mod remote_static;
pub mod run_plan;
pub mod server_profiles;
pub mod sidecar;
pub mod slurm;
pub mod slurm_adapter;
pub mod ssh;
pub mod state;
pub mod test_seams;
pub mod token;
pub mod updater;
