from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass


class BudgetExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class BudgetLimits:
    max_total_llm_calls: int = 10
    max_reasoning_model_calls: int = 4
    max_total_output_tokens: int = 40_000
    max_elapsed_seconds: float = 900.0


@dataclass
class BudgetEntry:
    started_at: float
    llm_calls: int = 0
    reasoning_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: float = 0.0


class EngineeringBudgetGuard:
    def __init__(self, limits: BudgetLimits | None = None) -> None:
        self._limits = limits or BudgetLimits()
        self._entries: dict[str, BudgetEntry] = {}
        self._lock = asyncio.Lock()

    async def register(self, request_id: str) -> None:
        async with self._lock:
            self._entries.setdefault(
                request_id,
                BudgetEntry(started_at=time.monotonic()),
            )

    async def reserve_llm_call(
        self,
        request_id: str,
        *,
        reasoning: bool,
    ) -> None:
        async with self._lock:
            entry = self._entries.setdefault(
                request_id,
                BudgetEntry(started_at=time.monotonic()),
            )

            elapsed = time.monotonic() - entry.started_at
            if elapsed >= self._limits.max_elapsed_seconds:
                raise BudgetExceeded("Maximum engineering runtime reached.")
            if entry.llm_calls >= self._limits.max_total_llm_calls:
                raise BudgetExceeded("Maximum total LLM calls reached.")
            if (
                reasoning
                and entry.reasoning_calls
                >= self._limits.max_reasoning_model_calls
            ):
                raise BudgetExceeded("Maximum reasoning-model calls reached.")
            if (
                entry.output_tokens
                >= self._limits.max_total_output_tokens
            ):
                raise BudgetExceeded("Maximum output-token budget reached.")

            entry.llm_calls += 1
            if reasoning:
                entry.reasoning_calls += 1

    async def record_usage(
        self,
        request_id: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        estimated_cost: float = 0.0,
    ) -> None:
        async with self._lock:
            entry = self._entries.setdefault(
                request_id,
                BudgetEntry(started_at=time.monotonic()),
            )
            entry.input_tokens += max(int(input_tokens), 0)
            entry.output_tokens += max(int(output_tokens), 0)
            entry.estimated_cost += max(float(estimated_cost), 0.0)

    async def snapshot(self, request_id: str) -> dict[str, float | int]:
        async with self._lock:
            entry = self._entries.get(request_id)
            if entry is None:
                return {
                    "llm_calls": 0,
                    "reasoning_calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "estimated_cost": 0.0,
                    "elapsed_seconds": 0.0,
                }

            return {
                "llm_calls": entry.llm_calls,
                "reasoning_calls": entry.reasoning_calls,
                "input_tokens": entry.input_tokens,
                "output_tokens": entry.output_tokens,
                "estimated_cost": round(entry.estimated_cost, 6),
                "elapsed_seconds": round(
                    time.monotonic() - entry.started_at,
                    3,
                ),
            }

    async def release(self, request_id: str) -> None:
        async with self._lock:
            self._entries.pop(request_id, None)


budget_guard = EngineeringBudgetGuard()