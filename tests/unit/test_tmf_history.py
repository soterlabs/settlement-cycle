"""Unit tests for the Smart Burn Engine history dataset (compute.tmf_history)
and the source's burn decoding."""

from __future__ import annotations

import csv
import json
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.compute.tmf_history import (
    SCHEMA_VERSION,
    HistoryDataset,
    aggregate,
    build_history_dataset,
    period_key,
    write_history_dataset,
)
from settle.domain.tmf import SbeKick, SbeParamChange, SkyBurn

# 2024-11-14 21:18:35Z (first real kick) … 2026-08-17 (TMF cast)
T_2024_11 = 1_731_619_115
T_2025_03 = 1_741_000_000
T_2026_08 = 1_786_975_400
# The three real timestamps this classification exists to separate:
# policy.tmf_effective_from (the TMF's first cast), the 2025-06-30 MKR->SKY
# supply correction before it, and the first engine burn after it.
CAST = 1_786_975_343             # 2026-08-17T14:02:23Z
BURN_CORRECTION_TS = 1_751_292_023   # 2025-06-30T14:00:23Z — 426,292,860.23 SKY
BURN_ENGINE_TS = 1_789_303_331       # 2026-09-13T12:42:11Z —   2,860,943.76 SKY


def _kick(i: int, ts: int, burn: str, bought: str, farm: str | None = None) -> SbeKick:
    b = D(burn)
    return SbeKick(block=100 + i, log_index=1, ts=ts, tx=f"0x{i:064x}", tot=D(6000),
                   lot=D(6000) * b, pay=D(6000) * (1 - b), bought=D(bought), burn=b, hop=3748,
                   farm=farm, flapper="0xflap")


def _burn(i: int, ts: int, amount: str, protocol: bool) -> SkyBurn:
    return SkyBurn(block=500 + i, log_index=1, ts=ts, tx=f"0xb{i:063x}",
                   sender="0xpp" if protocol else "0xanon", sink="0xdead", amount=D(amount),
                   protocol=protocol)


@pytest.mark.parametrize("gran,expected", [
    ("monthly", "2026-08"), ("quarterly", "2026-Q3"), ("annual", "2026"),
])
def test_period_key(gran, expected):
    assert period_key(T_2026_08, gran) == expected


def test_period_key_rejects_unknown():
    with pytest.raises(ValueError):
        period_key(T_2026_08, "weekly")


def test_aggregate_buckets_kicks_and_burns_by_period():
    kicks = [
        _kick(0, T_2024_11, "0.7", "1000"),            # 4,200 buyback / 1,800 stakers
        _kick(1, T_2025_03, "1", "2000"),              # 6,000 / 0
        _kick(2, T_2026_08, "0.55", "3000"),           # 3,300 / 2,700
        _kick(3, T_2026_08 + 3748, "0.55", "3000"),
    ]
    burns = [_burn(0, T_2026_08 + 86400 * 20, "2860943.76", True),
             _burn(1, T_2026_08 + 86400 * 21, "5", False)]
    monthly = {r.period: r for r in aggregate(kicks, burns, "monthly", tmf_effective_from=CAST)}
    assert list(monthly) == ["2024-11", "2025-03", "2026-08", "2026-09"]
    nov = monthly["2024-11"]
    assert (nov.kicks, nov.usds_buyback, nov.usds_to_stakers, nov.usds_total) == (1, D(4200), D(1800), D(6000))
    aug = monthly["2026-08"]
    assert (aug.kicks, aug.usds_buyback, aug.usds_to_stakers, aug.sky_bought) == (2, D(6600), D(5400), D(6000))
    assert aug.sky_avg_price == D(6600) / D(6000)
    sep = monthly["2026-09"]                          # burns only, no kicks
    assert (sep.kicks, sep.sky_burn_protocol, sep.sky_burn_other, sep.burn_events) == (
        0, D("2860943.76"), D(5), 2)
    assert sep.sky_avg_price is None

    annual = {r.period: r for r in aggregate(kicks, burns, "annual", tmf_effective_from=CAST)}
    assert annual["2026"].usds_total == D(12000)
    assert annual["2026"].sky_burn_protocol == D("2860943.76")
    quarterly = [r.period for r in aggregate(kicks, burns, "quarterly", tmf_effective_from=CAST)]
    assert quarterly == ["2024-Q4", "2025-Q1", "2026-Q3"]


