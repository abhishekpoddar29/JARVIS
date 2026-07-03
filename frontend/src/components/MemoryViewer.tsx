// frontend/src/components/MemoryViewer.tsx

import { useEffect, useState } from "react";
import { Brain, X, Trash2, Pin, RefreshCw } from "lucide-react";
import { memoryService, type MemoryEntry } from "../services/memoryService";

interface MemoryViewerProps {
  isOpen: boolean;
  onClose: () => void;
}

export function MemoryViewer({ isOpen, onClose }: MemoryViewerProps) {
  const [memories, setMemories] = useState<MemoryEntry[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  const loadMemories = async () => {
    setIsLoading(true);
    try {
      const data = await memoryService.getAllMemories();
      setMemories(data);
    } catch (err) {
      console.error(err);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    if (isOpen) loadMemories();
  }, [isOpen]);

  const handleDelete = async (id: string) => {
    await memoryService.deleteMemory(id);
    setMemories(prev => prev.filter(m => m.id !== id));
  };

  const handleTogglePin = async (memory: MemoryEntry) => {
    await memoryService.updateMemory(memory.id, { pinned: !memory.pinned });
    loadMemories(); // Refresh to show updated state
  };

  const handleDistill = async () => {
    try {
      const result = await memoryService.triggerDistillation();
      alert(`Distillation complete! New memories: ${result.new_memories}`);
      loadMemories();
    } catch (err) {
      alert("Distillation failed. Check backend logs.");
    }
  };

  if (!isOpen) return null;

  return (
    <div className="memory-viewer-overlay">
      <div className="memory-viewer-panel">
        <div className="memory-viewer-header">
          <div className="header-title">
            <Brain size={20} />
            <h2>Jarvis Semantic Memory</h2>
          </div>
          <button onClick={onClose} className="close-btn"><X size={20} /></button>
        </div>

        <div className="memory-actions">
          <button onClick={handleDistill} className="distill-btn">
            <RefreshCw size={14} /> Run Distillation Now
          </button>
        </div>

        <div className="memory-list">
          {isLoading ? (
            <p>Loading memories...</p>
          ) : memories.length === 0 ? (
            <p className="empty-state">Jarvis has no long-term memories yet. Talk to him!</p>
          ) : (
            memories.map(mem => (
              <div key={mem.id} className={`memory-card ${mem.pinned ? 'pinned' : ''}`}>
                <div className="memory-meta">
                  <span className={`memory-type ${mem.type}`}>{mem.type}</span>
                  <span className="memory-date">{new Date(mem.source_date).toLocaleDateString()}</span>
                </div>
                <p className="memory-content">{mem.content}</p>
                <div className="memory-controls">
                  <button 
                    className={`memory-action ${mem.pinned ? 'active' : ''}`}
                    onClick={() => handleTogglePin(mem)}
                    title={mem.pinned ? "Unpin" : "Pin"}
                  >
                    <Pin size={14} />
                  </button>
                  <button 
                    className="memory-action delete"
                    onClick={() => handleDelete(mem.id)}
                    title="Forget this"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}