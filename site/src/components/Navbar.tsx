import React from "react";

export const Navbar: React.FC = () => {
  return (
    <header className="sticky top-0 z-50 w-full bg-glass backdrop-blur-22 border-b border-hairline">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-14 flex items-center justify-between">
        {/* Brand */}
        <a
          href="#"
          className="flex items-center gap-2.5 text-primary font-semibold text-sm tracking-tight hover:opacity-90 transition"
        >
          <span className="w-6 h-6 rounded-md bg-gradient-to-br from-vehicle to-uav flex items-center justify-center p-0.5 text-black">
            <svg
              className="w-4 h-4"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <circle cx="12" cy="12" r="3" />
              <path d="M3 12a9 9 0 0 1 18 0" />
              <path d="M7 12a5 5 0 0 1 10 0" />
            </svg>
          </span>
          <span className="font-mono text-sm font-bold text-primary">
            coperceive
          </span>
          <span className="text-[10px] uppercase font-mono px-1.5 py-0.5 rounded bg-glass text-secondary border border-hairline">
            v0.1
          </span>
        </a>

        {/* Desktop Nav */}
        <nav className="hidden md:flex items-center gap-6 text-xs text-secondary">
          <a href="#occlusion" className="hover:text-primary transition">
            Occlusion
          </a>
          <a href="#architecture" className="hover:text-primary transition">
            Architecture
          </a>
          <a href="#datasets" className="hover:text-primary transition">
            Datasets
          </a>
          <a href="#desktop" className="hover:text-primary transition">
            Desktop App
          </a>
          <a href="#quickstart" className="hover:text-primary transition">
            Quick Start
          </a>
        </nav>

        {/* Action CTAs */}
        <div className="flex items-center gap-2.5">
          <a
            href="https://github.com/adarsh-nl/coperceive"
            target="_blank"
            rel="noreferrer"
            className="flex items-center gap-2 px-3 py-1.5 text-xs font-mono text-secondary hover:text-primary bg-glass hover:bg-glass/90 border border-hairline hover:border-hairline-bright rounded-full transition"
            aria-label="GitHub repository"
          >
            <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="currentColor">
              <path
                fillRule="evenodd"
                clipRule="evenodd"
                d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.53 1.032 1.53 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z"
              />
            </svg>
            <span>GitHub</span>
          </a>

          <a
            href="#quickstart"
            className="hidden sm:inline-flex items-center justify-center px-3.5 py-1.5 text-xs font-medium rounded-full text-black bg-primary hover:bg-primary/90 transition"
          >
            Install
          </a>
        </div>
      </div>
    </header>
  );
};
