#!/usr/bin/env python3
"""BMW i4 used-car watchdog: scrape listings, push new ones / price drops to ntfy.

State lives in seen.json (listing key -> {price, title, first_seen}).
Usage: python3 watch.py [--dry-run]
"""
import html
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "bmw-i4-a5ff41c96426")
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen.json")
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
MAX_SINGLE_PUSHES = 15  # above this, send one summary instead of spamming
DRY = "--dry-run" in sys.argv


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "sk,de;q=0.8,en;q=0.5"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def next_data(page):
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    return json.loads(m.group(1))["props"]["pageProps"]


def fmt_km(v):
    try:
        return f"{int(v):,} km".replace(",", " ")
    except (TypeError, ValueError):
        return str(v or "")


def autobazar():
    out = []
    for p in range(1, 6):
        d = next_data(fetch("https://www.autobazar.eu/vysledky/osobne-vozidla/bmw/i4/" + (f"?page={p}" if p > 1 else "")))["searchRecords"]
        for x in d["data"]:
            img = (x.get("image") or {}).get("previewUrls", {}).get("record_preview")
            out.append({
                "key": f"autobazar:{x['id']}",
                "src": "autobazar.eu",
                "title": x.get("title") or "BMW i4",
                "price": x.get("finalPrice") or x.get("price"),
                "info": ", ".join(filter(None, [
                    "/".join(str(v) for v in (x.get("monthValue"), x.get("yearValue")) if v),
                    fmt_km(x.get("mileage")),
                    f"{x.get('enginePower')} kW" if x.get("enginePower") else "",
                    (x.get("location") or {}).get("name", ""),
                ])),
                "url": f"https://www.autobazar.eu/detail/{x.get('sefName', 'bmw-i4')}/{x['id']}/",
                "img": img,
            })
        if p * 20 >= d["total"]:
            break
    return out


def bazos():
    out = []
    for off in range(0, 100, 20):
        page = fetch(f"https://auto.bazos.sk/bmw/{off or ''}{'/' if off else ''}?hledat=i4")
        blocks = page.split('class="inzeraty inzeratyflex"')[1:]
        for b in blocks:
            m = re.search(r'<h2 class=nadpis><a href="(/inzerat/(\d+)/[^"]+)">([^<]+)</a>', b)
            if not m:
                continue
            title = html.unescape(m.group(3)).strip()
            if not re.search(r"\bi4\b", title, re.I) and "i4" not in m.group(1):
                continue
            price = re.search(r'class="inzeratycena"><b><span[^>]*>([^<]+)<', b)
            price_v = int(re.sub(r"\D", "", price.group(1)) or 0) if price else None
            loc = re.search(r'class="inzeratylok">(.*?)</div>', b, re.S)
            img = re.search(r'<img src="([^"]+)"', b)
            desc = re.search(r'class=popis>(.*?)</div>', b, re.S)
            out.append({
                "key": f"bazos:{m.group(2)}",
                "src": "bazos.sk",
                "title": title,
                "price": price_v or None,
                "info": ", ".join(filter(None, [
                    html.unescape(loc.group(1).split("<br>")[0]).strip() if loc else "",
                    " ".join(html.unescape(re.sub(r"<[^>]+>", "", desc.group(1))).split())[:120] if desc else "",
                ])),
                "url": "https://auto.bazos.sk" + m.group(1),
                "img": img.group(1) if img else None,
            })
        if len(blocks) < 20:
            break
    return out


def autoscout():
    out = []
    for p in (1, 2, 3):
        d = next_data(fetch(f"https://www.autoscout24.de/lst/bmw/i4?sort=age&desc=1&cy=D%2CA&atype=C&ustate=N%2CU&page={p}"))
        for x in d["listings"]:
            v, t, loc = x.get("vehicle", {}), x.get("tracking", {}), x.get("location", {})
            imgs = x.get("images") or []
            out.append({
                "key": f"as24:{x['id']}",
                "src": f"autoscout24 {loc.get('countryCode', '')}".strip(),
                "title": f"BMW i4 {v.get('modelVersionInput') or v.get('motorTypeName') or ''}".strip(),
                "price": x.get("price", {}).get("priceRaw"),
                "info": ", ".join(filter(None, [
                    t.get("firstRegistration", "").replace("-", "/"),
                    v.get("mileageInKm", ""),
                    f"{loc.get('zip', '')} {loc.get('city', '')}".strip(),
                    (x.get("seller") or {}).get("type", ""),
                ])),
                "url": "https://www.autoscout24.de" + x["url"],
                "img": imgs[0] if imgs else None,
            })
    return out


SOURCES = [autobazar, bazos, autoscout]


def push(title, body, url=None, img=None, tags="car", priority=3):
    msg = {"topic": NTFY_TOPIC, "title": title, "message": body, "tags": [tags], "priority": priority}
    if url:
        msg["click"] = url
        msg["actions"] = [{"action": "view", "label": "Open", "url": url}]
    if img:
        msg["attach"] = img
    if DRY:
        print("PUSH", json.dumps(msg, ensure_ascii=False))
        return
    req = urllib.request.Request("https://ntfy.sh/", data=json.dumps(msg).encode(), headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=20).read()
    time.sleep(0.5)


def eur(v):
    return f"{int(v):,} €".replace(",", " ") if v else "cena ?"


def main():
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        state = json.load(open(STATE_FILE))
    except FileNotFoundError:
        state = None
    first_run = state is None
    state = state or {}

    listings, errors = [], []
    for src in SOURCES:
        try:
            got = src()
            if not got:
                errors.append(f"{src.__name__}: 0 listings parsed (layout change?)")
            listings += got
            print(f"{src.__name__}: {len(got)}")
        except Exception as e:  # keep other sources running
            errors.append(f"{src.__name__}: {type(e).__name__}: {e}")
            print(f"{src.__name__}: ERROR {e}")

    new, drops = [], []
    for l in {l["key"]: l for l in listings}.values():
        old = state.get(l["key"])
        if old is None:
            new.append(l)
            state[l["key"]] = {"price": l["price"], "title": l["title"], "url": l["url"], "first_seen": now}
        else:
            if l["price"] and old.get("price") and l["price"] < old["price"]:
                drops.append((l, old["price"]))
            if l["price"]:
                old["price"] = l["price"]
            old["last_seen"] = now

    if first_run:
        push("BMW i4 watchdog armed", f"Tracking {len(state)} existing listings. You'll get a push for every new one.", tags="white_check_mark")
    elif len(new) > MAX_SINGLE_PUSHES:
        lines = "\n".join(f"• {eur(l['price'])} {l['title']} ({l['src']})" for l in new[:40])
        push(f"{len(new)} new BMW i4 listings", lines, tags="car")
    else:
        for l in new:
            push(f"🆕 {eur(l['price'])} · {l['title']}", f"{l['info']}\n{l['src']}", l["url"], l["img"], priority=4)
    for l, oldp in drops:
        push(f"📉 {eur(oldp)} → {eur(l['price'])} · {l['title']}", f"{l['info']}\n{l['src']}", l["url"], l["img"], tags="chart_with_downwards_trend")
    if errors:
        push("BMW i4 watchdog: source error", "\n".join(errors), tags="warning", priority=2)

    if not DRY:
        json.dump(state, open(STATE_FILE, "w"), ensure_ascii=False, indent=1, sort_keys=True)
    print(f"total={len(listings)} new={len(new)} drops={len(drops)} errors={len(errors)} first_run={first_run}")


if __name__ == "__main__":
    main()
