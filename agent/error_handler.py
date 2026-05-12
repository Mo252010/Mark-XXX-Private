# agent/error_handler.py
# Smart error recovery for the ATLAS task executor.
# Combines local pattern-matching (fast, free) with AI analysis (thorough).

import json
import re
import sys
import logging
from pathlib import Path
from enum import Enum
from collections import defaultdict

log = logging.getLogger("ErrorHandler")
logging.basicConfig(level=logging.INFO, format="[%(name)s] %(levelname)s %(message)s")


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"


class ErrorDecision(Enum):
    RETRY  = "retry"
    SKIP   = "skip"
    REPLAN = "replan"
    ABORT  = "abort"


# ─── Circuit breaker ─────────────────────────────────────────────────────────
# Tracks consecutive failures per tool; trips after _CIRCUIT_TRIP_COUNT failures.

_FAIL_COUNTS: dict = defaultdict(int)
_CIRCUIT_TRIP_COUNT = 3


def record_failure(tool: str) -> None:
    _FAIL_COUNTS[tool] += 1


def record_success(tool: str) -> None:
    """Call this from the executor when a step succeeds — resets the circuit."""
    _FAIL_COUNTS[tool] = 0


def _circuit_open(tool: str) -> bool:
    return _FAIL_COUNTS[tool] >= _CIRCUIT_TRIP_COUNT


# ─── Local fast-path classifier ───────────────────────────────────────────────
# Handles ~60 % of errors instantly without an API call.

_RETRY_PATTERNS = [
    "timeout", "timed out", "connection reset", "temporary",
    "too many requests", "rate limit", "503", "502",
    "network", "socket", "ssl", "eof",
]
_ABORT_PATTERNS = [
    "permission denied", "access denied", "unauthorized", "403", "401",
    "drive is not ready", "disk full", "no space left",
    "keyboardinterrupt", "systemexit",
]
_REPLAN_PATTERNS = [
    "filenotfounderror", "no such file", "module not found", "importerror",
    "attributeerror", "typeerror", "valueerror", "element not found",
    "no element", "cannot find", "selector", "does not exist", "404",
]


def _classify_locally(error: str) -> "ErrorDecision | None":
    """
    Pattern-match the error string against known categories.
    Returns None if ambiguous (AI analysis needed).
    """
    lower = error.lower()
    for pat in _ABORT_PATTERNS:
        if pat in lower:
            return ErrorDecision.ABORT
    for pat in _RETRY_PATTERNS:
        if pat in lower:
            return ErrorDecision.RETRY
    for pat in _REPLAN_PATTERNS:
        if pat in lower:
            return ErrorDecision.REPLAN
    return None


def retry_delay(attempt: int) -> float:
    """Exponential backoff: 1s → 2s → 4s → … (capped at 8 s)."""
    return min(2 ** (attempt - 1), 8)


# ─── Prompts ─────────────────────────────────────────────────────────────────

ERROR_ANALYST_PROMPT = """You are the error recovery module of the ATLAS AI assistant.

A task step has failed. Analyze the error and decide what to do.

DECISIONS:
- retry   : Transient error (network timeout, file lock, race condition).
- skip    : Non-critical step; the task can succeed without it.
- replan  : Wrong approach. A different tool or method should be tried.
- abort   : Task is fundamentally impossible or unsafe to continue.

Return ONLY valid JSON (no markdown, no prose):
{
  "decision": "retry|skip|replan|abort",
  "reason": "one-sentence explanation",
  "fix_suggestion": "what to try instead (replan only, else empty string)",
  "max_retries": 1,
  "user_message": "max 15 words for the user"
}"""


# ─── API helpers ─────────────────────────────────────────────────────────────

def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _ai_analyze(step: dict, error: str, attempt: int) -> dict:
    """Calls the AI model for error analysis. Raises on failure."""
    import google.generativeai as genai
    genai.configure(api_key=_get_api_key())
    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash-lite",
        system_instruction=ERROR_ANALYST_PROMPT,
    )
    prompt = (
        f"Tool        : {step.get('tool')}\n"
        f"Description : {step.get('description')}\n"
        f"Parameters  : {json.dumps(step.get('parameters', {}), indent=2)}\n"
        f"Critical    : {step.get('critical', False)}\n"
        f"Attempt     : {attempt}\n\n"
        f"Error:\n{error[:600]}"
    )
    response = model.generate_content(prompt)
    text = re.sub(r"```(?:json)?", "", response.text.strip()).strip("`").strip()
    return json.loads(text)


def _default_message(decision: ErrorDecision) -> str:
    return {
        ErrorDecision.RETRY:  "Retrying, sir.",
        ErrorDecision.SKIP:   "Skipping this step, sir.",
        ErrorDecision.REPLAN: "Adjusting approach, sir.",
        ErrorDecision.ABORT:  "Unable to complete this task, sir.",
    }.get(decision, "Working on it, sir.")


def _make_result(
    decision: ErrorDecision,
    reason: str = "",
    fix: str = "",
    max_retries: int = 0,
    user_msg: str = "",
    attempt: int = 1,
) -> dict:
    return {
        "decision":       decision,
        "reason":         reason,
        "fix_suggestion": fix,
        "max_retries":    max_retries,
        "user_message":   user_msg or _default_message(decision),
        "backoff_delay":  retry_delay(attempt) if decision == ErrorDecision.RETRY else 0,
    }


