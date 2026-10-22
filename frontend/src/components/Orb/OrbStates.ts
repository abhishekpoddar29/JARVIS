// frontend/src/components/Orb/OrbStates.ts

import type { OrbState } from "../../types";

export type { OrbState } from "../../types";

export interface OrbStateConfig {
  label: string;
  cssClass: string;
  ariaLabel: string;
  primary: string;
  secondary: string;
  core: string;
  speed: number;
  intensity: number;
  pulse: number;
  particleOpacity: number;
  ringOpacity: number;
  distortion: number;
  jitter: number;
}

export const ORB_STATE_CONFIG: Record<OrbState, OrbStateConfig> = {
  sleeping: {
    label: "Sleeping",
    cssClass: "orb-sleeping",
    ariaLabel: "Jarvis is sleeping",
    primary: "#315d69",
    secondary: "#102c37",
    core: "#8cb8c1",
    speed: 0.16,
    intensity: 0.28,
    pulse: 0.008,
    particleOpacity: 0.2,
    ringOpacity: 0.22,
    distortion: 0.12,
    jitter: 0,
  },
  idle: {
    label: "Idle",
    cssClass: "orb-idle",
    ariaLabel: "Jarvis is idle and ready",
    primary: "#49d8e8",
    secondary: "#0e8297",
    core: "#d9fcff",
    speed: 0.55,
    intensity: 0.72,
    pulse: 0.016,
    particleOpacity: 0.62,
    ringOpacity: 0.68,
    distortion: 0.28,
    jitter: 0,
  },
  waking: {
    label: "Waking",
    cssClass: "orb-waking",
    ariaLabel: "Jarvis is waking up",
    primary: "#baf9ff",
    secondary: "#4addeb",
    core: "#ffffff",
    speed: 1.65,
    intensity: 1.15,
    pulse: 0.055,
    particleOpacity: 0.92,
    ringOpacity: 1,
    distortion: 0.82,
    jitter: 0,
  },
  listening: {
    label: "Listening",
    cssClass: "orb-listening",
    ariaLabel: "Jarvis is listening",
    primary: "#58f3c6",
    secondary: "#31cfe8",
    core: "#ebffff",
    speed: 0.95,
    intensity: 1,
    pulse: 0.042,
    particleOpacity: 0.82,
    ringOpacity: 0.9,
    distortion: 0.58,
    jitter: 0,
  },
  processing: {
    label: "Processing",
    cssClass: "orb-processing",
    ariaLabel: "Jarvis is processing your request",
    primary: "#f2a35f",
    secondary: "#42ddeb",
    core: "#fff3de",
    speed: 1.35,
    intensity: 1.05,
    pulse: 0.034,
    particleOpacity: 0.9,
    ringOpacity: 0.96,
    distortion: 0.82,
    jitter: 0,
  },
  processing_code: {
    label: "Executing Code",
    cssClass: "orb-processing-code",
    ariaLabel: "Jarvis is executing code",
    primary: "#ff784f",
    secondary: "#ffbd58",
    core: "#fff4e8",
    speed: 1.75,
    intensity: 1.16,
    pulse: 0.046,
    particleOpacity: 1,
    ringOpacity: 1,
    distortion: 1,
    jitter: 0,
  },
  speaking: {
    label: "Responding",
    cssClass: "orb-speaking",
    ariaLabel: "Jarvis is responding",
    primary: "#dafcff",
    secondary: "#69e5f3",
    core: "#ffffff",
    speed: 1.08,
    intensity: 1.12,
    pulse: 0.052,
    particleOpacity: 0.86,
    ringOpacity: 0.94,
    distortion: 0.66,
    jitter: 0,
  },
  error: {
    label: "Error",
    cssClass: "orb-error",
    ariaLabel: "Jarvis encountered an error",
    primary: "#ff5f57",
    secondary: "#d83d38",
    core: "#ffe0dc",
    speed: 0.42,
    intensity: 0.96,
    pulse: 0.028,
    particleOpacity: 0.74,
    ringOpacity: 0.78,
    distortion: 0.9,
    jitter: 0.028,
  },
};

export const BACKEND_STATE_MAP: Record<string, OrbState> = {
  idle: "idle",
  processing: "processing",
  processing_code: "processing_code",
  speaking: "speaking",
  listening: "listening",
  error: "error",
  sleeping: "sleeping",
  waking: "waking",
  thinking: "processing",
  responding: "speaking",
  generating: "speaking",
  active: "idle",
};

export function resolveOrbState(raw: string): OrbState {
  return BACKEND_STATE_MAP[raw] ?? "idle";
}