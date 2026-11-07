"""Plan schema: a closed vocabulary that rejects anything the pipeline cannot run."""
import pytest
from pydantic import ValidationError

from app.query import Filters
from app.schemas.plan import Plan, SeriesSpec


def series(label="melanoma", **filters):
    return SeriesSpec(label=label, filters=Filters(**(filters or {"condition": "melanoma"})))


def test_distribution_needs_a_known_dimension():
    plan = Plan(intent="distribution", series=[series()], dimension="phase", title="t")
    assert plan.visualization_type == "bar_chart"
    with pytest.raises(ValidationError, match="dimension"):
        Plan(intent="distribution", series=[series()], dimension="colour", title="t")
    with pytest.raises(ValidationError):
        Plan(intent="distribution", series=[series()], title="t")


def test_comparison_needs_two_series_with_distinct_labels():
    two = [series("a", drug="a"), series("b", drug="b")]
    assert Plan(intent="comparison", series=two, dimension="phase", title="t").visualization_type \
        == "grouped_bar_chart"
    with pytest.raises(ValidationError, match="two series"):
        Plan(intent="comparison", series=two[:1], dimension="phase", title="t")
    with pytest.raises(ValidationError, match="distinct"):
        Plan(intent="comparison", series=[series("a", drug="a"), series("a", drug="b")],
             dimension="phase", title="t")
    with pytest.raises(ValidationError, match="exactly one"):
        Plan(intent="distribution", series=two, dimension="phase", title="t")


def test_network_pairs():
    assert Plan(intent="network", series=[series()], network=("sponsor", "drug"), title="t")
    assert Plan(intent="network", series=[series()], network=("drug", "drug"), title="t")
    with pytest.raises(ValidationError, match="only defined for drugs"):
        Plan(intent="network", series=[series()], network=("sponsor", "sponsor"), title="t")
    with pytest.raises(ValidationError):
        Plan(intent="network", series=[series()], network=("drug", "investigator"), title="t")
    with pytest.raises(ValidationError, match="two node types"):
        Plan(intent="network", series=[series()], title="t")


def test_histogram_and_scatter_measures():
    assert Plan(intent="histogram", series=[series()], measure="enrollment_count", title="t")
    with pytest.raises(ValidationError):
        Plan(intent="histogram", series=[series()], measure="budget", title="t")
    assert Plan(intent="scatter", series=[series()], x="start_year", y="enrollment_count",
                color="phase", title="t").visualization_type == "scatter_plot"
    with pytest.raises(ValidationError, match="must differ"):
        Plan(intent="scatter", series=[series()], x="start_year", y="start_year", title="t")
    with pytest.raises(ValidationError, match="color"):
        Plan(intent="scatter", series=[series()], x="start_year", y="enrollment_count",
             color="hue", title="t")


def test_unknown_intent_and_too_many_series():
    with pytest.raises(ValidationError):
        Plan(intent="pie", series=[series()], title="t")
    with pytest.raises(ValidationError):
        Plan(intent="trend", series=[series(str(i), drug=str(i)) for i in range(5)], title="t")
