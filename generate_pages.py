#!/usr/bin/env python3
"""Generate one static, shareable page per book + sitemap.xml (stdlib only).

Each book in data/books.json gets books/<id>-<slug>.html, e.g.
books/2026-09-27-atomic-habits.html, with pre-rendered SEO/OG tags so link
previews work on WhatsApp/X (a single book.html?id=... template rendered
client-side would show no preview to crawlers that don't run JS).

URL scheme: <id>-<slug(title)> (both unique together; slug alone is kept
human-readable for SEO). The matching client-side helper in index.html
(slugify/titleSlug/bookPageUrl) must stay in sync with slugify() below:
NFKD -> ascii -> lower -> [^a-z0-9]+ to '-' -> strip '-'.

Usage:
    python3 generate_pages.py
    SITE_URL="https://novicelab.org" python3 generate_pages.py

Called best-effort from script.py after each curation run and explicitly in
.github/workflows/curate-book.yml (which commits books/ + sitemap.xml).
"""

import html
import json
import os
import re
import sys
import unicodedata
import urllib.parse

DATA_FILE = os.path.join("data", "books.json")
LIBRARIES_FILE = os.path.join("data", "libraries.json")
OUTPUT_DIR = "books"
SITEMAP_FILE = "sitemap.xml"
SITE_URL = os.environ.get("SITE_URL", "https://novicelab.org").strip().rstrip("/")
API_BASE = os.environ.get("GSK_API_BASE", "").strip().rstrip("/")


def slugify(value):
    """Must match the slugify() in index.html (see module docstring)."""
    s = unicodedata.normalize("NFKD", str(value or ""))
    s = s.encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "book"


def page_slug(book):
    """Evergreen URL slug for a book: the stored `slug` field, falling back
    to slugify(title) for entries predating it. Never includes the curation
    date `id` — URLs must stay stable forever."""
    stored = str(book.get("slug") or "").strip()
    if stored:
        return stored
    return slugify(book.get("title"))


def unique_slug(title, author, isbn, taken):
    """Assign a slug not in `taken`. Title-only when free; on collision
    append the author slug, then an ISBN suffix as a last resort."""
    base = slugify(title)
    if base not in taken:
        return base
    if str(author or "").strip():
        candidate = f"{base}-{slugify(author)}"
        if candidate not in taken:
            return candidate
    digits = re.sub(r"\D", "", str(isbn or ""))[-4:] or "book"
    candidate = f"{base}-{digits}"
    i = 2
    while candidate in taken:
        candidate = f"{base}-{digits}-{i}"
        i += 1
    return candidate


def is_valid_isbn13(isbn):
    s = re.sub(r"[-\s]", "", str(isbn or ""))
    if not re.fullmatch(r"\d{13}", s):
        return False
    total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(s[:12]))
    return (10 - total % 10) % 10 == int(s[12])


def goodreads_url(book):
    title = str(book.get("title") or "").strip()
    author = str(book.get("author") or "").strip()
    isbn = re.sub(r"[-\s]", "", str(book.get("isbn_13") or ""))
    q = isbn if is_valid_isbn13(isbn) else f"{title} {author}".strip() or isbn
    return "https://www.goodreads.com/search?q=" + urllib.parse.quote_plus(q)


def short_desc(book, limit=160):
    for key in ("one_sentence_hook",):
        v = str(book.get(key) or "").strip()
        if v:
            text = v
            break
    else:
        text = str((book.get("summary") or {}).get("overview") or "").strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text or f"{book.get('title', 'A book')} by {book.get('author', 'Unknown')} — curated on Golden Shelf."


def esc(text):
    return html.escape(str(text or ""), quote=True)


def normalize_isbn(value):
    return re.sub(r"[^0-9Xx]", "", str(value or "")).upper()


def library_query(book):
    isbn = normalize_isbn(book.get("isbn_13"))
    if is_valid_isbn13(isbn):
        return isbn
    title = str(book.get("title") or "").strip()
    author = str(book.get("author") or "").strip()
    return f"{title} {author}".strip() or isbn or title


def fill_library_url(template, book):
    isbn = normalize_isbn(book.get("isbn_13"))
    title = str(book.get("title") or "").strip()
    author = str(book.get("author") or "").strip()
    query = library_query(book)
    return (
        str(template or "")
        .replace("{query}", urllib.parse.quote_plus(query))
        .replace("{isbn}", urllib.parse.quote_plus(isbn))
        .replace("{title}", urllib.parse.quote_plus(title))
        .replace("{author}", urllib.parse.quote_plus(author))
    )


def library_search_url(lib, book):
    template = lib.get("search_url") or lib.get("catalog_url") or ""
    return fill_library_url(template, book)


def int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


