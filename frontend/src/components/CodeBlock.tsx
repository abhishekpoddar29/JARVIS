import { useState } from "react";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import {
  Copy,
  Check,
  ChevronDown,
  ChevronRight,
  Terminal,
  FileDown,
  AlertTriangle,
  CheckCircle2,
} from "lucide-react";
import type { CodeResult } from "../types";

export function CodeBlock({ result }: { result: CodeResult }) {
  const [isExpanded, setIsExpanded] = useState(false);
  const [isCopied, setIsCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(result.code);
    setIsCopied(true);
    setTimeout(() => setIsCopied(false), 2000);
  };

  // Don't render anything if there's no data
  if (!result) return null;

  return (
    <div className="code-block">
      {/* Header / Toggle */}
      <div className="code-block-header" onClick={() => setIsExpanded(!isExpanded)}>
        <div className="code-block-status">
          <span className={`status-dot ${result.success ? "success" : "error"}`} />
          {result.success ? <CheckCircle2 size={14} /> : <AlertTriangle size={14} />}
          <span>Python Execution</span>
          {result.retried && <span style={{ opacity: 0.6, marginLeft: "4px" }}>(Auto-retried)</span>}
        </div>
        <div className="code-block-actions">
          <span style={{ marginRight: "8px", opacity: 0.7 }}>
            {result.execution_time_ms}ms
          </span>
          <button 
            className="code-action" 
            onClick={(e) => { e.stopPropagation(); handleCopy(); }}
            title="Copy code"
          >
            {isCopied ? <Check size={12} /> : <Copy size={12} />}
            {isCopied ? "Copied" : "Copy"}
          </button>
          {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </div>
      </div>

      {/* Body */}
      <div className="code-block-body">
        {/* Collapsible Code View */}
        {isExpanded && (
          <SyntaxHighlighter
            language="python"
            style={oneDark}
            customStyle={{
              margin: 0,
              background: "transparent",
              padding: "0",
              fontSize: "12.5px",
            }}
          >
            {result.code}
          </SyntaxHighlighter>
        )}

        {/* Standard Output */}
        {result.stdout && (
          <div>
            <div style={{ fontSize: "10px", color: "#6a8a95", marginBottom: "4px", display: "flex", alignItems: "center", gap: "4px" }}>
              <Terminal size={10} /> STDOUT
            </div>
            <pre className="terminal-output terminal-stdout">{result.stdout}</pre>
          </div>
        )}

        {/* Standard Error */}
        {result.stderr && (
          <div>
            <div style={{ fontSize: "10px", color: "#fca5a5", marginBottom: "4px", display: "flex", alignItems: "center", gap: "4px" }}>
              <AlertTriangle size={10} /> STDERR
            </div>
            <pre className="terminal-output terminal-stderr">{result.stderr}</pre>
          </div>
        )}

        {/* Generated Images (Charts, Plots) */}
        {result.images && result.images.length > 0 && (
          result.images.map((img, idx) => (
            <img
              key={idx}
              src={`data:image/png;base64,${img}`}
              alt={`Generated output ${idx + 1}`}
              className="generated-image"
              style={{ maxWidth: '100%', borderRadius: '8px', marginTop: '8px' }}
            />
          ))
        )}

        {/* Downloadable Files */}
        {result.files.map((file, idx) => (
          <a
            key={idx}
            href={file.download_path}
            download={file.name}
            className="file-download"
          >
            <FileDown size={14} />
            {file.name} ({(file.size_bytes / 1024).toFixed(1)} KB)
          </a>
        ))}

        {/* Execution Meta Footer */}
        <div className="execution-meta">
          {result.success ? "Execution successful" : `Execution failed: ${result.error || "Unknown error"}`}
        </div>
      </div>
    </div>
  );
}