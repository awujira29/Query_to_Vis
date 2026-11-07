"""ClinicalTrials.gov API client: throttled, retrying, paginated, cached."""
import hashlib
import json
import logging
import time
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://clinicaltrials.gov/api/v2"



class ApiError(Exception):
    """Base class for all API problems."""


class BadRequestError(ApiError):
    """400: our request was wrong. Retrying won't help."""


class RateLimitedError(ApiError):
    """429: still rate-limited after all retries."""


class UpstreamError(ApiError):
    """5xx, timeouts, or network failures after all retries."""



CACHE_DIR = Path(".cache/ctgov")
CACHE_TTL_SECONDS = 24 * 60 * 60     # registry data changes slowly; a day is fine


def _cache_file(path: str, params: dict) -> Path:
    """Same path + params (in any order) -> same file name."""
    key = json.dumps({"path": path, "params": params}, sort_keys=True)
    digest = hashlib.sha256(key.encode()).hexdigest()[:32]
    return CACHE_DIR / f"{digest}.json"


def _cache_read(path: str, params: dict) -> dict | None:
    file = _cache_file(path, params)
    if not file.exists():
        return None
    if time.time() - file.stat().st_mtime > CACHE_TTL_SECONDS:
        return None                                  # expired
    try:
        return json.loads(file.read_text())
    except (OSError, ValueError):
        return None                                  # corrupt file: refetch


def _cache_write(path: str, params: dict, data: dict) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_file(path, params).write_text(json.dumps(data))
    except OSError as e:
        logger.warning("cache write failed: %s", e)  # caching is optional, never fatal




MAX_RETRIES = 4
MIN_INTERVAL = 1.5          # seconds between requests, to stay under the rate limit

_last_request_time = 0.0


def _throttle() -> None:
    """Wait if the previous request was too recent."""
    global _last_request_time
    wait = MIN_INTERVAL - (time.monotonic() - _last_request_time)
    if wait > 0:
        time.sleep(wait)
    _last_request_time = time.monotonic()


def get_json(path: str, params: dict, use_cache: bool = True) -> dict:
    """GET one API page with caching, throttling and retries. Returns parsed JSON."""
    if use_cache:
        cached = _cache_read(path, params)
        if cached is not None:
            return cached

    for attempt in range(MAX_RETRIES + 1):
        _throttle()
        try:
            r = httpx.get(BASE_URL + path, params=params, timeout=30)
        except httpx.HTTPError as e:
            error = UpstreamError(f"network error: {e!r}")
        else:
            if r.status_code == 200:
                data = r.json()
                if use_cache:
                    _cache_write(path, params, data)
                return data
            if r.status_code == 400:
                raise BadRequestError(r.text[:500])
            if r.status_code == 429:
                error = RateLimitedError("rate limited by ClinicalTrials.gov")
            else:
                error = UpstreamError(f"HTTP {r.status_code}: {r.text[:200]}")

        if attempt < MAX_RETRIES:
            delay = 2 ** attempt
            logger.warning("%s, retrying in %ss", error, delay)
            time.sleep(delay)

    raise error




PAGE_SIZE = 1000            # the max the API allows, confirmed by probing
DEFAULT_MAX_STUDIES = 3000

FETCH_FIELDS = ",".join([
    "NCTId", "BriefTitle", "OfficialTitle",
    "OverallStatus", "WhyStopped", "StudyType", "Phase",
    "DesignAllocation", "DesignPrimaryPurpose", "DesignMasking",
    "StartDate", "StartDateType",
    "PrimaryCompletionDate", "PrimaryCompletionDateType",
    "CompletionDate", "CompletionDateType",
    "EnrollmentCount", "EnrollmentType",
    "LeadSponsorName", "LeadSponsorClass", "CollaboratorName", "CollaboratorClass",
    "OverallOfficialName", "OverallOfficialAffiliation", "OverallOfficialRole",
    "Condition", "ConditionMeshTerm",
    "InterventionType", "InterventionName", "InterventionOtherName",
    "InterventionArmGroupLabel",
    "ArmGroupLabel", "ArmGroupType", "ArmGroupInterventionName",
    "LocationFacility", "LocationCity", "LocationState", "LocationCountry",
    "LocationGeoPoint",
    "HasResults",
])


def fetch_studies(params: dict, max_studies: int = DEFAULT_MAX_STUDIES) -> dict:
    """Fetch every study matching `params`, up to `max_studies`.

    Returns {"studies": [...], "total_available": int | None,
             "truncated": bool, "api_urls": [...]}.
    """
    base = {**params, "fields": FETCH_FIELDS, "pageSize": PAGE_SIZE, "countTotal": "true"}
    studies: list[dict] = []
    total = None
    token = None
    api_urls: list[str] = []

    while True:
        page_params = dict(base)
        if token:
            page_params["pageToken"] = token
            page_params.pop("countTotal")        # only needed on the first page

        api_urls.append(str(httpx.URL(BASE_URL + "/studies", params=page_params)))
        page = get_json("/studies", page_params)
        if total is None:
            total = page.get("totalCount")

        studies.extend(page.get("studies", []))
        token = page.get("nextPageToken")

        if not token or len(studies) >= max_studies:
            break

    truncated = len(studies) > max_studies or token is not None
    return {
        "studies": studies[:max_studies],
        "total_available": total,
        "truncated": truncated,
        "api_urls": api_urls,
    }

def count_studies(params: dict) -> int:
    """Exact number of studies matching `params`, without fetching them.

    One tiny request: a single NCT ID per page plus the total count.
    """
    page = get_json("/studies", {**params, "fields": "NCTId", "pageSize": 1, "countTotal": "true"})
    return int(page.get("totalCount") or 0)
