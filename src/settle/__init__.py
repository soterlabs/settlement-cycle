"""MSC monthly settlement pipeline."""

# Load `.env` from the nearest ancestor at import time so RPC / Dune env vars
# are available without manual `export`. ``override=False`` means a real
# environment variable still wins — useful for CI overrides and `ETH_RPC=...
# python ...` invocations.
from dotenv import load_dotenv as _load_dotenv

_load_dotenv(override=False)
del _load_dotenv

__version__ = "0.1.0"
