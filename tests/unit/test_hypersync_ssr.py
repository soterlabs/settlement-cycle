from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from settle.extract.hypersync import LogRow
from settle.normalize.sources.hypersync_ssr import FILE_TOPIC, SSR_KEY, SUSDS, HyperSyncSSRSource


def event(block, index, day, ray):
    return LogRow(block, index, int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp()),
                  SUSDS, FILE_TOPIC, SSR_KEY, None, None, "0x" + format(ray, "064x"))


def test_last_update_by_block_and_log_wins_and_window_is_respected():
    rows = [event(20, 3, "2026-08-02", 10**27),
            event(19, 5, "2026-08-02", 10**27 + 10**18),
            event(20, 2, "2026-08-02", 10**27 + 2 * 10**18),
            event(1, 0, "2026-07-31", 10**27),
            event(30, 0, "2026-08-03", 10**27)]
    src = HyperSyncSSRSource(fetch_logs=lambda *a: rows, resolve_start_block=lambda *a: 1)
    result = src.ssr_history(date(2026, 8, 1), 20)
    assert result.to_dict("records") == [{"effective_date": date(2026, 8, 2), "ssr_apy": Decimal(0)}]


def test_malformed_rate_fails_instead_of_inventing_zero():
    row = replace(event(20, 0, "2026-08-02", 10**27), data="0x")
    src = HyperSyncSSRSource(fetch_logs=lambda *a: [row], resolve_start_block=lambda *a: 1)
    with pytest.raises(ValueError, match="Malformed"):
        src.ssr_history(date(2026, 8, 1), 20)


def test_empty_history_has_protocol_columns():
    src = HyperSyncSSRSource(fetch_logs=lambda *a: [], resolve_start_block=lambda *a: 1)
    assert list(src.ssr_history(date(2026, 8, 1), 20)) == ["effective_date", "ssr_apy"]
