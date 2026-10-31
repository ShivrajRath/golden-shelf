#!/usr/bin/env python3
"""Refresh the cached library availability used by the book pages.

The static site cannot query every library catalog from the browser (most
catalogs do not allow cross-origin requests), so availability is cached in
data/library-availability.json at build time.

Libraries whose catalogs expose their data in the HTML are refreshed
automatically:

  * BiblioCommons  (Frisco, Harris County)
  * SirsiDynix     (Plano)

The remaining libraries are linked from the book page and can be filled in
manually in data/library-availability.json.

Examples:
  python3 scripts/update_library_availability.py --slug sector-7
  python3 scripts/update_library_availability.py --isbn 9780395746561
  python3 scripts/update_library_availability.py --all --limit 50
  python3 scripts/update_library_availability.py --slug sector-7 --library frisco
"""

import argparse
import datetime as _dt
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOOKS_FILE = os.path.join(ROOT, "data", "books.json")
LIBRARIES_FILE = os.path.join(ROOT, "data", "libraries.json")
AVAILABILITY_FILE = os.path.join(ROOT, "data", "library-availability.json")

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

DIGITAL_FORMATS = {"EBOOK", "EAUDIOBOOK", "EMAGAZINE", "STREAMING_VIDEO"}
FORMAT_LABELS = {
    "BOOK": "Book",
    "PICTURE_BOOK": "Picture book",
    "LARGE_PRINT": "Large print",
    "PAPERBACK": "Paperback",
    "HARDCOVER": "Hardcover",
    "AUDIOBOOK": "Audiobook",
    "EBOOK": "eBook",
    "EAUDIOBOOK": "eAudiobook",
    "EMAGAZINE": "eMagazine",
    "DVD": "DVD",
    "BLU_RAY": "Blu-ray",
    "MUSIC_CD": "Music CD",
    "BOOK_CLUB_KIT": "Book club kit",
}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def now_iso():
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_isbn(value):
    return re.sub(r"[^0-9Xx]", "", str(value or "")).upper()


def is_valid_isbn13(value):
    s = normalize_isbn(value)
    if not re.fullmatch(r"\d{13}", s):
        return False
    total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(s[:12]))
    return (10 - total % 10) % 10 == int(s[12])


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as exc:
        print(f"Error: could not parse {path}: {exc}", file=sys.stderr)
        sys.exit(1)


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def http_get(url, headers=None, timeout=30):
    req_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "identity",
    }
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        charset = resp.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace")


def fill_query(template, book):
    isbn = normalize_isbn(book.get("isbn_13"))
    title = str(book.get("title") or "").strip()
    author = str(book.get("author") or "").strip()
    query = isbn if is_valid_isbn13(isbn) else f"{title} {author}".strip() or isbn or title
    return (
        str(template or "")
        .replace("{query}", urllib.parse.quote_plus(query))
        .replace("{isbn}", urllib.parse.quote_plus(isbn))
        .replace("{title}", urllib.parse.quote_plus(title))
        .replace("{author}", urllib.parse.quote_plus(author))
    )


def clean_text(value):
    return re.sub(r"\s+", " ", html.unescape(str(value or ""))).strip()


# ---------------------------------------------------------------------------
# BiblioCommons (Frisco, Harris County)
# ---------------------------------------------------------------------------

def extract_bibliocommons_state(page):
    match = re.search(
        r'<script type="application/json" data-iso-key="_0">(.*?)</script>',
        page,
        re.S,
    )
    if not match:
        return {}
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}


def bibliocommons_candidates(state):
    entities = state.get("entities") or {}
    bibs = entities.get("bibs") or state.get("bibs") or {}
    if not isinstance(bibs, dict):
        return []
    out = []
    for bib_id, bib in bibs.items():
        if not isinstance(bib, dict):
            continue
        info = bib.get("briefInfo") or {}
        availability = bib.get("availability") or {}
        policy = bib.get("policy") or {}
        if not info:
            continue
        out.append((str(bib_id), info, availability, policy))
    return out


