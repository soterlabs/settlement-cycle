"""Read-only projections of published management-reporting metrics (v3)."""
from datetime import date, timedelta
from typing import Literal

from fastapi import Depends, HTTPException, Query, Request

from ..revenue.financial import (
    MONTHLY_STATEMENT_POLICIES,
    RWA_POLICIES,
    SPARK_INCOME_POLICY,
    STATEMENT_SOURCE_POLICY,
    decode_selection,
    statements,
)
from ..revenue.verification import PRIMES
from . import revenue


def register_financial(app, get_reader, document_response):
    def validate(prime, scope, cutoff=None):
        if prime not in PRIMES:
            raise HTTPException(404, 'Unknown prime')
        if scope != 'individual':
            raise HTTPException(422, 'Only the individual operational-prime perimeter is supported in this slice')
        if cutoff is not None and cutoff >= revenue.today():
            raise HTTPException(422, 'cutoff must be a completed UTC day')

    def read(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            raise HTTPException(503, 'Financial reporting store unavailable', headers={'Cache-Control': 'no-store'}) from None

    def annual(prime, year, scope, cutoff, revision, r):
        validate(prime, scope, cutoff)
        if date(year, 1, 1) >= revenue.today():
            raise HTTPException(422, 'year must include a completed UTC day')
        if revision is not None and cutoff is not None:
            raise HTTPException(422, 'Select either an immutable report revision or a cutoff')
        selection = None
        if revision:
            try:
                selection = decode_selection(revision, prime, year)
                cutoff = date.fromisoformat(selection['as_of'])
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
            validate(prime, scope, cutoff)
        if cutoff is not None and cutoff.year != year:
            raise HTTPException(422, 'cutoff must belong to the selected year')
        as_of = cutoff or min(revenue.today() - timedelta(days=1), date(year, 12, 31))
        rows = read(r.financial_year, prime, year,
                    revisions=[x for x in selection['revisions'] if x] if selection else None,
                    policy_version=(selection['policy_version'] if selection and selection['policy_version'] not in MONTHLY_STATEMENT_POLICIES
                                    else STATEMENT_SOURCE_POLICY),
                    **({'include_rwa': True} if prime == 'grove' and (not selection or selection['policy_version'] in RWA_POLICIES) else {}),
                    **({'include_spark_fees': True} if prime == 'spark' and (not selection or selection['policy_version'] == SPARK_INCOME_POLICY) else {}))
        spark_rows = []
        if prime == 'spark' and (not selection or selection['policy_version'] == SPARK_INCOME_POLICY) and hasattr(r, 'spark_component_year'):
            spark_rows = read(r.spark_component_year, year,
                             revisions=[ref for ref in selection['offchain_revisions'] if ref] if selection else None)
        try:
            result = statements(prime, year, as_of, rows,
                                exact=selection['exact'] if selection else cutoff is not None,
                                selection=selection, monthly=True, rollups=True, rwa=True, allocation_rewards=True, offchain=True, archive=True, spark_income=prime == 'spark', spark_publications=spark_rows)
        except ValueError as exc:
            raise HTTPException(404, str(exc), headers={'Cache-Control': 'no-store'}) from None
        selected_ids = {p['financial_revision'] for p in result['periods'] if p['financial_revision']}
        return {**result, 'source_lag_periods': [p['cutoff'] for p in rows
            if p['revision_id'] in selected_ids and p.get('source_lag')]}

    @app.get('/v3/reporting/statements')
    def statement(request: Request, prime: str, year: int = Query(ge=2020, le=9998),
                  scope: Literal['individual', 'group', 'combined'] = 'individual',
                  cutoff: date | None = None, revision: str | None = Query(None, max_length=4096),
                  r=Depends(get_reader)):  # noqa: B008
        return document_response(request, annual(prime, year, scope, cutoff, revision, r))

    def component_month(prime, month, revision, r):
        import calendar
        year, number = map(int, month.split('-'))
        if not 2020 <= year <= 9998:
            raise HTTPException(422, 'Invalid reporting year')
        if date(year, number, 1) >= revenue.today():
            raise HTTPException(422, 'month must include a completed UTC day')
        end = date(year, number, calendar.monthrange(year, number)[1])
        if prime != 'spark':
            validate(prime, 'individual')
            raise HTTPException(422, 'This reporting component is currently supported for Spark')
        doc = annual(prime, year, 'individual', end if end < revenue.today() and not revision else None, revision, r)
        if doc['policy_version'] != SPARK_INCOME_POLICY:
            raise HTTPException(422, 'Monthly Spark components require the Spark income reporting policy')
        return doc

    @app.get('/v3/reporting/fees')
    def fees(request: Request, prime: str, month: str = Query(pattern=r'^\d{4}-(0[1-9]|1[0-2])$'),
             venue: str | None = None, revision: str | None = Query(None, max_length=4096),
             r=Depends(get_reader)):  # noqa: B008
        doc = component_month(prime, month, revision, r)
        rows = [row for row in doc.get('spark_fee_ledger', []) if row['month'] == month and (venue is None or row['venue'] == venue)]
        if venue is not None and venue not in {'S10', 'S11', 'S65', 'S12', 'S13', 'S34'}:
            raise HTTPException(404, 'Unknown curator-fee venue')
        return document_response(request, {'prime': prime, 'month': month, 'policy_version': doc['policy_version'],
            'selection': doc['selection'], 'report_hash': doc['report_hash'], 'source_manifest': doc['source_manifest'],
            'metrics': {line['id']: line['values'][month] for line in doc['lines']
                        if (line['id'] == 'fees.curator.' + venue if venue else line['id'].startswith('fees.'))},
            'period': next(p for p in doc['periods'] if p['key'] == month),
            'curator_fees': rows, 'basis': 'curator accrual; SparkLend treasury receipts remain receipt-based'})

    @app.get('/v3/reporting/gains')
    def gains(request: Request, prime: str, month: str = Query(pattern=r'^\d{4}-(0[1-9]|1[0-2])$'),
              revision: str | None = Query(None, max_length=4096), r=Depends(get_reader)):  # noqa: B008
        doc = component_month(prime, month, revision, r)
        return document_response(request, {'prime': prime, 'month': month, 'selection': doc['selection'],
            'report_hash': doc['report_hash'], 'source_manifest': doc['source_manifest'],
            'metrics': {line['id']: line['values'][month] for line in doc['lines'] if line['id'].startswith('gains.')},
            'net_gains': [row for row in doc.get('spark_gain_ledger', []) if row['month'] == month],
            'method': 'estimated daily price revaluation on net Spark exposure; depositor liabilities offset'})

    @app.get('/v3/reporting/allocations')
    def allocation_revenue(request: Request, prime: str, month: str = Query(pattern=r'^\d{4}-(0[1-9]|1[0-2])$'),
                           venue: str | None = None, revision: str | None = Query(None, max_length=4096),
                           r=Depends(get_reader)):  # noqa: B008
        from ..revenue.allocation_revenue import month_projection
        doc = component_month(prime, month, revision, r)
        result = month_projection(doc, month, venue)
        if venue and not result['allocations']:
            raise HTTPException(404, 'Unknown supply-revenue venue')
        return document_response(request, result)

    @app.get('/v3/reporting/portfolio')
    def portfolio(request: Request, prime: str, month: str = Query(pattern=r'^\d{4}-(0[1-9]|1[0-2])$'),
                  scope: Literal['individual', 'group', 'combined'] = 'individual',
                  cutoff: date | None = None,
                  revision: str | None = Query(None, pattern=r'^[a-f0-9]{64}$'),
                  source_revision: str | None = Query(None, pattern=r'^[a-f0-9]{64}$'),
                  r=Depends(get_reader)):  # noqa: B008
        import calendar
        validate(prime, scope, cutoff)
        try:
            year, number = map(int, month.split('-'))
            first = date(year, number, 1)
            end = date(year, number, calendar.monthrange(year, number)[1])
        except ValueError:
            raise HTTPException(422, 'Invalid reporting month') from None
        if first >= revenue.today():
            raise HTTPException(422, 'month must include a completed UTC day')
        if cutoff and cutoff.strftime('%Y-%m') != month:
            raise HTTPException(422, 'cutoff must belong to the selected month')
        if revision and source_revision:
            raise HTTPException(422, 'Select either a financial revision or a source revision')
        selected = cutoff or (end if end < revenue.today() and not (revision or source_revision) else None)
        row, latest_source = read(r.financial_bundle, prime, cutoff=selected,
                                  source_revision=source_revision, revision=revision)
        if not row or row['cutoff'][:7] != month:
            raise HTTPException(404, 'No financial publication for this selection', headers={'Cache-Control': 'no-store'})
        data = row['document']
        source_lag = bool(latest_source and latest_source['revision_id'] != row['source_revision'])
        return document_response(request, {
            'schema_version': '3.0', 'prime': prime, 'scope': scope, 'cutoff': row['cutoff'],
            'financial_revision': row['revision_id'], 'report_hash': row['document_hash'],
            'computed_at': row['computed_at'], 'source': data['source'],
            'financial_document': data,
            'policy_version': data['policy_version'], 'portfolio': data['portfolio'],
            'limitations': data['limitations'], 'source_lag': source_lag,
            **({'claims': data['claims']} if 'claims' in data else {}),
            'freshness': revenue.freshness(row, cutoff or min(end, revenue.today() - timedelta(days=1))),
        })
