import React from "react";
import { Navbar } from "./components/Navbar";
import { HeroScene } from "./components/HeroScene";
import { OcclusionSection } from "./components/OcclusionSection";
import { HowItWorksSection } from "./components/HowItWorksSection";
import { FeaturesSection } from "./components/FeaturesSection";
import { DesktopSection } from "./components/DesktopSection";
import { DatasetsSection } from "./components/DatasetsSection";
import { QuickStartSection } from "./components/QuickStartSection";
import { CitationSection } from "./components/CitationSection";
import { Footer } from "./components/Footer";

export const App: React.FC = () => {
  return (
    <div className="min-h-screen bg-[#0a0d14] text-[#e6edf3] flex flex-col font-sans selection:bg-cyan-500/20 selection:text-cyan-200">
      {/* Accessible skip link */}
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:top-4 focus:left-4 z-50 px-4 py-2 bg-cyan-400 text-[#0a0d14] font-medium rounded-lg shadow-lg font-mono text-xs"
      >
        Skip to main content
      </a>

      <Navbar />

      <main id="main-content" className="flex-1">
        {/* Hero Section */}
        <section className="relative pt-12 pb-20 md:pt-16 md:pb-28 overflow-hidden">
          {/* Subtle background ambient radial gradient */}
          <div className="absolute top-0 left-1/2 -translate-x-1/2 w-[1000px] h-[500px] bg-gradient-to-b from-cyan-950/20 via-purple-950/10 to-transparent blur-3xl -z-10 pointer-events-none" />

          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 space-y-10">
            {/* Headline and Lead */}
            <div className="max-w-4xl space-y-5 text-center mx-auto">
              <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-[#111724] border border-[#232e42] text-xs font-mono text-zinc-300">
                <span className="w-2 h-2 rounded-full bg-cyan-400 animate-pulse" />
                <span>Multi-Agent Aerial-Ground Cooperative Perception</span>
              </div>

              <h1 className="text-4xl sm:text-5xl lg:text-6xl font-extrabold tracking-tight text-white leading-[1.12]">
                Cooperative Autonomous Perception for Connected Vehicles and Drones
              </h1>

              <p className="text-base sm:text-lg lg:text-xl text-zinc-400 max-w-2xl mx-auto font-sans leading-relaxed">
                A single unified 3D coordinate world where ground LiDAR sweeps, aerial drone cameras look down, and occluded blind spots are eliminated in real time.
              </p>

              {/* Action buttons */}
              <div className="flex flex-wrap items-center justify-center gap-4 pt-2">
                <a
                  href="#quickstart"
                  className="px-5 py-2.5 rounded-lg text-sm font-medium text-[#0a0d14] bg-cyan-400 hover:bg-cyan-300 transition shadow-[0_0_20px_rgba(34,211,238,0.25)] flex items-center gap-2"
                >
                  <span>Get Started in 4 Commands</span>
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 9l-7 7-7-7" />
                  </svg>
                </a>
                <a
                  href="#occlusion"
                  className="px-5 py-2.5 rounded-lg text-sm font-mono text-zinc-300 bg-[#151c2a] hover:bg-[#1b2336] border border-[#232e42] hover:border-[#3b4a6b] transition flex items-center gap-2"
                >
                  <span>Explore Town03-000 Case</span>
                  <svg className="w-4 h-4 text-cyan-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 5l7 7-7 7" />
                  </svg>
                </a>
              </div>
            </div>

            {/* Interactive 3D Hero Scene */}
            <div className="w-full">
              <HeroScene />
            </div>

            {/* Verified factual pills below hero */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 max-w-5xl mx-auto pt-2">
              <div className="p-3.5 rounded-xl bg-[#111724]/70 border border-[#1e2638] text-center">
                <div className="text-xs font-mono text-zinc-400">Platform</div>
                <div className="text-sm font-semibold text-zinc-200 mt-0.5">CARLA–AirSim Sim</div>
              </div>
              <div className="p-3.5 rounded-xl bg-[#111724]/70 border border-[#1e2638] text-center">
                <div className="text-xs font-mono text-zinc-400">Total Cameras</div>
                <div className="text-sm font-semibold text-zinc-200 mt-0.5">9 (4 CAV + 5 UAV)</div>
              </div>
              <div className="p-3.5 rounded-xl bg-[#111724]/70 border border-[#1e2638] text-center">
                <div className="text-xs font-mono text-zinc-400">Map Lane Alignment</div>
                <div className="text-sm font-semibold text-emerald-400 num mt-0.5">0.0137 m residual</div>
              </div>
              <div className="p-3.5 rounded-xl bg-[#111724]/70 border border-[#1e2638] text-center">
                <div className="text-xs font-mono text-zinc-400">Plugin Protocol</div>
                <div className="text-sm font-semibold text-cyan-400 mt-0.5">ADR 0011 JSON</div>
              </div>
            </div>
          </div>
        </section>

        {/* Section 2: Occlusion Deep Dive */}
        <OcclusionSection />

        {/* Section 3: How It Works & Architecture */}
        <HowItWorksSection />

        {/* Section 4: Capabilities Grid */}
        <FeaturesSection />

        {/* Section 5: Native Desktop App (Tauri v2) */}
        <DesktopSection />

        {/* Section 6: Datasets & Licensing Table */}
        <DatasetsSection />

        {/* Section 7: Quick Start CLI */}
        <QuickStartSection />

        {/* Section 8: Citation */}
        <CitationSection />
      </main>

      <Footer />
    </div>
  );
};