def bibliocommons_item(lib, bib_id, info, availability, policy):
    available = int(availability.get("availableCopies") or 0)
    total = int(availability.get("totalCopies") or 0)
    holds = int(availability.get("heldCopies") or 0)
    fmt = str(info.get("format") or "BOOK").upper()
    is_digital = (
        str(availability.get("bibType") or "").upper() == "DIGITAL"
        or fmt in DIGITAL_FORMATS
    )
    if available > 0:
        status = "available"
    elif total > 0 or str(availability.get("status") or "").upper() == "UNAVAILABLE":
        status = "on_hold" if holds > 0 else "unavailable"
    else:
        status = "unknown"

    holdable = bool(policy.get("holdable", True))
    if status == "available":
        action = "borrow"
    elif status in ("unavailable", "on_hold") and holdable:
        action = "hold"
    else:
        action = "view"

    return {
        "format": FORMAT_LABELS.get(fmt, fmt.replace("_", " ").title()),
        "status": status,
        "available_copies": available,
        "total_copies": total,
        "holds": holds,
        "action": action,
        "url": f"{lib['catalog_url'].rstrip('/')}/v2/record/{urllib.parse.quote(str(bib_id))}",
        "is_digital": is_digital,
    }


def fetch_bibliocommons(lib, book, timeout=30):
    isbn = normalize_isbn(book.get("isbn_13"))
    title = clean_text(book.get("title")).lower()
    url = fill_query(lib.get("search_url"), book)
    page = http_get(url, timeout=timeout)
    state = extract_bibliocommons_state(page)
    candidates = bibliocommons_candidates(state)
    if not candidates and isbn and is_valid_isbn13(isbn):
        title_book = {"isbn_13": "", "title": book.get("title"), "author": book.get("author")}
        url = fill_query(lib.get("search_url"), title_book)
        page = http_get(url, timeout=timeout)
        state = extract_bibliocommons_state(page)
        candidates = bibliocommons_candidates(state)
    if not candidates:
        return None, "no results parsed"

    matching = []
    for bib_id, info, availability, policy in candidates:
        isbns = {normalize_isbn(v) for v in (info.get("isbns") or [])}
        if isbn and isbn in isbns:
            matching.append((bib_id, info, availability, policy))
    if not matching and title:
        matching = [
            (bib_id, info, availability, policy)
            for bib_id, info, availability, policy in candidates
            if clean_text(info.get("title")).lower() == title
        ]
    if not matching:
        return None, "ISBN/title did not match a catalog record"

    items = []
    for is_digital in (False, True):
        group = [
            c
            for c in matching
            if (
                str(c[2].get("bibType") or "").upper() == "DIGITAL"
                or str(c[1].get("format") or "").upper() in DIGITAL_FORMATS
            )
            == is_digital
        ]
        if not group:
            continue
        group.sort(
            key=lambda c: (
                int(c[2].get("availableCopies") or 0),
                int(c[2].get("totalCopies") or 0),
                1 if str(c[2].get("status") or "").upper() == "AVAILABLE" else 0,
            ),
            reverse=True,
        )
        items.append(bibliocommons_item(lib, *group[0]))

    if not items:
        return None, "no matching formats"
    return {
        "checked_at": now_iso(),
        "source": "BiblioCommons catalog",
        "items": items,
    }, None


# ---------------------------------------------------------------------------
# SirsiDynix Enterprise (Plano, Houston)
# ---------------------------------------------------------------------------

def sirsi_doc_id(page, isbn, title=""):
    """Return (source, numeric id) for the first matching result."""
    chunks = re.split(r'<div id="results_cell\d+"', page)
    fallback = None
    title_candidate = None
    title_lower = (title or "").lower()
    for chunk in chunks[1:]:
        match = re.search(r'value="([^"]+)"[^>]*id="da\d+"', chunk)
        if not match:
            continue
        value = html.unescape(match.group(1))
        if fallback is None:
            fallback = value
        if isbn and isbn in normalize_isbn(chunk):
            return parse_sirsi_doc_value(value)
        if title_candidate is None and title_lower and title_lower in chunk.lower():
            title_candidate = value
    if title_candidate:
        parsed = parse_sirsi_doc_value(title_candidate)
        if parsed:
            return parsed
    if fallback:
        return parse_sirsi_doc_value(fallback)
    return None


