"""Configuration loading for the Kaifeng collection toolkit.

YAML files under collector/config/ are the single source of truth for scope,
categories and fetch policy. Missing or malformed values fail loudly instead of
silently falling back to defaults that could fabricate facts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

PACKAGE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parent
CONFIG_DIR = PACKAGE_ROOT / "config"
SCOPE_FILE = CONFIG_DIR / "collection_scope.yaml"
CATEGORIES_FILE = CONFIG_DIR / "categories.yaml"
FETCH_POLICY_FILE = CONFIG_DIR / "fetch_policy.yaml"

DEFAULT_DB_PATH = REPO_ROOT / "data" / "collection.db"
DEFAULT_RUNS_DIR = REPO_ROOT / "data" / "collection-runs"


def _load(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"缺少配置文件：{path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"配置文件格式错误：{path}")
    return data


@lru_cache(maxsize=None)
def load_scope() -> dict:
    return _load(SCOPE_FILE)


@lru_cache(maxsize=None)
def load_categories() -> dict:
    return _load(CATEGORIES_FILE)


@lru_cache(maxsize=None)
def load_fetch_policy() -> dict:
    return _load(FETCH_POLICY_FILE)


def clear_caches() -> None:
    load_scope.cache_clear()
    load_categories.cache_clear()
    load_fetch_policy.cache_clear()


@dataclass(frozen=True)
class District:
    key: str
    name: str
    role: str
    bbox: tuple[float, float, float, float]
    bbox_status: str

    def contains(self, longitude: float, latitude: float) -> bool:
        west, south, east, north = self.bbox
        return west <= longitude <= east and south <= latitude <= north


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    place_type: str | None
    fact_kind: str | None
    priority: str
    in_district: bool
    required_fields: tuple[str, ...]
    keywords: tuple[str, ...]
    channels: tuple[str, ...]


def districts() -> list[District]:
    out: list[District] = []
    for item in load_scope()["districts"]:
        bbox = tuple(float(x) for x in item["bbox"])
        if len(bbox) != 4:
            raise ValueError(f"城区 bbox 必须是 [west, south, east, north]：{item['key']}")
        out.append(District(item["key"], item["name"], item["role"], bbox, item.get("bbox_status", "unknown")))
    return out


def district_names() -> list[str]:
    return [d.name for d in districts()]


def district_by_name(name: str) -> District | None:
    for d in districts():
        if d.name == name:
            return d
    return None


def excluded_names() -> list[str]:
    return list(load_scope().get("excluded", []))


def district_unknown_label() -> str:
    return load_scope().get("district_unknown_label", "待核验")


def categories() -> list[Category]:
    out: list[Category] = []
    for item in load_categories()["categories"]:
        out.append(Category(
            key=item["key"], label=item.get("label", item["key"]),
            place_type=item.get("place_type"), fact_kind=item.get("fact_kind"),
            priority=item.get("priority", "normal"), in_district=bool(item.get("in_district", True)),
            required_fields=tuple(item.get("required_fields", [])),
            keywords=tuple(item.get("keywords", [])), channels=tuple(item.get("channels", [])),
        ))
    return out


def category(key: str) -> Category:
    for c in categories():
        if c.key == key:
            return c
    raise KeyError(f"未配置的类别：{key}")


def category_keys() -> list[str]:
    return [c.key for c in categories()]


@dataclass(frozen=True)
class FetchPolicy:
    timeouts: dict
    rate_limit: dict
    retry: dict
    robots: dict
    limits: dict
    blocked_status: dict
    storage: dict

    @property
    def retryable_status(self) -> set[int]:
        return {int(s) for s in self.retry.get("retryable_status", [])}

    @property
    def blocked_status_map(self) -> dict[int, str]:
        return {int(k): v for k, v in self.blocked_status.items()}


@lru_cache(maxsize=None)
def fetch_policy() -> FetchPolicy:
    raw = load_fetch_policy()
    return FetchPolicy(raw["timeouts"], raw["rate_limit"], raw["retry"], raw["robots"], raw["limits"],
                       raw["blocked_status"], raw["storage"])


@dataclass(frozen=True)
class SpatialVerdict:
    """Result of the coarse bbox spatial filter.

    `pending` means the record could not be placed inside or outside the main
    district; it is kept and routed to a review task rather than dropped.
    """
    status: str  # inside | outside | pending
    district: str | None = None
    reason: str = ""


@dataclass
class DiscoveryTask:
    district: str
    category: str
    keyword: str
    channel: str = "web_search"
    note: str = ""


def discovery_tasks(limit_per_category: int | None = None) -> list[DiscoveryTask]:
    """Build 城区 x 类别 x 关键词 discovery tasks from configuration."""
    tasks: list[DiscoveryTask] = []
    for category in categories():
        if not category.keywords:
            continue
        keywords = category.keywords[:limit_per_category] if limit_per_category else category.keywords
        for district in district_names():
            for keyword in keywords:
                tasks.append(DiscoveryTask(district=district, category=category.key, keyword=keyword))
    return tasks


def place_fact_kind(category_key: str) -> str | None:
    return category(category_key).fact_kind


@dataclass
class ValidationConfig:
    """Aggregate view of the configuration used by validation and reporting."""
    districts: list[District] = field(default_factory=districts)
    categories: list[Category] = field(default_factory=categories)


def validation_config() -> ValidationConfig:
    return ValidationConfig()
