import { useEffect, useMemo, useState } from "react";
import {
  Circle,
  CircleMarker,
  MapContainer,
  Polyline,
  TileLayer,
  useMap,
} from "react-leaflet";
import type { LatLngExpression } from "leaflet";
import {
  Crosshair,
  MapPin,
  Navigation,
  Radio,
  Square,
  Timer,
  X,
} from "lucide-react";
import "leaflet/dist/leaflet.css";

import type {
  TrackingLocationPoint,
  TrackingSessionState,
} from "../../types";

interface LiveLocationMapProps {
  session: TrackingSessionState;
  isConnected: boolean;
  onStop: (sessionId: string) => void;
  onDismiss: () => void;
}

type DisplayStatus =
  | TrackingSessionState["status"]
  | "STALE";

const TERMINAL_STATUSES = new Set([
  "DENIED",
  "ENDED",
  "EXPIRED",
]);

function formatDuration(seconds: number): string {
  const safeSeconds = Math.max(0, seconds);
  const minutes = Math.floor(safeSeconds / 60);
  const remainder = safeSeconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(
    remainder,
  ).padStart(2, "0")}`;
}

function formatLastUpdate(point?: TrackingLocationPoint): string {
  if (!point) return "Waiting for first GPS fix";

  const rawTimestamp =
    point.server_timestamp ?? point.timestamp;
  const date = new Date(rawTimestamp);
  if (Number.isNaN(date.getTime())) return "Just now";

  return new Intl.DateTimeFormat("en-IN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(date);
}

function FollowPosition({
  point,
}: {
  point: TrackingLocationPoint;
}) {
  const map = useMap();

  useEffect(() => {
    map.flyTo(
      [point.latitude, point.longitude],
      Math.max(map.getZoom(), 16),
      { duration: 0.8 },
    );
  }, [map, point.latitude, point.longitude]);

  return null;
}

function statusLabel(status: DisplayStatus): string {
  switch (status) {
    case "PENDING":
      return "Awaiting GPS";
    case "ACTIVE":
      return "Live";
    case "STALE":
      return "Signal stale";
    case "DENIED":
      return "Denied";
    case "ENDED":
      return "Stopped";
    case "EXPIRED":
      return "Expired";
  }
}

export function LiveLocationMap({
  session,
  isConnected,
  onStop,
  onDismiss,
}: LiveLocationMapProps) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const timer = window.setInterval(
      () => setNow(Date.now()),
      1_000,
    );
    return () => window.clearInterval(timer);
  }, []);

  const remainingSeconds = Math.max(
    0,
    Math.ceil(
      (new Date(session.expiresAt).getTime() - now) /
        1_000,
    ),
  );

  const latestPoint = session.latestPoint;
  const lastPointAt = latestPoint
    ? new Date(
        latestPoint.server_timestamp ??
          latestPoint.timestamp,
      ).getTime()
    : 0;

  const displayStatus: DisplayStatus =
    session.status === "ACTIVE" &&
    lastPointAt > 0 &&
    now - lastPointAt > 20_000
      ? "STALE"
      : remainingSeconds === 0 &&
          !TERMINAL_STATUSES.has(session.status)
        ? "EXPIRED"
        : session.status;

  const trail = useMemo<LatLngExpression[]>(
    () =>
      session.trail.map((point) => [
        point.latitude,
        point.longitude,
      ]),
    [session.trail],
  );

  const canStop =
    isConnected &&
    (session.status === "ACTIVE" ||
      session.status === "PENDING") &&
    remainingSeconds > 0;

  const canDismiss = TERMINAL_STATUSES.has(
    session.status,
  );

  return (
    <aside
      className={`live-location-widget status-${displayStatus.toLowerCase()}`}
      aria-label={`Live location for ${session.targetName}`}
    >
      <div className="widget-scan-line" aria-hidden="true" />

      <header className="live-location-header">
        <div>
          <div className="widget-eyebrow">
            <Radio size={12} /> LIVE LOCATION
          </div>
          <h2>{session.targetName}</h2>
        </div>

        <div className="live-location-header-actions">
          <span className={`tracking-status-chip status-${displayStatus.toLowerCase()}`}>
            <span className="tracking-status-dot" />
            {statusLabel(displayStatus)}
          </span>
          {canDismiss && (
            <button
              type="button"
              className="widget-icon-button"
              title="Close location widget"
              onClick={onDismiss}
            >
              <X size={15} />
            </button>
          )}
        </div>
      </header>

      <div className="live-map-frame">
        {latestPoint ? (
          <MapContainer
            key={session.sessionId}
            center={[
              latestPoint.latitude,
              latestPoint.longitude,
            ]}
            zoom={16}
            zoomControl={false}
            attributionControl
            scrollWheelZoom
          >
            <TileLayer
              attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
              url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            />
            <FollowPosition point={latestPoint} />

            <Circle
              center={[
                latestPoint.latitude,
                latestPoint.longitude,
              ]}
              radius={Math.max(latestPoint.accuracy, 1)}
              pathOptions={{
                color: "#65e6f4",
                fillColor: "#65e6f4",
                fillOpacity: 0.1,
                opacity: 0.45,
                weight: 1,
              }}
            />

            {trail.length > 1 && (
              <Polyline
                positions={trail}
                pathOptions={{
                  color: "#76f3d2",
                  opacity: 0.78,
                  weight: 3,
                }}
              />
            )}

            <CircleMarker
              center={[
                latestPoint.latitude,
                latestPoint.longitude,
              ]}
              radius={8}
              pathOptions={{
                color: "#e8fdff",
                fillColor: "#48dceb",
                fillOpacity: 1,
                opacity: 1,
                weight: 2,
              }}
            />
          </MapContainer>
        ) : (
          <div className="gps-awaiting-state">
            <div className="gps-radar" aria-hidden="true">
              <span />
              <span />
              <span />
              <Crosshair size={28} />
            </div>
            <strong>Consent received</strong>
            <p>Waiting for the browser’s first GPS position…</p>
          </div>
        )}
      </div>

      <div className="tracking-metrics-grid">
        <div className="tracking-metric">
          <Timer size={14} />
          <span>Remaining</span>
          <strong>{formatDuration(remainingSeconds)}</strong>
        </div>
        <div className="tracking-metric">
          <Crosshair size={14} />
          <span>Accuracy</span>
          <strong>
            {latestPoint
              ? `±${Math.round(latestPoint.accuracy)} m`
              : "—"}
          </strong>
        </div>
        <div className="tracking-metric tracking-metric-wide">
          <MapPin size={14} />
          <span>Last update</span>
          <strong>{formatLastUpdate(latestPoint)}</strong>
        </div>
        <div className="tracking-metric">
          <Navigation size={14} />
          <span>Heading</span>
          <strong>
            {latestPoint?.heading == null
              ? "—"
              : `${Math.round(latestPoint.heading)}°`}
          </strong>
        </div>
      </div>

      <footer className="live-location-footer">
        <span>
          {session.trail.length} route point
          {session.trail.length === 1 ? "" : "s"}
        </span>
        {canStop && (
          <button
            type="button"
            className="stop-tracking-button"
            onClick={() => onStop(session.sessionId)}
          >
            <Square size={12} fill="currentColor" />
            Stop tracking
          </button>
        )}
      </footer>
    </aside>
  );
}