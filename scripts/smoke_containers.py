"""HTTP/auth smoke for an isolated local production Compose project (no Stripe calls)."""

import argparse
import json
import re
import secrets
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode, urlsplit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--project", default="orderflow-local-smoke")
    parser.add_argument("--secrets", type=Path, default=Path(".secrets/local-smoke"))
    args = parser.parse_args()
    target = urlsplit(args.url)
    if target.scheme not in {"http", "https"} or target.hostname not in {
        "127.0.0.1",
        "localhost",
    }:
        raise SystemExit(
            "This script creates synthetic data and only accepts loopback URLs"
        )
    if not args.project.endswith("-smoke"):
        raise SystemExit("Use a separate Compose project ending in -smoke")
    compose = [
        "docker",
        "compose",
        "--env-file",
        ".env.production.example",
        "-f",
        "compose.production.yaml",
        "-p",
        args.project,
    ]

    def execute(*command: str, data: str | None = None) -> str:
        result = subprocess.run(  # noqa: S603 - fixed Docker CLI and local project only
            [*compose, *command],
            input=data,
            text=True,
            capture_output=True,
            timeout=90,
            check=False,
        )
        if result.returncode:
            raise RuntimeError(
                f"Compose command failed ({command[0]}); inspect local logs"
            )
        return result.stdout

    def http(
        path: str, *, method="GET", payload=None, token="", status=200, form=False
    ):
        headers = {}
        data = None
        if payload is not None:
            data = (urlencode(payload) if form else json.dumps(payload)).encode()
            headers["Content-Type"] = (
                "application/x-www-form-urlencoded" if form else "application/json"
            )
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(  # noqa: S310 - loopback HTTP(S) validated
            args.url + path, data, headers, method=method
        )
        try:
            response = urllib.request.urlopen(request, timeout=20)  # noqa: S310 - loopback validated
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            body = response.read()
            expected = status if isinstance(status, tuple) else (status,)
            if response.status not in expected:
                raise RuntimeError(
                    f"{method} {path}: expected {status}, got {response.status}"
                )
            return body, response.headers

    def api(path: str, **kwargs):
        body, _ = http("/api/v1" + path, **kwargs)
        return json.loads(body)

    password = (args.secrets / "LOCAL_ADMIN_PASSWORD").read_text().strip()
    bootstrap = """
import os, sys
os.environ['ORDERFLOW_ADMIN_PASSWORD'] = sys.stdin.read()
from app.cli import bootstrap_admin
from app.core.config import get_settings
from app.core.logging import configure_logging
configure_logging(get_settings())
code = bootstrap_admin(email='admin@smoke.example.com', full_name='Smoke Admin')
assert code in (0, 1)
"""
    execute("exec", "-T", "api", "python", "-c", bootstrap, data=password)
    index, headers = http("/")
    assert b'<div id="root">' in index
    assert headers["Cache-Control"] == "no-store"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert "/" not in headers["Server"]
    for route in ("/orders", "/orders/1", "/admin/users"):
        assert http(route)[0] == index
    assets = re.findall(rb'(?:src|href)="(/assets/[^\"]+)"', index)
    assert len(assets) >= 2
    for asset in assets:
        body, asset_headers = http(asset.decode())
        assert body
        assert "immutable" in asset_headers["Cache-Control"]
        assert b"http://localhost:8000" not in body
        if asset.endswith(b".js"):
            assert b"/api/v1" in body
    http("/assets/missing.js", status=404)
    http("/healthz")
    http("/health/live")
    ready, _ = http("/health/ready")
    assert json.loads(ready)["status"] == "ready"
    api("/does-not-exist", status=404)
    api("/customers", status=401)
    token = api(
        "/auth/login",
        method="POST",
        form=True,
        payload={
            "username": "admin@smoke.example.com",
            "password": password,
        },
    )["access_token"]
    assert api("/auth/me", token=token)["role"] == "admin"
    suffix = secrets.token_hex(5)
    customer = api(
        "/customers",
        method="POST",
        token=token,
        status=201,
        payload={
            "name": "Container Smoke",
            "email": f"customer-{suffix}@example.com",
        },
    )
    product = api(
        "/products",
        method="POST",
        token=token,
        status=201,
        payload={
            "sku": "SMOKE-" + suffix,
            "name": "Smoke Product",
            "price": "19.90",
            "stock": 10,
        },
    )
    order = api(
        "/orders",
        method="POST",
        token=token,
        status=201,
        payload={
            "customer_id": customer["id"],
            "items": [{"product_id": product["id"], "quantity": 1}],
        },
    )
    payment = api(
        "/payments",
        method="POST",
        token=token,
        status=201,
        payload={
            "order_id": order["id"],
            "provider": "manual",
        },
    )
    for path in (
        "/statistics/overview",
        "/customers",
        "/products",
        "/orders",
        "/payments",
        "/users",
    ):
        api(path, token=token)
    api(f"/payments/{payment['id']}/refunds", token=token)
    viewer_password = secrets.token_hex(24)
    viewer_email = f"viewer-{suffix}@example.com"
    api(
        "/users",
        method="POST",
        status=201,
        token=token,
        payload={
            "full_name": "Smoke Viewer",
            "email": viewer_email,
            "password": viewer_password,
            "role": "viewer",
        },
    )
    viewer_token = api(
        "/auth/login",
        method="POST",
        form=True,
        payload={
            "username": viewer_email,
            "password": viewer_password,
        },
    )["access_token"]
    api("/users", token=viewer_token, status=403)
    api(
        "/customers",
        method="POST",
        token=viewer_token,
        status=403,
        payload={
            "name": "Forbidden",
            "email": "forbidden@example.com",
        },
    )
    report = """
from app.tasks.reports import generate_order_report
result = generate_order_report.delay()
assert result.get(timeout=30)['total_orders'] >= 1
print('Celery task completed through Redis')
"""
    assert "completed" in execute("exec", "-T", "api", "python", "-c", report)
    execute("restart", "postgres", "redis")
    execute("up", "-d", "--wait", "--wait-timeout", "120")
    assert api(f"/orders/{order['id']}", token=token)["id"] == order["id"]
    execute("stop", "api")
    try:
        body, _ = http("/api/v1/customers", status=(502, 504))
        assert body != index
        http("/healthz")
        assert http("/orders")[0] == index
    finally:
        execute("start", "api")
        execute("up", "-d", "--wait", "--wait-timeout", "120")
    api("/auth/me", token="invalid-token", status=401)  # noqa: S106 - intentionally invalid JWT
    print(
        "PASS: SPA, assets, headers, health, JWT, roles, domain screens, Celery, persistence, API outage"
    )


if __name__ == "__main__":
    main()