# ─── Public API ───────────────────────────────────────────────────────────────

def analyze_error(
    step: dict,
    error: str,
    attempt: int = 1,
    max_attempts: int = 2,
) -> dict:
    """
    Analyzes a failed step and returns a recovery decision.

    Routing:
      1. Circuit breaker  — if the same tool keeps failing, stop immediately.
      2. Max-attempts     — force REPLAN after too many retries.
      3. Local fast-path  — pattern-match common errors (no API cost/latency).
      4. AI slow-path     — call gemini-2.5-flash-lite for ambiguous errors.

    Returns a dict with keys:
        decision, reason, fix_suggestion, max_retries, user_message, backoff_delay
    """
    tool = step.get("tool", "unknown")
    record_failure(tool)

    # 1 ── Circuit breaker
    if _circuit_open(tool):
        log.warning("Circuit open for tool '%s' (failed %d times) — aborting.", tool, _FAIL_COUNTS[tool])
        return _make_result(
            ErrorDecision.ABORT,
            reason=f"Tool '{tool}' has failed {_FAIL_COUNTS[tool]} consecutive times.",
            user_msg=f"Tool '{tool}' keeps failing — stopping, sir.",
        )

    # 2 ── Max attempts
    if attempt >= max_attempts:
        log.warning("Max attempts reached for step '%s' — forcing REPLAN.", step.get("step"))
        return _make_result(
            ErrorDecision.REPLAN,
            reason=f"Failed {attempt} time(s): {error[:120]}",
            fix="Try a completely different approach or tool.",
            user_msg="Trying a different approach, sir.",
        )

    # 3 ── Local fast-path
    local = _classify_locally(error)
    if local is not None:
        log.info("Fast-path decision: %s", local.value)
        if step.get("critical") and local == ErrorDecision.SKIP:
            local = ErrorDecision.REPLAN
        return _make_result(
            local,
            reason=f"Pattern-matched: {error[:120]}",
            fix="Try alternative approach." if local == ErrorDecision.REPLAN else "",
            max_retries=1 if local == ErrorDecision.RETRY else 0,
            attempt=attempt,
        )

    # 4 ── AI slow-path
    try:
        result = _ai_analyze(step, error, attempt)
    except Exception as e:
        log.warning("AI analysis failed (%s) — defaulting to REPLAN.", e)
        return _make_result(
            ErrorDecision.REPLAN,
            reason=str(e),
            fix="Try alternative approach.",
            max_retries=1,
            user_msg="Encountered an issue — adjusting approach, sir.",
        )

    decision_map = {
        "retry":  ErrorDecision.RETRY,
        "skip":   ErrorDecision.SKIP,
        "replan": ErrorDecision.REPLAN,
        "abort":  ErrorDecision.ABORT,
    }
    decision = decision_map.get(result.get("decision", "replan").lower(), ErrorDecision.REPLAN)
    if step.get("critical") and decision == ErrorDecision.SKIP:
        decision = ErrorDecision.REPLAN
        result["user_message"] = "Critical step failed — finding alternative, sir."

    log.info("AI decision: %s — %s", decision.value, result.get("reason", ""))
    return {
        "decision":       decision,
        "reason":         result.get("reason", ""),
        "fix_suggestion": result.get("fix_suggestion", ""),
        "max_retries":    result.get("max_retries", 1),
        "user_message":   result.get("user_message") or _default_message(decision),
        "backoff_delay":  retry_delay(attempt) if decision == ErrorDecision.RETRY else 0,
    }


def generate_fix(step: dict, error: str, fix_suggestion: str) -> dict:
    """
    When decision is REPLAN, generates a replacement step that runs
    the same goal via code_helper. Falls back to the original step on failure.
    """
    import google.generativeai as genai
    genai.configure(api_key=_get_api_key())
    model = genai.GenerativeModel(model_name="gemini-2.0-flash")

    prompt = (
        f"A task step failed. Write a Python script to accomplish the same goal differently.\n\n"
        f"Original tool : {step.get('tool')}\n"
        f"Description   : {step.get('description')}\n"
        f"Parameters    : {json.dumps(step.get('parameters', {}), indent=2)}\n"
        f"Error         : {error[:300]}\n"
        f"Fix suggestion: {fix_suggestion}\n\n"
        f"Return ONLY executable Python code. No markdown fences. No explanation."
    )

    try:
        response = model.generate_content(prompt)
        code = re.sub(r"```(?:python)?", "", response.text.strip()).strip("`").strip()
        return {
            "step":        step.get("step"),
            "tool":        "code_helper",
            "description": f"Auto-fix: {step.get('description')}",
            "parameters": {
                "action":      "run",
                "description": fix_suggestion,
                "code":        code,
                "language":    "python",
            },
            "depends_on": step.get("depends_on", []),
            "critical":   step.get("critical", False),
        }

    except Exception as e:
        log.warning("Fix generation failed (%s) — returning original step as fallback.", e)
        # Return original step unchanged so the executor can try the same spec once more
        return {
            "step":        step.get("step"),
            "tool":        step.get("tool"),
            "description": step.get("description", ""),
            "parameters":  step.get("parameters", {}),
            "depends_on":  step.get("depends_on", []),
            "critical":    step.get("critical", False),
        }