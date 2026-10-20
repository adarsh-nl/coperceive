import React, { useState } from "react";

export const OcclusionSection: React.FC = () => {
  const [viewTab, setViewTab] = useState<"cav" | "uav" | "coop">("coop");

  return (
    <section id="occlusion" className="py-24 border-t border-hairline bg-canvas">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        {/* Section Header */}
        <div className="max-w-3xl mb-12">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-glass border border-hairline text-warn text-xs font-mono mb-4">
            <span className="w-1.5 h-1.5 rounded-full bg-warn" />
            Empirical Benchmark Case (Town03-000)
          </div>
          <h2 className="text-3xl sm:text-4xl font-semibold tracking-tight text-primary mb-4">
            Solving the Single-Agent Blind Spot
          </h2>
          <p className="text-base sm:text-lg text-secondary leading-relaxed">
            Even vehicle LiDAR cannot see through physical obstacles or around road corners.
            When a pedestrian steps into a blind intersection, single-agent autonomy is limited to immediate line-of-sight reaction time.
          </p>
        </div>

        {/* Real Occlusion Case Study Grid */}
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-start">
          {/* Left: Case study data card */}
          <div className="lg:col-span-5 bg-glass backdrop-blur-22 border border-hairline rounded-2xl p-6 flex flex-col gap-6">
            <div>
              <div className="text-xs font-mono text-tertiary uppercase tracking-wider mb-1">
                Authentic Griffin-25m Event
              </div>
              <h3 className="text-xl font-semibold text-primary">
                Pedestrian Track 265 at Blind Corner
              </h3>
            </div>

            {/* Metrics Table */}
            <div className="space-y-3 font-mono text-xs">
              <div className="flex justify-between py-2 border-b border-hairline">
                <span className="text-tertiary">Dataset & Scenario</span>
                <span className="text-primary">Griffin-25m / Town03-000</span>
              </div>
              <div className="flex justify-between py-2 border-b border-hairline">
                <span className="text-tertiary">Timestamp</span>
                <span className="text-primary num">1,123,700,000 µs (1123.7s)</span>
              </div>
              <div className="flex justify-between py-2 border-b border-hairline">
                <span className="text-tertiary">Distance to CAV</span>
                <span className="text-warn font-semibold num">17.5 m</span>
              </div>
              <div className="flex justify-between py-2 border-b border-hairline">
                <span className="text-tertiary">Ground CAV Visibility</span>
                <span className="text-warn font-medium">0 returns (Blind spot)</span>
              </div>
              <div className="flex justify-between py-2 border-b border-hairline">
                <span className="text-tertiary">Aerial UAV Visibility</span>
                <span className="text-primary font-medium">1.00 <span className="text-secondary text-[11px]">(Clear aerial view)</span></span>
              </div>
              <div className="flex justify-between py-2">
                <span className="text-tertiary">Map Lane Residual</span>
                <span className="text-lanes font-medium num">0.0137 m (median)</span>
              </div>
            </div>

            {/* Factual Note */}
            <div className="p-4 rounded-xl bg-canvas border border-hairline text-xs text-secondary leading-relaxed">
              <strong className="text-primary block mb-1">Empirical Ground Truth:</strong>
              0 vehicle LiDAR returns on the pedestrian at this time; the UAV camera sees it (visibility 1.0).
              Simultaneously, the UAV hovering at 27m altitude detects the pedestrian with downward cameras, publishing the 3D bounding box into the shared cooperative representation.
            </div>
          </div>

          {/* Right: Interactive Perspective Comparison */}
          <div className="lg:col-span-7 bg-glass backdrop-blur-22 border border-hairline rounded-2xl p-6 flex flex-col gap-5">
            {/* Perspective Selector Tabs */}
            <div className="flex items-center gap-2 p-1 bg-canvas rounded-xl border border-hairline text-xs font-mono">
              <button
                onClick={() => setViewTab("cav")}
                className={`flex-1 py-2 px-3 rounded-lg text-center transition ${
                  viewTab === "cav"
                    ? "bg-vehicle/20 text-primary border border-vehicle/40"
                    : "text-secondary hover:text-primary"
                }`}
              >
                1. Ground CAV View
              </button>
              <button
                onClick={() => setViewTab("uav")}
                className={`flex-1 py-2 px-3 rounded-lg text-center transition ${
                  viewTab === "uav"
                    ? "bg-uav/20 text-primary border border-uav/40"
                    : "text-secondary hover:text-primary"
                }`}
              >
                2. Aerial UAV View
              </button>
              <button
                onClick={() => setViewTab("coop")}
                className={`flex-1 py-2 px-3 rounded-lg text-center transition ${
                  viewTab === "coop"
                    ? "bg-ok/20 text-primary border border-ok/40"
                    : "text-secondary hover:text-primary"
                }`}
              >
                3. Cooperative World
              </button>
            </div>

            {/* Visual Representation Area */}
            <div className="relative aspect-[16/9] bg-canvas rounded-xl border border-hairline overflow-hidden flex flex-col items-center justify-center p-6 text-center">
              {viewTab === "cav" && (
                <div className="space-y-4 max-w-md animate-fadeIn">
                  <div className="w-12 h-12 rounded-full bg-vehicle/10 border border-vehicle/30 flex items-center justify-center mx-auto text-vehicle">
                    <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M13.875 18.825A10.05 10.05 0 0112 19c-4.478 0-8.268-2.943-9.543-7a9.97 9.97 0 011.563-3.029m5.858.908a3 3 0 114.243 4.243M9.878 9.878l4.242 4.242M9.88 9.88l-3.29-3.29m7.532 7.532l3.29 3.29M3 3l18 18" />
                    </svg>
                  </div>
                  <div>
                    <h3 className="text-base font-semibold text-primary">Ground CAV Perspective</h3>
                    <p className="text-xs text-secondary mt-1">
                      0 vehicle LiDAR returns on the pedestrian target at this time.
                    </p>
                  </div>
                  <div className="inline-block px-3 py-1.5 bg-glass border border-warn/40 rounded-full text-xs font-mono text-primary">
                    Track 265 Status: <strong>BLIND SPOT (0 points)</strong>
                  </div>
                </div>
              )}

              {viewTab === "uav" && (
                <div className="space-y-4 max-w-md animate-fadeIn">
                  <div className="w-12 h-12 rounded-full bg-uav/10 border border-uav/30 flex items-center justify-center mx-auto text-uav">
                    <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
                    </svg>
                  </div>
                  <div>
                    <h3 className="text-base font-semibold text-primary">Aerial Drone Perspective</h3>
                    <p className="text-xs text-secondary mt-1">
                      Downward Camera 4 looks directly into crosswalk from 27m altitude.
                    </p>
                  </div>
                  <div className="inline-block px-3 py-1.5 bg-glass border border-uav/40 rounded-full text-xs font-mono text-primary">
                    Track 265 Status: <strong>DETECTED (Visibility 1.00)</strong>
                  </div>
                </div>
              )}

              {viewTab === "coop" && (
                <div className="space-y-4 max-w-md animate-fadeIn">
                  <div className="w-12 h-12 rounded-full bg-ok/10 border border-ok/30 flex items-center justify-center mx-auto text-ok">
                    <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                    </svg>
                  </div>
                  <div>
                    <h3 className="text-base font-semibold text-primary">coperceive Cooperative World</h3>
                    <p className="text-xs text-secondary mt-1">
                      Ground pose and aerial detection unified into shared coordinate frame via SE(3) transforms.
                    </p>
                  </div>
                  <div className="inline-block px-3 py-1.5 bg-glass border border-ok/40 rounded-full text-xs font-mono text-primary">
                    Track 265 Status: <strong>SHARED WITH CAV (17.5m notice)</strong>
                  </div>
                </div>
              )}
            </div>

            {/* Bottom summary bar */}
            <div className="flex flex-wrap items-center justify-between text-xs font-mono text-secondary gap-2 pt-2 border-t border-hairline">
              <span>Latency overhead: <strong className="text-primary">~10 ms</strong> server response</span>
              <span>Alignment error: <strong className="text-lanes num">0.0137 m</strong></span>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
};
