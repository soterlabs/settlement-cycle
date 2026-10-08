import json
from decimal import Decimal as D
from decimal import localcontext
from pathlib import Path

from settle.extract._keccak import keccak256
from settle.extract.transfer_logs import TRANSFER_TOPIC0


def test_actual_saver_cash_and_sky_draw_have_distinct_authenticated_token_paths():
    fixture = json.loads((Path(__file__).parents[1] / 'fixtures/spark_distinct_saver_and_sky_funding.json').read_text())
    b, receipt = fixture['batch'], fixture['receipt']
    assert receipt['status'] == '0x1' and int(receipt['blockNumber'], 16) == b['block'] == 25007787
    assert 'ethereum:' + receipt['transactionHash'] == b['identity']
    logs = receipt['logs']
    alm = '1601843c5e9bc251a3272907010afa41fa18347e'
    usds = '0xdc035d45d973e3ec169d2276ddab16f1e407384f'
    susds = '0xa3931d71877c0e7a3148cb7eb4463524fec27fbd'
    buffer = 'c395d150e71378b47a1b8e9de0c1a83b75a08324'
    vault = '0xe2e7a17dff93280dec073c995595155283e3c372'
    cash = next(m for m in b['movements'] if m['account'] == f'ethereum:0x{alm}:{usds}')
    assert D(cash['value_before']) == D(cash['change']) == 0
    ingress = [r for r in logs if r['address'] == usds and r['topics'][0] == TRANSFER_TOPIC0 and r['topics'][2].endswith(alm)]
    out = [r for r in logs if r['address'] == usds and r['topics'][0] == TRANSFER_TOPIC0 and r['topics'][1].endswith(alm)]
    assert len(ingress) == len(out) == 1
    assert ingress[0]['topics'][1].endswith(buffer) and out[0]['topics'][2].endswith(susds[2:])
    amount = int(ingress[0]['data'], 16)
    assert amount == int(out[0]['data'], 16)
    with localcontext() as ctx:
        ctx.prec = 60
        assert abs(D(amount)/10**18 - D(b['minted'])) < D('1e-18')
    deposit_topic = '0x' + keccak256(b'Deposit(address,address,uint256,uint256)').hex()
    deposit = next(r for r in logs if r['address'] == susds and r['topics'][0] == deposit_topic)
    assert deposit['topics'][1].endswith(alm) and deposit['topics'][2].endswith(alm)
    assert int(deposit['data'][2:66], 16) == amount
    shares = int(deposit['data'][66:], 16)
    assert any(r['address'] == susds and r['topics'][0] == TRANSFER_TOPIC0
               and int(r['topics'][1], 16) == 0 and r['topics'][2].endswith(alm)
               and int(r['data'], 16) == shares for r in logs)
    take_topic = '0x' + keccak256(b'Take(address,uint256)').hex()
    take = next(r for r in logs if r['address'] == vault and r['topics'][0] == take_topic)
    assert take['topics'][1].endswith(alm)
    assert D(int(take['data'], 16))/10**6 == D('180000802.451341')
    assert any(r['address'] == '0xdac17f958d2ee523a2206206994597c13d831ec7'
               and r['topics'][0] == TRANSFER_TOPIC0 and r['topics'][1].endswith(vault[2:])
               and r['topics'][2].endswith(alm) and int(r['data'], 16) == int(take['data'], 16) for r in logs)
