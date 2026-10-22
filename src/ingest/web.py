"""ingest_web (Part 0): crawl the synthetic web from its sitemap into data/web/.

  - URLs come from the sitemap named in params web.sitemap_url (from variant.json).
  - Allowlist: https only, on the synthetic-web host (web.host) or EDGAR; everything else is refused.
  - robots.txt of the synthetic host is respected (urllib.robotparser).
  - deal_id comes from the URL's first path segment matched to the deal code-names in variant.json
    ("Project Wren" -> /wren/... -> deal_a). A page under no deal's folder (e.g. a competitor) is
    'public', flagged deal_path_unmatched and printed, so a human can check it.
  - One lantern/2.0 record per visible block (h1-h6 -> Title; p, li, td -> Text), tier 5,
    web_span citation (url + tag + its index on the page); script/style/nav/noscript removed.
  - Saved: data/web/pages/<doc_id>.html (the bytes we parsed), data/web/records.jsonl,
    data/web/crawl_log.csv (every sitemap URL: kept or refused, and why).

Usage:  python -m src.ingest.web --output data/web
Offline test against a local copy of the site (URLs on web.host map to files under --mirror):
        python -m src.ingest.web --mirror <site dir> --variant <variant.json> --output /tmp/web_out
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import xml.etree.ElementTree as ET
from importlib.metadata import version
from pathlib import Path
from urllib import robotparser
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from src.ingest import edgar
from src.ingest.records import write_jsonl

EDGAR_HOSTS = {"www.sec.gov", "sec.gov", "data.sec.gov"}
DROP_TAGS = ["script", "style", "nav", "noscript"]
BLOCK_TAGS = {"h1": "Title", "h2": "Title", "h3": "Title", "h4": "Title", "h5": "Title", "h6": "Title",
              "p": "Text", "li": "Text", "td": "Text"}


def fetch(url: str, params: dict, mirror: Path | None) -> bytes:
    """Online: polite GET (edgar._get: User-Agent, rate limit, raise on HTTP errors).
    Offline (--mirror): https://<web.host>/<path> -> <mirror>/<path>."""
    if mirror is not None:
        return (mirror / urlparse(url).path.lstrip("/")).read_bytes()
    return edgar.get_bytes(url, params)


def sitemap_urls(xml_bytes: bytes) -> list[str]:
    root = ET.fromstring(xml_bytes)
    return [el.text.strip() for el in root.iter() if el.tag.endswith("loc") and el.text]


def deal_paths(variant: dict) -> dict[str, str]:
    """Code-name folder -> deal_id, e.g. {'wren': 'deal_a', 'heron': 'deal_b'} from
    variant.json deals {'deal_a': 'Project Wren', ...}; plus the deal ids themselves (e.g. /kestrel/)."""
    out = {}
    for deal, name in variant.get("deals", {}).items():
        out[name.lower().removeprefix("project").strip().replace(" ", "-")] = deal
        out[deal] = deal
    out.setdefault("kestrel", "kestrel")
    return out


