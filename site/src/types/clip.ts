export interface ClipManifest {
  scene_id: string;
  dataset_name: string;
  dataset_paper: string;
  platform: string;
  subset: string;
  origin: [number, number, number];
  scale_cm: number;
  time_start_us: number;
  time_step_us: number;
  num_frames: number;
  duration_s: number;
  fps: number;
  agents: {
    vehicle: {
      id: string;
      label: string;
      type: string;
      color: string;
      sensors: Record<string, string>;
    };
    drone: {
      id: string;
      label: string;
      type: string;
      color: string;
      altitude_m: number;
      sensors: Record<string, string>;
    };
  };
  occlusion_event: {
    time_us: number;
    frame_index: number;
    track_id: string;
    category: string;
    distance_m: number;
    description: string;
  };
  stats: {
    num_polylines: number;
    num_frames: number;
    total_lidar_points: number;
    avg_lidar_points_per_frame: number;
    file_size_bytes: number;
    gzip_size_bytes: number;
  };
  attributions: {
    dataset: string;
    map: string;
    software: string;
  };
}

export interface BoxRecord {
  trackId: number;
  agentFlag: number; // 1 = vehicle, 2 = drone, 3 = both
  category: number;  // 0 = car, 1 = pedestrian, 2 = cyclist, 3 = other
  center: [number, number, number];
  size: [number, number, number];
  rotation: [number, number, number, number]; // qx, qy, qz, qw
}

export interface FrameData {
  timeUs: number;
  vehiclePose: {
    position: [number, number, number];
    quaternion: [number, number, number, number];
  };
  dronePose: {
    position: [number, number, number];
    quaternion: [number, number, number, number];
  };
  lidarPoints: Float32Array; // xyz floats
  boxes: BoxRecord[];
}

export interface ParsedClip {
  manifest: ClipManifest;
  origin: [number, number, number];
  mapPositions: Float32Array;
  mapColors: Float32Array;
  frames: FrameData[];
}
