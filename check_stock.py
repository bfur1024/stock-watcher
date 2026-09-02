#!/usr/bin/env python3
"""
check_stock.py - one-shot stock check, built for GitHub Actions.

Runs once, compares against the state committed in the repo, pushes a phone
notification only on the sold-out -> in-stock transition, then writes state
back out for the workflow to commit.

Environment:
    PRODUCT_URL  product page to check (falls back to the Prete 6011FL)
    NTFY_TOPIC   ntfy.sh topic for push notifications
"""

import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone

URL = os.environ.get("PRODUCT_URL") or \
    "https://www.pretedecks.de/product/prete-decks-6011FL-2026"
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
STATE_FILE = "state.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/json,*/*",
        "Accept-Language": "en-US,en;q=0.9,de;q=0.8",
        "Cache-Control": "no-cache",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def check_via_json(url):
    try:
        data = json.loads(fetch(url.rstrip("/") + ".json"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None

    status = str(data.get("status", "")).lower()
    options = data.get("options") or []
    available = []
    for opt in options:
        if not isinstance(opt, dict):
            continue
        qty = opt.get("quantity")
        if opt.get("sold_out") is False or (isinstance(qty, int) and qty > 0):
            name = opt.get("name") or "default"
            available.append(name + (f" (qty {qty})" if isinstance(qty, int) else ""))

    if available:
        return True, "available: " + ", ".join(available)
    if status in ("sold-out", "sold_out"):
        return False, "status=sold-out"
    if status == "active" and not options:
        return None
    return False, f"status={status or 'unknown'}"


def check_via_html(url):
    html = fetch(url)
    m = re.search(
        r'<meta[^>]+property=["\']og:availability["\'][^>]+content=["\']([^"\']+)["\']',
        html, re.I)
    if not m:
        m = re.search(
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:availability["\']',
            html, re.I)
    if m:
        v = m.group(1).strip().lower()
        return (v not in {"oos", "out of stock", "outofstock", "sold out", "soldout"}), \
            f"og:availability={v}"

    sold_out = re.search(r">\s*Sold out\s*<", html, re.I) is not None
    buy = re.search(r'name=["\']cart\[add\]|type=["\']submit["\'][^>]*>\s*Add to', html, re.I)
    if sold_out and not buy:
        return False, 'page shows "Sold out"'
    if buy:
        return True, "add-to-cart control present"
    raise RuntimeError("no stock indicator found on page")


def check_stock(url):
    r = check_via_json(url)
    if r is not None:
        return r[0], "json: " + r[1]
    in_stock, detail = check_via_html(url)
    return in_stock, "html: " + detail


def push(title, message, url, priority="urgent", tags="rotating_light"):
    if not NTFY_TOPIC:
        print("  (no NTFY_TOPIC set - skipping push)")
        return
    try:
        req = urllib.request.Request(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={"Title": title, "Priority": priority,
                     "Tags": tags, "Click": url},
            method="POST")
        urllib.request.urlopen(req, timeout=20).read()
        print("  push sent")
    except Exception as exc:
        print(f"  push failed: {exc}")


def load_state():
    try:
        with open(STATE_FILE) as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_state(state):
    with open(STATE_FILE, "w") as fh:
        json.dump(state, fh, indent=2)
        fh.write("\n")


def main():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    state = load_state()
    was_in_stock = bool(state.get("in_stock"))
    fail_streak = int(state.get("fail_streak", 0))

    print(f"[{now}] checking {URL}")

    try:
        in_stock, detail = check_stock(URL)
    except Exception as exc:
        fail_streak += 1
        print(f"  ERROR: {exc} (streak {fail_streak})")
        # Only bug the user if it's been broken for roughly an hour, so a
        # single blip doesn't wake anyone up.
        if fail_streak in (12, 48):
            push("Stock watcher is failing",
                 f"{fail_streak} checks in a row failed. Last error: {exc}",
                 URL, priority="default", tags="warning")
        state.update({"fail_streak": fail_streak, "last_checked": now,
                      "last_error": str(exc)})
        save_state(state)
        return 0  # don't fail the workflow on a transient network hiccup

    print(f"  {'IN STOCK' if in_stock else 'sold out'} ({detail})")

    if in_stock and not was_in_stock:
        print("  *** RESTOCK DETECTED ***")
        push("Prete Decks 6011FL IS IN STOCK", f"{detail}\n{URL}", URL)
    elif was_in_stock and not in_stock:
        print("  stock gone again")

    state.update({
        "in_stock": in_stock,
        "detail": detail,
        "last_checked": now,
        "fail_streak": 0,
        "last_error": None,
    })
    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
