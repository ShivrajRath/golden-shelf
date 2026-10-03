# AGENTS.md

Static GitHub Pages site + Python curation script. No build, bundler, tests, lint, or dependencies.

## Structure
- `index.html` — entire frontend (Tailwind CDN + Alpine.js `bookShelf()`). Fetches `data/books.json` (`cache: 'no-cache'`); `books[0]` is the Featured Pick. `slugify()`/`bookSlug()`/`bookPageUrl()` build per-book links from the stored `slug` field and must stay in sync with `generate_pages.py:slugify()`.
- `script.py` — stdlib only (`urllib`, no pip install). Prepends a Friday batch of 5 entries (`BOOKS_PER_RUN`, env-overridable) to `data/books.json`, appends ISBN + title per book to `data/history.json`, then best-effort refreshes `books/` + `sitemap.xml` via `generate_pages`. Batch ids are `YYYY-MM-DD` + `YYYY-MM-DD-2…-5`. Fails fast with no partial save if any pick is invalid/duplicate.
- `generate_pages.py` — stdlib only. Renders evergreen `books/<slug>.html` (title-based `slug` stored on each entry, never the curation date; pre-rendered SEO/OG/JSON-LD so share previews work) + root `sitemap.xml` from `data/books.json`; deletes stale pages.
- `data/books.json` — newest-first. `id` is `YYYY-MM-DD` (first pick of a batch) or `YYYY-MM-DD-N` (batch siblings). `data/history.json` — flat `[isbn, title, ...]` list fed to the Gemini prompt for dedup.
- `.github/workflows/curate-book.yml` — Fridays `0 8 * * 5` + `workflow_dispatch`, Python 3.11. Runs `script.py` then `generate_pages.py`; commits `data/books.json` + `data/history.json` + `books/` + `sitemap.xml`.
- `.github/workflows/refresh-pages.yml` — manual only (`workflow_dispatch`). Rebuilds `books/` + `sitemap.xml` from `data/books.json` via `generate_pages.py` (no new picks, no API key needed); commits them if changed.

## Commands
```bash
export GEMINI_API_KEY="..."
python3 script.py
python3 -m http.server 8000  # required: file:// breaks fetch() of data/books.json
python3 -c "import json; json.load(open('data/books.json')); json.load(open('data/history.json'))"
```

## Gotchas
- `script.py` exits silently if any entry with today's batch `id` (`YYYY-MM-DD` or `YYYY-MM-DD-N`) already exists — delete/rename those entries to re-run for today. `BOOKS_PER_RUN=1 python3 script.py` forces a single-book run.
- Gemini model fallback is hardcoded: `gemini-3.8-flash` → `3.7-flash` → `3.6-flash`. Failures print HTTP body then try next.
- `cover_image_url` must be `https://covers.openlibrary.org/b/isbn/<ISBN_13>-L.jpg` (canonical; keep it). Frontend fallback chain at runtime: ISBN (`?default=false`) → title-keyed OL cover → `cover_ol_id` (`/b/id/...`, pinned by `script.py` when ISBN+title both miss) → inline SVG. `script.py:resolve_cover()` probes this at curation time and sets `cover_ol_id`; warn-only, never blocks the run.
- Selection criteria live in 3 places — keep in sync: `script.py` prompt, `README.md`, `index.html#how-we-select`. Rules: non-fiction, no True Crime, Goodreads ≥4.0 with ≥2500 ratings, <500 pages, post-2000 or evergreen classic, never duplicate `history.json`.
- Schedule is an internal growth mechanism (see workflow comment): never surface cron/schedule/countdown in the UI.
- Theme is gold: `amber-*` primary + `stone-*` neutrals on warm `bg-[#fdfbf7]`. Already migrated from `indigo/slate` — do not reintroduce them.
