"""A transit balance is not automatically an additional frozen-month refund."""

import copy
from datetime import UTC, date, datetime
from decimal import Decimal as D

import pytest

from scripts.audit_usds_bridge_transit_2026 import cost_delta, verified_wallet_usds
from settle.domain.config import load_prime_by_id
from settle.domain.sky_tokens import USDS_BY_CHAIN


def frozen(idle=100, utilized=150, bridge=0):
    return {
        "\u2212 ALM idle": str(idle),
        "utilized": str(utilized),
        "daily Sky charge": str(D(utilized) * D(".04") / 365),
        "base APR": ".04",
        "bridge_usds": str(bridge),
    }


def test_embedded_bridge_claim_is_not_refunded_twice():
    # Exactly the July finding: the frozen idle leg already includes the claim.
    assert cost_delta(frozen(), D(100), D(1000), wallet_usds=D(0)) == 0
    assert cost_delta(frozen(idle=80, bridge=20), D(100), D(1000), wallet_usds=D(0)) == 0
    with pytest.raises(ValueError, match="verified wallet"):
        cost_delta(frozen(), D(100), D(1000))
    missing = frozen()
    del missing["\u2212 ALM idle"]
    with pytest.raises(ValueError, match="verified wallet"):
        cost_delta(missing, D(100), D(1000), wallet_usds=D(0))


def test_october_baseline_never_uses_a_regenerated_workbook(monkeypatch):
    from scripts import audit_usds_bridge_transit_2026 as module

    def unexpected(*args):
        raise AssertionError("October workbook must not be used as the old baseline")

    monkeypatch.setattr(module, "historical_rows", unexpected)
    daily = {
        "2026-10-12": {"utilized": "100", "daily_sky_rev": "1", "base_apr": ".04", "alm_usds": "90"}
    }
    books = {}
    row = module.frozen_daily_row("spark", "2026-10-12", books, daily)
    assert row["alm_usds"] == "90" and row["daily Sky charge"] == "1"
    assert books == {}
    assert module.frozen_daily_row("spark", "2026-10-13", books, daily) is None


def test_stale_idle_after_delivery_produces_a_debit_and_preserves_rate():
    credit = cost_delta(frozen(idle=150), D(0), D(1000), wallet_usds=D(50))
    assert abs(credit + D(100) * D(".04") / 365) < D("1e-25")


def test_missing_claim_and_floor_use_the_whole_replacement():
    credit = cost_delta(frozen(idle=0, utilized=50), D(200), D(1000), wallet_usds=D(0))
    assert abs(credit - D(50) * D(".04") / 365) < D("1e-25")
    # A frozen zero charge cannot identify which historical rate convention
    # applies when removing stale idle makes the charge positive. Do not guess.
    credit = cost_delta(frozen(idle=150, utilized=0), D(0), D(1000), wallet_usds=D(50))
    assert credit is None


@pytest.mark.parametrize("wallet,pending", [(D(-1), D(0)), (D(0), D(-1)), (D("NaN"), D(0))])
def test_invalid_wallet_or_pending_is_not_a_financial_zero(wallet, pending):
    with pytest.raises(ValueError, match="Invalid idle"):
        cost_delta(frozen(), pending, D(1000), wallet_usds=wallet)


@pytest.fixture
def wallet_document():
    prime = load_prime_by_id("spark")
    day = "2026-07-06"
    stamp = int(datetime(2026, 7, 6, 23, 59, 59, tzinfo=UTC).timestamp())
    ids = {"ethereum": 1, "base": 8453, "arbitrum": 42161, "optimism": 10, "unichain": 130}
    document = {"status": "complete", "prime": "spark", "chains": {}, "days": {day: {}}}
    for chain in prime.alm:
        if chain not in USDS_BY_CHAIN or chain not in prime.chains_at(date.fromisoformat(day)):
            continue
        token = USDS_BY_CHAIN[chain]
        document["chains"][chain.value] = {
            "holder": prime.alm[chain].hex,
            "token": token.address.hex,
            "decimals": token.decimals,
            "chain_id": ids[chain.value],
        }
        document["days"][day][chain.value] = {
            "block": 100,
            "raw_balance": str(10**18),
            "headers": [
                {
                    "number": "0x64",
                    "hash": "0x" + "aa" * 32,
                    "parentHash": "0x" + "ff" * 32,
                    "timestamp": hex(stamp),
                },
                {
                    "number": "0x65",
                    "hash": "0x" + "bb" * 32,
                    "parentHash": "0x" + "aa" * 32,
                    "timestamp": hex(stamp + 1),
                },
            ],
        }
    return document, prime, day