ACTION_LABELS = {
    "borrow": "Borrow",
    "hold": "Place a hold",
    "view": "View",
    "search": "Check",
}

ACTION_CLASSES = {
    "borrow": "bg-amber-600 hover:bg-amber-700 text-white",
    "hold": "bg-amber-50 hover:bg-amber-100 text-amber-800 border border-amber-300",
    "view": "bg-white hover:bg-stone-50 text-stone-700 border border-stone-300",
    "search": "bg-white hover:bg-stone-50 text-stone-700 border border-stone-300",
}

STATUS_STYLES = {
    "available": ("text-emerald-700", "bg-emerald-500"),
    "on_hold": ("text-amber-700", "bg-amber-500"),
    "unavailable": ("text-rose-700", "bg-rose-500"),
    "unknown": ("text-stone-500", "bg-stone-300"),
}


def library_status_text(item):
    status = str(item.get("status") or "unknown").lower()
    available = int_or_none(item.get("available_copies"))
    total = int_or_none(item.get("total_copies"))
    holds = int_or_none(item.get("holds")) or 0

    if status == "available":
        if available is not None and total is not None and total > 0:
            return f"{available} of {total} copies available"
        if available is not None:
            return f"{available} copies available"
        return "Available"
    if status == "on_hold":
        if total is not None and total > 0:
            text = f"{available or 0} of {total} copies available"
        else:
            text = "On hold"
        if holds > 0:
            text += f" • {holds} hold{'s' if holds != 1 else ''}"
        return text
    if status == "unavailable":
        if total is not None and total > 0:
            return f"0 of {total} copies available"
        return "Not available"
    return "Check availability"


def render_library_item(item, lib, book):
    status = str(item.get("status") or "unknown").lower()
    if status not in STATUS_STYLES:
        status = "unknown"
    action = str(item.get("action") or "").lower()
    if action not in ACTION_LABELS:
        action = "view" if status == "available" else "search"

    url = str(item.get("url") or "").strip() or library_search_url(lib, book)
    fmt = str(item.get("format") or "Book").strip() or "Book"
    fmt_lower = fmt.lower()
    is_digital = (
        bool(item.get("is_digital"))
        or fmt_lower.startswith("e")
        or "digital" in fmt_lower
    )
    format_classes = (
        "bg-sky-50 text-sky-800 border-sky-100"
        if is_digital
        else "bg-stone-100 text-stone-700 border-stone-200"
    )
    text_color, dot_color = STATUS_STYLES[status]
    label = ACTION_LABELS[action]
    button_classes = ACTION_CLASSES[action]
    status_text = library_status_text(item)

    return (
        '<div class="flex flex-wrap sm:flex-nowrap items-center gap-x-3 gap-y-2">'
        '<div class="flex flex-wrap items-center gap-x-3 gap-y-1.5 min-w-0 flex-1">'
        f'<span class="inline-flex items-center px-2 py-0.5 rounded-md border text-[11px] font-semibold {format_classes}">{esc(fmt)}</span>'
        f'<span class="inline-flex items-center gap-1.5 text-xs font-medium {text_color}">'
        f'<span class="w-2 h-2 rounded-full {dot_color} flex-shrink-0" aria-hidden="true"></span>'
        f'<span>{esc(status_text)}</span></span>'
        "</div>"
        f'<a href="{esc(url)}" target="_blank" rel="noopener" '
        f'class="inline-flex items-center justify-center px-3.5 py-1.5 rounded-lg text-xs font-semibold shadow-sm transition-colors whitespace-nowrap {button_classes}">'
        f"{esc(label)}</a>"
        "</div>"
    )


