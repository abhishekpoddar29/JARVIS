import { useState } from "react";
import { ShieldCheck, X, Mail, Calendar, Send, CheckCircle2, Code } from "lucide-react";
import Editor from "react-simple-code-editor";
import { highlight, languages } from "prismjs/components/prism-core";
import "prismjs/components/prism-python";
import "prism-themes/themes/prism-one-dark.css"; // A clean dark theme
import type { ApprovalData } from "../types";

interface ApprovalCardProps {
  data: ApprovalData;
  onApprove: (editedCode?: string) => void; // Updated to accept edited code
  onReject: () => void;
}

export function ApprovalCard({ data, onApprove, onReject }: ApprovalCardProps) {
  const [status, setStatus] = useState<"pending" | "approved" | "rejected">("pending");
  
  // F17: State to hold the editable code
  const [editedCode, setEditedCode] = useState(data.draft.code || "");

  const handleApprove = () => {
    setStatus("approved");
    // Pass the edited code back. Only pass it if it's a self_modify action.
    onApprove(data.action_type === "self_modify" ? editedCode : undefined);
  };

  const handleReject = () => {
    setStatus("rejected");
    onReject();
  };

  const isEmail = data.action_type === "send_email";
  const isSelfModify = data.action_type === "self_modify";
  const draft = data.draft;

  const renderIcon = () => {
    if (isEmail) return <Mail size={12} />;
    if (isSelfModify) return <Code size={12} />;
    return <Calendar size={12} />;
  };

  const renderLabel = () => {
    if (isEmail) return "Send Email";
    if (isSelfModify) return "Self-Modify Code";
    return "Create Event";
  };

  return (
    <div className="approval-card">
      <div className="approval-header">
        <div className="approval-title">
          <ShieldCheck size={16} />
          <span>Action Approval Required</span>
        </div>
        <span className={`approval-type ${data.action_type}`}>
          {renderIcon()}
          {renderLabel()}
        </span>
      </div>

      <div className="approval-body">
        {isEmail ? (
          <>
            <div className="approval-row"><strong>To:</strong> {draft.to}</div>
            <div className="approval-row"><strong>Subject:</strong> {draft.subject}</div>
            <div className="approval-text"><strong>Body:</strong><br />{draft.body}</div>
          </>
        ) : isSelfModify ? (
          <>
            <div className="approval-row"><strong>Filename:</strong> {draft.filename}</div>
            <div className="approval-text" style={{ background: "rgba(0,0,0,0.3)", padding: "10px", borderRadius: "6px", border: "1px solid rgba(255,255,255,0.05)" }}>
              <strong>Code:</strong> (You can edit this before approving)<br />
              <Editor
                value={editedCode}
                onValueChange={code => setEditedCode(code)}
                highlight={code => highlight(code, languages.python, "python")}
                padding={10}
                style={{
                  fontFamily: '"Fira Code", "Cascadia Code", monospace',
                  fontSize: 12,
                  minHeight: "150px",
                  backgroundColor: "transparent",
                  color: "#fff"
                }}
              />
            </div>
          </>
        ) : (
          <>
            <div className="approval-row"><strong>Title:</strong> {draft.summary}</div>
            <div className="approval-row"><strong>Start:</strong> {new Date(draft.start.dateTime).toLocaleString()}</div>
            <div className="approval-row"><strong>End:</strong> {new Date(draft.end.dateTime).toLocaleString()}</div>
            {draft.attendees?.length > 0 && (
              <div className="approval-row"><strong>Attendees:</strong> {draft.attendees.map((a: any) => a.email).join(", ")}</div>
            )}
          </>
        )}
      </div>

      {status === "pending" && (
        <div className="approval-actions">
          <button className="approve-btn" onClick={handleApprove}>
            <CheckCircle2 size={14} /> Approve & Execute
          </button>
          <button className="reject-btn" onClick={handleReject}>
            <X size={14} /> Reject
          </button>
        </div>
      )}

      {status === "approved" && (
        <div className="approval-status approved">
          <Send size={14} /> Approved. Executing...
        </div>
      )}
      {status === "rejected" && (
        <div className="approval-status rejected">
          <X size={14} /> Rejected by user.
        </div>
      )}
    </div>
  );
}