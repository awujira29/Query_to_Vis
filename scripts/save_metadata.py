import json
import httpx


r =  httpx.get("https://clinicaltrials.gov/api/v2/studies/metadata", timeout=60)
r.raise_for_status()

with open("metadata.json", "w") as f:
    json.dump(r.json(), f, indent=2)

print("saved", len(r.content), "bytes")

