"""Supplement retained LP source pins with authenticated cash-principal evidence.

Called by the review worker only, never an HTTP read. Historical published
principal is reused when its cutoff matches; otherwise collect at the exact
retained boundary or keep the source explicitly incomplete.
"""
from copy import deepcopy
from datetime import date

from ...domain.config import load_prime_by_id
from ...domain.period import Month, Period
from ...domain.primes import Chain
from .. import spark_reporting_store
from ..spark_onchain import Source
from ..spark_sde_cash import collect, validate_document
from ..verification import digest


def complete(conn, cutoff, income):
    month = str(cutoff)[:7]
    existing = next((r for r in income.get('spark_sde_cash', []) if r['month'] == month), None)
    if existing and existing['status'] == 'available' and existing.get('through') == str(cutoff):
        return income
    result = deepcopy(income)
    period = next(p for p in result['periods'] if p['key'] == month)
    source = next((r for r in spark_reporting_store.year_rows(conn, cutoff.year)
                   if r['revision_id'] == period.get('offchain_revision')), None)
    proof = dict(status='unavailable', entries=[], errors={'source': 'Matching retained LP source is unavailable'})
    if source and source['cutoff'] == str(cutoff):
        raw = source['document']
        try:
            pins = {Chain(k): v for k,v in raw['closing_pins'].items()}
            span = Period.from_month(Month(cutoff.year,cutoff.month),pins,as_of=cutoff)
            witness = Source(load_prime_by_id('spark'),span,raw['opening_pins'])
            proof = collect(witness,raw)
            validate_document({'sde_cash':proof})
        except Exception as exc:
            proof = dict(status='unavailable',entries=[],errors={'collection':type(exc).__name__})
    record = dict(month=month, through=source['cutoff'] if source else None,
                  source_ref=source['revision_id'] if source else None,
                  origin='cash_flow_worker_authenticated_liquidity_receipts',
                  proof_hash=digest(proof), **proof)
    result['spark_sde_cash'] = [r for r in result.get('spark_sde_cash', []) if r['month'] != month] + [record]
    return result
