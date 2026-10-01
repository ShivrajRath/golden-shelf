#!/usr/bin/env python3
"""Generate one static, shareable page per book + sitemap.xml + new.html (stdlib only).

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
OUTPUT_DIR = "books"
SITEMAP_FILE = "sitemap.xml"
NEW_PAGE_FILE = "new.html"
SITE_URL = os.environ.get("SITE_URL", "https://novicelab.org").strip().rstrip("/")


def batch_date_of(book):
    """Leading YYYY-MM-DD date of a batch id (e.g. '2026-10-03-2' -> '2026-10-03')."""
    m = re.match(r"(\d{4}-\d{2}-\d{2})", str((book or {}).get("id") or ""))
    return m.group(1) if m else ""


def latest_batch(books):
    """Newest batch: leading run of entries sharing books[0]'s batch date.

    The curation script prepends each batch newest-first, so the latest
    batch is the run of leading entries with the same YYYY-MM-DD prefix —
    typically 5 books, but 1 for single-book manual runs. Falls back to
    books[:5] when ids carry no date prefix.
    """
    dicts = [b for b in (books or []) if isinstance(b, dict)]
    if not dicts:
        return [], ""
    batch_date = batch_date_of(dicts[0])
    if not batch_date:
        return dicts[:5], ""
    batch = []
    for b in dicts:
        if batch_date_of(b) != batch_date:
            break
        batch.append(b)
    return batch, batch_date


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


def render_page(book, newer, older):
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
</body>
</html>
"""


def undefined_image():
    return f"{SITE_URL}/"


def format_batch_date(date_str):
    """'2026-02-27' -> 'February 27, 2026' (empty string when unparseable)."""
    try:
        import datetime as _dt
        return _dt.date(*map(int, str(date_str).split("-"))).strftime("%B %-d, %Y")
    except Exception:
        try:
            import datetime as _dt
            return _dt.date(*map(int, str(date_str).split("-"))).strftime("%B %d, %Y").replace(" 0", " ")
        except Exception:
            return str(date_str or "")


