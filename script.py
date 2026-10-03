#!/usr/bin/env python3
import os
import json
import re
import datetime
import urllib.request
import urllib.error
import urllib.parse
import sys

import generate_pages

DATA_DIR = "data"
BOOKS_FILE = os.path.join(DATA_DIR, "books.json")
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")

# Friday batch size. Override with BOOKS_PER_RUN env var for manual runs
# (e.g. BOOKS_PER_RUN=1 python3 script.py). The workflow runs with the default.
try:
    BOOKS_PER_RUN = max(1, int(os.environ.get("BOOKS_PER_RUN", "5")))
except ValueError:
    BOOKS_PER_RUN = 5

def load_json(path, default):
    # Fail fast on corrupt data: returning `default` here would let a later
    # save overwrite the full file with a single entry (total data loss).
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"Error: {path} exists but could not be loaded ({e}). Refusing to continue to avoid overwriting it.")
        sys.exit(1)
    if not isinstance(data, list):
        print(f"Error: {path} must contain a JSON array. Refusing to continue to avoid overwriting it.")
        sys.exit(1)
    return data

def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def call_gemini_api(prompt, api_key):
    models = ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite"]
    
    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt}
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.7,
                "responseMimeType": "application/json"
            }
        }
        
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        
        try:
            print(f"Attempting generation with model {model}...")
            with urllib.request.urlopen(req, timeout=60) as response:
                result = json.loads(response.read().decode("utf-8"))
                # Extract text from Gemini response structure
                candidates = result.get("candidates", [])
                if candidates:
                    content = candidates[0].get("content", {})
                    parts = content.get("parts", [])
                    if parts:
                        text = parts[0].get("text", "")
                        return text
        except urllib.error.HTTPError as e:
            try:
                error_body = e.read().decode("utf-8", "replace")
            except Exception:
                error_body = ""
            print(f"Model {model} failed with HTTPError {e.code}: {e.reason}. Body: {error_body}")
        except Exception as e:
            print(f"Model {model} failed with exception: {e}")
            
    raise RuntimeError("All Gemini model generation attempts failed.")

def normalize_isbn(value):
    return str(value or "").strip().replace("-", "").replace(" ", "")

def isbn13_ok(value):
    """True if value is a real ISBN-13 (length, digits, and checksum).

    Gemini hallucinates near-miss ISBNs (e.g. Atomic Habits ...291 instead
    of ...292). An ISBN-only WorldCat/Goodreads/cover query with a bad
    checksum returns zero results, so reject these before saving.
    """
    s = normalize_isbn(value)
    if len(s) != 13 or not s.isdigit():
        return False
    total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(s[:12]))
    return (10 - total % 10) % 10 == int(s[12])

def build_library_lookup(book_data):
    """Deterministic library URLs (never trust the model for links).

    Past runs asked Gemini for these and it returned WorldCat for both
    buttons (identical destinations) with ISBN-only queries. Compute them
    instead: WorldCat = global holdings by ISBN. Overwrites whatever the
    model returned.
    """
    isbn = normalize_isbn(book_data.get("isbn_13"))
    title = str(book_data.get("title", "") or "").strip()
    author = str(book_data.get("author", "") or "").strip()
    title_author = f"{title} {author}".strip()
    book_data["library_lookup"] = {
        "goodreads_url": "https://www.goodreads.com/search?q=" + urllib.parse.quote_plus(isbn or title_author),
        "worldcat_url": "https://search.worldcat.org/search?q=" + urllib.parse.quote_plus(isbn or title_author),
    }
    return book_data["library_lookup"]

