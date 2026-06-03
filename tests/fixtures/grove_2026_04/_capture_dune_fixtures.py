"""Capture Grove April-2026 Dune fixture set.

Re-runs every Dune query the Q1 fixture (`grove_2026_03/dune_outputs.json`)
relied on, extended through April 30 pin blocks. Output:

  tests/fixtures/grove_2026_04/
      dune_outputs.json
      blocks_at_eod.json
      blocks_at_eod_base.json
      blocks_at_eod_avalanche.json
      blocks_at_eod_plume.json
      blocks_at_eod_monad.json

Requires ``DUNE_API_KEY`` env var (source ``.secrets/dune_env`` first).

Long-running — each Dune query is a fresh execution. Caches under
``cache/`` keyed by (source_id, query_hash, params, pin_block) so reruns
are fast.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "src"))

from settle.extract.dune import execute_query  # noqa: E402

QUERIES = _REPO / "src" / "settle" / "queries"
OUT = _REPO / "tests" / "fixtures" / "grove_2026_04"
OUT.mkdir(parents=True, exist_ok=True)

# April pin blocks (from settlements/grove/2026-04/provenance.json)
PIN_BLOCKS_SOM = {
    "ethereum": 24781026, "base": 44106126, "avalanche_c": 81789468,
    "plume": 58679343, "monad": 65143725,
}
PIN_BLOCKS_EOM = {
    "ethereum": 24996367, "base": 45402126, "avalanche_c": 84298393,
    "plume": 65382097, "monad": 71616121,
}
START_DATE = "2025-05-14"   # Grove prime start (per config/grove.yaml)
# NOTE: the original value here was "2025-10-23", which is WRONG — Grove's
# prime start is 2025-05-14 (see config/grove.yaml). Using Oct 23 truncated
# the BUIDL and JTRSY transfer history and resulted in cum_balance series
# missing ~$258M (BUIDL) / ~$237M (JTRSY) of pre-Oct-23 deposits, which in
# turn understated the SDE asset value deducted from utilized → over-charged
# CoF → inflated sky_revenue by ~$1.7M in April 2026. Future fixture
# re-captures MUST use the correct prime start.

GROVE_ALM_ETH = bytes.fromhex("491edfb0b8b608044e227225c715981a30f3a44e")
GROVE_ALM_BASE = bytes.fromhex("9b746dbc5269e1df6e4193bcb441c0fbbf1cecee")
GROVE_ALM_AVAX = bytes.fromhex("7107dd8f56642327945294a18a4280c78e153644")
GROVE_ALM_PLUME = bytes.fromhex("1db91ad50446a671e2231f77e00948e68876f812")
GROVE_SUB = bytes.fromhex("1369f7b2b38c76B6478c0f0E66D94923421891Ba".lower())
ZERO_ADDR = b"\x00" * 20

USDS = bytes.fromhex("dc035d45d973e3ec169d2276ddab16f1e407384f")
SUSDS = bytes.fromhex("a3931d71877c0e7a3148cb7eb4463524fec27fbd")
USDC = bytes.fromhex("a0b86991c6218b36c1d19d4a2e9eb0ce3606eb48")

GROVE_ILK = "0x414c4c4f4341544f522d424c4f4f4d2d41000000000000000000000000000000"


def _rows(df):
    """Convert DataFrame to JSON-serializable list of dict rows."""
    out = []
    for _, r in df.iterrows():
        row = {}
        for col in df.columns:
            val = r[col]
            if hasattr(val, "isoformat"):
                row[col] = val.isoformat()
            else:
                row[col] = str(val)
        out.append(row)
    return out


def transfer_ts(chain, token, holder, pin_block, min_transfer=0):
    df = execute_query(
        QUERIES / "transfer_timeseries.sql",
        params={"chain": chain, "token": token, "holder": holder,
                "start_date": START_DATE, "min_transfer_amount": min_transfer},
        pin_block=pin_block,
    )
    return _rows(df)


def venue_inflow(chain, token, from_addr, to_addr, pin_block):
    df = execute_query(
        QUERIES / "venue_inflow.sql",
        params={"chain": chain, "token": token, "from_addr": from_addr,
                "to_addr": to_addr, "start_date": START_DATE},
        pin_block=pin_block,
    )
    return _rows(df)


def main() -> int:
    eth_eom = PIN_BLOCKS_EOM["ethereum"]
    print(f"April fixture capture — Grove (Eth EoM = {eth_eom})")
    fx = {
        "_about": f"Grove April 2026 fixture, captured via Dune; "
                  f"Eth EoM={eth_eom}, start={START_DATE}",
        "pin_block_som_ethereum": PIN_BLOCKS_SOM["ethereum"],
        "pin_block_eom_ethereum": eth_eom,
    }

    # 1. debt
    print("  fetching debt …")
    df = execute_query(
        QUERIES / "debt_timeseries.sql",
        params={"ilk_bytes32": GROVE_ILK, "start_date": "2025-05-14"},
        pin_block=eth_eom,
    )
    fx["debt"] = {"_query": "debt_timeseries.sql",
                  "_params": {"ilk_bytes32": GROVE_ILK, "start_date": "2025-05-14"},
                  "rows": _rows(df)}

    # 2. ssr
    print("  fetching ssr …")
    df = execute_query(QUERIES / "ssr_history.sql", params={}, pin_block=eth_eom)
    fx["ssr"] = {"_anchor": "global", "rows": _rows(df)}

    # 3. subproxy + alm transfer timeseries
    print("  fetching subproxy_usds / subproxy_susds / alm_usds …")
    fx["subproxy_usds"] = {
        "_holder": GROVE_SUB.hex(), "rows": transfer_ts("ethereum", USDS, GROVE_SUB, eth_eom)}
    fx["subproxy_susds"] = {
        "_holder": GROVE_SUB.hex(), "_token": "sUSDS",
        "rows": transfer_ts("ethereum", SUSDS, GROVE_SUB, eth_eom)}
    fx["alm_usds"] = {
        "_holder": GROVE_ALM_ETH.hex(), "_token": "USDS",
        "rows": transfer_ts("ethereum", USDS, GROVE_ALM_ETH, eth_eom)}
    fx["psm_usds"] = {"_about": "Grove has no L2 PSM3", "rows": []}

    # 4. Cat C atokens — E1 aHorRwaRLUSD, E2 aHorRwaUSDC, E3 aEthRLUSD
    ATOKENS = {
        "e1": bytes.fromhex("e3190143eb552456f88464662f0c0c4ac67a77eb"),
        "e2": bytes.fromhex("68215b6533c47ff9f7125ac95adf00fe4a62f79e"),
        "e3": bytes.fromhex("fa82580c16a31d0c1bc632a36f82e83efef3eec0"),
    }
    for vid, atoken in ATOKENS.items():
        print(f"  fetching atoken_{vid}_mints / burns …")
        fx[f"atoken_{vid}_mints"] = {"_token": "0x" + atoken.hex(),
            "rows": venue_inflow("ethereum", atoken, ZERO_ADDR, GROVE_ALM_ETH, eth_eom)}
        fx[f"atoken_{vid}_burns"] = {"_token": "0x" + atoken.hex(),
            "rows": venue_inflow("ethereum", atoken, GROVE_ALM_ETH, ZERO_ADDR, eth_eom)}
        # Per-event Transfer log (sub-day block resolution). Used by the
        # Cat C per-segment yield path to place each event at its exact
        # block instead of bucketing to end-of-day. Closes the consecutive-
        # event-day precision loss observed in E1 April 2026.
        print(f"  fetching atoken_{vid}_event_log …")
        df = execute_query(
            QUERIES / "atoken_event_log.sql",
            params={"chain": "ethereum", "token": atoken,
                    "holder": GROVE_ALM_ETH, "start_date": START_DATE},
            pin_block=eth_eom,
        )
        fx[f"atoken_{vid}_event_log"] = {
            "_chain": "ethereum", "_token": "0x" + atoken.hex(),
            "_holder": "0x" + GROVE_ALM_ETH.hex(),
            "rows": _rows(df),
        }

    # 5. Cat B vaults — E4/E5/E6 on Eth, E19/E23 on Base
    VAULTS_ETH = {
        "e4": bytes.fromhex("beeff08df54897e7544ab01d0e86f013da354111"),
        "e5": bytes.fromhex("beef2b5fd3d94469b7782aebe6364e6e6fb1b709"),
        "e6": bytes.fromhex("beeff0d672ab7f5018dfb614c93981045d4aa98a"),
    }
    for vid, vt in VAULTS_ETH.items():
        print(f"  fetching vault_{vid}_mints / burns …")
        fx[f"vault_{vid}_mints"] = {"_token": "0x" + vt.hex(),
            "rows": venue_inflow("ethereum", vt, ZERO_ADDR, GROVE_ALM_ETH, eth_eom)}
        fx[f"vault_{vid}_burns"] = {"_token": "0x" + vt.hex(),
            "rows": venue_inflow("ethereum", vt, GROVE_ALM_ETH, ZERO_ADDR, eth_eom)}
    VAULTS_BASE = {
        "e19": (bytes.fromhex("beef2d50b428675a1921bc6bbf4bfb9d8cf1461a"), GROVE_ALM_BASE),
        "e23": (bytes.fromhex("beef0e0834849acc03f0089f01f4f1eeb06873c9"), GROVE_ALM_BASE),
    }
    for vid, (vt, alm) in VAULTS_BASE.items():
        print(f"  fetching vault_{vid}_mints / burns (base) …")
        base_eom = PIN_BLOCKS_EOM["base"]
        fx[f"vault_{vid}_mints"] = {"_chain": "base", "_token": "0x" + vt.hex(), "_holder": "0x" + alm.hex(),
            "rows": venue_inflow("base", vt, ZERO_ADDR, alm, base_eom)}
        fx[f"vault_{vid}_burns"] = {"_chain": "base", "_token": "0x" + vt.hex(), "_holder": "0x" + alm.hex(),
            "rows": venue_inflow("base", vt, alm, ZERO_ADDR, base_eom)}

    # 6. cum_balance_* — Cat A + Cat E venues
    CUM_VENUES = [
        # (vid, chain, token_hex)
        ("e7",  "ethereum", "51c2d74017390cbbd30550179a16a1c28f7210fc"),  # STAC
        ("e8",  "ethereum", "5a0f93d040de44e78f251b03c43be9cf317dcf64"),  # JAAA
        ("e9",  "ethereum", "8c213ee79581ff4984583c6a801e5263418c4b86"),  # JTRSY
        ("e10", "ethereum", "6a9da2d710bb9b700acde7cb81f10f1ff8c89041"),  # BUIDL-I
        ("e13", "ethereum", "8292bb45bf1ee4d140127049757c2e0ff06317ed"),  # RLUSD raw
        ("e14", "ethereum", "00000000efe302beaa2b3e6e1b18d08d69a9012a"),  # AUSD raw
        ("e15", "ethereum", "a0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"),  # USDC raw
        ("e16", "ethereum", "6b175474e89094c44da98b954eedeac495271d0f"),  # DAI raw
        ("e17", "ethereum", "dc035d45d973e3ec169d2276ddab16f1e407384f"),  # USDS raw
        ("e18", "ethereum", "a3931d71877c0e7a3148cb7eb4463524fec27fbd"),  # sUSDS raw
        ("e20", "avalanche_c", "58F93d6b1EF2F44eC379Cb975657C132CBeD3B6b"),  # JAAA-avax
        ("e21", "avalanche_c", "2C0aDFF8e114f3cA106051144353aC703D24B901"),  # GACLO-1
        ("e22", "plume",       "9477724Bb54AD5417de8Baff29e59DF3fB4DA74f"),  # ACRDX
    ]
    CHAIN_TO_ALM = {
        "ethereum": GROVE_ALM_ETH, "base": GROVE_ALM_BASE,
        "avalanche_c": GROVE_ALM_AVAX, "plume": GROVE_ALM_PLUME,
    }
    BUIDL_MIN_TRANSFER = 1_000_000   # filter sub-$1M yield mints (config E10)
    for vid, chain, token_hex in CUM_VENUES:
        token = bytes.fromhex(token_hex.lower())
        holder = CHAIN_TO_ALM[chain]
        min_tx = BUIDL_MIN_TRANSFER if vid == "e10" else 0
        pb = PIN_BLOCKS_EOM[chain]
        print(f"  fetching cum_balance_{vid} ({chain}) …")
        fx[f"cum_balance_{vid}"] = {
            "_chain": chain, "_token": "0x" + token_hex.lower(),
            "rows": transfer_ts(chain, token, holder, pb, min_tx)}

    # 7. inflow_by_counterparty_eXX — per-(token, holder) counterparty-attributed
    #    transfer log. Needed for Cat A par-stable venues so the pipeline can
    #    distinguish "principal arrived via transfer" from "yield accrued in
    #    place". Without this, balance changes get misclassified as revenue —
    #    bit us in April when $1M arrived at E14 (AUSD Eth ALM) with no
    #    inflow record. E15 was originally the only one captured (USDC); the
    #    rest were skipped because Q1 balances stayed at $0.
    AUSD = bytes.fromhex("00000000efe302beaa2b3e6e1b18d08d69a9012a")
    USDC_MONAD = bytes.fromhex("754704bc059f8c67012fed69bc8a327a5aafb603")
    ALT_HOLDER = bytes.fromhex("94b398acb2fce988871218221ea6a4a2b26cccbc")
    INFLOW_BY_CP: list[tuple[str, str, bytes, bytes, int]] = [
        # (vid, chain, token, holder, pin_block)
        ("e14", "ethereum", AUSD, GROVE_ALM_ETH, eth_eom),
        ("e15", "ethereum", USDC, GROVE_ALM_ETH, eth_eom),
        ("e31", "ethereum", AUSD, ALT_HOLDER, eth_eom),
        ("e34", "monad",    AUSD, ALT_HOLDER, PIN_BLOCKS_EOM["monad"]),
        ("e35", "monad",    USDC_MONAD, ALT_HOLDER, PIN_BLOCKS_EOM["monad"]),
    ]
    for vid, chain, token, holder, pb in INFLOW_BY_CP:
        print(f"  fetching inflow_by_counterparty_{vid} ({chain}) …")
        df = execute_query(
            QUERIES / "inflow_by_counterparty.sql",
            params={"chain": chain, "token": token, "holder": holder,
                    "start_date": START_DATE},
            pin_block=pb,
        )
        fx[f"inflow_by_counterparty_{vid}"] = {
            "_chain": chain, "_token": "0x" + token.hex(),
            "_holder": "0x" + holder.hex(), "rows": _rows(df)}

    # 7b. EOA directed-outflow caps — for ``display_only`` venues whose
    # ``paired_with`` anchors expect a ``directed_inflow_timeseries`` lookup
    # against (token, ALM, holder_override). The compute layer uses this as
    # the per-counterparty principal-cap when classifying paired_source
    # inflows at the anchor. Currently E36 (OOB USDC pipeline to 0xd94f...).
    # Without this, paired-source inflows above $0 cap get reclassified as
    # realized revenue at the anchor — exactly the E14 April phantom $6.5M.
    EOA_RELAY = bytes.fromhex("d94f9ef3395bbe41c1f05ced3c9a7dc520d08036")
    EOA_VENUES_OUTFLOW: list[tuple[str, str, bytes, bytes, bytes, int]] = [
        # (vid, chain, token, from_addr, to_addr, pin_block)
        ("e36", "ethereum", USDC, GROVE_ALM_ETH, EOA_RELAY, eth_eom),
    ]
    for vid, chain, token, from_addr, to_addr, pb in EOA_VENUES_OUTFLOW:
        print(f"  fetching eoa_outflow_{vid} ({chain}) …")
        fx[f"eoa_outflow_{vid}"] = {
            "_chain": chain, "_token": "0x" + token.hex(),
            "_from": "0x" + from_addr.hex(), "_to": "0x" + to_addr.hex(),
            "rows": venue_inflow(chain, token, from_addr, to_addr, pb),
        }

    # 8. V3 liquidity events — E12 (main ALM) and E30 (alt-holder)
    POOL = bytes.fromhex("bafead7c60ea473758ed6c6021505e8bbd7e8e5d")
    # E12 — main ALM tokenIds (from Q1 fixture _params)
    E12_TOKENIDS = [0x12327f]
    # E30 — alt-holder tokenIds (enumerated earlier in this session)
    E30_TOKENIDS = [1154814, 1154819, 1155392, 1155442, 1156415]
    for vid, holder, tids in [("e12", GROVE_ALM_ETH, E12_TOKENIDS),
                               ("e30", bytes.fromhex("94b398acb2fce988871218221ea6a4a2b26cccbc"), E30_TOKENIDS)]:
        padded = ", ".join("0x" + format(t, "x").rjust(64, "0") for t in sorted(tids))
        print(f"  fetching v3_liquidity_events_{vid} …")
        df = execute_query(
            QUERIES / "v3_liquidity_events.sql",
            params={"nfpm": bytes.fromhex("c36442b4a4522e871399cd717abdd847ab11fe88"),
                    "token_ids_padded": padded, "from_block": 24136052},
            pin_block=eth_eom,
        )
        fx[f"v3_liquidity_events_{vid}"] = {
            "_chain": "ethereum",
            "_holder": "0x" + holder.hex(),
            "_pool":   "0x" + POOL.hex(),
            "_params": {"nfpm": "0xC36442b4a4522E871399CD717aBDD847Ab11FE88",
                        "token_ids_padded": padded,
                        "from_block": 24136052, "pin_block": eth_eom},
            "rows": _rows(df)}

    # 9. cash_dist_e21 — Galaxy USDC sweeps
    print("  fetching cash_dist_e21 (Galaxy → ALM) …")
    galaxy = bytes.fromhex("aC3D86f9840A8bE07dE5F67d6427983B7009DF1B".lower())
    fx["cash_dist_e21"] = {
        "_chain": "ethereum", "_token": "0x" + USDC.hex(),
        "_from": "0x" + galaxy.hex(), "_to": "0x" + GROVE_ALM_ETH.hex(),
        "rows": venue_inflow("ethereum", USDC, galaxy, GROVE_ALM_ETH, eth_eom)}

    # 10. nav_overrides — reuse Q1 (the entries are for STAC pre-deployment;
    #     same applies to April).
    with open(_REPO / "tests/fixtures/grove_2026_03/dune_outputs.json") as f:
        q1 = json.load(f)
    fx["nav_overrides"] = q1["nav_overrides"]

    # Persist dune_outputs.json
    out_path = OUT / "dune_outputs.json"
    with open(out_path, "w") as f:
        json.dump(fx, f, indent=2)
    print(f"\n  wrote {out_path}")

    # 11. blocks_at_eod per chain
    for chain in ("ethereum", "base", "avalanche_c", "plume", "monad"):
        pb = PIN_BLOCKS_EOM[chain]
        print(f"  fetching blocks_at_eod_{chain} …")
        df = execute_query(
            QUERIES / "blocks_at_eod.sql",
            params={"chain": chain, "start_date": START_DATE},
            pin_block=pb,
        )
        suffix = "" if chain == "ethereum" else f"_{chain.replace('_c', '')}"
        path = OUT / f"blocks_at_eod{suffix}.json"
        with open(path, "w") as f:
            json.dump({"_chain": chain, "rows": _rows(df)}, f, indent=2)
        print(f"    → {path}")

    print("\nAll April fixture data captured.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
