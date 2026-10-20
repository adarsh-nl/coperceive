import React, { useState } from "react";

export const OcclusionSection: React.FC = () => {
  const [viewTab, setViewTab] = useState<"cav" | "uav" | "coop">("coop");

  return (
    <section id="occlusion" className="py-24 border-t border-[#1e2638] bg-[#0d121c]/50">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        {/* Section Header */}
        <div className="max-w-3xl mb-12">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-red-950/60 border border-red-500/40 text-red-300 text-xs font-mono mb-4">
            <span className="w-2 h-2 rounded-full bg-red-400" />
            Empirical Benchmark Case (Town03-000)
          </div>
          <h2 className="text-3xl sm:text-4xl font-bold tracking-tight text-white mb-4">
            Solving the Single-Agent Blind Spot
          </h2>
          <p className="text-base sm:text-lg text-zinc-400 leading-relaxed">
            Even high-end 64-channel LiDAR cannot see through buildings, parked trucks, or road corners.
            When a pedestrian steps into a blind intersection, ground autonomy is limited to line-of-sight reaction time.
          </p>
        </div>

        {/* Real Occlusion Case Study Grid */}
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-start">
          {/* Left: Case study data card */}
          <div className="lg:col-span-5 bg-[#111724] border border-[#232e42] rounded-xl p-6 flex flex-col gap-6">
            <div>
              <div className="text-xs font-mono text-zinc-400 uppercase tracking-wider mb-1">
                Authentic Griffin-25m Event
              </div>
              <h3 className="text-xl font-semibold text-zinc-100">
                Pedestrian #265 at Blind Corner
              </h3>
            </div>

            {/* Metrics Table */}
            <div className="space-y-3 font-mono text-xs">
              <div className="flex justify-between py-2 border-b border-[#1e2638]">
                <span className="text-zinc-400">Dataset & Scenario</span>
                <span className="text-zinc-200">Griffin-25m / Town03-000</span>
              </div>
              <div className="flex justify-between py-2 border-b border-[#1e2638]">
                <span className="text-zinc-400">Timestamp</span>
                <span className="text-zinc-200 num">1,123,700,000 µs (1123.7s)</span>
              </div>
              <div className="flex justify-between py-2 border-b border-[#1e2638]">
                <span className="text-zinc-400">Distance to CAV</span>
                <span className="text-red-400 font-semibold num">17.5 m</span>
              </div>
              <div className="flex justify-between py-2 border-b border-[#1e2638]">
                <span className="text-zinc-400">Ground CAV Visibility</span>
                <span className="text-red-400 font-medium">0.00 (Occluded by corner)</span>
              </div>
              <div className="flex justify-between py-2 border-b border-[#1e2638]">
                <span className="text-zinc-400">Aerial UAV Visibility</span>
                <span className="text-purple-400 font-medium">0.86 (Clear aerial view)</span>
              </div>
              <div className="flex justify-between py-2">
                <span className="text-zinc-400">Map Lane Error</span>
                <span className="text-emerald-400 font-medium num">0.0137 m (median residual)</span>
              </div>
            </div>

            {/* Analysis Note */}
            <div className="p-4 rounded-lg bg-[#0e131d] border border-[#1e2638] text-xs text-zinc-400 leading-relaxed">
              <strong className="text-zinc-200 block mb-1">The Physics of Occlusion:</strong>
              The ground vehicle travels east along the main road. The corner building completely intercepts the vehicle's 16-channel LiDAR sweep.
              Simultaneously, the UAV hovering at 27m altitude captures the pedestrian with downward camera rays, publishing the 3D bounding box to the shared cooperative perception world.
            </div>
          </div>

          {/* Right: Interactive Perspective Comparison */}
          <div className="lg:col-span-7 bg-[#111724] border border-[#232e42] rounded-xl p-6 flex flex-col gap-5">
            {/* Perspective Selector Tabs */}
            <div className="flex items-center gap-2 p-1 bg-[#0a0d14] rounded-lg border border-[#1e2638] text-xs font-mono">
              <button
                onClick={() => setViewTab("cav")}
                className={`flex-1 py-2 px-3 rounded text-center transition ${
                  viewTab === "cav"
                    ? "bg-cyan-500/20 text-cyan-300 border border-cyan-500/40"
                    : "text-zinc-400 hover:text-zinc-200"
                }`}
              >
                1. Ground CAV View
              </button>
              <button
                onClick={() => setViewTab("uav")}
                className={`flex-1 py-2 px-3 rounded text-center transition ${
                  viewTab === "uav"
                    ? "bg-purple-500/20 text-purple-300 border border-purple-500/40"
                    : "text-zinc-400 hover:text-zinc-200"
                }`}
              >
                2. Aerial UAV View
              </button>
              <button
                onClick={() => setViewTab("coop")}
                className={`flex-1 py-2 px-3 rounded text-center transition ${
                  viewTab === "coop"
                    ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/40"
                    : "text-zinc-400 hover:text-zinc-200"
                }`}
              >
                3. Cooperative World
              </button>
            </div>

            {/* Visual Representation Area */}
            <div className="relative aspect-[16/9] bg-[#0a0d14] rounded-lg border border-[#1e2638] overflow-hidden flex flex-col items-center justify-center p-6 text-center">
              {viewTab === "cav" && (
                <div className="space-y-4 max-w-md animate-fadeIn">
                  <div className="w-12 h-12 rounded-full bg-cyan-500/10 border border-cyan-500/30 flex items-center justify-center mx-auto text-cyan-400">
                    <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M13.875 18.825A10.05 10.05 0 0112 19c-4.478 0-8.268-2.943-9.543-7a9.97 9.97 0 011.563-3.029m5.858.908a3 3 0 114.243 4.243M9.878 9.878l4.242 4.242M9.88 9.88l-3.29-3.29m7.532 7.532l3.29 3.29M3 3l18 18" />
                    </svg>
                  </div>
                  <div>
                    <h3 className="text-base font-semibold text-zinc-100">Ground CAV Perspective</h3>
                    <p className="text-xs text-zinc-400 mt-1">
                      LiDAR rays terminate on building brick wall at x = -98m.
                    </p>
                  </div>
                  <div className="inline-block px-3 py-1.5 bg-red-950/60 border border-red-700/50 rounded text-xs font-mono text-red-300">
                    Track 265 Status: <strong>BLIND SPOT (0 points)</strong>
                  </div>
                </div>
              )}

              {viewTab === "uav" && (
                <div className="space-y-4 max-w-md animate-fadeIn">
                  <div className="w-12 h-12 rounded-full bg-purple-500/10 border border-purple-500/30 flex items-center justify-center mx-auto text-purple-400">
                    <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
                    </svg>
                  </div>
                  <div>
                    <h3 className="text-base font-semibold text-zinc-100">Aerial Drone Perspective</h3>
                    <p className="text-xs text-zinc-400 mt-1">
                      Downward Camera 4 looks directly into crosswalk from 27m altitude.
                    </p>
                  </div>
                  <div className="inline-block px-3 py-1.5 bg-purple-950/60 border border-purple-700/50 rounded text-xs font-mono text-purple-300">
                    Track 265 Status: <strong>DETECTED (Visibility 0.86)</strong>
                  </div>
                </div>
              )}

              {viewTab === "coop" && (
                <div className="space-y-4 max-w-md animate-fadeIn">
                  <div className="w-12 h-12 rounded-full bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-center mx-auto text-emerald-400">
                    <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                    </svg>
                  </div>
                  <div>
                    <h3 className="text-base font-semibold text-zinc-100">coperceive Cooperative World</h3>
                    <p className="text-xs text-zinc-400 mt-1">
                      Ground pose and aerial detection unified into shared coordinate frame via SE(3) transforms.
                    </p>
                  </div>
                  <div className="inline-block px-3 py-1.5 bg-emerald-950/60 border border-emerald-700/50 rounded text-xs font-mono text-emerald-300">
                    Track 265 Status: <strong>SHARED WITH CAV (17.5m notice)</strong>
                  </div>
                </div>
              )}
            </div>

            {/* Bottom summary bar */}
            <div className="flex flex-wrap items-center justify-between text-xs font-mono text-zinc-400 gap-2 pt-2 border-t border-[#1e2638]">
              <span>Latency overhead: <strong className="text-zinc-200">~10 ms</strong> server response</span>
              <span>Alignment error: <strong className="text-emerald-400 num">0.0137 m</strong></span>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
};
