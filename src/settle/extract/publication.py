"""Strict source failures for the isolated daily worker, including its child threads.

Legacy monthly fallback handlers catch Exception. This signal deliberately bypasses
those handlers and is caught by the daily worker's durable retry boundary.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from threading import Lock


class RequiredInputFailure(BaseException):
    """A terminal required-source failure; never substitute a financial default."""


_active = None
_lock = Lock()
_inside_source = ContextVar('publication_source', default=False)
_optional_revert = ContextVar('publication_optional_revert', default=False)


def active():
    return _active is not None


def fail(error):
    if _active is not None:
        failure = RequiredInputFailure(f'Required input unavailable ({type(error).__name__})')
        _active.failure = failure
        raise failure from error


class PublicationGuard:
    """Process-wide, like ProviderAudit; use one calculation per worker process."""
    def __enter__(self):
        global _active
        if not _lock.acquire(blocking=False):
            raise RuntimeError('Concurrent publication guards require separate worker processes')
        self.failure = None
        _active = self
        return self

    def __exit__(self, typ, value, tb):
        global _active
        _active = None
        _lock.release()
        if typ is None and self.failure is not None:
            raise self.failure


@contextmanager
def optional_revert():
    """Only a structured EVM revert may establish an unsupported capability.

    Transport failures, malformed responses and non-revert RPC errors still abort.
    """
    token = _optional_revert.set(True)
    try:
        yield
    finally:
        _optional_revert.reset(token)


def source_operation(fn=None, *, raw_rpc=False):
    """Allow internal retries to finish; abort when the logical operation fails."""
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            if _active is None:
                return function(*args, **kwargs)
            token = _inside_source.set(True)
            try:
                return function(*args, **kwargs)
            except Exception as exc:
                from .rpc import EVMRevert
                # Raw JSON-RPC passes a typed revert to eth_call, which also
                # classifies persisted negative responses at the call boundary.
                if isinstance(exc, EVMRevert) and (raw_rpc or _optional_revert.get()):
                    raise
                fail(exc)
                raise
            finally:
                _inside_source.reset(token)
        return wrapped
    return decorate(fn) if fn is not None else decorate


def transport_failure(error):
    """Unregistered HTTP paths cannot hide errors; known sources own retries."""
    if not _inside_source.get():
        fail(error)


def transport_rpc_response(response):
    """Validate unregistered JSON-RPC paths; registered operations own retries."""
    if not active() or _inside_source.get():
        return
    try:
        payload = response.json()
        if not isinstance(payload, dict) or 'error' in payload or 'result' not in payload:
            raise ValueError('Invalid upstream JSON-RPC response')
    except Exception as exc:
        fail(exc)
