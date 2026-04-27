"""Unit tests for `settle.extract.dune` — parameter formatting + SQL file presence.

The 4 SQL files were validated end-to-end against live Dune via MCP on 2026-04-27
(see PRD §13). This module ensures the param encoder still produces the shape Dune
expects and that the SQL files exist where the code expects them.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from settle.extract.dune import _format_param


# ----------------------------------------------------------------------------
# _format_param — encoder for Dune `query_parameters` items
# ----------------------------------------------------------------------------

def test_format_param_int():
    assert _format_param(24971074) == {"type": "number", "value": "24971074"}


def test_format_param_float():
    assert _format_param(1.5) == {"type": "number", "value": "1.5"}


def test_format_param_bool_does_not_route_to_number():
    """`bool` is a subclass of `int` — must be matched first."""
    assert _format_param(True) == {"type": "text", "value": "true"}
    assert _format_param(False) == {"type": "text", "value": "false"}


def test_format_param_bytes_renders_as_0x_text():
    """Validated via MCP: passing varbinary as `text` with `0x...` works."""
    out = _format_param(bytes.fromhex("414c4c4f4341544f522d4f4245582d41" + "00" * 16))
    assert out["type"] == "text"
    assert out["value"].startswith("0x414c4c4f4341544f522d4f4245582d41")


def test_format_param_bytearray():
    out = _format_param(bytearray(b"\x01\x02\x03"))
    assert out == {"type": "text", "value": "0x010203"}


def test_format_param_date_is_text_not_datetime():
    """`date` → text so SQL templates can wrap as `DATE '{{x}}'`."""
    assert _format_param(date(2026, 4, 27)) == {
        "type": "text",
        "value": "2026-04-27",
    }


def test_format_param_datetime_is_datetime():
    ts = datetime(2026, 4, 27, 22, 22, 23, tzinfo=timezone.utc)
    out = _format_param(ts)
    assert out["type"] == "datetime"
    assert out["value"].startswith("2026-04-27T22:22:23")


def test_format_param_string_passthrough():
    assert _format_param("ethereum") == {"type": "text", "value": "ethereum"}


# ----------------------------------------------------------------------------
# SQL files
# ----------------------------------------------------------------------------

EXPECTED_SQL_FILES = [
    "debt_timeseries.sql",
    "transfer_timeseries.sql",
    "ssr_history.sql",
    "venue_inflow.sql",
]


def test_all_sql_files_present(queries_dir: Path):
    for name in EXPECTED_SQL_FILES:
        assert (queries_dir / name).exists(), f"missing query file: {name}"


def test_all_sql_files_use_pin_block(queries_dir: Path):
    """Every shared query MUST gate on ``{{pin_block}}`` for reproducibility."""
    for name in EXPECTED_SQL_FILES:
        text = (queries_dir / name).read_text()
        assert "{{pin_block}}" in text, f"{name} is missing the pin_block parameter"


def test_debt_timeseries_uses_ilk_param(queries_dir: Path):
    text = (queries_dir / "debt_timeseries.sql").read_text()
    assert "{{ilk_bytes32}}" in text
    assert "{{start_date}}" in text


def test_transfer_timeseries_uses_token_holder_chain(queries_dir: Path):
    text = (queries_dir / "transfer_timeseries.sql").read_text()
    for needed in ("{{chain}}", "{{token}}", "{{holder}}", "{{start_date}}"):
        assert needed in text, f"{needed} missing from transfer_timeseries.sql"


def test_venue_inflow_uses_directed_addrs(queries_dir: Path):
    text = (queries_dir / "venue_inflow.sql").read_text()
    for needed in ("{{from_addr}}", "{{to_addr}}", "{{token}}", "{{chain}}"):
        assert needed in text, f"{needed} missing from venue_inflow.sql"
