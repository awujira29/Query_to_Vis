"""Probe the ClinicalTrials.gov v2 API and print a compact summary of each response.

Run:  pip install httpx && python probe_api.py > probe_output.txt
Then paste probe_output.txt back into the chat.
"""
import json
import time
import httpx

BASE = "https://clinicaltrials.gov/api/v2"
DELAY_SECONDS = 3  # pause between requests to avoid 429 rate limiting

PROBES = [
    # (label, path, params)
    ("max pageSize 1000", "/studies", {"query.intr": "pembrolizumab", "fields": "NCTId", "pageSize": 1000}),
    ("nonsense drug", "/studies", {"query.intr": "zzqxnotadrug", "countTotal": "true"}),
    ("invalid field name", "/studies", {"query.intr": "pembrolizumab", "fields": "NotARealField"}),
    ("invalid phase value", "/studies", {"filter.advanced": "AREA[Phase]PHASE9", "countTotal": "true", "pageSize": 1}),
    ("field value stats: Phase", "/stats/field/values", {"fields": "Phase"}),
    ("metadata (field names)", "/studies/metadata", {}),
]


def summarize(label, path, params):
    try:
        r = httpx.get(BASE + path, params=params, timeout=60)
    except httpx.HTTPError as e:
        return {"label": label, "error": repr(e)}
    out = {"label": label, "url": str(r.url), "status": r.status_code, "bytes": len(r.content)}
    try:
        body = r.json()
    except ValueError:
        out["text"] = r.text[:300]
        return out
    if isinstance(body, dict) and "studies" in body:
        out["totalCount"] = body.get("totalCount")
        out["returned"] = len(body["studies"])
        out["hasNextPage"] = "nextPageToken" in body
        if body["studies"]:
            out["first_study"] = json.dumps(body["studies"][0])[:1500]
    else:
        out["body_preview"] = json.dumps(body)[:1500]
    return out


if __name__ == "__main__":
    for probe in PROBES:
        print(json.dumps(summarize(*probe), indent=2, ensure_ascii=False))
        print("-" * 60)
        time.sleep(DELAY_SECONDS)