def render_new_page(batch, batch_date):
    """What's-new page: pre-rendered cards for the latest batch only.

    Static pre-render gives crawlers/share previews real content; the
    inline script below re-fetches data/books.json at runtime and re-renders
    when a newer batch has landed (so the page stays correct even from a
    cached copy between regenerations).
    """
    batch = [b for b in (batch or []) if isinstance(b, dict)]
    page_url = f"{SITE_URL}/new.html"
    batch_label = format_batch_date(batch_date) if batch_date else ""
    titles = [str(b.get("title") or "Untitled") for b in batch]
    noun = "pick" if len(batch) == 1 else "picks"
    if titles:
        desc = f"The latest {len(batch)} curated non-fiction {noun} on Golden Shelf" + (f" ({batch_label})" if batch_label else "") + ": " + ", ".join(titles) + "."
    else:
        desc = "The latest curated non-fiction picks on Golden Shelf — highly rated, easy to read, with summaries and takeaways."
    if len(desc) > 300:
        desc = desc[:299].rstrip() + "…"

    def card_html(book):
        title = str(book.get("title") or "Untitled")
        author = str(book.get("author") or "Unknown author")
        slug = page_slug(book)
        cover = str(book.get("cover_image_url") or "")
        cover_ol_id = book.get("cover_ol_id")
        title_cover = (
            "https://covers.openlibrary.org/b/title/" + urllib.parse.quote(title.strip()) + "-L.jpg?default=false"
            if title.strip()
            else ""
        )
        id_cover = f"https://covers.openlibrary.org/b/id/{cover_ol_id}-L.jpg?default=false" if isinstance(cover_ol_id, int) and cover_ol_id > 0 else ""
        goodreads = book.get("goodreads") or {}
        try:
            ratings_text = f"{int(goodreads.get('ratings_count')):,}"
        except (TypeError, ValueError):
            ratings_text = "—"
        rating = goodreads.get("rating")
        rating_text = esc(rating) if rating is not None else "—"
        tags = [t for t in (book.get("genres_tags") or []) if isinstance(t, str) and t.strip()][:3]
        tags_html = "".join(f'<span class="bg-amber-100/80 text-amber-900 px-2 py-0.5 rounded-md text-[11px] font-medium whitespace-nowrap">{esc(g)}</span>' for g in tags)
        hook = str(book.get("one_sentence_hook") or "")
        year = esc(book.get("publication_year", "—"))
        pages = esc(book.get("page_count", "—"))
        return (
            f'<article class="bg-white rounded-2xl border border-stone-200/80 hover:border-amber-300 shadow-sm hover:shadow-xl transition-all duration-300 overflow-hidden flex flex-col">'
            f'<a href="books/{esc(slug)}.html" class="block p-6 flex items-start space-x-4 flex-grow group">'
            f'<span class="w-24 flex-shrink-0 aspect-[2/3] rounded-lg overflow-hidden bg-gradient-to-br from-amber-50 to-amber-100 border border-stone-200 shadow-sm relative block">'
            f'<span class="absolute inset-0 flex flex-col items-center justify-center p-2 text-center">'
            f'<span class="font-serif font-bold text-amber-900 leading-tight text-xs">{esc(title)}</span>'
            f'<span class="mt-1 text-[10px] italic text-amber-800">{esc(author)}</span>'
            f"</span>"
            f'<img src="{esc(cover)}" alt="Cover of {esc(title)} by {esc(author)}" loading="lazy" class="absolute inset-0 w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"'
            f' data-title-cover="{esc(title_cover)}" data-id-cover="{esc(id_cover)}" onload="gsCheckCover(this)" onerror="gsCoverNext(this)">'
            f"</span>"
            f'<span class="block min-w-0 flex-grow">'
            f'<span class="flex items-center justify-between gap-2">'
            f'<span class="text-xs font-semibold text-amber-700">{year}</span>'
            f'<span class="text-xs text-stone-500 font-medium">⭐ {rating_text}</span>'
            f"</span>"
            f'<span class="block mt-1 font-serif font-bold text-stone-900 text-lg leading-snug group-hover:text-amber-700 transition-colors">{esc(title)}</span>'
            f'<span class="block text-xs text-stone-600 truncate">by {esc(author)} • {pages} pages • {esc(ratings_text)} ratings</span>'
            f'<span class="block text-xs text-stone-500 mt-1" style="display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden">{esc(hook)}</span>'
            f'<span class="mt-2 flex flex-wrap gap-1">{tags_html}</span>'
            f"</span></a>"
            f'<span class="px-6 py-3 bg-amber-50/50 border-t border-amber-100/70 flex items-center justify-between text-xs">'
            f'<a href="books/{esc(slug)}.html" class="text-amber-700 font-medium hover:underline">View book page →</a>'
            f"</span></article>"
        )

    cards_html = "".join(card_html(b) for b in batch) if batch else '<p class="text-stone-600">No books yet — check back soon.</p>'
    batch_ids_json = json.dumps([str(b.get("id") or "") for b in batch], ensure_ascii=False)
    count_text = f"{len(batch)} new {'pick' if len(batch) == 1 else 'picks'}" if batch else "New picks"
    date_html = f" <span class=\"text-stone-400\">•</span> <span>{esc(batch_label)}</span>" if batch_label else ""

    items = []
    for pos, b in enumerate(batch, 1):
        slug = page_slug(b)
        item = {
            "@type": "ListItem",
            "position": pos,
            "url": f"{SITE_URL}/books/{slug}.html",
            "name": str(b.get("title") or "Untitled"),
        }
        author = str(b.get("author") or "").strip()
        if author:
            item["author"] = {"@type": "Person", "name": author}
        items.append(item)
    ld_json = json.dumps(
        {
            "@context": "https://schema.org",
            "@type": "ItemList",
            "name": "What's New This Week on Golden Shelf",
            "url": page_url,
            "description": desc,
            "numberOfItems": len(batch),
            "itemListElement": items,
        },
        ensure_ascii=False,
        indent=2,
    )

    return f"""<!DOCTYPE html>
<html lang="en" class="h-full">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>What&apos;s New This Week | Golden Shelf</title>
  <meta name="description" content="{esc(desc)}">
  <meta name="author" content="Golden Shelf">
  <meta name="robots" content="index, follow">
  <link rel="canonical" href="{esc(page_url)}">
  <link rel="icon" type="image/svg+xml" href="favicon.svg">
  <link rel="apple-touch-icon" href="favicon.svg">
  <meta name="theme-color" content="#b45309">
  <meta property="og:type" content="website">
  <meta property="og:site_name" content="Golden Shelf">
  <meta property="og:title" content="What&apos;s New This Week | Golden Shelf">
  <meta property="og:description" content="{esc(desc)}">
  <meta property="og:url" content="{esc(page_url)}">
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="What&apos;s New This Week | Golden Shelf">
  <meta name="twitter:description" content="{esc(desc)}">
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
    <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between gap-4">
      <a href="./" class="flex items-center space-x-3 min-w-0">
        <div class="bg-gradient-to-br from-amber-400 to-amber-600 text-white p-2 rounded-xl shadow-sm ring-1 ring-amber-700/20 flex-shrink-0" aria-hidden="true">
          <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253"/></svg>
        </div>
        <div class="min-w-0">
          <p class="text-lg font-bold font-serif tracking-tight text-stone-900 truncate">Golden Shelf</p>
          <p class="text-xs text-stone-500 truncate">Curated Non-Fiction Books Worth Reading</p>
        </div>
      </a>
      <nav class="flex items-center gap-4 text-sm font-medium flex-shrink-0" aria-label="Primary">
        <a href="./" class="text-stone-600 hover:text-amber-700 transition-colors">Collection</a>
        <a href="new.html" aria-current="page" class="text-amber-700 font-semibold">What&apos;s new</a>
      </nav>
    </div>
  </header>
  <main class="flex-grow w-full max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6 sm:py-10">
    <nav class="text-xs text-stone-500 mb-6" aria-label="Breadcrumb">
      <a href="./" class="hover:text-amber-700 hover:underline">Golden Shelf</a>
      <span aria-hidden="true"> / </span>
      <span class="text-stone-800 font-medium">What&apos;s new</span>
    </nav>
    <section class="max-w-3xl space-y-3 mb-8" aria-labelledby="new-heading">
      <p class="text-xs font-semibold uppercase tracking-widest text-amber-700">Fresh on the shelf</p>
      <h1 id="new-heading" class="text-3xl sm:text-4xl font-serif font-bold tracking-tight text-stone-900">What&apos;s new this week</h1>
      <p class="text-stone-600 leading-relaxed" data-batch-line><span data-batch-count>{esc(count_text)}</span>{date_html} — the latest curated picks, with summaries, takeaways &amp; library links.</p>
    </section>
    <section aria-label="Latest books" data-new-grid class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6">
      {cards_html}
    </section>
    <div class="mt-10 text-center">
      <a href="./#collection" class="inline-flex items-center px-5 py-2.5 bg-white border border-stone-300 hover:border-amber-300 hover:bg-amber-50 text-stone-700 hover:text-amber-800 text-sm font-medium rounded-xl shadow-sm transition-colors">Browse the full collection →</a>
    </div>
  </main>
  <footer class="bg-white border-t border-amber-100 mt-12 py-8">
    <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 text-center">
      <p class="text-xs text-stone-500">Shivraj Rath</p>
    </div>
  </footer>
  <script>
    window.__NEW_BATCH_IDS = {batch_ids_json};
    window.__NEW_BATCH_DATE = {json.dumps(batch_date)};
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
    (function () {{
      function escHtml(s) {{
        return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
      }}
      function slugify(t) {{
        var s = String(t || '').normalize('NFKD').replace(/[\\u0300-\\u036f]/g, '').toLowerCase();
        return s.replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'book';
      }}
      function batchDateOf(b) {{
        var m = /^(\\d{{4}}-\\d{{2}}-\\d{{2}})/.exec(String((b && b.id) || ''));
        return m ? m[1] : '';
      }}
      function latestBatch(books) {{
        var list = (books || []).filter(function (b) {{ return b && typeof b === 'object'; }});
        if (!list.length) return {{ batch: [], date: '' }};
        var d = batchDateOf(list[0]);
        if (!d) return {{ batch: list.slice(0, 5), date: '' }};
        var out = [];
        for (var i = 0; i < list.length; i++) {{
          if (batchDateOf(list[i]) !== d) break;
          out.push(list[i]);
        }}
        return {{ batch: out, date: d }};
      }}
      function fmtDate(ds) {{
        if (!ds) return '';
        var parts = String(ds).split('-');
        if (parts.length !== 3) return ds;
        var months = ['January','February','March','April','May','June','July','August','September','October','November','December'];
        var m = parseInt(parts[1], 10), d = parseInt(parts[2], 10);
        if (!(m >= 1 && m <= 12) || !(d >= 1 && d <= 31)) return ds;
        return months[m - 1] + ' ' + d + ', ' + parts[0];
      }}
      function card(b) {{
        var title = b.title || 'Untitled', author = b.author || 'Unknown author';
        var slug = (b.slug && String(b.slug).trim()) || slugify(title);
        var cover = b.cover_image_url || '';
        var tCover = title.trim() ? 'https://covers.openlibrary.org/b/title/' + encodeURIComponent(title.trim()) + '-L.jpg?default=false' : '';
        var iCover = (b.cover_ol_id && Number(b.cover_ol_id) > 0) ? 'https://covers.openlibrary.org/b/id/' + b.cover_ol_id + '-L.jpg?default=false' : '';
        var gr = b.goodreads || {{}};
        var rc = (gr.ratings_count != null && isFinite(Number(gr.ratings_count))) ? Math.round(Number(gr.ratings_count)).toLocaleString() : '—';
        var rating = (gr.rating != null) ? gr.rating : '—';
        var tags = ((b.genres_tags || []).filter(function (t) {{ return typeof t === 'string' && t.trim(); }})).slice(0, 3).map(function (g) {{
          return '<span class="bg-amber-100/80 text-amber-900 px-2 py-0.5 rounded-md text-[11px] font-medium whitespace-nowrap">' + escHtml(g) + '</span>';
        }}).join('');
        return '<article class="bg-white rounded-2xl border border-stone-200/80 hover:border-amber-300 shadow-sm hover:shadow-xl transition-all duration-300 overflow-hidden flex flex-col">'
          + '<a href="books/' + escHtml(slug) + '.html" class="block p-6 flex items-start space-x-4 flex-grow group">'
          + '<span class="w-24 flex-shrink-0 aspect-[2/3] rounded-lg overflow-hidden bg-gradient-to-br from-amber-50 to-amber-100 border border-stone-200 shadow-sm relative block">'
          + '<span class="absolute inset-0 flex flex-col items-center justify-center p-2 text-center"><span class="font-serif font-bold text-amber-900 leading-tight text-xs">' + escHtml(title) + '</span><span class="mt-1 text-[10px] italic text-amber-800">' + escHtml(author) + '</span></span>'
          + '<img src="' + escHtml(cover) + '" alt="Cover of ' + escHtml(title) + ' by ' + escHtml(author) + '" loading="lazy" class="absolute inset-0 w-full h-full object-cover group-hover:scale-105 transition-transform duration-300" data-title-cover="' + escHtml(tCover) + '" data-id-cover="' + escHtml(iCover) + '" onload="gsCheckCover(this)" onerror="gsCoverNext(this)">'
          + '</span><span class="block min-w-0 flex-grow"><span class="flex items-center justify-between gap-2"><span class="text-xs font-semibold text-amber-700">' + escHtml(b.publication_year != null ? b.publication_year : '—') + '</span><span class="text-xs text-stone-500 font-medium">⭐ ' + escHtml(rating) + '</span></span>'
          + '<span class="block mt-1 font-serif font-bold text-stone-900 text-lg leading-snug group-hover:text-amber-700 transition-colors">' + escHtml(title) + '</span>'
          + '<span class="block text-xs text-stone-600 truncate">by ' + escHtml(author) + ' • ' + escHtml(b.page_count != null ? b.page_count : '—') + ' pages • ' + escHtml(rc) + ' ratings</span>'
          + '<span class="block text-xs text-stone-500 mt-1" style="display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden">' + escHtml(b.one_sentence_hook || '') + '</span>'
          + '<span class="mt-2 flex flex-wrap gap-1">' + tags + '</span></span></a>'
          + '<span class="px-6 py-3 bg-amber-50/50 border-t border-amber-100/70 flex items-center justify-between text-xs"><a href="books/' + escHtml(slug) + '.html" class="text-amber-700 font-medium hover:underline">View book page →</a></span></article>';
      }}
      fetch('data/books.json', {{ cache: 'no-cache' }}).then(function (r) {{
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      }}).then(function (data) {{
        if (!Array.isArray(data)) return;
        var res = latestBatch(data);
        var ids = res.batch.map(function (b) {{ return String(b.id || ''); }});
        var current = window.__NEW_BATCH_IDS || [];
        if (ids.length && JSON.stringify(ids) === JSON.stringify(current)) return;
        if (!res.batch.length) return;
        var grid = document.querySelector('[data-new-grid]');
        if (grid) grid.innerHTML = res.batch.map(card).join('');
        var line = document.querySelector('[data-batch-line]');
        if (line) {{
          var n = res.batch.length;
          line.innerHTML = '<span>' + n + ' new ' + (n === 1 ? 'pick' : 'picks') + '</span>' + (res.date ? ' <span class="text-stone-400">•</span> <span>' + escHtml(fmtDate(res.date)) + '</span>' : '') + ' — the latest curated picks, with summaries, takeaways &amp; library links.';
        }}
        window.__NEW_BATCH_IDS = ids;
      }}).catch(function (e) {{ /* pre-rendered content stays visible */ }});
    }})();
  </script>
</body>
</html>
"""


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


