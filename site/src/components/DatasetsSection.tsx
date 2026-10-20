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
      statusColor: "text-ok bg-ok/10 border-ok/30",
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
      statusColor: "text-warn bg-warn/10 border-warn/30",
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
      statusColor: "text-warn bg-warn/10 border-warn/30",
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
      statusColor: "text-lanes bg-lanes/10 border-lanes/30",
      notes: "Extracted lane centres and edges with 0.0137 m median residual alignment.",
    },
  ];

  return (
    <section id="datasets" className="py-24 border-t border-hairline bg-canvas">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="max-w-3xl mb-16">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-glass border border-hairline text-secondary text-xs font-mono mb-4">
            <span className="w-1.5 h-1.5 rounded-full bg-uav" />
            Dataset Ecosystem
          </div>
          <h2 className="text-3xl sm:text-4xl font-semibold tracking-tight text-primary mb-4">
            Supported Benchmarks & Licensing
          </h2>
          <p className="text-base sm:text-lg text-secondary leading-relaxed">
            coperceive converts heterogeneous dataset formats into a unified, high-speed storage schema.
            We uphold strict compliance with all upstream academic licenses.
          </p>
        </div>

        {/* Datasets Table */}
        <div className="overflow-x-auto rounded-2xl border border-hairline bg-glass backdrop-blur-22">
          <table className="w-full text-left text-xs font-mono">
            <thead className="bg-canvas/60 text-secondary uppercase tracking-wider border-b border-hairline">
              <tr>
                <th className="py-3.5 px-4 font-normal">Dataset</th>
                <th className="py-3.5 px-4 font-normal">Platform & Sensors</th>
                <th className="py-3.5 px-4 font-normal">License Status</th>
                <th className="py-3.5 px-4 font-normal">Integration Support</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-hairline text-secondary">
              {datasets.map((d) => (
                <tr key={d.name} className="hover:bg-white/[0.02] transition">
                  <td className="py-4 px-4 align-top">
                    <div className="font-semibold text-sm text-primary">{d.name}</div>
                    <div className="text-[11px] text-tertiary mt-0.5">{d.paper}</div>
                    <div className="text-[11px] text-secondary font-sans mt-1 max-w-xs">{d.notes}</div>
                  </td>
                  <td className="py-4 px-4 align-top">
                    <div className="text-primary">{d.platform}</div>
                    <div className="text-tertiary text-[11px] mt-0.5">{d.sensors}</div>
                  </td>
                  <td className="py-4 px-4 align-top">
                    <span className="text-secondary">{d.license}</span>
                  </td>
                  <td className="py-4 px-4 align-top">
                    <span className={`inline-block px-2.5 py-0.5 rounded-full border text-[11px] ${d.statusColor}`}>
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
