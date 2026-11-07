"""Intermediate shapes: what aggregators produce and builders consume.

Three shapes cover every visualization:
- Table:    grouped rows       -> bar, grouped bar, time series, histogram, box plot
- PointSet: one point per item -> scatter plot
- Graph:    nodes and edges    -> network graph

Every datum (row, point, node, edge) carries the NCT IDs behind it and a
capped sample of Citations, so any value shown can be traced to source records.
"""
from typing import Any, Literal

from pydantic import BaseModel

DType = Literal["nominal", "ordinal", "temporal", "quantitative"]


class Citation(BaseModel):
    """One piece of evidence: an exact value from one field of one trial."""
    nct_id: str
    url: str
    field: str                  # exact JSON path, e.g. "...designModule.phases"
    value: str                  # exact API text at that path
    title: str                  # brief title, so a reader knows which trial it is


class Column(BaseModel):
    name: str
    dtype: DType
    unit: str | None = None     # e.g. "trials", "participants"


class Table(BaseModel):
    """Rows of grouped values. Each row also carries "nct_ids" and "citations"."""
    columns: list[Column]
    rows: list[dict[str, Any]]
    total_records: int          # trials analyzed, before grouping
    missing_count: int = 0      # trials with no value for the dimension
    notes: list[str] = []


class Point(BaseModel):
    """One scatter point, usually one trial."""
    x: float
    y: float
    color: str | None = None    # optional category, e.g. phase
    nct_id: str
    citations: list[Citation] = []


class PointSet(BaseModel):
    x: Column
    y: Column
    color: Column | None = None
    points: list[Point]
    total_records: int
    missing_count: int = 0      # trials lacking x or y
    notes: list[str] = []


class Node(BaseModel):
    id: str                     # namespaced to avoid collisions, e.g. "drug:pembrolizumab"
    label: str
    type: str                   # "drug", "sponsor", "condition", "investigator"...
    weight: int                 # number of trials involving this node
    nct_ids: list[str] = []
    citations: list[Citation] = []


class Edge(BaseModel):
    source: str                 # Node.id
    target: str                 # Node.id
    weight: int                 # number of distinct trials linking the two
    nct_ids: list[str] = []
    citations: list[Citation] = []


class Graph(BaseModel):
    nodes: list[Node]
    edges: list[Edge]
    directed: bool = False
    total_records: int
    notes: list[str] = []