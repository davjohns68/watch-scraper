#!/usr/bin/env python3
"""
ShopGoodwill Men's Watches scraper — writes results to SQLite.

Usage:
    python3 scraper.py                        # run scrape, save to DB
    python3 scraper.py --db /path/to/watches.db
    python3 scraper.py --no-filter            # skip keyword filtering
    python3 scraper.py --debug                # print raw API response and exit

Dependencies:
    pip install requests
"""

import argparse
import json
import pathlib
import sqlite3
import sys
import time
import warnings
from datetime import datetime, timezone

warnings.filterwarnings("ignore", message=".*LibreSSL.*")
warnings.filterwarnings("ignore", message=".*NotOpenSSLWarning.*")

import requests  # noqa: E402

# ── Constants ─────────────────────────────────────────────────────────────────

SEARCH_URL = "https://buyerapi.shopgoodwill.com/api/Search/ItemListingData"

CAT_IDS = "6,89,340"
SCID    = "340"
CID     = "89"
PN      = "3"
CL      = "2"

PAGE_SIZE = 40

HEADERS = {
    "Accept":     "application/json, text/plain, */*",
    "Origin":     "https://shopgoodwill.com",
    "Referer":    "https://shopgoodwill.com/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}

HERE           = pathlib.Path(__file__).parent
DEFAULT_DB     = HERE / "watches.db"
DEFAULT_CONFIG = HERE / "filters.json"

# ── Database ──────────────────────────────────────────────────────────────────

def open_db(path: pathlib.Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS listings (
            item_id       TEXT PRIMARY KEY,
            title         TEXT,
            current_price REAL,
            num_bids      INTEGER,
            end_time      TEXT,
            seller        TEXT,
            condition     TEXT,
            image_url     TEXT,
            url           TEXT,
            first_seen    TEXT NOT NULL,
            last_seen     TEXT NOT NULL,
            active        INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS scrape_runs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at  TEXT NOT NULL,
            finished_at TEXT,
            total_found INTEGER,
            new_count   INTEGER,
            updated_count INTEGER
        );
    """)
    conn.commit()
    return conn


def upsert_listings(conn: sqlite3.Connection, items: list, now: str) -> tuple:
    """Insert new listings, update existing ones. Returns (new_count, updated_count)."""
    new_count     = 0
    updated_count = 0

    for item in items:
        item_id = str(item.get("itemId") or item.get("id") or "")
        if not item_id:
            continue

        existing = conn.execute(
            "SELECT item_id FROM listings WHERE item_id = ?", (item_id,)
        ).fetchone()

        row = {
            "item_id":       item_id,
            "title":         (item.get("title") or "").strip(),
            "current_price": item.get("currentPrice") or item.get("price"),
            "num_bids":      item.get("numBids") or item.get("bids") or 0,
            "end_time":      item.get("endTime") or item.get("endDate"),
            "seller":        (item.get("sellerName") or "").strip(),
            "condition":     (item.get("conditionDescription") or "").strip(),
            "image_url":     (item.get("imageURL") or "").strip(),
            "url":           f"https://shopgoodwill.com/item/{item_id}",
            "last_seen":     now,
            "active":        1,
        }

        if existing:
            conn.execute("""
                UPDATE listings SET
                    title         = :title,
                    current_price = :current_price,
                    num_bids      = :num_bids,
                    end_time      = :end_time,
                    seller        = :seller,
                    condition     = :condition,
                    image_url     = :image_url,
                    url           = :url,
                    last_seen     = :last_seen,
                    active        = :active
                WHERE item_id = :item_id
            """, row)
            updated_count += 1
        else:
            row["first_seen"] = now
            conn.execute("""
                INSERT INTO listings
                    (item_id, title, current_price, num_bids, end_time,
                     seller, condition, image_url, url, first_seen, last_seen, active)
                VALUES
                    (:item_id, :title, :current_price, :num_bids, :end_time,
                     :seller, :condition, :image_url, :url, :first_seen, :last_seen, :active)
            """, row)
            new_count += 1

    # Mark anything not seen this run as inactive (listing has ended or been removed)
    conn.execute(
        "UPDATE listings SET active = 0 WHERE last_seen != ? AND active = 1", (now,)
    )
    conn.commit()
    return new_count, updated_count


# ── Filtering ─────────────────────────────────────────────────────────────────

def load_keywords(config_path: pathlib.Path) -> list:
    """Load filter rules from config. Returns a list of dicts with
    'keyword' and 'exceptions' keys."""
    if not config_path.exists():
        return []
    with open(config_path, encoding="utf-8") as f:
        data = json.load(f)
    rules = []
    for entry in data.get("filter_keywords", []):
        # Support both old format (plain string) and new format (dict)
        if isinstance(entry, str):
            rules.append({"keyword": entry.lower().strip(), "exceptions": []})
        else:
            rules.append({
                "keyword":    entry.get("keyword", "").lower().strip(),
                "exceptions": [e.lower().strip() for e in entry.get("exceptions", [])],
            })
    rules = [r for r in rules if r["keyword"]]
    if rules:
        print(f"  [filter] {len(rules)} rule(s) loaded:")
        for r in rules:
            if r["exceptions"]:
                print(f"    exclude '{r['keyword']}' unless title contains: "
                      f"{', '.join(repr(e) for e in r['exceptions'])}")
            else:
                print(f"    exclude '{r['keyword']}'")
    return rules

def apply_keyword_filter(items: list, rules: list) -> list:
    """Exclude items matching a keyword, unless an exception term is present."""
    if not rules:
        return items
    before = len(items)
    filtered = []
    for item in items:
        title = (item.get("title") or "").lower()
        exclude = False
        for rule in rules:
            if rule["keyword"] in title:
                # Check if any exception saves it
                if rule["exceptions"] and any(e in title for e in rule["exceptions"]):
                    continue  # exception matched — keep checking other rules
                exclude = True
                break
        if not exclude:
            filtered.append(item)

    removed = before - len(filtered)
    if removed:
        print(f"  [filter] Removed {removed} listing(s) matching filter rules.")
    return filtered

# ── API ───────────────────────────────────────────────────────────────────────

def build_params(page: int = 1, search_text: str = "") -> dict:
    return {
        "pn": PN, "cl": CL, "cids": CAT_IDS, "scids": SCID,
        "p": str(page), "sc": "1", "sd": "false",
        "cid": CID, "sg": "Keyword", "st": search_text,
        "ps": str(PAGE_SIZE),
    }


def fetch_page(session, params, retries=3, debug=False):
    if debug:
        from urllib.parse import urlencode
        print(f"\n[debug] GET {SEARCH_URL}?{urlencode(params)}")

    for attempt in range(1, retries + 1):
        try:
            resp = session.get(SEARCH_URL, params=params, headers=HEADERS, timeout=15)
            if debug:
                print(f"[debug] HTTP {resp.status_code}")
                try:
                    print(json.dumps(resp.json(), indent=2)[:4000])
                except Exception:
                    print(resp.text[:4000])
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as exc:
            print(f"  [error] HTTP {exc.response.status_code}", file=sys.stderr)
            if attempt == retries:
                raise
            time.sleep(2 ** attempt)
        except requests.RequestException as exc:
            if attempt == retries:
                raise
            time.sleep(2 ** attempt)


def fetch_all(session, debug=False):
    all_items      = []
    page           = 1
    total_expected = None

    while True:
        params = build_params(page=page)
        data   = fetch_page(session, params, debug=debug)

        if debug:
            sys.exit(0)

        if isinstance(data, dict):
            items = (
                data.get("items")
                or data.get("searchResults", {}).get("items")
                or data.get("results") or data.get("data") or []
            )
            total_expected = (
                data.get("itemCount")
                or data.get("totalCount")
                or data.get("searchResults", {}).get("itemCount")
                or total_expected or 0
            )
        else:
            items = data if isinstance(data, list) else []

        if total_expected is None:
            total_expected = len(items)

        if not items:
            break

        all_items.extend(items)
        print(f"  Fetched page {page} — {len(all_items)}/{total_expected} listings")

        if len(all_items) >= total_expected:
            break

        page += 1
        time.sleep(0.5)

    return all_items


# ── Main ──────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="Scrape ShopGoodwill men's watches into SQLite.")
    p.add_argument("--db",        default=None, help="Path to SQLite database file.")
    p.add_argument("--config",    default=None, help="Path to filters.json config.")
    p.add_argument("--no-filter", action="store_true", help="Disable keyword filtering.")
    p.add_argument("--debug",     action="store_true", help="Print raw API response and exit.")
    return p.parse_args()


def main():
    args    = parse_args()
    db_path = pathlib.Path(args.db) if args.db else DEFAULT_DB
    now     = datetime.now(timezone.utc).isoformat()

    print(f"[{now}] Starting scrape…")

    conn   = open_db(db_path)
    run_id = conn.execute(
        "INSERT INTO scrape_runs (started_at) VALUES (?)", (now,)
    ).lastrowid
    conn.commit()

    session   = requests.Session()
    raw_items = fetch_all(session, debug=args.debug)
    print(f"  Fetched {len(raw_items)} total listings from API.")

    if not args.no_filter:
        config_path = pathlib.Path(args.config) if args.config else DEFAULT_CONFIG
        keywords    = load_keywords(config_path)
        raw_items   = apply_keyword_filter(raw_items, keywords)

    new_count, updated_count = upsert_listings(conn, raw_items, now)

    finished = datetime.now(timezone.utc).isoformat()
    conn.execute("""
        UPDATE scrape_runs
        SET finished_at = ?, total_found = ?, new_count = ?, updated_count = ?
        WHERE id = ?
    """, (finished, len(raw_items), new_count, updated_count, run_id))
    conn.commit()
    conn.close()

    print(f"  Done. {new_count} new, {updated_count} updated. DB: {db_path}")


if __name__ == "__main__":
    main()
