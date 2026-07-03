// frontend/src/components/Orb/OrbStates.ts

export type OrbState =
  | "idle"
  | "processing"
  | "processing_code"
  | "speaking"
  | "listening"
  | "error"
  | "sleeping"
  | "waking";

export interface OrbStateConfig {
  label: string;
  cssClass: string;
  ariaLabel: string;
  /** Hex color for the new ring + wireframe layer, picked to match each state's existing core-sphere palette in styles.css */
  ringColor: string;
  /** 0–1 opacity multiplier for the ring + wireframe layer */
  intensity: number;
  /** Rotation speed multiplier for the ring wobble + wireframe spin */
  speed: number;
}

export const ORB_STATE_CONFIG: Record<OrbState, OrbStateConfig> = {
  idle: {
    label: "Idle",
    cssClass: "orb-idle",
    ariaLabel: "Jarvis is idle and ready",
    ringColor: "#6fd8ea",
    intensity: 0.35,
    speed: 1,
  },
  processing: {
    label: "Processing",
    cssClass: "orb-processing",
    ariaLabel: "Jarvis is processing your request",
    ringColor: "#53e0f4",
    intensity: 0.55,
    speed: 1.8,
  },
  processing_code: {
    label: "Executing Code",
    cssClass: "orb-processing-code",
    ariaLabel: "Jarvis is executing code",
    ringColor: "#53e0f4",
    intensity: 0.75,
    speed: 2.5, // Spins faster when running code
  },
  speaking: {
    label: "Speaking",
    cssClass: "orb-speaking",
    ariaLabel: "Jarvis is responding",
    ringColor: "#7fe6fb",
    intensity: 0.65,
    speed: 1.4,
  },
  listening: {
    label: "Listening",
    cssClass: "orb-listening",
    ariaLabel: "Jarvis is listening",
    ringColor: "#ffb37a",
    intensity: 0.95,
    speed: 2.2,
  },
  error: {
    label: "Error",
    cssClass: "orb-error",
    ariaLabel: "Jarvis encountered an error",
    ringColor: "#ff8a65",
    intensity: 0.5,
    speed: 0.6,
  },
  sleeping: {
    label: "Sleeping",
    cssClass: "orb-sleeping",
    ariaLabel: "Jarvis is sleeping",
    ringColor: "#3d6470",
    intensity: 0.08,
    speed: 0.25,
  },
  waking: {
    label: "Waking",
    cssClass: "orb-waking",
    ariaLabel: "Jarvis is waking up",
    ringColor: "#bdf4ff",
    intensity: 0.9,
    speed: 3,
  },
};

// Backend → frontend state mapping
// Handles any backend orb_state strings that differ from frontend names
export const BACKEND_STATE_MAP: Record<string, OrbState> = {
  idle: "idle",
  processing: "processing",
  processing_code: "processing_code",
  speaking: "speaking",
  listening: "listening",
  error: "error",
  sleeping: "sleeping",
  waking: "waking",
  // aliases backend might send
  thinking: "processing",
  responding: "speaking",
  active: "idle",
};

export function resolveOrbState(raw: string): OrbState {
  return BACKEND_STATE_MAP[raw] ?? "idle";
}