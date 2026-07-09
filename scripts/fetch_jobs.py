#!/usr/bin/env python3
"""
Fetch relevant vacancies from UNHCR, UNICEF and WFP and write data/jobs.json.

Designed to run in a GitHub Action on a schedule. Each source is fetched
independently; if one fails, the previous run's jobs for that agency are kept
so the dashboard never goes blank.

Tune the keyword / level filters in the CONFIG block below.
"""

import json
import re
import sys
import datetime
from pathlib import Path

import requests

# ----------------------------- CONFIG ---------------------------------------

# A posting is kept if its title or summary matches ANY of these (case-insensitive).
KEYWORDS = [
    "private sector",
    "partnership",
    "fundrais",          # fundraising / fundraiser
    "resource mobili",   # mobilization / mobilisation
    "philanthrop",
    "donor",
    "foundations",
    "corporate engagement",
    "individual giving",
    "income",
    "innovative finance",
    "blended finance",
]

# Grades of interest. Empty list = keep all grades.
LEVELS = ["P4", "P3", "P5"]

# Requests timeout (seconds)
TIMEOUT = 30

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "jobs.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (personal vacancy monitor; contact via repo)",
    "Accept": "application/json",
}

# ----------------------------- HELPERS --------------------------------------

LEVEL_RE = re.compile(r"\b(P[-\s]?([2-6])|NO[-\s]?[A-E]|D[-\s]?[12])\b", re.I)


def detect_level(text: str):
    m = LEVEL_RE.search(text or "")
    if not m:
        return None
    lvl = m.group(0).upper().replace(" ", "").replace("-", "")
    return lvl


def relevant(title: str, summary: str = "") -> bool:
    blob = f"{title} {summary}".lower()
    return any(k in blob for k in KEYWORDS)


def level_ok(level):
    if not LEVELS:
        return True
    # Keep postings whose grade we couldn't detect rather than silently dropping them.
    return level is None or level in LEVELS


def iso(dt) -> str:
    if isinstance(dt, (int, float)):  # epoch millis
        return datetime.datetime.utcfromtimestamp(dt / 1000).date().isoformat()
    return str(dt)[:10]


# ----------------------------- SOURCES --------------------------------------


