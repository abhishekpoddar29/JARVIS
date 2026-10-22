export const API_URL =
  import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000";

const APP_TOKEN =
  import.meta.env.VITE_JARVIS_APP_TOKEN ?? "";

const APP_TOKEN_HEADER = "x-jarvis-app-token";

function mergeHeaders(headers?: HeadersInit): Headers {
  return new Headers(headers);
}

export function withJarvisAuth(init: RequestInit = {}): RequestInit {
  const headers = mergeHeaders(init.headers);

  if (APP_TOKEN && !headers.has(APP_TOKEN_HEADER)) {
    headers.set(APP_TOKEN_HEADER, APP_TOKEN);
  }

  return {
    ...init,
    headers,
  };
}

export function jarvisFetch(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  return fetch(input, withJarvisAuth(init));
}

export function websocketUrlWithAuth(url: string): string {
  if (!APP_TOKEN) return url;

  const parsed = new URL(url);
  parsed.searchParams.set("token", APP_TOKEN);
  return parsed.toString();
}
