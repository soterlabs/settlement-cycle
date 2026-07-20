"""HyperSync-backed ``IPositionBalanceSource`` — snapshot balance via events.

`balance_at` is called for *every* fungible venue, including **rebasing**
aTokens/spTokens (Cat C/D), where it must return the *rebased* on-chain amount.
Σ(Transfer) equals balanceOf only for **non-rebasing** tokens — so a pure
event reconstruction would be silently wrong for aTokens.

This source is therefore a **self-verifying hybrid** that can never be wrong:
  * First time it sees a `(chain, token)` with a non-zero balance, it computes
    BOTH the event-reconstructed balance and the RPC balanceOf and compares.
  * Equal AND not a structural aToken (no `POOL()`/`UNDERLYING_ASSET_ADDRESS()`)
    ⇒ non-rebasing ⇒ classify "events" and serve every later query for that
    token from Transfer logs (no RPC). The aToken gate matters: Σ(Transfer)
    also equals balanceOf for a rebasing aToken probed with no accrued interest
    (index ~RAY right after mint), so a value match alone is not sufficient.
  * Rebasing (aToken, or a value mismatch) ⇒ prefer the wei-exact Aave event
    reconstruction; if that disagrees with RPC, classify "rpc" and delegate.
  * Until classified (or on the probe itself) it returns the trusted RPC value.

Net: non-rebasing tokens (par-stables, 4626 shares, EOA principal) cost one RPC
probe then go event-only; rebasing tokens stay on RPC. Same `int` (raw units)
contract as ``RPCPositionBalanceSource``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ...extract import hypersync_store
from .hypersync_balances import _TRANSFER_T0, _addr_topic


class HyperSyncPositionBalanceSource:
    """Implements ``IPositionBalanceSource`` via a self-verifying event/RPC hybrid."""

    def __init__(
        self,
        rpc_fallback: Any = None,
        *,
        fetch_logs: Callable[..., list[Any]] = hypersync_store.fetch_logs,
        aave_source: Any = None,
    ) -> None:
        if rpc_fallback is None:
            from .rpc_position import RPCPositionBalanceSource
            rpc_fallback = RPCPositionBalanceSource()
        if aave_source is None:
            from .hypersync_atoken import HyperSyncAaveSource
            aave_source = HyperSyncAaveSource(fetch_logs=fetch_logs)
        self._rpc = rpc_fallback
        self._fetch = fetch_logs
        self._aave = aave_source
        self._verdict: dict[tuple[str, str], str] = {}  # -> events|aave|rpc

    def balance_at(self, chain: str, token: bytes, holder: bytes, block: int) -> int:
        key = (chain, bytes(token).hex())
        verdict = self._verdict.get(key)
        if verdict == "events":
            return self._events_balance(chain, token, holder, block)
        if verdict == "aave":
            return self._aave.reconstruct_balance(chain, token, holder, block)
        if verdict == "rpc":
            return self._rpc.balance_at(chain, token, holder, block)

        # Unknown token → probe: trust RPC, classify on a non-zero comparison.
        rpc_bal = self._rpc.balance_at(chain, token, holder, block)
        if rpc_bal != 0:  # zero tells us nothing (Σtransfers==balanceOf==0 trivially)
            events_bal, first_blk = self._events_balance_and_first(chain, token, holder, block)
            if (events_bal == rpc_bal
                    and not self._is_atoken(chain, token, block)
                    and self._two_point_check(chain, token, holder, first_blk, block)):
                # Σ(Transfer) == balanceOf at TWO distant blocks AND not a
                # structural aToken ⇒ non-rebasing. The is_atoken gate catches
                # Aave rebasers probed with no accrued interest (index ~RAY
                # right after mint); the two-point check catches NON-Aave
                # rebasers (no POOL() getter) that coincidentally match at the
                # probe block — a rebaser cannot match at two blocks far apart
                # unless zero yield accrued between them.
                self._verdict[key] = "events"
            else:
                # Rebasing (or an aToken that momentarily matches) → prefer the
                # wei-exact Aave event reconstruction (Cat C); else delegate to RPC.
                try:
                    recon = self._aave.reconstruct_balance(chain, token, holder, block)
                except Exception:                   # not an aToken / metadata read failed
                    recon = None
                self._verdict[key] = "aave" if recon == rpc_bal else "rpc"
        return rpc_bal

    def _is_atoken(self, chain: str, token: bytes, block: int) -> bool:
        """Structural rebasing test via the Aave source, tolerant of a stub
        source that doesn't implement it (treated as non-aToken).

        FAILS CLOSED on errors: a transport blip during the probe must never
        classify a real aToken as "events" (which would pin it to stale
        transfer sums and silently drop its accrued interest for the whole
        run). Treating the error as "might be an aToken" routes the token to
        the aave/rpc branch — the RPC path is always correct."""
        check = getattr(self._aave, "is_atoken", None)
        if check is None:
            return False
        try:
            return bool(check(chain, token, block))
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(
                "is_atoken probe failed for %s/0x%s at block %d (%s: %s) — "
                "failing CLOSED: token stays on the aave/rpc path this run.",
                chain, bytes(token).hex(), block, type(exc).__name__, exc,
            )
            return True

    def _two_point_check(self, chain: str, token: bytes, holder: bytes,
                         first_blk: int | None, block: int) -> bool:
        """Second equality probe at the midpoint between the holder's first
        Transfer and ``block``. One extra RPC read + one store-served event
        read, once per token; a mid-block where the holder held nothing is
        uninformative and passes (same residual risk as a single probe)."""
        if first_blk is None:
            return True
        mid = (first_blk + block) // 2
        if not (first_blk < mid < block):
            return True                  # window too narrow to add signal
        rpc_mid = self._rpc.balance_at(chain, token, holder, mid)
        if rpc_mid == 0:
            return True
        ev_mid, _ = self._events_balance_and_first(chain, token, holder, mid)
        return ev_mid == rpc_mid

    def _events_balance(self, chain: str, token: bytes, holder: bytes, block: int) -> int:
        return self._events_balance_and_first(chain, token, holder, block)[0]

    def _events_balance_and_first(
        self, chain: str, token: bytes, holder: bytes, block: int,
    ) -> tuple[int, int | None]:
        """Σ(Transfer value to holder) − Σ(from holder) for ``token`` at
        ``block`` (raw), plus the holder's first Transfer block (None if no
        transfers)."""
        ht = _addr_topic(holder)
        tok = "0x" + bytes(token).hex()
        sel = [
            {"address": [tok], "topics": [[_TRANSFER_T0], [ht]]},        # from == holder
            {"address": [tok], "topics": [[_TRANSFER_T0], [], [ht]]},    # to == holder
        ]
        rows = self._fetch(chain, sel, 0, block)
        seen: set[tuple[int, int]] = set()
        bal = 0
        first: int | None = None
        for r in rows:
            k = (r.block_number, r.log_index)
            if k in seen:                # self-transfer matches both selections
                continue
            seen.add(k)
            if first is None or r.block_number < first:
                first = r.block_number
            v = int(r.data, 16)
            if r.topic2 == ht:           # inflow
                bal += v
            if r.topic1 == ht:           # outflow
                bal -= v
        return bal, first
