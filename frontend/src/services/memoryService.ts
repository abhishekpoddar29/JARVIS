// frontend/src/services/memoryService.ts

import {
  API_URL,
  jarvisFetch,
} from "./apiClient";

export interface MemoryEntry {
  id: string;
  type: string;
  content: string;
  source_date: string;
  confidence: number;
  pinned: boolean;
  sensitivity: string;
  created_at: string;
  updated_at: string;
}

export const memoryService = {
  async getAllMemories(): Promise<MemoryEntry[]> {
    const res = await jarvisFetch(`${API_URL}/api/memory`);
    if (!res.ok) throw new Error("Failed to fetch memories");
    const data = await res.json();
    return data.memories;
  },

  async deleteMemory(id: string): Promise<void> {
    await jarvisFetch(`${API_URL}/api/memory/${id}`, { method: "DELETE" });
  },

  async updateMemory(id: string, payload: Partial<MemoryEntry>): Promise<void> {
    await jarvisFetch(`${API_URL}/api/memory/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  },
  
  async triggerDistillation(): Promise<any> {
    const res = await jarvisFetch(`${API_URL}/api/memory/distill`, { method: "POST" });
    return res.json();
  }
};
