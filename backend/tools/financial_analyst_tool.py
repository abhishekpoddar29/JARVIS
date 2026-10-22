from __future__ import annotations

import logging
from typing import Any

from agents.subgraphs.Financial_Analyst.financial_analyst import (
    financial_analyst_graph,
)
from agents.subgraphs.Financial_Analyst.financial_state import (
    build_initial_financial_state,
)
from tools.base import BaseTool, ToolResult


logger = logging.getLogger(__name__)


class FinancialAnalystTool(BaseTool):
    name: str = "financial_analyst_tool"

    description: str = (
        "Use this tool for structured financial research, financial health "
        "analysis, valuation-oriented analysis, and investment analysis of "
        "a public company or stock ticker. The tool resolves company identity, "
        "collects historical financial statements, gathers current web context, "
        "builds deterministic financial facts and evidence, reconciles figures, "
        "reviews accounting issues when signaled, calculates financial metrics, "
        "and returns an analyst-style final report. Pass the exact ticker symbol "
        "and analysis type."
    )

    async def run(
        self,
        ticker: str,
        company_name: str = "",
        analysis_type: str = "Valuation and Financial Health",
        analysis_depth: str = "standard",
        investment_horizon: str = "long_term",
        **kwargs: Any,
    ) -> ToolResult:
        ticker = ticker.strip().upper()
        user_company_name = (
            company_name
            or kwargs.get("company_name", "")
            or kwargs.get("user_company_name", "")
            or kwargs.get("original_company_name", "")
            or ""
        ).strip()


        logger.info(
            "[Financial Analyst] Starting analysis for %s",
            ticker,
        )

        try:
            initial_state = build_initial_financial_state({
                "ticker": ticker,
                "company_name": company_name,
                "user_company_name": user_company_name,
                "original_company_name": user_company_name,
                "analysis_type": analysis_type,
                "analysis_depth": analysis_depth,
                "investment_horizon": investment_horizon,
                "compare_with": kwargs.get("compare_with", []),
                "generate_excel": False,
                "generate_ppt": False,
                "report_language": kwargs.get("report_language", "English"),
            })

            result = await financial_analyst_graph.ainvoke(initial_state)

            execution = result.get("execution", {})
            failed_nodes = execution.get("failed_nodes", [])
            identity_validation = result.get("identity_validation", {})

            audit = result.get("audit", {})
            audit_events = audit.get("events", [])

            company = result.get("company", {})
            company_name = company.get("company_name") or ticker

            sources = result.get("sources", {}).get("sources", [])
            source_texts = result.get("source_texts", {}).get("texts", [])
            evidence = result.get("evidence", {}).get("evidence", [])
            facts = result.get("financial_facts", {}).get("facts", [])

            reconciliation = result.get("reconciliation", {})
            unresolved = reconciliation.get(
                "unresolved_conflict_ids",
                [],
            )

            accounting = result.get("accounting", {})
            accounting_status = accounting.get("status", "PENDING")

            metrics_state = result.get("metrics", {})
            metrics = metrics_state.get("metrics", [])
            final_report = result.get("final_report", {})

            completed_metrics = [
                metric
                for metric in metrics
                if metric.get("status") == "COMPLETED"
            ]

            failed_metrics = [
                metric
                for metric in metrics
                if metric.get("status") == "FAILED"
            ]

            if identity_validation.get("status") == "FAILED":
                reason = identity_validation.get(
                    "validation_reason",
                    "Identity validation failed.",
                )

                return ToolResult(
                    ok=False,
                    content=(
                        "Financial analysis stopped before historical "
                        "financial retrieval because company identity "
                        f"validation failed.\n{reason}"
                    ),
                    data=result,
                    error=reason,
                )

            if failed_nodes:
                last_error = self._latest_failure_message(
                    audit_events=audit_events,
                )

                return ToolResult(
                    ok=False,
                    content=(
                        "Financial analysis pipeline failed.\n"
                        f"Company: {company_name}.\n"
                        f"Failed nodes: {', '.join(failed_nodes)}.\n"
                        f"Reason: {last_error}"
                    ),
                    data=result,
                    error=last_error,
                )

            if not sources:
                return ToolResult(
                    ok=False,
                    content=(
                        "Financial analysis pipeline failed.\n"
                        f"Company: {company_name}.\n"
                        "Reason: No financial source documents were found."
                    ),
                    data=result,
                    error="No financial source documents found.",
                )


            if not evidence:
                return ToolResult(
                    ok=False,
                    content=(
                        "Financial analysis pipeline failed.\n"
                        f"Company: {company_name}.\n"
                        "Reason: No evidence records were extracted from "
                        "the scraped source text."
                    ),
                    data=result,
                    error="No evidence records extracted.",
                )

            if not facts:
                return ToolResult(
                    ok=False,
                    content=(
                        "Financial analysis pipeline failed.\n"
                        f"Company: {company_name}.\n"
                        "Reason: No financial facts were extracted. "
                        "Metrics cannot be calculated without financial facts."
                    ),
                    data=result,
                    error="No financial facts extracted.",
                )

            if unresolved:
                return ToolResult(
                    ok=False,
                    content=(
                        "Financial analysis pipeline could not complete "
                        "cleanly.\n"
                        f"Company: {company_name}.\n"
                        f"Extracted facts: {len(facts)}.\n"
                        f"Unresolved financial conflicts: {len(unresolved)}."
                    ),
                    data=result,
                    error="Unresolved financial conflicts remain.",
                )

            if not metrics:
                return ToolResult(
                    ok=False,
                    content=(
                        "Financial analysis pipeline failed.\n"
                        f"Company: {company_name}.\n"
                        "Reason: Financial facts were extracted, but no "
                        "metrics were generated."
                    ),
                    data=result,
                    error="No metrics generated.",
                )

            if not completed_metrics:
                return ToolResult(
                    ok=False,
                    content=(
                        "Financial analysis pipeline could not calculate "
                        "usable metrics.\n"
                        f"Company: {company_name}.\n"
                        f"Metrics attempted: {len(metrics)}.\n"
                        f"Metrics failed: {len(failed_metrics)}."
                    ),
                    data=result,
                    error="No completed financial metrics.",
                )

            if accounting_status == "COMPLETED":
                accounting_summary = (
                    "Accounting comparability broken: "
                    f"{accounting.get('comparability_broken', False)}."
                )
            else:
                accounting_summary = (
                    f"Accounting review status: {accounting_status}."
                )

            if (
                final_report.get("status") == "COMPLETED" and final_report.get("report_text")):
                return ToolResult(
                    ok=True,
                    content=final_report["report_text"],
                    data=result,
                )

            content = (
                f"Financial research completed for {company_name}.\n"
                f"Sources registered: {len(sources)}.\n"
                f"Sources scraped: {len(source_texts)}.\n"
                f"Evidence records extracted: {len(evidence)}.\n"
                f"Financial facts extracted: {len(facts)}.\n"
                f"Metrics calculated: {len(completed_metrics)}.\n"
                f"Metrics failed: {len(failed_metrics)}.\n"
                f"Unresolved financial conflicts: {len(unresolved)}.\n"
                f"{accounting_summary}"
            )

            return ToolResult(
                ok=True,
                content=content,
                data=result,
            )

        except Exception as exc:
            logger.exception(
                "[Financial Analyst] Pipeline crashed"
            )

            return ToolResult(
                ok=False,
                content=(
                    f"Financial analysis pipeline crashed: {exc}"
                ),
                data={},
                error=str(exc),
            )

    def _latest_failure_message(
        self,
        audit_events: list[dict[str, Any]],
    ) -> str:
        for event in reversed(audit_events):
            action = event.get("action", "")

            if action.endswith("_FAILED"):
                return event.get(
                    "message",
                    "Unknown graph node failure.",
                )

        return "Unknown graph node failure."


financial_analyst_tool = FinancialAnalystTool()