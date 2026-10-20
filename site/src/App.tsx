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
    <div className="min-h-screen bg-canvas text-primary flex flex-col font-sans selection:bg-uav/20 selection:text-primary">
      {/* Accessible skip link */}
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:top-4 focus:left-4 z-50 px-4 py-2 bg-primary text-black font-medium rounded-lg shadow-lg font-mono text-xs"
      >
        Skip to main content
      </a>

      <Navbar />

      <main id="main-content" className="flex-1">
        {/* Asymmetric Left-Aligned Hero Section */}
        <section className="relative pt-12 pb-20 md:pt-16 md:pb-28 overflow-hidden bg-canvas">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 space-y-10">
            {/* Left-Aligned Headline and Lead Copy */}
            <div className="max-w-3xl space-y-6 text-left">
              <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-glass border border-hairline text-xs font-mono text-secondary">
                <span className="w-1.5 h-1.5 rounded-full bg-uav animate-pulse" />
                <span>Multi-Agent Aerial-Ground Cooperative Perception</span>
              </div>

              <h1 className="text-4xl sm:text-5xl lg:text-6xl font-semibold tracking-tight text-primary leading-[1.1]">
                Cooperative Autonomous Perception for Connected Vehicles and Drones
              </h1>

              <p className="text-base sm:text-lg lg:text-xl text-secondary max-w-2xl font-sans leading-relaxed">
                A single unified 3D coordinate world where ground vehicle LiDAR sweeps, aerial drone cameras look down, and occluded blind spots are eliminated in real time.
              </p>

              {/* Action buttons */}
              <div className="flex flex-wrap items-center gap-3 pt-2">
                <a
                  href="#quickstart"
                  className="px-5 py-2.5 rounded-full text-xs font-medium text-black bg-primary hover:bg-primary/90 transition flex items-center gap-2"
                >
                  <span>Get Started in 4 Commands</span>
                  <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 9l-7 7-7-7" />
                  </svg>
                </a>
                <a
                  href="https://github.com/adarsh-nl/coperceive/blob/main/docs/viewer.md"
                  target="_blank"
                  rel="noreferrer"
                  className="px-5 py-2.5 rounded-full text-xs font-mono text-secondary hover:text-primary bg-glass hover:bg-glass/90 border border-hairline hover:border-hairline-bright transition flex items-center gap-2"
                >
                  <span>Open the viewer</span>
                  <svg className="w-3.5 h-3.5 text-tertiary" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                  </svg>
                </a>
                <a
                  href="#occlusion"
                  className="px-5 py-2.5 rounded-full text-xs font-mono text-secondary hover:text-primary bg-glass hover:bg-glass/90 border border-hairline hover:border-hairline-bright transition flex items-center gap-2"
                >
                  <span>Explore Town03-000 Case</span>
                  <svg className="w-3.5 h-3.5 text-uav" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 5l7 7-7 7" />
                  </svg>
                </a>
              </div>
            </div>

            {/* Scripted 3D Hero Film */}
            <div className="w-full">
              <HeroScene />
            </div>

            {/* Factual telemetry pills below hero */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 max-w-5xl pt-2">
              <div className="p-4 rounded-2xl bg-glass backdrop-blur-22 border border-hairline text-left">
                <div className="text-xs font-mono text-tertiary">Platform</div>
                <div className="text-sm font-semibold text-primary mt-0.5">CARLA–AirSim Sim</div>
              </div>
              <div className="p-4 rounded-2xl bg-glass backdrop-blur-22 border border-hairline text-left">
                <div className="text-xs font-mono text-tertiary">Total Cameras</div>
                <div className="text-sm font-semibold text-primary mt-0.5">9 (4 CAV + 5 UAV)</div>
              </div>
              <div className="p-4 rounded-2xl bg-glass backdrop-blur-22 border border-hairline text-left">
                <div className="text-xs font-mono text-tertiary">Map Alignment</div>
                <div className="text-sm font-semibold text-lanes num mt-0.5">0.0137 m residual</div>
              </div>
              <div className="p-4 rounded-2xl bg-glass backdrop-blur-22 border border-hairline text-left">
                <div className="text-xs font-mono text-tertiary">Plugin Protocol</div>
                <div className="text-sm font-semibold text-primary mt-0.5">ADR 0011 JSON</div>
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
