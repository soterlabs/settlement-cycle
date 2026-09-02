"""Capture Grove August-2026 (full month) Dune fixture set.

July's query inventory plus the two Diamond PAU Cat A venues (E40 / E41)
that the July capture omitted — see INFLOW_BY_CP below. Captured
2026-09-01 with the actual August-31 EoM pins (resolved via HyperSync
binary search; re-deriving the July-31 pins the same way reproduced the
published July fixture pins on all five chains, and the Ethereum pin
matches the one obex resolved independently through the Dune/RPC path).
SoM = July 31 EoM. This is the final monthly settlement.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "src"))

from settle.extract.dune import execute_query  # noqa: E402

QUERIES = _REPO / "src" / "settle" / "queries"
OUT = _REPO / "tests" / "fixtures" / "grove_2026_08"
OUT.mkdir(parents=True, exist_ok=True)

# August 2026 (FULL MONTH) pin blocks. SoM = July 31 EoM. EoM = August 31
# 23:59:59 UTC EoD blocks per chain. Resolved 2026-09-01 via HyperSync
# binary search (July-31 values re-derived the same way matched the July
# fixture pins on every chain).
PIN_BLOCKS_SOM = {
    "ethereum": 25656292, "base": 49376526, "avalanche_c": 91716609,
    "plume": 84574746, "monad": 92053501,
}
PIN_BLOCKS_EOM = {
    "ethereum": 25878704, "base": 50715726, "avalanche_c": 94159927,
    "plume": 90704090, "monad": 100893400,
}
START_DATE = "2025-05-14"   # Grove prime start (per config/grove.yaml)
# NOTE: the original value here was "2025-10-23", which is WRONG. See the
# detailed note in grove_2026_04/_capture_dune_fixtures.py — same bug.

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
    out = []
    for _, r in df.iterrows():
        row = {}
        for col in df.columns:
            val = r[col]
            row[col] = val.isoformat() if hasattr(val, "isoformat") else str(val)
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
    print(f"August 2026 (full month) fixture capture (Eth EoM = {eth_eom})")
    fx = {
        "_about": f"Grove August 2026 (full month) fixture, captured via Dune on 2026-09-01; "
                  f"Eth EoM={eth_eom}, start={START_DATE}",
        "pin_block_som_ethereum": PIN_BLOCKS_SOM["ethereum"],
        "pin_block_eom_ethereum": eth_eom,
    }

    print("  fetching debt …")
    df = execute_query(
        QUERIES / "debt_timeseries.sql",
        params={"ilk_bytes32": GROVE_ILK, "start_date": "2025-05-14"},
        pin_block=eth_eom,
    )
    fx["debt"] = {"_query": "debt_timeseries.sql",
                  "_params": {"ilk_bytes32": GROVE_ILK, "start_date": "2025-05-14"},
                  "rows": _rows(df)}

    # ALLOCATOR-GROVE-A (Diamond PAU compartment) — second ilk, summed with
    # BLOOM via config extra_ilks. First draws July 2026 (see grove.yaml).
    print("  fetching debt (ALLOCATOR-GROVE-A / Diamond PAU) …")
    GROVE_A_ILK = "0x414c4c4f4341544f522d47524f56452d41000000000000000000000000000000"
    df = execute_query(
        QUERIES / "debt_timeseries.sql",
        params={"ilk_bytes32": GROVE_A_ILK, "start_date": "2026-07-01"},
        pin_block=eth_eom,
    )
    fx["debt_grove_a"] = {"_query": "debt_timeseries.sql", "_ilk": GROVE_A_ILK,
                          "_about": "Diamond PAU compartment debt — summed with BLOOM via extra_ilks",
                          "rows": _rows(df)}

    print("  fetching ssr …")
    df = execute_query(QUERIES / "ssr_history.sql", params={}, pin_block=eth_eom)
    fx["ssr"] = {"_anchor": "global", "rows": _rows(df)}

    print("  fetching subproxy_usds / subproxy_susds / alm_usds …")
    fx["subproxy_usds"] = {"_holder": GROVE_SUB.hex(), "rows": transfer_ts("ethereum", USDS, GROVE_SUB, eth_eom)}
    fx["subproxy_susds"] = {"_holder": GROVE_SUB.hex(), "_token": "sUSDS",
                            "rows": transfer_ts("ethereum", SUSDS, GROVE_SUB, eth_eom)}
    fx["alm_usds"] = {"_holder": GROVE_ALM_ETH.hex(), "_token": "USDS",
                      "rows": transfer_ts("ethereum", USDS, GROVE_ALM_ETH, eth_eom)}
    fx["psm_usds"] = {"_about": "Grove has no L2 PSM3", "rows": []}

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
        # block instead of bucketing to end-of-day. Mirrors the April
        # capture; without this the loader silently falls back to day-
        # resolution boundaries, regressing E1/E2/E3 precision for May.
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

    VAULTS_ETH = {
        "e4": bytes.fromhex("beeff08df54897e7544ab01d0e86f013da354111"),
        "e5": bytes.fromhex("beef2b5fd3d94469b7782aebe6364e6e6fb1b709"),
        "e6": bytes.fromhex("beeff0d672ab7f5018dfb614c93981045d4aa98a"),
        # E37 Maple syrupUSDC (~$100M since 2026-05) — was missing from the
        # initial June capture (review finding: the loader silently books
        # $0 flows for absent vault sections). Verified no June flows
        # (inflow_by_counterparty_e15 shows no June USDC legs to Maple),
        # so June numbers were correct; kept in the inventory so a July
        # copy can't silently drop real flows.
        "e37": bytes.fromhex("80ac24aa929eaf5013f6436cda2a7ba190f5cc0b"),
    }
    for vid, vt in VAULTS_ETH.items():
        print(f"  fetching vault_{vid}_mints / burns …")
        fx[f"vault_{vid}_mints"] = {"_token": "0x" + vt.hex(),
            "rows": venue_inflow("ethereum", vt, ZERO_ADDR, GROVE_ALM_ETH, eth_eom)}
        fx[f"vault_{vid}_burns"] = {"_token": "0x" + vt.hex(),
            "rows": venue_inflow("ethereum", vt, GROVE_ALM_ETH, ZERO_ADDR, eth_eom)}
    # E37 share_burn_destinations legs (both directions per destination,
    # positionally keyed to config/grove.yaml's list). Destination [2] is
    # SPARK's Eth ALM: on 2026-07-20 (block 25574524, right after the
    # MSC#10 settlement tx) the whole 85,943,747.637271-share syrupUSDC
    # position Transferred Grove ALM → Spark ALM. Without this capture the
    # Cat B classifier books the disappearance as a −$100.67M phantom loss.
    E37_TOKEN = VAULTS_ETH["e37"]
    E37_QUEUE_DESTS = [
        bytes.fromhex("7ad5ffa5fdf509e30186f4609c2f6269f4b6158f"),  # Maple redemption escrow
        bytes.fromhex("1bc47a0dd0fdab96e9ef982fdf1f34dc6207cfe3"),  # WithdrawalManagerQueue
        bytes.fromhex("1601843c5e9bc251a3272907010afa41fa18347e"),  # Spark Eth ALM (inter-prime)
    ]
    for i, dest in enumerate(E37_QUEUE_DESTS):
        print(f"  fetching vault_e37_queue{i}_out / _in …")
        fx[f"vault_e37_queue{i}_out"] = {
            "_token": "0x" + E37_TOKEN.hex(), "_to": "0x" + dest.hex(),
            "rows": venue_inflow("ethereum", E37_TOKEN, GROVE_ALM_ETH, dest, eth_eom)}
        fx[f"vault_e37_queue{i}_in"] = {
            "_token": "0x" + E37_TOKEN.hex(), "_from": "0x" + dest.hex(),
            "rows": venue_inflow("ethereum", E37_TOKEN, dest, GROVE_ALM_ETH, eth_eom)}

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

    CUM_VENUES = [
        ("e7",  "ethereum", "51c2d74017390cbbd30550179a16a1c28f7210fc"),
        ("e8",  "ethereum", "5a0f93d040de44e78f251b03c43be9cf317dcf64"),
        ("e9",  "ethereum", "8c213ee79581ff4984583c6a801e5263418c4b86"),
        ("e10", "ethereum", "6a9da2d710bb9b700acde7cb81f10f1ff8c89041"),
        ("e13", "ethereum", "8292bb45bf1ee4d140127049757c2e0ff06317ed"),
        ("e14", "ethereum", "00000000efe302beaa2b3e6e1b18d08d69a9012a"),
        ("e15", "ethereum", "a0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"),
        ("e16", "ethereum", "6b175474e89094c44da98b954eedeac495271d0f"),
        ("e17", "ethereum", "dc035d45d973e3ec169d2276ddab16f1e407384f"),
        ("e18", "ethereum", "a3931d71877c0e7a3148cb7eb4463524fec27fbd"),
        ("e20", "avalanche_c", "58F93d6b1EF2F44eC379Cb975657C132CBeD3B6b"),
        ("e21", "avalanche_c", "2C0aDFF8e114f3cA106051144353aC703D24B901"),
        ("e22", "plume",       "9477724Bb54AD5417de8Baff29e59DF3fB4DA74f"),
    ]
    CHAIN_TO_ALM = {
        "ethereum": GROVE_ALM_ETH, "base": GROVE_ALM_BASE,
        "avalanche_c": GROVE_ALM_AVAX, "plume": GROVE_ALM_PLUME,
    }
    BUIDL_MIN_TRANSFER = 1_000_000
    for vid, chain, token_hex in CUM_VENUES:
        token = bytes.fromhex(token_hex.lower())
        holder = CHAIN_TO_ALM[chain]
        min_tx = BUIDL_MIN_TRANSFER if vid == "e10" else 0
        pb = PIN_BLOCKS_EOM[chain]
        print(f"  fetching cum_balance_{vid} ({chain}) …")
        fx[f"cum_balance_{vid}"] = {
            "_chain": chain, "_token": "0x" + token_hex.lower(),
            "rows": transfer_ts(chain, token, holder, pb, min_tx)}
        # BUIDL: second unfiltered capture for the SDE asset-value path —
        # see grove_2026_04/_capture_dune_fixtures.py for the rationale.
        if vid == "e10":
            print(f"  fetching cum_balance_{vid}_raw ({chain}, unfiltered) …")
            fx[f"cum_balance_{vid}_raw"] = {
                "_chain": chain, "_token": "0x" + token_hex.lower(),
                "_note": "unfiltered for SDE asset-value; min_transfer=0",
                "rows": transfer_ts(chain, token, holder, pb, 0)}

    # inflow_by_counterparty_eXX — Cat A par-stable counterparty-attributed
    # transfer log. Required for the Cat A classifier to distinguish
    # principal-preserving moves from off-chain yield. Without these, balance
    # changes at the ALM get misclassified as actual_revenue (E14 April $1M
    # phantom was the symptom; same risk applies on May).
    AUSD = bytes.fromhex("00000000efe302beaa2b3e6e1b18d08d69a9012a")
    RLUSD_ETH = bytes.fromhex("8292bb45bf1ee4d140127049757c2e0ff06317ed")
    DAI_ETH   = bytes.fromhex("6b175474e89094c44da98b954eedeac495271d0f")
    PYUSD_ETH = bytes.fromhex("6c3ea9036406852006290770bedfcaba0e23a0e8")
    USDC_BASE = bytes.fromhex("833589fcd6edb6e08f4c7c32d4f71b54bda02913")
    USDC_MONAD = bytes.fromhex("754704bc059f8c67012fed69bc8a327a5aafb603")
    ALT_HOLDER = bytes.fromhex("94b398acb2fce988871218221ea6a4a2b26cccbc")
    # holder_override values for E40 / E41 (config/grove.yaml).
    PAU_ALM_ETH = bytes.fromhex("0dcd9298e163dfd3c0b5b00f0d9093c36e40a153")
    JTRSY_BASIN_ESCROW = bytes.fromhex("2cd296095788a2741e72056d66b3ae1faee23ea2")
    INFLOW_BY_CP: list[tuple[str, str, bytes, bytes, int]] = [
        # (vid, chain, token, holder, pin_block)
        # E13 RLUSD raw at the ALM — added after the May/June ±$49,596
        # phantom: $49,596 of RLUSD transited the ALM (in 2026-05-07, out
        # 2026-06-01) and, with no counterparty log, the Cat A classifier
        # booked the balance delta as ±yield instead of capital.
        ("e13", "ethereum", RLUSD_ETH, GROVE_ALM_ETH, eth_eom),
        ("e14", "ethereum", AUSD, GROVE_ALM_ETH, eth_eom),
        # Remaining Cat A venues — added after the coverage sweep that
        # followed the E13 fix: every Cat A venue needs its counterparty
        # log or balance transits book as ±yield. E32 already showed a
        # live (small, ~$5.8K Mar/Apr) instance; the rest were all-zero
        # through June and are captured as forward protection.
        ("e16", "ethereum", DAI_ETH, GROVE_ALM_ETH, eth_eom),
        ("e17", "ethereum", USDS, GROVE_ALM_ETH, eth_eom),
        ("e26", "ethereum", PYUSD_ETH, GROVE_ALM_ETH, eth_eom),
        ("e27", "base", USDC_BASE, GROVE_ALM_BASE, PIN_BLOCKS_EOM["base"]),
        ("e32", "ethereum", USDC, ALT_HOLDER, eth_eom),
        ("e15", "ethereum", USDC, GROVE_ALM_ETH, eth_eom),
        ("e31", "ethereum", AUSD, ALT_HOLDER, eth_eom),
        ("e34", "monad",    AUSD, ALT_HOLDER, PIN_BLOCKS_EOM["monad"]),
        ("e35", "monad",    USDC_MONAD, ALT_HOLDER, PIN_BLOCKS_EOM["monad"]),
        # E40 / E41 — the Diamond PAU Cat A venues added in the July cycle,
        # both MISSING from the July capture. E41 (the JTRSY Basin
        # subscription escrow) went 1,000,000 → 12,500,000 USDS across
        # August: an +11.5M capital injection. Unattributed Cat A INflows
        # fail closed to capital (July booked E41's +999,999 delta at $0
        # revenue), so July's numbers stand — but an out-leg with no
        # counterparty log books as NEGATIVE yield, and the escrow is
        # expected to drain into JTRSY tokens once the subscription
        # settles (see the E41 note in config/grove.yaml). Capture both so
        # that month can't phantom.
        ("e40", "ethereum", USDS, PAU_ALM_ETH, eth_eom),
        ("e41", "ethereum", USDS, JTRSY_BASIN_ESCROW, eth_eom),
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

    # eoa_outflow_e36 — paired_principal_cap for E14. Without this, the cap
    # is $0 and inflows from E14's paired_source get fully classified as
    # realized revenue (the same April $6.5M phantom this branch fixed).
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

    POOL = bytes.fromhex("bafead7c60ea473758ed6c6021505e8bbd7e8e5d")
    # Token IDs are DISCOVERED, never hardcoded. The previous months listed
    # them by hand, and the list went stale the moment Grove opened a new
    # position: the value path enumerates NFPM positions live, so NFT 1353600
    # (minted August 2026) was priced into value_eom while its
    # IncreaseLiquidity event was absent from this fixture — and
    # ``revenue = eom - som - inflow`` reported $4,000,095.77 of fresh capital
    # as E12 revenue, 46.8% of Grove's August gross. Discovering at BOTH pins
    # also catches positions closed during the month, which an EoM-only scan
    # would miss. Shared with the live DuneV3InflowSource so both paths agree.
    from settle.extract.uniswap_v3 import discover_pool_token_ids  # noqa: E402
    from settle.domain.primes import Address as _Addr, Chain as _Chain  # noqa: E402
    _NFPM = _Addr.from_str("0xC36442b4a4522E871399CD717aBDD847Ab11FE88")
    for vid, holder in [("e12", GROVE_ALM_ETH),
                        ("e30", bytes.fromhex("94b398acb2fce988871218221ea6a4a2b26cccbc"))]:
        tids = sorted(discover_pool_token_ids(
            _Chain.ETHEREUM, _NFPM, _Addr(holder), _Addr(POOL),
            (PIN_BLOCKS_SOM["ethereum"], eth_eom),
        ))
        print(f"    {vid}: discovered token_ids={tids}")
        if not tids:
            # An empty list renders `topic1 IN ()` in v3_liquidity_events.sql
            # and Dune fails with an opaque parse error. Say what actually
            # happened instead — either the holder genuinely held nothing in
            # this pool at both pins (write the empty section and move on), or
            # the POOL/holder pair is wrong (note E30 deliberately reuses E12's
            # POOL constant, so a typo there is easy to miss).
            print(f"      !! no positions at either pin — writing empty {vid} section")
            fx[f"v3_liquidity_events_{vid}"] = {
                "_chain": "ethereum", "_holder": "0x" + holder.hex(),
                "_pool": "0x" + POOL.hex(),
                "_about": "no NFPM positions in this pool at either pin block",
                "rows": [],
            }
            continue
        padded = ", ".join("0x" + format(t, "x").rjust(64, "0") for t in sorted(tids))
        print(f"  fetching v3_liquidity_events_{vid} …")
        df = execute_query(
            QUERIES / "v3_liquidity_events.sql",
            params={"nfpm": bytes.fromhex("c36442b4a4522e871399cd717abdd847ab11fe88"),
                    "token_ids_padded": padded, "from_block": 24136052},
            pin_block=eth_eom,
        )
        fx[f"v3_liquidity_events_{vid}"] = {
            "_chain": "ethereum", "_holder": "0x" + holder.hex(),
            "_pool":   "0x" + POOL.hex(),
            "_params": {"nfpm": "0xC36442b4a4522E871399CD717aBDD847Ab11FE88",
                        "token_ids_padded": padded,
                        "from_block": 24136052, "pin_block": eth_eom},
            "rows": _rows(df)}

    # E42 Galaxy Warehouse — the second, larger Galaxy facility (confirmed by
    # the Grove team 2026-09). Single payer, funded from the same
    # 0x15abb66ba754… circuit the July principal was wired through.
    print("  fetching cash_dist_e42 (Galaxy Warehouse → ALM) …")
    gw_payer = bytes.fromhex("ba79473abba448c1a2912d3cdc241b18ee83e82c")
    fx["cash_dist_e42"] = {
        "_chain": "ethereum", "_token": "0x" + USDC.hex(),
        "_from": "0x" + gw_payer.hex(), "_to": "0x" + GROVE_ALM_ETH.hex(),
        "rows": venue_inflow("ethereum", USDC, gw_payer, GROVE_ALM_ETH, eth_eom)}

    print("  fetching cash_dist_e21 (Galaxy → ALM) …")
    galaxy = bytes.fromhex("aC3D86f9840A8bE07dE5F67d6427983B7009DF1B".lower())
    fx["cash_dist_e21"] = {
        "_chain": "ethereum", "_token": "0x" + USDC.hex(),
        "_from": "0x" + galaxy.hex(), "_to": "0x" + GROVE_ALM_ETH.hex(),
        "rows": venue_inflow("ethereum", USDC, galaxy, GROVE_ALM_ETH, eth_eom)}

    # E38 Agora AUSD incentives — two payers (config/grove.yaml
    # cash_distributions order = p0, p1). Missing from the initial June
    # capture; verified no June payments (last: 2026-05-29, $398,324.92),
    # so June numbers were correct. Kept so a July copy can't silently
    # drop a payment (~monthly cadence Feb–May).
    for i, payer_hex in enumerate((
        "4a4593c5d963473a95f0762bd6df4571542af651",
        "df27ac19cb1da767e181748aaa54e1535aaa3a1d",
    )):
        payer = bytes.fromhex(payer_hex)
        print(f"  fetching cash_dist_e38_p{i} (Agora → ALM) …")
        fx[f"cash_dist_e38_p{i}"] = {
            "_chain": "ethereum", "_token": "0x" + AUSD.hex(),
            "_from": "0x" + payer.hex(), "_to": "0x" + GROVE_ALM_ETH.hex(),
            "rows": venue_inflow("ethereum", AUSD, payer, GROVE_ALM_ETH, eth_eom)}

    with open(_REPO / "tests/fixtures/grove_2026_07/dune_outputs.json") as f:
        prev = json.load(f)
    fx["nav_overrides"] = prev["nav_overrides"]

    out_path = OUT / "dune_outputs.json"
    with open(out_path, "w") as f:
        json.dump(fx, f, indent=2)
    print(f"\n  wrote {out_path}")

    for chain in ("ethereum", "base", "avalanche_c", "plume", "monad"):
        pb = PIN_BLOCKS_EOM[chain]
        print(f"  fetching blocks_at_eod_{chain} …")
        df = execute_query(
            QUERIES / "blocks_at_eod.sql",
            params={"chain": chain, "start_date": START_DATE, "end_date": "2026-08-31"},
            pin_block=pb,
        )
        suffix = "" if chain == "ethereum" else f"_{chain.replace('_c', '')}"
        path = OUT / f"blocks_at_eod{suffix}.json"
        with open(path, "w") as f:
            json.dump({"_chain": chain, "rows": _rows(df)}, f, indent=2)
        print(f"    → {path}")

    print("\nAugust (full month) fixture data captured.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
