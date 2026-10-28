// SPDX-License-Identifier: Apache-2.0
/** The HPC workspace: saved servers and the "New evaluation" run-plan panel. */
import { useState } from "react";
import HpcServers from "./HpcServers";
import NewEvaluation from "./NewEvaluation";

type WorkspaceTab = "servers" | "evaluation";

export default function HpcWorkspace() {
  const [tab, setTab] = useState<WorkspaceTab>("servers");
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
      </div>
      {tab === "servers" ? <HpcServers /> : <NewEvaluation />}
    </div>
  );
}
