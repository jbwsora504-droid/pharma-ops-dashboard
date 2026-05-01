"""
triage.py
---------
Rule-based severity scoring engine for pharmacy automation incidents.

Each LogEntry is evaluated against a set of weighted rules derived from
real operational patterns in pharmacy dispensing environments. The result
is a TriageResult containing a numeric severity score (0-100), a severity
tier (CRITICAL / HIGH / MEDIUM / LOW), and a structured context payload
that gets passed to the AI summarizer.

Scoring approach:
- Base score is determined by event source and code
- Modifiers are applied for system ID patterns, keyword presence, and
  time-of-day risk factors (e.g. overnight faults with no on-site staff)
- Final score is clamped to [0, 100]
"""

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

from src.ingestor.ingestor import LogEntry, LogSource

logger = logging.getLogger(__name__)


class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


# Severity thresholds — overridable via environment variables
CRITICAL_THRESHOLD = int(os.getenv("TRIAGE_CRITICAL_THRESHOLD", 85))
HIGH_THRESHOLD = int(os.getenv("TRIAGE_HIGH_THRESHOLD", 60))
MEDIUM_THRESHOLD = int(os.getenv("TRIAGE_MEDIUM_THRESHOLD", 35))


@dataclass
class TriageResult:
    """Output of the triage engine for a single log entry."""
    entry: LogEntry
    severity: Severity
    score: int                        # 0-100
    matched_rules: list[str]          # Human-readable rule names that fired
    escalate_immediately: bool
    context: dict                     # Structured payload for AI summarizer

    def to_dict(self) -> dict:
        return {
            "severity": self.severity.value,
            "score": self.score,
            "matched_rules": self.matched_rules,
            "escalate_immediately": self.escalate_immediately,
            "context": self.context,
            "entry": self.entry.to_dict(),
        }


# ---------------------------------------------------------------------------
# Rule definitions
# ---------------------------------------------------------------------------
# Each rule is a tuple of:
#   (rule_name, score_delta, condition_fn)
# condition_fn receives a LogEntry and returns True if the rule applies.

def _kw(entry: LogEntry, *keywords: str) -> bool:
    """Return True if any keyword appears in the entry message (case-insensitive)."""
    msg = entry.message.lower()
    return any(kw.lower() in msg for kw in keywords)


RULES: list[tuple[str, int, callable]] = [
    # --- Source base scores ---
    ("source:dispenser",        40, lambda e: e.source == LogSource.DISPENSER),
    ("source:plc",              45, lambda e: e.source == LogSource.PLC),
    ("source:api",              25, lambda e: e.source == LogSource.API),
    ("source:sensor",           20, lambda e: e.source == LogSource.SENSOR),

    # --- Dispenser-specific ---
    ("dispenser:jam",           35, lambda e: e.source == LogSource.DISPENSER and _kw(e, "jam", "jammed", "stuck")),
    ("dispenser:empty",         20, lambda e: e.source == LogSource.DISPENSER and _kw(e, "empty", "out of stock", "depleted")),
    ("dispenser:carousel_fail", 30, lambda e: e.source == LogSource.DISPENSER and _kw(e, "carousel")),
    ("dispenser:canister",      15, lambda e: e.source == LogSource.DISPENSER and _kw(e, "canister")),
    ("dispenser:misfill",       40, lambda e: e.source == LogSource.DISPENSER and _kw(e, "misfill", "wrong drug", "wrong slot")),

    # --- PLC-specific ---
    ("plc:motor_overload",      30, lambda e: e.source == LogSource.PLC and _kw(e, "overload", "overcurrent")),
    ("plc:estop",               45, lambda e: e.source == LogSource.PLC and _kw(e, "e-stop", "estop", "emergency stop")),
    ("plc:comm_loss",           35, lambda e: e.source == LogSource.PLC and _kw(e, "communication loss", "comm loss", "no response")),
    ("plc:safe_stop",           25, lambda e: e.source == LogSource.PLC and _kw(e, "safe-stop", "safe stop")),
    ("plc:fault_code_e47",      30, lambda e: e.source == LogSource.PLC and e.event_code in ("E47", "FAULT:E47", "E-47")),

    # --- API-specific ---
    ("api:timeout",             20, lambda e: e.source == LogSource.API and _kw(e, "timeout")),
    ("api:auth_failure",        25, lambda e: e.source == LogSource.API and _kw(e, "auth", "unauthorized", "401", "403")),
    ("api:500_error",           30, lambda e: e.source == LogSource.API and _kw(e, "500", "internal server error")),
    ("api:repeated_failure",    20, lambda e: e.source == LogSource.API and _kw(e, "retry", "repeated", "consecutive")),

    # --- Sensor-specific ---
    ("sensor:temp_exceeded",    30, lambda e: e.source == LogSource.SENSOR and _kw(e, "temperature", "temp") and _kw(e, "exceeded", "high", "threshold")),
    ("sensor:door_open",        15, lambda e: e.source == LogSource.SENSOR and _kw(e, "door open", "door_open")),
    ("sensor:humidity",         20, lambda e: e.source == LogSource.SENSOR and _kw(e, "humidity")),

    # --- Cross-source modifiers ---
    ("modifier:patient_impact",  20, lambda e: _kw(e, "patient", "medication queue", "active queue", "dispense blocked")),
    ("modifier:data_integrity",  25, lambda e: _kw(e, "data integrity", "audit", "21 cfr", "validation")),
    ("modifier:offline",         20, lambda e: _kw(e, "offline", "unreachable", "down", "not responding")),
    ("modifier:overnight",       10, lambda e: e.timestamp.hour < 6 or e.timestamp.hour >= 22),
    ("modifier:repeated_event",  15, lambda e: _kw(e, "repeated", "recurring", "again", "second occurrence")),
]


