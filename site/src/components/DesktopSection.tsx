import React from "react";

export const DesktopSection: React.FC = () => {
  return (
    <section id="desktop" className="py-24 border-t border-hairline bg-canvas">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="max-w-3xl mb-16">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-glass border border-hairline text-secondary text-xs font-mono mb-4">
            <span className="w-1.5 h-1.5 rounded-full bg-uav" />
            Native Client
          </div>
          <h2 className="text-3xl sm:text-4xl font-semibold tracking-tight text-primary mb-4">
            coperceive Desktop
          </h2>
          <p className="text-base sm:text-lg text-secondary leading-relaxed">
            A native Tauri v2 desktop application engineered for high-throughput visualization and remote cluster workflows.
          </p>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-center">
          {/* Left: Desktop screenshot placeholder with honest note */}
          <div className="lg:col-span-7 bg-glass backdrop-blur-22 border border-hairline rounded-2xl p-4 flex flex-col gap-3">
            <div className="relative aspect-[16/10] bg-canvas rounded-xl border border-hairline overflow-hidden flex flex-col items-center justify-center p-6 text-center">
              {/* Window chrome header */}
              <div className="absolute top-0 left-0 right-0 h-8 bg-glass border-b border-hairline flex items-center px-3 gap-2">
                <span className="w-2.5 h-2.5 rounded-full bg-bad/80" />
                <span className="w-2.5 h-2.5 rounded-full bg-warn/80" />
                <span className="w-2.5 h-2.5 rounded-full bg-ok/80" />
                <span className="text-[11px] font-mono text-tertiary mx-auto">coperceive — Connect Target</span>
              </div>

              {/* Mock Connect UI */}
              <div className="max-w-md space-y-4 pt-6">
                <div className="inline-block px-3 py-1 bg-glass border border-hairline rounded-full text-xs font-mono text-secondary">
                  [TODO: Native app screenshot will be published upon Phase 2 macOS verification]
                </div>
                <h3 className="text-base font-semibold text-primary">
                  Two Execution Targets, One Unified Interface
                </h3>
                <div className="grid grid-cols-2 gap-3 text-left">
                  <div className="p-3 rounded-xl bg-canvas border border-hairline">
                    <div className="text-xs font-mono font-semibold text-vehicle mb-1">Target A: Local</div>
                    <p className="text-[11px] text-secondary">Bundled Python sidecar, folder picker, auto loopback bind.</p>
                  </div>
                  <div className="p-3 rounded-xl bg-canvas border border-hairline">
                    <div className="text-xs font-mono font-semibold text-uav mb-1">Target B: HPC (SSH)</div>
                    <p className="text-[11px] text-secondary">Slurm job lifecycle, SSH ControlMaster tunnel, 0600 token auth.</p>
                  </div>
                </div>
              </div>
            </div>
            <div className="text-[11px] font-mono text-tertiary text-center">
              Built on Tauri v2 (Rust backend + WebGL frontend). Memory footprint &lt; 85 MB.
            </div>
          </div>

          {/* Right: Technical specifications */}
          <div className="lg:col-span-5 space-y-6">
            <div className="space-y-3">
              <h3 className="text-xl font-semibold text-primary">
                Engineered for GPU Clusters & Laptops
              </h3>
              <p className="text-sm text-secondary leading-relaxed">
                Whether inspecting converted datasets locally on Apple Silicon or tunneling into multi-node Slurm clusters hosting hundreds of gigabytes on GPFS, the desktop client provides an identical seamless workflow.
              </p>
            </div>

            <div className="space-y-3 font-mono text-xs">
              <div className="p-3.5 rounded-xl bg-glass border border-hairline">
                <strong className="text-primary block mb-1">Zero Credentials Stored</strong>
                <span className="text-secondary">
                  Uses system ssh-agent and ControlMaster tunnels. Passwords and secret keys are never handled or persisted by the app.
                </span>
              </div>
              <div className="p-3.5 rounded-xl bg-glass border border-hairline">
                <strong className="text-primary block mb-1">Slurm Job Management</strong>
                <span className="text-secondary">
                  Submits <code>coperceive-desktop-&lt;uuid&gt;</code> sbatch jobs, negotiates free ports, monitors walltime leases, and auto-cleans on exit.
                </span>
              </div>
              <div className="p-3.5 rounded-xl bg-glass border border-hairline">
                <strong className="text-primary block mb-1">macOS arm64 & Linux Builds</strong>
                <span className="text-secondary">
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
