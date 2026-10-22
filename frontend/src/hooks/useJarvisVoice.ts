import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type {
  OrbState,
  VoicePhase,
  VoiceSocketEvent,
  VoiceSocketEventListener,
} from "../types";
import {
  speakJarvisResponse,
  transcribeVoiceAudio,
} from "../services/voiceService";


interface UseJarvisVoiceOptions {
  isSocketConnected: boolean;
  sendVoiceMessage: (message: string) => boolean;
  subscribeVoiceEvents: (listener: VoiceSocketEventListener) => () => void;
}

interface UseJarvisVoiceResult {
  isActive: boolean;
  isBusy: boolean;
  phase: VoicePhase;
  error: string | null;
  orbState: OrbState | null;
  toggle: () => void;
}

const TARGET_SAMPLE_RATE = 16_000;
const INITIAL_SPEECH_TIMEOUT_MS = 8_000;
const SILENCE_AFTER_SPEECH_MS = 900;
const MAX_UTTERANCE_MS = 30_000;
const SPEECH_RMS_THRESHOLD = 0.018;

const WORKLET_SOURCE = `
  class JarvisVoiceCaptureProcessor extends AudioWorkletProcessor {
    process(inputs) {
      const channel = inputs[0] && inputs[0][0];
      if (channel && channel.length) {
        const copy = new Float32Array(channel);
        this.port.postMessage(copy, [copy.buffer]);
      }
      return true;
    }
  }
  registerProcessor("jarvis-voice-capture", JarvisVoiceCaptureProcessor);
`;


function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}


function mergeChunks(chunks: Float32Array[]): Float32Array {
  const total = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const merged = new Float32Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    merged.set(chunk, offset);
    offset += chunk.length;
  }
  return merged;
}


function resample(
  input: Float32Array,
  inputRate: number,
  outputRate: number,
): Float32Array {
  if (inputRate === outputRate) return input;
  const ratio = inputRate / outputRate;
  const outputLength = Math.max(1, Math.round(input.length / ratio));
  const output = new Float32Array(outputLength);

  for (let index = 0; index < outputLength; index += 1) {
    const sourcePosition = index * ratio;
    const left = Math.floor(sourcePosition);
    const right = Math.min(left + 1, input.length - 1);
    const mix = sourcePosition - left;
    output[index] = input[left] * (1 - mix) + input[right] * mix;
  }
  return output;
}


function encodeWave(samples: Float32Array, sampleRate: number): Blob {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);

  const writeAscii = (offset: number, value: string) => {
    for (let index = 0; index < value.length; index += 1) {
      view.setUint8(offset + index, value.charCodeAt(index));
    }
  };

  writeAscii(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  writeAscii(8, "WAVE");
  writeAscii(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeAscii(36, "data");
  view.setUint32(40, samples.length * 2, true);

  let offset = 44;
  for (const sample of samples) {
    const clipped = Math.max(-1, Math.min(1, sample));
    view.setInt16(
      offset,
      clipped < 0 ? clipped * 0x8000 : clipped * 0x7fff,
      true,
    );
    offset += 2;
  }
  return new Blob([buffer], { type: "audio/wav" });
}


async function captureUtterance(signal: AbortSignal): Promise<Blob | null> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      channelCount: 1,
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
  });
  const context = new AudioContext({ latencyHint: "interactive" });
  const source = context.createMediaStreamSource(stream);
  const mutedOutput = context.createGain();
  mutedOutput.gain.value = 0;

  let workletUrl: string | null = null;
  let node: AudioWorkletNode | null = null;
  const chunks: Float32Array[] = [];
  const preRoll: Float32Array[] = [];
  let preRollSamples = 0;
  let heardSpeech = false;
  let lastSpeechAt = performance.now();
  let settled = false;
  let initialTimer: ReturnType<typeof setTimeout> | null = null;
  let maximumTimer: ReturnType<typeof setTimeout> | null = null;

  const cleanup = async () => {
    if (initialTimer) clearTimeout(initialTimer);
    if (maximumTimer) clearTimeout(maximumTimer);
    signal.removeEventListener("abort", onAbort);
    node?.disconnect();
    source.disconnect();
    mutedOutput.disconnect();
    stream.getTracks().forEach((track) => track.stop());
    if (context.state !== "closed") await context.close();
    if (workletUrl) URL.revokeObjectURL(workletUrl);
  };

  let rejectPromise: (reason?: unknown) => void = () => undefined;
  let resolvePromise: (value: Blob | null) => void = () => undefined;

  const finish = async (aborted = false) => {
    if (settled) return;
    settled = true;
    await cleanup();
    if (aborted) {
      rejectPromise(new DOMException("Voice capture aborted.", "AbortError"));
      return;
    }
    if (!heardSpeech || chunks.length === 0) {
      resolvePromise(null);
      return;
    }
    const merged = mergeChunks(chunks);
    const converted = resample(merged, context.sampleRate, TARGET_SAMPLE_RATE);
    resolvePromise(encodeWave(converted, TARGET_SAMPLE_RATE));
  };
  const onAbort = () => void finish(true);

  const result = new Promise<Blob | null>((resolve, reject) => {
    resolvePromise = resolve;
    rejectPromise = reject;
  });

  try {
    workletUrl = URL.createObjectURL(
      new Blob([WORKLET_SOURCE], { type: "text/javascript" }),
    );
    await context.audioWorklet.addModule(workletUrl);
    node = new AudioWorkletNode(context, "jarvis-voice-capture");
    node.port.onmessage = (event: MessageEvent<Float32Array>) => {
      if (settled) return;
      const chunk = new Float32Array(event.data);

      let squareSum = 0;
      for (const sample of chunk) squareSum += sample * sample;
      const rms = Math.sqrt(squareSum / Math.max(1, chunk.length));
      const now = performance.now();

      if (!heardSpeech) {
        preRoll.push(chunk);
        preRollSamples += chunk.length;
        const maximumPreRoll = Math.round(context.sampleRate * 0.25);
        while (preRollSamples > maximumPreRoll && preRoll.length > 1) {
          preRollSamples -= preRoll.shift()!.length;
        }

        if (rms >= SPEECH_RMS_THRESHOLD) {
          heardSpeech = true;
          lastSpeechAt = now;
          chunks.push(...preRoll);
          preRoll.length = 0;
          preRollSamples = 0;
        }
        return;
      }

      chunks.push(chunk);
      if (rms >= SPEECH_RMS_THRESHOLD) {
        lastSpeechAt = now;
      } else if (now - lastSpeechAt >= SILENCE_AFTER_SPEECH_MS) {
        void finish();
      }
    };

    source.connect(node);
    node.connect(mutedOutput);
    mutedOutput.connect(context.destination);
    if (context.state === "suspended") await context.resume();

    signal.addEventListener("abort", onAbort, { once: true });
    initialTimer = setTimeout(() => {
      if (!heardSpeech) void finish();
    }, INITIAL_SPEECH_TIMEOUT_MS);
    maximumTimer = setTimeout(() => void finish(), MAX_UTTERANCE_MS);
  } catch (error) {
    await cleanup();
    throw error;
  }

  return result;
}


