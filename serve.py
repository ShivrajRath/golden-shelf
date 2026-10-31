#!/usr/bin/env python3
"""Serve the Golden Shelf Kids site with LIVE library availability.

Unlike `python3 -m http.server`, this server also answers
`/api/library-availability?isbn=...` by querying the library catalogs on
every request, so the book detail pages show current status instead of a
build-time snapshot.

Usage:
    python3 serve.py                 # http://127.0.0.1:8000
    python3 serve.py --port 8765
    python3 serve.py --cache-ttl 0    # never cache (fully live)
    python3 serve.py --cache-ttl 60   # cache for 60s to be kind to catalogs
"""

import argparse
import http.server
import json
import os
import sys
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from scripts.update_library_availability import (  # noqa: E402
    AVAILABILITY_FILE,
    LIBRARIES_FILE,
    FETCHERS,
    clean_text,
    fill_query,
    is_valid_isbn13,
    load_json,
    normalize_isbn,
    now_iso,
)

# Reuse the same HTTP helpers by importing the module object.
import scripts.update_library_availability as updater  # noqa: E402

CACHE_TTL = 0
CACHE = {}
CACHE_LOCK = threading.Lock()


def load_libraries():
    data = load_json(LIBRARIES_FILE, {})
    libs = data.get("libraries") if isinstance(data, dict) else data
    return [lib for lib in (libs or []) if isinstance(lib, dict)]


def library_search_url(lib, isbn, title, author):
    book = {"isbn_13": isbn, "title": title, "author": author}
    return updater.fill_query(lib.get("search_url") or lib.get("catalog_url") or "", book)


def live_entry_for_blocked(lib, isbn, title, author):
    url = library_search_url(lib, isbn, title, author)
    return {
        "checked_at": None,
        "source": "Catalog blocks automated requests",
        "live": False,
        "blocked": True,
        "items": [
            {
                "format": "Book / eBook",
                "status": "unknown",
                "available_copies": None,
                "total_copies": None,
                "holds": 0,
                "action": "search",
                "url": url,
                "is_digital": False,
            }
        ],
    }


