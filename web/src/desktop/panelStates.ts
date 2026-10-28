// SPDX-License-Identifier: Apache-2.0

export type PanelStateKind = "empty" | "loading" | "error";

export interface PanelStateDef {
  kind: PanelStateKind;
  title: string;
  description: string;
  actionLabel: string;
}

export type PanelId =
  | "connect"
  | "library"
  | "get_data"
  | "inspector"
  | "camera_strip"
  | "telemetry"
  | "predictions"
  | "hpc_workspace";

export const PANEL_DEFINITIONS: Record<PanelId, Record<PanelStateKind, PanelStateDef>> = {
  connect: {
    empty: {
      kind: "empty",
      title: "No dataset selected",
      description: "Choose a folder on your computer containing cooperative perception scenes to connect.",
      actionLabel: "Choose Folder",
    },
    loading: {
      kind: "loading",
      title: "Connecting to dataset",
      description: "Starting local background services and loading scene indices.",
      actionLabel: "Cancel",
    },
    error: {
      kind: "error",
      title: "Connection failed",
      description: "Unable to start the local service with the chosen folder.",
      actionLabel: "Choose another folder",
    },
  },
  library: {
    empty: {
      kind: "empty",
      title: "Your library is empty",
      description: "Download benchmark datasets or import local scenes to populate your library.",
      actionLabel: "Explore Catalog",
    },
    loading: {
      kind: "loading",
      title: "Loading library scenes",
      description: "Scanning stored datasets and validating scene records.",
      actionLabel: "Refresh",
    },
    error: {
      kind: "error",
      title: "Unable to load library",
      description: "Could not read scenes from the local library directory.",
      actionLabel: "Retry",
    },
  },
  get_data: {
    empty: {
      kind: "empty",
      title: "No datasets available",
      description: "There are currently no benchmark datasets in the catalog.",
      actionLabel: "Import Local Folder",
    },
    loading: {
      kind: "loading",
      title: "Loading dataset catalog",
      description: "Fetching the latest benchmark datasets and presets from the repository.",
      actionLabel: "Reload Catalog",
    },
    error: {
      kind: "error",
      title: "Failed to load catalog",
      description: "Unable to retrieve the dataset catalog from the server.",
      actionLabel: "Try Again",
    },
  },
  inspector: {
    empty: {
      kind: "empty",
      title: "No object selected",
      description: "Select an agent or bounding box to inspect properties and distances.",
      actionLabel: "Reset Selection",
    },
    loading: {
      kind: "loading",
      title: "Loading object details",
      description: "Computing relative velocity and cross-agent distances.",
      actionLabel: "Cancel",
    },
    error: {
      kind: "error",
      title: "Unable to inspect object",
      description: "Telemetry data for this object could not be resolved from the current frame.",
      actionLabel: "Deselect",
    },
  },
  camera_strip: {
    empty: {
      kind: "empty",
      title: "No cameras found",
      description: "This scene does not contain camera sensors or recorded video feeds.",
      actionLabel: "Hide Strip",
    },
    loading: {
      kind: "loading",
      title: "Loading camera feeds",
      description: "Retrieving camera sensor frustums and sample frames.",
      actionLabel: "Reload",
    },
    error: {
      kind: "error",
      title: "Camera feed unavailable",
      description: "Unable to decode image frames for the active camera sensors.",
      actionLabel: "Retry",
    },
  },
  telemetry: {
    empty: {
      kind: "empty",
      title: "No telemetry data",
      description: "No active agents with pose or speed records in this frame.",
      actionLabel: "Show All Agents",
    },
    loading: {
      kind: "loading",
      title: "Loading telemetry",
      description: "Calculating agent velocities, headings, and lidar returns.",
      actionLabel: "Refresh",
    },
    error: {
      kind: "error",
      title: "Telemetry unavailable",
      description: "Could not read pose coordinates or trajectory history for this sample.",
      actionLabel: "Reset View",
    },
  },
  predictions: {
    empty: {
      kind: "empty",
      title: "No prediction runs",
      description: "There are no prediction runs for this scene.",
      actionLabel: "New Evaluation",
    },
    loading: {
      kind: "loading",
      title: "Loading prediction runs",
      description: "Scanning for cooperative perception evaluations and model outputs.",
      actionLabel: "Refresh",
    },
    error: {
      kind: "error",
      title: "Unevaluated predictions",
      description: "Prediction boxes were imported but have not been evaluated against ground truth.",
      actionLabel: "Evaluate Now",
    },
  },
  hpc_workspace: {
    empty: {
      kind: "empty",
      title: "No servers configured",
      description: "Add your remote cluster to view available partitions, GPUs, and remote datasets.",
      actionLabel: "Add Server",
    },
    loading: {
      kind: "loading",
      title: "Loading server profiles",
      description: "Reading configured remote HPC clusters and connection profiles.",
      actionLabel: "Refresh",
    },
    error: {
      kind: "error",
      title: "Failed to load servers",
      description: "Could not read saved server profiles from local storage.",
      actionLabel: "Try Again",
    },
  },
};
