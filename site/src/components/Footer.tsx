import React from "react";

export const Footer: React.FC = () => {
  return (
    <footer className="border-t border-hairline bg-canvas py-12 text-tertiary text-xs font-mono">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 space-y-8">
        <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-6">
          <div className="space-y-2">
            <div className="flex items-center gap-2 text-primary font-bold text-sm">
              <span className="w-5 h-5 rounded bg-gradient-to-br from-vehicle to-uav flex items-center justify-center p-0.5 text-black">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" className="w-3.5 h-3.5">
                  <circle cx="12" cy="12" r="3" />
                  <path d="M3 12a9 9 0 0 1 18 0" />
                </svg>
              </span>
              coperceive
            </div>
            <p className="text-secondary max-w-md font-sans text-xs">
              Open-source multi-agent cooperative perception framework. Bridging ground vehicles, aerial drones, and cooperative robotics models in one unified coordinate world.
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-6 text-secondary">
            <a href="https://github.com/adarsh-nl/coperceive" target="_blank" rel="noreferrer" className="hover:text-primary transition">
              GitHub Repository
            </a>
            <a href="https://github.com/adarsh-nl/coperceive/blob/main/LICENSE" target="_blank" rel="noreferrer" className="hover:text-primary transition">
              MIT License
            </a>
            <a href="https://github.com/adarsh-nl/coperceive/blob/main/THIRD_PARTY_NOTICES.md" target="_blank" rel="noreferrer" className="hover:text-primary transition">
              Third-Party Notices
            </a>
          </div>
        </div>

        <div className="pt-6 border-t border-hairline text-[11px] text-tertiary space-y-2">
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