def parse_sirsi_doc_value(value):
    match = re.match(r"ent://([^/]+)/\d+/([^:]+):(\d+)", value)
    if not match:
        return None
    return match.group(2), match.group(3)


def sirsi_urls(lib, source, doc_id):
    base = str(lib["catalog_url"]).rstrip("/")
    encoded = f"ent:$002f$002f{source}$002f0$002f{source}:{doc_id}"
    detail = f"{base}/search/detailnonmodal/{encoded}/one"
    availability = (
        f"{base}/search/detailnonmodal.detail.detailavailabilityaccordions.boundwithzone:"
        f"lookupavailability/{encoded}/ILS/0/false/LIBRARY$002cCALLNUMBER"
        f"?d=ent%3A%2F%2F{source}%2F0%2F{source}%3A{doc_id}%7E%7E0"
    )
    return detail, availability


def sirsi_format(detail_page):
    text = clean_text(re.sub(r"<[^>]+>", " ", detail_page))
    if re.search(r"\bAudiobook\b", text, re.I):
        return "Audiobook"
    if re.search(r"\bLarge Print\b", text, re.I):
        return "Large print"
    if re.search(r"\bDVD\b", text, re.I):
        return "DVD"
    if re.search(r"\bBlu-ray\b", text, re.I):
        return "Blu-ray"
    return "Book"


def sirsi_availability_payload(page):
    try:
        data = json.loads(page)
    except json.JSONDecodeError:
        return {}
    for init in data.get("inits") or []:
        for script in init.get("evalScript") or []:
            match = re.search(r"updateWebServiceFields\((\{.*\})\)", script, re.S)
            if match:
                try:
                    return json.loads(match.group(1))
                except json.JSONDecodeError:
                    continue
    return {}


def fetch_sirsi(lib, book, timeout=30):
    isbn = normalize_isbn(book.get("isbn_13"))
    title = clean_text(book.get("title"))
    search_url = fill_query(lib.get("search_url"), book)
    search_page = http_get(search_url, timeout=timeout)
    doc = sirsi_doc_id(search_page, isbn, title)
    if not doc and isbn and is_valid_isbn13(isbn):
        title_book = {"isbn_13": "", "title": book.get("title"), "author": book.get("author")}
        search_url = fill_query(lib.get("search_url"), title_book)
        search_page = http_get(search_url, timeout=timeout)
        doc = sirsi_doc_id(search_page, "", title)
    if not doc:
        return None, "no catalog record found"

    source, doc_id = doc
    detail_url, availability_url = sirsi_urls(lib, source, doc_id)
    detail_page = http_get(detail_url, timeout=timeout)
    payload = sirsi_availability_payload(
        http_get(
            availability_url,
            headers={"X-Requested-With": "XMLHttpRequest"},
            timeout=timeout,
        )
    )
    if not payload:
        return None, "availability endpoint returned no data"

    def as_int(key):
        try:
            return int(str(payload.get(key) or "0").strip() or 0)
        except ValueError:
            return 0

    available = as_int("availableCount")
    total = as_int("copyCount")
    holds = as_int("holdCount")
    holdable = bool(payload.get("holdable", True))

    if available > 0:
        status = "available"
    elif total > 0:
        status = "on_hold" if holds > 0 else "unavailable"
    else:
        status = "unknown"

    if status == "available":
        action = "borrow"
    elif status in ("unavailable", "on_hold") and holdable:
        action = "hold"
    else:
        action = "view"

    return {
        "checked_at": now_iso(),
        "source": "SirsiDynix Enterprise catalog",
        "items": [
            {
                "format": sirsi_format(detail_page),
                "status": status,
                "available_copies": available,
                "total_copies": total,
                "holds": holds,
                "action": action,
                "url": detail_url,
                "is_digital": False,
            }
        ],
    }, None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

FETCHERS = {
    "bibliocommons": fetch_bibliocommons,
    "sirsi": fetch_sirsi,
}


def select_books(books, args):
    if args.isbn:
        isbn = normalize_isbn(args.isbn)
        for book in books:
            if normalize_isbn(book.get("isbn_13")) == isbn:
                return [book]
        return [
            {
                "title": args.title or isbn,
                "author": "",
                "isbn_13": isbn,
                "slug": isbn,
            }
        ]
    if args.slug:
        for book in books:
            if str(book.get("slug") or "") == args.slug:
                return [book]
        return []
    if args.all:
        selected = [b for b in books if normalize_isbn(b.get("isbn_13"))]
        if args.limit:
            selected = selected[: args.limit]
        return selected
    return []


