#!/usr/bin/env python3
"""
Flask web app — displays new ShopGoodwill men's watch listings since last visit.

Usage:
    gunicorn --bind 0.0.0.0:5000 --workers 1 app:app
    python3 app.py  (dev only)

Dependencies:
    pip install flask gunicorn
"""

import pathlib
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from flask import Flask, make_response, render_template_string, request, redirect, url_for

HERE       = pathlib.Path(__file__).parent
DEFAULT_DB = HERE / "watches.db"

app = Flask(__name__)
app.config["DB_PATH"] = DEFAULT_DB

# ── DB helpers ────────────────────────────────────────────────────────────────

@contextmanager
def get_db():
    """Open a DB connection and guarantee it is closed when the block exits."""
    conn = sqlite3.connect(str(app.config["DB_PATH"]), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def get_listings(since: str = None) -> list:
    with get_db() as conn:
        if since:
            return conn.execute("""
                SELECT * FROM listings
                WHERE active = 1 AND (first_seen > ? OR tagged = 1)
                ORDER BY end_time ASC
            """, (since,)).fetchall()
        else:
            return conn.execute("""
                SELECT * FROM listings
                WHERE active = 1
                ORDER BY end_time ASC
            """).fetchall()


def get_total_active() -> int:
    with get_db() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM listings WHERE active = 1"
        ).fetchone()[0]


def get_last_scrape() -> dict:
    with get_db() as conn:
        try:
            row = conn.execute("""
                SELECT finished_at, new_count FROM scrape_runs
                WHERE finished_at IS NOT NULL
                ORDER BY id DESC LIMIT 1
            """).fetchone()
            return dict(row) if row else None
        except Exception:
            return None


# ── HTML template ─────────────────────────────────────────────────────────────

TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ShopGoodwill — Men's Watches</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #f5f5f5;
      color: #222;
      padding: 2rem 1rem;
    }

    .page-header {
      max-width: 960px;
      margin: 0 auto 1.5rem;
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 0.5rem;
    }

    h1 { font-size: 1.5rem; font-weight: 700; }

    .meta { font-size: 0.85rem; color: #666; }

    .actions {
      max-width: 960px;
      margin: 0 auto 1.5rem;
      display: flex;
      gap: 0.75rem;
      align-items: center;
      flex-wrap: wrap;
    }

    .btn {
      display: inline-block;
      padding: 0.5rem 1.1rem;
      border-radius: 6px;
      font-size: 0.9rem;
      font-weight: 600;
      cursor: pointer;
      text-decoration: none;
      border: none;
    }

    .btn-primary { background: #2563eb; color: #fff; }
    .btn-primary:hover { background: #1d4ed8; }
    .btn-secondary { background: #e5e7eb; color: #374151; }
    .btn-secondary:hover { background: #d1d5db; }

    .empty {
      max-width: 960px;
      margin: 3rem auto;
      text-align: center;
      color: #666;
      font-size: 1.05rem;
    }

    .grid {
      max-width: 960px;
      margin: 0 auto;
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
      gap: 1rem;
    }

    .card {
      background: #fff;
      border-radius: 10px;
      box-shadow: 0 1px 3px rgba(0,0,0,.1);
      overflow: hidden;
      display: flex;
      flex-direction: column;
      transition: box-shadow .15s;
    }
    .card:hover { box-shadow: 0 4px 12px rgba(0,0,0,.15); }

    .card-img {
      width: 100%;
      aspect-ratio: 1;
      object-fit: cover;
      background: #eee;
    }

    .card-img-placeholder {
      width: 100%;
      aspect-ratio: 1;
      background: #e5e7eb;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 2.5rem;
    }

    .card-body {
      padding: 0.85rem;
      display: flex;
      flex-direction: column;
      gap: 0.35rem;
      flex: 1;
    }

    .card-title {
      font-size: 0.9rem;
      font-weight: 600;
      line-height: 1.35;
    }

    .card-title a { color: #1d4ed8; text-decoration: none; }
    .card-title a:hover { text-decoration: underline; }

    .card-meta {
      font-size: 0.8rem;
      color: #555;
      display: flex;
      justify-content: space-between;
      margin-top: auto;
      padding-top: 0.5rem;
      border-top: 1px solid #f0f0f0;
    }

    .price { font-weight: 700; color: #16a34a; }
    .bids  { color: #6b7280; }

    .badge-new {
      display: inline-block;
      background: #fef3c7;
      color: #92400e;
      font-size: 0.7rem;
      font-weight: 700;
      padding: 0.15rem 0.45rem;
      border-radius: 999px;
    }

    .badge-tagged {
      display: inline-block;
      background: #e0e7ff;
      color: #3730a3;
      font-size: 0.7rem;
      font-weight: 700;
      padding: 0.15rem 0.45rem;
      border-radius: 999px;
    }

    .btn-tag {
      background: none;
      border: none;
      cursor: pointer;
      font-size: 1.1rem;
      line-height: 1;
      padding: 0;
      margin-right: 0.25rem;
      vertical-align: middle;
      transition: transform 0.15s;
    }
    .btn-tag:hover {
      transform: scale(1.1);
    }

    .end-time { font-size: 0.75rem; color: #9ca3af; }

    .last-visit-note {
      max-width: 960px;
      margin: 0 auto 1rem;
      font-size: 0.85rem;
      color: #6b7280;
    }

    .scrape-info {
      max-width: 960px;
      margin: 2rem auto 0;
      font-size: 0.78rem;
      color: #9ca3af;
      text-align: right;
    }
  </style>
</head>
<body>

<div class="page-header">
  <h1>🕐 Men's Watches</h1>
  <span class="meta">ShopGoodwill &bull; {{ total_active }} active listing{{ 's' if total_active != 1 else '' }} in DB</span>
</div>

<div class="actions">
  <form method="post" action="/mark-seen">
    <button class="btn btn-primary" type="submit">&#10003; Mark all seen</button>
  </form>
  <a class="btn btn-secondary" href="/?all=1">View all active</a>
  {% if showing_all %}
    <a class="btn btn-secondary" href="/">Show new only</a>
  {% endif %}
</div>

{% if last_visit %}
<div class="last-visit-note">
  Showing listings first seen after your last visit:
  <strong>{{ last_visit }}</strong>
</div>
{% else %}
<div class="last-visit-note">First visit — showing all active listings.</div>
{% endif %}

{% if not listings %}
  <div class="empty">
    No new listings since your last visit.<br><br>
    <a href="/?all=1">View all active listings</a> or check back after the next scrape.
  </div>
{% else %}
  <div class="grid">
    {% for row in listings %}
    <div class="card">
      {% if row['image_url'] %}
        <img class="card-img" src="{{ row['image_url'] }}" alt="{{ row['title'] }}" loading="lazy">
      {% else %}
        <div class="card-img-placeholder">&#8987;</div>
      {% endif %}
      <div class="card-body">
        <div style="display: flex; gap: 0.25rem; align-items: center; margin-bottom: 0.25rem; min-height: 1.2rem;">
          {% if last_visit_raw and row['first_seen'] > last_visit_raw %}
            <span class="badge-new">NEW</span>
          {% elif not last_visit_raw %}
            <span class="badge-new">NEW</span>
          {% endif %}
          {% if row['tagged'] %}
            <span class="badge-tagged">TAGGED</span>
          {% endif %}
        </div>
        <div class="card-title">
          <form method="post" action="/toggle-tag/{{ row['item_id'] }}" class="toggle-form" style="display:inline;">
            <button type="submit" class="btn-tag" title="Toggle tag">
              {% if row['tagged'] %}⭐{% else %}☆{% endif %}
            </button>
          </form>
          <a href="{{ row['url'] }}" target="_blank" rel="noopener">{{ row['title'] }}</a>
        </div>
        {% if row['end_time'] %}
          <div class="end-time">Ends {{ row['end_time'] }}</div>
        {% endif %}
        <div class="card-meta">
          <span class="price">${{ "%.2f"|format(row['current_price'] or 0) }}</span>
          <span class="bids">{{ row['num_bids'] or 0 }} bid{{ 's' if (row['num_bids'] or 0) != 1 else '' }}</span>
        </div>
      </div>
    </div>
    {% endfor %}
  </div>
{% endif %}

{% if last_scrape %}
<div class="scrape-info">Last scraped: {{ last_scrape }} &bull; {{ new_count }} new watch{{ 'es' if new_count != 1 else '' }} found</div>
{% endif %}

<script>
document.querySelectorAll('.toggle-form').forEach(form => {
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = form.querySelector('.btn-tag');
    const isTagged = btn.textContent.trim() === '⭐';
    
    // Optimistic UI update
    btn.textContent = isTagged ? '☆' : '⭐';
    
    const cardBody = form.closest('.card-body');
    const badgeContainer = cardBody.querySelector('div:first-child');
    if (isTagged) {
      const badge = badgeContainer.querySelector('.badge-tagged');
      if (badge) badge.remove();
    } else {
      if (!badgeContainer.querySelector('.badge-tagged')) {
        const badge = document.createElement('span');
        badge.className = 'badge-tagged';
        badge.textContent = 'TAGGED';
        badgeContainer.appendChild(badge);
      }
    }

    try {
      await fetch(form.action, {
        method: 'POST',
        headers: { 'Accept': 'application/json' }
      });
    } catch (err) {
      console.error(err);
      // Revert on error
      btn.textContent = isTagged ? '⭐' : '☆';
    }
  });
});
</script>

</body>
</html>
"""

# ── Routes ────────────────────────────────────────────────────────────────────

COOKIE_NAME = "last_visit"


@app.route("/")
def index():
    last_visit  = request.cookies.get(COOKIE_NAME)
    showing_all = request.args.get("all") == "1"

    if showing_all or not last_visit:
        listings   = get_listings()
        display_lv = None
    else:
        listings   = get_listings(since=last_visit)
        try:
            dt = datetime.fromisoformat(last_visit)
            display_lv = dt.strftime("%B %d, %Y at %H:%M UTC")
        except Exception:
            display_lv = last_visit

    total_active = get_total_active()
    scrape_data  = get_last_scrape()
    last_scrape  = None
    new_count    = 0

    if scrape_data and scrape_data.get("finished_at"):
        try:
            dt = datetime.fromisoformat(scrape_data["finished_at"])
            last_scrape = dt.strftime("%B %d, %Y at %H:%M UTC")
        except Exception:
            last_scrape = scrape_data["finished_at"]
        new_count = scrape_data.get("new_count") or 0

    html = render_template_string(
        TEMPLATE,
        listings       = listings,
        last_visit     = display_lv,
        last_visit_raw = last_visit or "",
        total_active   = total_active,
        last_scrape    = last_scrape,
        new_count      = new_count,
        showing_all    = showing_all,
    )

    resp = make_response(html)
    if not showing_all:
        resp.set_cookie(
            COOKIE_NAME,
            datetime.now(timezone.utc).isoformat(),
            max_age=60 * 60 * 24 * 365,
            samesite="Lax",
        )
    return resp


@app.route("/mark-seen", methods=["POST"])
def mark_seen():
    resp = make_response("", 302)
    resp.headers["Location"] = "/"
    resp.set_cookie(
        COOKIE_NAME,
        datetime.now(timezone.utc).isoformat(),
        max_age=60 * 60 * 24 * 365,
        samesite="Lax",
    )
    return resp


@app.route("/toggle-tag/<item_id>", methods=["POST"])
def toggle_tag(item_id):
    with get_db() as conn:
        conn.execute("UPDATE listings SET tagged = 1 - tagged WHERE item_id = ?", (item_id,))
        conn.commit()
    if request.headers.get("Accept") == "application/json":
        return {"status": "ok"}
    return redirect(request.referrer or url_for('index'))


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--db",   default=None)
    p.add_argument("--port", default=5000, type=int)
    p.add_argument("--host", default="0.0.0.0")
    args = p.parse_args()
    if args.db:
        app.config["DB_PATH"] = pathlib.Path(args.db)
    app.run(host=args.host, port=args.port)
