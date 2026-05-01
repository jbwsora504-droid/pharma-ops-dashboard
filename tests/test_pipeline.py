"""
test_ingestor.py
----------------
Unit tests for the log ingestor module.
"""

import pytest
from datetime import datetime
from src.ingestor.ingestor import parse_line, LogSource, LogFormat


def test_parse_plain_dispenser_jam():
    line = "[2024-01-15 08:32:11] DISPENSER-04 E-JAM12 Carousel jam detected on slot 12"
    entry = parse_line(line)
    assert entry is not None
    assert entry.source == LogSource.DISPENSER
    assert entry.system_id == "DISPENSER-04"
    assert entry.event_code == "E-JAM12"
    assert "jam" in entry.message.lower()
    assert entry.parse_format == LogFormat.PLAIN


def test_parse_plain_plc_fault():
    line = "[2024-01-15 09:14:05] PLC-UNIT2 FAULT:E47 Motor overload condition detected"
    entry = parse_line(line)
    assert entry is not None
    assert entry.source == LogSource.PLC
    assert entry.system_id == "PLC-UNIT2"
    assert entry.event_code == "E47"
    assert "overload" in entry.message.lower()


def test_parse_plain_api_timeout():
    line = "[2024-01-15 10:02:44] API-GATEWAY TIMEOUT REQ:pharmacy-svc endpoint /dispense/confirm"
    entry = parse_line(line)
    assert entry is not None
    assert entry.source == LogSource.API
    assert entry.system_id == "API-GATEWAY"
    assert entry.event_code == "TIMEOUT"


def test_parse_plain_sensor_alert():
    line = "[2024-01-15 09:45:00] SENSOR-TEMP3 ALERT Temperature threshold exceeded: 78F"
    entry = parse_line(line)
    assert entry is not None
    assert entry.source == LogSource.SENSOR
    assert entry.system_id == "SENSOR-TEMP3"
    assert entry.event_code == "ALERT"


def test_parse_json_entry():
    line = '{"source": "dispenser", "timestamp": "2024-01-16T06:12:00", "system_id": "DISPENSER-05", "event_code": "E-CAROUSEL", "message": "Carousel rotation failure", "unit": 5}'
    entry = parse_line(line)
    assert entry is not None
    assert entry.source == LogSource.DISPENSER
    assert entry.system_id == "DISPENSER-05"
    assert entry.event_code == "E-CAROUSEL"
    assert entry.parse_format == LogFormat.JSON
    assert entry.metadata.get("unit") == 5


def test_parse_csv_entry():
    line = "2024-01-15T08:00:00,plc,PLC-UNIT1,E22,Communication loss with conveyor controller"
    entry = parse_line(line)
    assert entry is not None
    assert entry.source == LogSource.PLC
    assert entry.system_id == "PLC-UNIT1"
    assert entry.event_code == "E22"
    assert entry.parse_format == LogFormat.CSV


def test_empty_line_returns_none():
    assert parse_line("") is None
    assert parse_line("   ") is None


def test_comment_line_returns_none():
    assert parse_line("# This is a comment") is None


def test_to_dict_serializable():
    line = "[2024-01-15 08:32:11] DISPENSER-04 E-JAM12 Carousel jam detected on slot 12"
    entry = parse_line(line)
    d = entry.to_dict()
    assert isinstance(d, dict)
    assert d["source"] == "dispenser"
    assert d["system_id"] == "DISPENSER-04"


# ---------------------------------------------------------------------------
# Triage tests
# ---------------------------------------------------------------------------

from src.triage.triage import triage, triage_batch, Severity


def _make_entry(source_str, system_id, event_code, message):
    """Helper to build a minimal LogEntry for triage tests."""
    from src.ingestor.ingestor import LogEntry, LogSource, LogFormat
    return LogEntry(
        raw=message,
        source=LogSource(source_str),
        timestamp=datetime(2024, 1, 15, 10, 0, 0),
        system_id=system_id,
        event_code=event_code,
        message=message,
        parse_format=LogFormat.PLAIN,
    )


def test_dispenser_jam_is_critical():
    entry = _make_entry("dispenser", "DISPENSER-04", "E-JAM12",
                        "Carousel jam detected on slot 12 — patient medication queue blocked")
    result = triage(entry)
    assert result.severity == Severity.CRITICAL
    assert result.score >= 85
    assert result.escalate_immediately is True


def test_plc_estop_is_critical():
    entry = _make_entry("plc", "PLC-UNIT2", "E-ESTOP",
                        "Emergency stop triggered on unit — manual reset required")
    result = triage(entry)
    assert result.severity == Severity.CRITICAL


def test_api_timeout_is_medium_or_higher():
    entry = _make_entry("api", "API-GATEWAY", "TIMEOUT",
                        "Timeout on endpoint /dispense/confirm")
    result = triage(entry)
    assert result.severity in (Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL)


def test_sensor_door_open_is_low():
    entry = _make_entry("sensor", "SENSOR-DOOR1", "DOOR_OPEN",
                        "Dispenser bay access door opened")
    result = triage(entry)
    assert result.severity in (Severity.LOW, Severity.MEDIUM)


def test_triage_batch_sorted_by_score():
    entries = [
        _make_entry("sensor", "SENSOR-DOOR1", "DOOR_OPEN", "Door opened"),
        _make_entry("dispenser", "DISPENSER-04", "E-JAM12", "Carousel jam — patient queue blocked"),
        _make_entry("api", "API-GATEWAY", "TIMEOUT", "Timeout on /dispense/confirm"),
    ]
    results = triage_batch(entries)
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)


def test_matched_rules_populated():
    entry = _make_entry("dispenser", "DISPENSER-04", "E-JAM12", "Carousel jam on slot 12")
    result = triage(entry)
    assert len(result.matched_rules) > 0
    assert "source:dispenser" in result.matched_rules
