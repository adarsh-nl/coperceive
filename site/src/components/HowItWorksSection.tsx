import React from "react";

export const HowItWorksSection: React.FC = () => {
  const steps = [
    {
      num: "01",
      title: "Unified Spatial Calibration",
      description:
        "Every vehicle, drone, and roadside unit operates in a unified SE(3) world coordinate frame with microsecond timestamps and sub-centimeter map alignment (0.0137 m median residual on Griffin-25m).",
      tag: "SE(3) Transforms",
      codeSnippet: "world_from_sensor = pose.compose(sensor.body_from_sensor)",
    },
    {
      num: "02",
      title: "High-Throughput Storage",
      description:
        "Zero-copy Apache Arrow Parquet metadata indexes combined with contiguous float32 binary buffers (points.bin). Fast random access for both deep learning DataLoaders and WebGL renderers.",
      tag: "Arrow + Binary",
      codeSnippet: "read_points_at(path, agent_id='vehicle', time_us=t)",
    },
    {
      num: "03",
      title: "Subprocess Plugin Protocol",
      description:
        "Per ADR 0011, models communicate over a clean newline-delimited JSON subprocess protocol. Run custom PyTorch, TensorRT, or ONNX models without coupling to a specific inference framework.",
      tag: "ADR 0011 JSON Protocol",
      codeSnippet: '{"op": "predict", "time_us": 1123700000, "boxes": [...]}',
    },
    {
      num: "04",
      title: "Cooperative Spatial Evaluation",
      description:
        "Rigorous 3D bounding box matching, BEV IoU evaluation, and latency profiling (~10 ms server response on HPC). Ground-truth attribution maps which agent saw which target.",
      tag: "3D IoU / BEV mAP",
      codeSnippet: "coperceive eval --scene Town03-000 --metric bev_map",
    },
  ];

  return (
    <section id="architecture" className="py-24 border-t border-[#1e2638] bg-[#0a0d14]">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="max-w-3xl mb-16">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-cyan-950/60 border border-cyan-500/40 text-cyan-300 text-xs font-mono mb-4">
            <span className="w-2 h-2 rounded-full bg-cyan-400" />
            System Architecture
          </div>
          <h2 className="text-3xl sm:text-4xl font-bold tracking-tight text-white mb-4">
            Engineered for Multi-Agent Robotics
          </h2>
          <p className="text-base sm:text-lg text-zinc-400 leading-relaxed">
            From sensor ingress to real-time 3D web streaming, coperceive bridges raw multi-agent telemetry and modern cooperative machine learning.
          </p>
        </div>

        {/* Steps Grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
          {steps.map((step) => (
            <div
              key={step.num}
              className="group bg-[#111724] hover:bg-[#151c2a] border border-[#232e42] hover:border-[#3b4a6b] rounded-xl p-8 transition flex flex-col justify-between"
            >
              <div>
                <div className="flex items-center justify-between mb-4">
                  <span className="text-xs font-mono font-bold text-cyan-400 bg-cyan-950/40 border border-cyan-800/40 px-2 py-0.5 rounded">
                    STEP {step.num}
                  </span>
                  <span className="text-xs font-mono text-zinc-400">
                    {step.tag}
                  </span>
                </div>
                <h3 className="text-xl font-semibold text-zinc-100 mb-3 group-hover:text-cyan-300 transition">
                  {step.title}
                </h3>
                <p className="text-sm text-zinc-400 leading-relaxed mb-6">
                  {step.description}
                </p>
              </div>

              {/* Code Snippet */}
              <div className="bg-[#0a0d14] border border-[#1e2638] rounded-lg p-3 font-mono text-xs text-zinc-300 overflow-x-auto">
                <span className="text-zinc-600 select-none">$ </span>
                <code>{step.codeSnippet}</code>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
};