def validate_book_data(book_data, history_list, expected_id):
    """Enforce the selection criteria + output schema. Returns a list of error strings."""
    import datetime as _dt
    errors = []
    if not isinstance(book_data, dict):
        return ["Top-level JSON must be an object."]

    isbn = normalize_isbn(book_data.get("isbn_13"))
    if len(isbn) != 13 or not isbn.isdigit():
        errors.append(f"isbn_13 must be a 13-digit string without hyphens, got {book_data.get('isbn_13')!r}.")
    elif not isbn13_ok(isbn):
        errors.append(f"isbn_13 checksum invalid ({book_data.get('isbn_13')!r} is not a real ISBN-13; catalog and cover links would 404).")
    else:
        book_data["isbn_13"] = isbn

    title = book_data.get("title")
    if not isinstance(title, str) or not title.strip():
        errors.append("title must be a non-empty string.")
    author = book_data.get("author")
    if not isinstance(author, str) or not author.strip():
        errors.append("author must be a non-empty string.")

    year = book_data.get("publication_year")
    current_year = _dt.date.today().year
    if isinstance(year, bool) or not isinstance(year, int) or not 1800 <= year <= current_year:
        errors.append(f"publication_year must be an integer between 1800 and {current_year}, got {year!r}.")

    pages = book_data.get("page_count")
    if isinstance(pages, bool) or not isinstance(pages, int) or not 1 <= pages < 500:
        errors.append(f"page_count must be an integer strictly under 500, got {pages!r}.")

    tags = book_data.get("genres_tags")
    if not isinstance(tags, list) or not tags or any(not isinstance(t, str) or not t.strip() for t in tags):
        errors.append("genres_tags must be a non-empty array of non-empty strings.")
    elif any(t.strip().lower().replace("-", " ") in ("true crime", "truecrime") for t in tags):
        errors.append("True Crime is excluded by the selection criteria.")

    goodreads = book_data.get("goodreads")
    if not isinstance(goodreads, dict):
        errors.append("goodreads must be an object with rating, ratings_count, review_summary_highlights.")
    else:
        rating = goodreads.get("rating")
        if isinstance(rating, bool) or not isinstance(rating, (int, float)) or not 4.0 <= float(rating) <= 5.0:
            errors.append(f"goodreads.rating must be between 4.0 and 5.0, got {rating!r}.")
        count = goodreads.get("ratings_count")
        if isinstance(count, bool) or not isinstance(count, int) or count < 2500:
            errors.append(f"goodreads.ratings_count must be an integer >= 2500, got {count!r}.")
        highlights = goodreads.get("review_summary_highlights")
        if not isinstance(highlights, list) or len(highlights) < 2 or any(not isinstance(h, str) or not h.strip() for h in highlights):
            errors.append("goodreads.review_summary_highlights must be an array of at least 2 non-empty strings.")

    expected_cover = f"https://covers.openlibrary.org/b/isbn/{normalize_isbn(book_data.get('isbn_13'))}-L.jpg"
    if book_data.get("cover_image_url") != expected_cover:
        errors.append(f"cover_image_url must be exactly {expected_cover!r}.")

    # Optional data-driven cover fallback, set by resolve_cover() below (never
    # by the model). Lets the frontend skip straight to a known-good Open
    # Library cover ID without an index.html edit (the daily workflow only
    # commits data/books.json + data/history.json).
    cover_ol_id = book_data.get("cover_ol_id")
    if cover_ol_id is not None and (
        isinstance(cover_ol_id, bool) or not isinstance(cover_ol_id, int) or cover_ol_id <= 0
    ):
        errors.append(f"cover_ol_id must be a positive integer cover ID or omitted, got {cover_ol_id!r}.")

    if not isinstance(book_data.get("one_sentence_hook"), str) or not book_data["one_sentence_hook"].strip():
        errors.append("one_sentence_hook must be a non-empty string.")

    summary = book_data.get("summary")
    if not isinstance(summary, dict):
        errors.append("summary must be an object with overview and key_takeaways.")
    else:
        if not isinstance(summary.get("overview"), str) or not summary["overview"].strip():
            errors.append("summary.overview must be a non-empty string.")
        takeaways = summary.get("key_takeaways")
        if not isinstance(takeaways, list) or len(takeaways) != 3 or any(not isinstance(t, str) or not t.strip() for t in takeaways):
            errors.append("summary.key_takeaways must be an array of exactly 3 non-empty strings.")

    lookup = book_data.get("library_lookup")
    if not isinstance(lookup, dict):
        errors.append("library_lookup must be an object with goodreads_url, worldcat_url.")
    else:
        for key in ("goodreads_url", "worldcat_url"):
            url = lookup.get(key)
            if not isinstance(url, str) or not url.strip() or not url.startswith("http"):
                errors.append(f"library_lookup.{key} must be a non-empty http(s) URL.")

    if book_data.get("id") != expected_id:
        errors.append(f"id must be {expected_id!r}.")

    slug = book_data.get("slug")
    if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        errors.append(f"slug must be a lowercase kebab-case evergreen URL slug, got {slug!r}.")

    return errors