def write_sitemap(slugs_with_dates, new_lastmod=""):
    urls = [f"  <url><loc>{SITE_URL}/</loc></url>"]
    if new_lastmod:
        urls.append(f"  <url><loc>{SITE_URL}/new.html</loc><lastmod>{new_lastmod}</lastmod></url>")
    else:
        urls.append(f"  <url><loc>{SITE_URL}/new.html</loc></url>")
    for slug, date_str in slugs_with_dates:
        loc = f"{SITE_URL}/books/{slug}.html"
        # Batch ids look like YYYY-MM-DD-N (Friday 5-book runs); the sitemap
        # only needs the leading YYYY-MM-DD date for <lastmod>.
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
            f.write(render_page(book, newer, older))
        sitemap_entries.append((slug, book.get("id")))
    # Remove stale pages from renamed titles / deleted entries.
    removed = 0
    for name in os.listdir(OUTPUT_DIR):
        if name.endswith(".html") and name not in expected:
            os.remove(os.path.join(OUTPUT_DIR, name))
            removed += 1
    batch, batch_date = latest_batch(books)
    with open(NEW_PAGE_FILE, "w", encoding="utf-8") as f:
        f.write(render_new_page(batch, batch_date))
    write_sitemap(sitemap_entries, batch_date)
    print(f"Generated {len(expected)} book pages in {OUTPUT_DIR}/ + {SITEMAP_FILE} + {NEW_PAGE_FILE} ({removed} stale removed). Latest batch: {batch_date or 'n/a'} ({len(batch)} books).")
    return sorted(expected)


if __name__ == "__main__":
    main()
