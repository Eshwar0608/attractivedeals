#!/usr/bin/env python3
"""Find place-verified Commons URLs via English Wikipedia article images."""
import csv
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

USER_AGENT = "AttractiveDealsPlaceVerify/1.0"
SLEEP = 2.5

# Wikipedia article title for each place id (48 zero-verified)
WIKI_TITLES: dict[str, str] = {
    "cmpy1xdbo002c04l5gb0lcug6": "Arambol",
    "cmpy1ws3y002204l5kbrk6xlo": "Divar",
    "cmpy1wq1p001w04l5w2zze9b5": "Mollem National Park",
    "cmpy1y8yu002d04l530y7g05s": "Dona Paula",
    "cmpy1wpt1001v04l5ku129twm": "Fontainhas (quarter)",
    "cmpy1wrdu001z04l5jlgww9eb": "Netravali",
    "cmpy1wrmj002004l5gxl88r8n": "Arvalem Falls",
    "cmpy1xcpw002a04l544kqh4t5": "Grand Island, Goa",
    "cmpy1ktm6000n04l5gbltk6cf": "Chandra Taal",
    "cmpy1lknk000u04l5kwelzzhn": "Chail, Himachal Pradesh",
    "cmpy1k88j000d04l569yhp49s": "Chamba, Himachal Pradesh",
    "cmpy1k7zz000c04l5e4nuzvjq": "Great Himalayan National Park",
    "cmpy1k7iw000a04l5v2pw56dy": "Jibhi",
    "cmpy1lkfc000t04l5rsqd8tf9": "Kasauli",
    "cmptwumk6000705l1a97cl6ve": "Kullu",
    "cmpy1k7ac000904l5z4m14re2": "Mashobra",
    "cmpy1k9fc000h04l5r6nqmw40": "Narkanda",
    "cmpy1k6bw000704l5os1l67e6": "Palampur, Himachal Pradesh",
    "cmpy1k96t000g04l5vjxl61mq": "Rewalsar Lake",
    "cmpy1ks8o000k04l5sjl8242i": "Atal Tunnel",
    "cmpy28v4v002f04l5gzdnqgty": "Yusmarg",
    "cmpxzwzte000104l7edddvnsn": "Chikmagalur",
    "cmpxzgayv000c04jrlbkcvekj": "Mekedatu",
    "cmpy0veqh000904jo421ziqhy": "Ashtamudi Lake",
    "cmpy0vzcf000a04jot88nqq56": "Banasura Sagar Dam",
    "cmpy0vdj4000804jod3wkmo8m": "Thommankuthu",
    "cmpy1cxhm000404l56hzuwsjz": "Karjat",
    "cmpy1bev3000104l5fpzpwwio": "Karnala Bird Sanctuary",
    "cmpmp7d1v000t04jysyjcv4p9": "Tiger's Leap",
    "cmpy1bfom000204l58lvldmo6": "Gorai Beach",
    "cmpy1rcx6001g04l54oqca9r5": "Almora",
    "cmpy1r97b001004l51q5syv6n": "Bhimtal",
    "cmpy1sblt001r04l5k2lcqyw4": "Binsar Wildlife Sanctuary",
    "cmpy1ratv001704l5d3989qn2": "Chakrata",
    "cmpy1rbzp001c04l59fhhf9sc": "Robber's Cave (Dehradun)",
    "cmpy1rbrc001b04l5j8mrmpv7": "Sahastradhara",
    "cmpy1rqbf001n04l5iq9iqaw6": "Hemkund",
    "cmpy1rad5001504l5fsnbpkix": "Kanatal",
    "cmpy1sbue001s04l5c0a1jw69": "Khirsu",
    "cmpy1ra4r001404l5a10hrt6w": "Lansdowne, India",
    "cmpy1rq31001m04l52zeb2mlo": "Mana, India",
    "cmpy1saiv001q04l5k06av7cu": "Mukteshwar",
    "cmpy1s9x6001p04l5tyix4tnj": "Munsiyari",
    "cmpy1r9fo001104l56xhxjlv0": "Naukuchiatal",
    "cmpy1rcot001f04l5qjy0zc2i": "Tehri Dam",
    "cmpy1rbiz001a04l5fb5jt620": "Ranikhet",
    "cmpy1rcgg001e04l5c46jso5d": "Rudraprayag",
    "cmpy1sc2x001t04l5jqj195tn": "Sitlakhet",
}

# Required substring in filename (normalized) per place id — at least one must match
REQUIRED_TOKENS: dict[str, list[str]] = {
    "cmpy1wpt1001v04l5ku129twm": ["fontainhas", "panaji", "panjim", "goa"],
    "cmpy1xcpw002a04l544kqh4t5": ["grand island", "ilha grande", "island goa"],
    "cmpmp7d1v000t04jysyjcv4p9": ["tiger", "lonavala", "tigers leap", "tiger's leap"],
    "cmpy1ks8o000k04l5sjl8242i": ["atal tunnel", "sissu", "rohtang"],
    "cmpy1rcot001f04l5qjy0zc2i": ["tehri"],
    "cmpy1rbzp001c04l59fhhf9sc": ["robber", "guchhupani", "dehradun"],
    "cmpxzgayv000c04jrlbkcvekj": ["sangama", "mekedatu", "kaveri"],
}

