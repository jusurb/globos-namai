#!/usr/bin/env python3
"""Fetch the vilnius.lt long/short-term social care providers table and write a filterable HTML page.

Usage: python3 globos.py [output.html]   (default: index.html, the GitHub Pages home page)
Also writes zemelapis.html, a self-contained map (needs internet only for map tiles) that can be shared as one file.
"""
import base64
import datetime
import functools
import hashlib
import html
import io
import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from html.parser import HTMLParser

URL = ("https://vilnius.lt/savivaldybe/sveikata-ir-socialine-parama/socialines-paslaugos/"
       "istaigos-teikiancios-socialines-globos-paslaugas-senyvo-amziaus-asmenims-ir-asmenims-su-negalia")
SOURCE_LINK = URL + "#:~:text=ILGALAIK%C4%96S,S%C4%84RA%C5%A0AS"  # text fragment: browser scrolls to the table heading
HEADER = "ILGALAIKĖS/TRUMPALAIKĖS SOCIALINĖS GLOBOS PASLAUGŲ"
JUNK = re.compile(r"Pasitikrinti eilę|Atidaryti XLSX dokumentą|h?ttps?://\S+|Vilniaus miesto gyventojai|Kitų savivaldybių gyventojai")
PRICE = re.compile(r"(\d{1,3}(?: ?\d{3})*(?:,\d+)?)\s*Eur\s*/\s*(mėn|dieną|val)\.?")
SENTENCE_END = re.compile(r";|\.(?:\s|$)")
VILNIUS = (54.6872, 25.2797)
SITE_URL = "https://jusurb.github.io/globos-namai/"  # public address: canonical/og:url, sitemap.xml, map -> table link; None for local-only use
ROAD_FACTOR = 1.12  # median source km / straight-line km over exactly located facilities
GEONAMES_LT = "https://download.geonames.org/export/zip/LT.zip"  # postcodes + village names, CC BY 4.0
LEAFLET_CSS = ("https://unpkg.com/leaflet@1.9.4/dist/leaflet.css", "p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=")
LEAFLET_JS = ("https://unpkg.com/leaflet@1.9.4/dist/leaflet.js", "20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=")


def norm(s):
    return re.sub(r"\s+", " ", s).strip()


class Tables(HTMLParser):
    """Collects every <table> as rows of cells, each cell a list of non-empty text lines,
    the links per cell as [(text, href)], plus the text that precedes the table (used to find it by its heading)."""

    def __init__(self):
        super().__init__()
        self.tables, self.before, self.cell, self.in_script, self.link = [], [], None, False, None

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.in_script = True
        elif tag == "table":
            self.tables.append({"before": norm("".join(self.before)), "rows": [], "links": []})
            self.before = []
        elif tag == "tr" and self.tables:
            self.tables[-1]["rows"].append([])
            self.tables[-1]["links"].append([])
        elif tag in ("td", "th") and self.tables:
            self.cell = []
            self.tables[-1]["links"][-1].append([])
        elif tag == "a" and self.cell is not None:
            self.link = [dict(attrs).get("href") or "", len(self.cell)]
        elif tag in ("br", "p", "li", "div") and self.cell is not None:
            self.cell.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.in_script = False
        elif tag == "a" and self.link is not None:
            href, start = self.link
            self.tables[-1]["links"][-1][-1].append((norm("".join(self.cell[start:])), href))
            self.link = None
        elif tag in ("td", "th") and self.cell is not None:
            lines = [norm(l) for l in "".join(self.cell).split("\n")]
            self.tables[-1]["rows"][-1].append([l for l in lines if l])
            self.cell = None

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)
        elif not self.in_script:
            self.before.append(data)


def num(s):
    return float(s.replace(" ", "").replace(",", "."))


def first_int(s):
    """'12' -> 12, '1 mot.' -> 1, '-' -> 0, 'Siuntimus išduoda ...' -> None (unknown)."""
    m = re.search(r"\d+", s)
    return int(m[0]) if m else (0 if s.strip("- ") == "" else None)


def severe_prices(lines):
    """Prices for 'asmenims su sunkia negalia' as [{eur, unit, note, label}], note = section like 'Stacionare',
    label = what is shown: section plus the source's own wording, e.g. 'Stacionare: asmenims su sunkia negalia'."""
    parts = PRICE.split(" ".join(lines))  # [before, amount, unit, text_after, amount, unit, text_after, ...]
    section = norm(parts[0]).rstrip(":– ")
    severe = []
    for amount, unit, after in zip(parts[1::3], parts[2::3], parts[3::3]):
        chunks = SENTENCE_END.split(after)
        desc = chunks[0].lower()
        entry = {"eur": num(amount), "unit": unit, "note": section}
        if re.search(r"\(|slaug", desc):  # e.g. 'specializuota slauga', '(trumpalaikė socialinė globa)'
            entry["note"] = norm(chunks[0])
        if "sunki" in desc and "be sunk" not in desc:
            label = norm(chunks[0]) if entry["note"] in ("", norm(chunks[0])) else f"{entry['note']}: {norm(chunks[0])}"
            severe.append(dict(entry, label=label, mixed="senyvo" in desc))
        tail = norm(chunks[-1]).rstrip(":– ") if len(chunks) > 1 else ""
        if tail:  # heading for the prices that follow, e.g. 'Grupinio gyvenimo namuose:'
            section = tail
    # '1450 senyvo amžiaus ir asmenims su sunkia negalia; 1650 asmenims su sunkia negalia': the pure one wins within a section
    pure = {e["note"] for e in severe if not e["mixed"]}
    severe = [{k: v for k, v in e.items() if k != "mixed"} for e in severe if not (e["mixed"] and e["note"] in pure)]
    return severe


