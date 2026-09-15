from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from settle.compute import compute_monthly_pnl
from settle.domain.config import load_prime_by_id
from settle.domain.monthly_pnl import MonthlyPnL
from settle.domain.period import Month, Period
from settle.extract import hypersync
from settle.load import enrich_with_dr, write_revenue_preview, write_settlement
from settle.normalize.sources.hypersync_block_resolver import HyperSyncBlockResolver


@pytest.mark.parametrize("cutoff", [date(2026, 7, 31), date(2026, 9, 1),
                                    "2026-08-15", datetime(2026, 8, 15)])
def test_invalid_cutoffs_fail_before_extracting(cutoff):
    with pytest.raises(ValueError, match="within the selected"):
        compute_monthly_pnl(load_prime_by_id("obex"), Month(2026, 8), as_of=cutoff)


@pytest.mark.parametrize("offset", [0, 1])
def test_today_and_future_are_not_completed_days(offset):
    day = datetime.now(UTC).date() + timedelta(days=offset)
    with pytest.raises(ValueError, match="completed UTC day"):
        compute_monthly_pnl(load_prime_by_id("obex"), Month(day.year, day.month), as_of=day)


def test_leap_day_and_month_opening():
    assert Period.from_month(Month(2024, 2), as_of=date(2024, 2, 29)).n_days == 29
    period = Period.from_month(Month(2024, 2), as_of=date(2024, 2, 1))
    assert period.start == period.end == date(2024, 2, 1)


@pytest.mark.parametrize("head,successor,accepted", [(1000, 1001, True),
                                                   (599, 1001, False),
                                                   (600, 1001, False),
                                                   (601, 1001, True),
                                                   (1000, 999, False)])
def test_as_of_pins_require_exact_boundary_and_finality(monkeypatch, head, successor, accepted):
    monkeypatch.setenv("HYPERSYNC_REORG_MARGIN", "500")
    monkeypatch.setattr(hypersync, "archive_height", lambda chain: head)
    resolver = HyperSyncBlockResolver(validation_ts_fn=lambda chain, block: 999 if block == 100 else successor)
    anchor = datetime.fromtimestamp(1000, UTC)
    if accepted:
        resolver.validate_finalized_boundary("ethereum", 100, anchor)
    else:
        with pytest.raises(hypersync.HyperSyncError):
            resolver.validate_finalized_boundary("ethereum", 100, anchor)


def test_lagging_archive_cannot_certify_successor(monkeypatch):
    monkeypatch.setenv("HYPERSYNC_REORG_MARGIN", "500")
    monkeypatch.setattr(hypersync, "archive_height", lambda chain: 1000)
    def timestamp(chain, block):
        if block > 100:
            raise hypersync.HyperSyncBlockUnavailable("Archive has not indexed the successor")
        return 900

    with pytest.raises(hypersync.HyperSyncBlockUnavailable):
        HyperSyncBlockResolver(validation_ts_fn=timestamp).validate_finalized_boundary(
            "ethereum", 100, datetime.fromtimestamp(1000, UTC),
        )


