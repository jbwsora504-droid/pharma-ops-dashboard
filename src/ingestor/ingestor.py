"""
ingestor.py
-----------
Parses and normalizes raw log entries from pharmacy automation equipment.

Handles multiple log formats:
- Dispenser unit logs (carousel, slot, canister events)
- PLC fault logs (Allen-Bradley fault codes)
- API integration logs (timeouts, auth failures, payload errors)
- Sensor/environmental alerts (temperature, humidity, door events)

Each parsed entry is returned as a normalized LogEntry dataclass
ready for the triage engine.
"""

import re
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class LogSource(str, Enum):
    DISPENSER = "dispenser"
    PLC = "plc"
    API = "api"
    SENSOR = "sensor"
    UNKNOWN = "unknown"


class LogFormat(str, Enum):
    JSON = "json"
    PLAIN = "plain"
    CSV = "csv"


@dataclass
class LogEntry:
    """Normalized representation of a single log event."""
    raw: str
    source: LogSource
    timestamp: datetime
    system_id: str
    event_code: Optional[str]
    message: str
    metadata: dict = field(default_factory=dict)
    parse_format: LogFormat = LogFormat.PLAIN

    def to_dict(self) -> dict:
        return {
            "raw": self.raw,
            "source": self.source.value,
            "timestamp": self.timestamp.isoformat(),
            "system_id": self.system_id,
            "event_code": self.event_code,
            "message": self.message,
            "metadata": self.metadata,
            "parse_format": self.parse_format.value,
        }


# ---------------------------------------------------------------------------
# Format detectors
# ---------------------------------------------------------------------------

def _detect_format(line: str) -> LogFormat:
    """Identify whether a log line is JSON, CSV, or plain text."""
    stripped = line.strip()
    if stripped.startswith("{"):
        try:
            json.loads(stripped)
            return LogFormat.JSON
        except json.JSONDecodeError:
            pass
    if stripped.count(",") >= 3 and not stripped.startswith("["):
        return LogFormat.CSV
    return LogFormat.PLAIN


# ---------------------------------------------------------------------------
# Source-specific parsers
# ---------------------------------------------------------------------------

def _parse_json_entry(line: str) -> LogEntry:
    """Parse a structured JSON log entry."""
    data = json.loads(line.strip())

    source_raw = data.get("source", "unknown").lower()
    source = LogSource(source_raw) if source_raw in LogSource._value2member_map_ else LogSource.UNKNOWN

    try:
        ts = datetime.fromisoformat(data.get("timestamp", datetime.now().isoformat()))
    except ValueError:
        ts = datetime.now()

    return LogEntry(
        raw=line.strip(),
        source=source,
        timestamp=ts,
        system_id=data.get("system_id", "UNKNOWN"),
        event_code=data.get("event_code"),
        message=data.get("message", ""),
        metadata={k: v for k, v in data.items()
                  if k not in ("source", "timestamp", "system_id", "event_code", "message")},
        parse_format=LogFormat.JSON,
    )


# Patterns for plain-text log lines
_PLAIN_PATTERNS = [
    # Dispenser: [2024-01-15 08:32:11] DISPENSER-04 E-JAM12 Carousel jam detected on slot 12
    (
        re.compile(
            r"\[(?P<ts>[^\]]+)\]\s+(?P<sys>DISPENSER-\d+)\s+(?P<code>[A-Z0-9\-]+)\s+(?P<msg>.+)",
            re.IGNORECASE,
        ),
        LogSource.DISPENSER,
    ),
    # PLC: [2024-01-15 09:14:05] PLC-UNIT2 FAULT:E47 Motor overload condition detected
    (
        re.compile(
            r"\[(?P<ts>[^\]]+)\]\s+(?P<sys>PLC-\w+)\s+FAULT:(?P<code>\w+)\s+(?P<msg>.+)",
            re.IGNORECASE,
        ),
        LogSource.PLC,
    ),
    # API: [2024-01-15 10:02:44] API-GATEWAY TIMEOUT REQ:pharmacy-svc endpoint /dispense/confirm
    (
        re.compile(
            r"\[(?P<ts>[^\]]+)\]\s+(?P<sys>API-\w+)\s+(?P<code>TIMEOUT|AUTH_FAIL|PAYLOAD_ERR|500|503)\s+(?P<msg>.+)",
            re.IGNORECASE,
        ),
        LogSource.API,
    ),
    # Sensor: [2024-01-15 11:45:00] SENSOR-TEMP3 ALERT Temperature threshold exceeded: 78F
    (
        re.compile(
            r"\[(?P<ts>[^\]]+)\]\s+(?P<sys>SENSOR-\w+)\s+(?P<code>ALERT|WARN|DOOR_OPEN)\s+(?P<msg>.+)",
            re.IGNORECASE,
        ),
        LogSource.SENSOR,
    ),
]


