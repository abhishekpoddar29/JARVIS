import {
  Clock3,
  Heart,
  ListMusic,
  LoaderCircle,
  Music2,
  Move,
  Pause,
  Play,
  Plus,
  Search,
  SkipForward,
  Trash2,
  X,
} from "lucide-react";
import {
  FormEvent,
  PointerEvent as ReactPointerEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import type {
  MusicClientState,
  MusicCommand,
  MusicPlaybackState,
  MusicTrack,
} from "../../types";
import {
  API_URL,
  jarvisFetch,
} from "../../services/apiClient";


type MusicTab = "search" | "queue" | "favorites";


type WidgetPosition = {
  x: number;
  y: number;
};

const MUSIC_WIDGET_POSITION_KEY = "jarvis.musicWidget.position";
const MUSIC_FAVORITES_CACHE_KEY = "jarvis.musicWidget.favorites";
const MUSIC_WIDGET_VIEWPORT_MARGIN = 10;

function getDefaultWidgetPosition(): WidgetPosition {
  if (typeof window === "undefined") {
    return { x: 16, y: 78 };
  }

  const estimatedWidth = Math.min(410, window.innerWidth - 32);
  return {
    x: Math.max(
      MUSIC_WIDGET_VIEWPORT_MARGIN,
      window.innerWidth - estimatedWidth - 16,
    ),
    y: 78,
  };
}

function readSavedWidgetPosition(): WidgetPosition | null {
  if (typeof window === "undefined") return null;

  try {
    const raw = window.localStorage.getItem(
      MUSIC_WIDGET_POSITION_KEY,
    );
    if (!raw) return null;

    const parsed = JSON.parse(raw) as Partial<WidgetPosition>;
    if (
      typeof parsed.x !== "number" ||
      typeof parsed.y !== "number"
    ) {
      return null;
    }

    return { x: parsed.x, y: parsed.y };
  } catch {
    return null;
  }
}

function readCachedFavorites(): MusicTrack[] {
  if (typeof window === "undefined") return [];

  try {
    const raw = window.localStorage.getItem(
      MUSIC_FAVORITES_CACHE_KEY,
    );
    if (!raw) return [];

    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];

    return parsed.filter((track): track is MusicTrack => (
      Boolean(track) &&
      typeof track.video_id === "string" &&
      typeof track.title === "string" &&
      typeof track.channel_title === "string"
    ));
  } catch {
    return [];
  }
}

function persistCachedFavorites(tracks: MusicTrack[]): void {
  if (typeof window === "undefined") return;

  try {
    window.localStorage.setItem(
      MUSIC_FAVORITES_CACHE_KEY,
      JSON.stringify(tracks),
    );
  } catch {
    // Browser storage is an optional resilience layer.
  }
}


declare global {
  interface Window {
    YT?: any;
    onYouTubeIframeAPIReady?: () => void;
  }
}


let youtubeApiPromise: Promise<void> | null = null;

function loadYouTubeIframeApi(): Promise<void> {
  if (window.YT?.Player) {
    return Promise.resolve();
  }
  if (youtubeApiPromise) {
    return youtubeApiPromise;
  }

  youtubeApiPromise = new Promise<void>((resolve, reject) => {
    const existing = document.querySelector<HTMLScriptElement>(
      'script[src="https://www.youtube.com/iframe_api"]',
    );
    const previousReady = window.onYouTubeIframeAPIReady;

    window.onYouTubeIframeAPIReady = () => {
      previousReady?.();
      resolve();
    };

    if (existing) {
      const timeout = window.setTimeout(() => {
        if (window.YT?.Player) resolve();
        else reject(new Error("YouTube player API did not become ready."));
      }, 10_000);
      existing.addEventListener(
        "load",
        () => window.clearTimeout(timeout),
        { once: true },
      );
      return;
    }

    const script = document.createElement("script");
    script.src = "https://www.youtube.com/iframe_api";
    script.async = true;
    script.onerror = () => reject(
      new Error("Could not load the YouTube IFrame Player API."),
    );
    document.head.appendChild(script);
  });

  return youtubeApiPromise;
}


