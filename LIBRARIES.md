# Local library availability

Every book page now has a **Find it at your library** panel. It shows the
six library systems from the reference screenshot and gives each one a
one-click **Borrow**, **Place a hold**, **View**, or **Check** action.

Because the site is static, availability cannot be fetched live from every
catalog in the browser (the catalogs do not allow cross-origin requests).
Instead, availability is cached in `data/library-availability.json` and baked
into the pages when the site is generated.

## Files

| File | Purpose |
| --- | --- |
| `data/libraries.json` | The library systems, their catalog search URLs, and digital-collection links. |
| `data/library-availability.json` | Cached availability for each ISBN. Entries are optional; books without an entry still show one-click catalog links. |
| `scripts/update_library_availability.py` | Refreshes the cache from catalogs that allow automated access. |
| `generate_pages.py` | Renders the panel into every book page. |

## Refresh availability

```bash
# One book
python3 scripts/update_library_availability.py --slug sector-7
python3 scripts/update_library_availability.py --isbn 9780395746561

# A small batch
python3 scripts/update_library_availability.py --all --limit 50

# Only one library
python3 scripts/update_library_availability.py --slug sector-7 --library frisco

# Always rebuild the pages after the cache changes
python3 generate_pages.py
```

The script is polite by default: one request at a time with a one-second
delay. Use `--delay 0.5` for a faster batch, or `--dry-run` to see what would
be refreshed.

### Which libraries refresh automatically?

| Library | Catalog | Auto refresh |
| --- | --- | --- |
| Frisco Public Library | BiblioCommons | Yes |
| Harris County Public Library | BiblioCommons | Yes |
| Plano Public Library System | SirsiDynix Enterprise | Yes |
| The Colony Public Library | Aspen Discovery | No — catalog blocks automated requests |
| Little Elm Public Library | Koha | No — catalog blocks automated requests |
| Houston Public Library | SirsiDynix Enterprise | No — catalog blocks automated requests |

The three blocked catalogs still get a working **Check** button that opens the
library's search results. If you want a cached status for those libraries, add
an entry to `data/library-availability.json` manually (see the Sector 7
example already in the file).

## Availability JSON shape

```json
{
  "version": 1,
  "updated": "2026-10-30T11:09:16Z",
  "books": {
    "9780395746561": {
      "title": "Sector 7",
      "libraries": {
        "frisco": {
          "checked_at": "2026-10-30T11:09:12Z",
          "source": "BiblioCommons catalog",
          "items": [
            {
              "format": "Picture book",
              "status": "available",
              "available_copies": 2,
              "total_copies": 2,
              "holds": 0,
              "action": "borrow",
              "url": "https://friscolibrary.bibliocommons.com/v2/record/S159C52475",
              "is_digital": false
            }
          ]
        }
      }
    }
  }
}
```

The key under `books` is the ISBN-13 without hyphens. A library key must match
an `id` in `data/libraries.json`.

### Item fields

| Field | Values |
| --- | --- |
| `format` | `Book`, `Picture book`, `Large print`, `Audiobook`, `eBook`, `eAudiobook`, … |
| `status` | `available`, `on_hold`, `unavailable`, `unknown` |
| `available_copies` / `total_copies` | Integers. Used for the green/red “N of M copies available” line. |
| `holds` | Integer. Shown as a hold count when the book is unavailable. |
| `action` | `borrow`, `hold`, `view`, `search` |
| `url` | Where the button goes. If omitted, the library's search URL is used. |
| `is_digital` | `true` for eBook/eAudiobook rows. |

The panel also accepts multiple items per library, so a library can show a
physical book and a digital edition, exactly like the reference screenshot.

## Add or change a library

Edit `data/libraries.json`. The important fields are:

```json
{
  "id": "frisco",
  "name": "Frisco Public Library",
  "city": "Frisco, TX",
  "system": "BiblioCommons",
  "catalog_type": "bibliocommons",
  "auto_update": true,
  "catalog_url": "https://friscolibrary.bibliocommons.com",
  "search_url": "https://friscolibrary.bibliocommons.com/v2/search?query={query}&searchType=keyword",
  "homepage": "https://friscolibrary.com",
  "digital_url": "https://libbyapp.com/library/frisco/search/query-{query}/page-1"
}
```

`search_url` supports `{query}`, `{isbn}`, `{title}`, and `{author}`. The
generator fills in the ISBN when it is valid and falls back to title + author
otherwise.

`catalog_type` is only used by the refresh script. Supported auto-update
values are `bibliocommons` and `sirsi`. Set `auto_update: false` for a library
you want to keep manual.

## Live availability (no cached data)

The book pages no longer bake in a snapshot. Instead, each detail page ships a
small client-side loader that asks the (optional) local API for the current
status of all six catalogs:

1. Start the live server instead of `python3 -m http.server`:

   ```bash
   python3 serve.py                 # http://127.0.0.1:8000
   python3 serve.py --port 8765
   python3 serve.py --cache-ttl 0   # fully live (default)
   ```

2. Open any book page. The panel shows “Checking live availability…”, then
   updates with the live status for the catalogs that allow automated access
   (Frisco, Harris County, Plano). The other three catalogs (The Colony,
   Little Elm, Houston) block automated requests and keep a working **Check**
   button.

3. On a static host (e.g. GitHub Pages) the API is unavailable, so the page
   falls back to **Check** buttons only — no stale numbers are ever shown.

The API endpoint is `GET /api/library-availability?isbn=...&title=...` and
returns JSON with one entry per library (`live: true/false`, `items`, …).

## One-click actions

The action buttons open the library catalog in a new tab:

- **Borrow** → the catalog record when a record URL is available, otherwise
  the catalog search.
- **Place a hold** → the catalog record or search, where the library's own
  “Place hold” control is available after signing in.
- **View** → the digital collection (Libby or cloudLibrary).
- **Check** → the catalog search for the book's ISBN.

The library catalog still asks for the patron's library card/PIN before an
actual checkout or hold, as it should. The site never stores library
credentials.
