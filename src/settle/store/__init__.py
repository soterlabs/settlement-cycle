"""Postgres persistence for the daily pipeline (docs/PRD_daily_pipeline_api.md).

Distinct from ``extract.postgres_store`` (the opaque raw-data cache) and
``extract.hypersync_store`` (raw log rows): this package holds DECODED,
queryable facts plus the ``runs`` versioning spine, and is the only thing the
read API talks to.
"""
