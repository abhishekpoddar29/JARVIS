import type { VoiceTranscriptionResponse } from "../types";
import {
  API_URL,
  jarvisFetch,
} from "./apiClient";

const VOICE_LOAD_TIMEOUT_MS = 5_000;
const SPEECH_CHUNK_MAX_CHARS = 220;
const JARVIS_RATE = 1.12;
const JARVIS_PITCH = 0.88;
const JARVIS_VOLUME = 1;

const PREFERRED_MALE_VOICE_NAMES = [
  "brian multilingual",
  "brian online",
  "andrew multilingual",
  "andrew online",
  "guy",
  "mark",
  "david",
] as const;


async function readError(response: Response): Promise<string> {
  try {
    const payload = await response.json();
    return payload.detail ?? payload.message ?? `Request failed: ${response.status}`;
  } catch {
    return `Request failed: ${response.status}`;
  }
}


export async function transcribeVoiceAudio(
  audio: Blob,
  signal?: AbortSignal,
): Promise<string> {
  const formData = new FormData();
  formData.append("audio", audio, "jarvis-voice.wav");

  const response = await jarvisFetch(`${API_URL}/api/voice/transcribe`, {
    method: "POST",
    body: formData,
    signal,
  });

  if (!response.ok) {
    throw new Error(await readError(response));
  }

  const payload = (await response.json()) as VoiceTranscriptionResponse;
  return payload.transcript.trim();
}


function getSpeechSynthesis(): SpeechSynthesis {
  if (
    typeof window === "undefined" ||
    !("speechSynthesis" in window) ||
    typeof SpeechSynthesisUtterance === "undefined"
  ) {
    throw new Error("Local browser text-to-speech is not supported.");
  }

  return window.speechSynthesis;
}


function waitForVoices(
  synthesis: SpeechSynthesis,
  signal?: AbortSignal,
): Promise<SpeechSynthesisVoice[]> {
  const hasBrianMultilingual = (
    voices: SpeechSynthesisVoice[],
  ) =>
    voices.some((voice) => {
      const name = voice.name.toLowerCase();
      const language = voice.lang
        .toLowerCase()
        .replace("_", "-");

      return (
        name.includes("brian multilingual") &&
        language === "en-us"
      );
    });

  const initialVoices = synthesis.getVoices();

  // Brian may already be available after a warm page load.
  if (hasBrianMultilingual(initialVoices)) {
    return Promise.resolve(initialVoices);
  }

  return new Promise((resolve, reject) => {
    let settled = false;
    let latestVoices = initialVoices;

    const cleanup = () => {
      window.clearTimeout(timeoutId);

      synthesis.removeEventListener(
        "voiceschanged",
        onVoicesChanged,
      );

      signal?.removeEventListener(
        "abort",
        onAbort,
      );
    };

    const finish = (
      voices: SpeechSynthesisVoice[],
    ) => {
      if (settled) return;

      settled = true;
      cleanup();
      resolve(voices);
    };

    const onVoicesChanged = () => {
      latestVoices = synthesis.getVoices();

      // Finish as soon as Edge exposes Brian Multilingual.
      if (hasBrianMultilingual(latestVoices)) {
        finish(latestVoices);
      }
    };

    const onAbort = () => {
      if (settled) return;

      settled = true;
      cleanup();

      reject(
        new DOMException(
          "Voice selection aborted.",
          "AbortError",
        ),
      );
    };

    const timeoutId = window.setTimeout(() => {
      latestVoices = synthesis.getVoices();
      finish(latestVoices);
    }, VOICE_LOAD_TIMEOUT_MS);

    synthesis.addEventListener(
      "voiceschanged",
      onVoicesChanged,
    );

    signal?.addEventListener(
      "abort",
      onAbort,
      { once: true },
    );

    // Trigger Chromium/Edge online voice discovery.
    void synthesis.getVoices();
  });
}


function normalizedLanguage(voice: SpeechSynthesisVoice): string {
  return voice.lang.toLowerCase().replace("_", "-");
}


function nameRank(voice: SpeechSynthesisVoice): number {
  const name = voice.name.toLowerCase();
  const index = PREFERRED_MALE_VOICE_NAMES.findIndex((candidate) =>
    name.includes(candidate),
  );
  return index === -1 ? PREFERRED_MALE_VOICE_NAMES.length : index;
}


function voiceScore(
  voice: SpeechSynthesisVoice,
): number {
  const language = normalizedLanguage(voice);
  const name = voice.name.toLowerCase();
  const rank = nameRank(voice);

  let score = 0;

  // Absolute first preference:
  // Microsoft Brian Multilingual Online (Natural) - English (United States)
  if (
    name.includes("brian multilingual") &&
    language === "en-us"
  ) {
    score += 10_000;
  }

  // Second preference: normal Brian Natural US.
  if (
    name.includes("brian online") &&
    language === "en-us"
  ) {
    score += 5_000;
  }

  // Prefer Microsoft's Natural online voices over old local SAPI voices.
  if (
    name.includes("microsoft") &&
    name.includes("online") &&
    name.includes("natural")
  ) {
    score += 1_000;
  }

  if (language === "en-us") {
    score += 500;
  } else if (language.startsWith("en-")) {
    score += 100;
  }

  if (
    rank < PREFERRED_MALE_VOICE_NAMES.length
  ) {
    score += 200 - rank * 10;
  }

  if (voice.default) {
    score += 5;
  }

  return score;
}

