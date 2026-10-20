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
    <section className="py-24 border-t border-hairline bg-canvas">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="max-w-3xl mb-12">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-glass border border-hairline text-tertiary text-xs font-mono mb-4">
            Academic Attribution
          </div>
          <h2 className="text-3xl font-semibold tracking-tight text-primary mb-3">
            Citation & Attribution
          </h2>
          <p className="text-sm sm:text-base text-secondary">
            If you use coperceive in your research, please cite the framework and the upstream benchmark datasets.
          </p>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 max-w-6xl">
          {/* Coperceive BibTeX */}
          <div className="bg-glass backdrop-blur-22 border border-hairline rounded-2xl p-5 flex flex-col justify-between">
            <div className="flex items-center justify-between mb-3">
              <span className="text-xs font-mono font-medium text-primary">
                coperceive Framework
              </span>
              <button
                onClick={() => handleCopy(bibtexCoperceive, "coperceive")}
                className="px-2.5 py-1 text-xs font-mono rounded-full bg-canvas border border-hairline hover:border-hairline-bright text-secondary hover:text-primary transition"
              >
                {copiedKey === "coperceive" ? "Copied" : "Copy BibTeX"}
              </button>
            </div>
            <pre
              tabIndex={0}
              aria-label="coperceive BibTeX citation code"
              className="bg-canvas rounded-xl p-3 text-xs font-mono text-primary overflow-x-auto border border-hairline focus:outline-none focus-visible:ring-1 focus-visible:ring-uav"
            >
              {bibtexCoperceive}
            </pre>
          </div>

          {/* Griffin BibTeX */}
          <div className="bg-glass backdrop-blur-22 border border-hairline rounded-2xl p-5 flex flex-col justify-between">
            <div className="flex items-center justify-between mb-3">
              <span className="text-xs font-mono font-medium text-primary">
                Griffin Benchmark (Wang et al., 2025)
              </span>
              <button
                onClick={() => handleCopy(bibtexGriffin, "griffin")}
                className="px-2.5 py-1 text-xs font-mono rounded-full bg-canvas border border-hairline hover:border-hairline-bright text-secondary hover:text-primary transition"
              >
                {copiedKey === "griffin" ? "Copied" : "Copy BibTeX"}
              </button>
            </div>
            <pre
              tabIndex={0}
              aria-label="Griffin BibTeX citation code"
              className="bg-canvas rounded-xl p-3 text-xs font-mono text-primary overflow-x-auto border border-hairline focus:outline-none focus-visible:ring-1 focus-visible:ring-uav"
            >
              {bibtexGriffin}
            </pre>
          </div>
        </div>
      </div>
    </section>
  );
};