UNIT_KINDS = ("spec|slaug", "grup")


def unit_prices(prices, label, labels):
    """Prices for one sub-unit of a split row: 'spec. slauga' gets the nursing price, 'grup.' the group-home ones,
    the rest the others. A kind no sibling unit claims stays with everyone."""
    for kind in UNIT_KINDS:
        mine = [p for p in prices if re.search(kind, p["note"], re.I)]
        if re.search(kind, label, re.I):
            if mine:
                return mine
        elif any(re.search(kind, l, re.I) for l in labels):
            prices = [p for p in prices if p not in mine] or prices
    return prices


def distance(lines):
    m = re.search(r"(\d+(?:,\d+)?)\s*km", " ".join(lines))
    if m:
        return num(m[1])
    return 0.0 if "Vilnius" in lines else None


def spot_entries(lines):
    """[(count, label)] for each 'N vietų' mention; label names the sub-unit (padalinys, address, 'grup.' ...)."""
    out = []
    for i, line in enumerate(lines):
        m = re.search(r"(\d+)\s*viet\w*", line)
        if not m:
            continue
        label = re.sub(r"^iš kurių\s*", "", (line[:m.start()] + line[m.end():]).strip(" ,–-")).strip(" ,–-")
        if not label and i and lines[i - 1].startswith("iš kurių"):
            label = lines[i - 1].removeprefix("iš kurių").strip()
        elif not label and i + 1 < len(lines) and not re.search(r"\d|^iš kurių", lines[i + 1]):
            label = lines[i + 1].strip(" ,")
        out.append((int(m[1]), label))
    return out or [(int(l), "") for l in lines if l.isdigit()]


def address_and_contact(lines):
    i = next((k for k, l in enumerate(lines) if re.match(r"El\. ?p\.|Tel\.|\+", l)), len(lines))
    site = [l for l in lines[:i] if l.startswith("Veiklos vieta:")]
    address = site[0].removeprefix("Veiklos vieta:").strip() if site else " ".join(lines[:i])
    return address, "\n".join(lines[i:])


def website(name_links):
    """The facility name's own link; skips 'Pasitikrinti eilę' queue sheets and the generic anta.lrv.lt list.
    Only http(s) is kept, since the URL ends up in an href on our page."""
    return next((href for text, href in name_links
                 if re.match(r"https?://", href) and text and not JUNK.fullmatch(text)), None)


def main_price(prices):
    """The one severe-disability price: specialised nursing if offered, else stacionare, else group homes; cheapest on ties."""
    rank = lambda p: (0 if re.search(UNIT_KINDS[0], p["note"], re.I) else 2 if re.search(UNIT_KINDS[1], p["note"], re.I) else 1, p["eur"])
    return min(prices, key=rank) if prices else None


def total(values):
    """Merge free/queue cells of several units: '5', '7', '-' -> '12'."""
    if len(values) == 1:
        return values[0]
    nums = [x for x in map(first_int, values) if x]
    return str(sum(nums)) if nums else "-"


def parse_row(cells, links=()):
    """One row per Eil. Nr.: the main severe-disability price, with the places/queue of the units that price is for."""
    nr, name, spots, free, queue, price, contact = cells
    name = [l for l in (norm(JUNK.sub("", x)) for x in name) if l]
    address, contact = address_and_contact(contact)
    prices = severe_prices(price)
    chosen = main_price(prices)
    base = {"nr": first_int(" ".join(nr)), "website": website(links[1] if links else []), "address": address, "contact": contact, "price": [chosen] if chosen else [], "km": distance(spots),
            "place": next((l for l in spots if not re.search(r"\d", l)), "")}  # e.g. 'Palanga', 'Prienų r. sav.'
    entries = spot_entries([l for l in spots if "km" not in l])
    n = max(len(free), len(queue))
    if n > 1 and len(entries) >= n:  # several units: free/queue lines align with the last n spot counts
        units = entries[-n:]
        rest = sum(c for c, _ in units[1:])
        if len(entries) == n and "iš kurių" in " ".join(spots) and units[0][0] > rest:  # '180 vietų, iš kurių spec. slauga 40': first line is the total
            units[0] = (units[0][0] - rest, "kitos vietos")
        labels = [l for _, l in units]
        mine = [i for i, (_, l) in enumerate(units) if chosen in unit_prices(prices, l, labels)] or range(len(units))
        label = units[mine[0]][1] if len(mine) == 1 else ""
        return [dict(base, name=f"{name[0]} — {label}" if label else name[0], spots=sum(units[i][0] for i in mine),
                     free=total([free[i] if i < len(free) else "-" for i in mine]), queue=total([queue[i] if i < len(queue) else "-" for i in mine]))]
    # first line = main unit / Vilnius residents ('81 ¶ 2' = Vilniaus miesto / kitų savivaldybių gyventojai, '7 ¶ grup. 1')
    return [dict(base, name=" ".join(name), spots=entries[0][0] if entries else None,
                 free=free[0] if free else "", queue=queue[0] if queue else "")]


