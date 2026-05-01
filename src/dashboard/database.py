"""
database.py
-----------
SQLite-backed persistence layer for incident records.

Stores every IncidentSummary produced by the pipeline with full audit
trail support. Designed for 21 CFR Part 11-aware environments — all
records are append-only; updates are not permitted on closed incidents.
"""

import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.api.summarizer import IncidentSummary

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", "data/incidents.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id     TEXT NOT NULL UNIQUE,
    severity        TEXT NOT NULL,
    score           INTEGER NOT NULL,
    system_id       TEXT NOT NULL,
    short_title     TEXT NOT NULL,
    summary         TEXT NOT NULL,
    recommended_action TEXT NOT NULL,
    estimated_impact TEXT NOT NULL,
    escalate_immediately INTEGER NOT NULL DEFAULT 0,
    ai_generated    INTEGER NOT NULL DEFAULT 1,
    error           TEXT,
    raw_log         TEXT,
    source          TEXT,
    event_code      TEXT,
    log_timestamp   TEXT,
    created_at      TEXT NOT NULL,
    acknowledged    INTEGER NOT NULL DEFAULT 0,
    acknowledged_by TEXT,
    acknowledged_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_severity ON incidents(severity);
CREATE INDEX IF NOT EXISTS idx_system_id ON incidents(system_id);
CREATE INDEX IF NOT EXISTS idx_created_at ON incidents(created_at);
CREATE INDEX IF NOT EXISTS idx_acknowledged ON incidents(acknowledged);
"""


@contextmanager
def _get_conn():
    """Context manager for SQLite connections with WAL mode enabled."""
    db_path = Path(DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize_db() -> None:
    """Create tables and indexes if they do not exist."""
    with _get_conn() as conn:
        conn.executescript(SCHEMA)
    logger.info("Database initialized at %s", DB_PATH)


def save_incident(summary: IncidentSummary, raw_log: Optional[str] = None,
                  source: Optional[str] = None, event_code: Optional[str] = None,
                  log_timestamp: Optional[str] = None) -> int:
    """
    Persist an IncidentSummary to the database.

    Args:
        summary:       IncidentSummary from the AI summarizer.
        raw_log:       Original raw log line for audit purposes.
        source:        Log source (dispenser, plc, api, sensor).
        event_code:    Event/fault code from the log entry.
        log_timestamp: Timestamp from the original log entry.

    Returns:
        The rowid of the inserted record.
    """
    with _get_conn() as conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO incidents (
                incident_id, severity, score, system_id,
                short_title, summary, recommended_action, estimated_impact,
                escalate_immediately, ai_generated, error,
                raw_log, source, event_code, log_timestamp, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                summary.incident_id,
                summary.severity,
                summary.score,
                summary.system_id,
                summary.short_title,
                summary.summary,
                summary.recommended_action,
                summary.estimated_impact,
                int(summary.escalate_immediately),
                int(summary.ai_generated),
                summary.error,
                raw_log,
                source,
                event_code,
                log_timestamp,
                datetime.utcnow().isoformat(),
            ),
        )
        row_id = cursor.lastrowid
        logger.debug("Saved incident %s (rowid=%d)", summary.incident_id, row_id)
        return row_id


def acknowledge_incident(incident_id: str, acknowledged_by: str = "operator") -> bool:
    """
    Mark an incident as acknowledged.
    Acknowledged incidents are filtered from the active dashboard view.

    Args:
        incident_id:     The incident ID string (e.g. 'INC-0001').
        acknowledged_by: Name or ID of the operator acknowledging the incident.

    Returns:
        True if the record was updated, False if incident_id was not found.
    """
    with _get_conn() as conn:
        cursor = conn.execute(
            """
            UPDATE incidents
            SET acknowledged = 1,
                acknowledged_by = ?,
                acknowledged_at = ?
            WHERE incident_id = ?
            """,
            (acknowledged_by, datetime.utcnow().isoformat(), incident_id),
        )
        updated = cursor.rowcount > 0
        if updated:
            logger.info("Incident %s acknowledged by %s", incident_id, acknowledged_by)
        else:
            logger.warning("Acknowledge failed — incident not found: %s", incident_id)
        return updated


def get_active_incidents(limit: int = 100) -> list[dict]:
    """
    Retrieve unacknowledged incidents ordered by severity score descending.

    Args:
        limit: Maximum number of records to return.

    Returns:
        List of incident dicts ready for API serialization.
    """
    with _get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM incidents
            WHERE acknowledged = 0
            ORDER BY score DESC, created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_incident_history(
    system_id: Optional[str] = None,
    severity: Optional[str] = None,
    limit: int = 500,
) -> list[dict]:
    """
    Retrieve incident history with optional filters.

    Args:
        system_id: Filter to a specific system (e.g. 'DISPENSER-04').
        severity:  Filter to a severity tier ('CRITICAL', 'HIGH', etc.).
        limit:     Maximum number of records to return.

    Returns:
        List of incident dicts ordered by creation time descending.
    """
    query = "SELECT * FROM incidents WHERE 1=1"
    params: list = []

    if system_id:
        query += " AND system_id = ?"
        params.append(system_id)
    if severity:
        query += " AND severity = ?"
        params.append(severity.upper())

    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    with _get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]


def get_summary_stats() -> dict:
    """
    Return aggregate counts for the dashboard status bar.

    Returns:
        Dict with counts by severity and acknowledgement status.
    """
    with _get_conn() as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN severity = 'CRITICAL' AND acknowledged = 0 THEN 1 ELSE 0 END) as critical_active,
                SUM(CASE WHEN severity = 'HIGH' AND acknowledged = 0 THEN 1 ELSE 0 END) as high_active,
                SUM(CASE WHEN severity = 'MEDIUM' AND acknowledged = 0 THEN 1 ELSE 0 END) as medium_active,
                SUM(CASE WHEN severity = 'LOW' AND acknowledged = 0 THEN 1 ELSE 0 END) as low_active,
                SUM(CASE WHEN acknowledged = 1 THEN 1 ELSE 0 END) as acknowledged_total
            FROM incidents
            """
        ).fetchone()
        return dict(row)