def render_library_panel(book, libraries):
    if not libraries:
        return ""

    rows = []
    for lib in libraries:
        if not isinstance(lib, dict) or not lib.get("name"):
            continue
        name_url = str(lib.get("homepage") or lib.get("catalog_url") or "").strip()
        system = str(lib.get("system") or "").strip()
        search_url = library_search_url(lib, book)
        item_html = render_library_item(
            {
                "format": "Book / eBook",
                "status": "unknown",
                "action": "search",
                "url": search_url,
            },
            lib,
            book,
        )
        rows.append(
            '<li class="px-5 sm:px-7 py-4" data-gsk-lib="'
            + esc(lib.get("id"))
            + '">'
            '<div class="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">'
            f'<a href="{esc(name_url)}" target="_blank" rel="noopener" '
            f'class="font-semibold text-sky-800 hover:text-sky-900 hover:underline">{esc(lib.get("name"))}</a>'
            + (f'<span class="text-[11px] text-stone-400">{esc(system)}</span>' if system else "")
            + "</div>"
            f'<div class="mt-2 space-y-2" data-gsk-items>{item_html}</div>'
            "</li>"
        )

    if not rows:
        return ""

    count = len(rows)
    return (
        '<section id="library-availability" class="mt-6 bg-white rounded-3xl border border-amber-200/60 shadow-lg overflow-hidden">'
        '<div class="px-5 sm:px-7 py-5 border-b border-stone-100 flex flex-wrap items-start justify-between gap-3">'
        "<div>"
        '<h2 class="font-serif text-xl sm:text-2xl font-bold text-stone-900">Find it at your library</h2>'
        '<p class="text-xs sm:text-sm text-stone-500 mt-1">Check availability, then borrow or place a hold in one click.</p>'
        "</div>"
        '<div class="text-left sm:text-right">'
        f'<span class="inline-flex items-center px-3 py-1 rounded-full bg-amber-50 border border-amber-200 text-xs font-semibold text-amber-800">{count} libraries</span>'
        f'<p class="text-[11px] text-stone-400 mt-1" data-gsk-note>Checking live availability…</p>'
        "</div>"
        "</div>"
        f'<ul class="divide-y divide-stone-100" data-gsk-list>{"".join(rows)}</ul>'
        '<div class="px-5 sm:px-7 py-3 bg-stone-50 border-t border-stone-100">'
        '<p class="text-[11px] text-stone-500" data-gsk-footer>Availability is checked live when this page is served by <code>serve.py</code>. On a static host, use the Check buttons to open each catalog.</p>'
        "</div>"
        "</section>"
    )


