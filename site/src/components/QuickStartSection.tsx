import React, { useState } from "react";

export const QuickStartSection: React.FC = () => {
  const [copiedIndex, setCopiedIndex] = useState<number | null>(null);

  const snippets = [
    {
      title: "1. Install via pip",
      cmd: "pip install git+https://github.com/adarsh-nl/coperceive",
      comment: "# Core package with Arrow & SE(3) transforms",
    },
    {
      title: "2. Download Dev Subset (Griffin-25m)",
      cmd: "coperceive download griffin --subset 25m --preset dev --dest ~/data/raw --accept-griffin-terms",
      comment: "# Downloads curated 5-scene dev split with 9 cameras + LiDAR",
    },
    {
      title: "3. Convert to Unified Parquet Schema",
      cmd: "coperceive convert griffin --subset 25m --raw-root ~/data/raw/griffin_50scenes_25m/griffin-release --out ~/data/scenes",
      comment: "# Generates zero-copy parquets, binary sweeps & HD map bounds",
    },
    {
      title: "4. Launch Local Interactive Server",
      cmd: "coperceive serve --root ~/data/scenes",
      comment: "# Starts HTTP & WebGL server with automatic token auth on 127.0.0.1",
    },
  ];

  const handleCopy = (text: string, index: number) => {
    navigator.clipboard.writeText(text);
    setCopiedIndex(index);
    setTimeout(() => setCopiedIndex(null), 2000);
  };

  return (
    <section id="quickstart" className="py-24 border-t border-[#1e2638] bg-[#0a0d14]">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="max-w-3xl mb-16">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-cyan-950/60 border border-cyan-500/40 text-cyan-300 text-xs font-mono mb-4">
            <span className="w-2 h-2 rounded-full bg-cyan-400" />
            Quick Start
          </div>
          <h2 className="text-3xl sm:text-4xl font-bold tracking-tight text-white mb-4">
            Get Started in 4 Commands
          </h2>
          <p className="text-base sm:text-lg text-zinc-400 leading-relaxed">
            Install coperceive, download the Griffin-25m evaluation subset, convert to unified parquet format, and start inspecting multi-agent scenes in your browser or desktop viewer.
          </p>
        </div>

        {/* Command list */}
        <div className="space-y-4 max-w-4xl">
          {snippets.map((item, index) => (
            <div
              key={index}
              className="bg-[#111724] border border-[#232e42] rounded-xl p-4 sm:p-5 flex flex-col gap-2 relative group hover:border-[#3b4a6b] transition"
            >
              <div className="flex items-center justify-between">
                <span id={`cmd-title-${index}`} className="text-xs font-mono font-semibold text-zinc-300">
                  {item.title}
                </span>
                <button
                  onClick={() => handleCopy(item.cmd, index)}
                  className="px-2.5 py-1 text-xs font-mono rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 transition flex items-center gap-1.5 focus-visible:ring-1 focus-visible:ring-cyan-400"
                  aria-describedby={`cmd-title-${index}`}
                >
                  {copiedIndex === index ? (
                    <>
                      <svg className="w-3.5 h-3.5 text-emerald-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M5 13l4 4L19 7" />
                      </svg>
                      <span className="text-emerald-400">Copied</span>
                    </>
                  ) : (
                    <>
                      <svg className="w-3.5 h-3.5 text-zinc-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                      </svg>
                      <span>Copy</span>
                    </>
                  )}
                </button>
              </div>

              <div className="bg-[#0a0d14] rounded-lg p-3 font-mono text-xs sm:text-sm text-cyan-300 overflow-x-auto border border-[#1e2638]">
                <div className="text-zinc-400 select-none text-[11px] mb-1">{item.comment}</div>
                <div className="flex items-center">
                  <span className="text-zinc-500 select-none mr-2">$</span>
                  <code>{item.cmd}</code>
                </div>
              </div>
            </div>
          ))}
        </div>

        {/* Desktop Download Notice */}
        <div className="mt-8 p-6 rounded-xl bg-[#111724] border border-[#232e42] max-w-4xl flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
          <div>
            <h3 className="text-sm font-semibold text-zinc-200">Looking for the Desktop App (.dmg / .deb)?</h3>
            <p className="text-xs text-zinc-400 mt-0.5">
              Tauri v2 desktop bundles with bundled hash-pinned Python runtime.
            </p>
          </div>
          <span className="inline-block px-3 py-1.5 rounded-lg bg-zinc-800 border border-zinc-700 text-xs font-mono text-zinc-400 whitespace-nowrap">
            [TODO: GitHub Release asset]
          </span>
        </div>
      </div>
    </section>
  );
};
