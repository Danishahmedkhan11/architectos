from backend import impact


def test_blast_radius_stays_bounded(graph, retriever):
    seeds = impact.pick_seeds(retriever, "update the healthcheck message")
    result = impact.blast_radius(graph, seeds)
    assert result["summary"]["total"] <= 120
