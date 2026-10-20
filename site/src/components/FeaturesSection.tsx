import React from "react";

export const FeaturesSection: React.FC = () => {
  const features = [
    {
      title: "9-Camera Multi-View Frustums",
      desc: "Simultaneous 4-camera ground vehicle and 5-camera aerial drone frustum projections with ground footprints.",
      badge: "Aerial + Ground",
    },
    {
      title: "OpenDRIVE HD Map Integration",
      desc: "Lane centres, road boundaries, and intersection geometry parsed directly from OpenDRIVE (0.0137 m median residual).",
      badge: "0.0137m Precision",
    },
    {
      title: "Subprocess Plugin Protocol (ADR 0011)",
      desc: "Integrate custom cooperative models using clean newline-delimited JSON over standard I/O streams.",
      badge: "Language Agnostic",
    },
    {
      title: "Slurm HPC Cluster Integration",
      desc: "Spawn headless perception jobs on Slurm GPU clusters with automatic SSH ControlMaster tunnels and token security.",
      badge: "Slurm + SSH",
    },
    {
      title: "Arrow + Binary Point Storage",
      desc: "Instant random access for machine learning loaders and web clients via typed binary buffers.",
      badge: "Zero-Copy Parquet",
    },
    {
      title: "Cross-Platform Desktop & Web",
      desc: "High-performance WebGL client in browser and native Tauri v2 desktop app for macOS Apple Silicon & Linux.",
      badge: "Tauri v2 + WebGL",
    },
  ];

  return (
    <section className="py-24 border-t border-[#1e2638] bg-[#0d121c]/40">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="max-w-3xl mb-16">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-purple-950/60 border border-purple-500/40 text-purple-300 text-xs font-mono mb-4">
            <span className="w-2 h-2 rounded-full bg-purple-400" />
            Capabilities
          </div>
          <h2 className="text-3xl sm:text-4xl font-bold tracking-tight text-white mb-4">
            Built for Cooperative Robotics Research
          </h2>
          <p className="text-base sm:text-lg text-zinc-400 leading-relaxed">
            From single-command dataset conversion to interactive multi-agent evaluation at scale.
          </p>
        </div>

        {/* Feature Cards Grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {features.map((f, i) => (
            <div
              key={i}
              className="bg-[#111724] border border-[#232e42] hover:border-[#3b4a6b] rounded-xl p-6 transition flex flex-col justify-between"
            >
              <div>
                <div className="inline-block px-2 py-0.5 rounded text-[11px] font-mono bg-zinc-800 text-cyan-300 border border-zinc-700 mb-3">
                  {f.badge}
                </div>
                <h3 className="text-lg font-semibold text-zinc-100 mb-2">
                  {f.title}
                </h3>
                <p className="text-xs text-zinc-400 leading-relaxed">
                  {f.desc}
                </p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
};