# ---------------------------------------------------------------------------
# Scoring engine
# ---------------------------------------------------------------------------

def _score_entry(entry: LogEntry) -> tuple[int, list[str]]:
    """
    Apply all rules to a LogEntry and return (total_score, matched_rule_names).
    Score is clamped to [0, 100].
    """
    total = 0
    matched = []

    for rule_name, delta, condition in RULES:
        try:
            if condition(entry):
                total += delta
                matched.append(rule_name)
        except Exception as exc:
            logger.warning("Rule '%s' raised an exception: %s", rule_name, exc)

    return min(max(total, 0), 100), matched


def _score_to_severity(score: int) -> Severity:
    if score >= CRITICAL_THRESHOLD:
        return Severity.CRITICAL
    if score >= HIGH_THRESHOLD:
        return Severity.HIGH
    if score >= MEDIUM_THRESHOLD:
        return Severity.MEDIUM
    return Severity.LOW


def _build_context(entry: LogEntry, score: int, matched: list[str]) -> dict:
    """
    Build the structured context dict passed to the AI summarizer.
    Keeps the payload focused and token-efficient.
    """
    return {
        "system_id": entry.system_id,
        "source": entry.source.value,
        "event_code": entry.event_code,
        "message": entry.message,
        "timestamp": entry.timestamp.isoformat(),
        "severity_score": score,
        "matched_rules": matched,
        "metadata": entry.metadata,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def triage(entry: LogEntry) -> TriageResult:
    """
    Evaluate a single LogEntry and return a TriageResult.

    Args:
        entry: A normalized LogEntry from the ingestor.

    Returns:
        TriageResult with severity, score, matched rules, and AI context.
    """
    score, matched = _score_entry(entry)
    severity = _score_to_severity(score)
    escalate = severity == Severity.CRITICAL

    context = _build_context(entry, score, matched)

    logger.debug(
        "Triaged %s | score=%d | severity=%s | rules=%s",
        entry.system_id, score, severity.value, matched
    )

    return TriageResult(
        entry=entry,
        severity=severity,
        score=score,
        matched_rules=matched,
        escalate_immediately=escalate,
        context=context,
    )


def triage_batch(entries: list[LogEntry]) -> list[TriageResult]:
    """
    Triage a list of LogEntry objects.
    Returns results sorted by severity score descending (highest priority first).

    Args:
        entries: List of normalized LogEntry objects.

    Returns:
        List of TriageResult objects, highest score first.
    """
    results = [triage(e) for e in entries]
    return sorted(results, key=lambda r: r.score, reverse=True)