function formatDuration(seconds: number): string {
  if (!seconds) return "--:--";
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remaining = Math.floor(seconds % 60);
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(remaining).padStart(2, "0")}`;
  }
  return `${minutes}:${String(remaining).padStart(2, "0")}`;
}


async function requestJson<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const response = await jarvisFetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });

  if (!response.ok) {
    let message = `Request failed (${response.status}).`;
    try {
      const body = await response.json();
      message = body.detail ?? body.message ?? message;
    } catch {
      // Keep the HTTP fallback message.
    }
    throw new Error(message);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}


interface MusicPlayerWidgetProps {
  command: MusicCommand | null;
  trackingActive?: boolean;
  onStateChange: (state: MusicClientState) => void;
}


export function MusicPlayerWidget({
  command,
  trackingActive = false,
  onStateChange,
}: MusicPlayerWidgetProps) {
  const widgetRef = useRef<HTMLElement>(null);
  const playerHostRef = useRef<HTMLDivElement>(null);
  const playerRef = useRef<any>(null);
  const dragStateRef = useRef<{
    pointerId: number;
    offsetX: number;
    offsetY: number;
  } | null>(null);
  const playerReadyRef = useRef(false);
  const pendingTrackRef = useRef<MusicTrack | null>(null);
  const currentTrackRef = useRef<MusicTrack | null>(null);
  const ignoreEndedUntilRef = useRef(0);
  const alternativesRef = useRef<MusicTrack[]>([]);
  const playTrackRef = useRef<(track: MusicTrack, alternatives?: MusicTrack[]) => void>(() => undefined);
  const playNextRef = useRef<() => Promise<void>>(async () => undefined);
  const previousTracksRef = useRef<MusicTrack[]>([]);

  const [visible, setVisible] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const [widgetPosition, setWidgetPosition] = useState<WidgetPosition>(
    () => readSavedWidgetPosition() ?? getDefaultWidgetPosition(),
  );
  const [hasActivated, setHasActivated] = useState(false);
  const [currentTrack, setCurrentTrack] = useState<MusicTrack | null>(null);
  const [playbackState, setPlaybackState] = useState<MusicPlaybackState>("idle");
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<MusicTrack[]>([]);
  const [favorites, setFavorites] = useState<MusicTrack[]>(
    () => readCachedFavorites(),
  );
  const [queue, setQueue] = useState<MusicTrack[]>([]);
  const [activeTab, setActiveTab] = useState<MusicTab>("search");
  const [isSearching, setIsSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const clampWidgetPosition = useCallback((
    position: WidgetPosition,
  ): WidgetPosition => {
    const rect = widgetRef.current?.getBoundingClientRect();
    const width = rect?.width ?? Math.min(410, window.innerWidth - 20);
    const height = rect?.height ?? 420;

    const maxX = Math.max(
      MUSIC_WIDGET_VIEWPORT_MARGIN,
      window.innerWidth - width - MUSIC_WIDGET_VIEWPORT_MARGIN,
    );
    const maxY = Math.max(
      MUSIC_WIDGET_VIEWPORT_MARGIN,
      window.innerHeight - height - MUSIC_WIDGET_VIEWPORT_MARGIN,
    );

    return {
      x: Math.min(
        Math.max(MUSIC_WIDGET_VIEWPORT_MARGIN, position.x),
        maxX,
      ),
      y: Math.min(
        Math.max(MUSIC_WIDGET_VIEWPORT_MARGIN, position.y),
        maxY,
      ),
    };
  }, []);

  const persistWidgetPosition = useCallback((
    position: WidgetPosition,
  ) => {
    try {
      window.localStorage.setItem(
        MUSIC_WIDGET_POSITION_KEY,
        JSON.stringify(position),
      );
    } catch {
      // Position persistence is optional.
    }
  }, []);

  const startWidgetDrag = useCallback((
    event: ReactPointerEvent<HTMLElement>,
  ) => {
    if (event.button !== 0) return;

    const target = event.target as HTMLElement;
    if (target.closest("button, input, a")) return;

    const rect = widgetRef.current?.getBoundingClientRect();
    if (!rect) return;

    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    dragStateRef.current = {
      pointerId: event.pointerId,
      offsetX: event.clientX - rect.left,
      offsetY: event.clientY - rect.top,
    };
    setIsDragging(true);
  }, []);

  const moveWidget = useCallback((
    event: ReactPointerEvent<HTMLElement>,
  ) => {
    const dragState = dragStateRef.current;
    if (!dragState || dragState.pointerId !== event.pointerId) return;

    event.preventDefault();
    setWidgetPosition(
      clampWidgetPosition({
        x: event.clientX - dragState.offsetX,
        y: event.clientY - dragState.offsetY,
      }),
    );
  }, [clampWidgetPosition]);

  const stopWidgetDrag = useCallback((
    event: ReactPointerEvent<HTMLElement>,
  ) => {
    const dragState = dragStateRef.current;
    if (!dragState || dragState.pointerId !== event.pointerId) return;

    dragStateRef.current = null;
    setIsDragging(false);

    try {
      event.currentTarget.releasePointerCapture(event.pointerId);
    } catch {
      // Pointer capture may already be released by the browser.
    }

    setWidgetPosition((current) => {
      const clamped = clampWidgetPosition(current);
      persistWidgetPosition(clamped);
      return clamped;
    });
  }, [clampWidgetPosition, persistWidgetPosition]);

  useEffect(() => {
    if (!visible) return;

    const frame = window.requestAnimationFrame(() => {
      setWidgetPosition((current) => clampWidgetPosition(current));
    });

    const handleResize = () => {
      setWidgetPosition((current) => {
        const clamped = clampWidgetPosition(current);
        persistWidgetPosition(clamped);
        return clamped;
      });
    };

    window.addEventListener("resize", handleResize);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("resize", handleResize);
    };
  }, [clampWidgetPosition, persistWidgetPosition, visible]);

  const isFavorite = useMemo(
    () => Boolean(
      currentTrack &&
      favorites.some((track) => track.video_id === currentTrack.video_id),
    ),
    [currentTrack, favorites],
  );

  useEffect(() => {
    currentTrackRef.current = currentTrack;
  }, [currentTrack]);

  useEffect(() => {
    persistCachedFavorites(favorites);
  }, [favorites]);

  useEffect(() => {
    const cached = readCachedFavorites();
    if (cached.length === 0) return;

    let cancelled = false;

    const restoreCachedFavorites = async () => {
      try {
        for (const track of cached) {
          await requestJson<MusicTrack[]>(
            "/api/music/favorites",
            {
              method: "POST",
              body: JSON.stringify({ track }),
            },
          );
        }

        const restored = await requestJson<MusicTrack[]>(
          "/api/music/favorites",
        );
        if (!cancelled) setFavorites(restored);
      } catch (restoreError) {
        console.warn(
          "Could not restore cached music favorites:",
          restoreError,
        );
      }
    };

    void restoreCachedFavorites();
    return () => {
      cancelled = true;
    };
  }, []);

  const refreshLibrary = useCallback(async () => {
    try {
      const [nextFavorites, nextQueue] = await Promise.all([
        requestJson<MusicTrack[]>("/api/music/favorites"),
        requestJson<MusicTrack[]>("/api/music/queue"),
      ]);
      setFavorites((current) => (
        nextFavorites.length > 0 || current.length === 0
          ? nextFavorites
          : current
      ));
      setQueue(nextQueue);
    } catch (refreshError) {
      console.warn("Could not refresh music library:", refreshError);
    }
  }, []);

  const loadTrackIntoPlayer = useCallback((track: MusicTrack) => {
    pendingTrackRef.current = track;

    const player = playerRef.current;
    if (!playerReadyRef.current || !player) return;

    pendingTrackRef.current = null;
    ignoreEndedUntilRef.current = performance.now() + 1_500;

    try {
      player.loadVideoById(track.video_id, 0);
    } catch {
      player.loadVideoById({ videoId: track.video_id, startSeconds: 0 });
    }

    window.setTimeout(() => {
      const activePlayer = playerRef.current;
      if (!activePlayer) return;

      const videoData = activePlayer.getVideoData?.() ?? {};
      const loadedId = videoData.video_id ?? videoData.videoId;
      if (loadedId && loadedId !== track.video_id) {
        try {
          activePlayer.loadVideoById(track.video_id, 0);
        } catch {
          activePlayer.loadVideoById({
            videoId: track.video_id,
            startSeconds: 0,
          });
        }
      }
    }, 450);
  }, []);

  const playTrack = useCallback((
    track: MusicTrack,
    alternatives: MusicTrack[] = [],
  ) => {
    const previousTrack = currentTrackRef.current;
    if (
      previousTrack &&
      previousTrack.video_id !== track.video_id
    ) {
      previousTracksRef.current = [
        previousTrack,
        ...previousTracksRef.current,
      ].slice(0, 20);
    }

    currentTrackRef.current = track;
    setHasActivated(true);
    setVisible(true);
    setError(null);
    setCurrentTrack(track);
    setPlaybackState("loading");
    alternativesRef.current = alternatives.filter(
      (candidate) => candidate.video_id !== track.video_id,
    );
    loadTrackIntoPlayer(track);
  }, [loadTrackIntoPlayer]);

  playTrackRef.current = playTrack;

  const playNext = useCallback(async () => {
    setError(null);
    try {
      const nextTrack = await requestJson<MusicTrack | null>(
        "/api/music/queue/next",
        { method: "POST" },
      );
      if (!nextTrack) {
        setPlaybackState("idle");
        setQueue([]);
        return;
      }
      setQueue((current) => current.filter(
        (track) => track.video_id !== nextTrack.video_id,
      ));
      playTrack(nextTrack);
      void refreshLibrary();
    } catch (nextError) {
      setPlaybackState("error");
      setError(
        nextError instanceof Error
          ? nextError.message
          : "Could not play the next song.",
      );
    }
  }, [playTrack, refreshLibrary]);

  playNextRef.current = playNext;

  useEffect(() => {
    if (!hasActivated) return;

    let disposed = false;

    void loadYouTubeIframeApi()
      .then(() => {
        if (disposed || !playerHostRef.current || playerRef.current) {
          return;
        }

        playerRef.current = new window.YT.Player(
          playerHostRef.current,
          {
            width: "100%",
            height: "100%",
            playerVars: {
              controls: 1,
              playsinline: 1,
              rel: 0,
              origin: window.location.origin,
            },
            events: {
              onReady: () => {
                playerReadyRef.current = true;
                playerRef.current?.setVolume?.(70);
                const pending = pendingTrackRef.current;
                if (pending) {
                  loadTrackIntoPlayer(pending);
                }
              },
              onStateChange: (event: { data: number }) => {
                const stateMap: Record<number, MusicPlaybackState> = {
                  [-1]: "idle",
                  [0]: "idle",
                  [1]: "playing",
                  [2]: "paused",
                  [3]: "buffering",
                  [5]: "paused",
                };
                setPlaybackState(stateMap[event.data] ?? "idle");

                if (event.data === 1 || event.data === 3) {
                  ignoreEndedUntilRef.current = 0;
                }

                if (event.data === 0) {
                  if (performance.now() < ignoreEndedUntilRef.current) {
                    return;
                  }
                  void playNextRef.current();
                }
              },
              onError: () => {
                const alternative = alternativesRef.current.shift();
                if (alternative) {
                  playTrackRef.current(
                    alternative,
                    alternativesRef.current,
                  );
                  return;
                }
                setPlaybackState("error");
                setError(
                  "This video cannot be played here. Choose another result.",
                );
              },
              onAutoplayBlocked: () => {
                setPlaybackState("blocked");
              },
            },
          },
        );
      })
      .catch((apiError) => {
        if (disposed) return;
        setPlaybackState("error");
        setError(
          apiError instanceof Error
            ? apiError.message
            : "The YouTube player could not load.",
        );
      });

    return () => {
      disposed = true;
      playerReadyRef.current = false;
      try {
        playerRef.current?.destroy?.();
      } catch {
        // The iframe may already have been removed by React.
      }
      playerRef.current = null;
    };
  }, [hasActivated, loadTrackIntoPlayer]);

  useEffect(() => {
    if (!visible) return;
    void refreshLibrary();
  }, [visible, refreshLibrary]);

  useEffect(() => {
    if (!command) return;

    setHasActivated(true);

    if (command.favorites) {
      setFavorites(command.favorites);
    }
    if (command.queue) {
      setQueue(command.queue);
    }
    if (command.track && command.action === "open") {
      setCurrentTrack(command.track);
    }

    switch (command.action) {
      case "play":
        if (command.track) {
          playTrack(command.track, command.alternatives ?? []);
        }
        break;
      case "pause":
        playerRef.current?.pauseVideo?.();
        setPlaybackState("paused");
        setVisible(true);
        break;
      case "resume":
        setVisible(true);
        playerRef.current?.playVideo?.();
        break;
      case "next":
        setVisible(true);
        void playNext();
        break;
      case "previous": {
        const previous = previousTracksRef.current.shift();
        if (previous) playTrack(previous);
        break;
      }
      case "close":
        playerRef.current?.stopVideo?.();
        setPlaybackState("idle");
        setVisible(false);
        break;
      case "show_favorites":
        setVisible(true);
        setActiveTab("favorites");
        break;
      case "show_queue":
      case "queue_updated":
        setVisible(true);
        setActiveTab("queue");
        break;
      case "favorites_updated":
        setVisible(true);
        setActiveTab("favorites");
        break;
      case "open":
        setVisible(command.visible ?? true);
        break;
      default:
        if (command.visible !== undefined) {
          setVisible(command.visible);
        }
    }
  }, [command?.command_id, playNext, playTrack]);

  useEffect(() => {
    onStateChange({
      playback_state: playbackState,
      current_track: currentTrack,
      visible,
      position_seconds: Number(
        playerRef.current?.getCurrentTime?.() ?? 0,
      ),
      volume: Number(
        playerRef.current?.getVolume?.() ?? 70,
      ),
      updated_at: new Date().toISOString(),
    });
  }, [currentTrack, onStateChange, playbackState, visible]);

  const handleSearch = useCallback(async (
    event: FormEvent<HTMLFormElement>,
  ) => {
    event.preventDefault();
    const query = searchQuery.trim();
    if (!query || isSearching) return;

    setIsSearching(true);
    setError(null);
    setActiveTab("search");
    try {
      const result = await requestJson<{
        query: string;
        results: MusicTrack[];
      }>(`/api/music/search?q=${encodeURIComponent(query)}&limit=8`);
      setSearchResults(result.results);
      if (result.results.length === 0) {
        setError(`No playable result was found for “${query}”.`);
      }
    } catch (searchError) {
      setError(
        searchError instanceof Error
          ? searchError.message
          : "Music search failed.",
      );
    } finally {
      setIsSearching(false);
    }
  }, [isSearching, searchQuery]);

  const addFavorite = useCallback(async (track: MusicTrack) => {
    try {
      const next = await requestJson<MusicTrack[]>(
        "/api/music/favorites",
        {
          method: "POST",
          body: JSON.stringify({ track }),
        },
      );
      setFavorites(next);
    } catch (favoriteError) {
      setError(
        favoriteError instanceof Error
          ? favoriteError.message
          : "Could not update favorites.",
      );
    }
  }, []);

  const removeFavorite = useCallback(async (track: MusicTrack) => {
    try {
      const next = await requestJson<MusicTrack[]>(
        `/api/music/favorites/${encodeURIComponent(track.video_id)}`,
        { method: "DELETE" },
      );
      setFavorites(next);
    } catch (favoriteError) {
      setError(
        favoriteError instanceof Error
          ? favoriteError.message
          : "Could not update favorites.",
      );
    }
  }, []);

  const enqueue = useCallback(async (
    track: MusicTrack,
    position: "next" | "end" = "end",
  ) => {
    try {
      const next = await requestJson<MusicTrack[]>(
        "/api/music/queue",
        {
          method: "POST",
          body: JSON.stringify({ track, position }),
        },
      );
      setQueue(next);
      setActiveTab("queue");
    } catch (queueError) {
      setError(
        queueError instanceof Error
          ? queueError.message
          : "Could not update the queue.",
      );
    }
  }, []);

  const clearQueue = useCallback(async () => {
    try {
      await requestJson<void>("/api/music/queue", {
        method: "DELETE",
      });
      setQueue([]);
    } catch (queueError) {
      setError(
        queueError instanceof Error
          ? queueError.message
          : "Could not clear the queue.",
      );
    }
  }, []);

  const closeWidget = useCallback(() => {
    playerRef.current?.stopVideo?.();
    setPlaybackState("idle");
    setVisible(false);
  }, []);

  const togglePlayback = useCallback(() => {
    const player = playerRef.current;
    if (!player) return;

    const youtubeState = Number(player.getPlayerState?.() ?? -1);
    if (youtubeState === 1) {
      player.pauseVideo?.();
      setPlaybackState("paused");
      return;
    }

    player.playVideo?.();
    setPlaybackState("loading");
  }, []);

  const playCurrentTrack = useCallback(() => {
    const player = playerRef.current;
    if (!player) return;
    player.playVideo?.();
    setPlaybackState("loading");
  }, []);

  const renderTrackRow = (
    track: MusicTrack,
    source: MusicTab,
  ) => (
    <div className="music-track-row" key={`${source}-${track.video_id}`}>
      <button
        type="button"
        className="music-track-main"
        onClick={(event) => {
          event.preventDefault();
          event.stopPropagation();
          playTrack(track);
        }}
        title={`Play ${track.title}`}
      >
        <img src={track.thumbnail} alt="" loading="lazy" />
        <span className="music-track-copy">
          <strong>{track.title}</strong>
          <small>{track.channel_title}</small>
        </span>
        <span className="music-track-duration">
          {formatDuration(track.duration_seconds)}
        </span>
      </button>
      <div className="music-track-actions">
        {source !== "queue" && (
          <button
            type="button"
            onClick={() => void enqueue(track)}
            title="Add to queue"
          >
            <Plus size={14} />
          </button>
        )}
        {source === "favorites" ? (
          <button
            type="button"
            onClick={() => void removeFavorite(track)}
            title="Remove favorite"
          >
            <Trash2 size={14} />
          </button>
        ) : (
          <button
            type="button"
            onClick={() => void addFavorite(track)}
            title="Add to favorites"
          >
            <Heart size={14} />
          </button>
        )}
      </div>
    </div>
  );

  return (
    <aside
      ref={widgetRef}
      className={`music-player-widget ${visible ? "open" : ""} ${
        trackingActive ? "tracking-active" : ""
      } ${isDragging ? "dragging" : ""}`}
      style={{
        left: widgetPosition.x,
        top: widgetPosition.y,
        right: "auto",
        bottom: "auto",
      }}
      aria-label="Jarvis music player"
      aria-hidden={!visible}
    >
      <div className="music-widget-scan-line" aria-hidden="true" />

      <header
        className="music-widget-header"
        onPointerDown={startWidgetDrag}
        onPointerMove={moveWidget}
        onPointerUp={stopWidgetDrag}
        onPointerCancel={stopWidgetDrag}
        title="Drag to move the music player"
      >
        <div>
          <span className="music-widget-kicker">
            <Move size={11} className="music-widget-drag-icon" />
            <Music2 size={12} /> JARVIS MUSIC
          </span>
          <strong>{
            playbackState === "playing"
              ? "Now playing"
              : playbackState === "buffering"
                ? "Buffering"
                : playbackState === "blocked"
                  ? "Action required"
                  : "Music console"
          }</strong>
        </div>
        <button
          type="button"
          className="music-widget-close"
          onClick={closeWidget}
          title="Close player and stop playback"
        >
          <X size={16} />
        </button>
      </header>

      <div className="music-youtube-shell">
        <div ref={playerHostRef} className="music-youtube-player" />
      </div>

      {currentTrack ? (
        <section className="music-now-playing">
          <div className="music-now-copy">
            <strong>{currentTrack.title}</strong>
            <span>{currentTrack.channel_title}</span>
          </div>
          <div className="music-now-controls">
            <button
              type="button"
              onPointerDown={(event) => event.stopPropagation()}
              onClick={(event) => {
                event.stopPropagation();
                togglePlayback();
              }}
              title={playbackState === "playing" ? "Pause" : "Play"}
            >
              {playbackState === "playing" ? (
                <Pause size={16} />
              ) : (
                <Play size={16} />
              )}
            </button>
            <button
              type="button"
              onClick={() => void playNext()}
              title="Play next"
            >
              <SkipForward size={16} />
            </button>
            <button
              type="button"
              className={isFavorite ? "active" : ""}
              onClick={() => void (
                isFavorite
                  ? removeFavorite(currentTrack)
                  : addFavorite(currentTrack)
              )}
              title={isFavorite ? "Remove favorite" : "Add favorite"}
            >
              <Heart size={16} fill={isFavorite ? "currentColor" : "none"} />
            </button>
            <button
              type="button"
              onClick={() => void enqueue(currentTrack, "next")}
              title="Play this next"
            >
              <Plus size={16} />
            </button>
          </div>
        </section>
      ) : (
        <div className="music-empty-now-playing">
          Ask Jarvis to play a song or search below.
        </div>
      )}

      {playbackState === "blocked" && (
        <div className="music-autoplay-notice">
          Browser autoplay is blocked.
          <button
            type="button"
            onPointerDown={(event) => event.stopPropagation()}
            onClick={(event) => {
              event.stopPropagation();
              playCurrentTrack();
            }}
          >
            <Play size={13} /> Play now
          </button>
        </div>
      )}

      {error && (
        <div className="music-widget-error" role="alert">
          {error}
        </div>
      )}

      <form className="music-search-form" onSubmit={handleSearch}>
        <Search size={15} />
        <input
          value={searchQuery}
          onChange={(event) => setSearchQuery(event.target.value)}
          placeholder="Search songs or artists"
          aria-label="Search music"
          maxLength={180}
        />
        <button type="submit" disabled={!searchQuery.trim() || isSearching}>
          {isSearching ? (
            <LoaderCircle size={15} className="music-spin" />
          ) : (
            "Search"
          )}
        </button>
      </form>

      <nav className="music-tabs" aria-label="Music library views">
        <button
          type="button"
          className={activeTab === "search" ? "active" : ""}
          onClick={() => setActiveTab("search")}
        >
          <Search size={13} /> Results
        </button>
        <button
          type="button"
          className={activeTab === "queue" ? "active" : ""}
          onClick={() => setActiveTab("queue")}
        >
          <ListMusic size={13} /> Up next
          {queue.length > 0 && <span>{queue.length}</span>}
        </button>
        <button
          type="button"
          className={activeTab === "favorites" ? "active" : ""}
          onClick={() => setActiveTab("favorites")}
        >
          <Heart size={13} /> Favorites
          {favorites.length > 0 && <span>{favorites.length}</span>}
        </button>
      </nav>

      <section className="music-library-list">
        {activeTab === "search" && (
          searchResults.length > 0
            ? searchResults.map((track) => renderTrackRow(track, "search"))
            : (
              <div className="music-library-empty">
                <Search size={18} />
                <span>Search results will appear here.</span>
              </div>
            )
        )}

        {activeTab === "queue" && (
          <>
            {queue.length > 0 && (
              <button
                type="button"
                className="music-clear-queue"
                onClick={() => void clearQueue()}
              >
                <Trash2 size={12} /> Clear queue
              </button>
            )}
            {queue.length > 0
              ? queue.map((track) => renderTrackRow(track, "queue"))
              : (
                <div className="music-library-empty">
                  <ListMusic size={18} />
                  <span>Your Up Next queue is empty.</span>
                </div>
              )}
          </>
        )}

        {activeTab === "favorites" && (
          favorites.length > 0
            ? favorites.map((track) => renderTrackRow(track, "favorites"))
            : (
              <div className="music-library-empty">
                <Heart size={18} />
                <span>Favorite songs will stay here.</span>
              </div>
            )
        )}
      </section>

      <footer className="music-widget-footer">
        <Clock3 size={11} />
        Queue and favorites are stored by Jarvis.
      </footer>
    </aside>
  );
}