def main():
    parser = argparse.ArgumentParser(
        description="Refresh cached public-library availability for book pages."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--isbn", help="ISBN-13 to refresh")
    group.add_argument("--slug", help="Book slug to refresh (e.g. sector-7)")
    group.add_argument(
        "--all",
        action="store_true",
        help="Refresh every book that has an ISBN (use --limit to keep it small)",
    )
    parser.add_argument(
        "--library",
        action="append",
        default=[],
        help="Only refresh this library id (repeatable)",
    )
    parser.add_argument("--title", help="Title to use when --isbn is not in books.json")
    parser.add_argument("--limit", type=int, help="Maximum books to process with --all")
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Seconds between catalog requests (default: 1.0)",
    )
    parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout in seconds")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change")
    args = parser.parse_args()

    books = load_json(BOOKS_FILE, [])
    libraries_data = load_json(LIBRARIES_FILE, {})
    libraries = libraries_data.get("libraries") or []
    availability = load_json(AVAILABILITY_FILE, {"version": 1, "books": {}})
    availability.setdefault("version", 1)
    availability.setdefault("books", {})

    wanted_ids = {lib_id.strip() for lib_id in args.library if lib_id.strip()}
    selected_libraries = [
        lib
        for lib in libraries
        if (not wanted_ids or lib.get("id") in wanted_ids)
        and lib.get("catalog_type") in FETCHERS
        and lib.get("auto_update", False)
    ]
    if not selected_libraries:
        print("No auto-update libraries selected. Check data/libraries.json.")
        return 0

    selected_books = select_books(books, args)
    if not selected_books:
        print("No matching book found.")
        return 1

    print(
        f"Refreshing {len(selected_books)} book(s) across "
        f"{len(selected_libraries)} library catalog(s)…"
    )
    updated = 0
    failed = 0
    request_count = 0

    for index, book in enumerate(selected_books, 1):
        isbn = normalize_isbn(book.get("isbn_13"))
        title = clean_text(book.get("title")) or isbn
        if not isbn:
            print(f"[{index}/{len(selected_books)}] {title}: skipped (no ISBN)")
            continue
        print(f"[{index}/{len(selected_books)}] {title} ({isbn})")
        existing_entry = availability["books"].get(isbn) or {}
        book_libraries = dict(existing_entry.get("libraries") or {})

        for lib in selected_libraries:
            fetcher = FETCHERS[lib["catalog_type"]]
            if args.dry_run:
                print(f"  {lib['name']}: would refresh")
                continue
            try:
                if request_count and args.delay:
                    time.sleep(args.delay)
                request_count += 1
                entry, error = fetcher(lib, book, timeout=args.timeout)
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
                entry, error = None, str(exc)
            except Exception as exc:  # noqa: BLE001 - keep one bad catalog from stopping a batch
                entry, error = None, f"{type(exc).__name__}: {exc}"

            if entry:
                book_libraries[lib["id"]] = entry
                updated += 1
                summary = ", ".join(
                    f"{item['format']}: {item['status']}"
                    for item in entry.get("items", [])
                )
                print(f"  {lib['name']}: {summary}")
            else:
                failed += 1
                print(f"  {lib['name']}: skipped ({error})")

        if book_libraries:
            availability["books"][isbn] = {
                "title": title,
                "libraries": book_libraries,
            }

        if not args.dry_run and index % 10 == 0:
            availability["updated"] = now_iso()
            save_json(AVAILABILITY_FILE, availability)
            print(f"  …saved progress ({index} books)")

    if args.dry_run:
        print("Dry run — no files written.")
        return 0

    availability["updated"] = now_iso()
    save_json(AVAILABILITY_FILE, availability)
    print(
        f"Done. {updated} library record(s) updated, {failed} skipped. "
        f"Wrote {os.path.relpath(AVAILABILITY_FILE, ROOT)}."
    )
    print("Run `python3 generate_pages.py` to rebuild the book pages.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
