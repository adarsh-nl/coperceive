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
    <section className="py-24 border-t border-hairline bg-canvas">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="max-w-3xl mb-16">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-glass border border-hairline text-secondary text-xs font-mono mb-4">
            <span className="w-1.5 h-1.5 rounded-full bg-lanes" />
            Capabilities
          </div>
          <h2 className="text-3xl sm:text-4xl font-semibold tracking-tight text-primary mb-4">
            Built for Cooperative Robotics Research
          </h2>
          <p className="text-base sm:text-lg text-secondary leading-relaxed">
            From single-command dataset conversion to interactive multi-agent evaluation at scale.
          </p>
        </div>

        {/* Feature Cards Grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {features.map((f, i) => (
            <div
              key={i}
              className="bg-glass backdrop-blur-22 border border-hairline hover:border-hairline-bright rounded-2xl p-6 transition flex flex-col justify-between"
            >
              <div>
                <div className="inline-block px-2.5 py-0.5 rounded-full text-[11px] font-mono bg-canvas border border-hairline text-secondary mb-3">
                  {f.badge}
                </div>
                <h3 className="text-lg font-semibold text-primary mb-2">
                  {f.title}
                </h3>
                <p className="text-xs text-secondary leading-relaxed">
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