def load_json_file(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def load_libraries():
    data = load_json_file(LIBRARIES_FILE, {})
    libraries = data.get("libraries") if isinstance(data, dict) else data
    return [lib for lib in (libraries or []) if isinstance(lib, dict)]


def render_page(book, newer, older, libraries=None):
    title = str(book.get("title") or "Untitled")
    author = str(book.get("author") or "Unknown author")
    slug = page_slug(book)
    page_url = f"{SITE_URL}/books/{slug}.html"
    desc = short_desc(book)
    cover = str(book.get("cover_image_url") or "")
    cover_ol_id = book.get("cover_ol_id")
    title_cover = (
        "https://covers.openlibrary.org/b/title/" + urllib.parse.quote(title.strip()) + "-L.jpg?default=false"
        if title.strip()
        else ""
    )
    id_cover = f"https://covers.openlibrary.org/b/id/{cover_ol_id}-L.jpg?default=false" if isinstance(cover_ol_id, int) and cover_ol_id > 0 else ""
    goodreads = (book.get("goodreads") or {})
    rating = goodreads.get("rating")
    ratings_count = goodreads.get("ratings_count")
    try:
        ratings_text = f"{int(ratings_count):,}"
    except (TypeError, ValueError):
        ratings_text = "—"
    rating_text = esc(rating) if rating is not None else "—"
    tags = [t for t in (book.get("genres_tags") or []) if isinstance(t, str) and t.strip()]
    overview = str((book.get("summary") or {}).get("overview") or "")
    takeaways = [t for t in ((book.get("summary") or {}).get("key_takeaways") or []) if isinstance(t, str) and t.strip()]
    highlights = [h for h in (goodreads.get("review_summary_highlights") or []) if isinstance(h, str) and h.strip()]
    hook = str(book.get("one_sentence_hook") or "")
    year = esc(book.get("publication_year", "—"))
    pages = esc(book.get("page_count", "—"))
    isbn = esc(book.get("isbn_13", "—"))
    gr_url = goodreads_url(book)

    library_panel = render_library_panel(book, libraries or [])

    libraries_for_js = []
    for lib in (libraries or []):
        if not isinstance(lib, dict) or not lib.get("name"):
            continue
        libraries_for_js.append(
            {
                "id": lib.get("id"),
                "name": lib.get("name"),
                "system": lib.get("system"),
                "homepage": lib.get("homepage") or lib.get("catalog_url"),
                "catalog_url": lib.get("catalog_url"),
                "digital_url": lib.get("digital_url"),
                "digital_label": lib.get("digital_label"),
                "search_url": library_search_url(lib, book),
            }
        )
    libraries_js = json.dumps(libraries_for_js, ensure_ascii=False)
    isbn_js = json.dumps(str(book.get("isbn_13") or ""), ensure_ascii=False)
    title_js = json.dumps(str(book.get("title") or ""), ensure_ascii=False)
    author_js = json.dumps(str(book.get("author") or ""), ensure_ascii=False)

    tags_html = "".join(f'<span class="px-3 py-1 bg-amber-50 text-amber-900 border border-amber-100 rounded-lg text-xs font-medium">{esc(g)}</span>' for g in tags)
    takeaways_html = "".join(
        f'<div class="flex items-start bg-stone-50 border border-stone-200/80 p-3.5 rounded-xl">'
        f'<span class="flex-shrink-0 w-6 h-6 rounded-full bg-amber-100 text-amber-800 flex items-center justify-center text-xs font-bold mr-3 mt-0.5">{i}</span>'
        f'<p class="text-stone-700 text-sm leading-relaxed">{esc(t)}</p></div>'
        for i, t in enumerate(takeaways, 1)
    )
    highlights_html = "".join(f'<p class="text-xs text-stone-600 italic">“{esc(h)}”</p>' for h in highlights)

    def nav_card(other, label, arrow, arrow_last=False):
        if not other:
            return '<span></span>'
        o_slug = page_slug(other)
        o_title = esc(other.get("title") or "Untitled")
        caption = f"{label} {arrow}" if arrow_last else f"{arrow} {label}"
        return (
            f'<a href="{o_slug}.html" class="flex-1 min-w-0 bg-white border border-stone-200 hover:border-amber-300 rounded-2xl p-4 shadow-sm hover:shadow-md transition-all group">'
            f'<span class="text-xs font-medium text-amber-700">{caption}</span>'
            f'<span class="block mt-1 font-serif font-bold text-stone-900 truncate group-hover:text-amber-700">{o_title}</span></a>'
        )

    ld = {
        "@context": "https://schema.org",
        "@type": "Book",
        "name": title,
        "author": {"@type": "Person", "name": author},
        "url": page_url,
        "image": cover or undefined_image(),
        "description": desc,
        "publisher": {"@type": "Organization", "name": "Golden Shelf"},
    }
    if isinstance(book.get("publication_year"), int):
        ld["datePublished"] = str(book["publication_year"])
    if isinstance(book.get("page_count"), int):
        ld["numberOfPages"] = book["page_count"]
    if isinstance(book.get("isbn_13"), str) and book["isbn_13"]:
        ld["isbn"] = book["isbn_13"]
    if isinstance(rating, (int, float)) and isinstance(ratings_count, int):
        ld["aggregateRating"] = {"@type": "AggregateRating", "ratingValue": rating, "bestRating": 5, "ratingCount": ratings_count}
    ld_json = json.dumps(ld, ensure_ascii=False, indent=2)

    return f"""<!DOCTYPE html>
<html lang="en" class="h-full">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{esc(title)} by {esc(author)} | Golden Shelf</title>
  <meta name="description" content="{esc(desc)}">
  <meta name="author" content="Golden Shelf">
  <meta name="robots" content="index, follow">
  <link rel="canonical" href="{esc(page_url)}">
  <link rel="icon" type="image/svg+xml" href="../favicon.svg">
  <link rel="apple-touch-icon" href="../favicon.svg">
  <meta name="theme-color" content="#b45309">
  <meta name="gsk-api-base" content="{esc(API_BASE)}">
  <meta property="og:type" content="article">
  <meta property="og:site_name" content="Golden Shelf">
  <meta property="og:title" content="{esc(title)} by {esc(author)} | Golden Shelf">
  <meta property="og:description" content="{esc(desc)}">
  <meta property="og:url" content="{esc(page_url)}">
  <meta property="og:image" content="{esc(cover)}">
  <meta property="og:image:alt" content="Cover of {esc(title)} by {esc(author)}">
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="{esc(title)} by {esc(author)} | Golden Shelf">
  <meta name="twitter:description" content="{esc(desc)}">
  <meta name="twitter:image" content="{esc(cover)}">
  <style>
    html {{ background: #fdfbf7; }}
    html:not(.tw-ready) body {{ visibility: hidden; }}
    html.tw-ready body {{ visibility: visible; }}
    #fouc-loader {{
      visibility: visible;
      position: fixed; inset: 0; z-index: 9999;
      display: flex; flex-direction: column; align-items: center; justify-content: center;
      gap: 16px; background: #fdfbf7;
    }}
    #fouc-loader .fouc-spinner {{
      width: 40px; height: 40px; border-radius: 9999px;
      border: 4px solid #f5d78e; border-top-color: #b45309;
      animation: fouc-spin 0.8s linear infinite;
    }}
    #fouc-loader p {{ margin: 0; font-family: Inter, system-ui, sans-serif; font-size: 14px; color: #57534e; }}
    @keyframes fouc-spin {{ to {{ transform: rotate(360deg); }} }}
    html.tw-ready #fouc-loader {{ display: none; }}
  </style>
  <noscript><style>html:not(.tw-ready) body {{ visibility: visible; }} #fouc-loader {{ display: none; }}</style></noscript>
  <script src="https://cdn.tailwindcss.com"></script>
  <script>
    (function () {{
      var done = false;
      function ready() {{
        if (done) return; done = true;
        document.documentElement.classList.add('tw-ready');
      }}
      setTimeout(ready, 2500);
      window.addEventListener('load', function () {{ setTimeout(ready, 0); }});
      try {{
        var obs = new MutationObserver(function (muts) {{
          for (var i = 0; i < muts.length; i++) {{
            var nodes = muts[i].addedNodes || [];
            for (var j = 0; j < nodes.length; j++) {{
              var n = nodes[j];
              if (n && n.tagName === 'STYLE' && (n.textContent || '').indexOf('--tw-') !== -1) {{
                ready(); obs.disconnect(); return;
              }}
            }}
          }}
        }});
        obs.observe(document.head, {{ childList: true }});
        var styles = document.head.querySelectorAll('style');
        for (var k = 0; k < styles.length; k++) {{
          if ((styles[k].textContent || '').indexOf('--tw-') !== -1) {{ ready(); obs.disconnect(); break; }}
        }}
      }} catch (e) {{}}
    }})();
  </script>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Playfair+Display:ital,wght@0,600;0,700;1,400&display=swap" rel="stylesheet">
  <style>
    body {{ font-family: 'Inter', sans-serif; }}
    .font-serif {{ font-family: 'Playfair Display', serif; }}
    ::selection {{ background: #fde68a; color: #451a03; }}
  </style>
  <script type="application/ld+json">
{ld_json}
  </script>
</head>
<body class="bg-[#fdfbf7] text-stone-900 min-h-full flex flex-col antialiased">
  <div id="fouc-loader" aria-hidden="true"><div class="fouc-spinner"></div><p>Loading Golden Shelf…</p></div>
  <div class="h-1 bg-gradient-to-r from-amber-200 via-amber-500 to-amber-200" aria-hidden="true"></div>
  <header class="bg-white/90 backdrop-blur border-b border-amber-100 sticky top-0 z-30 shadow-sm">
    <div class="max-w-4xl mx-auto px-4 sm:px-6 h-16 flex items-center justify-between gap-4">
      <a href="../" class="flex items-center space-x-3 min-w-0">
        <div class="bg-gradient-to-br from-amber-400 to-amber-600 text-white p-2 rounded-xl shadow-sm ring-1 ring-amber-700/20 flex-shrink-0" aria-hidden="true">
          <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253"/></svg>
        </div>
        <div class="min-w-0">
          <p class="text-lg font-bold font-serif tracking-tight text-stone-900 truncate">Golden Shelf</p>
          <p class="text-xs text-stone-500 truncate">Curated Non-Fiction Books Worth Reading</p>
        </div>
      </a>
      <a href="../" class="text-sm font-medium text-amber-700 hover:text-amber-800 hover:underline flex-shrink-0 whitespace-nowrap">← Back to the shelf</a>
    </div>
  </header>
  <main class="flex-grow w-full max-w-4xl mx-auto px-4 sm:px-6 py-6 sm:py-10">
    <nav class="text-xs text-stone-500 mb-6" aria-label="Breadcrumb">
      <a href="../" class="hover:text-amber-700 hover:underline">Golden Shelf</a>
      <span aria-hidden="true"> / </span>
      <a href="../#collection" class="hover:text-amber-700 hover:underline">Collection</a>
      <span aria-hidden="true"> / </span>
      <span class="text-stone-800 font-medium">{esc(title)}</span>
    </nav>
    <article class="bg-white rounded-3xl border border-amber-200/60 shadow-xl overflow-hidden">
      <div class="p-6 sm:p-10 grid grid-cols-1 sm:grid-cols-12 gap-8 items-start">
        <div class="sm:col-span-4 flex flex-col items-center space-y-4">
          <div class="relative w-48 sm:w-full max-w-[240px] aspect-[2/3] rounded-2xl overflow-hidden bg-gradient-to-br from-amber-50 to-amber-100 border border-stone-200 shadow-lg">
            <div class="absolute inset-0 flex flex-col items-center justify-center p-4 text-center">
              <p class="font-serif font-bold text-amber-900 leading-snug">{esc(title)}</p>
              <p class="mt-2 text-xs italic text-amber-800">{esc(author)}</p>
              <p class="mt-4 text-[11px] tracking-widest text-amber-700/70">GOLDEN SHELF</p>
            </div>
            <img src="{esc(cover)}" alt="Cover of {esc(title)} by {esc(author)}" class="absolute inset-0 w-full h-full object-cover"
              data-title-cover="{esc(title_cover)}" data-id-cover="{esc(id_cover)}" onload="gsCheckCover(this)" onerror="gsCoverNext(this)">
          </div>
          <a href="{esc(gr_url)}" target="_blank" rel="noopener" class="w-full max-w-[240px] flex items-center justify-center px-4 py-2.5 bg-amber-600 hover:bg-amber-700 text-white font-medium rounded-xl shadow-sm transition-colors text-sm">View on Goodreads</a>
        </div>
        <div class="sm:col-span-8 space-y-5 min-w-0">
          <div class="flex flex-wrap gap-2">{tags_html}</div>
          <div>
            <h1 class="text-3xl sm:text-4xl font-serif font-bold text-stone-900 tracking-tight leading-tight break-words">{esc(title)}</h1>
            <p class="text-lg text-stone-600 mt-1">by <span class="font-semibold text-stone-800">{esc(author)}</span> ({year} • {pages} pages)</p>
            <p class="mt-2 text-sm text-stone-700">⭐ <strong class="text-stone-900">{rating_text}</strong> / 5.0 • {esc(ratings_text)} Goodreads ratings</p>
          </div>
          <div class="bg-gradient-to-r from-amber-50 to-yellow-50/60 border-l-4 border-amber-500 p-4 rounded-r-xl">
            <p class="text-amber-900 font-medium italic">“{esc(hook)}”</p>
          </div>
          <div class="space-y-2">
            <h2 class="text-sm font-semibold text-stone-900 uppercase tracking-wider">Executive Summary</h2>
            <p class="text-stone-700 leading-relaxed">{esc(overview)}</p>
          </div>
          <div class="space-y-3">
            <h2 class="text-sm font-semibold text-stone-900 uppercase tracking-wider">Core Takeaways &amp; Mental Models</h2>
            <div class="grid grid-cols-1 gap-3">{takeaways_html}</div>
          </div>
          <div class="space-y-2 pt-2 border-t border-stone-100">
            <h2 class="text-xs font-semibold text-stone-400 uppercase tracking-wider">Community Highlights</h2>
            <div class="space-y-2">{highlights_html}</div>
          </div>
          <dl class="grid grid-cols-2 sm:grid-cols-4 gap-3 pt-2 text-sm">
            <div class="bg-stone-50 border border-stone-200/80 rounded-xl p-3"><dt class="text-[11px] uppercase tracking-wider text-stone-400 font-semibold">Published</dt><dd class="font-semibold text-stone-800">{year}</dd></div>
            <div class="bg-stone-50 border border-stone-200/80 rounded-xl p-3"><dt class="text-[11px] uppercase tracking-wider text-stone-400 font-semibold">Pages</dt><dd class="font-semibold text-stone-800">{pages}</dd></div>
            <div class="bg-stone-50 border border-stone-200/80 rounded-xl p-3"><dt class="text-[11px] uppercase tracking-wider text-stone-400 font-semibold">ISBN-13</dt><dd class="font-semibold text-stone-800 break-all">{isbn}</dd></div>
            <div class="bg-stone-50 border border-stone-200/80 rounded-xl p-3"><dt class="text-[11px] uppercase tracking-wider text-stone-400 font-semibold">Rating</dt><dd class="font-semibold text-stone-800">⭐ {rating_text}</dd></div>
          </dl>
        </div>
      </div>
    </article>
    {library_panel}
    <div class="mt-6 flex flex-col sm:flex-row gap-3">
      {nav_card(newer, "Newer pick", "←")}
      {nav_card(older, "Older pick", "→", True)}
    </div>
  </main>
  <footer class="bg-white border-t border-amber-100 mt-12 py-8">
    <div class="max-w-4xl mx-auto px-4 sm:px-6 text-center">
      <p class="text-xs text-stone-500">Shivraj Rath</p>
    </div>
  </footer>
  <script>
    window.GSK_ISBN = {isbn_js};
    window.GSK_TITLE = {title_js};
    window.GSK_AUTHOR = {author_js};
    window.GSK_LIBRARIES = {libraries_js};
    window.GSK_API_BASE = (document.querySelector('meta[name="gsk-api-base"]') && document.querySelector('meta[name="gsk-api-base"]').content || '').replace(/\\/$/, '');
  </script>
  <script>
    function gsCoverNext(el) {{
      if (!el || el.dataset.gsDone) return;
      if (!el.dataset.triedTitle && el.dataset.titleCover) {{
        el.dataset.triedTitle = '1';
        if (el.src !== el.dataset.titleCover) {{ el.src = el.dataset.titleCover; return; }}
      }}
      if (!el.dataset.triedId && el.dataset.idCover) {{
        el.dataset.triedId = '1';
        if (el.src !== el.dataset.idCover) {{ el.src = el.dataset.idCover; return; }}
      }}
      el.dataset.gsDone = '1';
      el.remove();
    }}
    function gsCheckCover(el) {{
      if (!el || el.dataset.gsDone) return;
      if (el.naturalWidth <= 2 && el.naturalHeight <= 2) gsCoverNext(el);
    }}
  </script>

  <script>
  (function () {{
    var isbn = window.GSK_ISBN || '';
    var libs = window.GSK_LIBRARIES || [];
    var apiBase = window.GSK_API_BASE || '';
    var panel = document.getElementById('library-availability');
    if (!panel || !isbn || !libs.length) return;
    var note = panel.querySelector('[data-gsk-note]');
    var list = panel.querySelector('[data-gsk-list]');
    var footer = panel.querySelector('[data-gsk-footer]');
    function esc(s){{ return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }}
    var STATUS_STYLES = {{ available:['text-emerald-700','bg-emerald-500'], on_hold:['text-amber-700','bg-amber-500'], unavailable:['text-rose-700','bg-rose-500'], unknown:['text-stone-500','bg-stone-300'] }};
    var ACTION_LABELS = {{ borrow:'Borrow', hold:'Place a hold', view:'View', search:'Check' }};
    var ACTION_CLASSES = {{ borrow:'bg-amber-600 hover:bg-amber-700 text-white', hold:'bg-amber-50 hover:bg-amber-100 text-amber-800 border border-amber-300', view:'bg-white hover:bg-stone-50 text-stone-700 border border-stone-300', search:'bg-white hover:bg-stone-50 text-stone-700 border border-stone-300' }};
    function statusText(item) {{
      var status = String(item.status||'unknown').toLowerCase();
      var avail = (item.available_copies==null?null:Number(item.available_copies));
      var total = (item.total_copies==null?null:Number(item.total_copies));
      var holds = Number(item.holds||0);
      if (status==='available') {{
        if (avail!==null && total!==null && total>0) return avail+' of '+total+' copies available';
        if (avail!==null) return avail+' copies available';
        return 'Available';
      }}
      if (status==='on_hold') {{
        var t = (total!==null && total>0) ? (avail||0)+' of '+total+' copies available' : 'On hold';
        if (holds>0) t += ' • '+holds+' hold'+(holds!==1?'s':'');
        return t;
      }}
      if (status==='unavailable') {{
        if (total!==null && total>0) return '0 of '+total+' copies available';
        return 'Not available';
      }}
      return 'Check availability';
    }}
    function isDigitalFormat(fmt, isDigital) {{
      return !!isDigital || String(fmt||'').toLowerCase().indexOf('e')===0 || /digital/i.test(String(fmt||''));
    }}
    function renderItem(item, lib) {{
      var status = String(item.status||'unknown').toLowerCase();
      if (!STATUS_STYLES[status]) status = 'unknown';
      var action = String(item.action||'').toLowerCase();
      if (!ACTION_LABELS[action]) action = (status==='available'?'view':'search');
      var url = String(item.url||'').trim() || (lib.search_url||'');
      var fmt = String(item.format||'Book').trim() || 'Book';
      var digital = isDigitalFormat(fmt, item.is_digital);
      var fc = digital ? 'bg-sky-50 text-sky-800 border-sky-100' : 'bg-stone-100 text-stone-700 border-stone-200';
      var sc = STATUS_STYLES[status][0];
      var dc = STATUS_STYLES[status][1];
      var label = ACTION_LABELS[action];
      var bc = ACTION_CLASSES[action];
      var st = statusText(item);
      return '<div class="flex flex-wrap sm:flex-nowrap items-center gap-x-3 gap-y-2">'
        + '<div class="flex flex-wrap items-center gap-x-3 gap-y-1.5 min-w-0 flex-1">'
        + '<span class="inline-flex items-center px-2 py-0.5 rounded-md border text-[11px] font-semibold '+fc+'">'+esc(fmt)+'</span>'
        + '<span class="inline-flex items-center gap-1.5 text-xs font-medium '+sc+'">'
        + '<span class="w-2 h-2 rounded-full '+dc+' flex-shrink-0" aria-hidden="true"></span>'
        + '<span>'+esc(st)+'</span></span>'
        + '</div>'
        + '<a href="'+esc(url)+'" target="_blank" rel="noopener" class="inline-flex items-center justify-center px-3.5 py-1.5 rounded-lg text-xs font-semibold shadow-sm transition-colors whitespace-nowrap '+bc+'">'+esc(label)+'</a>'
        + '</div>';
    }}
    function renderRow(lib, items) {{
      var nameUrl = lib.homepage || lib.catalog_url || '';
      var system = lib.system || '';
      var its = (items && items.length) ? items : [{{format:'Book / eBook',status:'unknown',action:'search',url:lib.search_url}}];
      var itemsHtml = its.map(function(it){{ return renderItem(it, lib); }}).join('');
      return '<li class="px-5 sm:px-7 py-4" data-gsk-lib="'+esc(lib.id)+'">'
        + '<div class="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">'
        + '<a href="'+esc(nameUrl)+'" target="_blank" rel="noopener" class="font-semibold text-sky-800 hover:text-sky-900 hover:underline">'+esc(lib.name)+'</a>'
        + (system ? '<span class="text-[11px] text-stone-400">'+esc(system)+'</span>' : '')
        + '</div>'
        + '<div class="mt-2 space-y-2">'+itemsHtml+'</div>'
        + '</li>';
    }}
    function applyData(data) {{
      var byId = {{}};
      (data.libraries||[]).forEach(function(l){{ byId[l.id]=l; }});
      var html = libs.map(function(lib) {{
        var live = byId[lib.id];
        var items = live ? (live.items||[]) : [];
        if (!items.length) items = [{{format:'Book / eBook',status:'unknown',action:'search',url:lib.search_url}}];
        return renderRow(lib, items);
      }}).join('');
      if (list) list.innerHTML = html;
      if (note) {{
        var liveCount = (data.libraries||[]).filter(function(l){{return l.live;}}).length;
        var total = (data.libraries||[]).length || libs.length;
        note.textContent = liveCount>0 ? ('Live • '+liveCount+' of '+total+' catalogs checked just now') : 'Open a library to check current status';
      }}
      if (footer) {{
        var checked = (data.libraries||[]).filter(function(l){{return l.live;}}).length;
        if (checked>0) footer.textContent = 'Live availability from '+checked+' catalog(s). Confirm at checkout; catalogs may require a library card.';
      }}
    }}
    if (note) note.textContent = 'Checking live availability…';
    var url = (apiBase ? apiBase : '') + '/api/library-availability?isbn=' + encodeURIComponent(isbn)
      + '&title=' + encodeURIComponent(window.GSK_TITLE||'');
    var ctrl = null;
    try {{ ctrl = new AbortController(); }} catch (e) {{ ctrl = null; }}
    var fetchOpts = {{credentials:'omit'}};
    if (ctrl) {{ fetchOpts.signal = ctrl.signal; setTimeout(function(){{ try {{ ctrl.abort(); }} catch(e){{}} }}, 8000); }}
    fetch(url, fetchOpts)
      .then(function(r){{ if(!r.ok) throw new Error('HTTP '+r.status); return r.json(); }})
      .then(applyData)
      .catch(function(err){{
        try {{ console.error('GSK live fetch failed:', err); }} catch (e) {{}}
        var msg = (err && err.message) ? err.message : String(err);
        if (note) note.textContent = 'Live status unavailable (' + msg + ').';
        if (footer) footer.textContent = 'Could not reach the live API. Check your network/ad-blocker. API: ' + url;
      }});
  }})();
  </script>
</body>
</html>
"""


def undefined_image():
    return f"{SITE_URL}/"


def load_books():
    if not os.path.exists(DATA_FILE):
        print(f"Error: {DATA_FILE} not found.")
        sys.exit(1)
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        print(f"Error: {DATA_FILE} must contain a JSON array.")
        sys.exit(1)
    return data


def write_sitemap(slugs_with_dates):
    urls = [f"  <url><loc>{SITE_URL}/</loc></url>"]
    for slug, date_str in slugs_with_dates:
        loc = f"{SITE_URL}/books/{slug}.html"
        m = re.match(r"(\d{4}-\d{2}-\d{2})", str(date_str or ""))
        if m:
            urls.append(f"  <url><loc>{loc}</loc><lastmod>{m.group(1)}</lastmod></url>")
        else:
            urls.append(f"  <url><loc>{loc}</loc></url>")
    content = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + "\n".join(urls) + "\n</urlset>\n"
    with open(SITEMAP_FILE, "w", encoding="utf-8") as f:
        f.write(content)


def main():
    books = load_books()
    libraries = load_libraries()
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    expected = set()
    sitemap_entries = []
    for i, book in enumerate(books):
        if not isinstance(book, dict):
            continue
        slug = page_slug(book)
        expected.add(slug + ".html")
        newer = books[i - 1] if i > 0 and isinstance(books[i - 1], dict) else None
        older = books[i + 1] if i + 1 < len(books) and isinstance(books[i + 1], dict) else None
        with open(os.path.join(OUTPUT_DIR, slug + ".html"), "w", encoding="utf-8") as f:
            f.write(render_page(book, newer, older, libraries))
        sitemap_entries.append((slug, book.get("id")))
    removed = 0
    for name in os.listdir(OUTPUT_DIR):
        if name.endswith(".html") and name not in expected:
            os.remove(os.path.join(OUTPUT_DIR, name))
            removed += 1
    write_sitemap(sitemap_entries)
    print(f"Generated {len(expected)} book pages in {OUTPUT_DIR}/ + {SITEMAP_FILE} ({removed} stale removed).")
    return sorted(expected)


if __name__ == "__main__":
    main()
