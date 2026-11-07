"""Tiny local web server for SubKeep (standard library only).

It listens on 127.0.0.1 so the app is only reachable from this computer.
"""

from __future__ import annotations

import csv
import io
import json
import mimetypes
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__
from .db import Store, ValidationError

STATIC_DIR = Path(__file__).parent / "static"
MAX_BODY = 5 * 1024 * 1024  # 5 MB is plenty for a backup file
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]"}

ROUTES: list[tuple[str, re.Pattern, str]] = []


def route(method: str, pattern: str):
    def register(fn):
        ROUTES.append((method, re.compile(f"^{pattern}$"), fn.__name__))
        return fn
    return register


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def to_csv(rows: list[dict], columns: list[str]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    for r in rows:
        values = []
        for c in columns:
            v = r.get(c)
            v = "" if v is None else str(v)
            # Stop spreadsheet apps from running cell text as a formula.
            if v[:1] in ("=", "+", "-", "@", "\t", "\r") and not _is_number(v):
                v = "'" + v
            values.append(v)
        writer.writerow(values)
    return buf.getvalue()


def _is_number(v: str) -> bool:
    try:
        float(v)
        return True
    except ValueError:
        return False


class Handler(BaseHTTPRequestHandler):
    server_version = f"SubKeep/{__version__}"
    store: Store  # set by make_server

    # Quieter console: only log failed requests.
    def log_request(self, code="-", size="-"):
        if str(int(code) if isinstance(code, HTTPStatus) else code).startswith(("4", "5")):
            super().log_request(code, size)

    # ------------------------------------------------------------ plumbing
    def _host_ok(self) -> bool:
        """Reject requests whose Host isn't local (blocks DNS-rebinding attacks)."""
        host = (self.headers.get("Host") or "").lower()
        if host.startswith("["):
            hostname = host.split("]")[0] + "]"
        else:
            hostname = host.split(":")[0]
        return hostname in ALLOWED_HOSTS

    def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
            "frame-ancestors 'none'; form-action 'none'",
        )
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload):
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    def _read_json(self):
        # Requiring a JSON content type means a malicious web page can't post to
        # this server with a plain HTML form (browsers preflight JSON requests).
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            raise ApiError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Requests must be JSON")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Bad Content-Length") from None
        if length > MAX_BODY:
            raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request is too large")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ApiError(HTTPStatus.BAD_REQUEST, "Body is not valid JSON") from None
        if not isinstance(data, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, "Body must be a JSON object")
        return data

    def _dispatch(self):
        if not self._host_ok():
            return self._json(HTTPStatus.FORBIDDEN, {"error": "Forbidden host"})
        url = urlparse(self.path)
        self.query = {k: v[0] for k, v in parse_qs(url.query).items()}
        path = url.path
        method = "GET" if self.command == "HEAD" else self.command

        if path.startswith("/api/"):
            for m, pattern, name in ROUTES:
                match = pattern.match(path)
                if match and m == method:
                    try:
                        return getattr(self, name)(*match.groups())
                    except ApiError as e:
                        return self._json(e.status, {"error": e.message})
                    except ValidationError as e:
                        return self._json(HTTPStatus.BAD_REQUEST, {"error": str(e)})
                    except Exception as e:  # pragma: no cover - last-resort guard
                        self.log_error("Unhandled error: %r", e)
                        return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Something went wrong"})
            return self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

        if method != "GET":
            return self._json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "Method not allowed"})
        return self._static(path)

    def _static(self, path: str):
        if path in ("", "/"):
            path = "/index.html"
        target = (STATIC_DIR / path.lstrip("/")).resolve()
        if STATIC_DIR.resolve() not in target.parents or not target.is_file():
            target = STATIC_DIR / "index.html"
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        self._send(HTTPStatus.OK, target.read_bytes(), ctype)

    do_GET = do_HEAD = do_POST = do_PUT = do_DELETE = lambda self: self._dispatch()

    # --------------------------------------------------------------- routes
    @route("GET", "/api/health")
    def health(self):
        self._json(200, {"ok": True, "version": __version__})

    @route("GET", "/api/summary")
    def summary(self):
        self._json(200, self.store.summary())

    @route("GET", "/api/settings")
    def get_settings(self):
        self._json(200, self.store.get_settings())

    @route("PUT", "/api/settings")
    def put_settings(self):
        self._json(200, self.store.update_settings(self._read_json()))

    @route("GET", "/api/categories")
    def list_categories(self):
        self._json(200, self.store.list_categories())

    @route("POST", "/api/categories")
    def create_category(self):
        self._json(201, self.store.create_category(self._read_json()))

    @route("DELETE", r"/api/categories/(\d+)")
    def delete_category(self, cat_id):
        self._deleted(self.store.delete_category(int(cat_id)))

    @route("GET", "/api/subscriptions")
    def list_subscriptions(self):
        self._json(200, self.store.list_subscriptions())

    @route("POST", "/api/subscriptions")
    def create_subscription(self):
        self._json(201, self.store.create_subscription(self._read_json()))

    @route("PUT", r"/api/subscriptions/(\d+)")
    def update_subscription(self, sub_id):
        self._found(self.store.update_subscription(int(sub_id), self._read_json()))

    @route("DELETE", r"/api/subscriptions/(\d+)")
    def delete_subscription(self, sub_id):
        self._deleted(self.store.delete_subscription(int(sub_id)))

    @route("GET", "/api/expenses")
    def list_expenses(self):
        self._json(200, self.store.list_expenses(self.query.get("month") or None))

    @route("POST", "/api/expenses")
    def create_expense(self):
        self._json(201, self.store.create_expense(self._read_json()))

    @route("PUT", r"/api/expenses/(\d+)")
    def update_expense(self, exp_id):
        self._found(self.store.update_expense(int(exp_id), self._read_json()))

    @route("DELETE", r"/api/expenses/(\d+)")
    def delete_expense(self, exp_id):
        self._deleted(self.store.delete_expense(int(exp_id)))

    @route("GET", "/api/calendar")
    def get_calendar(self):
        from datetime import date
        self._json(200, self.store.calendar(self.query.get("month") or date.today().strftime("%Y-%m")))

    @route("GET", "/api/export/json")
    def export_json(self):
        body = json.dumps(self.store.export_all(), indent=2).encode("utf-8")
        self._send(200, body, "application/json; charset=utf-8",
                   {"Content-Disposition": 'attachment; filename="subkeep-backup.json"'})

    @route("GET", "/api/export/subscriptions.csv")
    def export_subs_csv(self):
        cols = ["name", "cost", "cycle", "monthly_cost", "next_renewal", "status", "category",
                "payment_method", "url", "notes"]
        body = to_csv(self.store.list_subscriptions(), cols).encode("utf-8-sig")
        self._send(200, body, "text/csv; charset=utf-8",
                   {"Content-Disposition": 'attachment; filename="subkeep-subscriptions.csv"'})

    @route("GET", "/api/export/expenses.csv")
    def export_expenses_csv(self):
        cols = ["spent_on", "description", "amount", "category", "payment_method", "notes"]
        body = to_csv(self.store.list_expenses(), cols).encode("utf-8-sig")
        self._send(200, body, "text/csv; charset=utf-8",
                   {"Content-Disposition": 'attachment; filename="subkeep-expenses.csv"'})

    @route("POST", "/api/import/json")
    def import_json(self):
        self._json(200, self.store.import_all(self._read_json()))

    # -------------------------------------------------------------- helpers
    def _found(self, item):
        if item is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "Not found")
        self._json(200, item)

    def _deleted(self, ok: bool):
        if not ok:
            raise ApiError(HTTPStatus.NOT_FOUND, "Not found")
        self._json(200, {"deleted": True})


def make_server(store: Store, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"store": store})
    return LocalServer((host, port), handler)


class LocalServer(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets two programs share a port; we want a clean error instead.
    allow_reuse_address = False
    daemon_threads = True