def fetch_unicef():
    """UNICEF runs on SmartRecruiters, which has a public postings API."""
    jobs, offset = [], 0
    while True:
        r = requests.get(
            "https://api.smartrecruiters.com/v1/companies/UNICEF/postings",
            params={"limit": 100, "offset": offset},
            headers=HEADERS,
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        payload = r.json()
        content = payload.get("content", [])
        for p in content:
            title = p.get("name", "")
            loc = p.get("location", {}) or {}
            location = ", ".join(x for x in [loc.get("city"), loc.get("country")] if x)
            level = detect_level(title)
            summary = (p.get("customField") and "") or ""
            if not relevant(title, summary) or not level_ok(level):
                continue
            pid = str(p.get("id"))
            jobs.append(
                {
                    "id": f"unicef-{pid}",
                    "title": title,
                    "agency": "UNICEF",
                    "level": level,
                    "location": location or None,
                    "posted_date": iso(p.get("releasedDate", "")),
                    "closing_date": None,
                    "summary": (p.get("department", {}) or {}).get("label"),
                    "evergreen": False,
                    "url": f"https://jobs.smartrecruiters.com/UNICEF/{pid}",
                }
            )
        offset += len(content)
        if offset >= payload.get("totalFound", 0) or not content:
            break
    return jobs


def fetch_unhcr():
    """UNHCR careers run on Workday; the CXS endpoint returns JSON."""
    jobs = []
    base = "https://unhcr.wd3.myworkdayjobs.com"
    endpoint = f"{base}/wday/cxs/unhcr/External/jobs"
    for search_text in ["private sector partnerships", "fundraising", "resource mobilization"]:
        offset = 0
        while True:
            r = requests.post(
                endpoint,
                json={"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": search_text},
                headers={**HEADERS, "Content-Type": "application/json"},
                timeout=TIMEOUT,
            )
            r.raise_for_status()
            payload = r.json()
            postings = payload.get("jobPostings", [])
            for p in postings:
                title = p.get("title", "")
                level = detect_level(title)
                if not relevant(title) or not level_ok(level):
                    continue
                path = p.get("externalPath", "")
                evergreen = "evergreen" in title.lower() or "talent pool" in title.lower()
                jobs.append(
                    {
                        "id": "unhcr-" + (p.get("bulletFields", [path])[0] if p.get("bulletFields") else path),
                        "title": title,
                        "agency": "UNHCR",
                        "level": level,
                        "location": p.get("locationsText"),
                        "posted_date": None,  # Workday returns "posted X days ago" text only
                        "posted_text": p.get("postedOn"),
                        "closing_date": None,
                        "summary": None,
                        "evergreen": evergreen,
                        "url": f"{base}/en-US/External{path}",
                    }
                )
            offset += len(postings)
            if not postings or offset >= payload.get("total", 0):
                break
    # Convert "Posted 3 Days Ago" style text into an approximate date
    for j in jobs:
        t = (j.pop("posted_text", "") or "").lower()
        m = re.search(r"(\d+)\+?\s*day", t)
        if "today" in t or "yesterday" in t:
            delta = 0 if "today" in t else 1
        elif m:
            delta = int(m.group(1))
        else:
            delta = None
        if delta is not None:
            j["posted_date"] = (datetime.date.today() - datetime.timedelta(days=delta)).isoformat()
    # De-duplicate across the three searches
    seen, unique = set(), []
    for j in jobs:
        if j["id"] not in seen:
            seen.add(j["id"])
            unique.append(j)
    return unique


def fetch_wfp():
    """
    WFP careers run on SAP SuccessFactors, which has no clean public JSON API.
    Best effort: parse the public career-site search results. If WFP changes
    their site this will fail gracefully and previous data is kept.
    """
    jobs = []
    url = "https://career5.successfactors.eu/career"
    r = requests.get(
        url,
        params={
            "company": "C0000168410P",
            "career_ns": "job_listing_summary",
            "navBarLevel": "JOB_SEARCH",
        },
        headers={**HEADERS, "Accept": "text/html"},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    html = r.text
    # Very defensive extraction of job title links
    for m in re.finditer(r'career_job_req_id=(\d+)[^>]*>([^<]{5,150})<', html):
        req_id, title = m.group(1), m.group(2).strip()
        level = detect_level(title)
        if not relevant(title) or not level_ok(level):
            continue
        jobs.append(
            {
                "id": f"wfp-{req_id}",
                "title": title,
                "agency": "WFP",
                "level": level,
                "location": None,
                "posted_date": None,
                "closing_date": None,
                "summary": None,
                "evergreen": False,
                "url": f"{url}?career_ns=job_application&company=C0000168410P&career_job_req_id={req_id}",
            }
        )
    if not jobs:
        raise RuntimeError("WFP parse produced 0 jobs — site structure may have changed")
    return jobs


# ----------------------------- MAIN -----------------------------------------


def main():
    previous = {"jobs": [], "sources": {}}
    if OUT.exists():
        try:
            previous = json.loads(OUT.read_text())
        except Exception:
            pass

    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    all_jobs, sources = [], {}

    for agency, fn in [("UNICEF", fetch_unicef), ("UNHCR", fetch_unhcr), ("WFP", fetch_wfp)]:
        try:
            jobs = fn()
            all_jobs.extend(jobs)
            sources[agency] = {"status": "ok", "count": len(jobs), "fetched_at": now}
            print(f"{agency}: {len(jobs)} relevant postings")
        except Exception as e:
            # Keep the previous run's jobs for this agency
            kept = [j for j in previous.get("jobs", []) if j.get("agency") == agency]
            all_jobs.extend(kept)
            sources[agency] = {"status": f"error: {e}", "count": len(kept), "fetched_at": now}
            print(f"{agency}: FAILED ({e}) — kept {len(kept)} previous postings", file=sys.stderr)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(
            {"generated_at": now, "sample": False, "sources": sources, "jobs": all_jobs},
            indent=2,
            ensure_ascii=False,
        )
    )
    print(f"Wrote {len(all_jobs)} jobs to {OUT}")


if __name__ == "__main__":
    main()