export function useJarvisVoice({
  isSocketConnected,
  sendVoiceMessage,
  subscribeVoiceEvents,
}: UseJarvisVoiceOptions): UseJarvisVoiceResult {
  const [isActive, setIsActive] = useState(false);
  const [phase, setPhase] = useState<VoicePhase>("inactive");
  const [error, setError] = useState<string | null>(null);
  const activeRef = useRef(false);
  const operationRef = useRef<AbortController | null>(null);
  const eventQueueRef = useRef<Promise<void>>(Promise.resolve());
  const startListeningRef = useRef<() => Promise<void>>(async () => undefined);

  const fail = useCallback((cause: unknown) => {
    const message = cause instanceof Error ? cause.message : String(cause);
    console.error("[Jarvis Voice]", cause);
    operationRef.current?.abort();
    operationRef.current = null;
    activeRef.current = false;
    setIsActive(false);
    setPhase("error");
    setError(message);
  }, []);

  const startListening = useCallback(async () => {
    if (!activeRef.current || !isSocketConnected) return;

    operationRef.current?.abort();
    const controller = new AbortController();
    operationRef.current = controller;
    setError(null);
    setPhase("listening");

    try {
      const audio = await captureUtterance(controller.signal);
      if (!activeRef.current || controller.signal.aborted) return;

      if (!audio) {
        await startListeningRef.current();
        return;
      }

      setPhase("transcribing");
      const transcript = await transcribeVoiceAudio(audio, controller.signal);
      if (!activeRef.current || controller.signal.aborted) return;

      if (!transcript) {
        await startListeningRef.current();
        return;
      }

      setPhase("waiting");
      operationRef.current = null;
      if (!sendVoiceMessage(transcript)) {
        throw new Error("Jarvis WebSocket is not connected.");
      }
    } catch (cause) {
      if (!isAbortError(cause)) fail(cause);
    }
  }, [fail, isSocketConnected, sendVoiceMessage]);

  useEffect(() => {
    startListeningRef.current = startListening;
  }, [startListening]);

  const handleVoiceEvent = useCallback(
    async (event: VoiceSocketEvent) => {
      if (!activeRef.current) return;
      if (event.type === "error") {
        fail(new Error(event.text));
        return;
      }

      const controller = new AbortController();
      operationRef.current?.abort();
      operationRef.current = controller;
      setPhase("speaking");

      try {
        await speakJarvisResponse(event.text, controller.signal);
        operationRef.current = null;
        if (activeRef.current) await startListeningRef.current();
      } catch (cause) {
        if (!isAbortError(cause)) fail(cause);
      }
    },
    [fail],
  );

  useEffect(() => {
    return subscribeVoiceEvents((event) => {
      eventQueueRef.current = eventQueueRef.current
        .then(() => handleVoiceEvent(event))
        .catch(fail);
    });
  }, [fail, handleVoiceEvent, subscribeVoiceEvents]);

  const stop = useCallback(() => {
    activeRef.current = false;
    operationRef.current?.abort();
    operationRef.current = null;
    setIsActive(false);
    setPhase("inactive");
    setError(null);
  }, []);

  const toggle = useCallback(() => {
    if (activeRef.current) {
      stop();
      return;
    }
    if (!isSocketConnected) {
      fail(new Error("Jarvis WebSocket is not connected."));
      return;
    }

    activeRef.current = true;
    setIsActive(true);
    setPhase("waking");
    setError(null);
    void startListeningRef.current();
  }, [fail, isSocketConnected, stop]);

  useEffect(() => stop, [stop]);

  useEffect(() => {
    if (!isSocketConnected && activeRef.current) {
      fail(new Error("Jarvis WebSocket disconnected."));
    }
  }, [fail, isSocketConnected]);

  const orbState = useMemo<OrbState | null>(() => {
    switch (phase) {
      case "waking":
        return "waking";
      case "listening":
        return "listening";
      case "transcribing":
      case "waiting":
        return "processing";
      case "speaking":
        return "speaking";
      case "error":
        return "error";
      default:
        return null;
    }
  }, [phase]);

  return {
    isActive,
    isBusy: phase !== "inactive" && phase !== "listening",
    phase,
    error,
    orbState,
    toggle,
  };
}