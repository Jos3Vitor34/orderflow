"""Validate local TLS using an explicitly trusted test CA, never disabling verification."""

import argparse
import json
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="https://localhost:8443")
    parser.add_argument("--ca", type=Path, required=True)
    args = parser.parse_args()
    target = urlsplit(args.url)
    if target.scheme != "https" or target.hostname not in {"localhost", "127.0.0.1"}:
        raise SystemExit("Only local HTTPS smoke URLs are supported")
    context = ssl.create_default_context(cafile=str(args.ca))

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = urllib.request.build_opener(
        NoRedirect(), urllib.request.HTTPSHandler(context=context)
    )
    try:
        opener.open(args.url + "/api/v1/auth/me/", timeout=10)
    except urllib.error.HTTPError as response:
        if response.code != 307 or response.headers["Location"] != "/api/v1/auth/me":
            raise RuntimeError(
                "Canonical API redirect must preserve same-origin HTTPS"
            ) from None
    else:
        raise RuntimeError("Expected canonical-path redirect")
    for path in ("/", "/orders/1", "/admin/users", "/healthz", "/health/ready"):
        with urllib.request.urlopen(  # noqa: S310 - validated local HTTPS with verified CA
            args.url + path, context=context, timeout=10
        ) as response:
            if response.status != 200:
                raise RuntimeError(f"TLS smoke failed: {path}")
            if response.headers["Strict-Transport-Security"] != "max-age=31536000":
                raise RuntimeError("HSTS header missing on TLS listener")
            body = response.read()
            if path == "/health/ready" and json.loads(body)["status"] != "ready":
                raise RuntimeError("API not ready behind TLS proxy")
    print(
        "PASS: trusted local TLS, hostname verification, HSTS, SPA, health, API proxy"
    )


if __name__ == "__main__":
    main()