def page_records(url: str, html: bytes, deal: str, company: str | None, flags: list[str]) -> list[dict]:
    sha = hashlib.sha256(html).hexdigest()
    doc_id = f"WEB-{sha[:8]}"
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else None
    for t in soup(DROP_TAGS):
        t.decompose()
    body = soup.body or soup
    counts: dict[str, int] = {}
    out = []
    for el in body.find_all(list(BLOCK_TAGS)):
        if el.find_parent(list(BLOCK_TAGS)):          # e.g. a <p> inside an <li>: counted once, by its parent
            continue
        text = " ".join(el.get_text(" ").split())
        counts[el.name] = counts.get(el.name, 0) + 1
        if not text:
            continue
        out.append({
            "schema": "lantern/2.0", "doc_id": doc_id, "company": company, "cik": None, "ticker": None,
            "form": "web", "fiscal_year": None, "fiscal_period": None, "page": None,
            "section": title, "block_id": f"w{len(out) + 1:03d}", "block_type": BLOCK_TAGS[el.name],
            "bbox": None, "text": text, "table": None,
            "extractor": "beautifulsoup4", "extractor_version": version("beautifulsoup4"),
            "ocr": False, "ocr_conf": None, "source_path": f"data/web/pages/{doc_id}.html", "sha256": sha,
            "detector": None, "detector_score": None, "figure_path": None,
            "deal_id": deal, "source_type": "synthetic_web", "tier": 5,
            "citation": {"kind": "web_span", "doc_id": doc_id, "url": url,
                         "tag": el.name, "index": counts[el.name]},
            "lineage": {"parents": [], "source_sha256": sha}, "ingest_flags": list(flags),
        })
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Part 0 ingest_web")
    ap.add_argument("--output", default="data/web")
    ap.add_argument("--params", default="params.yaml")
    ap.add_argument("--variant", default="data/dataroom/variant.json")
    ap.add_argument("--mirror", default=None, help="offline: local copy of the synthetic-web site")
    args = ap.parse_args()

    params = edgar.load_params(args.params)
    host = params["web"]["host"]
    allowed_hosts = {host} | EDGAR_HOSTS
    mirror = Path(args.mirror).expanduser() if args.mirror else None
    variant = json.loads(Path(args.variant).expanduser().read_text(encoding="utf-8"))
    paths = deal_paths(variant)
    names = variant.get("deals", {})
    out = Path(args.output)
    (out / "pages").mkdir(parents=True, exist_ok=True)

    ua = f'{params["download"]["user_agent_name"]} {params["download"]["user_agent_email"]}'
    rp = robotparser.RobotFileParser()
    rp.parse(fetch(f"https://{host}/robots.txt", params, mirror).decode("utf-8").splitlines())

    log, records = [], []
    for url in sitemap_urls(fetch(params["web"]["sitemap_url"], params, mirror)):
        u = urlparse(url)
        if u.scheme != "https" or u.hostname not in allowed_hosts:
            log.append({"url": url, "status": "refused", "reason": "host not allowlisted", "doc_id": "", "deal_id": "", "blocks": 0})
            print(f"[INFO] refused (allowlist): {url}")
            continue
        if u.hostname == host and not rp.can_fetch(ua, url):
            log.append({"url": url, "status": "refused", "reason": "robots.txt", "doc_id": "", "deal_id": "", "blocks": 0})
            print(f"[INFO] refused (robots.txt): {url}")
            continue
        folder = u.path.strip("/").split("/")[0].lower() if "/" in u.path.strip("/") else ""
        if u.hostname in EDGAR_HOSTS:
            deal, flags = "public", []                    # EDGAR material is public by definition
        else:
            deal = paths.get(folder, "public")
            flags = [] if deal != "public" else ["deal_path_unmatched"]
            if deal == "public":
                print(f"[WARN] {url}: no deal code-name in the path -> deal_id public (check it)")
        html = fetch(url, params, mirror)
        recs = page_records(url, html, deal, names.get(deal), flags)
        doc_id = f"WEB-{hashlib.sha256(html).hexdigest()[:8]}"
        (out / "pages" / f"{doc_id}.html").write_bytes(html)
        records += recs
        log.append({"url": url, "status": "kept", "reason": "", "doc_id": doc_id, "deal_id": deal, "blocks": len(recs)})
        print(f"[INFO] kept {url} -> {doc_id} {deal} ({len(recs)} blocks)")

    with open(out / "crawl_log.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(log[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(log)
    n = write_jsonl(records, out / "records.jsonl")
    kept = sum(r["status"] == "kept" for r in log)
    print(f"[INFO] {kept} of {len(log)} sitemap URLs kept; {n} validated lantern/2.0 records -> {out / 'records.jsonl'}")


if __name__ == "__main__":
    main()
