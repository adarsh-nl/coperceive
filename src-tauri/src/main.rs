// SPDX-License-Identifier: Apache-2.0
//! Coperceive Desktop entrypoint.

use std::env;
use std::path::PathBuf;
use std::process;

use coperceive_desktop::port::find_free_loopback_port;
use coperceive_desktop::sidecar::SidecarSession;
use coperceive_desktop::token::generate_secure_token;

fn main() {
    let args: Vec<String> = env::args().collect();
    if args.len() > 1 && args[1] == "--version" {
        println!("coperceive-desktop 0.0.0");
        return;
    }

    if args.len() > 2 && args[1] == "sidecar" {
        let python = PathBuf::from(&args[2]);
        let root = if args.len() > 3 {
            PathBuf::from(&args[3])
        } else {
            PathBuf::from(".")
        };

        match SidecarSession::spawn(&python, &root) {
            Ok(mut session) => {
                println!(
                    "Started local sidecar on port {} with data root {}",
                    session.port,
                    session.root.display()
                );
                // Keep running until interrupted
                while session.is_alive() {
                    std::thread::sleep(std::time::Duration::from_millis(500));
                }
            }
            Err(e) => {
                eprintln!("Failed to spawn local sidecar: {}", e);
                process::exit(1);
            }
        }
        return;
    }

    println!("Coperceive Desktop runtime initialized.");
    let port = find_free_loopback_port().unwrap_or(8731);
    let token = generate_secure_token().unwrap_or_default();
    println!("Ephemeral port: {}, Token prefix: {}...", port, &token[..8.min(token.len())]);
}
