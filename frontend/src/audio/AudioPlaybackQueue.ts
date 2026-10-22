type PlaybackStateListener = (isPlaying: boolean) => void;

function decodeBase64(value: string): Uint8Array {
  const binary = atob(value);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}

function sampleRateFromMimeType(mimeType?: string): number {
  const match = /rate=(\d+)/i.exec(mimeType ?? "");
  return match ? Number(match[1]) : 24_000;
}

export class AudioPlaybackQueue {
  private context: AudioContext | null = null;
  private readonly sources = new Set<AudioBufferSourceNode>();
  private nextStartTime = 0;

  constructor(private readonly onStateChange?: PlaybackStateListener) {}

  get isPlaying(): boolean {
    return this.sources.size > 0;
  }

  async initialize(): Promise<void> {
    this.context ??= new AudioContext();
    if (this.context.state === "suspended") {
      await this.context.resume();
    }
  }

  async enqueue(data: string, mimeType?: string): Promise<void> {
    await this.initialize();
    const context = this.context;
    if (!context || !data) return;

    const bytes = decodeBase64(data);
    const sampleCount = Math.floor(bytes.byteLength / 2);
    if (sampleCount === 0) return;

    const sampleRate = sampleRateFromMimeType(mimeType);
    const audioBuffer = context.createBuffer(1, sampleCount, sampleRate);
    const channel = audioBuffer.getChannelData(0);
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);

    for (let index = 0; index < sampleCount; index += 1) {
      channel[index] = view.getInt16(index * 2, true) / 32_768;
    }

    const source = context.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(context.destination);

    const startAt = Math.max(context.currentTime + 0.015, this.nextStartTime);
    this.nextStartTime = startAt + audioBuffer.duration;
    this.sources.add(source);
    this.onStateChange?.(true);

    source.onended = () => {
      this.sources.delete(source);
      if (this.sources.size === 0) {
        this.nextStartTime = 0;
        this.onStateChange?.(false);
      }
    };
    source.start(startAt);
  }

  interrupt(): void {
    const sources = [...this.sources];
    this.sources.clear();
    this.nextStartTime = 0;
    for (const source of sources) {
      try {
        source.stop();
      } catch {
        // The source may already have ended.
      }
    }
    this.onStateChange?.(false);
  }

  async close(): Promise<void> {
    this.interrupt();
    const context = this.context;
    this.context = null;
    if (context && context.state !== "closed") {
      await context.close();
    }
  }
}
