"""Shared fixtures: isolated ledger, no network, no runtime fact store."""
import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from collector import ledger


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "collection.db"
    ledger.init_db(path)
    return path


@pytest.fixture
def db(db_path):
    with ledger.connect(db_path) as conn:
        yield conn


@pytest.fixture
def runs_dir(tmp_path):
    path = tmp_path / "collection-runs"
    path.mkdir()
    return path


@pytest.fixture
def sample_records():
    from collector.extract import StructuredRecord
    stamp = "2026-10-23T10:00:00+08:00"
    return [
        StructuredRecord(record_id="place_museum", category="museum", source_keys=["src_a"],
                         collected_at=stamp, extraction_method="test", name="开封市博物馆",
                         district="待核验", address="开封市郑开大道与六大街交叉口", place_type="poi"),
        StructuredRecord(record_id="place_night", category="night_market", source_keys=["src_b"],
                         collected_at=stamp, extraction_method="test", name="鼓楼夜市",
                         district="鼓楼区", longitude=114.35, latitude=34.79,
                         coordinate_system="GCJ02", place_type="night_visit"),
        StructuredRecord(record_id="place_outside", category="scenic", source_keys=["src_c"],
                         collected_at=stamp, extraction_method="test", name="杞县某景区",
                         address="开封市杞县某路1号", place_type="scenic"),
        StructuredRecord(record_id="food_bao", category="food", source_keys=["src_a"],
                         collected_at=stamp, extraction_method="test", name="灌汤小笼包"),
        StructuredRecord(record_id="place_museum_dup", category="museum", source_keys=["src_d"],
                         collected_at=stamp, extraction_method="test", name="开封市博物馆",
                         address="另一处地址", place_type="poi"),
    ]


@pytest.fixture
def evidence_dir(tmp_path):
    """Two small raw evidence files with known content."""
    directory = tmp_path / "evidence"
    directory.mkdir()
    (directory / "a.html").write_text("<html><body>开放时间 9:00-17:00</body></html>", encoding="utf-8")
    (directory / "b.html").write_text("<html><body>鼓楼夜市 摊位200多个</body></html>", encoding="utf-8")
    return directory


@pytest.fixture
def collected_day():
    return date(2026, 10, 23)
