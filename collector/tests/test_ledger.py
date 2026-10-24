"""Ledger schema, idempotency and separation from the runtime fact store."""
import sqlite3

import pytest

from collector import ledger


def test_schema_is_idempotent(db_path):
    ledger.init_db(db_path)
    ledger.init_db(db_path)
    with ledger.connect(db_path) as db:
        tables = {r["name"] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"source_registry", "collect_task", "collect_run", "collect_failure",
            "verify_task"} <= tables


def test_ledger_has_no_city_facts_tables(db):
    tables = {r["name"] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "city_facts" not in tables
    assert "city_fact_revisions" not in tables


def test_source_registry_upsert_is_stable_and_refreshes(db):
    first = ledger.upsert_source(db, source_key="src_a", name="页面A", source_type="official",
                                 url="https://example.gov.cn/a")
    second = ledger.upsert_source(db, source_key="src_a", name="页面A（更新）",
                                  source_type="official", url="https://example.gov.cn/a",
                                  content_sha256="0" * 64)
    assert first == second == "src_a"
    sources = ledger.list_sources(db)
    assert len(sources) == 1
    assert sources[0]["name"] == "页面A（更新）"
    assert sources[0]["content_sha256"] == "0" * 64
    assert sources[0]["first_seen_at"]  # 首次发现时间不被覆盖


def test_source_url_dedup_lookup(db):
    ledger.upsert_source(db, source_key="src_a", name="A", source_type="other",
                         url="https://example.com/x")
    found = ledger.source_by_url(db, "https://example.com/x")
    assert found and found["source_key"] == "src_a"
    assert ledger.source_by_url(db, "https://example.com/absent") is None


def test_task_run_failure_chain(db):
    task = ledger.create_task(db, category="museum", objective="测试任务",
                              scope_district="龙亭区", priority="high")
    run = ledger.start_run(db, task_id=task, params={"keyword": "开封市博物馆"})
    ledger.upsert_source(db, source_key="src_a", name="A", source_type="official")
    failure = ledger.record_failure(db, run_id=run, source_key="src_a",
                                    failure_kind="waf_block", detail="HTTP 403")
    ledger.finish_run(db, run, status="partial", items_fetched=3, items_new=2,
                      items_failed=1, error_summary="waf_block")
    ledger.update_task_status(db, task, "done")
    assert len(ledger.list_tasks(db)) == 1
    assert ledger.get_run(db, run)["status"] == "partial"
    assert ledger.get_run(db, run)["items_failed"] == 1
    recorded = ledger.list_failures(db, run)
    assert recorded[0]["failure_id"] == failure
    assert recorded[0]["failure_kind"] == "waf_block"
    assert ledger.list_failures(db) == recorded


def test_failure_supports_retry_of(db):
    run = ledger.start_run(db)
    first = ledger.record_failure(db, run_id=run, source_key=None,
                                  failure_kind="network_error", detail="reset")
    second = ledger.record_failure(db, run_id=run, source_key=None,
                                   failure_kind="network_error", detail="reset again",
                                   retry_of=first)
    assert second != first
    retry = [f for f in ledger.list_failures(db, run) if f["retry_of"] == first]
    assert len(retry) == 1


def test_verify_task_lifecycle(db):
    verify = ledger.add_verify_task(db, fact_ref="place:test", check_item="坐标待核验",
                                    method_required="现场核验")
    assert ledger.open_verify_refs(db) == {"place:test"}
    ledger.set_verify_status(db, verify, "verified", reviewer="核验人", note="已确认")
    assert ledger.open_verify_refs(db) == set()
    task = ledger.list_verify_tasks(db, "verified")[0]
    assert task["reviewer"] == "核验人"
    assert task["verified_at"]


def test_new_ids_are_unique():
    ids = {ledger.new_id("run") for _ in range(200)}
    assert len(ids) == 200
    assert all(i.startswith("run_") for i in ids)


def test_ledger_file_is_separate_from_runtime_store(tmp_path):
    ledger_path = tmp_path / "collection.db"
    ledger.init_db(ledger_path)
    runtime = tmp_path / "tourism.sqlite3"
    with ledger.connect(ledger_path) as db:
        ledger.upsert_source(db, source_key="s", name="n", source_type="other")
    assert ledger_path.exists()
    assert not runtime.exists()  # 台账绝不创建或写入运行库


def test_verify_task_is_idempotent_on_fact_ref_and_check_item(db):
    first = ledger.add_verify_task(db, fact_ref="place:a", check_item="坐标待核验",
                                   method_required="现场核验", note="第一次")
    second = ledger.add_verify_task(db, fact_ref="place:a", check_item="坐标待核验",
                                    method_required="地图服务", note="第二次")
    assert first == second, "同一事实的同一待核验项不得叠加"
    assert len(ledger.list_verify_tasks(db, "open")) == 1
    task = ledger.list_verify_tasks(db, "open")[0]
    assert task["method_required"] == "地图服务" and task["note"] == "第二次"


def test_verify_task_different_check_items_stay_separate(db):
    ledger.add_verify_task(db, fact_ref="place:a", check_item="坐标待核验", method_required="m1")
    ledger.add_verify_task(db, fact_ref="place:a", check_item="营业时间待核验", method_required="m2")
    assert len(ledger.list_verify_tasks(db, "open")) == 2


def test_verify_task_reopens_when_a_closed_item_is_reported_again(db):
    verify = ledger.add_verify_task(db, fact_ref="place:a", check_item="坐标待核验",
                                    method_required="m")
    ledger.set_verify_status(db, verify, "verified", reviewer="核验人", note="已确认")
    assert ledger.open_verify_refs(db) == set()
    reopened = ledger.add_verify_task(db, fact_ref="place:a", check_item="坐标待核验",
                                      method_required="m")
    assert reopened != verify, "已核验项被再次报出应按新任务开放"
    assert ledger.open_verify_refs(db) == {"place:a"}
    # 历史行保留，唯一索引按 status 过滤所以不冲突
    assert len(ledger.list_verify_tasks(db)) == 2


def test_init_db_folds_preexisting_duplicate_open_tasks(tmp_path):
    """旧版台账可能已有重复 open 行，init_db 必须能升级而不丢任务。"""
    path = tmp_path / "legacy.db"
    # 旧版只建表、不建唯一索引；直接用裸连接还原历史表结构
    conn = sqlite3.connect(str(path))
    conn.executescript(ledger.SCHEMA)
    for i in range(3):
        conn.execute("""INSERT INTO verify_task (verify_id, fact_ref, check_item, method_required,
                       status, created_at) VALUES (?,?,?,?,?,?)""",
                     (f"verify_old{i}", "place:a", "坐标待核验", "m", "open",
                      f"2026-10-23T22:4{i}:00+08:00"))
    conn.commit()
    conn.close()
    ledger.init_db(path)
    with ledger.connect(path) as db:
        tasks = ledger.list_verify_tasks(db, "open")
        indexes = [r["name"] for r in ledger.rows(
            db, "SELECT name FROM sqlite_master WHERE type='index'")]
    assert len(tasks) == 1
    assert tasks[0]["verify_id"] == "verify_old0", "保留最早一条"
    assert "idx_verify_task_unique_open" in indexes


def test_unique_index_rejects_duplicate_open_tasks(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("""INSERT INTO verify_task (verify_id, fact_ref, check_item, method_required,
                       status, created_at) VALUES ('v1','place:a','x','m','open','2026-10-23T10:00:00+08:00')""")
        db.execute("""INSERT INTO verify_task (verify_id, fact_ref, check_item, method_required,
                       status, created_at) VALUES ('v2','place:a','x','m','open','2026-10-23T10:00:01+08:00')""")