def test_dataset_document_shape_and_totals(tmp_path: Path):
    kicks = [_kick(0, T_2025_03, "1", "2000", farm="0xfarmA"),
             _kick(1, T_2026_08, "0.55", "3000", farm="0xfarmB")]
    burns = [_burn(0, T_2026_08 + 86400, "100", True)]
    params = [SbeParamChange(block=50, log_index=0, ts=T_2024_11 - 10, tx="0xf",
                             contract="MCD_SPLIT", what="farm", value="0xfarmA")]
    ds = HistoryDataset(tmf_effective_from=CAST, from_block=20770191, to_block=25_900_000, to_ts=T_2026_08 + 90000,
                        kicks=kicks, burns=burns, param_changes=params,
                        contracts={"MCD_SPLIT": "0xsplit"}, notes=["n1"])
    doc = build_history_dataset(ds)
    assert doc["schema_version"] == SCHEMA_VERSION
    assert doc["source"]["from_block"] == 20770191 and doc["source"]["to_block"] == 25_900_000
    assert doc["totals"]["kicks"] == 2
    assert doc["totals"]["usds_buyback"] == 9300.0
    assert doc["totals"]["usds_to_stakers"] == 2700.0
    assert doc["totals"]["usds_total"] == 12000.0
    assert doc["totals"]["sky_burn_protocol"] == 100.0
    assert doc["latest_kick"]["tx"] == kicks[1].tx and doc["latest_kick"]["splitter_hop"] == 3748
    assert [r["period"] for r in doc["periods"]["monthly"]] == ["2025-03", "2026-08"]
    assert [r["period"] for r in doc["periods"]["annual"]] == ["2025", "2026"]
    assert doc["parameter_changes"][0]["what"] == "farm"
    assert set(doc["definitions"]) >= {"usds_buyback", "usds_to_stakers", "usds_total",
                                       "sky_bought", "sky_burn_protocol"}

    paths = write_history_dataset(ds, tmp_path / "data")
    reloaded = json.loads(paths["json"].read_text())
    assert reloaded["totals"] == doc["totals"]
    rows = list(csv.DictReader(paths["kicks"].open()))
    assert [r["farm"] for r in rows] == ["0xfarmA", "0xfarmB"]
    assert rows[1]["usds_buyback"] == "3300"                   # exact Decimal, trailing zeros stripped
    brows = list(csv.DictReader(paths["burns"].open()))
    assert brows[0]["protocol"] == "true" and brows[0]["sky_amount"] == "100"


def test_json_numbers_are_finite_floats_rounded_to_cents():
    ds = HistoryDataset(tmf_effective_from=CAST, from_block=1, to_block=2, to_ts=T_2026_08,
                        kicks=[_kick(0, T_2026_08, "0.55", "3000.123456789")], burns=[])
    m = build_history_dataset(ds)["periods"]["monthly"][0]
    assert m["sky_bought"] == 3000.12
    assert m["sky_avg_price"] == round(3300 / 3000.123456789, 6)


# ── source: burns + history decode with a mock fetch ────────────────────────

from settle.extract.hypersync import LogRow  # noqa: E402
from settle.normalize.sources import hypersync_sbe as S  # noqa: E402