@pytest.mark.parametrize("cache_layer", ["local", "postgres"])
def test_finality_retry_rechecks_cached_boundary_after_reorg(tmp_path, monkeypatch, cache_layer):
    from settle.extract import postgres_store

    monkeypatch.setenv("SETTLE_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("SETTLE_NO_CACHE", "0")
    monkeypatch.setenv("HYPERSYNC_REORG_MARGIN", "500")
    monkeypatch.setattr(hypersync, "_token", lambda: "test")
    stored = {}
    monkeypatch.setattr(postgres_store, "get", lambda source, key: stored.get((source, key), postgres_store.MISS))
    monkeypatch.setattr(postgres_store, "put", lambda source, key, **kw: stored.update({(source, key): kw["payload"]}))
    state = {"head": 600, "timestamps": {100: 999, 101: 1001}, "reads": []}

    def execute(chain, body, headers, post):
        block = body["from_block"]
        state["reads"].append(block)
        return {"data": [{"blocks": [{"number": block, "timestamp": state["timestamps"][block]}]}]}

    monkeypatch.setattr(hypersync, "_execute", execute)
    monkeypatch.setattr(hypersync, "archive_height", lambda chain: state["head"])
    # The normal resolution path can have cached these before certification.
    assert hypersync.block_timestamp("ethereum", 100) == 999
    assert hypersync.block_timestamp("ethereum", 101) == 1001
    anchor = datetime.fromtimestamp(1000, UTC)
    with pytest.raises(hypersync.HyperSyncError, match="not finalized"):
        HyperSyncBlockResolver().validate_finalized_boundary("ethereum", 100, anchor)

    # A retry after a reorg must reject the old boundary even though it is
    # now deep enough. Simulate a worker restart reading either cache layer.
    state.update(head=1000, timestamps={100: 999, 101: 1000, 102: 1002}, reads=[])
    if cache_layer == "postgres":
        for path in tmp_path.glob("*.pkl"):
            path.unlink()
    assert hypersync.block_timestamp("ethereum", 101) == 1001
    assert state["reads"] == []  # stale cache is present in both scenarios
    resolver = HyperSyncBlockResolver()
    with pytest.raises(hypersync.HyperSyncError, match="not the requested UTC boundary"):
        resolver.validate_finalized_boundary("ethereum", 100, anchor)
    assert state["reads"] == [100, 101]
    resolver.validate_finalized_boundary("ethereum", 101, anchor)
    assert state["reads"] == [100, 101, 101, 102]


def test_partial_active_gar_is_not_read_from_full_month_artifact():
    from settle.compute.gar import validate_gar_cutoff

    prime = load_prime_by_id("skybase")
    assert prime.gar is not None
    prime = replace(prime, gar=replace(prime.gar, from_month="2026-01", until_month=None))
    with pytest.raises(ValueError, match="partial-month GAR"):
        compute_monthly_pnl(prime, Month(2026, 8), as_of=date(2026, 8, 15))
    validate_gar_cutoff(prime, Month(2026, 8), date(2026, 8, 31))
    retired = replace(prime, gar=replace(prime.gar, until_month="2026-08"))
    validate_gar_cutoff(retired, Month(2026, 8), date(2026, 8, 15))


def test_preview_output_cannot_overwrite_settlement_or_import_monthly_dr(tmp_path, monkeypatch):
    import json

    from settle.load import dr_rewards, writer

    pnl = MonthlyPnL(
        prime_id="obex", month=Month(2026, 8),
        period=Period.from_month(Month(2026, 8), as_of=date(2026, 8, 15)),
        sky_revenue=Decimal(0), agent_rate=Decimal(0), prime_agent_revenue=Decimal(0),
        monthly_pnl=Decimal(0), venue_breakdown=[], pin_blocks_som={},
    )
    def forbidden(*args, **kwargs):
        raise AssertionError("Monthly enrichment or XLSX must not run for a preview")

    monkeypatch.setattr(dr_rewards, "load_dr", forbidden)
    monkeypatch.setattr(writer, "_build_canonical_xlsx", forbidden)
    canonical = tmp_path / "provenance.json"
    canonical.write_text("existing monthly report")
    with pytest.raises(ValueError, match="Partial-month"):
        write_settlement(pnl, tmp_path)
    with pytest.raises(ValueError, match="Monthly distribution"):
        enrich_with_dr(pnl)
    written = write_revenue_preview(pnl, tmp_path)
    assert set(written) == {"summary", "provenance"}
    assert written["provenance"].parent == tmp_path / "as-of" / "2026-08-15"
    assert canonical.read_text() == "existing monthly report"
    prov = json.loads(written["provenance"].read_text())
    assert prov["provisional"] is True
    assert prov["excluded_components"] == ["distribution_rewards"]
    assert "Provisional revenue through 2026-08-15" in written["summary"].read_text()


def test_cli_parses_explicit_cutoff():
    from settle.cli import _build_parser

    args = _build_parser().parse_args(["run", "--prime", "obex", "--month", "2026-08",
                                       "--as-of", "2026-08-15"])
    assert args.as_of == date(2026, 8, 15)


def test_partial_sde_does_not_apply_a_later_burn_override():
    import pandas as pd

    from settle.compute.prime_agent_revenue import _capped_sd_revenue_daily_resolved
    from settle.domain.sde import SDEEntry

    entry = SDEEntry("grove", "E8", "ethereum", "capped", Decimal(100), None,
                     date(2026, 1, 1), None, burn_date=date(2026, 3, 20))
    month = Month(2026, 3)
    for cutoff, expected in [(date(2026, 3, 15), Decimal("0.5")),
                             (date(2026, 3, 31), Decimal(1))]:
        period = Period.from_month(month, as_of=cutoff)
        values = pd.DataFrame([{"block_date": date(2026, 3, day),
                                "cum_value": Decimal(50), "uncapped_value": Decimal(100)}
                               for day in range(1, cutoff.day + 1)])
        revenue, share = _capped_sd_revenue_daily_resolved(
            values, Decimal(10), entry, period, value_eom=Decimal(90),
        )
        assert share == expected
        assert revenue == Decimal(10) * expected
