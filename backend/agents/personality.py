"""
backend/agents/personality.py

F5 — Personality Engine.

Responsibilities (per Jarvis 2.0 doc, Section 3 F5 + Section 9):
  1. Load personality.yaml as the single source of truth for character config.
  2. Detect which "scenario" the current message calls for (default / stressed /
     code_heavy / playful) using lightweight heuristics — no extra LLM call needed.
  3. Render the matching Jinja2 prompt template with the config + any memory
     facts supplied by the memory layer (F9), producing the final system prompt
     handed to the main LangGraph agent before it calls Gemini.

Hot reload: personality.yaml is re-read automatically whenever its mtime changes,
so editing the file takes effect on the next message — no process restart needed.
(The doc marks hot-reload as deferrable to MVP+; it's included here since it's
cheap to support and makes iterating on tone painless.)
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Optional

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import BaseModel, Field, field_validator

# --------------------------------------------------------------------------- #
# Config schema
# --------------------------------------------------------------------------- #


class Voice(BaseModel):
    tone: str
    formality: str
    humor: str
    urgency_handling: str


class Behavior(BaseModel):
    proactive: bool
    initiative: str
    correction_style: str
    unknown_handling: str
    approval_gate: bool


class Memory(BaseModel):
    recall_style: str
    preference_weight: str
    distillation_time: str


class Boundaries(BaseModel):
    never_execute_harmful_code: bool
    always_confirm_destructive_actions: bool
    cite_sources: bool
    admit_tool_usage: bool


class StressSignals(BaseModel):
    keywords: list[str] = Field(default_factory=list)
    caps_ratio_threshold: float = 0.4
    exclamation_threshold: int = 2
    min_length_for_caps_check: int = 8


class PlayfulSignals(BaseModel):
    keywords: list[str] = Field(default_factory=list)


class CodeHeavySignals(BaseModel):
    markers: list[str] = Field(default_factory=list)


class ToneAdaptation(BaseModel):
    enabled: bool = True
    stress_signals: StressSignals = Field(default_factory=StressSignals)
    playful_signals: PlayfulSignals = Field(default_factory=PlayfulSignals)
    code_heavy_signals: CodeHeavySignals = Field(default_factory=CodeHeavySignals)
    default_scenario: str = "default"


class PersonalityConfig(BaseModel):
    name: str
    version: str
    user_name: str
    voice: Voice
    behavior: Behavior
    memory: Memory
    boundaries: Boundaries
    tone_adaptation: ToneAdaptation = Field(default_factory=ToneAdaptation)

    @field_validator("name", "user_name")
    @classmethod
    def _not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be empty")
        return v


# --------------------------------------------------------------------------- #
# Tone detection
# --------------------------------------------------------------------------- #

VALID_SCENARIOS = {"default", "stressed", "code_heavy", "playful"}


class ToneDetector:
    """Heuristic-based scenario detection. No LLM call — must be fast and free."""

    def __init__(self, config: PersonalityConfig):
        self.cfg = config.tone_adaptation

    def detect(self, message: str) -> str:
        if not self.cfg.enabled or not message:
            return self.cfg.default_scenario

        if self._is_code_heavy(message):
            return "code_heavy"
        if self._is_stressed(message):
            return "stressed"
        if self._is_playful(message):
            return "playful"
        return self.cfg.default_scenario

    def _is_code_heavy(self, message: str) -> bool:
        return any(marker in message for marker in self.cfg.code_heavy_signals.markers)

    def _is_stressed(self, message: str) -> bool:
        s = self.cfg.stress_signals
        lower = message.lower()

        if any(kw in lower for kw in s.keywords):
            return True

        if message.count("!") >= s.exclamation_threshold:
            return True

        if len(message) >= s.min_length_for_caps_check:
            letters = [c for c in message if c.isalpha()]
            if letters:
                caps_ratio = sum(1 for c in letters if c.isupper()) / len(letters)
                if caps_ratio >= s.caps_ratio_threshold:
                    return True

        return False

    def _is_playful(self, message: str) -> bool:
        lower = message.lower()
        return any(kw.lower() in lower for kw in self.cfg.playful_signals.keywords)


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #


class PersonalityEngine:
    """
    Owns config loading (with hot-reload) + prompt rendering.

    Usage:
        engine = PersonalityEngine(
            config_path="personality.yaml",
            templates_dir="backend/agents/prompts",
        )
        system_prompt = engine.build_system_prompt(
            user_message=incoming_text,
            memory_facts=["Prefers dark mode", "Works in fintech"],
        )
    """

    def __init__(self, config_path: str | Path, templates_dir: str | Path):
        self.config_path = Path(config_path)
        self.templates_dir = Path(templates_dir)

        self._config: Optional[PersonalityConfig] = None
        self._mtime: float = 0.0
        self._detector: Optional[ToneDetector] = None

        self._env = Environment(
            loader=FileSystemLoader(str(self.templates_dir)),
            autoescape=select_autoescape(enabled_extensions=()),
            trim_blocks=True,
            lstrip_blocks=True,
        )

        self._load()  # fail fast on bad config at startup

    # -- config loading ---------------------------------------------------- #

    def _load(self) -> None:
        if not self.config_path.exists():
            raise FileNotFoundError(f"personality.yaml not found at {self.config_path}")

        with self.config_path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        self._config = PersonalityConfig.model_validate(raw)
        self._detector = ToneDetector(self._config)
        self._mtime = self.config_path.stat().st_mtime

    def _maybe_reload(self) -> None:
        try:
            current_mtime = self.config_path.stat().st_mtime
        except FileNotFoundError:
            return  # keep last good config rather than crashing mid-conversation

        if current_mtime != self._mtime:
            try:
                self._load()
            except Exception as exc:  # noqa: BLE001 — log and keep last-good config
                print(f"[personality] reload failed, keeping previous config: {exc}")

    @property
    def config(self) -> PersonalityConfig:
        self._maybe_reload()
        assert self._config is not None
        return self._config

    # -- prompt building ----------------------------------------------------- #

    def detect_scenario(self, user_message: str) -> str:
        self._maybe_reload()
        assert self._detector is not None
        return self._detector.detect(user_message)

    def build_system_prompt(
        self,
        user_message: str,
        memory_facts: Optional[list[str]] = None,
        scenario: Optional[str] = None,
    ) -> str:
        """
        Render the full system prompt for this turn.

        Args:
            user_message: the incoming text, used for tone detection if `scenario`
                           is not forced explicitly.
            memory_facts: distilled semantic-memory facts (F9) relevant to this
                          turn — pass [] or None if memory hasn't kicked in yet.
            scenario: override automatic detection (e.g. for tests, or if a
                      caller already knows the scenario from elsewhere).
        """
        cfg = self.config  # triggers hot-reload check

        resolved_scenario = scenario or self.detect_scenario(user_message)
        if resolved_scenario not in VALID_SCENARIOS:
            resolved_scenario = cfg.tone_adaptation.default_scenario

        template = self._env.get_template(f"{resolved_scenario}.j2")

        return template.render(
            name=cfg.name,
            user_name=cfg.user_name,
            voice=cfg.voice,
            behavior=cfg.behavior,
            memory=cfg.memory,
            boundaries=cfg.boundaries,
            memory_facts=memory_facts or [],
        ).strip()


# --------------------------------------------------------------------------- #
# Manual smoke test — run directly: python personality.py
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    import sys

    root = Path(__file__).resolve().parents[2]  # backend/agents/personality.py -> project root
    engine = PersonalityEngine(
        config_path=root / "personality.yaml",
        templates_dir=Path(__file__).resolve().parent / "prompts",
    )

    test_cases = [
        ("default", "What's the weather looking like tomorrow?"),
        ("stressed", "THE DEPLOY IS BROKEN AND I HAVE A DEMO IN 10 MINUTES!!!"),
        ("code_heavy", "Getting this traceback:\n```\nTraceback (most recent call last):\nFile \"app.py\"\n```"),
        ("playful", "lmao ok but seriously can you also check my calendar"),
    ]

    facts = ["Prefers dark mode", "Building Jarvis 2.0", "Works in fintech, based in Mumbai"]

    all_passed = True
    for expected, msg in test_cases:
        detected = engine.detect_scenario(msg)
        status = "PASS" if detected == expected else "FAIL"
        all_passed &= detected == expected
        print(f"[{status}] expected={expected:<11} detected={detected:<11} msg={msg[:50]!r}")

    print("\n--- Sample rendered prompt (stressed scenario) ---\n")
    print(engine.build_system_prompt(test_cases[1][1], memory_facts=facts))

    sys.exit(0 if all_passed else 1)
