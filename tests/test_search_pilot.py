from scripts.check_search_pilot import latency, target_match


def test_target_matching_rejects_host_spoofs_and_title_collisions():
    case = {"targets": [{"host": "doi.org", "path": "/10.1023/a:1010933404324"}]}
    assert target_match(case, {"url": "https://doi.org/10.1023/A:1010933404324"})
    assert not target_match(case, {"title": "Random Forests", "url": "https://doi.org/10.1234/different-paper"})
    assert not target_match(case, {"url": "https://doi.org.evil.example/10.1023/A:1010933404324"})
    assert not target_match(case, {"url": "https://example.org/?url=doi.org/10.1023/A:1010933404324"})


def test_news_has_no_automatic_accuracy_verdict():
    assert target_match({"topic_terms": ["nasa"]}, {"title": "NASA", "url": "https://example.org"}) is None


def test_small_sample_latency_uses_observed_nearest_rank():
    assert latency([]) == {}
    assert latency([{"seconds": 10}, {"seconds": 1}, {"seconds": 2}]) == {
        "samples": 3, "p50_seconds": 2, "p95_seconds": 10, "max_seconds": 10}
