"""
summarizer.py
-------------
AI-powered incident summarization using the Anthropic Claude API.

Takes a TriageResult context payload and returns a plain-English incident
summary with a recommended corrective action — formatted for both technical
engineers and non-technical clinical/administrative stakeholders.

Design decisions:
- Prompts are kept deterministic (low temperature) for consistency
- JSON schema is enforced on the response for reliable downstream parsing
- Failures fall back to a structured error summary rather than raising,
  so a single API failure never halts the dashboard
"""

import json
import logging
import os
from dataclasses import dataclass
from typing import Optional

import anthropic

from src.triage.triage import TriageResult, Severity

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------

@dataclass
class IncidentSummary:
    """Structured AI-generated summary for a single incident."""
    incident_id: str
    severity: str
    score: int
    system_id: str
    short_title: str           # One-line title for dashboard card
    summary: str               # 2-3 sentence plain-English explanation
    recommended_action: str    # Step-by-step corrective action
    estimated_impact: str      # Who/what is affected if unresolved
    escalate_immediately: bool
    ai_generated: bool = True
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "incident_id": self.incident_id,
            "severity": self.severity,
            "score": self.score,
            "system_id": self.system_id,
            "short_title": self.short_title,
            "summary": self.summary,
            "recommended_action": self.recommended_action,
            "estimated_impact": self.estimated_impact,
            "escalate_immediately": self.escalate_immediately,
            "ai_generated": self.ai_generated,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are an expert pharmacy automation systems engineer with deep knowledge of:
- Robotic pharmacy dispensing systems (carousels, conveyors, dispensing mechanisms)
- PLC control systems (Allen-Bradley, Siemens fault codes and behaviors)
- Healthcare API integrations and timeout/failure patterns
- Environmental monitoring in regulated pharmacy environments
- 21 CFR Part 11 compliance and HIPAA-regulated operations

Your job is to analyze incident data from an automated pharmacy system and produce a structured
JSON summary that will be shown to both technical engineers and non-technical clinical staff.

Respond ONLY with a valid JSON object. No preamble, no markdown, no explanation outside the JSON.

JSON schema to return:
{
  "short_title": "One-line incident title under 12 words",
  "summary": "2-3 sentence plain-English explanation of what happened and why it matters",
  "recommended_action": "Clear step-by-step corrective action. Number each step.",
  "estimated_impact": "Who or what is affected if this is not resolved promptly"
}"""


def _build_user_prompt(context: dict) -> str:
    return f"""Analyze the following pharmacy automation incident and return a JSON summary.

System: {context.get('system_id', 'Unknown')}
Source: {context.get('source', 'Unknown')}
Event Code: {context.get('event_code', 'None')}
Severity Score: {context.get('severity_score', 0)}/100
Timestamp: {context.get('timestamp', 'Unknown')}

Raw Log Message:
{context.get('message', '')}

Matched Triage Rules:
{', '.join(context.get('matched_rules', [])) or 'None'}

Additional Metadata:
{json.dumps(context.get('metadata', {}), indent=2)}

Return ONLY the JSON object. Be specific to the system type and event code where relevant."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def summarize(result: TriageResult, incident_id: str) -> IncidentSummary:
    """
    Call the Claude API to generate a structured incident summary.

    Args:
        result:      TriageResult from the triage engine.
        incident_id: Unique identifier for this incident record.

    Returns:
        IncidentSummary dataclass. On API failure, returns a fallback
        summary with ai_generated=False and the error message populated.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.error("ANTHROPIC_API_KEY not set — skipping AI summarization")
        return _fallback_summary(result, incident_id, "ANTHROPIC_API_KEY not configured")

    try:
        client = anthropic.Anthropic(api_key=api_key)

        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=512,
            system=SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": _build_user_prompt(result.context)}
            ],
        )

        raw_text = message.content[0].text.strip()

        # Strip markdown fences if the model wrapped its response
        if raw_text.startswith("```"):
            raw_text = raw_text.split("```")[1]
            if raw_text.startswith("json"):
                raw_text = raw_text[4:]
            raw_text = raw_text.strip()

        parsed = json.loads(raw_text)

        return IncidentSummary(
            incident_id=incident_id,
            severity=result.severity.value,
            score=result.score,
            system_id=result.entry.system_id,
            short_title=parsed.get("short_title", "Unknown incident"),
            summary=parsed.get("summary", ""),
            recommended_action=parsed.get("recommended_action", ""),
            estimated_impact=parsed.get("estimated_impact", ""),
            escalate_immediately=result.escalate_immediately,
            ai_generated=True,
        )

    except json.JSONDecodeError as exc:
        logger.error("Failed to parse AI response as JSON: %s", exc)
        return _fallback_summary(result, incident_id, f"JSON parse error: {exc}")

    except anthropic.APIError as exc:
        logger.error("Anthropic API error: %s", exc)
        return _fallback_summary(result, incident_id, f"API error: {exc}")

    except Exception as exc:
        logger.exception("Unexpected error during summarization")
        return _fallback_summary(result, incident_id, str(exc))


def _fallback_summary(result: TriageResult, incident_id: str, error: str) -> IncidentSummary:
    """
    Generate a rule-based fallback summary when the AI call fails.
    Ensures the dashboard always has something displayable.
    """
    entry = result.entry
    return IncidentSummary(
        incident_id=incident_id,
        severity=result.severity.value,
        score=result.score,
        system_id=entry.system_id,
        short_title=f"{entry.source.value.upper()} event on {entry.system_id}",
        summary=(
            f"A {result.severity.value} severity event was detected on {entry.system_id}. "
            f"Event code: {entry.event_code or 'N/A'}. "
            f"Message: {entry.message[:200]}"
        ),
        recommended_action="Review system logs and escalate to on-call engineer if severity is CRITICAL or HIGH.",
        estimated_impact="Impact unknown — AI summarization unavailable. Manual review required.",
        escalate_immediately=result.escalate_immediately,
        ai_generated=False,
        error=error,
    )


def summarize_batch(
    results: list[TriageResult],
    id_prefix: str = "INC",
) -> list[IncidentSummary]:
    """
    Summarize a list of TriageResults.
    Processes highest-severity incidents first.
    Incident IDs are assigned sequentially: INC-0001, INC-0002, etc.

    Args:
        results:   List of TriageResult objects (typically pre-sorted by score).
        id_prefix: Prefix for generated incident IDs.

    Returns:
        List of IncidentSummary objects in the same order as input results.
    """
    summaries = []
    for i, result in enumerate(results, start=1):
        incident_id = f"{id_prefix}-{i:04d}"
        summary = summarize(result, incident_id)
        summaries.append(summary)
        logger.info(
            "Summarized %s | %s | score=%d",
            incident_id, summary.severity, summary.score
        )
    return summaries