def page_text(page_html):
    """Visible page text, lowercased, to check that a text fragment occurs only once."""
    return norm(html.unescape(re.sub(r"(?is)<(script|style)\b.*?</\1>|<[^>]+>", " ", page_html))).lower()


def row_link(cells, text):
    """Link that scrolls to this row on vilnius.lt: the first of name / email / phone / contact line found only once on the page.
    Numbers repeat across the page's tables, so the Eil. Nr. itself can't be used."""
    contact = " ".join(cells[6])
    candidates = [norm(JUNK.sub("", cells[1][0])) if cells[1] else ""] + re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", contact) + re.findall(r"\+370[\d ()]*\d", contact) + cells[6]
    for c in candidates:
        if len(c) > 5 and text.count(c.lower()) == 1:
            return URL + "#:~:text=" + urllib.parse.quote(c, safe="").replace("-", "%2D")
    return SOURCE_LINK


def extract(page_html):
    p = Tables()
    p.feed(page_html)
    table = next((t for t in p.tables if HEADER in t["before"]), None)
    if not table:
        sys.exit(f"Table with heading '{HEADER}' not found - page layout changed?")
    head, *rows = table["rows"]
    if len(head) != 7 or "Laisvos" not in " ".join(head[3]):
        sys.exit(f"Unexpected table columns, page layout changed? {head}")
    text = page_text(page_html)
    out = [dict(r, link=row_link(row, text)) for row, links in zip(rows, table["links"][1:]) if len(row) == 7 for r in parse_row(row, links)]
    for r in out:
        r["free_n"], r["queue_n"] = first_int(r["free"]), first_int(r["queue"])
        if r["queue_n"] is None:  # e.g. 'Siuntimus išduoda Kauno m. sav.' or 'Trakų r. sav.': that municipality keeps the queue
            r["referral"], r["queue"] = re.sub(r"^Siuntimus išduoda\s*", "", r["queue"]), ""
    assert len(out) >= len(rows) - 1 and all(r["name"] for r in out), "parse produced empty rows"
    out = [r for r in out if r["price"]]  # only facilities with a 'su sunkia negalia' price
    assert len(out) > len(rows) // 2, "most rows lost their severe-disability price - price format changed?"
    return out


def km_between(a, b):
    (la1, lo1), (la2, lo2) = [(math.radians(x), math.radians(y)) for x, y in (a, b)]
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 12742 * math.asin(math.sqrt(h))


def address_queries(r):
    """Geocoder queries from most to least precise: full address, without street, then the town from the distance column."""
    a = re.sub(r"\bLT\s?-?\s?\d{4,6}\b|\b\d{5}\b", "", r["address"])
    parts = [p.strip() for p in a.split(",") if p.strip()]
    queries = [", ".join(parts), ", ".join(parts[1:]), r["place"]]
    return [q for q in dict.fromkeys(queries) if q]


