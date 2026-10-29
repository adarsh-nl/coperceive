// SPDX-License-Identifier: Apache-2.0
/** The HPC workspace: saved servers and the "New evaluation" run-plan panel. */
import { useEffect, useState } from "react";
import HpcServers from "./HpcServers";
import NewEvaluation from "./NewEvaluation";
import RunHistory from "./RunHistory";

type WorkspaceTab = "servers" | "evaluation" | "history";

export default function HpcWorkspace() {
  const [tab, setTab] = useState<WorkspaceTab>(() => {
    if (typeof window !== "undefined") {
      try {
        if (sessionStorage.getItem("coperceive-hpc-tab") === "evaluation") {
          sessionStorage.removeItem("coperceive-hpc-tab");
          return "evaluation";
        }
      } catch {}
      if (window.location.hash.includes("evaluation")) {
        return "evaluation";
      }
    }
    return "servers";
  });

  useEffect(() => {
    const handleOpenEval = () => {
      setTab("evaluation");
    };
    window.addEventListener("coperceive-open-evaluation", handleOpenEval);
    return () => window.removeEventListener("coperceive-open-evaluation", handleOpenEval);
  }, []);
  const tabStyle = (active: boolean) =>
    ({
      padding: "0.45rem 0.9rem",
      borderRadius: "6px",
      border: "none",
      fontSize: "0.85rem",
      fontWeight: active ? 600 : 400,
      cursor: "pointer",
      backgroundColor: active ? "rgba(255, 255, 255, 0.12)" : "transparent",
      color: active ? "#fff" : "#a1a1a6",
    }) as const;
  return (
    <div>
      <div role="tablist" aria-label="HPC workspace sections" style={{ display: "flex", gap: "0.35rem", justifyContent: "center", marginBottom: "0.75rem" }}>
        <button type="button" role="tab" aria-selected={tab === "servers"} style={tabStyle(tab === "servers")} onClick={() => setTab("servers")}>
          Servers
        </button>
        <button type="button" role="tab" aria-selected={tab === "evaluation"} style={tabStyle(tab === "evaluation")} onClick={() => setTab("evaluation")}>
          New evaluation
        </button>
        <button type="button" role="tab" aria-selected={tab === "history"} style={tabStyle(tab === "history")} onClick={() => setTab("history")}>
          Run history
        </button>
      </div>
      {tab === "servers" ? <HpcServers /> : tab === "evaluation" ? <NewEvaluation /> : <RunHistory />}
    </div>
  );
}
