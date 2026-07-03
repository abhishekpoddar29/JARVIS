graph TD
    Start((START)) --> MemNode[Memory Retrieval Node]
    
    MemNode -->|Query ChromaDB for context| Chroma[(ChromaDB)]
    MemNode --> PersonalityNode[Personality Node]
    
    PersonalityNode -->|Inject Memory & Rules| GeminiNode[Gemini Node]
    
    GeminiNode -->|Parse JSON| Router{Route After Gemini?}
    
    Router -->|No Tool Call| FinalizeNode[Finalize Node]
    Router -->|Tool Call Detected| ToolNode[Tool Node]
    
    %% Tool Execution & HITL
    ToolNode --> ToolExec[Execute Tool: Weather/Email/Code/etc]
    ToolExec --> CheckApproval{Requires Approval?}
    
    CheckApproval -->|No| ToolResponse[Tool Response Node]
    CheckApproval -->|Yes - F13 Gate| Interrupt[[INTERRUPT: Pause Graph]]
    
    Interrupt <-->|Send Approval Card to UI| UI_Wait((User Approves/Rejects))
    UI_Wait -->|Command resume=approved| ToolExec
    
    %% ReAct Loop
    ToolResponse --> ChainCheck{Chaining Tool?}
    ChainCheck -->|Yes, another tool needed| ToolNode
    ChainCheck -->|No, final answer ready| FinalizeNode
    
    FinalizeNode --> End((END))
    
    %% Side effects
    ToolResponse <-->|Read/Write History| Redis[(Redis)]
    FinalizeNode <-->|Save Final Reply| Redis