COVER_TIMEOUT = 10
COVER_UA = {"User-Agent": "GoldenShelf/1.0 (daily curation; contact via repo)"}

def ol_cover_ok(url):
    """True if an Open Library cover URL resolves to a real image.

    Returns None when the check itself fails (network down, etc.) so callers
    can skip pinning instead of failing the curation run. Missing covers
    return HTTP 404 with ?default=false, or a 43-byte 1x1 blank gif.
    """
    try:
        req = urllib.request.Request(url, headers=COVER_UA)
        with urllib.request.urlopen(req, timeout=COVER_TIMEOUT) as r:
            body = r.read()
            ctype = r.headers.get_content_type()
            return r.status == 200 and ctype.startswith("image/") and len(body) > 1000
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        return None
    except Exception as e:
        print(f"Cover check skipped ({url[:80]}...): {e}")
        return None

def ol_search_cover_id(title, author):
    """Best-effort Open Library cover ID for title+author via search.json."""
    for params in (
        {"title": title, "author": author, "limit": 5,
         "fields": "key,title,author_name,cover_i"},
        {"title": title, "limit": 5,
         "fields": "key,title,author_name,cover_i"},
    ):
        if not params.get("title"):
            continue
        try:
            url = "https://openlibrary.org/search.json?" + urllib.parse.urlencode(params)
            req = urllib.request.Request(url, headers=COVER_UA)
            with urllib.request.urlopen(req, timeout=COVER_TIMEOUT) as r:
                docs = json.loads(r.read().decode("utf-8")).get("docs", [])
            for doc in docs:
                if isinstance(doc.get("cover_i"), int):
                    return doc["cover_i"]
        except Exception as e:
            print(f"Cover search skipped ({params.get('title', '')[:40]}...): {e}")
            return None
    return None

def resolve_cover(book_data):
    """Mirror of the frontend chain (ISBN -> title -> cover ID -> SVG).

    Probes which level resolves and pins book_data["cover_ol_id"] only when
    both the ISBN-keyed and title-keyed covers are missing. Warn-only: never
    raises, so a cover-service outage can't break the daily run (the inline
    SVG fallback still renders).
    """
    isbn = normalize_isbn(book_data.get("isbn_13"))
    title = str(book_data.get("title", "") or "").strip()
    author = str(book_data.get("author", "") or "").strip()
    try:
        if isbn and ol_cover_ok(
            f"https://covers.openlibrary.org/b/isbn/{isbn}-L.jpg?default=false"
        ):
            book_data.pop("cover_ol_id", None)
            print("Cover check: ISBN-keyed Open Library cover resolves.")
            return
        if title and ol_cover_ok(
            "https://covers.openlibrary.org/b/title/"
            + urllib.parse.quote(title) + "-L.jpg?default=false"
        ):
            book_data.pop("cover_ol_id", None)
            print("Cover check: ISBN cover missing, title-keyed cover resolves (frontend fallback).")
            return
        cover_id = ol_search_cover_id(title, author) if title else None
        if cover_id:
            book_data["cover_ol_id"] = cover_id
            print(f"Cover check: pinned cover_ol_id={cover_id} (ISBN + title covers missing).")
        else:
            book_data.pop("cover_ol_id", None)
            print("Cover check: no Open Library cover found; frontend SVG fallback will render.")
    except Exception as e:
        print(f"Cover check skipped due to error: {e}")