def test_verified_wallet_evidence_covers_every_active_native_usds_chain(wallet_document):
    document, prime, day = wallet_document
    assert verified_wallet_usds(document, prime, day) == 5
    del document["days"][day]["base"]
    with pytest.raises(ValueError, match="missing an active chain"):
        verified_wallet_usds(document, prime, day)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d.update(status="capturing"),
        lambda d: d.update(prime="grove"),
        lambda d: d["chains"]["base"].update(holder="0x" + "11" * 20),
        lambda d: d["chains"]["base"].update(chain_id=1),
        lambda d: d["days"]["2026-07-06"]["base"]["headers"][1].update(parentHash="bad"),
        lambda d: d["days"]["2026-07-06"]["base"]["headers"][1].update(timestamp="0x0"),
        lambda d: d["days"]["2026-07-06"]["base"].update(raw_balance="-1"),
        lambda d: d["days"]["2026-07-06"]["base"].update(raw_balance=str(2**256)),
        lambda d: d["days"]["2026-07-06"]["base"].update(raw_balance="\u0661"),
    ],
)
def test_unverified_evidence_never_becomes_a_balance(wallet_document, mutation):
    document, prime, day = wallet_document
    document = copy.deepcopy(document)
    mutation(document)
    with pytest.raises(ValueError):
        verified_wallet_usds(document, prime, day)


@pytest.mark.parametrize("change", ["wallet", "manifest", "published_checks"])
def test_copied_packet_checks_the_files_it_actually_consumes(tmp_path, change):
    import json
    import shutil

    from scripts.reconcile_usds_bridge_baseline_2026 import FOLDER, PACKAGE, replay

    folder = tmp_path / "packet"
    shutil.copytree(FOLDER, folder)
    if change == "wallet":
        path = folder / "wallet-inputs.json"
        doc = json.loads(path.read_text())
        doc["days"]["2026-07-06"]["base"]["raw_balance"] = str(10**18)
        path.write_text(json.dumps(doc))
        message = "Changed retained bridge source"
    else:
        path = folder / "sources.json"
        doc = json.loads(path.read_text())
        if change == "manifest":
            del doc["sha256"][PACKAGE + "/wallet-inputs.json"]
            message = "source hash manifest"
        else:
            doc["prior_idle_baseline_checks"] = []
            message = "published idle baseline"
        path.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match=message):
        replay(folder)


def test_capture_refuses_a_checkpoint_from_another_date_scope(
    wallet_document, tmp_path, monkeypatch
):
    import json

    from scripts import capture_bridge_idle_wallets as module

    document, _, day = wallet_document
    source = tmp_path / "pins.json"
    source.write_text(json.dumps(document))
    document = copy.deepcopy(document)
    document["days"]["2026-07-07"] = document["days"][day]
    output = tmp_path / "checkpoint.json"
    output.write_text(json.dumps(document))

    def unexpected(*args):
        raise AssertionError("Invalid scope must be rejected before any RPC call")

    monkeypatch.setattr(module.rpc, "_post", unexpected)
    with pytest.raises(ValueError, match="date scope"):
        module.capture(source, output)


def test_capture_revalidates_cached_headers_and_keeps_failure_incomplete(
    wallet_document, tmp_path, monkeypatch
):
    import json

    from scripts import capture_bridge_idle_wallets as module

    document, _, day = wallet_document
    source = tmp_path / "pins.json"
    source.write_text(json.dumps(document))
    document = copy.deepcopy(document)
    document["days"][day]["base"]["headers"][1]["parentHash"] = "0x" + "00" * 32
    output = tmp_path / "checkpoint.json"
    output.write_text(json.dumps(document))
    monkeypatch.setattr(module.rpc, "rpc_url", lambda c: c.value)

    def post(chain, method, params):
        assert method == "eth_chainId"
        return hex(module.CHAIN_IDS[chain])

    monkeypatch.setattr(module.rpc, "_post", post)
    with pytest.raises(ValueError, match="daily close"):
        module.capture(source, output)
    assert json.loads(output.read_text())["status"] == "incomplete"


def test_offline_replay_replaces_only_spark_sky_item_and_preserves_other_agents():
    import json

    from scripts.reconcile_usds_bridge_baseline_2026 import FOLDER, replay

    result = replay()
    assert result == json.loads((FOLDER / "results.json").read_text())
    assert result["historical_replacement_sky_adjustment"] == "557546.90"
    assert result["incremental_sky_adjustment_vs_pr277"] == "813416.38"
    assert len(result["daily"]) == 132
    checkpoint = json.loads((FOLDER / "pr277-checkpoint.json").read_text())
    combined = result["combined_adjustment_totals"]
    for prime in ["grove", "osero", "obex"]:
        assert combined[prime] == checkpoint["adjustment_totals"][prime]
    for field in ["sv_adj", "dv_adj", "send_credit"]:
        assert combined["spark"][field] == checkpoint["adjustment_totals"]["spark"][field]
    july = {r["date"]: r for r in result["daily"] if r["date"].startswith("2026-07")}
    # During transit only the small claims are missing; after delivery the
    # frozen wallet deduction is stale and must produce signed debits.
    assert abs(D(july["2026-07-06"]["deduction_change"]) - 30000) < D("0.000001")
    assert D(july["2026-07-13"]["signed_credit"]) < 0
    assert D(result["october_ordinary_signed_credit"]).quantize(D(".01")) == D("75063.88")
