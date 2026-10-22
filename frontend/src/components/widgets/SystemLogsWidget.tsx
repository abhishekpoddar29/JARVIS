import { useEffect, useRef } from "react";
import { Activity, TerminalSquare } from "lucide-react";

import type { SystemLogEntry } from "../../types";

interface SystemLogsWidgetProps {
  logs: SystemLogEntry[];
  isConnected: boolean;
}

function formatLogTime(timestamp: string): string {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return "--:--:--";

  return new Intl.DateTimeFormat("en-IN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

export function SystemLogsWidget({
  logs,
  isConnected,
}: SystemLogsWidgetProps) {
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const list = listRef.current;
    if (list) list.scrollTop = list.scrollHeight;
  }, [logs]);

  return (
    <section className="hud-widget system-logs-widget">
      <header className="system-logs-header">
        <div>
          <div className="widget-eyebrow">
            <TerminalSquare size={12} /> SYSTEM ACTIVITY
          </div>
          <h2>Runtime log</h2>
        </div>
        <span
          className={`system-link-state ${
            isConnected ? "online" : "offline"
          }`}
        >
          <Activity size={12} />
          {isConnected ? "LINKED" : "OFFLINE"}
        </span>
      </header>

      <div className="system-log-list" ref={listRef}>
        {logs.length === 0 ? (
          <div className="system-log-empty">No runtime events.</div>
        ) : (
          logs.map((entry) => (
            <div
              key={entry.id}
              className={`system-log-line level-${entry.level}`}
            >
              <time>{formatLogTime(entry.timestamp)}</time>
              <span className="system-log-source">
                [{entry.source}]
              </span>
              <span className="system-log-message">
                {entry.message}
              </span>
            </div>
          ))
        )}
      </div>
    </section>
  );
}