def main():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY environment variable is not set.")
        sys.exit(1)
        
    today_str = datetime.date.today().isoformat()
    
    books = load_json(BOOKS_FILE, [])
    history_list = load_json(HISTORY_FILE, [])
    
    # Batch ids: first pick keeps the plain date (backward compatible),
    # siblings get a "-N" suffix so every entry still has a unique id.
    # Example for BOOKS_PER_RUN=5: 2026-10-03, 2026-10-03-2, ..., 2026-10-03-5.
    expected_ids = [today_str] + [f"{today_str}-{i}" for i in range(2, BOOKS_PER_RUN + 1)]

    # Check if this batch already exists (idempotent Friday run)
    existing_ids = {b.get("id") for b in books if isinstance(b, dict)}
    if any(eid in existing_ids for eid in expected_ids):
        print(f"Books for batch {today_str} (x{BOOKS_PER_RUN}) already exist in books.json. Skipping generation.")
        return

    # Build prompt
    count = BOOKS_PER_RUN
    noun = "book" if count == 1 else "books"
    prompt = f"""
You are an automated, high-precision book curation engine running inside an automated curation script. Your objective is to select exactly {count} non-fiction {noun} that satisfy all criteria below, generate a rich structured dataset for each, and format the output strictly as JSON for integration into the Golden Shelf GitHub Pages collection.

---

### 1. SELECTION CRITERIA (ALL MUST BE MET, FOR EACH OF THE {count} BOOKS)
1. **Genre:** Non-fiction (e.g., neuroscience, cognitive psychology, decision-making, productivity, systems thinking, history, technology, biography).
   - EXCLUDED: True Crime is strictly prohibited. Never select True Crime books.
2. **Community Rating & Volume:**
   - Goodreads Rating: Strictly 4.0 or higher (out of 5.0 stars).
   - Volume: Minimum 2,500 total ratings/reviews on Goodreads.
3. **Length & Readability:**
   - Page Count: Strictly under 500 pages.
   - Style: Highly readable, insightful, accessible, and engaging (avoid dense academic textbooks or dry reference manuals).
4. **Recency / Relevance:**
   - Published AFTER the year 2000, OR classified as an undeniable Evergreen Classic if published prior to 2000.
5. **Strict Deduplication:**
   - Cross-reference the provided `history_list` array.
   - No selected book may exist in `history_list` by title, subtitle, or ISBN.
   - The {count} books in this batch must also be distinct from each other: different titles, different ISBNs, no repeated author. Spread them across at least 3 different genres/categories.

---

### 2. CORRECTNESS REQUIREMENTS (EACH BOOK MUST LOOK CORRECT — VERIFY BEFORE EMITTING)
- Only real, actually published books. Never invent a title, author, or edition.
- `title`: exact full title as published (correct spelling, no invented subtitles).
- `author`: exact author name(s) as published (correct spelling, full name).
- `publication_year`: the real first-publication year of the book (integer 1800-present; post-2000 unless an undeniable evergreen classic). It must match the edition the ISBN belongs to.
- `page_count`: the real page count of a standard print edition of that ISBN (integer strictly under 500). Do not guess a round number — use the actual edition length.
- `isbn_13`: the exact, real 13-digit ISBN of a standard print edition (digits only, no hyphens). Recompute the ISBN-13 checksum digit before emitting: a near-miss ISBN (even one digit off) makes catalog and cover links return zero results and fails validation. Never invent, truncate, or alter digits.
- `goodreads.rating` (4.0-5.0) and `goodreads.ratings_count` (>= 2500) must be plausible real values for that book, not aspirational numbers. If you are unsure a book clears both bars, pick a different, better-known book.
- `genres_tags`: the book's actual categories (non-empty array). Never tag a book True Crime.
- `review_summary_highlights` (at least 2), `summary.overview`, `summary.key_takeaways` (exactly 3), and `one_sentence_hook` must describe THIS specific book's real thesis and contents — concrete frameworks, findings, or narrative — never generic filler that could apply to any book.
- Consistency check before emitting each entry: title <-> author <-> year <-> ISBN must all belong to the same real book and edition. If any field is uncertain, discard the candidate and select a book you know with certainty.

---

### 3. COVER IMAGE ACCESSIBILITY (EACH BOOK MUST HAVE A REACHABLE COVER)
- Construct `cover_image_url` strictly using the Open Library pattern `https://covers.openlibrary.org/b/isbn/{{ISBN_13}}-L.jpg` with your verified `isbn_13`.
- Only select books/editions whose cover is actually reachable at that URL. Prefer the most popular print edition's ISBN (the one Open Library, Goodreads, and booksellers list first), and avoid obscure, print-on-demand, box-set, or regional ISBNs that have no Open Library cover.
- Self-check per book: the ISBN-keyed cover should resolve to a real book cover image (not a 1x1 blank placeholder or 404). If you doubt a specific ISBN has a cover, choose a different well-known edition/ISBN of the same book, or a different book entirely.
- The curation script probes `b/isbn/<ISBN>-L.jpg`, then the title-keyed cover, then pins an Open Library cover ID as a fallback — picking mainstream ISBNs maximizes the chance the first-level cover resolves. Do not emit `cover_ol_id` yourself (the script sets it); just make the ISBN choice cover-friendly.

---

### 4. DEDUPLICATION (STRICT — NO DUPLICATES, EVER)
- Compare every candidate against `history_list` (which contains past ISBNs and titles): reject on exact ISBN match, case-insensitive title/subtitle match, or same author + title combination.
- The {count} books in this batch must be pairwise distinct by ISBN, title, and author — no two entries may share any of these.
- If a candidate collides with `history_list` or with another pick in this batch, replace it with a different qualifying book. Never return a duplicate and hope the script accepts it — duplicates fail validation and discard the whole batch.

---

### 5. INPUT CONTEXT PROVIDED AT RUNTIME
- `history_list`: {json.dumps(history_list)}
- `target_date`: "{today_str}"
- `batch_size`: {count}

---

### 6. REQUIRED JSON OUTPUT SCHEMA
Output ONLY a single valid, raw JSON array containing exactly {count} objects, each matching this structure:

[
  {{
    "isbn_13": "string (13-digit ISBN without hyphens)",
    "title": "string",
    "author": "string",
    "publication_year": integer,
    "page_count": integer,
    "genres_tags": ["array of strings"],
    "goodreads": {{
      "rating": float,
      "ratings_count": integer,
      "review_summary_highlights": [
        "Praise highlight 1 regarding tone, actionable value, or core narrative style",
        "Praise highlight 2 regarding real-world application or insights"
      ]
    }},
    "cover_image_url": "https://covers.openlibrary.org/b/isbn/{{ISBN_13}}-L.jpg",
    "one_sentence_hook": "string (Engaging, punchy single sentence summarizing why to read this book)",
    "summary": {{
      "overview": "2-3 sentence executive summary explaining the core thesis.",
      "key_takeaways": [
        "Key actionable takeaway or mental model 1",
        "Key actionable takeaway or mental model 2",
        "Key actionable takeaway or mental model 3"
      ]
    }},
    "library_lookup": {{
      "goodreads_url": "https://www.goodreads.com/search?q={{ISBN_13}}",
      "worldcat_url": "https://search.worldcat.org/search?q={{ISBN_13}}"
    }}
  }}
]

---

### 7. EXECUTION CONSTRAINTS
- Return exactly {count} array items, ordered by your confidence (strongest pick first).
- Construct each `cover_image_url` strictly using the Open Library API pattern `https://covers.openlibrary.org/b/isbn/{{ISBN_13}}-L.jpg` with that entry's own ISBN.
- Double-check each `isbn_13` is the exact, real 13-digit ISBN of the book (including the checksum digit): a near-miss ISBN makes catalog and cover links return zero results and fails validation.
- `library_lookup` URLs are rebuilt deterministically by the script after parsing; still include best-effort http(s) placeholders matching the schema.
- Omit `id` and `slug`: the script assigns batch ids (`{today_str}`, `{today_str}-2`, ...) and evergreen URL slugs from the title (any values you provide are overwritten).
- Ensure the 3 `key_takeaways` per book focus on concrete, practical frameworks or distinct cognitive insights rather than generic chapter descriptions.
- Output ONLY valid JSON. Do not include markdown headers, surrounding text, preambles, or postscript notes.
"""

    print(f"Calling Gemini API for book curation ({count} {noun})...")
    raw_response = call_gemini_api(prompt, api_key)

    # Clean response (strip markdown fences if present)
    cleaned = raw_response.strip()
    if cleaned.lower().startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as e:
        print(f"Failed to parse JSON response: {e}")
        print(f"Raw response was:\n{raw_response}")
        sys.exit(1)

    # Accept a single object only for a 1-book run (backward compatible);
    # batch runs must return an array of exactly BOOKS_PER_RUN items.
    if isinstance(parsed, dict):
        if count == 1:
            parsed = [parsed]
        else:
            print(f"Error: expected a JSON array of {count} books, got a single object.")
            print(f"Raw response was:\n{raw_response}")
            sys.exit(1)
    if not isinstance(parsed, list) or len(parsed) != count:
        got = len(parsed) if isinstance(parsed, list) else type(parsed).__name__
        print(f"Error: expected a JSON array of exactly {count} books, got {got}.")
        print(f"Raw response was:\n{raw_response}")
        sys.exit(1)
    if any(not isinstance(item, dict) for item in parsed):
        print("Error: every array item must be a JSON object.")
        sys.exit(1)

    # Per-book processing: deterministic ids/links/slugs, schema validation,
    # cover probing, and dedup (against history AND within this batch).
    taken_slugs = {str(b.get("slug") or "") for b in books if isinstance(b, dict)}
    history_exact = {str(h).strip() for h in history_list}
    history_titles = {str(h).strip().casefold() for h in history_list}
    batch_isbns = set()
    batch_titles = set()
    new_books = []

    for idx, book_data in enumerate(parsed):
        expected_id = expected_ids[idx]

        # Assign the batch id (overwrites any model-provided value).
        book_data["id"] = expected_id

        # Deterministic links: never trust model-invented catalog URLs (it
        # returned WorldCat for both buttons with ISBNs that 404). Rebuilt
        # here so validation below also covers them.
        build_library_lookup(book_data)

        # Evergreen URL slug for the dedicated page (title-based, unique across
        # the shelf; never the curation date). Overwrites any model-provided value.
        book_data["slug"] = generate_pages.unique_slug(
            book_data.get("title"), book_data.get("author"), book_data.get("isbn_13"), taken_slugs
        )
        taken_slugs.add(book_data["slug"])

        # Enforce selection criteria + schema before touching any data files.
        validation_errors = validate_book_data(book_data, history_list, expected_id)
        if validation_errors:
            print(f"Generated book #{idx + 1} ('{book_data.get('title')}') failed validation:")
            for err in validation_errors:
                print(f"  - {err}")
            sys.exit(1)

        isbn = str(book_data.get("isbn_13", ""))
        title = str(book_data.get("title", ""))

        # Intra-batch duplicate check (history sets already include earlier
        # picks in this batch, added at the end of each iteration).
        if isbn in batch_isbns:
            print(f"Error: batch contains duplicate isbn_13 {isbn!r} (book #{idx + 1}). Refusing to save.")
            sys.exit(1)
        if title.strip().casefold() in batch_titles:
            print(f"Error: batch contains duplicate title {title!r} (book #{idx + 1}). Refusing to save.")
            sys.exit(1)

        # Probe cover resolvability and pin a data-driven fallback ID when needed.
        # Warn-only: never blocks the run (frontend SVG fallback always renders).
        resolve_cover(book_data)

        # Strict deduplication: abort without saving so a duplicate is never committed.
        if isbn in history_exact or title.strip().casefold() in history_titles:
            print(f"Error: Selected book '{title}' (ISBN: {isbn}) is already in history_list. Refusing to save duplicate.")
            sys.exit(1)

        batch_isbns.add(isbn)
        batch_titles.add(title.strip().casefold())
        history_exact.add(isbn)
        history_titles.add(title.strip().casefold())
        new_books.append(book_data)

    # All picks valid: prepend preserving prompt order (books[0] = strongest pick)
    # and extend history. Nothing was saved before this point, so a failure
    # above never leaves a partial batch behind.
    books[0:0] = new_books
    for book_data in new_books:
        isbn = str(book_data.get("isbn_13", ""))
        title = str(book_data.get("title", ""))
        if isbn and isbn not in history_list:
            history_list.append(isbn)
        if title and title not in history_list:
            history_list.append(title)

    save_json(BOOKS_FILE, books)
    save_json(HISTORY_FILE, history_list)

    # Refresh the per-book static pages + sitemap.xml. Best-effort: the
    # curated data above is already saved, so a template bug here must not
    # fail the run (the workflow also runs generate_pages.py explicitly).
    try:
        generate_pages.main()
    except Exception as e:
        print(f"Warning: book page generation skipped due to error: {e}")

    titles = ", ".join(f"'{b.get('title')}' by {b.get('author')}" for b in new_books)
    print(f"Successfully selected and saved {len(new_books)} books for {today_str}: {titles}")

if __name__ == "__main__":
    main()