async function selectJarvisVoice(
  signal?: AbortSignal,
): Promise<SpeechSynthesisVoice> {
  const synthesis = getSpeechSynthesis();

  const voices = await waitForVoices(
    synthesis,
    signal,
  );

  if (voices.length === 0) {
    throw new Error(
      "No speech voices were exposed by this browser.",
    );
  }

  const brianMultilingual = voices.find(
    (voice) => {
      const name = voice.name.toLowerCase();

      return (
        name.includes("brian multilingual") &&
        normalizedLanguage(voice) === "en-us"
      );
    },
  );

  const brianNatural = voices.find(
    (voice) => {
      const name = voice.name.toLowerCase();

      return (
        name.includes("brian online") &&
        normalizedLanguage(voice) === "en-us"
      );
    },
  );

  const microsoftNaturalUS = voices
    .filter((voice) => {
      const name = voice.name.toLowerCase();

      return (
        normalizedLanguage(voice) === "en-us" &&
        name.includes("microsoft") &&
        name.includes("online") &&
        name.includes("natural")
      );
    })
    .sort(
      (left, right) =>
        voiceScore(right) - voiceScore(left),
    )[0];

  const localUSVoice = voices.find(
    (voice) =>
      normalizedLanguage(voice) === "en-us" &&
      voice.localService,
  );

  const selected =
    brianMultilingual ??
    brianNatural ??
    microsoftNaturalUS ??
    localUSVoice ??
    voices[0];

  console.info(
    "[Jarvis Local TTS] Selected voice",
    {
      name: selected.name,
      language: selected.lang,
      localService: selected.localService,
      voiceURI: selected.voiceURI,
    },
  );

  if (!brianMultilingual) {
    console.warn(
      `[Jarvis Local TTS] Brian Multilingual was not available. Using "${selected.name}" instead.`,
    );
  }

  return selected;
}


function cleanSpeechText(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, " Code output is available on screen. ")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/!\[([^\]]*)\]\([^)]+\)/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/https?:\/\/\S+/gi, " link ")
    .replace(/^\s{0,3}#{1,6}\s+/gm, "")
    .replace(/^\s*[-*+]\s+/gm, "")
    .replace(/^\s*\d+[.)]\s+/gm, "")
    .replace(/[*_~>|]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}


function splitLongSegment(segment: string): string[] {
  if (segment.length <= SPEECH_CHUNK_MAX_CHARS) return [segment];

  const words = segment.split(/\s+/);
  const chunks: string[] = [];
  let current = "";

  for (const word of words) {
    const next = current ? `${current} ${word}` : word;
    if (next.length <= SPEECH_CHUNK_MAX_CHARS) {
      current = next;
      continue;
    }

    if (current) chunks.push(current);
    current = word;
  }

  if (current) chunks.push(current);
  return chunks;
}


function splitForSpeech(text: string): string[] {
  const cleaned = cleanSpeechText(text);
  if (!cleaned) return [];

  const sentences = cleaned.split(/(?<=[.!?])\s+/);
  const chunks: string[] = [];
  let current = "";

  for (const sentence of sentences) {
    const parts = splitLongSegment(sentence);

    for (const part of parts) {
      const next = current ? `${current} ${part}` : part;
      if (next.length <= SPEECH_CHUNK_MAX_CHARS) {
        current = next;
      } else {
        if (current) chunks.push(current);
        current = part;
      }
    }
  }

  if (current) chunks.push(current);
  return chunks;
}


function speakChunk(
  synthesis: SpeechSynthesis,
  voice: SpeechSynthesisVoice,
  text: string,
  signal?: AbortSignal,
): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("Voice playback aborted.", "AbortError"));
      return;
    }

    const utterance = new SpeechSynthesisUtterance(text);
    utterance.voice = voice;
    utterance.lang = voice.lang || "en-US";
    utterance.rate = JARVIS_RATE;
    utterance.pitch = JARVIS_PITCH;
    utterance.volume = JARVIS_VOLUME;

    let settled = false;

    const cleanup = () => {
      signal?.removeEventListener("abort", onAbort);
      utterance.onend = null;
      utterance.onerror = null;
    };

    const finish = (error?: Error) => {
      if (settled) return;
      settled = true;
      cleanup();
      if (error) reject(error);
      else resolve();
    };

    const onAbort = () => {
      synthesis.cancel();
      finish(new DOMException("Voice playback aborted.", "AbortError"));
    };

    utterance.onend = () => finish();
    utterance.onerror = (event) => {
      finish(
        new Error(
          `Local speech synthesis failed${event.error ? `: ${event.error}` : "."}`,
        ),
      );
    };

    signal?.addEventListener("abort", onAbort, { once: true });
    synthesis.speak(utterance);
  });
}


/**
 * Speaks each final orchestration response using a pinned browser/Windows voice.
 *
 * Selection priority:
 * 1. Microsoft Derek Multilingual (US)
 * 2. Another known US male voice
 * 3. Another installed/local male Microsoft voice
 * 4. The highest-ranked English voice exposed by the browser
 */
export async function speakJarvisResponse(
  text: string,
  signal?: AbortSignal,
): Promise<void> {
  const synthesis = getSpeechSynthesis();
  const chunks = splitForSpeech(text);

  if (chunks.length === 0) return;

  // Prevent previous responses from overlapping the current one.
  synthesis.cancel();

  const voice = await selectJarvisVoice(signal);

  for (const chunk of chunks) {
    if (signal?.aborted) {
      throw new DOMException("Voice playback aborted.", "AbortError");
    }
    await speakChunk(synthesis, voice, chunk, signal);
  }
}
