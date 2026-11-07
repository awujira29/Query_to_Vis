"""HTTP service: POST a question, get a visualization specification.

    uvicorn app.main:app --reload

Endpoints
    POST /query     QueryRequest -> QueryResponse (the whole pipeline)
    POST /plan      QueryRequest -> the Plan only, no data fetched (cheap: inspect how a
                    question would be read before running it)
    GET  /health    liveness, plus which planner and cache mode are active
    GET  /schema    JSON Schemas of the request and response, for frontend engineers
    GET  /docs      interactive OpenAPI documentation (FastAPI built-in)

Status mapping: an upstream (ClinicalTrials.gov) failure is reported as HTTP 502 with
the same QueryResponse body (status "error"), so clients handle one shape. A request
that fails validation is HTTP 422 (FastAPI's default), with the field errors.
"""
import logging
import os

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app import llm
from app.client import offline
from app.pipeline import answer
from app.planner import make_plan
from app.schemas.request import QueryRequest
from app.schemas.response import QueryResponse

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(
    title="ClinicalTrials.gov query-to-visualization agent",
    version="1.0.0",
    description="Turns a natural-language question about clinical trials into a structured "
                "visualization specification backed by ClinicalTrials.gov data, with citations "
                "to the source records.",
)


@app.get("/health")
def health() -> dict:
    conf = llm.provider()
    return {"status": "ok",
            "planner": f"llm ({conf[0]}:{conf[1]}) with rules fallback" if conf else "rules only (no LLM key)",
            "offline": offline()}


@app.get("/schema")
def schema() -> dict:
    return {"request": QueryRequest.model_json_schema(),
            "response": QueryResponse.model_json_schema()}


@app.post("/plan")
def plan(request: QueryRequest) -> dict:
    planning = make_plan(request)
    return {"planner": planning.planner,
            "plan": planning.plan.model_dump(mode="json") if planning.plan else None,
            "notes": planning.notes}


@app.post("/query", response_model=QueryResponse, response_model_exclude_none=False)
def query(request: QueryRequest):
    response = answer(request)
    if response.status == "error":
        return JSONResponse(status_code=502, content=response.model_dump(mode="json"))
    return response
