"""Prove whether reviewed controlled wallets hold internal Savings claims.

This is a closing-stock check, not a transfer replay. Savings shares are already
excluded from allocation assets and cash; net their owned claim against the gross
depositor liability once. Missing coverage/valuation remains unavailable.
The recorded gross liability supplies the same USD valuation for both sides.
"""

import re
from decimal import Decimal, localcontext

from .verification import digest

BALANCE_OF = "0x70a08231"
ADDRESS = re.compile(r"0x[0-9a-f]{40}")


def collect(reader, savings, config, liabilities):
    vaults = sorted((key, chain, vault.lower()) for key, chain, vault, *_ in savings)
    scope = {
        chain: sorted(
            set(config["wallets"].get(chain, {}))
            | {vault for _, ch, vault in vaults if ch == chain}
        )
        for chain in sorted({ch for _, ch, _ in vaults})
    }
    observations = []
    for key, chain, vault in vaults:
        for owner in scope[chain]:
            row = dict(venue=key, chain=chain, vault=vault, owner=owner)
            try:
                shares = reader.balance(chain, vault, owner)
                row["shares"] = str(shares)
                row["assets_units"] = str(
                    reader.number(chain, vault, "convertToAssets(uint256)", shares) if shares else 0
                )
                if shares:
                    total_assets = reader.number(chain, vault, "totalAssets()")
                    reader.number(chain, vault, "totalSupply()")
                    row["usd_value"] = claim_value(
                        int(row["assets_units"]), total_assets, liabilities[key]
                    )
                else:
                    row["usd_value"] = "0"
            except Exception as error:
                row.update(shares=row.get("shares"), usd_value=None, error=type(error).__name__)
            observations.append(row)
    complete = all(r.get("usd_value") is not None for r in observations)
    with localcontext() as ctx:
        ctx.prec = 100
        value = (
            str(sum((Decimal(r["usd_value"]) for r in observations), Decimal(0)))
            if complete
            else None
        )
    return dict(
        schema_version="spark-savings-control.v1",
        configuration_revision=config["revision"],
        vaults=[dict(venue=k, chain=ch, vault=v) for k, ch, v in vaults],
        owners=scope,
        observations=observations,
        status="available" if complete else "unavailable",
        value=value,
        reason=None if complete else "Controlled Savings share coverage or valuation is incomplete",
        basis="owned_Savings_claims_at_same_closing_USD_basis_as_gross_liabilities",
        scope_hash=digest(dict(vaults=vaults, owners=scope)),
    )


def validate(control, document):
    """Require Cartesian coverage, raw share/claim getters and consistent USD marks."""
    if control.get("schema_version") != "spark-savings-control.v1":
        raise ValueError("Unknown Savings control schema")
    vaults = [(r["venue"], r["chain"], r["vault"]) for r in control["vaults"]]
    owners = control["owners"]
    if (
        not vaults
        or len({k for k, _, _ in vaults}) != len(vaults)
        or {k for k, _, _ in vaults} != set(document["liabilities"])
        or set(owners) != {ch for _, ch, _ in vaults}
        or control["scope_hash"] != digest(dict(vaults=vaults, owners=owners))
        or not re.fullmatch(r"[0-9a-f]{64}", control["configuration_revision"])
    ):
        raise ValueError("Savings control scope differs")
    expected = set()
    for key, chain, vault in vaults:
        if (
            not ADDRESS.fullmatch(vault)
            or document["liabilities"][key].get("chain") != chain
            or document["liabilities"][key].get("token") != vault
            or not owners[chain]
            or len(set(owners[chain])) != len(owners[chain])
            or vault not in owners[chain]
            or any(not ADDRESS.fullmatch(owner) for owner in owners[chain])
        ):
            raise ValueError("Invalid Savings control owners")
        expected.update((key, chain, vault, owner) for owner in owners[chain])
    reads = {}
    for row in document["reads"]:
        key = row["chain"], row["contract"], row["method"], row.get("data")
        if key in reads and reads[key] != row["result"]:
            raise ValueError("Conflicting closing raw responses")
        reads[key] = row["result"]
    seen = set()
    complete = True
    usd_values = []
    owned_shares, owned_assets = {}, {}
    for row in control["observations"]:
        identity = row["venue"], row["chain"], row["vault"], row["owner"]
        if identity not in expected or identity in seen:
            raise ValueError("Unexpected or duplicate Savings control observation")
        seen.add(identity)
        value = row["shares"]
        if value is None:
            complete = False
            continue
        if not isinstance(value, str) or not re.fullmatch(r"0|[1-9][0-9]*", value):
            raise ValueError("Invalid controlled share count")
        venue, chain, vault, owner = identity
        if chain not in document["pins"]:
            raise ValueError("Savings control chain pin missing")
        code = reads.get((chain, vault, "eth_getCode", None))
        raw = reads.get((chain, vault, "eth_call", BALANCE_OF + owner[2:].zfill(64)))
        if code == "0x":
            proved = 0
        elif code and raw and re.fullmatch(r"0x[0-9a-fA-F]{64}", raw):
            proved = int(raw, 16)
        else:
            raise ValueError("Controlled shares lack raw chain proof")
        if int(value) != proved:
            raise ValueError("Controlled shares differ from chain proof")
        if row.get("usd_value") is None:
            complete = False
            continue
        if proved:

            def scalar(signature, argument=None, chain=chain, vault=vault):
                from ..extract._keccak import keccak256

                data = "0x" + keccak256(signature.encode()).hex()[:8]
                if argument is not None:
                    data += f"{argument:064x}"
                raw_value = reads.get((chain, vault, "eth_call", data))
                if not raw_value or not re.fullmatch(r"0x[0-9a-fA-F]{64}", raw_value):
                    raise ValueError("Internal Savings valuation lacks raw proof")
                return int(raw_value, 16)

            asset_units = scalar("convertToAssets(uint256)", proved)
            total_assets = scalar("totalAssets()")
            total_supply = scalar("totalSupply()")
            owned_shares[venue] = owned_shares.get(venue, 0) + proved
            owned_assets[venue] = owned_assets.get(venue, 0) + asset_units
            if owned_shares[venue] > total_supply or owned_assets[venue] > total_assets:
                raise ValueError("Internal Savings claims exceed gross issuance")
            expected_usd = claim_value(asset_units, total_assets, document["liabilities"][venue])
        else:
            asset_units, expected_usd = 0, "0"
        if row.get("assets_units") != str(asset_units) or row["usd_value"] != expected_usd:
            raise ValueError("Internal Savings claim valuation differs")
        usd_values.append(Decimal(expected_usd))
    available = complete and seen == expected
    with localcontext() as ctx:
        ctx.prec = 100
        expected_value = str(sum(usd_values, Decimal(0))) if available else None
    if seen != expected or (
        control["status"] != ("available" if available else "unavailable")
        or control["value"] != expected_value
    ):
        raise ValueError("Savings control coverage/availability differs")
    return control


