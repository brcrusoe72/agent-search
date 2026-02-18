"""
AgentSearch API Tests
Run: python -m pytest tests/ -v
"""
import subprocess
import json
import time
import pytest

BASE_URL = "http://localhost:3939"


def curl_json(url: str) -> dict:
    """Helper: curl a URL and return parsed JSON."""
    result = subprocess.run(
        ["curl", "-s", url],
        capture_output=True, text=True, timeout=30
    )
    return json.loads(result.stdout)


class TestHealth:
    def test_health_endpoint(self):
        data = curl_json(f"{BASE_URL}/health")
        assert data["status"] == "healthy"
        assert data["searxng_available"] is True
        assert "version" in data

    def test_engines_endpoint(self):
        data = curl_json(f"{BASE_URL}/engines")
        assert isinstance(data, list)
        assert len(data) > 0
        # Check structure
        engine = data[0]
        assert "name" in engine
        assert "enabled" in engine


class TestSearch:
    def test_basic_search_returns_results(self):
        data = curl_json(f"{BASE_URL}/search?q=Python+programming")
        assert "results" in data
        assert "meta" in data
        assert len(data["results"]) > 0

    def test_search_result_structure(self):
        data = curl_json(f"{BASE_URL}/search?q=Python+pandas+tutorial")
        result = data["results"][0]
        assert "title" in result
        assert "url" in result
        assert "snippet" in result
        assert "engines" in result
        assert "score" in result
        assert result["url"].startswith("http")

    def test_search_count_parameter(self):
        data = curl_json(f"{BASE_URL}/search?q=data+analysis&count=3")
        assert len(data["results"]) <= 3

    def test_search_meta_contains_query(self):
        data = curl_json(f"{BASE_URL}/search?q=manufacturing+OEE")
        assert data["meta"]["query"] == "manufacturing OEE"

    def test_search_caching(self):
        """Second identical query should be cached."""
        q = f"cache_test_{int(time.time())}"
        # First request
        data1 = curl_json(f"{BASE_URL}/search?q={q}")
        # Second request (same query)
        data2 = curl_json(f"{BASE_URL}/search?q={q}")
        assert data2["meta"]["cached"] is True

    def test_search_deduplication(self):
        """Results should not contain duplicate URLs."""
        data = curl_json(f"{BASE_URL}/search?q=Python+data+science&count=20")
        urls = [r["url"] for r in data["results"]]
        assert len(urls) == len(set(urls)), "Duplicate URLs found in results"

    def test_empty_query_handled(self):
        """Empty query should return empty results or error gracefully."""
        result = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", f"{BASE_URL}/search?q="],
            capture_output=True, text=True, timeout=10
        )
        # Should return 400 or 422, or 200 with empty results — not 500
        assert result.stdout in ("200", "400", "422")


class TestJobSearch:
    def test_job_search_returns_results(self):
        data = curl_json(f"{BASE_URL}/search/jobs?q=MES+engineer+manufacturing")
        assert "results" in data
        assert len(data["results"]) > 0

    def test_job_search_targets_job_boards(self):
        """Job results should come from job-related sites."""
        data = curl_json(f"{BASE_URL}/search/jobs?q=data+analyst+Python")
        job_domains = ["indeed.com", "linkedin.com", "glassdoor.com", "ziprecruiter.com", "builtin.com"]
        urls = [r["url"] for r in data["results"]]
        # At least some results should be from job boards
        job_board_hits = sum(1 for u in urls if any(d in u for d in job_domains))
        assert job_board_hits > 0, f"No job board results found in: {urls[:5]}"

    def test_job_search_result_structure(self):
        data = curl_json(f"{BASE_URL}/search/jobs?q=manufacturing+engineer")
        result = data["results"][0]
        assert "title" in result
        assert "url" in result


class TestMultiEngine:
    def test_results_from_multiple_engines(self):
        """A broad query should return results from multiple search engines."""
        data = curl_json(f"{BASE_URL}/search?q=Python+programming+tutorial&count=20")
        all_engines = set()
        for r in data["results"]:
            all_engines.update(r.get("engines", []))
        # Should have results from at least 2 different engines
        assert len(all_engines) >= 2, f"Only got results from: {all_engines}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
