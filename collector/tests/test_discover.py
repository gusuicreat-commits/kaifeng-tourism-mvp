"""Discovery matrix and access classification.

The search backend is always injected; no test calls a real search provider.
"""
from collector import discover
from collector import ledger
from collector.config import discovery_tasks


def test_discovery_tasks_cover_district_x_category_x_keyword():
    tasks = discovery_tasks()
    assert tasks, "配置必须产出发现任务"
    assert all(t.district and t.category and t.keyword for t in tasks)
    pairs = {(t.district, t.category, t.keyword) for t in tasks}
    assert len(pairs) == len(tasks), "发现任务不得重复"
    districts = {t.district for t in tasks}
    for category in ("scenic", "museum", "night_market"):
        subset = {t.district for t in tasks if t.category == category}
        assert subset == districts


def test_limit_per_category_truncates_keywords_only():
    tasks = discovery_tasks(limit_per_category=1)
    counts: dict[str, set[str]] = {}
    for t in tasks:
        counts.setdefault(t.category, set()).add(t.keyword)
    assert counts and all(len(v) == 1 for v in counts.values())


def test_run_discovery_dedups_urls_within_one_task(db):
    def backend(query, category):
        assert query, "查询串必须非空"
        return [
            {"title": "开封市博物馆", "url": "https://www.kfsbwg.com/a", "text": "开放时间 9:00-17:00"},
            {"title": "开封市博物馆（重复）", "url": "https://www.kfsbwg.com/a"},
            {"title": "", "url": ""},
            {"title": "政府名录", "url": "https://www.kaifeng.gov.cn/list"},
        ]

    task = discovery_tasks(limit_per_category=1)[0]
    (result,) = discover.run_discovery(db, run_id="run_test", backend=backend, tasks=[task])
    urls = [h.url for h in result.hits]
    assert urls == ["https://www.kfsbwg.com/a", "https://www.kaifeng.gov.cn/list"]
    assert result.hits[0].title == "开封市博物馆"
    assert result.hits[0].snippet == "开放时间 9:00-17:00"
    assert len(result.source_keys) == len(urls)
    assert set(ledger.source_keys(db)) == set(result.source_keys)


def test_run_discovery_over_all_tasks_registers_every_hit(db):
    def backend(query, category):
        return [{"title": "开封市博物馆", "url": "https://www.kfsbwg.com/a"},
                {"title": "政府名录", "url": "https://www.kaifeng.gov.cn/list"}]

    tasks = discovery_tasks(limit_per_category=1)
    results = discover.run_discovery(db, run_id="run_test", backend=backend, tasks=tasks)
    assert len(results) == len(tasks)
    assert all(r.hits for r in results)
    keys = [k for r in results for k in r.source_keys]
    # source_key 只由 (category, url) 决定：同一页面在不同城区被命中时收敛为一条来源
    unique = set(keys)
    assert len(keys) > len(unique)
    assert len(ledger.list_sources(db)) == len(unique)
    assert all(k.startswith("src_") for k in unique)


def test_repeat_discovery_does_not_duplicate_sources(db):
    def backend(query, category):
        return [{"title": "t", "url": "https://a.test/x"}]

    tasks = discovery_tasks(limit_per_category=1)
    first = discover.run_discovery(db, run_id="run_test", backend=backend, tasks=tasks)
    expected = [k for r in first for k in r.source_keys]
    second = discover.run_discovery(db, run_id="run_test", backend=backend, tasks=tasks)
    assert [k for r in second for k in r.source_keys] == expected
    assert len(ledger.list_sources(db)) == len(set(expected)), "重复发现不得重复登记来源"


def test_backend_failure_is_recorded_not_raised(db):
    def backend(query, category):
        raise RuntimeError("search quota exceeded")

    tasks = discovery_tasks(limit_per_category=1)
    results = discover.run_discovery(db, run_id="run_fail", backend=backend, tasks=tasks)
    assert all(r.error_kind == "discovery_backend_error" for r in results)
    assert all(r.hits == [] for r in results)
    failures = ledger.list_failures(db, "run_fail")
    assert len(failures) == len(results)
    assert failures[0]["failure_kind"] == "discovery_backend_error"
    assert "RuntimeError" in failures[0]["detail"]


def test_classify_access_kind_and_source_type():
    assert discover.classify_access_kind("search://snapshot/1") == "search_snapshot"
    assert discover.classify_access_kind("https://www.kaifeng.gov.cn/x") == "official_site"
    assert discover.classify_access_kind("https://restaurant.dianping.com/x") == "platform"
    assert discover.classify_access_kind("https://www.openstreetmap.org/way/1") == "map_service"
    assert discover.classify_access_kind("https://example.com/x") == "platform"
    assert discover.infer_source_type("https://www.kaifeng.gov.cn/x") == "official"
    assert discover.infer_source_type("https://x.test/", "高德地图数据") == "map_service"
    assert discover.infer_source_type("https://x.test/", "现场走查记录") == "field"


def test_discovery_summary_counts_tasks_hits_and_errors(db):
    def backend(query, category):
        if category == "museum":
            return [{"title": "a", "url": "https://a.test/1"},
                    {"title": "b", "url": "https://b.test/1"}]
        return []

    tasks = discovery_tasks(limit_per_category=1)
    results = discover.run_discovery(db, run_id="run_test", backend=backend, tasks=tasks)
    summary = discover.discovery_summary(results)
    museum_tasks = sum(1 for t in tasks if t.category == "museum")
    expected_hits = 2 * museum_tasks
    assert summary["tasks"] == len(tasks)
    assert summary["hits"] == expected_hits
    assert summary["unique_urls"] == 2, "summary 按 URL 去重"
    assert summary["by_category"]["museum"] == expected_hits
    assert summary["by_category"].get("scenic", 0) == 0
    assert sum(summary["by_district"].values()) == expected_hits
    assert all(count == expected_hits // len(summary["by_district"])
               for count in summary["by_district"].values())
    assert summary["errors"] == 0
    assert summary["by_category"]["museum"] == expected_hits
    assert sum(summary["by_district"].values()) == expected_hits
