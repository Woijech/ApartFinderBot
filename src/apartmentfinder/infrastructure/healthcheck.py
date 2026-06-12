"""CLI probe for Docker healthcheck commands."""

from __future__ import annotations

import sys
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


def main() -> None:
    """Exit with zero only when the given health URL returns a 2xx response."""
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080/readiness"
    try:
        with urlopen(url, timeout=3) as response:
            if 200 <= response.status < 300:
                return
    except (HTTPError, URLError, TimeoutError):
        pass
    raise SystemExit(1)


if __name__ == "__main__":
    main()