def fetch_one(lib, book, timeout):
    catalog_type = lib.get("catalog_type")
    if catalog_type not in FETCHERS or not lib.get("auto_update", False):
        return lib.get("id"), live_entry_for_blocked(
            lib,
            normalize_isbn(book.get("isbn_13")),
            clean_text(book.get("title")),
            clean_text(book.get("author")),
        ), None
    fetcher = FETCHERS[catalog_type]
    try:
        entry, error = fetcher(lib, book, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        entry, error = None, f"{type(exc).__name__}: {exc}"
    if entry:
        entry = dict(entry)
        entry["live"] = True
        entry["blocked"] = False
        entry.setdefault("source", "Live catalog")
        return lib.get("id"), entry, None
    url = library_search_url(lib, book.get("isbn_13"), book.get("title"), book.get("author"))
    not_found = bool(error) and any(
        token in str(error).lower()
        for token in ("no results", "no catalog record", "did not match", "no matching")
    )
    if not_found:
        # Successfully checked — the book simply isn't in this catalog.
        entry = {
            "checked_at": now_iso(),
            "source": f"{lib.get('system') or 'Library'} catalog",
            "live": True,
            "blocked": False,
            "error": None,
            "items": [
                {
                    "format": "Book / eBook",
                    "status": "unknown",
                    "available_copies": None,
                    "total_copies": None,
                    "holds": 0,
                    "action": "search",
                    "url": url,
                    "is_digital": False,
                }
            ],
        }
        return lib.get("id"), entry, None
    # Real failure (network, parse, challenge) — keep a working Check link.
    fallback = {
        "checked_at": None,
        "source": f"Live check failed: {error}",
        "live": False,
        "blocked": False,
        "error": error,
        "items": [
            {
                "format": "Book / eBook",
                "status": "unknown",
                "available_copies": None,
                "total_copies": None,
                "holds": 0,
                "action": "search",
                "url": url,
                "is_digital": False,
            }
        ],
    }
    return lib.get("id"), fallback, error


def live_availability(isbn, title, author, timeout):
    cache_key = normalize_isbn(isbn)
    now = time.time()
    if CACHE_TTL > 0:
        with CACHE_LOCK:
            cached = CACHE.get(cache_key)
            if cached and (now - cached[0]) < CACHE_TTL:
                return cached[1]
    book = {"isbn_13": isbn, "title": title, "author": author, "slug": ""}
    libraries = load_libraries()
    out_libs = []
    errors = []
    with ThreadPoolExecutor(max_workers=min(8, len(libraries) or 1)) as pool:
        futures = {pool.submit(fetch_one, lib, book, timeout): lib for lib in libraries}
        results = {}
        for fut in as_completed(futures):
            lib_id, entry, err = fut.result()
            results[lib_id] = (entry, err)
            if err:
                errors.append(err)
    # Preserve library order from libraries.json.
    for lib in libraries:
        entry, err = results.get(lib.get("id"), (None, "missing"))
        if entry is None:
            entry = live_entry_for_blocked(lib, isbn, title, author)
        # Attach display metadata.
        enriched = {
            "id": lib.get("id"),
            "name": lib.get("name"),
            "city": lib.get("city"),
            "system": lib.get("system"),
            "homepage": lib.get("homepage") or lib.get("catalog_url"),
            "catalog_url": lib.get("catalog_url"),
            "digital_url": lib.get("digital_url"),
            "digital_label": lib.get("digital_label"),
            "auto_update": bool(lib.get("auto_update")),
            "checked_at": entry.get("checked_at"),
            "source": entry.get("source"),
            "live": bool(entry.get("live")),
            "blocked": bool(entry.get("blocked")),
            "error": entry.get("error"),
            "items": entry.get("items") or [],
        }
        out_libs.append(enriched)

    payload = {
        "isbn": normalize_isbn(isbn),
        "title": clean_text(title),
        "author": clean_text(author),
        "checked_at": now_iso(),
        "libraries": out_libs,
    }
    if CACHE_TTL > 0:
        with CACHE_LOCK:
            CACHE[cache_key] = (now, payload)
    return payload


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/api/library-availability":
            qs = urllib.parse.parse_qs(parsed.query)
            isbn = (qs.get("isbn") or [""])[0]
            title = (qs.get("title") or [""])[0]
            author = (qs.get("author") or [""])[0]
            if not isbn:
                self._send_json(400, {"error": "missing isbn"})
                return
            timeout = getattr(self.server, "api_timeout", 15)
            try:
                payload = live_availability(isbn, title, author, timeout)
            except Exception as exc:  # noqa: BLE001
                self._send_json(500, {"error": f"{type(exc).__name__}: {exc}"})
                return
            self._send_json(200, payload)
            return
        if parsed.path == "/api/health":
            self._send_json(200, {"ok": True, "time": now_iso()})
            return
        super().do_GET()

    def log_message(self, fmt, *args):
        # Keep output quiet but still show API calls.
        msg = fmt % args
        if "/api/" in msg:
            sys.stderr.write("[serve] " + msg + "\n")


def main():
    global CACHE_TTL
    parser = argparse.ArgumentParser(description="Golden Shelf Kids live server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--cache-ttl", type=int, default=0,
                        help="Seconds to cache live results per ISBN (0 = fully live)")
    parser.add_argument("--timeout", type=int, default=15,
                        help="Per-catalog HTTP timeout in seconds")
    args = parser.parse_args()
    CACHE_TTL = max(0, args.cache_ttl)

    httpd = http.server.ThreadingHTTPServer((args.host, args.port), Handler)
    httpd.api_timeout = args.timeout
    httpd.allow_reuse_address = True
    print(f"Golden Shelf Kids live server: http://{args.host}:{args.port}")
    print(f"  cache TTL: {CACHE_TTL}s   catalog timeout: {args.timeout}s")
    print("  Press Ctrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
EOF