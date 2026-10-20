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
    <section id="architecture" className="py-24 border-t border-hairline bg-canvas">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="max-w-3xl mb-16">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-glass border border-hairline text-secondary text-xs font-mono mb-4">
            <span className="w-1.5 h-1.5 rounded-full bg-uav" />
            System Architecture
          </div>
          <h2 className="text-3xl sm:text-4xl font-semibold tracking-tight text-primary mb-4">
            Engineered for Multi-Agent Robotics
          </h2>
          <p className="text-base sm:text-lg text-secondary leading-relaxed">
            From sensor ingress to real-time 3D web streaming, coperceive bridges raw multi-agent telemetry and modern cooperative machine learning.
          </p>
        </div>

        {/* Steps Grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
          {steps.map((step) => (
            <div
              key={step.num}
              className="group bg-glass backdrop-blur-22 hover:border-hairline-bright border border-hairline rounded-2xl p-8 transition flex flex-col justify-between"
            >
              <div>
                <div className="flex items-center justify-between mb-4">
                  <span className="text-xs font-mono font-medium text-secondary bg-glass border border-hairline px-2 py-0.5 rounded">
                    STEP {step.num}
                  </span>
                  <span className="text-xs font-mono text-tertiary">
                    {step.tag}
                  </span>
                </div>
                <h3 className="text-xl font-semibold text-primary mb-3 group-hover:text-uav transition">
                  {step.title}
                </h3>
                <p className="text-sm text-secondary leading-relaxed mb-6">
                  {step.description}
                </p>
              </div>

              {/* Code Snippet */}
              <div className="bg-canvas border border-hairline rounded-xl p-3 font-mono text-xs text-primary overflow-x-auto">
                <span className="text-tertiary select-none">$ </span>
                <code>{step.codeSnippet}</code>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
};
