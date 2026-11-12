"""Recompute October TMF from retained activity/state, without chain extraction."""
from __future__ import annotations

import json
from decimal import Decimal as D
from pathlib import Path

import yaml

from settle.compute.tmf import TmfPolicy, compute_tmf_monthly, write_tmf
from settle.domain.tmf import SbeActivity, SbeDistribution, SbeKick, SbeParamChange

ROOT = Path(__file__).resolve().parents[1]


def replay():
    saved = json.loads((ROOT / 'reconciliation/2026-10/treasury/tmf.json').read_text())
    raw = saved['activity']
    if saved['month'] != '2026-10' or saved['pins']['partial'] or raw is None:
        raise ValueError('Complete October activity required')
    activity = SbeActivity(
        **{k: raw[k] for k in ('month', 'from_block', 'to_block', 'from_ts', 'to_ts')},
        kicks=[SbeKick(**{k: D(v) if k in {'tot', 'lot', 'pay', 'bought', 'burn'} else v
                         for k, v in row.items()}) for row in raw['kicks']],
        param_changes=[SbeParamChange(**row) for row in raw['param_changes']],
        distributions=[SbeDistribution(**{**row, 'amount': D(row['amount'])})
                       for row in raw['distributions']],
    )
    cfg = yaml.safe_load((ROOT / 'config/tmf.yaml').read_text())
    result = compute_tmf_monthly(
        '2026-10', cfg['months']['2026-10'], TmfPolicy.from_config(cfg),
        activity=activity, state=saved['state'], repo_root=ROOT,
        sources=cfg['months']['2026-10']['sources'], pins=saved['pins'],
    )
    allowed = {'sky_rewards_monthly', 'vest_tot_exact', 'vest_tot', 'stream_rate', 'weekly_pull'}
    for key, value in saved['results']['waterfall'].items():
        if key not in allowed:
            actual = getattr(result.waterfall, key)
            if actual != (value if isinstance(value, bool) else D(str(value))):
                raise ValueError('Unexpected non-stream change: ' + key)
    for key in ('sky_bought_month', 'usds_spent_month', 'sky_to_burn', 'sky_to_stakers_from_buys'):
        if getattr(result, key) != D(saved['results'][key]):
            raise ValueError('Unexpected activity change: ' + key)
    if result.inputs.snr != D(saved['inputs']['snr']):
        raise ValueError('SNR changed')
    write_tmf(result, ROOT / 'settlements/tmf/2026-10')
    print('Monthly SKY rewards:', result.waterfall.sky_rewards_monthly)
    print('90-day vest:', result.waterfall.vest_tot)
    print('Weekly pull:', result.waterfall.weekly_pull)
    print('SNR and all non-stream amounts unchanged.')


if __name__ == '__main__':
    replay()
