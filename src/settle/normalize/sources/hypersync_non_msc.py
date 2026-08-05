"""HyperSync-direct extractor for the non-MSC Sky P&L — raw-log parity with
``queries/non_msc_streams.sql``.

Produces the identical ``(stream, label, event_date, amount)`` row set the Dune
query emits, straight from Envio HyperSync raw logs — no decoded tables, no
HyperIndex. It is the side-by-side twin of the Dune source (same idiom as
``HyperSyncDebtSource`` vs ``DuneDebtSource``): run both, diff, retire Dune only
once they agree (``scripts/compare_non_msc_sources.py``). Every line was
validated to the dollar against the 2026-07-16 methodology's June figures.

Why raw logs (not HyperIndex)? The Maker Vat/Jug record state changes via a
``note`` modifier that emits an anonymous 4-topic ``LogNote`` — undecodable by
HyperIndex (see ``hypersync_debt``). We match the raw fn-selector topic0 and
decode the payload ourselves. Two distinct note layouts are in play:

  * Vat custom note  — topics = [sig, arg1, arg2, arg3]; the rest of the
    calldata is in ``data``. (frob/grab dart at payload byte 164; fold rate,
    suck rad, move rad are arg3 = topic3.)
  * Jug/DS-Note      — topics = [sig, caller, arg1, arg2]; ALL args are in
    ``data``. (jug.file duty = arg3, payload byte 68.)

Real (non-anonymous) events — Dog.Bark, Clipper.Take/Kick/Redo, DssVest.Vest,
ERC20.Transfer, sUSDS/stUSDS.Drip — are matched by their signature topic0 and
decoded positionally.

Accounting basis is identical to the SQL (see that file's header): stability
fees on the accrual basis (Art × Δr_true from ``duty``); PSM at the jar burn's
landing month; liquidation revenue = Σ take.owe − Σ bark.due; surplus returns =
join→vow moves not attributable to the PSM/RWA jar; savings interest on the
accrual basis (each drip apportioned to the month by chi-boundary
interpolation); vest gross at call time.

Config (env):
    ENVIO_API_TOKEN   required — free token from https://app.envio.dev/api-tokens
    DATABASE_URL      optional — enables the reorg-safe log store (cached,
                      incremental genesis scans); without it, every run
                      re-fetches live (correct, just slower).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time, timezone
from decimal import Decimal, localcontext
from typing import Any

import pandas as pd
import requests

from ...extract import hypersync
from ...extract import hypersync_store
from ._hypersync_common import _addr_topic, _evt, _row, _sel, _word
from .hypersync_debt import _decode_dart

__all__ = ["HyperSyncNonMscSource"]

_CHAIN = "ethereum"
_WAD = Decimal(10) ** 18
_RAD = Decimal(10) ** 45
_RAY = 10 ** 27
# Seconds of slack each side of the month when fetching savings drips, so the
# two drip intervals straddling the month boundaries are captured. sUSDS/stUSDS
# drip ~hourly-or-faster and the pot many times a day, so 3 days is ample.
_SAVINGS_BUFFER = 3 * 86400

# ── addresses (lower-case) ──────────────────────────────────────────────────
_VAT = "0x35d1b3f3d7966a1dfe207aa4514c12a259a0492b"
_JUG = "0x19c0976f590d67707e62397c87829d896dc0f1f1"
_DOG = "0x135954d155898d42c90d2a57824c690e0c7bef1b"
_VOW = "0xa950524441892a31ebddf91d3ceefa04bf454466"
_POT = "0x197e90f9fad81970ba7976f33cbd77088e5d7cf7"
_SUSDS = "0xa3931d71877c0e7a3148cb7eb4463524fec27fbd"
_STUSDS = "0x99cd4ec3f88a45940936f469e4bb72a2a701eeb9"
_LITE_PSM_JAR = "0x69ca348bd928a158ade7aa193c133f315803b06e"
_DAI = "0x6b175474e89094c44da98b954eedeac495271d0f"
_USDS = "0xdc035d45d973e3ec169d2276ddab16f1e407384f"
_DAI_JOIN = "0x9759a6ac90977b93b58547b4a71c78317f391a28"
_USDS_JOIN = "0x3c0f895007ca717aa01c8693e59df1e8c3777feb"
_ZERO = "0x0000000000000000000000000000000000000000"
_VEST_CONTRACTS = (
    "0xa4c22f0e25c6630b2017979acf1f865e94695c4b",   # MCD_VEST_DAI
    "0xc447a9745ade9a44bb9e37b7f6c92f9582544110",   # MCD_VEST_USDS
    "0x2cc583c0aacdac9e23cb601fda8f1a0c56cdcb71",   # MCD_VEST_DAI_LEGACY
)
_RWA_JARS = (
    "0xef1b095f700be471981aae025f92b03091c3ad47",   # RWA007_A_JAR
    "0x6c6d4be2223b5d202263515351034861dd9afdb6",   # RWA009_A_JAR (H.V.Bank)
    "0x71ec6d5ee95b12062139311ca1fe8fd698cbe0cf",   # RWA014_A_JAR
    "0xc27c3d3130563c1171fecc4f76c217db603997cf",   # RWA015_A_JAR
)
_EXCLUDE_ILK_PREFIXES = ("ALLOCATOR-", "DIRECT-", "PSM-", "TELEPORT")


# Vat custom-note selectors (topics = sig, arg1, arg2, arg3).
_FROB = "0x7608870300000000000000000000000000000000000000000000000000000000"
_GRAB = "0x7bab3f4000000000000000000000000000000000000000000000000000000000"
_FOLD = _sel("fold(bytes32,address,int256)")
_INIT = _sel("init(bytes32)")
_SUCK = _sel("suck(address,address,uint256)")
_MOVE = _sel("move(address,address,uint256)")
# Jug DS-note (topics = sig, caller, arg1=ilk, arg2=what); value in data.
_FILE3 = _sel("file(bytes32,bytes32,uint256)")
# Real events.
_BARK = _evt("Bark(bytes32,address,uint256,uint256,uint256,address,uint256)")
_TAKE = _evt("Take(uint256,uint256,uint256,uint256,uint256,uint256,address)")
_KICK = _evt("Kick(uint256,uint256,uint256,uint256,address,address,uint256)")
_REDO = _evt("Redo(uint256,uint256,uint256,uint256,address,address,uint256)")
_VEST = _evt("Vest(uint256,uint256)")
_DRIP = _evt("Drip(uint256,uint256)")
_TRANSFER = _evt("Transfer(address,address,uint256)")

# Self-check: the frob/grab constants must agree with keccak of the signatures.
assert _sel("frob(bytes32,address,address,address,int256,int256)") == _FROB
assert _sel("grab(bytes32,address,address,address,int256,int256)") == _GRAB

_DUTY_WHAT = "0x" + b"duty".hex() + "00" * 28   # bytes32 "duty"


def _ilk_topic(ilk: str) -> str:
    b = ilk.encode()
    return "0x" + b.hex() + "00" * (32 - len(b))


def _ilk_from_topic(topic: str) -> str:
    return bytes.fromhex(topic[2:]).rstrip(b"\0").decode("latin-1")


def _sint(topic: str) -> int:
    v = int(topic, 16)
    return v - (1 << 256) if v >= (1 << 255) else v


def _note_calldata_word(data_hex: str, byte_off: int) -> int:
    """Read a 32-byte word at calldata byte ``byte_off`` from a note ``data``
    payload ([0x20 offset][length][calldata...])."""
    raw = data_hex[2:] if data_hex.startswith("0x") else data_hex
    start = 128 + byte_off * 2
    return int(raw[start : start + 64] or "0", 16)


class HyperSyncNonMscSource:
    """Emits the non-MSC stream rows from HyperSync raw logs.

    ``streams(month, pin_block)`` returns a DataFrame with the exact columns the
    Dune query yields — ``[stream, label, event_date, amount]`` — so the compute
    layer can't tell which backend produced it. Injectable ``post`` for tests.
    """

    def __init__(self, post: Callable[..., Any] = requests.post) -> None:
        self._post = post

    # -- public --------------------------------------------------------------

    def streams(self, month: Any, pin_block: int) -> pd.DataFrame:
        start = date(month.year, month.month, 1)
        end_excl = (
            date(month.year + 1, 1, 1)
            if month.month == 12
            else date(month.year, month.month + 1, 1)
        )
        start_ts = int(datetime.combine(start, time.min, tzinfo=timezone.utc).timestamp())
        end_ts = int(datetime.combine(end_excl, time.min, tzinfo=timezone.utc).timestamp())
        # In-month block window (logs are re-filtered by exact block_time so the
        # fuzzy endpoints don't leak an out-of-month event).
        fb = hypersync.find_block_at_or_before(_CHAIN, start_ts)
        tb = min(pin_block, hypersync.find_block_at_or_before(_CHAIN, end_ts))

        rows: list[dict[str, Any]] = []
        rows += self._psm_jar(start_ts, end_ts, fb, tb)
        rows += self._stability_fees(start_ts, end_ts, pin_block)
        rows += self._liquidations(start_ts, end_ts, fb, tb, pin_block)
        rows += self._surplus_and_rwa(start_ts, end_ts, fb, tb)
        rows += self._vest(start_ts, end_ts, fb, tb)
        rows += self._bad_debt_writeoffs(start_ts, end_ts, fb, tb)
        rows += self._savings(start_ts, end_ts)
        return pd.DataFrame(rows, columns=["stream", "label", "event_date", "amount"])

    # -- income: PSM jar burns ----------------------------------------------

    def _psm_jar(self, start_ts, end_ts, fb, tb) -> list[dict]:
        sel = [{
            "address": [_DAI, _USDS],
            "topics": [[_TRANSFER], [_addr_topic(_LITE_PSM_JAR)], [_addr_topic(_ZERO)]],
        }]
        out: list[dict] = []
        for r in hypersync.query_logs(_CHAIN, sel, fb, tb, post=self._post).rows:
            if start_ts <= r.block_time < end_ts:
                d = datetime.fromtimestamp(r.block_time, tz=timezone.utc).date()
                amt = Decimal(_word(r.data, 0)) / _WAD
                out.append(_row("income:psm_jar", str(d), amt, event_date=d))
        return out

    # -- income: stability fees (accrual) -----------------------------------

    def _stability_fees(self, start_ts, end_ts, pin_block) -> list[dict]:
        out: list[dict] = []
        for ilk in self._fee_ilks(pin_block):
            amt = self._accrued_fee(ilk, start_ts, end_ts, pin_block)
            if amt is not None and abs(amt) >= Decimal("0.01"):
                out.append(_row("income:stability_fee", ilk, amt))
        return out

    def _fee_ilks(self, pin_block: int) -> list[str]:
        """Full ilk universe (every Vat.init) minus prime/defunct prefixes."""
        rows = hypersync_store.fetch_logs(
            _CHAIN, [{"address": [_VAT], "topics": [[_INIT]]}], 0, pin_block, post=self._post
        )
        ilks: set[str] = set()
        for r in rows:
            ilk = _ilk_from_topic(r.topic1) if r.topic1 else ""
            if ilk and not any(ilk.startswith(p) for p in _EXCLUDE_ILK_PREFIXES):
                ilks.add(ilk)
        return sorted(ilks)

    def _accrued_fee(self, ilk, start_ts, end_ts, pin_block) -> Decimal | None:
        it = _ilk_topic(ilk)
        arts = [
            (r.block_number, r.log_index, r.block_time, _decode_dart(r.data))
            for r in hypersync_store.fetch_logs(
                _CHAIN, [{"address": [_VAT], "topics": [[_FROB, _GRAB], [it]]}],
                0, pin_block, post=self._post)
        ]
        folds = [
            (r.block_number, r.log_index, r.block_time, _sint(r.topic3))
            for r in hypersync_store.fetch_logs(
                _CHAIN, [{"address": [_VAT], "topics": [[_FOLD], [it]]}],
                0, pin_block, post=self._post)
        ]
        duties = [
            (r.block_number, r.log_index, r.block_time, _note_calldata_word(r.data, 68))
            for r in hypersync_store.fetch_logs(
                _CHAIN, [{"address": [_JUG], "topics": [[_FILE3], [], [it], [_DUTY_WHAT]]}],
                0, pin_block, post=self._post)
        ]
        return _integrate_fee(arts, folds, duties, start_ts, end_ts)

    # -- income + expense: liquidations -------------------------------------

    def _liquidations(self, start_ts, end_ts, fb, tb, pin_block) -> list[dict]:
        # Clipper instances are discovered from the Dog's Bark `clip` field
        # (data word[3]) so takes on auctions barked in a prior month count.
        clippers: set[str] = set()
        due = Decimal(0)
        for r in hypersync_store.fetch_logs(
            _CHAIN, [{"address": [_DOG], "topics": [[_BARK]]}], 0, pin_block, post=self._post
        ):
            clip = "0x" + r.data[2:][3 * 64 + 24 : 3 * 64 + 64]   # word[3], last 20 bytes
            clippers.add(clip.lower())
            if start_ts <= r.block_time < end_ts:
                due += Decimal(_word(r.data, 2)) / _RAD                 # Bark due = word[2]
        clip_list = sorted(clippers)

        owe = Decimal(0)
        for r in hypersync.query_logs(
            _CHAIN, [{"address": clip_list, "topics": [[_TAKE]]}], fb, tb, post=self._post
        ).rows:
            if start_ts <= r.block_time < end_ts:
                owe += Decimal(_word(r.data, 2)) / _RAD                 # Take owe = word[2]

        coin = Decimal(0)
        for r in hypersync.query_logs(
            _CHAIN, [{"address": clip_list, "topics": [[_KICK, _REDO]]}], fb, tb, post=self._post
        ).rows:
            if start_ts <= r.block_time < end_ts:
                coin += Decimal(_word(r.data, 3)) / _RAD                # Kick/Redo coin = word[3]

        return [
            _row("income:liq_owe", "liquidation owe (takes)", owe),
            _row("income:liq_due", "liquidation due (barks)", due),
            _row("expense:liq_coin", "keeper incentives (kicks + redos)", coin),
        ]

    # -- income: surplus returns + RWA jar voids (join → vow) ----------------

    def _surplus_and_rwa(self, start_ts, end_ts, fb, tb) -> list[dict]:
        # Vat.move(src ∈ {daiJoin, usdsJoin}, dst = vow); rad = topic3 (arg3).
        # tx-level classification: PSM jar burn tx → PSM (already booked);
        # RWA jar transfer tx → RWA void; otherwise Surplus Return.
        lf = hypersync._DEFAULT_LOG_FIELDS + ["transaction_hash"]
        moves = [
            r for r in hypersync.query_logs(
                _CHAIN,
                [{"address": [_VAT], "topics": [
                    [_MOVE], [_addr_topic(_DAI_JOIN), _addr_topic(_USDS_JOIN)], [_addr_topic(_VOW)]]}],
                fb, tb, log_fields=lf, post=self._post).rows
            if start_ts <= r.block_time < end_ts
        ]
        psm_txs = self._burn_txs([_LITE_PSM_JAR], start_ts, end_ts, fb, tb)
        rwa_txs = self._burn_txs(list(_RWA_JARS), start_ts, end_ts, fb, tb)

        out: list[dict] = []
        rwa_void = Decimal(0)
        for r in moves:
            amt = Decimal(int(r.topic3, 16)) / _RAD
            tx = r.transaction_hash
            if tx in psm_txs:
                continue                        # PSM jar → PSM line (income:psm_jar)
            if tx in rwa_txs:
                rwa_void += amt                 # RWA jar → RWA void line
            else:
                d = datetime.fromtimestamp(r.block_time, tz=timezone.utc).date()
                out.append(_row("income:surplus_return", str(d), amt, event_date=d))
        out.append(_row("income:rwa_void", "RWA jars (void)", rwa_void))
        return out

    def _burn_txs(self, senders, start_ts, end_ts, fb, tb) -> set[str]:
        """Tx hashes in which any ``senders`` address moved DAI/USDS in-month."""
        lf = hypersync._DEFAULT_LOG_FIELDS + ["transaction_hash"]
        sel = [{
            "address": [_DAI, _USDS],
            "topics": [[_TRANSFER], [_addr_topic(a) for a in senders]],
        }]
        return {
            r.transaction_hash
            for r in hypersync.query_logs(_CHAIN, sel, fb, tb, log_fields=lf, post=self._post).rows
            if start_ts <= r.block_time < end_ts
        }

    # -- expense: vest -------------------------------------------------------

    def _vest(self, start_ts, end_ts, fb, tb) -> list[dict]:
        total = Decimal(0)
        for r in hypersync.query_logs(
            _CHAIN, [{"address": list(_VEST_CONTRACTS), "topics": [[_VEST]]}],
            fb, tb, post=self._post
        ).rows:
            if start_ts <= r.block_time < end_ts:
                total += Decimal(_word(r.data, 0)) / _WAD               # amt = data word[0]
        return [_row("expense:vest", "vest (gross suckable)", total)]

    # -- bad-debt write-offs (vat.grab on legacy ilks) ----------------------

    def _bad_debt_writeoffs(
        self, start_ts: int, end_ts: int, fb: int, tb: int
    ) -> list[dict[str, Any]]:
        """Protocol bad debt realized against the surplus buffer via
        ``vat.grab`` with negative ``dart`` on NON-allocator ilks — legacy
        vault offboardings (first case: RWA001-A's ``cull()`` on 2026-07-20,
        block 25574490, writing off 3,019,173.48 DAI; forum t/27706).

        Expense = |dart| × ilk rate at the grab block (normalised Art →
        actual DAI/USDS). Exclusions:
          * ``_EXCLUDE_ILK_PREFIXES`` ilks — allocator grabs are MSC
            settlement mechanics, not non-MSC P&L;
          * grabs in transactions that also emit a ``Dog.Bark`` — those are
            Liquidations-2.0 kicks whose debt is already accounted by the
            ``liq_owe − liq_due`` netting; counting the grab too would
            double-book every barked auction.
        """
        from ...domain.primes import Address as _Addr, Chain as _ChainEnum
        from ...extract.rpc import ilk_rate as _ilk_rate

        bark_txs = {
            r.transaction_hash
            for r in hypersync.query_logs(
                _CHAIN, [{"address": [_DOG], "topics": [[_BARK]]}], fb, tb,
                log_fields=["block_number", "log_index", "transaction_hash",
                            "topic0", "data"],
                post=self._post,
            ).rows
            if r.transaction_hash is not None
            and start_ts <= r.block_time < end_ts
        }
        out: list[dict[str, Any]] = []
        for r in hypersync.query_logs(
            _CHAIN, [{"address": [_VAT], "topics": [[_GRAB]]}], fb, tb,
            log_fields=["block_number", "log_index", "transaction_hash",
                        "topic0", "topic1", "data"],
            post=self._post,
        ).rows:
            if not (start_ts <= r.block_time < end_ts):
                continue
            if r.transaction_hash in bark_txs:
                continue
            if r.topic1 is None:
                continue   # malformed log — grab always carries the ilk topic
            ilk_bytes = bytes.fromhex(r.topic1[2:])
            ilk_name = ilk_bytes.rstrip(b"\x00").decode(errors="replace")
            if ilk_name.startswith(_EXCLUDE_ILK_PREFIXES):
                continue
            dart = _decode_dart(r.data)
            if dart >= 0:
                continue
            rate_ray = _ilk_rate(
                _ChainEnum.ETHEREUM, _Addr.from_str(_VAT), ilk_bytes,
                r.block_number,
            )
            with localcontext() as ctx:
                ctx.prec = 60
                amt = Decimal(-dart) * Decimal(rate_ray) / Decimal(10) ** 45
            d = datetime.fromtimestamp(r.block_time, tz=timezone.utc).date()
            out.append(_row(
                "expense:bad_debt",
                f"{ilk_name} write-off (vat.grab)",
                amt, event_date=d,
            ))
        return out

    # -- expense: savings interest (ACCRUAL, chi-boundary interpolation) ------

    def _savings(self, start_ts, end_ts) -> list[dict]:
        # Methodology §2: each drip's minted interest is apportioned to the month
        # by interpolating the accumulator at the month boundaries — the
        # drip-contract analog of r_true. A drip's `diff` covers the interval
        # since the previous drip; the two intervals straddling month_start /
        # month_end are split (geometric in chi for sUSDS/stUSDS, which carry chi
        # in the Drip event; by time-fraction for the pot suck, which does not —
        # exact to the cent since the pot drips many times a day), everything
        # in-between is booked whole. The end-boundary interval is closed by the
        # FIRST drip after month_end (it realizes the month's accrued-but-not-yet
        # -dripped tail) — that drip lands just past pin_block (which is
        # month-end), so the savings window deliberately extends to end_ts +
        # buffer rather than capping at pin_block. For a finalized month those
        # blocks are fully mined and deterministic; an in-progress month simply
        # has no such drip yet (its last-day tail is not final regardless).
        wb = hypersync.find_block_at_or_before(_CHAIN, start_ts - _SAVINGS_BUFFER)
        we = hypersync.find_block_at_or_before(_CHAIN, end_ts + _SAVINGS_BUFFER)

        def drip_events(addr: str) -> list[tuple[int, int, int]]:
            rows = sorted(
                hypersync.query_logs(
                    _CHAIN, [{"address": [addr], "topics": [[_DRIP]]}], wb, we, post=self._post
                ).rows,
                key=lambda r: (r.block_number, r.log_index),
            )
            # (block_time, chi = data word[0], diff = data word[1])
            return [(r.block_time, _word(r.data, 0), _word(r.data, 1)) for r in rows]

        susds = _accrue_savings(drip_events(_SUSDS), start_ts, end_ts) / _WAD
        stusds = _accrue_savings(drip_events(_STUSDS), start_ts, end_ts) / _WAD

        # DSR — Vat.suck(u=vow, v=pot, rad); v = arg2 = topic2, rad = topic3. The
        # pot suck carries no chi, so acc=None → time-fraction split.
        sk = sorted(
            hypersync.query_logs(
                _CHAIN, [{"address": [_VAT], "topics": [[_SUCK], [], [_addr_topic(_POT)]]}],
                wb, we, post=self._post
            ).rows,
            key=lambda r: (r.block_number, r.log_index),
        )
        dsr = _accrue_savings([(r.block_time, None, int(r.topic3, 16)) for r in sk],
                              start_ts, end_ts) / _RAD
        return [
            _row("expense:susds_drip", "sUSDS SSR (gross, all holders)", susds),
            _row("expense:stusds_drip", "stUSDS", stusds),
            _row("expense:dsr_drip", "DSR (pot)", dsr),
        ]


def _integrate_fee(
    arts: list[tuple[int, int, int, int]],
    folds: list[tuple[int, int, int, int]],
    duties: list[tuple[int, int, int, int]],
    start_ts: int,
    end_ts: int,
) -> Decimal | None:
    """Accrued stability fee over [start_ts, end_ts) for one ilk — the pure
    Art × Δr_true integration (no I/O), mirroring ``non_msc_streams.sql``.

    Each event is ``(block_number, log_index, block_time, value)``:
      * ``arts``   value = signed dart (raw wad) from frob/grab
      * ``folds``  value = signed rate delta (RAY) from Vat.fold
      * ``duties`` value = duty (RAY per-second) from Jug.file

    Returns None when the ilk never folded or never had a duty before
    ``start_ts`` (no accrual basis — matches the SQL's NULL filter).
    """
    arts = sorted(arts); folds = sorted(folds); duties = sorted(duties)

    # Carried-in state at month_start.
    art0 = sum(d for (_, _, t, d) in arts if t < start_ts)
    rate0 = _RAY + sum(dr for (_, _, t, dr) in folds if t < start_ts)
    pre_folds = [f for f in folds if f[2] < start_ts]
    rho0 = pre_folds[-1][2] if pre_folds else None
    pre_duty = [d for d in duties if d[2] < start_ts]
    duty0 = pre_duty[-1][3] if pre_duty else None
    if rho0 is None or duty0 is None:
        return None

    in_art = [a for a in arts if start_ts <= a[2] < end_ts]
    in_fold = [f for f in folds if start_ts <= f[2] < end_ts]
    in_duty = [d for d in duties if start_ts <= d[2] < end_ts]

    # Checkpoints: seed(month_start) + every in-month Art change + month_end.
    # Folds / duty files are NOT checkpoints — they only advance the
    # forward-filled (rate, rho, duty) used to evaluate r_true, which is
    # continuous across a fold (a fold merely re-anchors rho).
    neg, inf = (-1, -1), (1 << 62, 1 << 62)
    cps = [(neg, start_ts)] + [((a[0], a[1]), a[2]) for a in in_art] + [(inf, end_ts)]

    def state(key, t):
        art = art0 + sum(a[3] for a in in_art if (a[0], a[1]) <= key)
        rate = rate0 + sum(f[3] for f in in_fold if (f[0], f[1]) <= key)
        fr = [f for f in in_fold if (f[0], f[1]) <= key]
        rho = fr[-1][2] if fr else rho0
        du = [d for d in in_duty if (d[0], d[1]) <= key]
        duty = du[-1][3] if du else duty0
        return art, float(rate) * ((duty / 1e27) ** float(t - rho))

    pts = [state(k, t) for (k, t) in cps]
    total = sum(
        (pts[i][0] / 1e18) * (pts[i + 1][1] - pts[i][1]) for i in range(len(pts) - 1)
    )
    return Decimal(str(total / 1e27))


def _accrue_savings(
    events: list[tuple[int, int | None, int]],
    start_ts: int,
    end_ts: int,
) -> Decimal:
    """Interest accrued in [start_ts, end_ts) from a drip series — the pure
    chi-boundary interpolation (no I/O), mirroring ``non_msc_streams.sql``.

    Each event is ``(block_time, acc, diff)`` sorted ascending: ``diff`` is the
    amount minted at that drip (raw units) for the interval since the previous
    drip; ``acc`` is the accumulator (chi) at the drip, or ``None`` to fall back
    to time-fraction splitting (the pot suck carries no chi).

    Intervals fully inside the month contribute their whole ``diff`` (summed
    exactly as ints); the ≤2 intervals straddling a boundary are split — by the
    geometric growth of ``acc`` when present (a boundary at time ``τ`` in an
    interval ``(t_prev, t]`` maps to ``chi(τ) = acc_prev ×
    (acc/acc_prev)^((τ−t_prev)/(t−t_prev))``), else by elapsed-time fraction.
    Intervals fully outside contribute nothing. Returns raw units (caller scales
    by WAD/RAD).
    """
    whole = 0            # exact int sum of fully-in-month drips
    part = 0.0           # float sum of the (≤2) boundary-straddling intervals
    for i in range(1, len(events)):
        tp, ap, _ = events[i - 1]
        t, a, d = events[i]
        if t <= tp:
            continue
        if start_ts <= tp and t <= end_ts:
            whole += d                       # interval fully inside the month
            continue
        os = max(tp, start_ts)
        oe = min(t, end_ts)
        if oe <= os:
            continue                         # interval fully outside the month
        if a is not None and ap is not None and a != ap:
            g = a / ap
            chi_os = ap * g ** ((os - tp) / (t - tp))
            chi_oe = ap * g ** ((oe - tp) / (t - tp))
            frac = (chi_oe - chi_os) / (a - ap)
        else:
            frac = (oe - os) / (t - tp)
        part += d * frac
    return Decimal(whole) + Decimal(str(part))


