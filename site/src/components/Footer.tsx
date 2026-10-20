import React from "react";

export const Footer: React.FC = () => {
  return (
    <footer className="border-t border-[#1e2638] bg-[#0a0d14] py-12 text-zinc-400 text-xs font-mono">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 space-y-8">
        <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-6">
          <div className="space-y-2">
            <div className="flex items-center gap-2 text-zinc-200 font-bold text-sm">
              <span className="w-5 h-5 rounded bg-cyan-400 flex items-center justify-center p-0.5 text-[#0a0d14]">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" className="w-3.5 h-3.5">
                  <circle cx="12" cy="12" r="3" />
                  <path d="M3 12a9 9 0 0 1 18 0" />
                </svg>
              </span>
              coperceive
            </div>
            <p className="text-zinc-400 max-w-md font-sans text-xs">
              Open-source multi-agent cooperative perception framework. Bridging ground vehicles, aerial drones, and cooperative robotics models in one unified coordinate world.
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-6 text-zinc-300">
            <a href="https://github.com/adarsh-nl/coperceive" target="_blank" rel="noreferrer" className="hover:text-white transition">
              GitHub Repository
            </a>
            <a href="https://github.com/adarsh-nl/coperceive/blob/main/LICENSE" target="_blank" rel="noreferrer" className="hover:text-white transition">
              MIT License
            </a>
            <a href="https://github.com/adarsh-nl/coperceive/blob/main/THIRD_PARTY_NOTICES.md" target="_blank" rel="noreferrer" className="hover:text-white transition">
              Third-Party Notices
            </a>
          </div>
        </div>

        <div className="pt-6 border-t border-[#1e2638] text-[11px] text-zinc-400 space-y-2">
          <p>
            CARLA OpenDRIVE HD Map assets are distributed under the Creative Commons Attribution (CC-BY) License (CARLA Simulator, carla.org).
          </p>
          <p>
            Griffin benchmark dataset is referenced under its published academic terms (Wang et al., 2025). No proprietary or unlicensed datasets are redistributed.
          </p>
          <p>© {new Date().getFullYear()} Adarsh van Ee and coperceive contributors. Released under the MIT License.</p>
        </div>
      </div>
    </footer>
  );
};
