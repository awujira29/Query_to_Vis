"""The HTTP layer: status codes and body shapes, with the pipeline faked."""
import pytest
from fastapi.testclient import TestClient

from app import main
from app.client import UpstreamError
from app.schemas.response import QueryResponse, ResponseMeta, VisualizationSpec, Channel


@pytest.fixture
def client():
    return TestClient(main.app)


def ok_response(request):
    spec = VisualizationSpec(type="bar_chart", title="t",
                             encoding={"x": Channel(field="label", type="ordinal"),
                                       "y": Channel(field="trial_count", type="quantitative")},
                             data=[{"label": "Phase 2", "trial_count": 3, "nct_ids": [], "citations": []}])
    return QueryResponse(status="ok", visualization=spec,
                         meta=ResponseMeta(query=request.query, planner="rules", analyzed=3))


def test_health_and_schema(client):
    health = client.get("/health").json()
    assert health["status"] == "ok" and "planner" in health
    schema = client.get("/schema").json()
    assert "query" in schema["request"]["properties"]
    assert "visualization" in schema["response"]["properties"]


def test_query_ok(client, monkeypatch):
    monkeypatch.setattr(main, "answer", ok_response)
    r = client.post("/query", json={"query": "melanoma trials by phase"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["visualization"]["type"] == "bar_chart"
    assert body["meta"]["analyzed"] == 3


def test_query_validation_error_is_422(client):
    r = client.post("/query", json={"query": "melanoma trials", "trial_phase": ["Phase 9"]})
    assert r.status_code == 422 and "unknown phase" in r.text
    assert client.post("/query", json={}).status_code == 422
    assert client.post("/query", json={"query": "x"}).status_code == 422


def test_upstream_error_is_502_with_same_body_shape(client, monkeypatch):
    def failing(request):
        return QueryResponse(status="error", message="ClinicalTrials.gov request failed: HTTP 503",
                             meta=ResponseMeta(query=request.query))
    monkeypatch.setattr(main, "answer", failing)
    r = client.post("/query", json={"query": "melanoma trials by phase"})
    assert r.status_code == 502
    assert r.json()["status"] == "error" and "503" in r.json()["message"]


def test_plan_endpoint_does_not_fetch(client, monkeypatch):
    def must_not_fetch(*a, **k):
        raise AssertionError("fetch called")
    monkeypatch.setattr("app.pipeline.fetch_studies", must_not_fetch)
    monkeypatch.setattr("app.planner.rules.count_studies",
                        lambda p: (_ for _ in ()).throw(UpstreamError("offline")))
    r = client.post("/plan", json={"query": "Compare phases for pembrolizumab vs nivolumab trials"})
    assert r.status_code == 200
    body = r.json()
    assert body["planner"] == "rules" and body["plan"]["intent"] == "comparison"
    assert [s["label"] for s in body["plan"]["series"]] == ["pembrolizumab", "nivolumab"]
