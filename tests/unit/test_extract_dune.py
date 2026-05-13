"""Unit tests for `settle.extract.dune` — parameter formatting + SQL file presence.

The 4 SQL files were validated end-to-end against live Dune via MCP on 2026-04-27
(see PRD §13). This module ensures the param encoder still produces the shape Dune
expects and that the SQL files exist where the code expects them.

NOTE: Dune's /execute endpoint uses a plain dict ``{param_name: value}`` format
where values are JSON primitives (not the old ``{"type": ..., "value": ...}`` format).
``_format_param`` returns the raw JSON-native value for each Python type.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from settle.extract.dune import _format_param


# ----------------------------------------------------------------------------
# _format_param — encoder for Dune `query_parameters` values (JSON primitives)
# ----------------------------------------------------------------------------

def test_format_param_int():
    assert _format_param(24971074) == 24971074


def test_format_param_float():
    assert _format_param(1.5) == 1.5


def test_format_param_bool_does_not_route_to_number():
    """`bool` is a subclass of `int` — must be matched first."""
    assert _format_param(True) is True
    assert _format_param(False) is False


def test_format_param_bytes_renders_as_0x_text():
    """Validated via MCP: passing varbinary as hex string with `0x` prefix works."""
    out = _format_param(bytes.fromhex("414c4c4f4341544f522d4f4245582d41" + "00" * 16))
    assert isinstance(out, str)
    assert out.startswith("0x414c4c4f4341544f522d4f4245582d41")


def test_format_param_bytearray():
    assert _format_param(bytearray(b"\x01\x02\x03")) == "0x010203"


def test_format_param_date_is_iso_string():
    """`date` → ISO-8601 string so SQL templates can wrap as ``DATE '{{x}}'``."""
    assert _format_param(date(2026, 4, 27)) == "2026-04-27"


def test_format_param_datetime_is_iso_string():
    ts = datetime(2026, 4, 27, 22, 22, 23, tzinfo=timezone.utc)
    out = _format_param(ts)
    assert isinstance(out, str)
    assert out.startswith("2026-04-27T22:22:23")


def test_format_param_string_passthrough():
    assert _format_param("ethereum") == "ethereum"


# ----------------------------------------------------------------------------
# SQL files
# ----------------------------------------------------------------------------

EXPECTED_SQL_FILES = [
    "blocks_at_eod.sql",
    "debt_timeseries.sql",
    "inflow_by_counterparty.sql",
    "ssr_history.sql",
    "transfer_timeseries.sql",
    "v3_liquidity_events.sql",
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


# ----------------------------------------------------------------------------
# _infer_parameters — name-based numeric inference (replaces the old context-
# based heuristic that mis-typed addresses sitting next to `=` as numbers).
#
# The promise: parameter names matching ``_NUMERIC_NAME`` → ``type="number"``,
# everything else → ``type="text"``. These tests pin both directions against
# the param names actually used in this project's SQL.
# ----------------------------------------------------------------------------

from settle.extract.dune import _infer_parameters


def _typed(sql: str) -> dict[str, str]:
    """Convenience: collapse the param-defs list into ``{name: type}``."""
    return {p["key"]: p["type"] for p in _infer_parameters(sql)}


def test_infer_parameters_pin_block_is_number():
    """``{{pin_block}}`` is used in every SQL file as a numeric block cap."""
    assert _typed("SELECT * FROM x WHERE block_number <= {{pin_block}}") == {
        "pin_block": "number",
    }


def test_infer_parameters_named_block_variants_are_numbers():
    """Block-bound params with prefix / suffix variants — all should type as
    numbers. Covers ``from_block`` / ``to_block`` / ``block_height`` /
    ``last_block`` shapes that appear across the SQL set."""
    types = _typed(
        "WHERE blk >= {{from_block}} AND blk <= {{to_block}} "
        "AND height = {{block_height}} AND tip = {{last_block}}"
    )
    assert types == {
        "from_block":   "number",
        "to_block":     "number",
        "block_height": "number",
        "last_block":   "number",
    }


def test_infer_parameters_amount_params_are_numbers():
    """Amount / threshold / count / limit / min_ / max_ all type as numbers."""
    sql = (
        "WHERE amount >= {{min_transfer_amount}} "
        "AND total_amount <= {{max_total_amount}} "
        "AND n <= {{max_count}} AND x > {{threshold}} "
        "LIMIT {{limit}}"
    )
    types = _typed(sql)
    for name in (
        "min_transfer_amount", "max_total_amount",
        "max_count", "threshold", "limit",
    ):
        assert types[name] == "number", f"{name} should be number, got {types[name]}"


def test_infer_parameters_addresses_and_chains_are_text():
    """Crucial: addresses (varbinary on Dune), chain names, and the
    ``ilk`` bytes32 parameter must type as text. Previously the
    context-heuristic mis-typed these as numbers when they sat next to
    ``=``, which Dune then rejected at runtime."""
    sql = (
        "WHERE blockchain = '{{chain}}' "
        "AND contract_address = {{token}} AND \"to\" = {{holder}} "
        "AND nfpm = {{nfpm}} AND from_addr = {{from_addr}} "
        "AND ilk = {{ilk_bytes32}}"
    )
    types = _typed(sql)
    for name in ("chain", "token", "holder", "nfpm", "from_addr", "ilk_bytes32"):
        assert types[name] == "text", f"{name} should be text, got {types[name]}"


def test_infer_parameters_dates_and_timestamps_are_text():
    """``{{start_date}}`` and ``{{ts}}`` are wrapped in ``DATE '...'`` /
    ``TIMESTAMP '...'`` in the SQL so they must type as text strings."""
    sql = "WHERE block_date >= DATE '{{start_date}}' AND time <= TIMESTAMP '{{ts}}'"
    assert _typed(sql) == {"start_date": "text", "ts": "text"}


def test_infer_parameters_returns_zero_value_for_numbers():
    """The default ``value`` is ``"0"`` for numeric params and ``""`` for
    text — Dune requires *some* value on each param def even when the
    caller will override it on every execute."""
    params = _infer_parameters(
        "WHERE block_number <= {{pin_block}} AND token = {{token}}"
    )
    by_key = {p["key"]: p for p in params}
    assert by_key["pin_block"]["value"] == "0"
    assert by_key["token"]["value"] == ""


def test_infer_parameters_deduplicates_repeated_placeholders():
    """A placeholder used twice in the SQL produces one param def, not two —
    Dune's create-query endpoint rejects duplicates."""
    sql = "WHERE x = {{token}} OR y = {{token}}"
    keys = [p["key"] for p in _infer_parameters(sql)]
    assert keys == ["token"]


def test_infer_parameters_preserves_order_of_first_appearance():
    """Stable ordering eases visual diffing when SQL is re-published."""
    sql = "WHERE x = {{chain}} AND y <= {{pin_block}} AND z = {{token}}"
    keys = [p["key"] for p in _infer_parameters(sql)]
    assert keys == ["chain", "pin_block", "token"]