WAD = 10**18
RAD = 10**45
PP = "0xbe8e3e3618f7474f8cb1d074a26affef007e98fb"
DEAD = "0x000000000000000000000000000000000000dead"
ZERO = "0x0000000000000000000000000000000000000000"
_C = {
    "MCD_SPLIT": "0xBF7111F13386d23cb2Fba5A538107A73f6872bCF",
    "MCD_FLAP": "0x374D9c3d5134052Bc558F432Afa1df6575f07407",
    "MCD_KICK": "0xD889477102e8C4A857b78Fcc2f134535176Ec1Fc",
    "REWARDS_DIST_LSSKY_SKY": "0x675671A8756dDb69F7254AFB030865388Ef699Ee",
    "MCD_VEST_SKY_TREASURY": "0x67eaDb3288cceDe034cE95b0511DCc65cf630bB6",
    "REWARDS_LSSKY_USDS": "0x38E4254bD82ED5Ee97CD1C4278FAae748d998865",
    "SKY": "0x56072C95FAA701256059aa122697B133aDEd9279",
}
LEGACY_FLAP = "0xc5A9CaeBA70D6974cBDFb28120C3611Dd9910355"


def _w(n: int) -> str:
    return hex(n)[2:].rjust(64, "0")


def _t(addr: str) -> str:
    return "0x" + "0" * 24 + addr.lower()[2:]


def _lrow(addr, topic0, data_words, block, idx, tx, topic1=None, topic2=None) -> LogRow:
    return LogRow(block, idx, 1_000 * block, addr.lower(), topic0, topic1, topic2, None,
                  "0x" + "".join(_w(x) for x in data_words), transaction_hash=tx)


def test_sky_burns_classifies_sinks_and_senders():
    rows = [
        _lrow(_C["SKY"], S._TRANSFER, [100 * WAD], 10, 1, "0xa", _t(PP), _t(DEAD)),      # protocol → dead
        _lrow(_C["SKY"], S._TRANSFER, [5 * WAD], 11, 1, "0xb", _t("0x" + "11" * 20), _t(DEAD)),  # other → dead
        _lrow(_C["SKY"], S._TRANSFER, [7 * WAD], 12, 1, "0xc", _t(PP), _t(ZERO)),        # protocol burn()
        _lrow(_C["SKY"], S._TRANSFER, [9 * WAD], 13, 1, "0xd", _t("0x" + "22" * 20), _t(ZERO)),  # converter — excluded
    ]
    captured = {}

    def fetch(chain, selections, lo, hi, log_fields=None, post=None):
        captured["sel"] = selections
        return rows
    src = S.HyperSyncSbeSource(_C)
    burns = src.sky_burns(1, 20, sinks={"dead": DEAD, "zero": ZERO}, protocol_senders=[PP], fetch=fetch)
    assert [(b.amount, b.protocol, b.sink) for b in burns] == [
        (D(100), True, DEAD), (D(5), False, DEAD), (D(7), True, ZERO)]
    # zero-address selection is restricted to protocol senders at the QUERY level too
    zero_sel = captured["sel"][1]
    assert zero_sel["topics"][1] == [_t(PP)] and zero_sel["topics"][2] == [_t(ZERO)]