def claim_value(units, total_assets, liability):
    if liability.get("status") != "available":
        raise ValueError("Gross Savings liability unavailable")
    gross = Decimal(liability["value"])
    if not gross.is_finite() or gross < 0 or units > total_assets:
        raise ValueError("Invalid gross Savings valuation")
    if not total_assets:
        if units or gross:
            raise ValueError("Nonzero claim on zero underlying")
        return "0"
    with localcontext() as ctx:
        ctx.prec = 100
        return str(gross * Decimal(units) / Decimal(total_assets))


def other_controlled_eth(reader, config, alm):
    """Closing native/wrapped ETH outside existing ALM and Ops inventories.

    Savings idle underlying is captured separately. Intents custody is not an
    unconditional entity asset. Ops WETH already belongs to reserve_nonstable;
    Ops native ETH does not. Restrict native reads to ETH-native chains.
    """
    from .spark_onchain import WETH
    from .spark_treasury import OPS

    units = 0
    for chain, token in WETH.items():
        for owner, meta in config["wallets"].get(chain, {}).items():
            if meta["role"] in {"savings_idle", "intents"} or owner == alm.get(chain):
                continue
            units += reader.native(chain, owner)
            if (chain, owner) != ("ethereum", OPS):
                units += reader.balance(chain, token, owner)
    with localcontext() as ctx:
        ctx.prec = 100
        return Decimal(units) / 10**18 * reader.price("ETH")


# Chainlink's official directory used by its documentation:
# https://github.com/smartcontractkit/documentation/blob/main/src/features/data/chains.ts
# https://reference-data-directory.vercel.app/feeds-mainnet.json
# EURC identity: https://developers.circle.com/stablecoins/eurc-contract-addresses
# These are explicit reviewed assets, never identities inferred from symbols.
OTHER_TOKENS = {
    "LINK": (
        "0x514910771af9ca656af840dff83e8264ecf986ca",
        18,
        "0x2c1d072e956affc0d435cb7ac38ef18d24d9127c",
        3600,
    ),
    "EURC": (
        "0x1abaea1f7c830bd89acc67ec4af516284b1bc33c",
        6,
        "0xb49f677943bc038e9857d61e7d053caa2c1734c1",
        86400,
    ),
}


def other_controlled_token(reader, config, symbol):
    """Historical holdings times a fresh onchain USD mark; EURC is a EUR claim."""
    from .cash_flow.classify import is_cash

    token, decimals, feed, max_age = OTHER_TOKENS[symbol]
    units = sum(
        reader.balance("ethereum", token, owner)
        for owner, meta in config["wallets"].get("ethereum", {}).items()
        if meta["role"] not in {"savings_idle", "intents"}
        and not is_cash(config, "ethereum", token, owner)
    )
    if not units:
        return Decimal(0)
    if reader.number("ethereum", token, "decimals()") != decimals:
        raise ValueError("Reviewed token scale differs")
    raw = reader.call("ethereum", feed, "latestRoundData()")
    if not isinstance(raw, str) or not re.fullmatch(r"0x[0-9a-fA-F]{320}", raw):
        raise ValueError("Invalid closing token oracle")
    round_id, answer, _, updated, answered = [
        int(raw[i : i + 64], 16) for i in range(2, len(raw), 64)
    ]
    if not 0 < answer < 2**255 or answered < round_id:
        raise ValueError("Invalid closing token oracle round")
    if not 0 <= reader.pins["ethereum"]["timestamp"] - updated <= max_age:
        raise ValueError("Stale closing token oracle")
    if reader.number("ethereum", feed, "decimals()") != 8:
        raise ValueError("Reviewed oracle scale differs")
    with localcontext() as ctx:
        ctx.prec = 100
        return Decimal(units) * answer / 10 ** (decimals + 8)
