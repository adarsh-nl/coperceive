// SPDX-License-Identifier: Apache-2.0
//! Coperceive Desktop Rust Core
//!
//! Provides native process, port discovery, token generation, SSH tunneling,
//! and Slurm session management for the Coperceive Tauri desktop application.

pub mod controller;
pub mod detect;
pub mod hpc;
pub mod library;
pub mod pack;
pub mod port;
pub mod remote;
pub mod server_profiles;
pub mod sidecar;
pub mod slurm;
pub mod slurm_adapter;
pub mod ssh;
pub mod state;
pub mod test_seams;
pub mod token;