def _parse_plain_entry(line: str) -> LogEntry:
    """Parse a plain-text log line using pattern matching."""
    for pattern, source in _PLAIN_PATTERNS:
        match = pattern.match(line.strip())
        if match:
            try:
                ts = datetime.strptime(match.group("ts"), "%Y-%m-%d %H:%M:%S")
            except ValueError:
                ts = datetime.now()
            return LogEntry(
                raw=line.strip(),
                source=source,
                timestamp=ts,
                system_id=match.group("sys"),
                event_code=match.group("code"),
                message=match.group("msg").strip(),
                parse_format=LogFormat.PLAIN,
            )

    # Fallback: unrecognized format, preserve raw for triage
    logger.warning("Could not match pattern for log line: %s", line[:80])
    return LogEntry(
        raw=line.strip(),
        source=LogSource.UNKNOWN,
        timestamp=datetime.now(),
        system_id="UNKNOWN",
        event_code=None,
        message=line.strip(),
        parse_format=LogFormat.PLAIN,
    )


def _parse_csv_entry(line: str) -> LogEntry:
    """Parse a CSV-formatted log line: timestamp,source,system_id,code,message"""
    parts = [p.strip() for p in line.strip().split(",", 4)]
    if len(parts) < 5:
        return _parse_plain_entry(line)

    ts_raw, source_raw, system_id, code, message = parts
    try:
        ts = datetime.fromisoformat(ts_raw)
    except ValueError:
        ts = datetime.now()

    source_val = source_raw.lower()
    source = LogSource(source_val) if source_val in LogSource._value2member_map_ else LogSource.UNKNOWN

    return LogEntry(
        raw=line.strip(),
        source=source,
        timestamp=ts,
        system_id=system_id,
        event_code=code if code else None,
        message=message,
        parse_format=LogFormat.CSV,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_line(line: str) -> Optional[LogEntry]:
    """
    Parse a single log line into a normalized LogEntry.
    Returns None if the line is empty or a comment.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None

    fmt = _detect_format(stripped)
    if fmt == LogFormat.JSON:
        return _parse_json_entry(stripped)
    if fmt == LogFormat.CSV:
        return _parse_csv_entry(stripped)
    return _parse_plain_entry(stripped)


def ingest_file(path: str | Path) -> list[LogEntry]:
    """
    Read a log file and return all successfully parsed entries.

    Args:
        path: Path to a .log, .txt, .json, or .csv log file.

    Returns:
        List of LogEntry objects sorted by timestamp ascending.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Log file not found: {path}")

    entries = []
    errors = 0

    with open(path, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            try:
                entry = parse_line(line)
                if entry:
                    entries.append(entry)
            except Exception as exc:
                logger.error("Parse error on line %d: %s — %s", lineno, line[:60], exc)
                errors += 1

    logger.info(
        "Ingested %d entries from %s (%d parse errors)",
        len(entries), path.name, errors
    )

    return sorted(entries, key=lambda e: e.timestamp)


def ingest_directory(directory: str | Path) -> list[LogEntry]:
    """
    Ingest all log files from a directory.
    Supports .log, .txt, .json, .csv extensions.

    Args:
        directory: Path to directory containing log files.

    Returns:
        Combined, timestamp-sorted list of LogEntry objects.
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise NotADirectoryError(f"Not a directory: {directory}")

    supported = {".log", ".txt", ".json", ".csv"}
    files = [f for f in directory.iterdir() if f.suffix.lower() in supported]

    if not files:
        logger.warning("No supported log files found in %s", directory)
        return []

    all_entries = []
    for f in files:
        all_entries.extend(ingest_file(f))

    return sorted(all_entries, key=lambda e: e.timestamp)
