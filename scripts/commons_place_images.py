#!/usr/bin/env python3
"""Search Wikimedia Commons for place-specific images and verify URLs."""
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

USER_AGENT = "AttractiveDealsPlaceVerify/1.0 (educational; contact: local)"


def normalize(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s


def tokens_from_place(name: str, city: str = "") -> list[str]:
    parts = re.split(r"[\s,()/-]+", f"{name} {city}")
    out = []
    for p in parts:
        p = p.strip().lower()
        if len(p) < 3:
            continue
        skip = {
            "beach", "lake", "falls", "fall", "waterfall", "temple", "fort",
            "wildlife", "sanctuary", "national", "park", "island", "point",
            "village", "quarter", "dam", "cave", "sahib", "tunnel", "scuba",
            "bird", "tiger", "grand", "latin", "bubbling", "wildlife",
        }
        if p in skip:
            continue
        out.append(normalize(p))
    # Also use full normalized name chunks
    for chunk in re.split(r"\s+", name):
        c = normalize(chunk)
        if len(c) >= 4:
            out.append(c)
    return list(dict.fromkeys(out))


def filename_from_url(url: str) -> str:
    path = urllib.parse.urlparse(url).path
    name = path.split("/")[-1]
    return urllib.parse.unquote(name).lower()


def url_matches_place(url: str, name: str, city: str, extra_aliases: list[str] | None = None) -> bool:
    fn = filename_from_url(url)
    fn_norm = normalize(fn.replace(".jpg", "").replace(".jpeg", "").replace(".png", ""))
    tokens = tokens_from_place(name, city)
    if extra_aliases:
        tokens.extend(normalize(a) for a in extra_aliases if len(normalize(a)) >= 3)
    if not tokens:
        return False
    # Require at least one strong token match in filename
    for t in tokens:
        if len(t) >= 5 and t in fn_norm:
            return True
        if len(t) >= 4 and t in fn_norm:
            return True
    return False


def commons_search(query: str, limit: int = 8) -> list[str]:
    params = urllib.parse.urlencode(
        {
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": f'filetype:bitmap {query}',
            "gsrlimit": str(limit),
            "prop": "imageinfo",
            "iiprop": "url",
        }
    )
    req = urllib.request.Request(
        f"https://commons.wikimedia.org/w/api.php?{params}",
        headers={"User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        print(f"  API error for {query!r}: {e}", file=sys.stderr)
        return []
    pages = data.get("query", {}).get("pages", {})
    urls = []
    for p in pages.values():
        ii = p.get("imageinfo", [{}])[0]
        u = ii.get("url")
        if u and "upload.wikimedia.org" in u:
            urls.append(u)
    return urls


# Manual aliases for tricky place names
ALIASES: dict[str, list[str]] = {
    "Arambol Beach": ["Arambol"],
    "Divar Island": ["Divar"],
    "Bhagwan Mahavir Wildlife Sanctuary": ["Bhagwan Mahavir", "Mollem", "Molem"],
    "Dona Paula": ["Dona Paula"],
    "Fontainhas Latin Quarter": ["Fontainhas", "Panaji"],
    "Netravali Bubbling Lake": ["Netravali", "Budbudyachi Tali"],
    "Harvalem Waterfall": ["Harvalem", "Arvalem"],
    "Grand Island Scuba": ["Grand Island Goa", "Ilha Grande Goa"],
    "Chandratal Lake": ["Chandratal", "Chandra Taal"],
    "Chail": ["Chail Himachal"],
    "Chamba": ["Chamba Himachal", "Chamba town"],
    "Great Himalayan National Park": ["GHNP", "Great Himalayan National Park"],
    "Jibhi": ["Jibhi"],
    "Kasauli": ["Kasauli"],
    "Kullu": ["Kullu", "Kullu valley"],
    "Mashobra": ["Mashobra"],
    "Narkanda": ["Narkanda", "Hatupur"],
    "Palampur": ["Palampur"],
    "Rewalsar Lake": ["Rewalsar", "Rewalsar lake"],
    "Atal Tunnel Sissu": ["Atal Tunnel", "Sissu", "Rohtang"],
    "Yusmarg": ["Yusmarg"],
    "Chikmagalur": ["Chikmagalur", "Chikkamagaluru"],
    "Sangama": ["Sangama", "Mekedatu", "Kaveri sangam"],
    "Ashtamudi Lake": ["Ashtamudi"],
    "Banasura Sagar Dam": ["Banasura", "Banasurasagar"],
    "Thommankuthu": ["Thommankuthu"],
    "Karjat": ["Karjat"],
    "Karnala Bird Sanctuary": ["Karnala fort", "Karnala"],
    "Lonavala Tiger Point": ["Tiger Point Lonavala", "Tiger's Leap"],
    "Gorai Beach": ["Gorai", "Gorai beach Mumbai"],
    "Almora": ["Almora"],
    "Bhimtal": ["Bhimtal"],
    "Binsar Wildlife Sanctuary": ["Binsar"],
    "Chakrata": ["Chakrata"],
    "Robbers Cave": ["Guchhupani", "Robbers Cave Dehradun"],
    "Sahastradhara": ["Sahastradhara"],
    "Hemkund Sahib": ["Hemkund", "Gurudwara Hemkund"],
    "Kanatal": ["Kanatal"],
    "Khirsu": ["Khirsu"],
    "Lansdowne": ["Lansdowne Uttarakhand"],
    "Mana Village": ["Mana village", "Mana Badrinath"],
    "Mukteshwar": ["Mukteshwar"],
    "Munsiyari": ["Munsiyari"],
    "Naukuchiatal": ["Naukuchiatal"],
    "Tehri Lake": ["Tehri dam", "New Tehri"],
    "Ranikhet": ["Ranikhet"],
    "Rudraprayag": ["Rudraprayag"],
    "Sitlakhet": ["Sitlakhet"],
}


def find_images(name: str, city: str, state: str) -> list[str]:
    aliases = ALIASES.get(name, [])
    queries = [name, f"{name} {state}", f"{city} {name}" if city else name]
    if aliases:
        queries.extend(aliases[:3])
    seen = set()
    verified = []
    reject_wrong = [
        "anjuna", "palolem", "calangute", "shimla", "manali", "mussoorie",
        "basilica", "aguada", "chapora", "ooty", "munnar", "alleppey",
    ]
    for q in queries:
        if len(verified) >= 5:
            break
        for url in commons_search(q, limit=10):
            if url in seen:
                continue
            seen.add(url)
            fn = filename_from_url(url)
            if any(bad in fn for bad in reject_wrong):
                continue
            if url_matches_place(url, name, city, aliases):
                verified.append(url)
        time.sleep(0.35)
    return verified[:5]


def main():
    report_path = Path("/workspace/verified_images_report.json")
    csv_path = Path(
        "/home/ubuntu/.cursor/projects/workspace/uploads/"
        "Supabase_Snippet_Inspect_Specific_Public_Columns__1__e3f8.csv"
    )
    out_csv = Path("/workspace/verified_place_images_final.csv")
    out_json = Path("/workspace/verified_place_images_final.json")

    with report_path.open() as f:
        report = json.load(f)

    zero = [r for r in report if not r.get("verified_urls")]
    print(f"Searching Commons for {len(zero)} places with no verified URLs...")
    for r in zero:
        found = find_images(r["name"], r.get("city", ""), r.get("state", ""))
        if found:
            r["verified_urls"] = found
            r["commons_added"] = True
            print(f"  + {r['name']}: {len(found)} URLs")
        else:
            print(f"  - {r['name']}: none found")

    # Write final JSON
    with out_json.open("w") as f:
        json.dump(report, f, indent=2)

    # Write CSV for Supabase import
    import csv

    rows_out = []
    for r in report:
        urls = r.get("verified_urls") or []
        rows_out.append(
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
        w = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()))
        w.writeheader()
        w.writerows(rows_out)

    still_zero = sum(1 for r in report if not r.get("verified_urls"))
    total_urls = sum(len(r.get("verified_urls") or []) for r in report)
    print(f"\nFinal: {total_urls} verified URLs, {still_zero} places still empty")
    print(f"Wrote {out_csv} and {out_json}")


if __name__ == "__main__":
    main()
