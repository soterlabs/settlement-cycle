from decimal import Decimal as D

import pytest

from settle.normalize.allocation_vault_deposits import deposit_transaction_value


def test_deposit_cash_and_unrelated_share_receipt_are_both_retained():
    result = deposit_transaction_value(
        cash=D(100), deposited_shares=80, fee_shares=0, net_shares=100, price=D("1.25"), scale=D(1)
    )
    assert result == 125


def test_fee_mints_are_not_counted_twice_and_deposit_rounding_uses_actual_cash():
    result = deposit_transaction_value(
        cash=D("100.000001"),
        deposited_shares=80,
        fee_shares=2,
        net_shares=102,
        price=D("1.25"),
        scale=D(1),
    )
    assert result == D("127.500001")


def test_pure_deposit_keeps_actual_cash_cost():
    result = deposit_transaction_value(
        cash=D("100.000001"),
        deposited_shares=80,
        fee_shares=0,
        net_shares=80,
        price=D("1.25"),
        scale=D(1),
    )
    assert result == D("100.000001")


def test_missing_or_outgoing_shares_require_a_different_path():
    with pytest.raises(ValueError, match="exceed incoming shares"):
        deposit_transaction_value(
            cash=D(100),
            deposited_shares=80,
            fee_shares=2,
            net_shares=81,
            price=D("1.25"),
            scale=D(1),
        )


def test_all_176_historical_mixed_deposits_restore_only_the_missing_share_leg():
    import gzip
    import importlib.util
    import json
    from pathlib import Path

    root = Path(__file__).parents[2]
    spec = importlib.util.spec_from_file_location(
        "mixed_deposit_repair", root / "scripts/repair_spark_mixed_deposits.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    witnesses = json.loads(
        gzip.decompress((root / "tests/fixtures/spark_susds_mixed_deposits.json.gz").read_bytes())
    )
    assert len(witnesses) == 176
    for witness in witnesses:
        before = witness["batch"]
        after, audit = module.repair(before, witness)
        assert D(audit["restored_share_value"]) > 0
        assert {k: v for k, v in after.items() if k != "movements"} == {
            k: v for k, v in before.items() if k != "movements"
        }
        for old, new in zip(before["movements"], after["movements"], strict=True):
            if old["account"] != module.ACCOUNT:
                assert old == new
            else:
                assert {k: v for k, v in old.items() if k != "change"} == {
                    k: v for k, v in new.items() if k != "change"
                }
        if (
            witness["transaction_hash"]
            == "0xbf6382cb7c44bb47b75366e1d7ed5bc1959af028521a83a444cd6c4aae268683"
        ):
            # Actual USDT paid in receipt log 702; sUSDS received in log 703.
            receipt = json.loads(
                (root / "tests/fixtures/spark_susds_mixed_april29.json").read_text()
            )
            paid = next(r for r in receipt["logs"] if int(r["logIndex"], 16) == 702)
            assert D(int(paid["data"], 16)) / 10**6 == D("861111.111111")
            assert D(audit["restored_share_value"]).quantize(D(".000001")) == D("861096.398291")
            # Preserve the real $14.71 execution/NAV difference. Do not make
            # purchased shares appear worth more just to force cash equality.
            assert (D("861111.111111") - D(audit["restored_share_value"])).quantize(
                D(".000001")
            ) == D("14.712820")
        with pytest.raises(ValueError, match="deposit-only overwrite"):
            module.repair(after, witness)


def test_incomplete_mixed_deposit_witness_is_rejected():
    import gzip
    import importlib.util
    import json
    from pathlib import Path

    root = Path(__file__).parents[2]
    spec = importlib.util.spec_from_file_location(
        "mixed_deposit_repair", root / "scripts/repair_spark_mixed_deposits.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    witness = json.loads(
        gzip.decompress((root / "tests/fixtures/spark_susds_mixed_deposits.json.gz").read_bytes())
    )[0]
    witness["logs"] = [r for r in witness["logs"] if r["topic1"] != module.ZERO_TOPIC]
    with pytest.raises(ValueError, match="mints and peer receipts"):
        module.repair(witness["batch"], witness)