def test_history_tracks_farm_and_flapper_pointers_across_a_swap():
    burn70 = 70 * WAD // 100
    rows = [
        # deployment: hop, burn=100%, flapper=legacy
        _lrow(_C["MCD_SPLIT"], S._FILE_U, [10249], 1, 1, "0xd", "0x" + b"hop".ljust(32, b"\x00").hex()),
        _lrow(_C["MCD_SPLIT"], S._FILE_U, [1 * WAD], 1, 2, "0xd", "0x" + b"burn".ljust(32, b"\x00").hex()),
        _lrow(_C["MCD_SPLIT"], S._FILE_A, [int(LEGACY_FLAP, 16)], 1, 3, "0xd", "0x" + b"flapper".ljust(32, b"\x00").hex()),
        # swap to the SwapOnly flapper, then a farm is set and burn → 70%
        _lrow(_C["MCD_SPLIT"], S._FILE_A, [int(_C["MCD_FLAP"], 16)], 2, 1, "0xe", "0x" + b"flapper".ljust(32, b"\x00").hex()),
        _lrow(_C["MCD_SPLIT"], S._FILE_A, [int("0x" + "fa" * 20, 16)], 3, 1, "0xf", "0x" + b"farm".ljust(32, b"\x00").hex()),
        _lrow(_C["MCD_SPLIT"], S._FILE_U, [burn70], 3, 2, "0xf", "0x" + b"burn".ljust(32, b"\x00").hex()),
        # a kick under the new regime
        _lrow(_C["MCD_FLAP"], S._EXEC, [4200 * WAD, 100_000 * WAD], 4, 1, "0xk"),
        _lrow(_C["MCD_SPLIT"], S._KICK, [6000 * RAD, 4200 * WAD, 1800 * WAD], 4, 2, "0xk"),
    ]
    src = S.HyperSyncSbeSource(_C, flappers=[LEGACY_FLAP, _C["MCD_FLAP"]])
    act = src.history(1, 10, deploy_block=1, fetch=lambda *a, **k: rows)
    assert len(act.kicks) == 1
    k = act.kicks[0]
    assert (k.burn, k.hop, k.pay, k.bought) == (D("0.7"), 10249, D(1800), D(100_000))
    assert k.farm == "0x" + "fa" * 20 and k.flapper == _C["MCD_FLAP"].lower()
    whats = [c.what for c in act.param_changes]
    assert whats == ["hop", "burn", "flapper", "flapper", "farm", "burn"]
    assert all(c.address == _C["MCD_SPLIT"].lower() for c in act.param_changes)
    assert LEGACY_FLAP.lower() in src._flappers and src._by_addr[LEGACY_FLAP.lower()] == "MCD_FLAP"


# ── review round 3 ──────────────────────────────────────────────────────────

def test_history_refuses_a_start_that_is_not_the_deployment_block():
    src = S.HyperSyncSbeSource(_C)
    with pytest.raises(ValueError, match="deployment block"):
        src.history(21_000_000, 21_100_000, deploy_block=20_770_191, fetch=lambda *a, **k: [])


def test_legacy_lp_flapper_exec_is_decoded():
    """FlapperUniV2 (LP variant) emits Exec(lot, sell, buy, liquidity); ``buy`` is
    the SKY bought. A kick routed to it must not fail the build."""
    rows = [
        _lrow(_C["MCD_SPLIT"], S._FILE_A, [int(LEGACY_FLAP, 16)], 1, 1, "0xd",
              "0x" + b"flapper".ljust(32, b"\x00").hex()),
        _lrow(LEGACY_FLAP, S._EXEC_LP, [6000 * WAD, 5000 * WAD, 90_000 * WAD, 123], 2, 1, "0xk"),
        _lrow(_C["MCD_SPLIT"], S._KICK, [6000 * RAD, 6000 * WAD, 0], 2, 2, "0xk"),
    ]
    src = S.HyperSyncSbeSource(_C, flappers=[LEGACY_FLAP])
    act = src.history(1, 10, deploy_block=1, fetch=lambda *a, **k: rows)
    assert act.kicks[0].bought == D(90_000)
    assert act.kicks[0].flapper == LEGACY_FLAP.lower()
    assert act.param_changes[0].address == _C["MCD_SPLIT"].lower()


