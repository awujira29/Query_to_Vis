"""Network graph builder: Graph -> VisualizationSpec. Presentation only, no computation."""
from typing import Any

from app.schemas.response import Channel, VisualizationSpec
from app.schemas.shapes import Graph

TYPE_LABELS = {"drug": "Drug", "sponsor": "Lead sponsor", "condition": "Condition"}


def type_label(node_type: str) -> str:
    return TYPE_LABELS.get(node_type, node_type.replace("_", " ").capitalize())


def build_network(graph: Graph, title: str) -> VisualizationSpec:
    # "type_label" is the readable legend name; the raw type stays in nodes[].type.
    nodes = [{**n.model_dump(), "type_label": type_label(n.type)} for n in graph.nodes]
    return VisualizationSpec(
        type="network_graph",
        title=title,
        encoding={
            "node_id": Channel(field="id", type="nominal"),
            "node_label": Channel(field="label", type="nominal", title="Name"),
            "node_color": Channel(field="type_label", type="nominal", title="Type",
                                  sort=[type_label(t) for t in graph.node_types]),
            "node_size": Channel(field="weight", type="quantitative",
                                 title="Linked trials", unit="trials"),
            "edge_source": Channel(field="source", type="nominal"),
            "edge_target": Channel(field="target", type="nominal"),
            "edge_width": Channel(field="weight", type="quantitative",
                                  title="Shared trials", unit="trials"),
        },
        data={"nodes": nodes,
              "edges": [e.model_dump() for e in graph.edges],
              "directed": graph.directed},
    )


def render_hints(graph: Graph) -> dict[str, Any]:
    """Layout suggestions: two node types read best as two columns (bipartite);
    one type as a force layout centred on the focus node, if any."""
    return {
        "layout": "bipartite" if len(graph.node_types) == 2 else "force",
        "focus_node": next((n.id for n in graph.nodes if n.focus), None),
        "node_types": graph.node_types,
    }
