// frontend/src/services/exportService.ts

import {
  API_URL,
  jarvisFetch,
} from "./apiClient";

export interface ExportResult {
  ok: boolean;
  filename?: string;
  error?: string;
}

export async function exportConversation(): Promise<ExportResult> {
  try {
    const res = await jarvisFetch(`${API_URL}/api/export`, { method: "POST" });

    if (!res.ok) {
      return { ok: false, error: `Export failed: ${res.status}` };
    }

    const filename =
      res.headers.get("X-Export-Filename") ??
      `jarvis-${new Date().toISOString().slice(0, 19).replace(/:/g, "-")}.md`;

    const markdown = await res.text();

    // Trigger browser download
    const blob = new Blob([markdown], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);

    return { ok: true, filename };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}
