from datetime import date
from decimal import Decimal as D

from settle.normalize.allocation_capital import AssetMovement, CapitalBatch, CapitalHistory
from settle.normalize.allocation_history_cache import load_history, save_history


def test_snapshot_roundtrip_preserves_exact_replay_inputs(tmp_path):
    history = CapitalHistory((CapitalBatch('tx', date(2026, 8, 1), 123, 'ethereum', 12,
        (AssetMovement('queue', D('123.123456789012345678901234567890'), D('-1.001'),
                       D('0.001'), True),), D(100), 7, {'ilk': D(100)}),),
        {'V': 'account'}, {'U': 'unknown'}, {'V': ['queue']}, {'cash'}, ('PAU',))
    path = tmp_path / 'history.jsonl.gz'
    save_history(path, history, 'version1')
    assert load_history(path, 'version1') == history
    assert load_history(path, 'version2') is None
    assert not path.with_suffix('.gz.tmp').exists()
