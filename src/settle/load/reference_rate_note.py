"""Stable human-readable disclosure of non-official reference-rate inputs."""
from decimal import Decimal


def reference_rate_note(provenance: dict) -> str:
    inherited = provenance.get('reference_rate_notes') or []
    if inherited:
        return '; '.join(dict.fromkeys(inherited))
    rates = provenance.get('close_reference_rate_provenance') or {}
    if rates.get('coverage_complete') is False:
        estimate = rates.get('operator_estimate') or {}
        if all(k in estimate for k in ('apr', 'source_effective_date', 'effective_date')):
            percent = format((Decimal(estimate['apr']) * 100).normalize(), 'f')
            return (f"Operator-authorized SOFR carry-forward: {estimate['source_effective_date']} "
                    f"rate of {percent}% used for {estimate['effective_date']}; "
                    'not an official observation for that date.')
        return 'Reference-rate inputs contain an estimate; official coverage is incomplete.'
    # API-to-monthly finalization supplies this validated status via sources.
    status = str((provenance.get('sources') or {}).get('reference_rate_status') or '')
    if status.lower().startswith(('operator-authorized', 'preliminary')):
        return status
    return ''
