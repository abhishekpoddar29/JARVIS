// frontend/src/components/HudStatusBar.tsx

import { useEffect, useState } from "react";
import type { OrbState } from "../types";

interface HudStatusBarProps {
  orbState: OrbState;
  researchProgress: string | null;
}

export function HudStatusBar({ orbState, researchProgress }: HudStatusBarProps) {
  const [displayText, setDisplayText] = useState("");
  
  useEffect(() => {
    if (researchProgress) {
      const prefix = researchProgress.startsWith("[Engineering Team]")
        ? "ENGINEERING TEAM ACTIVE"
        : "DEEP RESEARCH ACTIVE";
      setDisplayText(`> ${prefix}: ${researchProgress.toUpperCase()}`);
    } else {
      switch (orbState) {
        case "processing":
          setDisplayText("> PROCESSING REQUEST...");
          break;
        case "processing_code":
          setDisplayText("> EXECUTING CODE IN SANDBOX...");
          break;
        case "speaking":
          setDisplayText("> GENERATING RESPONSE...");
          break;
        case "listening":
          setDisplayText("> LISTENING...");
          break;
        case "error":
          setDisplayText("> SYSTEM ERROR. AWAITING RETRY...");
          break;
        case "sleeping":
          setDisplayText("> CORE SYSTEMS IDLE...");
          break;
        default:
          setDisplayText("> CORE SYSTEMS ONLINE. STANDING BY.");
      }
    }
  }, [orbState, researchProgress]);

  return (
    <div className="hud-status-bar">
      <span className="hud-cursor">▌</span>
      <span className="hud-text">{displayText}</span>
    </div>
  );
}