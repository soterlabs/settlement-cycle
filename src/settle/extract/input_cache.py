"""Run-local policy for finalized revenue inputs, separate from result caching."""

from __future__ import annotations

import hashlib
import inspect
import os
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps


class InputCacheError(RuntimeError):
    """An input cannot be certified for persistent reuse."""


@dataclass
class InputCacheScope:
    revision: str
    ceilings: dict[str, int] = field(default_factory=dict)
    failures: int = 0
    fatal_error: Exception | None = None

    def check_block(self, chain, block: int) -> None:
        from . import hypersync
        from .hypersync_store import _reorg_margin

        chain = getattr(chain, "value", chain)
        try:
            if chain not in self.ceilings:
                self.ceilings[chain] = hypersync.archive_height(chain) - _reorg_margin()
            if type(block) is not int or block < 0 or block > self.ceilings[chain]:
                raise InputCacheError(f"{chain}: block {block} is outside finalized input coverage")
        except Exception as exc:
            self.fatal_error = exc
            self.failures += 1
            raise


_scope: ContextVar[InputCacheScope | None] = ContextVar("settle_input_cache", default=None)


def current_scope() -> InputCacheScope | None:
    return _scope.get()


def mark_fatal(error: Exception) -> None:
    scope = current_scope()
    if scope is not None and scope.fatal_error is None:
        scope.fatal_error = error


def input_revision() -> str:
    """Operator-controlled input correction generation, independent of code."""
    return os.environ.get("SETTLE_INPUT_REVISION", "0")


def revision_key() -> str:
    return hashlib.sha256(input_revision().encode()).hexdigest()[:16]


def revenue_input_scope(fn):
    """As-of calculations use fresh finalized snapshots, not legacy cache blobs.

    Cache policy stays in this invocation; concurrent/ordinary monthly callers
    retain their own policy. Pin-resolution workers already use finalized
    HyperSync helpers; contract-state extraction runs in the invoking thread.
    """
    signature = inspect.signature(fn)
    @wraps(fn)
    def wrapped(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        if bound.arguments.get("as_of") is None:
            return fn(*args, **kwargs)
        scope = InputCacheScope(revision=revision_key())
        token = _scope.set(scope)
        try:
            result = fn(*args, **kwargs)
            if scope.fatal_error is not None:
                raise scope.fatal_error
            return result
        finally:
            _scope.reset(token)
    return wrapped
