"""Configuration loading, discovery matrix and spatial policy."""
from pathlib import Path

import pytest

from collector import config


def test_scope_excludes_non_main_district_counties():
    excluded = config.excluded_names()
    for county in ("杞县", "通许县", "尉氏县", "兰考县"):
        assert county in excluded
    assert any("祥符区" in e for e in excluded)


def test_scope_covers_five_districts_with_approximate_bbox():
    names = config.district_names()
    assert set(names) == {"龙亭区", "鼓楼区", "顺河回族区", "禹王台区", "城乡一体化示范区"}
    for district in config.districts():
        assert district.bbox_status == "approximate_pending"
        west, south, east, north = district.bbox
        assert west < east and south < north
        # 粗包围盒必须落在开封一带，明显错误要立刻失败
        assert 113.5 < west < 115.5 and 34.0 < south < 35.5


def test_no_precise_polygon_is_configured():
    scope = config.load_scope()
    assert scope["spatial_filter"]["mode"] == "coarse_bbox"
    assert "polygon" not in scope["spatial_filter"]["mode"]


def test_categories_map_to_city_facts_landing_points():
    keys = config.category_keys()
    for expected in ("scenic", "museum", "street_block", "night_market", "food",
                     "restaurant", "supply", "transport", "rest", "entrance",
                     "open_rule", "show"):
        assert expected in keys
    assert config.category("scenic").place_type == "scenic"
    assert config.category("museum").place_type == "poi"
    assert config.category("night_market").place_type == "night_visit"
    assert config.category("food").fact_kind == "food"
    assert config.category("supply").fact_kind == "supply"


def test_required_fields_are_declared_per_category():
    for category in config.categories():
        assert category.required_fields, f"{category.key} 未声明必填字段清单"
        assert "name" in category.required_fields or category.key in ("open_rule", "supply", "show")


def test_discovery_matrix_is_district_x_category_x_keyword():
    tasks = config.discovery_tasks(limit_per_category=3)
    assert tasks
    for task in tasks:
        assert task.district in config.district_names()
        assert task.category in config.category_keys()
        assert task.keyword


def test_fetch_policy_never_bypasses_access_control():
    policy = config.fetch_policy()
    blocked = policy.blocked_status_map
    for status in (401, 403, 429):
        assert status in blocked
    assert policy.robots["enabled"] is True
    assert policy.retry["max_attempts"] >= 1
    assert policy.rate_limit["per_domain_seconds"] > 0
    assert 429 in policy.retryable_status


def test_missing_config_file_fails_loudly(tmp_path, monkeypatch):
    from collector import config as cfg
    cfg.clear_caches()
    monkeypatch.setattr(cfg, "SCOPE_FILE", tmp_path / "absent.yaml")
    with pytest.raises(FileNotFoundError):
        cfg.load_scope()
    cfg.clear_caches()


def test_config_files_exist_in_repository():
    for name in ("collection_scope.yaml", "categories.yaml", "fetch_policy.yaml"):
        assert (config.CONFIG_DIR / name).exists(), name
