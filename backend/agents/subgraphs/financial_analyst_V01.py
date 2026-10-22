'''
from __future__ import annotations
import asyncio
import json
import re
from typing import TypedDict, Any, Literal
from langgraph.graph import END, START, StateGraph

from agents.gemini_client import generate_reply, GeminiClientError
from config import get_settings
from tools.code_execution_tool import code_agent
from tools.web_scraper_tool import WebScraperTool
from agents.research_agent import ResearchAgent, ResearchDepth

settings = get_settings()
ANALYST_MODEL = "gemini-3.5-flash"

# Shared instruction appended to every narrative-writing prompt. Prevents the
# model from quietly estimating a number it was never given.
ANTI_HALLUCINATION_CLAUSE = (
    "\nIMPORTANT: If a specific metric, figure, or fact is not present in the "
    "data provided above, explicitly write 'data unavailable' for that item. "
    "Do not estimate, infer, or invent a number that was not supplied to you."
)

# NEW: Forces the model to treat any hard-coded ⚠ FLAG line as non-negotiable —
# a real Wall Street analyst doesn't "average" a solvency risk against a nice
# growth story. This is what prevents a BUY from being issued over an unaddressed
# leverage or cash-burn problem.
FLAG_RECONCILIATION_CLAUSE = (
    "\nCRITICAL — RISK RECONCILIATION RULE: If any line beginning with '⚠ FLAG' "
    "appears in the core ratios, forensic findings, or controversy context provided "
    "to you, you MUST explicitly name and address that flag in your written output. "
    "You are not permitted to omit it, soften it into vague language, or let a "
    "positive qualitative narrative silently outweigh it. If you are writing a "
    "recommendation and a flag is present, you must either (a) explicitly justify "
    "in writing why the recommendation still holds despite the flagged risk, or "
    "(b) downgrade the recommendation accordingly. Never issue a Buy rating "
    "while a leverage, cash-flow, or legal/regulatory flag goes unaddressed."
)

class FinancialAnalystState(TypedDict, total=False):
    ticker: str
    analysis_type: str
    raw_financials: str
    research_context: str
    controversy_context: str
    is_data_valid: bool
    stock_type: str
    core_ratios: str
    kpi_calculations: str
    qualitative_analysis: str
    risk_assessment: str
    investment_thesis: str
    final_report: str
    pptx_report: str

def strip_markdown(text: str) -> str:
    pattern = r"```(?:python)?\s*\n(.*?)\n```"
    matches = re.findall(pattern, text, re.DOTALL)
    if matches:
        return "\n\n".join(match.strip() for match in matches)
    text = text.strip()
    if text.startswith("```python"): text = text[9:]
    elif text.startswith("```"): text = text[3:]
    return text.strip()

def extract_schema(stdout: str) -> str:
    match = re.search(r'(\[.*\]|\{.*\})', stdout, re.DOTALL)
    if match: return match.group(1)
    return stdout

# ── Node 1: Data Fetcher ───────────────────────────────────────────────────
async def data_fetcher(state: FinancialAnalystState) -> dict:
    print("\n[FinAnalyst] 📈 Fetching historical financial data...")
    ticker = state["ticker"]
    
    task = f"""Write a python script that uses the `yfinance` library to get the last 3 years of annual financial data for '{ticker}'.
    Retrieve the income statement, balance sheet, cash flow, and basic info (market cap, beta, sector).
    
    CRITICAL JSON FIX: Pandas DataFrames from yfinance use Timestamp objects as columns. Before converting to a dictionary, you MUST convert columns to strings:
    `df.columns = df.columns.astype(str)`
    
    Convert the dataframes to dictionaries. 
    Print a single JSON object to stdout containing all the data. 
    DO NOT save any files.
    CRITICAL: Add `import warnings; warnings.filterwarnings('ignore')` at the top. Output ONLY raw Python code.
    """
    try:
        result = await code_agent.execute(task)
        stdout = getattr(result, 'stdout', '') or ''
        json_start = stdout.find('{')
        json_end = stdout.rfind('}') + 1
        raw_financials = stdout[json_start:json_end] if json_start != -1 and json_end != -1 else ""
        return {"raw_financials": raw_financials}
    except Exception as e:
        return {"raw_financials": ""}

# ── Node 2: Data Validator ─────────────────────────────────────────────────
async def data_validator(state: FinancialAnalystState) -> dict:
    print("\n[FinAnalyst] ✅ Validating fetched data...")
    raw_data = state.get("raw_financials", "")

    # FIX: Original check `bool(raw_data) and "Total Revenue" in raw_data or "totalRevenue" in raw_data`
    # had an operator-precedence bug — it parsed as `(bool(raw_data) and A) or B`, so a
    # non-empty but garbage string containing "totalRevenue" anywhere (e.g. an error message)
    # could still pass validation. Parenthesize explicitly and prefer a real JSON parse.
    is_valid = False
    if raw_data:
        try:
            parsed = json.loads(raw_data)
            is_valid = "Total Revenue" in str(parsed) or "totalRevenue" in str(parsed)
        except json.JSONDecodeError:
            is_valid = ("Total Revenue" in raw_data) or ("totalRevenue" in raw_data)

    if not is_valid:
        print("[FinAnalyst] ⚠️ yfinance data invalid or empty.")
    return {"is_data_valid": is_valid}

# ── Node 3: Fallback Scraper ───────────────────────────────────────────────
async def fallback_scraper(state: FinancialAnalystState) -> dict:
    print("\n[FinAnalyst] 🌐 Falling back to web scraping for financials...")
    ticker = state["ticker"]
    scraper = WebScraperTool()
    try:
        result = await scraper.run(url=f"https://finance.yahoo.com/quote/{ticker}/key-statistics/")
        if result.ok:
            return {"raw_financials": f"Fallback Scraped Data:\n{result.content[:3000]}"}
    except Exception:
        pass
    return {"raw_financials": "Data fetch failed entirely. Insufficient data."}

# ── Node 4: Market Researcher (Powered by ResearchAgent) ───────────────────
async def market_researcher(state: FinancialAnalystState) -> dict:
    print("\n[FinAnalyst] 🔍 Gathering qualitative market research (via Deep Research Agent)...")
    ticker = state["ticker"]
    
    try:
        agent = ResearchAgent()
    except RuntimeError as e:
        print(f"[FinAnalyst] ⚠️ ResearchAgent unavailable (API keys missing?): {e}")
        return {
            "research_context": "Research agent unavailable.",
            "controversy_context": "Controversy scan unavailable."
        }

    general_topic = f"{ticker} company profile business model competitive advantage moat management team"
    controversy_topic = f"{ticker} lawsuit investigation controversy regulatory probe SEC fraud"

    # F14: Create a callback function that emits progress over the WebSocket safely
    async def _emit_progress(msg: str):
        try:
            from agents.graph_nodes import orb_emitter, progress_emitter
            emit_orb = orb_emitter.get()
            emit_prog = progress_emitter.get()
            if emit_orb:
                await emit_orb("processing")
            if emit_prog:
                await emit_prog(f"[Research] {msg}")
        except Exception:
            pass # Never crash research due to UI emit failure

    # Wrapper to catch exceptions per-task so one failing track doesn't kill the other
    async def _safe_run(topic: str, depth: ResearchDepth):
        try:
            return await agent.run(topic=topic, depth=depth, progress_callback=_emit_progress)
        except Exception as e:
            print(f"[FinAnalyst] ⚠️ Research track failed ('{topic}'): {e}")
            return None

    print(f"[FinAnalyst] Launching parallel deep research threads for {ticker}...")
    
    try:
        # Run both research tracks in parallel
        general_report, controversy_report = await asyncio.gather(
            _safe_run(general_topic, ResearchDepth.DEEP),
            _safe_run(controversy_topic, ResearchDepth.QUICK)
        )

        # Extract the synthesized Markdown reports safely
        research_context = general_report.markdown[:8000] if general_report and general_report.markdown else "No recent news or qualitative data found."
        controversy_context = controversy_report.markdown[:3000] if controversy_report and controversy_report.markdown else "No litigation, investigation, or regulatory controversy found in search."
        
        print(f"[FinAnalyst] ✅ Deep research retrieved ({len(research_context)} chars). Controversy scan: {len(controversy_context)} chars.")
        
        # This return dictionary is exactly what company_classifier reads from state
        return {
            "research_context": research_context,
            "controversy_context": controversy_context,
        }
    except Exception as e:
        print(f"[FinAnalyst] ⚠️ Deep research aggregation failed: {e}")
        return {
            "research_context": "Research failed during execution.",
            "controversy_context": "Controversy scan failed."
        }
    
# ── Node 5: Company Classifier ─────────────────────────────────────────────
async def company_classifier(state: FinancialAnalystState) -> dict:
    print("\n[FinAnalyst] 🧠 Classifying stock archetype (Growth vs Value)...")
    raw_data = state.get("raw_financials", "N/A")
    research = state.get("research_context", "N/A")
    
    prompt = f"""Analyze the following financial data and market research for '{state['ticker']}'.
    Classify this stock as exactly one of the following:
    - "growth" (High revenue growth, high P/E, reinvesting profits, tech/biotech)
    - "value" (Low P/E, undervalued, stable cash flows, banking/energy)
    - "dividend" (Mature, high dividend yield, utilities/REITs)
    
    Look for clues in both the raw financial data (P/E, revenue growth, dividends) and the qualitative market research (business model, sector).
    Respond with ONLY the lowercase string. No punctuation.
    
    Financial Data: {raw_data[:2000]}
    Market Research: {research[:1500]}
    """
    loop = asyncio.get_running_loop()
    try:
        reply = await loop.run_in_executor(
            None, lambda: generate_reply(
                api_key=settings.gemini_api_key, model=ANALYST_MODEL,
                user_message=prompt, system_instruction="You are a Portfolio Manager.",
                temperature=0.1, max_output_tokens=50,
            )
        )
        stock_type = reply.text.strip().lower()
        if stock_type not in ["growth", "value", "dividend"]:
            stock_type = "value"
        print(f"[FinAnalyst] Classification: {stock_type.upper()}")
        return {"stock_type": stock_type}
    except Exception:
        return {"stock_type": "value"}

# ── Node 6: Core Ratios (always runs, regardless of archetype) ────────────
async def core_ratios(state: FinancialAnalystState) -> dict:
    print("\n[FinAnalyst] 📐 Computing core valuation & solvency ratios...")
    ticker = state["ticker"]
    task = f"""Write a python script using `yfinance` for '{ticker}'.
    Calculate and print these core ratios, which apply regardless of stock archetype:
    1. Trailing P/E and Forward P/E.
    2. Price-to-Book (P/B).
    3. EV/EBITDA.
    4. Beta.
    5. Total Debt and Debt-to-Equity ratio.
    6. Free Cash Flow (Operating Cash Flow minus Capital Expenditures) for each of the last 3 fiscal years available, printed individually by year — NOT only as an input to a DCF formula. Also print FCF Margin (most recent year FCF / most recent year Revenue).
    If any value is unavailable from yfinance, print "N/A" for that specific metric rather than skipping it silently.

    CRITICAL SIGNAL FLAGS — after computing the above, add these explicit checks and print a line for each that triggers:
    - If Debt-to-Equity > 2.0: print "⚠ FLAG: HIGH LEVERAGE — Debt/Equity = <value>"
    - If the most recent year's Free Cash Flow is negative: print "⚠ FLAG: NEGATIVE FCF — <value> in <year>, driven by capex of <capex value> vs operating cash flow of <ocf value>"
    - If Free Cash Flow has declined for 2+ consecutive years: print "⚠ FLAG: DETERIORATING FCF TREND"
    These flag lines must be plain, unmissable, and printed even if the rest of the summary looks favorable.

    Print a clean summary to stdout. DO NOT save files.
    CRITICAL: Add `import warnings; warnings.filterwarnings('ignore')` at top. Output ONLY raw Python code.
    """
    try:
        result = await code_agent.execute(task)
        return {"core_ratios": getattr(result, 'stdout', '') or "Core ratio calculation failed."}
    except Exception as e:
        return {"core_ratios": f"Core ratio calculation failed: {e}"}

# ── Conditional Edge: Route Valuation ──────────────────────────────────────
def route_valuation(state: FinancialAnalystState) -> Literal["growth_valuation", "value_valuation"]:
    if state.get("stock_type") == "growth":
        return "growth_valuation"
    return "value_valuation"

# ── Node 7a: Growth Valuation Engine ───────────────────────────────────────
async def growth_valuation(state: FinancialAnalystState) -> dict:
    print("\n[FinAnalyst] 🚀 Running Growth Valuation (DCF, Margins, PEG)...")
    ticker = state["ticker"]
    task = f"""Write a python script using `yfinance` for '{ticker}'.
    Calculate Growth metrics:
    1. Revenue CAGR (Compound Annual Growth Rate) for last 3 years.
    2. Discounted Cash Flow (DCF) assuming 15% growth for 5 years, 3% terminal.
       If the most recent Free Cash Flow is negative or unavailable, DO NOT simply print "N/A" and stop —
       explicitly print the actual FCF figure that made the DCF infeasible, and note in one line that DCF
       is not meaningful until FCF turns positive. Never let a failed DCF hide the underlying FCF number.
    3. Price-to-Sales (P/S) and Price-to-Earnings-Growth (PEG) ratio.
    Print a clean summary to stdout. DO NOT save files.
    CRITICAL: Add `import warnings; warnings.filterwarnings('ignore')` at top. Output ONLY raw Python code.
    """
    try:
        result = await code_agent.execute(task)
        return {"kpi_calculations": getattr(result, 'stdout', '') or "Growth math failed."}
    except Exception as e:
        return {"kpi_calculations": f"Growth math failed: {e}"}

# ── Node 7b: Value/Dividend Valuation Engine ───────────────────────────────
async def value_valuation(state: FinancialAnalystState) -> dict:
    print("\n[FinAnalyst] 🏦 Running Value/Dividend Valuation (Multiples heavy)...")
    ticker = state["ticker"]
    task = f"""Write a python script using `yfinance` for '{ticker}'.
    Calculate Value/Dividend metrics:
    1. Graham Number (sqrt(22.5 * EPS * Book Value per Share)).
    2. Debt-to-Equity ratio and Current Ratio.
    3. Dividend Yield and Payout Ratio.
    Print a clean summary to stdout. DO NOT save files.
    CRITICAL: Add `import warnings; warnings.filterwarnings('ignore')` at top. Output ONLY raw Python code.
    """
    try:
        result = await code_agent.execute(task)
        return {"kpi_calculations": getattr(result, 'stdout', '') or "Value math failed."}
    except Exception as e:
        return {"kpi_calculations": f"Value math failed: {e}"}

# ── Node 8: Qualitative Strategist ─────────────────────────────────────────
async def qualitative_strategist(state: FinancialAnalystState) -> dict:
    print("\n[FinAnalyst] ♟️ Evaluating Economic Moat & Management Quality...")
    ticker = state["ticker"]
    research = state.get("research_context", "N/A")
    analysis_type = state.get("analysis_type", "Valuation and Financial Health")
    
    prompt = f"""You are a Qualitative Equity Strategist. Analyze the qualitative factors for '{ticker}' based on the market research.
    Requested analysis focus: {analysis_type}
    Evaluate the following:
    1. Economic Moat: Does the company have a competitive advantage? (Brand strength, high switching costs, network effects, or patents).
    2. Management Quality: Assess capital allocation track record, executive tenure, and any mentions of institutional/promoter holdings.
    
    Market Research:
    {research[:3000]}
    
    Write a concise 2-paragraph qualitative analysis in Markdown.
    {ANTI_HALLUCINATION_CLAUSE}
    """
    loop = asyncio.get_running_loop()
    try:
        reply = await loop.run_in_executor(None, lambda: generate_reply(api_key=settings.gemini_api_key, model=ANALYST_MODEL, user_message=prompt, system_instruction="You are an expert Strategic Analyst.", temperature=0.3, max_output_tokens=1000))
        return {"qualitative_analysis": reply.text}
    except Exception:
        return {"qualitative_analysis": "Qualitative analysis failed."}

# ── Node 9: Risk Assessor ──────────────────────────────────────────────────
async def risk_assessor(state: FinancialAnalystState) -> dict:
    print("\n[FinAnalyst] ⚠️ Assessing risks & forensic accounting red flags...")
    ticker = state["ticker"]
    research = state.get("research_context", "N/A")
    raw_data = state.get("raw_financials", "N/A")
    core = state.get("core_ratios", "N/A")
    controversy = state.get("controversy_context", "N/A")
    
    task = f"""Write a python script using `yfinance` for '{ticker}' to perform Forensic Accounting checks:
    1. Aggressive Accounting: Compare Revenue growth vs Accounts Receivable growth over the last 2 years. If Receivables are growing much faster than Revenue, flag it as "⚠ FLAG: AGGRESSIVE ACCOUNTING".
    2. Cash Flow vs Net Income: Compare Net Income vs Operating Cash Flow. If Net Income is positive but OCF is negative, flag it as "⚠ FLAG: EARNINGS QUALITY CONCERN".
    Print findings to stdout. DO NOT save files.
    CRITICAL: Add `import warnings; warnings.filterwarnings('ignore')` at top. Output ONLY raw Python code.
    """
    loop = asyncio.get_running_loop()
    try:
        result = await code_agent.execute(task)
        forensic_math = getattr(result, 'stdout', '') or "Forensic math failed."
        
        risk_prompt = f"""You are a Risk Manager. Analyze the risks for '{ticker}'.

        Core ratios including Beta, Debt, and FCF trend (check for ⚠ FLAG lines): {core[:1500]}
        Raw financial data (for additional context): {raw_data[:1500]}
        General market research: {research[:1500]}
        Controversy / legal / regulatory scan (check for lawsuits, investigations, probes): {controversy[:1500]}
        Forensic Accounting findings (check for ⚠ FLAG lines): {forensic_math[:1000]}

        Write a concise risk assessment in Markdown, highlighting any red flags.
        You MUST explicitly reference the Beta and Debt-to-Equity figures above if present, and you
        MUST explicitly state whether the controversy scan found any pending litigation, investigation,
        or regulatory action — do not omit this even if it seems minor relative to the company's size.
        {FLAG_RECONCILIATION_CLAUSE}
        {ANTI_HALLUCINATION_CLAUSE}
        """
        risk_reply = await loop.run_in_executor(None, lambda: generate_reply(api_key=settings.gemini_api_key, model=ANALYST_MODEL, user_message=risk_prompt, system_instruction="You are a Risk Manager.", temperature=0.3, max_output_tokens=1200))
        return {"risk_assessment": risk_reply.text}
    except Exception:
        return {"risk_assessment": "Risk assessment failed."}

# ── Node 10: Investment Thesis ──────────────────────────────────────────────
async def investment_thesis(state: FinancialAnalystState) -> dict:
    print("\n[FinAnalyst] 📝 Drafting C-Suite investment thesis...")
    ticker = state["ticker"]
    stock_type = state.get("stock_type", "N/A").upper()
    analysis_type = state.get("analysis_type", "Valuation and Financial Health")
    raw_data = state.get("raw_financials", "N/A")
    core = state.get("core_ratios", "N/A")
    kpis = state.get("kpi_calculations", "N/A")
    risks = state.get("risk_assessment", "N/A")
    qualitative = state.get("qualitative_analysis", "N/A")
    controversy = state.get("controversy_context", "N/A")
    
    prompt = f"""You are a Senior Equity Research Analyst writing at a Wall Street bulge-bracket standard.
    Write a comprehensive investment thesis in Markdown for '{ticker}'.
    Stock Archetype: {stock_type}
    Requested analysis focus: {analysis_type}
    
    1. Raw Data: {raw_data[:2000]}
    2. Core Ratios (P/E, P/B, EV/EBITDA, Beta, Debt, FCF trend — check for ⚠ FLAG lines): {core[:1500]}
    3. Archetype-Specific KPIs: {kpis}
    4. Qualitative Strategy (Moat/Management): {qualitative}
    5. Risk Assessment & Red Flags: {risks}
    6. Controversy / Legal / Regulatory Scan: {controversy[:1000]}
    
    Structure:
    # Investment Thesis: {ticker}
    ## Executive Summary
    ## Financial Health & KPIs
    ## Valuation Analysis
    ## Qualitative Strategy (Moat & Management)
    ## Risk Profile & Forensic Red Flags
    ## Final Recommendation (Buy/Hold/Sell)

    Before writing the Final Recommendation, silently check: does the Core Ratios or Risk
    Assessment text above contain any ⚠ FLAG line, or does the Controversy Scan mention active
    litigation, investigation, or regulatory action? If so, your Final Recommendation section
    must explicitly name that flag/controversy and explain how it factors into the rating — a
    strong qualitative story does not override an unaddressed solvency, cash-flow, or legal risk.
    {FLAG_RECONCILIATION_CLAUSE}
    {ANTI_HALLUCINATION_CLAUSE}
    """
    loop = asyncio.get_running_loop()
    try:
        reply = await loop.run_in_executor(None, lambda: generate_reply(api_key=settings.gemini_api_key, model=ANALYST_MODEL, user_message=prompt, system_instruction="You are an expert Financial Writer trained to write with the rigor and skepticism of a senior Wall Street equity research analyst.", temperature=0.3, max_output_tokens=4000))
        return {"investment_thesis": reply.text}
    except Exception as e:
        return {"investment_thesis": f"Thesis generation failed: {e}"}


# ── Node 11: Excel Exporter ────────────────────────────────────────────────
async def excel_exporter(state: FinancialAnalystState) -> dict:
    print("\n[FinAnalyst] 📊 Building professional Excel financial model...")
    ticker = state["ticker"]
    stock_type = state.get("stock_type", "value")
    
    # FIX: Remove dots from ticker for clean filenames (e.g., ADANIGREEN.BO -> ADANIGREEN_BO)
    safe_ticker = ticker.replace(".", "_")
    
    task = f"""Write a python script using `yfinance` and `pandas` to pull 3 years of data for '{ticker}'.
    Use `pd.ExcelWriter('./financial_model_{safe_ticker}.xlsx', engine='openpyxl')` to write the dataframes directly to sheets.
    
    CRITICAL: You MUST explicitly write `import openpyxl` at the top of the script, even if you only use pandas.to_excel.
    
    Create sheets: "Income Statement", "Balance Sheet", "Cash Flow".
    If a dataframe is empty, write "No data available" to cell A1 of that sheet.
    
    In a separate sheet named "KPIs", include a cell labeled "Stock Archetype" with the value "{stock_type}".
    
    Save the workbook. Print "EXCEL_FILE_SAVED" on success.
    Add `import warnings; warnings.filterwarnings('ignore')` at top. Output ONLY raw Python code.
    """
    try:
        result = await code_agent.execute(task)
        stdout = getattr(result, 'stdout', '') or ''
        stderr = getattr(result, 'stderr', '') or ''
        if getattr(result, 'success', True) and "EXCEL_FILE_SAVED" in stdout and "Traceback" not in stderr:
            return {"final_report": f"Excel model saved successfully as financial_model_{safe_ticker}.xlsx!"}
        else:
            return {"final_report": f"Excel model generation failed: {stderr or stdout[:300]}"}
    except Exception as e:
        return {"final_report": f"Excel code generation crashed: {e}"}

# ── Node 12: PowerPoint Exporter ───────────────────────────────────────────
async def pptx_exporter(state: FinancialAnalystState) -> dict:
    print("\n[FinAnalyst] 📊 Generating professional PowerPoint presentation...")
    ticker = state["ticker"]
    safe_ticker = ticker.replace(".", "_") # FIX: Clean ticker for filename
    thesis = state.get("investment_thesis", "N/A")
    kpis = state.get("kpi_calculations", "N/A")
    
    if "failed" in kpis.lower() or "error" in kpis.lower() or len(kpis) < 20:
        kpis = "KPI data unavailable or not applicable for this asset type."
    
    task = f"""Write a concise python script (under 3000 characters) using `python-pptx`, `matplotlib`, and `yfinance` for ticker '{ticker}'.
    Create a 3-slide presentation:
    Slide 1: Title "Investment Analysis: {ticker}"
    Slide 2: Bullet points of Executive Summary (first 500 chars of this text: {thesis[:500]})
    Slide 3: Bullet points of KPIs (first 500 chars of this text: {kpis[:500]})
    
    Save as `./financial_analysis_{safe_ticker}.pptx`. Print "PPTX_SAVED".
    CRITICAL: Output ONLY raw Python code. No markdown formatting, no explanations.
    """
    try:
        result = await code_agent.execute(task)
        stdout = getattr(result, 'stdout', '') or ''
        stderr = getattr(result, 'stderr', '') or ''
        if getattr(result, 'success', True) and "PPTX_SAVED" in stdout and "Traceback" not in stderr:
            return {"pptx_report": f"PowerPoint presentation saved successfully as financial_analysis_{safe_ticker}.pptx!"}
        else:
            error_msg = stderr or stdout or "Unknown PPTX generation error."
            return {"pptx_report": f"PowerPoint generation failed: {error_msg[:300]}"}
    except Exception as e:
        return {"pptx_report": f"PPTX code generation crashed: {e}"}

# ── Build the Linear Subgraph ──────────────────────────────────────────────
def build_financial_analyst_graph():
    graph = StateGraph(FinancialAnalystState)
    graph.add_node("data_fetcher", data_fetcher)
    graph.add_node("data_validator", data_validator)
    graph.add_node("fallback_scraper", fallback_scraper)
    graph.add_node("market_researcher", market_researcher)
    graph.add_node("company_classifier", company_classifier)
    graph.add_node("core_ratios", core_ratios)
    graph.add_node("growth_valuation", growth_valuation)
    graph.add_node("value_valuation", value_valuation)
    graph.add_node("qualitative_strategist", qualitative_strategist)
    graph.add_node("risk_assessor", risk_assessor)
    graph.add_node("investment_thesis", investment_thesis)
    graph.add_node("excel_exporter", excel_exporter)
    graph.add_node("pptx_exporter", pptx_exporter)
    
    # Linear flow to prevent LangGraph parallel convergence double-execution
    graph.add_edge(START, "data_fetcher")
    graph.add_edge("data_fetcher", "data_validator")
    graph.add_conditional_edges(
        "data_validator",
        lambda state: "fallback_scraper" if not state.get("is_data_valid") else "market_researcher"
    )
    graph.add_edge("fallback_scraper", "market_researcher")
    graph.add_edge("market_researcher", "company_classifier")

    # core_ratios always runs after classification, regardless of archetype,
    # so P/E, P/B, EV/EBITDA, Beta, Debt, and FCF trend are never silently dropped.
    graph.add_edge("company_classifier", "core_ratios")
    graph.add_conditional_edges("core_ratios", route_valuation)
    
    graph.add_edge("growth_valuation", "qualitative_strategist")
    graph.add_edge("value_valuation", "qualitative_strategist")
    
    graph.add_edge("qualitative_strategist", "risk_assessor")
    graph.add_edge("risk_assessor", "investment_thesis")
    
    # Thesis fans out to Excel and PowerPoint in parallel (they go to END, so no convergence issue)
    graph.add_edge("investment_thesis", "excel_exporter")
    graph.add_edge("investment_thesis", "pptx_exporter")
    
    graph.add_edge("excel_exporter", END)
    graph.add_edge("pptx_exporter", END)
    
    return graph.compile()

financial_analyst_graph = build_financial_analyst_graph()

'''