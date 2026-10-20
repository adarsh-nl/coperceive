import React from "react";

export const DesktopSection: React.FC = () => {
  return (
    <section id="desktop" className="py-24 border-t border-[#1e2638] bg-[#0a0d14]">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="max-w-3xl mb-16">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-cyan-950/60 border border-cyan-500/40 text-cyan-300 text-xs font-mono mb-4">
            <span className="w-2 h-2 rounded-full bg-cyan-400" />
            Native Client
          </div>
          <h2 className="text-3xl sm:text-4xl font-bold tracking-tight text-white mb-4">
            coperceive Desktop
          </h2>
          <p className="text-base sm:text-lg text-zinc-400 leading-relaxed">
            A native Tauri v2 desktop application engineered for high-throughput visualization and remote cluster workflows.
          </p>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-center">
          {/* Left: Desktop screenshot placeholder with honest note */}
          <div className="lg:col-span-7 bg-[#111724] border border-[#232e42] rounded-xl p-4 flex flex-col gap-3">
            <div className="relative aspect-[16/10] bg-[#0a0d14] rounded-lg border border-[#1e2638] overflow-hidden flex flex-col items-center justify-center p-6 text-center">
              {/* Window chrome header */}
              <div className="absolute top-0 left-0 right-0 h-8 bg-[#151c2a] border-b border-[#232e42] flex items-center px-3 gap-2">
                <span className="w-3 h-3 rounded-full bg-red-500/80" />
                <span className="w-3 h-3 rounded-full bg-yellow-500/80" />
                <span className="w-3 h-3 rounded-full bg-green-500/80" />
                <span className="text-[11px] font-mono text-zinc-400 mx-auto">coperceive — Connect Target</span>
              </div>

              {/* Mock Connect UI */}
              <div className="max-w-md space-y-4 pt-6">
                <div className="inline-block px-3 py-1 bg-zinc-800/80 border border-zinc-700 rounded text-xs font-mono text-zinc-300">
                  [TODO: Native app screenshot will be published upon Phase 2 macOS verification]
                </div>
                <h3 className="text-base font-semibold text-zinc-200">
                  Two Execution Targets, One Unified Interface
                </h3>
                <div className="grid grid-cols-2 gap-3 text-left">
                  <div className="p-3 rounded-lg bg-[#111724] border border-[#232e42]">
                    <div className="text-xs font-mono font-semibold text-cyan-400 mb-1">Target A: Local</div>
                    <p className="text-[11px] text-zinc-400">Bundled Python sidecar, folder picker, auto loopback bind.</p>
                  </div>
                  <div className="p-3 rounded-lg bg-[#111724] border border-[#232e42]">
                    <div className="text-xs font-mono font-semibold text-purple-400 mb-1">Target B: HPC (SSH)</div>
                    <p className="text-[11px] text-zinc-400">Slurm job lifecycle, SSH ControlMaster tunnel, 0600 token auth.</p>
                  </div>
                </div>
              </div>
            </div>
            <div className="text-[11px] font-mono text-zinc-400 text-center">
              Built on Tauri v2 (Rust backend + WebGL frontend). Memory footprint &lt; 85 MB.
            </div>
          </div>

          {/* Right: Technical specifications */}
          <div className="lg:col-span-5 space-y-6">
            <div className="space-y-3">
              <h3 className="text-xl font-semibold text-zinc-100">
                Engineered for GPU Clusters & Laptops
              </h3>
              <p className="text-sm text-zinc-400 leading-relaxed">
                Whether inspecting converted datasets locally on Apple Silicon or tunneling into multi-node Slurm clusters hosting hundreds of gigabytes on GPFS, the desktop client provides an identical seamless workflow.
              </p>
            </div>

            <div className="space-y-3 font-mono text-xs">
              <div className="p-3 rounded-lg bg-[#111724] border border-[#232e42]">
                <strong className="text-zinc-200 block mb-1">Zero Credentials Stored</strong>
                <span className="text-zinc-400">
                  Uses system ssh-agent and ControlMaster tunnels. Passwords and secret keys are never handled or persisted by the app.
                </span>
              </div>
              <div className="p-3 rounded-lg bg-[#111724] border border-[#232e42]">
                <strong className="text-zinc-200 block mb-1">Slurm Job Management</strong>
                <span className="text-zinc-400">
                  Submits <code>coperceive-desktop-&lt;uuid&gt;</code> sbatch jobs, negotiates free ports, monitors walltime leases, and auto-cleans on exit.
                </span>
              </div>
              <div className="p-3 rounded-lg bg-[#111724] border border-[#232e42]">
                <strong className="text-zinc-200 block mb-1">macOS arm64 & Linux Builds</strong>
                <span className="text-zinc-400">
                  Universal macOS dmg builds with hash-pinned Python 3.13 runtime locks.
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
};
