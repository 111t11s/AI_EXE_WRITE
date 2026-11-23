"""生成任务持久化：重启后恢复历史、标记中断，并支持手动删除。"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from api.generation_jobs import JobStore
from db import generation_jobs as job_db
from db.migrate_accounts import create_account
from test_writing_workflow import create_test_db


def test_database_error_summary_reports_driver_code_and_message():
    from api.generation_jobs import _database_error_summary

    error = SimpleNamespace(orig=SimpleNamespace(args=(1054, "Unknown column 'event_sequence'")))
    assert _database_error_summary(error) == "1054: Unknown column 'event_sequence'"


def setup():
    engine = create_test_db()
    account_id = create_account(engine, "writer", "browser-test-password")
    return engine, account_id


def make_job(job_id="job1", status="completed", account_id=1, **overrides):
    now = datetime.now(timezone.utc)
    values = dict(id=job_id, account_id=account_id, key=f"draft:{job_id}", kind="draft", title="生成正文",
                  project_id=1, resource_id="5", status=status, message="已完成", text="正文",
                  thinking="思考", truncated=False, result={"word_count": 10}, error=None,
                  sequence=7, created_at=now, finished_at=now if status != "running" else None)
    values.update(overrides)
    return SimpleNamespace(**values)


def test_job_row_roundtrip_keeps_result_and_times():
    engine, account_id = setup()
    job_db.save_values(engine, job_db.snapshot(make_job(account_id=account_id)))
    rows = job_db.load_rows(engine)
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == "job1" and row["result"] == {"word_count": 10}
    assert row["created_at"].tzinfo is not None
    assert row["text"] == "正文" and row["sequence"] == 7


def test_restart_marks_running_jobs_as_interrupted():
    engine, account_id = setup()
    job_db.save_values(engine, job_db.snapshot(make_job("running1", "running", account_id)))
    assert job_db.fail_running(engine) == 1
    row = job_db.load_rows(engine)[0]
    assert row["status"] == "failed"
    assert row["message"] == job_db.INTERRUPTED_MESSAGE
    assert row["error"]["status"] == 503
    assert row["finished_at"] is not None


def test_bind_restores_history_and_interrupts_unfinished_jobs():
    engine, account_id = setup()
    job_db.save_values(engine, job_db.snapshot(make_job("done", "completed", account_id)))
    job_db.save_values(engine, job_db.snapshot(make_job("half", "running", account_id)))
    store = JobStore()
    assert store.bind(lambda: engine) is True
    assert set(store.jobs) == {"done", "half"}
    assert store.jobs["done"].result == {"word_count": 10}
    assert store.jobs["half"].status == "failed"
    assert store.jobs["half"].message == job_db.INTERRUPTED_MESSAGE


def test_page_filters_and_counts():
    from api.generation_jobs import GenerationJob

    store = JobStore()
    now = datetime.now(timezone.utc)

    def job(job_id, kind="draft", status="completed", project=1, account=1, minutes=0):
        return GenerationJob(id=job_id, account_id=account, key=f"{kind}:{job_id}", kind=kind, title=job_id,
                             project_id=project, status=status, message="m",
                             created_at=now - timedelta(minutes=minutes),
                             finished_at=None if status == "running" else now)

    for index in range(5):
        store.jobs[f"d{index}"] = job(f"d{index}", minutes=index)
    store.jobs["run"] = job("run", status="running", minutes=9)
    store.jobs["o1"] = job("o1", kind="outline", project=2)
    store.jobs["o2"] = job("o2", kind="outline", project=None)
    store.jobs["other"] = job("other", account=2)

    page = store.page(1, category="draft", limit=3, offset=0)
    assert page["total"] == 6 and len(page["items"]) == 3 and page["has_more"] is True
    assert page["items"][0]["status"] == "running"
    assert page["counts"]["category"] == {"all": 8, "draft": 6, "outline": 2}
    assert page["counts"]["category_running"]["draft"] == 1
    assert page["counts"]["project"]["1"] == 6 and page["counts"]["project"]["all"] == 6
    assert page["counts"]["status"] == {"all": 6, "running": 1, "completed": 5, "failed": 0}

    second = store.page(1, category="draft", status="completed", limit=2, offset=2)
    assert second["total"] == 5 and len(second["items"]) == 2
    assert all(item["status"] == "completed" for item in second["items"])

    outline = store.page(1, category="outline", project_id="unassigned")
    assert outline["total"] == 1 and outline["items"][0]["id"] == "o2"
    assert {row["project_id"] for row in outline["projects"]} == {None, 2}


def test_manual_delete_removes_terminal_rows_only():
    engine, account_id = setup()
    job_db.save_values(engine, job_db.snapshot(make_job("done", "completed", account_id)))
    job_db.save_values(engine, job_db.snapshot(make_job("busy", "running", account_id)))
    store = JobStore()
    store.engine_factory = lambda: engine
    store.jobs["done"] = make_job("done", "completed", account_id)
    store.jobs["busy"] = make_job("busy", "running", account_id)
    store.jobs["foreign"] = make_job("foreign", "completed", account_id + 99)

    removed = store.delete(["done", "busy", "foreign"], account_id)
    assert removed == ["done"]
    assert set(store.jobs) == {"busy", "foreign"}
    assert {row["id"] for row in job_db.load_rows(engine)} == {"busy"}


def test_delete_rows_ignores_missing_and_empty_ids():
    engine, _ = setup()
    assert job_db.delete_rows(engine, []) == 0
    assert job_db.delete_rows(engine, ["missing"]) == 0