def geocode(query, cache):
    if query not in cache:
        time.sleep(1.1)  # Nominatim usage policy: max 1 request/second
        url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
            {"q": query, "format": "json", "limit": 1, "countrycodes": "lt"})
        req = urllib.request.Request(url, headers={"User-Agent": "elena-globos-map/1.0 (personal script)"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                found = json.load(resp)
        except OSError as e:
            print(f"  geocoding failed for {query!r}: {e}", file=sys.stderr)
            return None  # not cached, retried next run
        cache[query] = [float(found[0]["lat"]), float(found[0]["lon"])] if found else None
    return cache[query]


@functools.cache
def postcode_places():
    """GeoNames Lithuanian post-office areas as [(postcode, place, municipality, lat, lon)]; Nominatim has no LT postcodes."""
    try:
        with urllib.request.urlopen(GEONAMES_LT, timeout=60) as resp:
            text = zipfile.ZipFile(io.BytesIO(resp.read())).read("LT.txt").decode("utf-8")
    except (OSError, zipfile.BadZipFile, KeyError) as e:
        print(f"  GeoNames postcodes unavailable: {e}", file=sys.stderr)
        return []
    return [(f[1], f[2], f[5], float(f[9]), float(f[10])) for f in (l.split("\t") for l in text.splitlines()) if len(f) > 10 and f[2].strip() and f[5].strip()]


def postcode_lookup(address, places):
    """(lat, lon) of the address's postcode, else of its village/town within the same municipality, else None.
    shortcut: GeoNames has post-office codes (59015), not every address code (59252), so the village name does most of the work."""
    codes = set(re.findall(r"\b(?:LT\s?-?\s?)?(\d{5})\b", address))
    rajonas = bool(re.search(r"\br\.|\braj\.", address))
    same_muni = lambda m: m.split()[0] in address and (" r." in m) == rajonas  # 'Vilniaus r. sav.' is not 'Vilniaus m. sav.'
    exact = [p for p in places if p[0] in codes]
    named = [p for p in places if same_muni(p[2]) and re.search(rf"(?<!\w){re.escape(p[1])}(?!\w)", address)]
    # the most specific name wins; the municipality's own town ('Kalvarijos') only if nothing else matches
    named.sort(key=lambda p: (p[1].split()[0] == p[2].split()[0], -len(p[1])))
    if exact:
        return exact[0][3:]
    if not named or any(n[1] == named[0][1] and n[3:] != named[0][3:] for n in named):  # two same-named villages: don't guess
        return None
    return named[0][3:]


def add_coords(rows, cache_path):
    """Sets lat/lon/approx on each row. A hit is trusted only if it fits the page's own 'km from Vilnius'
    (catches head-office addresses and same-named villages elsewhere)."""
    try:
        with open(cache_path, encoding="utf-8") as f:
            cache = json.load(f)
    except FileNotFoundError:
        cache = {}
    try:
        for r in rows:
            hits = [(i, p) for i, q in enumerate(address_queries(r)) if (p := geocode(q, cache))]
            fits = [(i, p) for i, p in hits
                    if r["km"] is None or r["km"] * 0.4 - 15 <= km_between(p, VILNIUS) <= r["km"] + 20]
            i, p = (fits or hits or [(1, None)])[0]
            approx = i > 0 or not fits
            if p is None or not fits or address_queries(r)[i] == r["place"]:  # only the district centre: try the village's post area
                g = postcode_lookup(r["address"], postcode_places())
                if g and (r["km"] is None or r["km"] * 0.4 - 15 <= km_between(g, VILNIUS) <= r["km"] + 20):
                    p, approx = g, True
            r["lat"], r["lon"] = p or (None, None)
            r["approx"] = approx
            if r["km"] is None and p:  # source gives no distance: estimate it from the map position
                r["km"], r["km_est"] = round(km_between(p, VILNIUS) * ROAD_FACTOR), True
    finally:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=0)


PAGE = """<!doctype html>
<html lang="lt"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
__HEAD__
<style>
 body{font:14px/1.4 system-ui,sans-serif;margin:16px;color:#222;background:#fff}
 .filters{display:flex;flex-wrap:wrap;gap:12px;align-items:end;margin:12px 0;position:sticky;top:0;background:#fff;padding:8px 0}
 label{display:flex;flex-direction:column;font-size:12px;color:#555}
 label.cb{flex-direction:row;align-items:center;gap:4px}
 input[type=number]{width:90px} input[type=search]{width:260px}
 table{border-collapse:collapse;width:100%} th,td{border:1px solid #ddd;padding:6px;vertical-align:top;text-align:left}
 th{background:#f4f4f4;cursor:pointer;white-space:nowrap;position:sticky;top:64px} td.n{text-align:right;white-space:nowrap}
 td.c{white-space:pre-line;font-size:12px} .note{color:#777;font-size:12px} small{color:#777}
 .arrow{font-size:11px} .arrow.off{color:#bbb}
__NAV_CSS__
</style></head><body>
__NAV__
<h1 style="font-size:22px;margin:12px 0 6px">Socialinės globos namai asmenims su sunkia negalia: kainos, eilės ir laisvos vietos</h1>
<p style="margin:0 0 6px;max-width:900px">__SUMMARY__</p>
<small>Šaltinis: <a href="__URL__">vilnius.lt</a>, duomenys gauti __DATE__. <span id="count"></span>. Nežinomos reikšmės (?) filtruojant paliekamos. ≈ atstumas apskaičiuotas pagal vietą žemėlapyje. Spustelėkite stulpelio antraštę, kad surikiuotumėte.</small>
<div class="filters">
 <label>Paieška<input type="search" id="q" placeholder="pavadinimas, adresas, kontaktai"></label>
 <label>Maks. atstumas, km<input type="number" id="km" min="0"></label>
 <label>Maks. kaina, €<input type="number" id="price" min="0"></label>
 <label>Maks. eilė<input type="number" id="queue" min="0"></label>
 <label>Min. vietų<input type="number" id="spots" min="0"></label>
 <label class="cb"><input type="checkbox" id="free"> tik su laisvomis vietomis</label>
</div>
<table><thead><tr id="head"></tr></thead><tbody id="body">__ROWS__</tbody></table>
<script>
const DATA = __DATA__;
const minPrice = r => r.price.length ? Math.min(...r.price.map(p => p.eur)) : null;
const COLS = [
  ["Eil. Nr.", r => r.nr, "n"], ["Įstaiga", r => r.name], ["Adresas", r => r.address], ["Vietų skaičius", r => r.spots, "n"],
  ["Laisvos vietos", r => r.free_n, "n"], ["Laukiančiųjų eilėje", r => r.queue_n, "n"],
  ["Kaina (su sunkia negalia)", minPrice, "n"], ["Kontaktai", r => r.contact], ["Atstumas, km", r => r.km, "n"],
];
let sortCol = 8, sortDir = 1;
const val = id => { const v = document.getElementById(id).value; return v === "" ? null : +v; };
const over = (x, max) => max !== null && x !== null && x > max;

function cell(tr, text, cls) { const td = tr.insertCell(); td.textContent = text; if (cls) td.className = cls; return td; }
function linkTo(el, text, href) {
  if (!href) { el.append(text); return; }
  const a = document.createElement("a"); a.href = href; a.target = "_blank"; a.rel = "noopener"; a.textContent = text;
  el.append(a);
}
function referralNote(el, r) {
  if (r.referral) { const s = document.createElement("div"); s.className = "note"; s.textContent = `Siuntimus išduoda ${r.referral}`; el.append(s); }
}

function render() {
  const q = document.getElementById("q").value.toLowerCase(), onlyFree = document.getElementById("free").checked;
  const [km, price, queue, spots] = ["km", "price", "queue", "spots"].map(val);
  const rows = DATA.filter(r =>
    (!q || (r.name + " " + r.address + " " + r.contact).toLowerCase().includes(q)) &&
    !over(r.km, km) && !over(minPrice(r), price) && !over(r.queue_n, queue) &&
    !(spots !== null && r.spots !== null && r.spots < spots) && (!onlyFree || r.free_n > 0));
  const key = COLS[sortCol][1];
  rows.sort((a, b) => {
    const x = key(a), y = key(b);
    if (x === y) return 0; if (x === null) return 1; if (y === null) return -1;
    return (typeof x === "string" ? x.localeCompare(y, "lt") : x - y) * sortDir;
  });
  const body = document.getElementById("body"); body.textContent = "";
  for (const r of rows) {
    const tr = body.insertRow();
    linkTo(cell(tr, "", "n"), String(r.nr ?? "?"), r.link); const nc = cell(tr, ""); linkTo(nc, r.name, r.website); referralNote(nc, r); cell(tr, r.address); cell(tr, r.spots ?? "?", "n");
    cell(tr, r.free || "?", "n"); cell(tr, r.queue || "?", "n");
    const p = cell(tr, r.price.length ? "" : "?", "n");
    r.price.forEach((x, i) => {
      if (i) p.append(document.createElement("br"));
      p.append(`${x.eur.toFixed(2)} €/${x.unit}`);
      if (x.label) { const s = document.createElement("div"); s.className = "note"; s.textContent = x.label; p.append(s); }
    });
    cell(tr, r.contact, "c"); cell(tr, r.km === null ? "?" : (r.km_est ? "≈ " : "") + r.km, "n").title = r.km_est ? "apskaičiuota pagal vietą žemėlapyje" : "";
  }
  document.getElementById("count").textContent = `Rodoma ${rows.length} iš ${DATA.length}`;
  head.querySelectorAll("th").forEach((th, i) => {
    const on = i === sortCol;
    th.querySelector(".arrow").textContent = on ? (sortDir > 0 ? "▲" : "▼") : "↕";
    th.querySelector(".arrow").classList.toggle("off", !on);
    th.setAttribute("aria-sort", on ? (sortDir > 0 ? "ascending" : "descending") : "none");
  });
}

const head = document.getElementById("head");
COLS.forEach(([title], i) => {
  const th = document.createElement("th"); th.textContent = title + " ";
  const arrow = document.createElement("span"); arrow.className = "arrow"; th.append(arrow);
  th.onclick = () => { sortDir = sortCol === i ? -sortDir : 1; sortCol = i; render(); };
  head.append(th);
});
document.querySelectorAll("input").forEach(el => el.addEventListener("input", render));
render();
</script></body></html>
"""

MAP_PAGE = """<!doctype html>
<html lang="lt"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
__HEAD__
<style>__LEAFLET_CSS__</style>
<script>__LEAFLET_JS__</script>
<style>
 html,body{height:100%;margin:0;font:14px/1.4 system-ui,sans-serif;color:#222;background:#fff}
 body{display:flex;flex-direction:column}
 header{padding:8px 16px;display:flex;gap:16px;align-items:center;flex-wrap:wrap} #map{flex:1;min-height:300px}
__NAV_CSS__
 small{color:#666} .legend{display:inline-flex;gap:4px;align-items:center;flex-wrap:wrap}
 .pin{transform:translate(-50%,-50%);white-space:nowrap;font:600 11px/1 system-ui,sans-serif;padding:3px 5px;
  border-radius:4px;border:2px solid #fff;box-shadow:0 1px 3px rgba(0,0,0,.35);cursor:pointer;display:inline-block}
 .pin.approx{border:2px dashed #333}
 /* the marker box sits below-right of its centred label and would steal clicks from nearby labels; only the label is clickable */
 .leaflet-marker-icon.leaflet-interactive{pointer-events:none} .leaflet-marker-icon .pin{pointer-events:auto}
 #bands button.pin{transform:none} #bands button[aria-pressed=false]{opacity:.35}
 .pop b{display:block;margin-top:10px} .pop b:first-child{margin-top:0} .pop .a{color:#666;font-size:12px}
 .pop .c{white-space:pre-line;font-size:12px}
</style></head><body>
<header>__NAV__<h1 style="font-size:16px;margin:0">Globos namų žemėlapis: kainos asmenims su sunkia negalia</h1> <small>duomenys: <a href="__URL__" target="_blank" rel="noopener">vilnius.lt</a>, __DATE__</small>
 <small class="legend">Kaina su sunkia negalia, €/mėn. (spustelėkite spalvą – rodys tik ją): <span id="bands" class="legend"></span>
 · <span class="pin approx" style="transform:none;background:#fff">apytikslė vieta</span> (gyvenvietė / rajonas)
 <span id="missing"></span></small></header>
<div id="map"></div>
<script>
const DATA = __DATA__;
// traffic light: green = cheap, yellow = medium, red = expensive; the price text is always on the label too
const BANDS = [
  {max: 1600, bg: "#2e8b3e", fg: "#fff", label: "< 1600 (pigu)"},
  {max: 2000, bg: "#f2c12e", fg: "#222", label: "1600–1999 (vidutiniškai)"},
  {max: Infinity, bg: "#c8322b", fg: "#fff", label: "≥ 2000 (brangu)"},
];
const UNKNOWN = {bg: "#e4e4e1", fg: "#333", label: "nenurodyta"};
const map = L.map("map", {zoomSnap: 0.25});  // finer zoom so Lithuania fills narrow phone screens
// Esri tiles load from a file opened from disk; OpenStreetMap and CARTO block those (no Referer)
L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}", {
  maxZoom: 19, attribution: "Tiles &copy; Esri · vietos: &copy; OpenStreetMap, GeoNames"}).addTo(map);
const sameSpot = new Map();  // facilities geocoded to the same point (e.g. district centre) each get a label, stacked
function linkTo(el, text, href) {
  if (!href) { el.append(text); return; }
  const a = document.createElement("a"); a.href = href; a.target = "_blank"; a.rel = "noopener"; a.textContent = text;
  el.append(a);
}
const add = (parent, tag, text, cls) => { const e = document.createElement(tag); e.textContent = text; if (cls) e.className = cls; parent.append(e); };
const points = [], layers = new Map();  // price band -> its markers, so the legend can show one band at a time
for (const r of DATA.filter(r => r.lat !== null)) {
  const pop = document.createElement("div");
  pop.className = "pop";
  add(pop, "b", "");
  linkTo(pop.lastChild, `${r.nr}.`, r.link); pop.lastChild.append(" "); linkTo(pop.lastChild, r.name, r.website);
  add(pop, "div", r.price.length
    ? r.price.map(p => `${p.eur.toFixed(2)} €/${p.unit}` + (p.label ? ` (${p.label})` : "")).join("; ")
    : "Kaina (su sunkia negalia) nenurodyta");
  add(pop, "div", `Vietų: ${r.spots ?? "?"} · laisvų: ${r.free || "?"} · eilėje: ${r.queue || "?"}`, "a");
  if (r.referral) add(pop, "div", `Siuntimus išduoda ${r.referral}`, "a");
  add(pop, "div", r.address, "a");
  add(pop, "div", r.contact, "c");
  if (r.approx) add(pop, "div", "Apytikslė vieta", "a");
  const prices = r.price.map(p => p.eur), min = prices.length ? Math.min(...prices) : null;
  const band = min === null ? UNKNOWN : BANDS.find(b => min < b.max);
  const label = min === null ? "?" : `${Math.round(min)} €`;
  const ll = [r.lat, r.lon], k = ll.join(","), stack = sameSpot.get(k) || 0;
  sameSpot.set(k, stack + 1);
  points.push(ll);
  L.marker(ll, {icon: L.divIcon({className: "", iconSize: null,
      html: `<span class="pin${r.approx ? " approx" : ""}" style="background:${band.bg};color:${band.fg};margin-top:${stack * 22}px">${label}</span>`})})
    .bindPopup(pop).bindTooltip(Object.assign(document.createElement("span"), {textContent: `${r.nr}. ${r.name}`}))  // a string would be parsed as HTML
    .addTo(layers.get(band) || layers.set(band, L.layerGroup().addTo(map)).get(band));
}
let shown = null;  // null = all bands
const buttons = [...BANDS, UNKNOWN].filter(b => layers.has(b)).map(b => {
  const el = document.createElement("button");
  el.type = "button"; el.className = "pin"; el.textContent = b.label; el.setAttribute("aria-pressed", "true");
  Object.assign(el.style, {background: b.bg, color: b.fg});
  el.onclick = () => {
    shown = shown === b ? null : b;  // clicking the shown band again brings all back
    for (const [band, layer] of layers) shown === null || band === shown ? layer.addTo(map) : layer.remove();
    buttons.forEach(([bb, e]) => e.setAttribute("aria-pressed", String(shown === null || bb === shown)));
  };
  document.getElementById("bands").append(el);
  return [b, el];
});
map.fitBounds(points, {padding: [30, 30]});
const missing = DATA.filter(r => r.lat === null);
if (missing.length) document.getElementById("missing").textContent =
  ` · nerasta žemėlapyje: ${missing.map(r => `${r.nr}. ${r.name}`).join(", ")}`;
</script></body></html>
"""


ICON = "data:image/svg+xml," + urllib.parse.quote('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><rect width="16" height="16" rx="3" fill="#2e8b3e"/><path d="M3 8l5-4 5 4v5H3z" fill="#fff"/></svg>')


def summary(rows, date):
    """Plain-text answer for readers and crawlers: how many, price range, where places are free."""
    prices = sorted(r["price"][0]["eur"] for r in rows if r["price"][0]["unit"] == "mėn")
    free = sum(1 for r in rows if (r["free_n"] or 0) > 0)
    return (f"{len(rows)} ilgalaikės ir trumpalaikės socialinės globos įstaigos iš Vilniaus miesto savivaldybės skelbiamo sąrašo. "
            f"Mėnesio kaina asmeniui su sunkia negalia – nuo {prices[0]:.0f} iki {prices[-1]:.0f} € (mediana {prices[len(prices) // 2]:.0f} €); "
            f"laisvų vietų nurodyta {free} įstaigose. Lentelėje – vietų skaičius, eilės, kontaktai ir atstumas nuo Vilniaus; duomenys {date}.")


def head(title, description, page, date, dataset=False):
    """<title>, description, social preview, canonical (once SITE_URL is known) and, for the table, Dataset JSON-LD."""
    esc = lambda t: html.escape(t, quote=True)
    tags = [f"<title>{esc(title)}</title>", f'<meta name="description" content="{esc(description)}">',
            f'<link rel="icon" href="{ICON}">', '<meta property="og:type" content="website">', '<meta property="og:locale" content="lt_LT">',
            f'<meta property="og:title" content="{esc(title)}">', f'<meta property="og:description" content="{esc(description)}">']
    url = SITE_URL and SITE_URL + ("" if page == "index.html" else page)
    if url:
        tags += [f'<link rel="canonical" href="{esc(url)}">', f'<meta property="og:url" content="{esc(url)}">']
    if dataset:
        ld = {"@context": "https://schema.org", "@type": "Dataset", "name": title, "description": description, "inLanguage": "lt",
              "dateModified": date, "isBasedOn": URL, "spatialCoverage": {"@type": "Place", "name": "Lietuva"},
              "variableMeasured": ["Vietų skaičius", "Laisvos vietos", "Laukiančiųjų eilėje", "Mėnesio kaina asmeniui su sunkia negalia, €", "Atstumas nuo Vilniaus, km"]}
        if url:
            ld["url"] = url
        tags.append('<script type="application/ld+json">' + json.dumps(ld, ensure_ascii=False).replace("</", "<\\/") + "</script>")
    return "\n".join(tags)


def static_rows(rows):
    """Plain table rows in the HTML itself, so the content is readable without JavaScript; the script redraws them."""
    esc = html.escape
    km = lambda r: "?" if r["km"] is None else ("≈ " if r.get("km_est") else "") + str(r["km"])
    return "".join(f"<tr><td>{r['nr']}</td><td>{esc(r['name'])}</td><td>{esc(r['address'])}</td><td>{r['spots'] or '?'}</td>"
                   f"<td>{esc(r['free'] or '?')}</td><td>{esc(r['queue'] or '?')}</td><td>{r['price'][0]['eur']:.2f} €/{r['price'][0]['unit']}</td>"
                   f"<td>{esc(r['contact'])}</td><td>{km(r)}</td></tr>" for r in rows)


NAV_CSS = """ .nav{display:inline-flex;gap:4px;padding:4px;border-radius:999px;background:#eef5ef;border:1px solid #cfe3d3}
 .nav a{display:inline-flex;align-items:center;gap:8px;padding:13px 18px;border-radius:999px;color:#1f6b2d;text-decoration:none;
  font:600 15px/1 system-ui,sans-serif;transition:background .15s}
 .nav a:hover{background:#dcebdf} .nav a:focus-visible{outline:2px solid #1f6b2d;outline-offset:2px}
 .nav a[aria-current=page]{background:#2e8b3e;color:#fff;box-shadow:0 1px 3px rgba(0,0,0,.25)}
 .nav svg{width:18px;height:18px;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}"""
NAV_ICONS = {"table": '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M3 15h18M9 4v16"/></svg>',
             "map": '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 4 3 6v14l6-2 6 2 6-2V4l-6 2z"/><path d="M9 4v14M15 6v14"/></svg>'}


def nav(active, table_href, map_href):
    """Same two tabs on both pages, current one filled: easy to see and to tap on a phone."""
    tab = lambda key, href, label: (f'<a href="{html.escape(href)}"{" aria-current=page" if key == active else ""}>'
                                    f"{NAV_ICONS[key]}{label}</a>")
    return f'<nav class="nav" aria-label="Rodinys">{tab("table", table_href, "Lentelė")}{tab("map", map_href, "Žemėlapis")}</nav>'


def fill(template, **values):
    """Replace __NAME__ placeholders in one pass, so text from the source page is never scanned for placeholders again."""
    return re.sub(r"__([A-Z_]+)__", lambda m: values[m[1]] if m[1] in values else m[0], template)


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else "index.html"
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        rows = extract(resp.read().decode("utf-8"))
    out_dir = os.path.dirname(out_path)
    print(f"{len(rows)} facilities, geocoding addresses (cached in geocode_cache.json)...")
    add_coords(rows, os.path.join(out_dir, "geocode_cache.json"))
    data = json.dumps(rows, ensure_ascii=False).replace("</", "<\\/")  # page text must not close our <script>
    date = datetime.date.today().isoformat()
    page_name, map_name = os.path.basename(out_path), "zemelapis.html"
    text = summary(rows, date)
    table_head = head("Globos namai asmenims su sunkia negalia – kainos, eilės, laisvos vietos | Vilniaus sąrašas",
                      f"{text.split('. ')[0]}: mėnesio kainos (nuo {text.split('nuo ')[1].split(' (')[0]}), laisvos vietos, eilės, kontaktai ir atstumas nuo Vilniaus. Atnaujinta {date}.",
                      page_name, date, dataset=True)
    map_head = head("Globos namų žemėlapis – kainos asmenims su sunkia negalia",
                    f"Interaktyvus {len(rows)} socialinės globos įstaigų žemėlapis: mėnesio kaina asmeniui su sunkia negalia spalvomis "
                    f"(pigu / vidutiniškai / brangu), eilės, laisvos vietos ir kontaktai. Duomenys iš vilnius.lt, {date}.", map_name, date)
    # the map is also shared as a single file, so its table link is absolute once the site has a public address
    table_href = SITE_URL + ("" if page_name == "index.html" else page_name) if SITE_URL else page_name
    page = fill(PAGE, HEAD=table_head, NAV=nav("table", table_href, map_name), NAV_CSS=NAV_CSS, SUMMARY=html.escape(text),
                ROWS=static_rows(rows), URL=SOURCE_LINK, DATE=date, DATA=data)
    map_path = os.path.join(out_dir, map_name)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(page)
    with open(map_path, "w", encoding="utf-8") as f:
        f.write(fill(MAP_PAGE, HEAD=map_head, NAV=nav("map", table_href, map_name), NAV_CSS=NAV_CSS, URL=SOURCE_LINK, LEAFLET_CSS=fetch_verified(*LEAFLET_CSS),
                     LEAFLET_JS=fetch_verified(*LEAFLET_JS), DATE=date, DATA=data))
    if SITE_URL:
        urls = "".join(f"<url><loc>{html.escape(SITE_URL + ('' if n == 'index.html' else n))}</loc><lastmod>{date}</lastmod></url>" for n in (page_name, map_name))
        with open(os.path.join(out_dir, "sitemap.xml"), "w", encoding="utf-8") as f:
            f.write(f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>\n')
    located = sum(r["lat"] is not None for r in rows)
    print(f"-> {out_path}, {map_path} ({located}/{len(rows)} on map, {sum(r['approx'] for r in rows)} approximate)")


def fetch_verified(url, sha256_b64):
    """Leaflet is inlined so the map is one shareable file; check it against the hash Leaflet publishes."""
    with urllib.request.urlopen(url, timeout=60) as resp:
        body = resp.read()
    if base64.b64encode(hashlib.sha256(body).digest()).decode() != sha256_b64:
        sys.exit(f"{url} does not match its published sha256 - refusing to inline it")
    return body.decode("utf-8")


if __name__ == "__main__":
    main()