# Reject if filename contains these (wrong place / generic)
GLOBAL_REJECT = [
    "anjuna", "palolem", "calangute", "baga_beach", "basilica", "aguada",
    "chapora", "shimla", "manali", "mussoorie", "ooty", "munnar",
    "forminguinhas", "corvosanto", "cape verde",
]


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def api(url: str, retries: int = 5) -> dict:
    last_err = None
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 429:
                time.sleep(15 * (attempt + 1))
                continue
            raise
    raise last_err  # type: ignore[misc]


def wiki_images(title: str) -> list[str]:
    params = urllib.parse.urlencode(
        {
            "action": "query",
            "format": "json",
            "titles": title,
            "prop": "images",
            "imlimit": "50",
        }
    )
    data = api(f"https://en.wikipedia.org/w/api.php?{params}")
    pages = data.get("query", {}).get("pages", {})
    p = next(iter(pages.values()))
    if "missing" in p:
        return []
    return [i["title"] for i in p.get("images", [])]


def commons_url(file_title: str) -> str | None:
    params = urllib.parse.urlencode(
        {
            "action": "query",
            "format": "json",
            "titles": file_title,
            "prop": "imageinfo",
            "iiprop": "url",
        }
    )
    data = api(f"https://commons.wikimedia.org/w/api.php?{params}")
    for p in data.get("query", {}).get("pages", {}).values():
        return p.get("imageinfo", [{}])[0].get("url")
    return None


def place_tokens(name: str, city: str, place_id: str) -> list[str]:
    if place_id in REQUIRED_TOKENS:
        return REQUIRED_TOKENS[place_id]
    tokens = []
    for part in re.split(r"[\s,()/-]+", name + " " + city):
        p = norm(part)
        if len(p) >= 4 and p not in (
            "beach", "lake", "falls", "waterfall", "temple", "fort",
            "wildlife", "sanctuary", "national", "park", "island",
            "village", "quarter", "tunnel", "scuba", "bird", "point",
        ):
            tokens.append(p.replace(" ", ""))
            if " " in p:
                tokens.append(p)
    # full name compact
    compact = norm(name).replace(" ", "")
    if len(compact) >= 5:
        tokens.append(compact)
    return list(dict.fromkeys(tokens))


def matches_place(fn: str, tokens: list[str]) -> bool:
    fnn = norm(fn.replace("_", " ").replace("%2C", ","))
    fn_compact = fnn.replace(" ", "")
    for t in tokens:
        tc = t.replace(" ", "")
        if len(tc) >= 4 and (tc in fn_compact or tc in fnn):
            return True
    return False


def pick_urls(place_id: str, name: str, city: str, wiki_title: str) -> list[str]:
    skip_ext = (".svg", ".djvu", ".webm", ".ogv", ".gif")
    skip_words = (
        "icon", "logo", "flag", "map", "coat of arms", "symbol",
        "locator", "location", "diagram", "chart", "emblem",
    )
    tokens = place_tokens(name, city, place_id)
    files = wiki_images(wiki_title)
    time.sleep(SLEEP)
    urls = []
    seen = set()
    for ft in files:
        low = ft.lower()
        if not low.startswith("file:"):
            continue
        if any(low.endswith(x) for x in skip_ext):
            continue
        if any(w in low for w in skip_words):
            continue
        fn = ft[5:]  # drop File:
        fnn = norm(fn)
        if any(b in fnn for b in GLOBAL_REJECT):
            continue
        if not matches_place(fn, tokens):
            continue
        time.sleep(0.8)
        url = commons_url(ft)
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
        if len(urls) >= 5:
            break
    return urls


def main():
    report_path = Path("/workspace/verified_images_report.json")
    out_csv = Path("/workspace/verified_place_images_final.csv")
    out_json = Path("/workspace/verified_place_images_final.json")
    supplement = Path("/workspace/verified_images_supplement.json")

    with report_path.open() as f:
        report = json.load(f)
    by_id = {r["id"]: r for r in report}

    added = {}
    for pid, wiki_title in WIKI_TITLES.items():
        r = by_id.get(pid)
        if not r:
            continue
        if r.get("verified_urls"):
            print(f"= {r['name']}: already {len(r['verified_urls'])}")
            continue
        urls = pick_urls(pid, r["name"], r.get("city", ""), wiki_title)
        if urls:
            r["verified_urls"] = urls
            r["source"] = "wikipedia_article_images"
            added[pid] = {"name": r["name"], "wiki": wiki_title, "urls": urls}
            print(f"+ {r['name']}: {len(urls)}")
        else:
            print(f"- {r['name']} ({wiki_title})")
        time.sleep(SLEEP)
        with report_path.open("w") as f:
            json.dump(report, f, indent=2)

    with supplement.open("w") as f:
        json.dump(added, f, indent=2)

    with out_json.open("w") as f:
        json.dump(report, f, indent=2)

    rows = []
    for r in report:
        urls = r.get("verified_urls") or []
        rows.append(
            {
                "id": r["id"],
                "name": r["name"],
                "city": r.get("city", ""),
                "state": r.get("state", ""),
                "verified_count": len(urls),
                "verified_images_pipe": "|".join(urls),
            }
        )
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    still = sum(1 for r in report if not r.get("verified_urls"))
    total = sum(len(r.get("verified_urls") or []) for r in report)
    print(f"\nTotal verified URLs: {total}, places still empty: {still}")


if __name__ == "__main__":
    main()
