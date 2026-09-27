# AGENTS.md

Static GitHub Pages site + Python curation script. No build, bundler, tests, lint, or dependencies.

## Structure
- `index.html` — entire frontend (Tailwind CDN + Alpine.js `bookShelf()`). Fetches `data/books.json` (`cache: 'no-cache'`); `books[0]` is the Featured Pick. `slugify()`/`bookSlug()`/`bookPageUrl()` build per-book links from the stored `slug` field and must stay in sync with `generate_pages.py:slugify()`.
- `script.py` — stdlib only (`urllib`, no pip install). Prepends one entry to `data/books.json`, appends ISBN + title to `data/history.json`, then best-effort refreshes `books/` + `sitemap.xml` via `generate_pages`.
- `generate_pages.py` — stdlib only. Renders evergreen `books/<slug>.html` (title-based `slug` stored on each entry, never the curation date; pre-rendered SEO/OG/JSON-LD so share previews work) + root `sitemap.xml` from `data/books.json`; deletes stale pages.
- `data/books.json` — newest-first, 178 entries. `id` is `YYYY-MM-DD`. `data/history.json` — flat `[isbn, title, ...]` list fed to the Gemini prompt for dedup.
- `.github/workflows/curate-book.yml` — daily `0 8 * * *` + `workflow_dispatch`, Python 3.11. Runs `script.py` then `generate_pages.py`; commits `data/books.json` + `data/history.json` + `books/` + `sitemap.xml`.

## Commands
```bash
export GEMINI_API_KEY="..."
python3 script.py
python3 -m http.server 8000  # required: file:// breaks fetch() of data/books.json
python3 -c "import json; json.load(open('data/books.json')); json.load(open('data/history.json'))"
```

## Gotchas
- `script.py` exits silently if an entry with today's `id` already exists — delete/rename that entry to re-run for today.
- Gemini model fallback is hardcoded: `gemini-2.5-flash` → `2.0-flash` → `1.5-flash`. Failures print HTTP body then try next.
- `cover_image_url` must be `https://covers.openlibrary.org/b/isbn/<ISBN_13>-L.jpg` (canonical; keep it). Frontend fallback chain at runtime: ISBN (`?default=false`) → title-keyed OL cover → `cover_ol_id` (`/b/id/...`, pinned by `script.py` when ISBN+title both miss) → inline SVG. `script.py:resolve_cover()` probes this at curation time and sets `cover_ol_id`; warn-only, never blocks the run.
- Selection criteria live in 3 places — keep in sync: `script.py` prompt, `README.md`, `index.html#how-we-select`. Rules: non-fiction, no True Crime, Goodreads ≥4.0 with ≥2500 ratings, <500 pages, post-2000 or evergreen classic, never duplicate `history.json`.
- Schedule is an internal growth mechanism (see workflow comment): never surface cron/schedule/countdown in the UI.
- Theme is gold: `amber-*` primary + `stone-*` neutrals on warm `bg-[#fdfbf7]`. Already migrated from `indigo/slate` — do not reintroduce them.
