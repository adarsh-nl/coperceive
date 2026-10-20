import React from "react";

export const DatasetsSection: React.FC = () => {
  const datasets = [
    {
      name: "Griffin",
      paper: "Wang et al., arXiv 2503.06983",
      fullTitle: "Griffin: Aerial-Ground Cooperative Detection and Tracking Dataset and Benchmark",
      platform: "CARLA–AirSim Simulation",
      sensors: "9 cameras (4 CAV + 5 UAV) + LiDAR",
      license: "Academic research terms",
      status: "First-class support (25m / 50m / 100m presets)",
      statusColor: "text-emerald-400 bg-emerald-950/40 border-emerald-800/40",
      notes: "High-density aerial-ground cooperative perception in complex urban intersections.",
    },
    {
      name: "OPV2V",
      paper: "Xu et al., IEEE ICRA 2022",
      fullTitle: "OPV2V: An Open Benchmark for Vehicle-to-Vehicle Cooperative Perception",
      platform: "CARLA Simulation",
      sensors: "Multi-vehicle LiDAR + 4 cameras/vehicle",
      license: "Licence not published; local research use only",
      status: "Converter supported (local research only)",
      statusColor: "text-amber-400 bg-amber-950/40 border-amber-800/40",
      notes: "No derived OPV2V data is bundled or hosted. Users convert their local copies directly.",
    },
    {
      name: "V2XSet",
      paper: "Xu et al., IEEE ECCV 2022",
      fullTitle: "V2X-ViT: Vehicle-to-Everything Cooperative Perception with Vision Transformer",
      platform: "CARLA Simulation",
      sensors: "Vehicle + RSU LiDAR & cameras",
      license: "Licence not published; local research use only",
      status: "Converter supported (local research only)",
      statusColor: "text-amber-400 bg-amber-950/40 border-amber-800/40",
      notes: "V2X infrastructure integration. Zero derived data redistributed.",
    },
    {
      name: "CARLA OpenDRIVE Maps",
      paper: "CARLA Simulator (Dosovitskiy et al.)",
      fullTitle: "OpenDRIVE HD Map lane geometry and road edge polylines (Town01–Town10)",
      platform: "OpenDRIVE XML",
      sensors: "Vector lane polylines & boundaries",
      license: "CC-BY License (CARLA Simulator, carla.org)",
      status: "Included under CC-BY License",
      statusColor: "text-cyan-400 bg-cyan-950/40 border-cyan-800/40",
      notes: "Extracted lane centres and edges with 0.0137 m median residual alignment.",
    },
  ];

  return (
    <section id="datasets" className="py-24 border-t border-[#1e2638] bg-[#0d121c]/50">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="max-w-3xl mb-16">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-cyan-950/60 border border-cyan-500/40 text-cyan-300 text-xs font-mono mb-4">
            <span className="w-2 h-2 rounded-full bg-cyan-400" />
            Dataset Ecosystem
          </div>
          <h2 className="text-3xl sm:text-4xl font-bold tracking-tight text-white mb-4">
            Supported Benchmarks & Licensing
          </h2>
          <p className="text-base sm:text-lg text-zinc-400 leading-relaxed">
            coperceive converts heterogeneous dataset formats into a unified, high-speed storage schema.
            We uphold strict compliance with all upstream academic licenses.
          </p>
        </div>

        {/* Datasets Table */}
        <div className="overflow-x-auto rounded-xl border border-[#232e42] bg-[#111724]">
          <table className="w-full text-left text-xs font-mono">
            <thead className="bg-[#151c2a] text-zinc-400 uppercase tracking-wider border-b border-[#232e42]">
              <tr>
                <th className="py-3.5 px-4">Dataset</th>
                <th className="py-3.5 px-4">Platform & Sensors</th>
                <th className="py-3.5 px-4">License Status</th>
                <th className="py-3.5 px-4">Integration Support</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#1e2638] text-zinc-300">
              {datasets.map((d) => (
                <tr key={d.name} className="hover:bg-[#151c2a]/60 transition">
                  <td className="py-4 px-4 align-top">
                    <div className="font-bold text-sm text-zinc-100">{d.name}</div>
                    <div className="text-[11px] text-zinc-400 mt-0.5">{d.paper}</div>
                    <div className="text-[11px] text-zinc-300 font-sans mt-1 max-w-xs">{d.notes}</div>
                  </td>
                  <td className="py-4 px-4 align-top">
                    <div className="text-zinc-200">{d.platform}</div>
                    <div className="text-zinc-400 text-[11px] mt-0.5">{d.sensors}</div>
                  </td>
                  <td className="py-4 px-4 align-top">
                    <span className="text-zinc-300">{d.license}</span>
                  </td>
                  <td className="py-4 px-4 align-top">
                    <span className={`inline-block px-2 py-0.5 rounded border text-[11px] ${d.statusColor}`}>
                      {d.status}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
};
