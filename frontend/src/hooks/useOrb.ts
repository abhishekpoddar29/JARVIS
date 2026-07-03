// frontend/src/hooks/useOrb.ts
// Derives orb display state from raw socket state + connection status.

import { useMemo } from "react";
import { resolveOrbState, type OrbState } from "../components/Orb/OrbStates";

interface UseOrbOptions {
  rawState: string;
  isConnected: boolean;
  isLoadingSession: boolean;
  hasError: boolean;
}

interface UseOrbResult {
  orbState: OrbState;
  stateLabel: string;
}

export function useOrb({
  rawState,
  isConnected,
  isLoadingSession,
  hasError,
}: UseOrbOptions): UseOrbResult {
  const orbState = useMemo<OrbState>(() => {
    if (isLoadingSession) return "sleeping";
    if (hasError) return "error";
    if (!isConnected) return "sleeping";
    return resolveOrbState(rawState);
  }, [rawState, isConnected, isLoadingSession, hasError]);

  const stateLabel = useMemo(() => {
    if (isLoadingSession) return "Restoring session...";
    if (hasError) return "Connection failed";
    if (!isConnected) return "Connecting...";
    switch (orbState) {
      case "processing": return "Processing";
      case "speaking":   return "Speaking";
      case "listening":  return "Listening";
      case "sleeping":   return "Sleeping";
      case "waking":     return "Waking";
      case "error":      return "Needs attention";
      default:           return "Idle";
    }
  }, [orbState, isConnected, isLoadingSession, hasError]);

  return { orbState, stateLabel };
}