def test_param_change_rows_carry_the_emitting_address_for_both_flappers():
    rows = [
        _lrow(LEGACY_FLAP, S._FILE_U, [98 * WAD // 100], 1, 1, "0xa", "0x" + b"want".ljust(32, b"\x00").hex()),
        _lrow(_C["MCD_FLAP"], S._FILE_U, [98 * WAD // 100], 2, 1, "0xb", "0x" + b"want".ljust(32, b"\x00").hex()),
    ]
    src = S.HyperSyncSbeSource(_C, flappers=[LEGACY_FLAP])
    act = src.history(1, 10, deploy_block=1, fetch=lambda *a, **k: rows)
    assert [(c.contract, c.address) for c in act.param_changes] == [
        ("MCD_FLAP", LEGACY_FLAP.lower()), ("MCD_FLAP", _C["MCD_FLAP"].lower())]
    doc = build_history_dataset(HistoryDataset(tmf_effective_from=CAST, from_block=1, to_block=10, to_ts=0, kicks=[], burns=[],
                                               param_changes=act.param_changes))
    assert doc["parameter_changes"][0]["address"] == LEGACY_FLAP.lower()
    assert doc["parameter_changes"][0]["value"] == "0.98"


def test_activity_seeds_farm_and_flapper(monkeypatch):
    """A month with no Splitter File(farm)/File(flapper) must still stamp kicks
    with the pointers in force at the start of the month."""
    rows = [
        _lrow(_C["MCD_FLAP"], S._EXEC, [3300 * WAD, 50_000 * WAD], 10, 1, "0xk"),
        _lrow(_C["MCD_SPLIT"], S._KICK, [6000 * RAD, 3300 * WAD, 2700 * WAD], 10, 2, "0xk"),
    ]

    from types import SimpleNamespace
    monkeypatch.setattr(S.hs, "query_logs", lambda *a, **k: SimpleNamespace(rows=rows))
    act = S.HyperSyncSbeSource(_C).activity(
        "2026-08", 1, 20, from_ts=0, to_ts=1, burn_at_start=D("0.55"), hop_at_start=3748,
        farm_at_start=_C["REWARDS_LSSKY_USDS"].lower(), flapper_at_start=_C["MCD_FLAP"].lower(),
    )
    assert (act.kicks[0].farm, act.kicks[0].flapper) == (
        _C["REWARDS_LSSKY_USDS"].lower(), _C["MCD_FLAP"].lower())


def test_activity_and_history_query_the_same_flapper_set(monkeypatch):
    from types import SimpleNamespace
    seen: list[list[str]] = []

    def q(chain, sel, lo, hi, **k):
        seen.append(sorted(sel[0]["address"]))
        return SimpleNamespace(rows=[])
    monkeypatch.setattr(S.hs, "query_logs", q)
    src = S.HyperSyncSbeSource(_C, flappers=[LEGACY_FLAP])
    src.activity("2026-08", 1, 2, from_ts=0, to_ts=1, burn_at_start=D(1), hop_at_start=1)
    src.history(1, 2, deploy_block=1, fetch=lambda chain, sel, lo, hi, **k: (seen.append(sorted(sel[0]["address"])), [])[1])
    assert LEGACY_FLAP.lower() in seen[0] and LEGACY_FLAP.lower() in seen[1]


def test_dec_never_uses_exponent_notation():
    from settle.compute.tmf_history import dec_str as _dec
    assert _dec(D(1) / D(10**18)) == "0.000000000000000001"
    assert _dec(D("25000.000000000000000000")) == "25000"
    assert _dec(D("6000.000000000000000000")) == "6000"
    assert _dec(D("-200000000.000000000000000000")) == "-200000000"
    assert _dec(D("0.55")) == "0.55"


def test_sky_burns_refuses_empty_protocol_senders():
    with pytest.raises(ValueError, match="protocol_senders is empty"):
        S.HyperSyncSbeSource(_C).sky_burns(1, 2, sinks={"dead": DEAD, "zero": ZERO},
                                            protocol_senders=[], fetch=lambda *a, **k: [])


# ── burn classification (schema 1.2.0) ──────────────────────────────────────

from settle.compute.tmf_history import BURN_KINDS, burn_kind  # noqa: E402


def _b(ts: int, amount: str, protocol: bool) -> SkyBurn:
    return SkyBurn(block=900, log_index=1, ts=ts, tx="0xb", sender="0xpp" if protocol else "0xanon",
                   sink="0x" + "00" * 19 + "00", amount=D(amount), protocol=protocol)


def test_burn_kind_splits_protocol_burns_at_the_tmf_cast():
    # The two protocol kinds are indistinguishable on-chain — both are
    # Pause Proxy -> zero address — so the cast timestamp is the only rule.
    assert burn_kind(_b(CAST - 1, "426292860.23", True), CAST) == "supply_correction"
    assert burn_kind(_b(CAST, "2860943.76", True), CAST) == "engine"
    assert burn_kind(_b(CAST + 1, "1", True), CAST) == "engine"
    # a third party sending to 0x…dEaD is neither
    assert burn_kind(_b(CAST + 1, "1.6", False), CAST) == "third_party"
    assert burn_kind(_b(CAST - 1, "1.6", False), CAST) == "third_party"
    assert set(BURN_KINDS) == {"engine", "supply_correction", "third_party"}


def test_period_rows_split_the_two_protocol_burns_and_still_sum_to_protocol():
    """The real shape, with the real timestamps: a 426.3M correction in
    2025-06 and a 2.86M engine burn in 2026-09. Summed together the engine
    burn is 0.67% of the total, which is why it needs its own field."""
    burns = [_b(BURN_CORRECTION_TS, "426292860.23", True),     # 2025-06-30
             _b(BURN_ENGINE_TS, "2860943.76", True),       # 2026-09-13
             _b(1_751_103_491, "1.61", False)]            # third party, 2025-06-28
    rows = {r.period: r for r in aggregate([], burns, "monthly", tmf_effective_from=CAST)}
    jun = rows["2025-06"]
    assert jun.sky_burn_supply_correction == D("426292860.23")
    assert jun.sky_burn_engine == 0
    assert jun.sky_burn_other == D("1.61")
    sep = rows["2026-09"]
    assert sep.sky_burn_engine == D("2860943.76")
    assert sep.sky_burn_supply_correction == 0
    # the legacy field is still the sum of both protocol kinds
    assert jun.sky_burn_protocol == D("426292860.23")
    assert sep.sky_burn_protocol == D("2860943.76")

    ds = HistoryDataset(tmf_effective_from=CAST, from_block=1, to_block=2, to_ts=T_2026_08,
                        kicks=[], burns=burns)
    t = ds.totals
    assert t.sky_burn_engine == D("2860943.76")
    assert t.sky_burn_supply_correction == D("426292860.23")
    assert t.sky_burn_protocol == D("429153803.99")       # what consumers had before


def test_document_publishes_the_split_the_boundary_and_the_kinds(tmp_path: Path):
    burns = [_b(BURN_CORRECTION_TS, "426292860.23", True), _b(BURN_ENGINE_TS, "2860943.76", True)]
    ds = HistoryDataset(tmf_effective_from=CAST, from_block=1, to_block=2, to_ts=T_2026_08,
                        kicks=[], burns=burns)
    doc = build_history_dataset(ds)
    assert doc["schema_version"] == "1.2.0"
    assert doc["source"]["tmf_effective_from"] == "2026-08-17T14:02:23Z"
    assert doc["totals"]["sky_burn_engine"] == 2860943.76
    assert doc["totals"]["sky_burn_supply_correction"] == 426292860.23
    assert doc["totals"]["sky_burn_protocol"] == 429153803.99
    assert {"sky_burn_engine", "sky_burn_supply_correction"} <= set(doc["definitions"])
    # the CSV carries the per-row classification
    paths = write_history_dataset(ds, tmp_path / "d")
    rows = list(csv.DictReader(paths["burns"].open()))
    assert [r["kind"] for r in rows] == ["supply_correction", "engine"]


def test_aggregate_requires_the_boundary():
    """No default is correct: one would silently call every protocol burn an
    engine burn, the other would call none of them that."""
    with pytest.raises(TypeError):
        aggregate([], [], "monthly")   # type: ignore[call-arg]
