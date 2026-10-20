import React, { useState } from "react";

export const CitationSection: React.FC = () => {
  const [copiedKey, setCopiedKey] = useState<string | null>(null);

  const bibtexCoperceive = `@software{coperceive2026,
  title = {coperceive: Cooperative Perception Framework for Connected Autonomous Vehicles and Drones},
  author = {van Ee, Adarsh and Contributors},
  year = {2026},
  url = {https://github.com/adarsh-nl/coperceive},
  license = {MIT}
}`;

  const bibtexGriffin = `@article{wang2025griffin,
  title = {Griffin: Aerial-Ground Cooperative Detection and Tracking Dataset and Benchmark},
  author = {Wang, Y. and et al.},
  journal = {arXiv preprint arXiv:2503.06983},
  year = {2025}
}`;

  const handleCopy = (text: string, key: string) => {
    navigator.clipboard.writeText(text);
    setCopiedKey(key);
    setTimeout(() => setCopiedKey(null), 2000);
  };

  return (
    <section className="py-24 border-t border-[#1e2638] bg-[#0d121c]/40">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="max-w-3xl mb-12">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-zinc-800 border border-zinc-700 text-zinc-300 text-xs font-mono mb-4">
            Academic Attribution
          </div>
          <h2 className="text-3xl font-bold tracking-tight text-white mb-3">
            Citation & Attribution
          </h2>
          <p className="text-sm sm:text-base text-zinc-400">
            If you use coperceive in your research, please cite the framework and the upstream benchmark datasets.
          </p>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 max-w-6xl">
          {/* Coperceive BibTeX */}
          <div className="bg-[#111724] border border-[#232e42] rounded-xl p-5 flex flex-col justify-between">
            <div className="flex items-center justify-between mb-3">
              <span className="text-xs font-mono font-semibold text-zinc-300">
                coperceive Framework
              </span>
              <button
                onClick={() => handleCopy(bibtexCoperceive, "coperceive")}
                className="px-2 py-1 text-xs font-mono rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 transition"
              >
                {copiedKey === "coperceive" ? "Copied" : "Copy BibTeX"}
              </button>
            </div>
            <pre
              tabIndex={0}
              aria-label="coperceive BibTeX citation code"
              className="bg-[#0a0d14] rounded-lg p-3 text-xs font-mono text-zinc-300 overflow-x-auto border border-[#1e2638] focus:outline-none focus-visible:ring-1 focus-visible:ring-cyan-500"
            >
              {bibtexCoperceive}
            </pre>
          </div>

          {/* Griffin BibTeX */}
          <div className="bg-[#111724] border border-[#232e42] rounded-xl p-5 flex flex-col justify-between">
            <div className="flex items-center justify-between mb-3">
              <span className="text-xs font-mono font-semibold text-zinc-300">
                Griffin Benchmark (Wang et al., 2025)
              </span>
              <button
                onClick={() => handleCopy(bibtexGriffin, "griffin")}
                className="px-2 py-1 text-xs font-mono rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 transition"
              >
                {copiedKey === "griffin" ? "Copied" : "Copy BibTeX"}
              </button>
            </div>
            <pre
              tabIndex={0}
              aria-label="Griffin BibTeX citation code"
              className="bg-[#0a0d14] rounded-lg p-3 text-xs font-mono text-zinc-300 overflow-x-auto border border-[#1e2638] focus:outline-none focus-visible:ring-1 focus-visible:ring-cyan-500"
            >
              {bibtexGriffin}
            </pre>
          </div>
        </div>
      </div>
    </section>
  );
};
