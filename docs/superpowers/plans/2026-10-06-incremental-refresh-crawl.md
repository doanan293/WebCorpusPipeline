# Incremental Refresh Crawl Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Thêm `hust-crawl --refresh` để chỉ fetch lại trang hub và bài gần đây, crawl các URL chưa từng thấy, xuất `changes/<run_id>/`, và cho `ir-data` nhận phần mới mà không làm lại toàn bộ.

**Architecture:** Refresh chạy trên chính state SQLite của crawl gốc. `refresh_store.py` giữ các bảng `refresh_runs`/`refresh_targets`/`refresh_changes`; `refresh.py` chọn tập refresh, quản lý phiên và xuất kết quả; `UnifiedSpider` chỉ thêm nhánh xử lý request có `refresh_reason`. Frontier khôi phục toàn bộ URL đã biết nên link cũ chỉ là `record_only`. Phía `ir_data`, inventory chuyển sang con trỏ theo từng nguồn và work dir nhận nguồn mới khi cùng `crawl_id`.

**Tech Stack:** Python 3.13, uv, Scrapy, SQLite (JSON1), pytest, ruff.

**Spec:** [Thiết kế crawl tăng dần](../specs/2026-10-06-incremental-refresh-crawl-design.md)

## Global Constraints

- Mọi lệnh chạy trong `data_pipeline/`: `uv run pytest -q`, `uv run ruff check .`.
- Không đọc/ghi `data/crawl/` hay `data/enrichment/` thật trong test hoặc khi triển khai; chỉ dùng `tmp_path`.
- Không thêm dependency mới.
- Bốn khoá `refresh_pagination_empty_pages: 1`, `refresh_recent_days: 60`, `refresh_recent_runs: 4`, `refresh_recheck_limit: 5000` là cấu hình runtime (thuộc `_RUNTIME_CONFIG_KEYS`), không thuộc semantic config.
- Crawl thường (không `--refresh`) giữ nguyên hành vi: toàn bộ test hiện có phải pass sau mỗi task.
- `--refresh` loại trừ `--resume`, `--reuse-content-from`, `--retry-failed`, `--retry-truncated`, `--retry-access-gates`, `--retry-policy-skips` (exit code `2`).
- Bài cũ không bao giờ bị xoá hay hạ trạng thái trong refresh; lỗi refetch chỉ ghi vào `refresh_targets`, không ghi bảng `errors`.
- `run_id` dạng `YYYYMMDDTHHMMSSZ` (UTC), tăng nghiêm ngặt giữa các run.
- `crawl_id` = 16 ký tự hex đầu của `sha256(f"{input_sha256}:{started_at!r}")` từ manifest state.
- Commit theo kiểu `feat:`/`test:`/`docs:`; kết thúc message bằng `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. Hai lần refresh bắt đầu trong cùng một giây: `run_id` mới phải lớn hơn run trước, không lỗi khoá chính — Task 4.
2. Gọi `--refresh` khi crawl gốc đang chạy dở hoặc chưa từng kết thúc: bị từ chối trước khi state bị sửa — Task 4, Task 7.
3. `published_at`/`modified_at` sai định dạng (ví dụ `"08/09/2026"`, chuỗi rỗng): bài bị bỏ qua, không làm sập bước chọn — Task 4.
4. Work dir enrichment có sẵn từ trước khi refresh: báo lỗi có hướng dẫn, `--rebind-source` chạy được và các lần sau tự nhận cùng `crawl_id` — Task 9.
5. Inventory một thư mục `changes` bị ngắt giữa chừng rồi chạy lại: refs đã ghi từ phần đầu file không bị xoá lần hai — Task 9.

---

### Task 1: Khoá cấu hình refresh

**Files:**
- Modify: `src/hust_crawler/config.py` (dataclass `CrawlerConfig`, `CrawlerConfig.load`, `_RUNTIME_CONFIG_KEYS`)
- Modify: `config/crawler.yaml`
- Test: `tests/unit/test_config.py`

**Interfaces:**
- Produces: `CrawlerConfig.refresh_pagination_empty_pages: int`, `refresh_recent_days: int`, `refresh_recent_runs: int`, `refresh_recheck_limit: int`.

- [ ] **Step 1: Write the failing tests**

Thêm vào cuối `tests/unit/test_config.py` (thêm `semantic_config_snapshot` vào import từ `hust_crawler.config`):

```python
def test_config_loads_refresh_settings(tmp_path: Path) -> None:
    raw = complete_yaml(tmp_path, contact="ops@example.org", refresh_recent_days=30)
    config = CrawlerConfig.load(raw, hostnames=frozenset({"a.test"}))

    assert config.refresh_pagination_empty_pages == 1
    assert config.refresh_recent_days == 30
    assert config.refresh_recent_runs == 4
    assert config.refresh_recheck_limit == 5000


def test_config_requires_refresh_settings(tmp_path: Path) -> None:
    path = complete_yaml(tmp_path, contact="ops@example.org")
    values = yaml.safe_load(path.read_text(encoding="utf-8"))
    del values["refresh_recheck_limit"]
    path.write_text(yaml.safe_dump(values), encoding="utf-8")

    with pytest.raises(ValueError, match="refresh_recheck_limit"):
        CrawlerConfig.load(path, hostnames=frozenset({"a.test"}))


def test_config_rejects_non_positive_refresh_settings(tmp_path: Path) -> None:
    path = complete_yaml(tmp_path, contact="ops@example.org", refresh_recent_runs=0)

    with pytest.raises(ValueError, match="refresh_recent_runs must be positive"):
        CrawlerConfig.load(path, hostnames=frozenset({"a.test"}))


def test_refresh_settings_do_not_change_crawl_identity(tmp_path: Path) -> None:
    base = CrawlerConfig.load(
        complete_yaml(tmp_path, contact="ops@example.org"), hostnames=frozenset({"a.test"})
    )
    changed = CrawlerConfig.load(
        complete_yaml(tmp_path, contact="ops@example.org", refresh_recent_days=7),
        hostnames=frozenset({"a.test"}),
    )

    assert semantic_config_snapshot(base) == semantic_config_snapshot(changed)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_config.py -q`
Expected: 4 test mới FAIL (`AttributeError: ... refresh_pagination_empty_pages` hoặc không có `ValueError`).

- [ ] **Step 3: Implement**

Trong `CrawlerConfig`, ngay sau `progress_interval_seconds: float = 10.0`:

```python
    refresh_pagination_empty_pages: int = 1
    refresh_recent_days: int = 60
    refresh_recent_runs: int = 4
    refresh_recheck_limit: int = 5000
```

Trong `load`, nối 4 tên vào cuối tuple `required`:

```python
            "journal_flush_seconds", "progress_interval_seconds",
            "refresh_pagination_empty_pages", "refresh_recent_days",
            "refresh_recent_runs", "refresh_recheck_limit",
        )
```

Trong dict `values`, sau dòng `"progress_interval_seconds": ...`:

```python
            "refresh_pagination_empty_pages": int(raw["refresh_pagination_empty_pages"]),
            "refresh_recent_days": int(raw["refresh_recent_days"]),
            "refresh_recent_runs": int(raw["refresh_recent_runs"]),
            "refresh_recheck_limit": int(raw["refresh_recheck_limit"]),
```

(Kiểm tra dương đã tự áp dụng vì tuple `positive` lấy mọi tên trong `required` trừ danh sách loại trừ.)

Thêm vào `_RUNTIME_CONFIG_KEYS`:

```python
    "refresh_pagination_empty_pages", "refresh_recent_days",
    "refresh_recent_runs", "refresh_recheck_limit",
```

Thêm vào cuối `config/crawler.yaml`:

```yaml
refresh_pagination_empty_pages: 1
refresh_recent_days: 60
refresh_recent_runs: 4
refresh_recheck_limit: 5000
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: tất cả PASS.

- [ ] **Step 5: Commit**

```bash
git add src/hust_crawler/config.py config/crawler.yaml tests/unit/test_config.py
git commit -m "feat: add refresh crawl settings"
```

---

### Task 2: `RefreshStore` — bảng refresh trong index crawl

**Files:**
- Create: `src/hust_crawler/crawl/refresh_store.py`
- Test: `tests/unit/crawl/test_refresh_store.py`

**Interfaces:**
- Produces:
  - `SUCCESS_OUTCOMES: frozenset[str]` = `{"new", "unchanged", "modified", "not_modified", "fetched"}`
  - `CONTINUABLE_RUN_STATUSES: frozenset[str]` = `{"running", "interrupted", "failed"}`
  - `class RefreshStore(connection: sqlite3.Connection)` với các method:
    `start_run(run_id) -> None`, `resume_run(run_id) -> None`, `latest_run() -> dict | None` (khoá `run_id`, `status`, `started_at`, `finished_at`), `run_started_at(run_id) -> float`, `finish_run(run_id, status, payload: dict) -> None`, `completed_run_ids(limit: int) -> list[str]` (mới nhất trước),
    `add_targets(run_id, targets: Iterable[tuple[str, str]]) -> None`, `add_target(run_id, url, reason) -> bool`, `pending_targets(run_id) -> list[tuple[str, str]]`, `finish_target(run_id, url, outcome, error=None) -> None`,
    `record_change(run_id, change: dict) -> None`, `iter_changes(run_id) -> Iterator[dict]` (theo URL),
    `target_summary(run_id) -> dict` (khoá `targets_by_reason`, `outcomes`),
    `failure_counts(run_id, truncation_reasons: Iterable[str]) -> tuple[int, int]`.

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

from pathlib import Path

from hust_crawler.crawl.refresh_store import RefreshStore
from hust_crawler.crawl.state import CrawlState

RUN = "20261006T000000Z"


def open_state(tmp_path: Path) -> CrawlState:
    input_path = tmp_path / "seeds.txt"
    input_path.write_text("a.test\n", encoding="utf-8")
    return CrawlState.open(
        tmp_path / "state",
        phase="crawl",
        input_path=input_path,
        semantic_config={},
        runtime_config={},
    )


def test_run_lifecycle_and_latest_run(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    store = RefreshStore(state.connection)
    store.start_run("20261001T000000Z")
    store.finish_run("20261001T000000Z", "complete", {"new": 1})
    store.start_run(RUN)

    assert store.latest_run()["run_id"] == RUN
    assert store.latest_run()["status"] == "running"
    assert store.completed_run_ids(4) == ["20261001T000000Z"]

    store.finish_run(RUN, "interrupted", {})
    store.resume_run(RUN)
    assert store.latest_run()["status"] == "running"
    state.close()


def test_targets_are_unique_per_run_and_tracked(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    store = RefreshStore(state.connection)
    store.start_run(RUN)
    store.add_targets(RUN, [("https://a.test/", "homepage"), ("https://a.test/list", "navigation")])

    assert store.add_target(RUN, "https://a.test/list", "pagination_next") is False
    assert store.add_target(RUN, "https://a.test/list?page=2", "pagination_next") is True

    store.finish_target(RUN, "https://a.test/", "not_modified")
    store.finish_target(RUN, "https://a.test/list", "http_500", "server error")

    assert store.pending_targets(RUN) == [("https://a.test/list?page=2", "pagination_next")]
    assert store.target_summary(RUN) == {
        "targets_by_reason": {"homepage": 1, "navigation": 1, "pagination_next": 1},
        "outcomes": {"http_500": 1, "not_modified": 1, "pending": 1},
    }
    state.close()


def test_changes_are_upserted_and_ordered(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    store = RefreshStore(state.connection)
    store.record_change(RUN, {"url": "https://a.test/b", "change": "new"})
    store.record_change(RUN, {"url": "https://a.test/a", "change": "new"})
    store.record_change(RUN, {"url": "https://a.test/a", "change": "modified"})

    assert [(c["url"], c["change"], c["run_id"]) for c in store.iter_changes(RUN)] == [
        ("https://a.test/a", "modified", RUN),
        ("https://a.test/b", "new", RUN),
    ]
    state.close()


def test_failure_counts_only_cover_this_run(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    store = RefreshStore(state.connection)
    store.start_run(RUN)
    store.add_targets(RUN, [("https://a.test/", "homepage"), ("https://a.test/x", "recent")])
    store.finish_target(RUN, "https://a.test/", "request_failed")
    store.finish_target(RUN, "https://a.test/x", "unchanged")
    state.upsert_url({"url": "https://a.test/old", "status": "failed"})
    state.upsert_url({"url": "https://a.test/new", "status": "failed", "first_seen_run": RUN})
    state.upsert_url(
        {"url": "https://a.test/cut", "status": "skipped", "reason": "host_url_limit", "first_seen_run": RUN}
    )

    assert store.failure_counts(RUN, {"host_url_limit"}) == (2, 1)
    state.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/crawl/test_refresh_store.py -q`
Expected: FAIL với `ModuleNotFoundError: hust_crawler.crawl.refresh_store`.

- [ ] **Step 3: Implement**

```python
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Iterator
import json
import sqlite3
import time

SUCCESS_OUTCOMES = frozenset({"new", "unchanged", "modified", "not_modified", "fetched"})
CONTINUABLE_RUN_STATUSES = frozenset({"running", "interrupted", "failed"})
FINISHED_RUN_STATUSES = ("complete", "complete_with_failures", "truncated")


class RefreshStore:
    """Persist refresh runs, their targets and detected changes in the crawl index."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS refresh_runs (
                run_id TEXT PRIMARY KEY,
                started_at REAL NOT NULL,
                finished_at REAL,
                status TEXT NOT NULL,
                payload TEXT NOT NULL DEFAULT '{}'
            );
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS refresh_targets (
                run_id TEXT NOT NULL,
                url TEXT NOT NULL,
                reason TEXT NOT NULL,
                done INTEGER NOT NULL DEFAULT 0,
                outcome TEXT,
                error TEXT,
                PRIMARY KEY(run_id, url)
            );
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS refresh_changes (
                run_id TEXT NOT NULL,
                url TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY(run_id, url)
            );
            """
        )

    def start_run(self, run_id: str) -> None:
        self.connection.execute(
            "INSERT INTO refresh_runs (run_id, started_at, status) VALUES (?, ?, 'running');",
            (run_id, time.time()),
        )

    def resume_run(self, run_id: str) -> None:
        self.connection.execute(
            "UPDATE refresh_runs SET status = 'running', finished_at = NULL WHERE run_id = ?;",
            (run_id,),
        )

    def latest_run(self) -> dict[str, object] | None:
        row = self.connection.execute(
            """
            SELECT run_id, status, started_at, finished_at
            FROM refresh_runs ORDER BY run_id DESC LIMIT 1;
            """
        ).fetchone()
        if row is None:
            return None
        return {"run_id": row[0], "status": row[1], "started_at": row[2], "finished_at": row[3]}

    def run_started_at(self, run_id: str) -> float:
        row = self.connection.execute(
            "SELECT started_at FROM refresh_runs WHERE run_id = ?;", (run_id,)
        ).fetchone()
        return float(row[0])

    def finish_run(self, run_id: str, status: str, payload: dict[str, object]) -> None:
        self.connection.execute(
            "UPDATE refresh_runs SET status = ?, finished_at = ?, payload = ? WHERE run_id = ?;",
            (status, time.time(), json.dumps(payload, ensure_ascii=False), run_id),
        )

    def completed_run_ids(self, limit: int) -> list[str]:
        rows = self.connection.execute(
            f"""
            SELECT run_id FROM refresh_runs
            WHERE status IN ({",".join("?" for _ in FINISHED_RUN_STATUSES)})
            ORDER BY run_id DESC LIMIT ?;
            """,
            (*FINISHED_RUN_STATUSES, limit),
        )
        return [str(row[0]) for row in rows]

    def add_targets(self, run_id: str, targets: Iterable[tuple[str, str]]) -> None:
        self.connection.execute("BEGIN IMMEDIATE;")
        try:
            self.connection.executemany(
                "INSERT OR IGNORE INTO refresh_targets (run_id, url, reason) VALUES (?, ?, ?);",
                ((run_id, url, reason) for url, reason in targets),
            )
            self.connection.execute("COMMIT;")
        except Exception:
            self.connection.execute("ROLLBACK;")
            raise

    def add_target(self, run_id: str, url: str, reason: str) -> bool:
        cursor = self.connection.execute(
            "INSERT OR IGNORE INTO refresh_targets (run_id, url, reason) VALUES (?, ?, ?);",
            (run_id, url, reason),
        )
        return cursor.rowcount == 1

    def pending_targets(self, run_id: str) -> list[tuple[str, str]]:
        rows = self.connection.execute(
            "SELECT url, reason FROM refresh_targets WHERE run_id = ? AND done = 0 ORDER BY url;",
            (run_id,),
        )
        return [(str(url), str(reason)) for url, reason in rows]

    def finish_target(
        self, run_id: str, url: str, outcome: str, error: str | None = None
    ) -> None:
        self.connection.execute(
            """
            UPDATE refresh_targets SET done = 1, outcome = ?, error = ?
            WHERE run_id = ? AND url = ?;
            """,
            (outcome, error, run_id, url),
        )

    def record_change(self, run_id: str, change: dict[str, object]) -> None:
        payload = json.dumps({"run_id": run_id, **change}, ensure_ascii=False)
        self.connection.execute(
            """
            INSERT INTO refresh_changes (run_id, url, payload) VALUES (?, ?, ?)
            ON CONFLICT(run_id, url) DO UPDATE SET payload = excluded.payload;
            """,
            (run_id, str(change["url"]), payload),
        )

    def iter_changes(self, run_id: str) -> Iterator[dict[str, object]]:
        rows = self.connection.execute(
            "SELECT payload FROM refresh_changes WHERE run_id = ? ORDER BY url;", (run_id,)
        ).fetchall()
        for (payload,) in rows:
            yield json.loads(payload)

    def target_summary(self, run_id: str) -> dict[str, dict[str, int]]:
        reasons: Counter[str] = Counter()
        outcomes: Counter[str] = Counter()
        for reason, outcome in self.connection.execute(
            "SELECT reason, outcome FROM refresh_targets WHERE run_id = ?;", (run_id,)
        ):
            reasons[str(reason)] += 1
            outcomes[str(outcome or "pending")] += 1
        return {
            "targets_by_reason": dict(sorted(reasons.items())),
            "outcomes": dict(sorted(outcomes.items())),
        }

    def failure_counts(
        self, run_id: str, truncation_reasons: Iterable[str]
    ) -> tuple[int, int]:
        successes = sorted(SUCCESS_OUTCOMES)
        failed_targets = self.connection.execute(
            f"""
            SELECT count(*) FROM refresh_targets
            WHERE run_id = ? AND done = 1
              AND outcome NOT IN ({",".join("?" for _ in successes)});
            """,
            (run_id, *successes),
        ).fetchone()[0]
        failed_new = self.connection.execute(
            """
            SELECT count(*) FROM url_records
            WHERE status = 'failed' AND json_extract(payload, '$.first_seen_run') = ?;
            """,
            (run_id,),
        ).fetchone()[0]
        reasons = sorted(truncation_reasons)
        truncated = 0
        if reasons:
            truncated = self.connection.execute(
                f"""
                SELECT count(*) FROM url_records
                WHERE json_extract(payload, '$.first_seen_run') = ?
                  AND json_extract(payload, '$.reason') IN ({",".join("?" for _ in reasons)});
                """,
                (run_id, *reasons),
            ).fetchone()[0]
        return int(failed_targets) + int(failed_new), int(truncated)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/crawl/test_refresh_store.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/hust_crawler/crawl/refresh_store.py tests/unit/crawl/test_refresh_store.py
git commit -m "feat: persist refresh runs, targets and changes"
```

---

### Task 3: Bổ sung `CrawlState` và `CrawlWriter` cho refresh

**Files:**
- Modify: `src/hust_crawler/crawl/state.py` (`CrawlState.__init__`, `merge_url`, `export_final`; thêm method mới)
- Modify: `src/hust_crawler/crawl/writer.py` (thêm `content_id_for`, dùng trong `write_article`)
- Test: `tests/unit/crawl/test_state.py`, `tests/unit/crawl/test_writer.py`

**Interfaces:**
- Produces:
  - `CrawlState.refresh_run_id: str | None` (mặc định `None`). Khi khác `None`, mọi dòng `url_records` **mới** nhận `first_seen_run`.
  - `CrawlState.crawl_id -> str` (property)
  - `CrawlState.url_record(url: str) -> dict | None`
  - `CrawlState.update_url_fields(url: str, fields: Mapping[str, object]) -> None` (gộp payload, không đổi `status`/`completed`; bỏ qua nếu URL chưa có)
  - `CrawlState.release_content_owner(url: str, content_id: str) -> None`
  - `CrawlState.iter_final_articles(urls: Iterable[str] | None = None) -> Iterator[tuple[str, str]]`
  - `CrawlState.backup_to(destination: Path) -> None`
  - `writer.content_id_for(text: str) -> str`
  - `final/README.json` có thêm `crawl_id`, và `last_refresh_run_id` nếu `manifest["last_refresh_run_id"]` có giá trị.

- [ ] **Step 1: Write the failing tests**

Thêm vào `tests/unit/crawl/test_state.py` (bổ sung import `sqlite3`, `from hust_crawler.crawl.options import CrawlOptions`, `from hust_crawler.crawl.writer import CrawlWriter`):

```python
def test_new_records_are_stamped_with_refresh_run(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    state.upsert_url({"url": "https://a.test/old", "status": "extracted"})
    state.refresh_run_id = "20261006T000000Z"
    state.upsert_url({"url": "https://a.test/old", "status": "extracted", "etag": '"x"'})
    state.upsert_url({"url": "https://a.test/new", "status": "scheduled"})

    assert "first_seen_run" not in state.url_record("https://a.test/old")
    assert state.url_record("https://a.test/new")["first_seen_run"] == "20261006T000000Z"
    state.close()


def test_update_url_fields_keeps_status_and_completion(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    url = "https://a.test/page"
    state.upsert_url({"url": url, "status": "extracted", "completed": True, "duplicate_of": "x"})
    state.update_url_fields(url, {"etag": '"v1"', "duplicate_of": None})
    state.update_url_fields("https://a.test/missing", {"etag": '"v1"'})

    record = state.url_record(url)
    assert record["status"] == "extracted"
    assert record["etag"] == '"v1"'
    assert record["duplicate_of"] is None
    assert state.is_complete(url)
    assert state.url_record("https://a.test/missing") is None
    state.close()


def test_release_content_owner_promotes_first_duplicate(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    writer = CrawlWriter(tmp_path / "output", state, CrawlOptions())
    for url in ("https://a.test/owner", "https://a.test/b", "https://a.test/c"):
        writer.write_article({"url": url, "final_url": url, "text": "Shared text"})
    old_id = state.url_record("https://a.test/owner")["content_id"]

    state.release_content_owner("https://a.test/owner", old_id)

    articles = {
        url: json.loads(payload)
        for url, payload in state.connection.execute("SELECT url, payload FROM articles")
    }
    assert articles["https://a.test/b"]["text"] == "Shared text"
    assert "duplicate_of" not in articles["https://a.test/b"]
    assert articles["https://a.test/c"]["duplicate_of"] == "https://a.test/b"
    assert state.url_record("https://a.test/b")["duplicate_of"] is None
    assert state.url_record("https://a.test/c")["duplicate_of"] == "https://a.test/b"
    assert state.claim_content(old_id, "https://a.test/z") == "https://a.test/b"
    state.close()


def test_release_content_owner_without_duplicates_frees_content(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    writer = CrawlWriter(tmp_path / "output", state, CrawlOptions())
    writer.write_article({"url": "https://a.test/a", "final_url": "https://a.test/a", "text": "Only"})
    old_id = state.url_record("https://a.test/a")["content_id"]

    state.release_content_owner("https://a.test/a", old_id)

    assert state.claim_content(old_id, "https://a.test/z") == "https://a.test/z"
    state.close()


def test_iter_final_articles_limits_to_requested_urls(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    writer = CrawlWriter(tmp_path / "output", state, CrawlOptions())
    for url, text in (("https://a.test/a", "A"), ("https://a.test/b", "B")):
        writer.write_article({"url": url, "final_url": url, "text": text})

    rows = list(state.iter_final_articles(["https://a.test/b", "https://a.test/missing"]))

    assert [url for url, _ in rows] == ["https://a.test/b"]
    assert json.loads(rows[0][1])["text"] == "B"
    state.close()


def test_final_readme_records_crawl_lineage(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    state.manifest["last_refresh_run_id"] = "20261006T000000Z"
    output = tmp_path / "public"
    state.finish("complete", exit_code=0, counts={}, metrics={}, public_output=output)

    readme = json.loads((output / "final" / "README.json").read_text(encoding="utf-8"))
    assert len(state.crawl_id) == 16
    assert readme["crawl_id"] == state.crawl_id
    assert readme["last_refresh_run_id"] == "20261006T000000Z"
    state.close()


def test_backup_to_copies_the_index(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    state.upsert_url({"url": "https://a.test/", "status": "extracted"})
    target = tmp_path / "archive" / "pre-refresh-x" / "index.sqlite3"

    state.backup_to(target)

    copy = sqlite3.connect(target)
    assert copy.execute("SELECT count(*) FROM url_records").fetchone()[0] == 1
    copy.close()
    state.close()
```

Thêm vào `tests/unit/crawl/test_writer.py`:

```python
def test_content_id_for_matches_written_article(tmp_path: Path) -> None:
    from hust_crawler.crawl.writer import content_id_for

    writer = make_writer(tmp_path)
    stored = writer.write_article({"url": "https://a.test/a", "final_url": "https://a.test/a", "text": "  Body \n"})

    assert stored["content_id"] == content_id_for("  Body \n") == content_id_for("Body")
    writer.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/crawl/test_state.py tests/unit/crawl/test_writer.py -q`
Expected: các test mới FAIL (`AttributeError: 'CrawlState' object has no attribute 'url_record'`, `ImportError: content_id_for`).

- [ ] **Step 3: Implement**

`writer.py` — thêm hàm module ngay trước `class StorageLimit` và dùng trong `write_article`:

```python
def content_id_for(text: str) -> str:
    """Return the content hash stored for extracted article text."""
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()
```

```python
    def write_article(self, record: dict[str, object]) -> dict[str, object]:
        content_id = content_id_for(str(record.get("text") or ""))
        url = str(record["url"])
```

(Xoá hai dòng cũ `text = ...strip()` và `content_id = hashlib.sha256(...)`.)

`state.py`:

1. Import thêm: `from collections.abc import Iterable, Mapping` (thay cho dòng `from collections.abc import Mapping`).
2. Trong `CrawlState.__init__` thêm `self.refresh_run_id: str | None = None`.
3. Trong `merge_url`, ngay sau `merged = self._merged_record(record)`:

```python
        if existing_row is None and self.refresh_run_id is not None:
            merged.setdefault("first_seen_run", self.refresh_run_id)
```

4. Thêm các method (đặt sau `checkpoint`):

```python
    @property
    def crawl_id(self) -> str:
        seed = f"{self.manifest['input_sha256']}:{self.manifest['started_at']!r}"
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]

    def url_record(self, url: str) -> dict[str, object] | None:
        row = self.connection.execute(
            "SELECT payload FROM url_records WHERE url = ?;", (url,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def update_url_fields(self, url: str, fields: Mapping[str, object]) -> None:
        record = self.url_record(url)
        if record is None:
            return
        record.update(fields)
        self.connection.execute(
            "UPDATE url_records SET payload = ? WHERE url = ?;",
            (json.dumps(record, ensure_ascii=False), url),
        )

    def release_content_owner(self, url: str, content_id: str) -> None:
        """Hand ownership of content_id from url to its first duplicate, if any."""
        owner = self.connection.execute(
            "SELECT url FROM content_owner WHERE content_id = ?;", (content_id,)
        ).fetchone()
        if owner is None or owner[0] != url:
            return
        article_row = self.connection.execute(
            "SELECT payload FROM articles WHERE url = ?;", (url,)
        ).fetchone()
        old_text = json.loads(article_row[0]).get("text") if article_row else None
        dependents = [
            str(row[0])
            for row in self.connection.execute(
                """
                SELECT a.url FROM articles AS a JOIN url_records AS r ON r.url = a.url
                WHERE json_extract(a.payload, '$.duplicate_of') = ?
                  AND json_extract(r.payload, '$.content_id') = ?
                ORDER BY a.url;
                """,
                (url, content_id),
            )
        ]
        self.connection.execute("BEGIN IMMEDIATE;")
        try:
            if not dependents:
                self.connection.execute(
                    "DELETE FROM content_owner WHERE content_id = ?;", (content_id,)
                )
            else:
                new_owner = dependents[0]
                self.connection.execute(
                    "UPDATE content_owner SET url = ? WHERE content_id = ?;",
                    (new_owner, content_id),
                )
                for dependent in dependents:
                    article = json.loads(
                        self.connection.execute(
                            "SELECT payload FROM articles WHERE url = ?;", (dependent,)
                        ).fetchone()[0]
                    )
                    record = self.url_record(dependent) or {"url": dependent}
                    if dependent == new_owner:
                        article.pop("duplicate_of", None)
                        article["text"] = old_text
                        record["duplicate_of"] = None
                    else:
                        article["duplicate_of"] = new_owner
                        record["duplicate_of"] = new_owner
                    self.connection.execute(
                        "UPDATE articles SET payload = ? WHERE url = ?;",
                        (json.dumps(article, ensure_ascii=False), dependent),
                    )
                    self.connection.execute(
                        "UPDATE url_records SET payload = ? WHERE url = ?;",
                        (json.dumps(record, ensure_ascii=False), dependent),
                    )
            self.connection.execute("COMMIT;")
        except Exception:
            self.connection.execute("ROLLBACK;")
            raise

    def backup_to(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(str(destination))
        try:
            self.connection.backup(target)
        finally:
            target.close()
```

5. Tách vòng lặp của `export_final` thành `iter_final_articles` (giữ nguyên logic nhúng text của duplicate owner bị loại):

```python
    def iter_final_articles(
        self, urls: Iterable[str] | None = None
    ) -> Iterator[tuple[str, str]]:
        """Yield (url, payload) for extracted articles, inlining excluded owner text."""
        query = """
            SELECT a.url, a.payload
            FROM articles AS a
            JOIN url_records AS r ON r.url = a.url
            WHERE r.status = 'extracted'
        """
        if urls is None:
            rows: Iterable[tuple[str, str]] = self.connection.execute(query + " ORDER BY a.url;")
        else:
            def selected() -> Iterator[tuple[str, str]]:
                for url in sorted(set(urls)):
                    row = self.connection.execute(query + " AND a.url = ?;", (url,)).fetchone()
                    if row is not None:
                        yield row

            rows = selected()
        owner_text: dict[str, str] = {}
        for url, payload in rows:
            if '"duplicate_of"' in payload:
                article = json.loads(payload)
                owner_url = article.get("duplicate_of")
                if owner_url:
                    owner_status = self.connection.execute(
                        "SELECT status FROM url_records WHERE url = ?", (owner_url,)
                    ).fetchone()
                    if owner_status is None or owner_status[0] != "extracted":
                        if owner_url not in owner_text:
                            owner_row = self.connection.execute(
                                "SELECT payload FROM articles WHERE url = ?", (owner_url,)
                            ).fetchone()
                            if owner_row is None:
                                raise ValueError(f"missing duplicate owner article: {owner_url}")
                            text = json.loads(owner_row[0]).get("text")
                            if text is None:
                                raise ValueError(f"duplicate owner has no text: {owner_url}")
                            owner_text[owner_url] = str(text)
                        article["text"] = owner_text[owner_url]
                        article.pop("duplicate_of", None)
                        payload = json.dumps(article, ensure_ascii=False)
            yield url, payload
```

Trong `export_final`, thay khối `cursor = ...` và vòng `for url, payload in cursor:` bằng:

```python
        count = 0
        with tmp_articles.open("w", encoding="utf-8") as articles_file, tmp_urls.open(
            "w", encoding="utf-8"
        ) as urls_file:
            for url, payload in self.iter_final_articles():
                articles_file.write(f"{payload}\n")
                urls_file.write(f"{url}\n")
                count += 1
```

Trong dict `summary` của `export_final`, thêm sau `"description"`:

```python
            "crawl_id": self.crawl_id,
```

và ngay sau khi tạo `summary`:

```python
        if self.manifest.get("last_refresh_run_id"):
            summary["last_refresh_run_id"] = self.manifest["last_refresh_run_id"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: tất cả PASS (gồm các test export hiện có).

- [ ] **Step 5: Commit**

```bash
git add src/hust_crawler/crawl/state.py src/hust_crawler/crawl/writer.py tests/unit/crawl/test_state.py tests/unit/crawl/test_writer.py
git commit -m "feat: add crawl state helpers for refresh runs"
```

---

### Task 4: `refresh.py` — điều kiện chạy, chọn tập refresh, phiên và xuất changes

**Files:**
- Create: `src/hust_crawler/crawl/refresh.py`
- Test: `tests/unit/crawl/test_refresh.py`

**Interfaces:**
- Consumes: `RefreshStore` (Task 2); `CrawlState.crawl_id`, `refresh_run_id`, `backup_to`, `iter_final_articles` (Task 3); `CrawlerConfig.refresh_*` (Task 1).
- Produces:
  - `RefreshReason` (Literal), `RefreshSettings(pagination_empty_pages=1, recent_days=60, recent_runs=4, recheck_limit=5000)` + `RefreshSettings.from_config(config)`
  - `new_run_id(now: float) -> str`
  - `preflight(state_dir: Path) -> str | None` (run cần tiếp tục, hoặc `None`; `ValueError` nếu không được refresh)
  - `select_recent(connection, settings, recent_run_ids, now) -> list[str]`
  - `select_targets(connection, recursive_hostnames, settings, recent_run_ids, now) -> list[tuple[str, RefreshReason]]` (sắp theo URL)
  - `conditional_headers(record: Mapping) -> dict[str, str]`, `response_validators(headers) -> dict[str, str]` (khoá `etag`, `last_modified`)
  - `classify_refetch(previous_content_id: str | None, content_id: str, gate_reason: str | None) -> str` (`new`/`unchanged`/`modified`/`gate_<reason>`)
  - `class RefreshSession(store, run_id, settings)`: `.store`, `.run_id`, `.settings`, `follow_pagination(family_key, new_content_count) -> bool`, `add_pagination_target(url) -> bool`, `finish_target(url, outcome, error=None)`, `record_change(url, change, *, page_role, content_id, previous_content_id=None)`
  - `prepare_refresh_run(state, store, output, recursive_hostnames, settings, continue_run_id, *, now) -> tuple[str, list[tuple[str, str]]]`
  - `complete_refresh_run(state, session, output, status) -> dict[str, object]` (summary; xuất `changes/<run_id>/` khi status không phải `interrupted`/`failed`)

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time

import pytest

from hust_crawler.crawl.refresh import (
    RefreshSession,
    RefreshSettings,
    classify_refetch,
    complete_refresh_run,
    conditional_headers,
    new_run_id,
    preflight,
    prepare_refresh_run,
    response_validators,
    select_recent,
    select_targets,
)
from hust_crawler.crawl.refresh_store import RefreshStore
from hust_crawler.crawl.state import CrawlState

NOW = 1_790_000_000.0  # 2026-09-21
RECENT = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(NOW - 3 * 86400))
OLD = "2020-01-01T08:00:00+07:00"


def open_state(tmp_path: Path, *, resume: bool = False) -> CrawlState:
    input_path = tmp_path / "seeds.txt"
    if not input_path.exists():
        input_path.write_text("a.test\n", encoding="utf-8")
    return CrawlState.open(
        tmp_path / "out" / "state",
        phase="crawl",
        input_path=input_path,
        semantic_config={"crawl_strategy": "hybrid-unified"},
        runtime_config={},
        resume=resume,
    )


def put_page(
    state: CrawlState,
    url: str,
    *,
    role: str = "content",
    article: dict[str, object] | None = None,
    record: dict[str, object] | None = None,
) -> None:
    state.complete_url(
        {
            "url": url,
            "status": "extracted",
            "frontier_action": "scheduled",
            "response_purpose": "page",
            "discovery_source": "html_link",
            "seed_type": "recursive",
            "content_id": hashlib.sha256(url.encode()).hexdigest(),
            **(record or {}),
        },
        article={"url": url, "final_url": url, "text": url, "page_role": role, **(article or {})},
    )


def test_preflight_rejects_missing_and_unfinished_crawls(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="existing crawl"):
        preflight(tmp_path / "out" / "state")
    state = open_state(tmp_path)
    state.close()
    with pytest.raises(ValueError, match="finished crawl"):
        preflight(tmp_path / "out" / "state")


def test_preflight_starts_new_or_continues_interrupted_run(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    state.finish("complete", exit_code=0, counts={}, metrics={})
    state.close()
    assert preflight(tmp_path / "out" / "state") is None

    state = open_state(tmp_path, resume=True)
    store = RefreshStore(state.connection)
    store.start_run("20261006T000000Z")
    store.finish_run("20261006T000000Z", "interrupted", {})
    state.finish("interrupted", exit_code=130, counts={}, metrics={})
    state.close()
    assert preflight(tmp_path / "out" / "state") == "20261006T000000Z"


def test_select_targets_collects_each_hub_kind(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    put_page(state, "https://a.test/")
    put_page(state, "https://a.test/tin-tuc")
    put_page(
        state,
        "https://a.test/tin-tuc?page=2",
        role="navigation",
        record={"family_key": "a.test/tin-tuc?page={page}", "ordinal": 2, "discovered_from": "https://a.test/tin-tuc"},
    )
    put_page(state, "https://a.test/bai/recent", article={"published_at": RECENT})
    put_page(state, "https://a.test/bai/old", article={"published_at": OLD})
    put_page(state, "https://a.test/bai/undated")
    put_page(state, "https://a.test/bai/from-run", record={"first_seen_run": "20260920T000000Z"})
    for url, purpose in (("https://a.test/robots.txt", "robots"), ("https://a.test/sitemap.xml", "sitemap")):
        state.upsert_url({"url": url, "status": "scheduled", "response_purpose": purpose, "completed": True})
    state.upsert_url({"url": "https://b.test/robots.txt", "status": "scheduled", "response_purpose": "robots", "completed": True})

    targets = select_targets(
        state.connection, frozenset({"a.test"}), RefreshSettings(), ["20260920T000000Z"], NOW
    )

    assert targets == [
        ("https://a.test/", "homepage"),
        ("https://a.test/bai/from-run", "recent"),
        ("https://a.test/bai/recent", "recent"),
        ("https://a.test/robots.txt", "sitemap"),
        ("https://a.test/sitemap.xml", "sitemap"),
        ("https://a.test/tin-tuc", "pagination_head"),
        ("https://a.test/tin-tuc?page=2", "navigation"),
    ]
    state.close()


def test_select_recent_orders_newest_first_and_ignores_bad_dates(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    day = 86400
    put_page(state, "https://a.test/a", article={"published_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW - 5 * day))})
    put_page(state, "https://a.test/b", article={"published_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW - 1 * day))})
    put_page(state, "https://a.test/c", article={"modified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW - 2 * day))})
    put_page(state, "https://a.test/bad", article={"published_at": "08/09/2026", "modified_at": ""})

    recent = select_recent(state.connection, RefreshSettings(recheck_limit=2), [], NOW)

    assert recent == ["https://a.test/b", "https://a.test/c"]
    state.close()


def test_validators_round_trip_to_conditional_headers() -> None:
    validators = response_validators({b"ETag": b'"v1"', b"Last-Modified": b"Tue, 06 Oct 2026 00:00:00 GMT"})
    assert validators == {"etag": '"v1"', "last_modified": "Tue, 06 Oct 2026 00:00:00 GMT"}
    assert conditional_headers(validators) == {
        "If-None-Match": '"v1"',
        "If-Modified-Since": "Tue, 06 Oct 2026 00:00:00 GMT",
    }
    assert conditional_headers({}) == {}


def test_classify_refetch() -> None:
    assert classify_refetch(None, "x", None) == "new"
    assert classify_refetch("x", "x", None) == "unchanged"
    assert classify_refetch("x", "y", None) == "modified"
    assert classify_refetch("x", "y", "soft_404") == "gate_soft_404"


def test_follow_pagination_stops_after_configured_empty_pages(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    session = RefreshSession(RefreshStore(state.connection), "R", RefreshSettings(pagination_empty_pages=2))
    assert session.follow_pagination("f", 3) is True
    assert session.follow_pagination("f", 0) is True
    assert session.follow_pagination("f", 0) is False
    assert session.follow_pagination("g", 0) is True
    state.close()


def test_prepare_new_run_backs_up_and_stores_targets(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    put_page(state, "https://a.test/")
    store = RefreshStore(state.connection)
    output = tmp_path / "out"

    run_id, targets = prepare_refresh_run(
        state, store, output, frozenset({"a.test"}), RefreshSettings(), None, now=NOW
    )

    assert run_id == new_run_id(NOW)
    assert targets == [("https://a.test/", "homepage")]
    assert state.refresh_run_id == run_id
    assert (output / "archive" / f"pre-refresh-{run_id}" / "index.sqlite3").exists()
    assert store.latest_run()["status"] == "running"
    state.close()


def test_prepare_keeps_run_ids_strictly_increasing(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    store = RefreshStore(state.connection)
    store.start_run(new_run_id(NOW))
    store.finish_run(new_run_id(NOW), "complete", {})

    run_id, _ = prepare_refresh_run(
        state, store, tmp_path / "out", frozenset({"a.test"}), RefreshSettings(), None, now=NOW
    )

    assert run_id == new_run_id(NOW + 1)
    state.close()


def test_prepare_continues_interrupted_run_without_new_backup(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    store = RefreshStore(state.connection)
    store.start_run("20261006T000000Z")
    store.add_targets("20261006T000000Z", [("https://a.test/", "homepage"), ("https://a.test/x", "recent")])
    store.finish_target("20261006T000000Z", "https://a.test/", "unchanged")
    store.finish_run("20261006T000000Z", "interrupted", {})

    run_id, targets = prepare_refresh_run(
        state, store, tmp_path / "out", frozenset({"a.test"}), RefreshSettings(), "20261006T000000Z", now=NOW
    )

    assert run_id == "20261006T000000Z"
    assert targets == [("https://a.test/x", "recent")]
    assert not (tmp_path / "out" / "archive").exists()
    assert store.latest_run()["status"] == "running"
    state.close()


def test_complete_refresh_run_exports_changes(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    put_page(state, "https://a.test/new")
    store = RefreshStore(state.connection)
    output = tmp_path / "out"
    run_id, _ = prepare_refresh_run(state, store, output, frozenset(), RefreshSettings(), None, now=NOW)
    session = RefreshSession(store, run_id, RefreshSettings())
    session.record_change("https://a.test/new", "new", page_role="content", content_id="c1")

    summary = complete_refresh_run(state, session, output, "complete")

    run_dir = output / "changes" / run_id
    changes = [json.loads(line) for line in (run_dir / "changes.jsonl").read_text(encoding="utf-8").splitlines()]
    articles = [json.loads(line) for line in (run_dir / "articles.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [c["url"] for c in changes] == ["https://a.test/new"]
    assert [a["url"] for a in articles] == ["https://a.test/new"]
    assert json.loads((run_dir / "summary.json").read_text(encoding="utf-8")) == summary
    assert summary["new"] == 1 and summary["modified"] == 0
    assert summary["crawl_id"] == state.crawl_id
    assert state.manifest["last_refresh_run_id"] == run_id
    assert store.latest_run()["status"] == "complete"
    state.close()


def test_interrupted_refresh_run_exports_nothing(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    store = RefreshStore(state.connection)
    output = tmp_path / "out"
    run_id, _ = prepare_refresh_run(state, store, output, frozenset(), RefreshSettings(), None, now=NOW)

    complete_refresh_run(state, RefreshSession(store, run_id, RefreshSettings()), output, "interrupted")

    assert not (output / "changes").exists()
    assert "last_refresh_run_id" not in state.manifest
    assert store.latest_run()["status"] == "interrupted"
    state.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/crawl/test_refresh.py -q`
Expected: FAIL với `ModuleNotFoundError: hust_crawler.crawl.refresh`.

- [ ] **Step 3: Implement**

```python
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time
from typing import Any, Literal
from urllib.parse import urlsplit

from hust_crawler.config import CrawlerConfig
from .refresh_store import CONTINUABLE_RUN_STATUSES, RefreshStore
from .state import CrawlState

RefreshReason = Literal[
    "homepage", "sitemap", "navigation", "pagination_head", "recent", "pagination_next"
]
FINISHED_CRAWL_STATUSES = frozenset({"complete", "complete_with_failures", "truncated"})
RUN_ID_FORMAT = "%Y%m%dT%H%M%SZ"


@dataclass(frozen=True, slots=True)
class RefreshSettings:
    pagination_empty_pages: int = 1
    recent_days: int = 60
    recent_runs: int = 4
    recheck_limit: int = 5000

    @classmethod
    def from_config(cls, config: CrawlerConfig) -> RefreshSettings:
        return cls(
            pagination_empty_pages=config.refresh_pagination_empty_pages,
            recent_days=config.refresh_recent_days,
            recent_runs=config.refresh_recent_runs,
            recheck_limit=config.refresh_recheck_limit,
        )


def new_run_id(now: float) -> str:
    return time.strftime(RUN_ID_FORMAT, time.gmtime(now))


def _run_timestamp(run_id: str) -> float:
    return datetime.strptime(run_id, RUN_ID_FORMAT).replace(tzinfo=UTC).timestamp()


def _timestamp(value: object) -> float | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def _hostname(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().rstrip(".")


def _latest_run(db_path: Path) -> tuple[str, str] | None:
    if not db_path.exists():
        return None
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'refresh_runs';"
        ).fetchone()
        if table is None:
            return None
        row = connection.execute(
            "SELECT run_id, status FROM refresh_runs ORDER BY run_id DESC LIMIT 1;"
        ).fetchone()
        return (str(row[0]), str(row[1])) if row else None
    finally:
        connection.close()


def preflight(state_dir: Path) -> str | None:
    """Return the refresh run to continue, or None to start a new one."""
    manifest_path = state_dir / "manifest.json"
    if not manifest_path.exists():
        raise ValueError(f"--refresh requires an existing crawl: {state_dir.parent}")
    latest = _latest_run(state_dir / "index.sqlite3")
    if latest is not None and latest[1] in CONTINUABLE_RUN_STATUSES:
        return latest[0]
    status = json.loads(manifest_path.read_text(encoding="utf-8")).get("status")
    if status not in FINISHED_CRAWL_STATUSES:
        raise ValueError(f"--refresh requires a finished crawl; current status is {status}")
    return None


def _homepages(connection: sqlite3.Connection, recursive_hostnames: Iterable[str]) -> list[str]:
    urls: list[str] = []
    for hostname in sorted(recursive_hostnames):
        urls.append(f"https://{hostname}/")
        row = connection.execute(
            "SELECT status FROM url_records WHERE url = ?;", (f"http://{hostname}/",)
        ).fetchone()
        if row is not None and row[0] == "extracted":
            urls.append(f"http://{hostname}/")
    return urls


def _sitemaps(connection: sqlite3.Connection) -> list[str]:
    # A robots/sitemap response that parsed successfully keeps status 'scheduled'
    # and is only checkpointed; absent or failed ones get another status.
    rows = connection.execute(
        """
        SELECT url, json_extract(payload, '$.response_purpose')
        FROM url_records
        WHERE completed = 1 AND status = 'scheduled'
          AND json_extract(payload, '$.response_purpose') IN ('robots', 'sitemap')
        ORDER BY url;
        """
    ).fetchall()
    sitemap_hosts = {_hostname(url) for url, purpose in rows if purpose == "sitemap"}
    return [
        str(url) for url, purpose in rows if purpose == "sitemap" or _hostname(url) in sitemap_hosts
    ]


def _navigation_pages(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        """
        SELECT a.url FROM articles AS a JOIN url_records AS r ON r.url = a.url
        WHERE r.status = 'extracted' AND json_extract(a.payload, '$.page_role') = 'navigation'
        ORDER BY a.url;
        """
    )
    return [str(row[0]) for row in rows]


def _pagination_heads(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        """
        SELECT json_extract(payload, '$.family_key'), url, json_extract(payload, '$.discovered_from')
        FROM url_records
        WHERE status = 'extracted' AND json_extract(payload, '$.family_key') IS NOT NULL
        ORDER BY json_extract(payload, '$.family_key'),
                 json_extract(payload, '$.ordinal') IS NULL,
                 json_extract(payload, '$.ordinal'),
                 url;
        """
    ).fetchall()
    heads: list[str] = []
    seen: set[str] = set()
    for family_key, url, parent in rows:
        if family_key in seen:
            continue
        seen.add(family_key)
        head = str(url)
        if parent:
            parent_row = connection.execute(
                "SELECT status FROM url_records WHERE url = ?;", (parent,)
            ).fetchone()
            if parent_row is not None and parent_row[0] == "extracted":
                head = str(parent)
        heads.append(head)
    return heads


def select_recent(
    connection: sqlite3.Connection,
    settings: RefreshSettings,
    recent_run_ids: Iterable[str],
    now: float,
) -> list[str]:
    cutoff = now - settings.recent_days * 86400
    runs = set(recent_run_ids)
    scored: list[tuple[float, str]] = []
    rows = connection.execute(
        """
        SELECT a.url,
               json_extract(a.payload, '$.published_at'),
               json_extract(a.payload, '$.modified_at'),
               json_extract(r.payload, '$.first_seen_run')
        FROM articles AS a JOIN url_records AS r ON r.url = a.url
        WHERE r.status = 'extracted' AND json_extract(a.payload, '$.page_role') = 'content';
        """
    )
    for url, published_at, modified_at, first_seen_run in rows:
        dated = [
            stamp
            for stamp in (_timestamp(modified_at), _timestamp(published_at))
            if stamp is not None
        ]
        if dated and max(dated) >= cutoff:
            scored.append((max(dated), str(url)))
        elif first_seen_run in runs:
            scored.append((_run_timestamp(str(first_seen_run)), str(url)))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [url for _, url in scored[: settings.recheck_limit]]


def select_targets(
    connection: sqlite3.Connection,
    recursive_hostnames: Iterable[str],
    settings: RefreshSettings,
    recent_run_ids: Iterable[str],
    now: float,
) -> list[tuple[str, RefreshReason]]:
    groups: tuple[tuple[RefreshReason, list[str]], ...] = (
        ("homepage", _homepages(connection, recursive_hostnames)),
        ("sitemap", _sitemaps(connection)),
        ("navigation", _navigation_pages(connection)),
        ("pagination_head", _pagination_heads(connection)),
        ("recent", select_recent(connection, settings, recent_run_ids, now)),
    )
    selected: dict[str, RefreshReason] = {}
    for reason, urls in groups:
        for url in urls:
            selected.setdefault(url, reason)
    return sorted(selected.items())


def conditional_headers(record: Mapping[str, object]) -> dict[str, str]:
    headers: dict[str, str] = {}
    if record.get("etag"):
        headers["If-None-Match"] = str(record["etag"])
    if record.get("last_modified"):
        headers["If-Modified-Since"] = str(record["last_modified"])
    return headers


def response_validators(headers: Any) -> dict[str, str]:
    values: dict[str, str] = {}
    for header, key in ((b"ETag", "etag"), (b"Last-Modified", "last_modified")):
        raw = headers.get(header)
        if raw:
            values[key] = raw.decode("latin1") if isinstance(raw, bytes) else str(raw)
    return values


def classify_refetch(
    previous_content_id: str | None, content_id: str, gate_reason: str | None
) -> str:
    if gate_reason is not None:
        return f"gate_{gate_reason}"
    if previous_content_id is None:
        return "new"
    return "unchanged" if previous_content_id == content_id else "modified"


class RefreshSession:
    def __init__(self, store: RefreshStore, run_id: str, settings: RefreshSettings) -> None:
        self.store = store
        self.run_id = run_id
        self.settings = settings
        self._empty_pages: dict[str, int] = defaultdict(int)

    def follow_pagination(self, family_key: str, new_content_count: int) -> bool:
        if new_content_count > 0:
            self._empty_pages[family_key] = 0
            return True
        self._empty_pages[family_key] += 1
        return self._empty_pages[family_key] < self.settings.pagination_empty_pages

    def add_pagination_target(self, url: str) -> bool:
        return self.store.add_target(self.run_id, url, "pagination_next")

    def finish_target(self, url: str, outcome: str, error: str | None = None) -> None:
        self.store.finish_target(self.run_id, url, outcome, error)

    def record_change(
        self,
        url: str,
        change: str,
        *,
        page_role: str,
        content_id: str,
        previous_content_id: str | None = None,
    ) -> None:
        record: dict[str, object] = {
            "url": url,
            "change": change,
            "page_role": page_role,
            "content_id": content_id,
        }
        if previous_content_id is not None:
            record["previous_content_id"] = previous_content_id
        self.store.record_change(self.run_id, record)


def prepare_refresh_run(
    state: CrawlState,
    store: RefreshStore,
    output: Path,
    recursive_hostnames: Iterable[str],
    settings: RefreshSettings,
    continue_run_id: str | None,
    *,
    now: float,
) -> tuple[str, list[tuple[str, str]]]:
    if continue_run_id is not None:
        store.resume_run(continue_run_id)
        state.refresh_run_id = continue_run_id
        return continue_run_id, store.pending_targets(continue_run_id)

    run_id = new_run_id(now)
    latest = store.latest_run()
    while latest is not None and run_id <= str(latest["run_id"]):
        now += 1
        run_id = new_run_id(now)
    state.backup_to(output / "archive" / f"pre-refresh-{run_id}" / "index.sqlite3")
    targets = select_targets(
        state.connection,
        recursive_hostnames,
        settings,
        store.completed_run_ids(settings.recent_runs),
        now,
    )
    store.start_run(run_id)
    store.add_targets(run_id, targets)
    state.refresh_run_id = run_id
    return run_id, store.pending_targets(run_id)


def _export_changes(
    state: CrawlState,
    changes: list[dict[str, object]],
    destination: Path,
    summary: dict[str, object],
) -> None:
    temporary = destination.with_name(f".{destination.name}.tmp")
    shutil.rmtree(temporary, ignore_errors=True)
    temporary.mkdir(parents=True)
    with (temporary / "changes.jsonl").open("w", encoding="utf-8") as file:
        for change in changes:
            file.write(json.dumps(change, ensure_ascii=False) + "\n")
    with (temporary / "articles.jsonl").open("w", encoding="utf-8") as file:
        for _, payload in state.iter_final_articles(str(change["url"]) for change in changes):
            file.write(f"{payload}\n")
    (temporary / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    shutil.rmtree(destination, ignore_errors=True)
    os.replace(temporary, destination)


def complete_refresh_run(
    state: CrawlState, session: RefreshSession, output: Path, status: str
) -> dict[str, object]:
    store = session.store
    changes = list(store.iter_changes(session.run_id))
    summary: dict[str, object] = {
        "run_id": session.run_id,
        "crawl_id": state.crawl_id,
        "status": status,
        "started_at": store.run_started_at(session.run_id),
        "finished_at": time.time(),
        **store.target_summary(session.run_id),
        "new": sum(1 for change in changes if change["change"] == "new"),
        "modified": sum(1 for change in changes if change["change"] == "modified"),
    }
    store.finish_run(session.run_id, status, summary)
    if status not in {"interrupted", "failed"}:
        state.manifest["last_refresh_run_id"] = session.run_id
        _export_changes(state, changes, output / "changes" / session.run_id, summary)
    return summary
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/crawl/test_refresh.py -q && uv run ruff check src/hust_crawler/crawl/refresh.py`
Expected: PASS, ruff sạch.

- [ ] **Step 5: Commit**

```bash
git add src/hust_crawler/crawl/refresh.py tests/unit/crawl/test_refresh.py
git commit -m "feat: select refresh targets and export refresh changes"
```

---

### Task 5: Tái cấu trúc spider (không đổi hành vi) và lưu ETag/Last-Modified

**Files:**
- Modify: `src/hust_crawler/crawl/spider.py`
- Test: `tests/unit/crawl/test_refresh_spider.py` (file mới, dùng `CrawlState` thật)

**Interfaces:**
- Consumes: `response_validators` (Task 4), `CrawlState.update_url_fields`/`url_record` (Task 3).
- Produces (dùng ở Task 6):
  - `UnifiedSpider._resume_requests() -> Iterator[scrapy.Request]`
  - `UnifiedSpider._parse_crawl_response(response) -> Iterator[scrapy.Request]` (thân cũ của `parse_response`)
  - `UnifiedSpider._article_record(response, analysis, input_url: str, content_type: str) -> dict[str, Any]`
  - `UnifiedSpider._expand_html(response, analysis, stored_article: dict[str, Any], input_url: str, seed_mode: str) -> Iterator[scrapy.Request]`
  - `UnifiedSpider._store_validators(url: str, response: Response, **extra: object) -> None`

- [ ] **Step 1: Write the failing test**

Tạo `tests/unit/crawl/test_refresh_spider.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

from scrapy.http import HtmlResponse, Request

from hust_crawler.config import CrawlerConfig
from hust_crawler.crawl.article import analyze_article
from hust_crawler.crawl.assets import AssetPolicy
from hust_crawler.crawl.frontier import FrontierPolicy
from hust_crawler.crawl.options import CrawlOptions
from hust_crawler.crawl.seeds import SeedSet
from hust_crawler.crawl.sitemaps import SitemapDiscoveryCoordinator
from hust_crawler.crawl.spider import UnifiedSpider
from hust_crawler.crawl.state import CrawlState
from hust_crawler.crawl.writer import CrawlWriter

SEEDS = SeedSet(frozenset({"a.test"}), (), ())


def open_writer(tmp_path: Path) -> CrawlWriter:
    input_path = tmp_path / "seeds.txt"
    input_path.write_text("a.test\n", encoding="utf-8")
    options = CrawlOptions()
    state = CrawlState.open(
        tmp_path / "state",
        phase="crawl",
        input_path=input_path,
        semantic_config=options.semantic_snapshot(SEEDS),
        runtime_config={},
    )
    return CrawlWriter(tmp_path, state, options)


def make_spider(writer: CrawlWriter, **kwargs: Any) -> UnifiedSpider:
    frontier = FrontierPolicy(SEEDS, writer.options)
    frontier.restore(writer.state.iter_url_records())
    return UnifiedSpider(
        seeds=SEEDS,
        config=CrawlerConfig(hostnames=SEEDS.allowed_hostnames, contact="ops@example.com"),
        options=writer.options,
        frontier=frontier,
        writer=writer,
        policy=AssetPolicy(writer.options),
        coordinator=SitemapDiscoveryCoordinator(strategy="hybrid-unified"),
        **kwargs,
    )


def page(
    request: Request, body: str, *, status: int = 200, headers: dict[bytes, bytes] | None = None
) -> HtmlResponse:
    return HtmlResponse(
        url=request.url,
        status=status,
        headers={b"Content-Type": b"text/html; charset=utf-8", **(headers or {})},
        body=body.encode("utf-8"),
        encoding="utf-8",
        request=request,
    )


def known_page(writer: CrawlWriter, url: str, body: str, **record: object) -> dict[str, Any]:
    """Store url as an extracted page whose text matches what the spider extracts from body."""
    text = str(analyze_article(body.encode("utf-8"), url, "utf-8").article.get("text") or "")
    writer.state.merge_url(
        {
            "url": url,
            "status": "scheduled",
            "frontier_action": "scheduled",
            "response_purpose": "page",
            "discovery_source": "html_link",
            "seed_type": "recursive",
            **record,
        }
    )
    return writer.write_article({"url": url, "final_url": url, "text": text, "page_role": "content"})


def test_html_response_stores_http_validators(tmp_path: Path) -> None:
    writer = open_writer(tmp_path)
    spider = make_spider(writer)
    request = next(r for r in spider.start_requests() if r.url == "https://a.test/")

    list(
        spider.parse_response(
            page(
                request,
                "<main><h1>Home</h1><p>Welcome to the department news portal.</p></main>",
                headers={b"ETag": b'"v1"', b"Last-Modified": b"Tue, 06 Oct 2026 00:00:00 GMT"},
            )
        )
    )

    record = writer.state.url_record("https://a.test/")
    assert record["status"] == "extracted"
    assert record["etag"] == '"v1"'
    assert record["last_modified"] == "Tue, 06 Oct 2026 00:00:00 GMT"
    writer.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/crawl/test_refresh_spider.py -q`
Expected: FAIL với `KeyError: 'etag'`.

- [ ] **Step 3: Implement (thuần di chuyển code + một hook)**

Trong `spider.py`:

1. Import: `from .refresh import response_validators`.
2. Chuyển nguyên vòng `for record in self.resume_records:` ở đầu `start_requests` sang method mới, và để `start_requests` gọi lại:

```python
    def _resume_requests(self) -> Iterator[scrapy.Request]:
        for record in self.resume_records:
            ...  # thân vòng lặp hiện có, không đổi
```

```python
    def start_requests(self) -> Iterator[scrapy.Request]:
        yield from self._resume_requests()

        # Recursive hostname seeds (schedule robots, 3 sitemaps, and homepage)
        ...  # phần còn lại giữ nguyên
```

3. Đổi tên `parse_response` hiện có thành `_parse_crawl_response` (giữ nguyên thân) và thêm dispatcher:

```python
    def parse_response(self, response: Response) -> Iterator[scrapy.Request]:
        yield from self._parse_crawl_response(response)
```

4. Thêm helpers:

```python
    def _article_record(
        self, response: Response, analysis: Any, input_url: str, content_type: str
    ) -> dict[str, Any]:
        return {
            "url": input_url,
            "final_url": response.url,
            **analysis.article,
            "page_role": self.frontier.page_role(
                response.url, cast(Any, response.meta.get("link_kind"))
            ),
            "status": response.status,
            "content_type": content_type,
        }

    def _store_validators(self, url: str, response: Response, **extra: object) -> None:
        state = getattr(self.writer, "state", None)
        if state is None or not hasattr(state, "update_url_fields"):
            return
        fields: dict[str, object] = {**response_validators(response.headers), **extra}
        if fields:
            state.update_url_fields(url, fields)
```

5. Tạo `_expand_html(self, response, analysis, stored_article, input_url, seed_mode)`: **chuyển nguyên văn** mọi dòng của nhánh `if route.action == "html":` bắt đầu từ `gate_reason = self._expansion_gate_reason(response, stored_article)` đến hết dòng `self.writer.checkpoint(input_url)` kết thúc nhánh. Bên trong method mới, `return` trong khối `gate_reason is not None` giữ nguyên (method là generator).

6. Nhánh html trong `_parse_crawl_response` còn lại:

```python
        if route.action == "html":
            analysis = analyze_article(
                response.body, response.url, getattr(response, "encoding", None)
            )
            article_data = self._article_record(response, analysis, input_url, content_type)
            if hasattr(self.writer, "write_article"):
                stored_article = self.writer.write_article(article_data)
            elif hasattr(self.writer, "put_article"):
                self.writer.put_article(article_data)
                stored_article = article_data
            else:
                stored_article = article_data
            self._store_validators(input_url, response)
            yield from self._expand_html(response, analysis, stored_article, input_url, seed_mode)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: tất cả PASS (test spider/integration hiện có xác nhận hành vi không đổi).

- [ ] **Step 5: Commit**

```bash
git add src/hust_crawler/crawl/spider.py tests/unit/crawl/test_refresh_spider.py
git commit -m "refactor: split spider html handling and store http validators"
```

---

### Task 6: Chế độ refresh trong spider và bỏ qua 304 ở Playwright fallback

**Files:**
- Modify: `src/hust_crawler/crawl/spider.py`
- Modify: `src/hust_crawler/crawl/middlewares.py` (`PlaywrightFallbackMiddleware.process_response`)
- Test: `tests/unit/crawl/test_refresh_spider.py`, `tests/unit/crawl/test_middlewares.py`

**Interfaces:**
- Consumes: `RefreshSession`, `RefreshSettings`, `classify_refetch`, `conditional_headers` (Task 4); `content_id_for` (Task 3); `RefreshStore` (Task 2); helpers Task 5.
- Produces: `UnifiedSpider(..., refresh: RefreshSession | None = None, refresh_targets: Iterable[tuple[str, str]] = ())`. Khi có `refresh`: `start_requests` chỉ phát resume records + một request cho mỗi target (meta `refresh_reason`, header điều kiện); request refresh không ghi bảng `errors` và không hạ `status`.

- [ ] **Step 1: Write the failing tests**

Thêm vào `tests/unit/crawl/test_refresh_spider.py`:

```python
import json
from types import SimpleNamespace

from hust_crawler.crawl.refresh import RefreshSession, RefreshSettings
from hust_crawler.crawl.refresh_store import RefreshStore

RUN_ID = "20261006T000000Z"
ARTICLE_1 = "<main><h1>Bài một</h1><p>Nội dung bài một của khoa, đủ dài để trích xuất.</p></main>"
ARTICLE_2 = "<main><h1>Bài hai</h1><p>Nội dung bài hai của khoa, đủ dài để trích xuất.</p></main>"
LIST_V1 = '<main><h1>Tin tức</h1><p>Danh sách bài mới.</p><a href="/bai/1">Bài một</a></main>'
LIST_V2 = (
    '<main><h1>Tin tức</h1><p>Danh sách bài mới, đã cập nhật bài hai.</p>'
    '<a href="/bai/2">Bài hai</a><a href="/bai/1">Bài một</a></main>'
)


def refresh_spider(
    writer: CrawlWriter, targets: list[tuple[str, str]], settings: RefreshSettings | None = None
) -> tuple[UnifiedSpider, RefreshStore]:
    store = RefreshStore(writer.state.connection)
    store.start_run(RUN_ID)
    store.add_targets(RUN_ID, targets)
    writer.state.refresh_run_id = RUN_ID
    session = RefreshSession(store, RUN_ID, settings or RefreshSettings())
    return make_spider(writer, refresh=session, refresh_targets=store.pending_targets(RUN_ID)), store


def outcome(store: RefreshStore, url: str) -> str:
    return store.connection.execute(
        "SELECT outcome FROM refresh_targets WHERE run_id = ? AND url = ?;", (RUN_ID, url)
    ).fetchone()[0]


def changes(store: RefreshStore) -> dict[str, str]:
    return {str(c["url"]): str(c["change"]) for c in store.iter_changes(RUN_ID)}


def article_text(writer: CrawlWriter, url: str) -> str:
    payload = writer.state.connection.execute(
        "SELECT payload FROM articles WHERE url = ?;", (url,)
    ).fetchone()[0]
    return str(json.loads(payload).get("text"))


def test_refresh_requests_only_targets_with_validators(tmp_path: Path) -> None:
    writer = open_writer(tmp_path)
    known_page(writer, "https://a.test/tin-tuc", LIST_V1, etag='"v1"')
    known_page(writer, "https://a.test/bai/1", ARTICLE_1)
    spider, _ = refresh_spider(writer, [("https://a.test/tin-tuc", "pagination_head")])

    requests = list(spider.start_requests())

    assert [r.url for r in requests] == ["https://a.test/tin-tuc"]
    assert requests[0].headers.get(b"If-None-Match") == b'"v1"'
    assert requests[0].meta["refresh_reason"] == "pagination_head"
    writer.close()


def test_not_modified_target_is_closed_without_expansion(tmp_path: Path) -> None:
    writer = open_writer(tmp_path)
    known_page(writer, "https://a.test/tin-tuc", LIST_V1, etag='"v1"')
    spider, store = refresh_spider(writer, [("https://a.test/tin-tuc", "navigation")])
    [request] = list(spider.start_requests())

    assert list(spider.parse_response(page(request, "", status=304))) == []
    assert outcome(store, "https://a.test/tin-tuc") == "not_modified"
    assert changes(store) == {}
    writer.close()


def test_unchanged_page_records_no_change_and_fetches_nothing(tmp_path: Path) -> None:
    writer = open_writer(tmp_path)
    known_page(writer, "https://a.test/tin-tuc", LIST_V1)
    known_page(writer, "https://a.test/bai/1", ARTICLE_1)
    spider, store = refresh_spider(writer, [("https://a.test/tin-tuc", "navigation")])
    [request] = list(spider.start_requests())

    assert list(spider.parse_response(page(request, LIST_V1))) == []
    assert outcome(store, "https://a.test/tin-tuc") == "unchanged"
    assert changes(store) == {}
    assert writer.state.url_record("https://a.test/tin-tuc")["last_checked_run"] == RUN_ID
    writer.close()


def test_changed_hub_is_modified_and_only_new_link_is_fetched(tmp_path: Path) -> None:
    writer = open_writer(tmp_path)
    known_page(writer, "https://a.test/tin-tuc", LIST_V1)
    known_page(writer, "https://a.test/bai/1", ARTICLE_1)
    spider, store = refresh_spider(writer, [("https://a.test/tin-tuc", "navigation")])
    [request] = list(spider.start_requests())

    follow_ups = list(spider.parse_response(page(request, LIST_V2)))

    assert [r.url for r in follow_ups] == ["https://a.test/bai/2"]
    assert writer.state.url_record("https://a.test/bai/2")["first_seen_run"] == RUN_ID
    assert "cập nhật bài hai" in article_text(writer, "https://a.test/tin-tuc")
    list(spider.parse_response(page(follow_ups[0], ARTICLE_2)))
    assert changes(store) == {"https://a.test/bai/2": "new", "https://a.test/tin-tuc": "modified"}
    writer.close()


def test_failed_refetch_keeps_existing_article(tmp_path: Path) -> None:
    writer = open_writer(tmp_path)
    known_page(writer, "https://a.test/tin-tuc", LIST_V1)
    before = article_text(writer, "https://a.test/tin-tuc")
    spider, store = refresh_spider(writer, [("https://a.test/tin-tuc", "navigation")])
    [request] = list(spider.start_requests())

    assert list(spider.parse_response(page(request, "<h1>Server error</h1>", status=500))) == []

    assert outcome(store, "https://a.test/tin-tuc") == "http_500"
    assert writer.state.url_record("https://a.test/tin-tuc")["status"] == "extracted"
    assert article_text(writer, "https://a.test/tin-tuc") == before
    assert writer.state.connection.execute("SELECT count(*) FROM errors").fetchone()[0] == 0
    writer.close()


def test_soft_404_refetch_keeps_existing_article(tmp_path: Path) -> None:
    writer = open_writer(tmp_path)
    known_page(writer, "https://a.test/tin-tuc", LIST_V1)
    before = article_text(writer, "https://a.test/tin-tuc")
    spider, store = refresh_spider(writer, [("https://a.test/tin-tuc", "navigation")])
    [request] = list(spider.start_requests())

    body = "<html><head><title>404 Not Found</title></head><body><h1>404 Not Found</h1></body></html>"
    list(spider.parse_response(page(request, body)))

    assert outcome(store, "https://a.test/tin-tuc").startswith("gate_")
    assert article_text(writer, "https://a.test/tin-tuc") == before
    assert changes(store) == {}
    writer.close()


def test_request_error_on_refresh_target_is_not_a_crawl_error(tmp_path: Path) -> None:
    writer = open_writer(tmp_path)
    known_page(writer, "https://a.test/tin-tuc", LIST_V1)
    spider, store = refresh_spider(writer, [("https://a.test/tin-tuc", "navigation")])
    [request] = list(spider.start_requests())

    spider.on_request_error(
        SimpleNamespace(request=request, value=TimeoutError("timed out"), type=TimeoutError)
    )

    assert outcome(store, "https://a.test/tin-tuc") == "request_failed"
    assert writer.state.url_record("https://a.test/tin-tuc")["status"] == "extracted"
    assert writer.state.connection.execute("SELECT count(*) FROM errors").fetchone()[0] == 0
    writer.close()


def test_pagination_refetches_known_next_page_only_while_new_links_appear(tmp_path: Path) -> None:
    writer = open_writer(tmp_path)
    page1_v1 = LIST_V1.replace("</main>", '<a href="/tin-tuc?page=2">2</a></main>')
    page2 = (
        '<main><h1>Tin tức trang 2</h1><p>Danh sách cũ.</p>'
        '<a href="/bai/1">Bài một</a><a href="/tin-tuc?page=3">3</a></main>'
    )
    known_page(writer, "https://a.test/tin-tuc", page1_v1)
    known_page(writer, "https://a.test/tin-tuc?page=2", page2)
    known_page(writer, "https://a.test/tin-tuc?page=3", page2.replace("trang 2", "trang 3"))
    known_page(writer, "https://a.test/bai/1", ARTICLE_1)
    spider, store = refresh_spider(writer, [("https://a.test/tin-tuc", "pagination_head")])
    [request] = list(spider.start_requests())

    follow_ups = list(spider.parse_response(page(request, LIST_V2.replace("</main>", '<a href="/tin-tuc?page=2">2</a></main>'))))

    by_url = {r.url: r for r in follow_ups}
    assert set(by_url) == {"https://a.test/bai/2", "https://a.test/tin-tuc?page=2"}
    assert by_url["https://a.test/tin-tuc?page=2"].meta["refresh_reason"] == "pagination_next"
    assert list(spider.parse_response(page(by_url["https://a.test/tin-tuc?page=2"], page2))) == []
    assert outcome(store, "https://a.test/tin-tuc?page=2") == "unchanged"
    writer.close()
```

Thêm vào `tests/unit/crawl/test_middlewares.py`:

```python
def test_playwright_fallback_ignores_not_modified_response() -> None:
    request = Request("https://a.test/")
    response = HtmlResponse(
        "https://a.test/",
        status=304,
        headers={b"Content-Type": b"text/html"},
        body=b"",
        request=request,
    )

    assert PlaywrightFallbackMiddleware().process_response(request, response) is response
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/crawl/test_refresh_spider.py tests/unit/crawl/test_middlewares.py -q`
Expected: FAIL (`TypeError: ... unexpected keyword argument 'refresh'`; test middleware trả request Playwright thay vì response).

- [ ] **Step 3: Implement**

`middlewares.py`, dòng đầu của `PlaywrightFallbackMiddleware.process_response`:

```python
        if response.status == 304:
            return response
```

`spider.py`:

1. Import thêm:

```python
from hust_crawler.policies.url import canonicalize_url
from .refresh import RefreshSession, classify_refetch, conditional_headers, response_validators
from .writer import CrawlWriter, StorageLimit, content_id_for
```

2. `__init__` thêm tham số và thuộc tính:

```python
        refresh: RefreshSession | None = None,
        refresh_targets: Iterable[tuple[str, str]] = (),
```

```python
        self.refresh = refresh
        self.refresh_targets = tuple(refresh_targets)
```

3. `start_requests`, ngay sau `yield from self._resume_requests()`:

```python
        if self.refresh is not None:
            for url, reason in self.refresh_targets:
                yield self._refresh_request(url, reason)
            return
```

4. Thêm method:

```python
    def _refresh_request(self, url: str, reason: str) -> scrapy.Request:
        record = self.writer.state.url_record(url) or {}
        hostname = (urlsplit(url).hostname or "").lower().rstrip(".")
        meta: dict[str, Any] = {
            "allowed_hostnames": frozenset({hostname}),
            "input_url": url,
            "seed_mode": "exact" if record.get("seed_type") == "exact" else "recursive",
            "explicit": bool(record.get("explicit", False)),
            "response_purpose": str(record.get("response_purpose") or "page"),
            "discovery_source": record.get("discovery_source") or "seed",
            "discovered_from": record.get("discovered_from"),
            "handle_httpstatus_all": True,
            "request_scope": "page",
            "asset_mode": self.options.assets,
            "link_kind": record.get("link_kind"),
            "family_key": record.get("family_key"),
            "ordinal": record.get("ordinal"),
            "refresh_reason": reason,
            "previous_content_id": (
                record.get("content_id") if record.get("status") == "extracted" else None
            ),
            "previous_duplicate_of": record.get("duplicate_of"),
        }
        return scrapy.Request(
            url,
            callback=self.parse_response,
            errback=self.on_request_error,
            priority=500,
            headers=conditional_headers(record),
            meta=meta,
            dont_filter=True,
        )

    def _refresh_pagination_request(self, url: str) -> scrapy.Request | None:
        assert self.refresh is not None
        canonical, _, _ = canonicalize_url(url)
        if not canonical:
            return None
        record = self.writer.state.url_record(canonical)
        if record is None or record.get("status") != "extracted":
            return None
        if not self.refresh.add_pagination_target(canonical):
            return None
        return self._refresh_request(canonical, "pagination_next")

    def _parse_refresh_response(self, response: Response) -> Iterator[scrapy.Request]:
        assert self.refresh is not None
        url = str(response.meta.get("input_url", response.url))
        if response.status == 304:
            self.refresh.finish_target(url, "not_modified")
            return
        if response.status >= 300:
            self.refresh.finish_target(url, f"http_{response.status}")
            return
        if response.meta.get("response_purpose") in {"robots", "sitemap"}:
            yield from self._parse_crawl_response(response)
            self.refresh.finish_target(url, "fetched")
            return
        content_type = response.headers.get(b"Content-Type", b"").decode("latin1", errors="replace")
        mime = content_type.partition(";")[0].strip().lower()
        if (
            "download_stopped" in getattr(response, "flags", [])
            or response.meta.get("header_stop_reason")
            or (mime and mime not in {"text/html", "application/xhtml+xml"})
        ):
            self.refresh.finish_target(url, "non_html")
            return
        analysis = analyze_article(response.body, response.url, getattr(response, "encoding", None))
        text = str(analysis.article.get("text") or "")
        gate = classify_page_gate(
            status=response.status, url=response.url, html=response.text, text=text
        )
        previous = response.meta.get("previous_content_id")
        outcome = classify_refetch(
            previous, content_id_for(text), None if gate.expandable else gate.reason
        )
        if outcome.startswith("gate_"):
            self.refresh.finish_target(url, outcome)
            return
        state = self.writer.state
        if outcome == "unchanged":
            stored_article: dict[str, Any] = {
                "url": url,
                "text": text,
                "content_id": previous,
                "duplicate_of": response.meta.get("previous_duplicate_of"),
            }
        else:
            if outcome == "modified":
                state.release_content_owner(url, str(previous))
                state.update_url_fields(url, {"duplicate_of": None})
            article_data = self._article_record(response, analysis, url, content_type)
            stored_article = self.writer.write_article(article_data)
            self.refresh.record_change(
                url,
                outcome,
                page_role=str(article_data["page_role"]),
                content_id=str(stored_article["content_id"]),
                previous_content_id=previous,
            )
        self._store_validators(url, response, last_checked_run=self.refresh.run_id)
        self.refresh.finish_target(url, outcome)
        yield from self._expand_html(
            response, analysis, stored_article, url, str(response.meta.get("seed_mode", "recursive"))
        )
```

5. `parse_response` thành dispatcher:

```python
    def parse_response(self, response: Response) -> Iterator[scrapy.Request]:
        if self.refresh is not None and response.meta.get("refresh_reason"):
            yield from self._parse_refresh_response(response)
        else:
            yield from self._parse_crawl_response(response)
```

6. Trong nhánh html của `_parse_crawl_response`, trước `yield from self._expand_html(...)`:

```python
            if self.refresh is not None:
                self.refresh.record_change(
                    input_url,
                    "new",
                    page_role=str(article_data["page_role"]),
                    content_id=str(stored_article["content_id"]),
                )
```

7. Trong `_expand_html`, đổi `if current_family_key:` (khối `FamilyObservation`/`observe_family`/`put_route_family`/`observe_pagination`) thành:

```python
                if current_family_key and self.refresh is None:
```

và thay toàn bộ vòng `for f_key, links in pagination_by_family.items():` bằng:

```python
                for f_key, links in pagination_by_family.items():
                    if self.refresh is not None:
                        if not self.refresh.follow_pagination(f_key, new_scheduled_content_count):
                            continue
                    elif self.frontier.family_is_closed(f_key):
                        continue
                    next_link = select_semantic_next(
                        links,
                        current_ordinal=current_ordinal if f_key == current_family_key else None,
                    )
                    if next_link is None:
                        continue
                    cand = Candidate(
                        url=next_link.url,
                        source_mode="recursive",
                        discovered_from=response.url,
                        kind="html",
                        purpose="page",
                        explicit=False,
                        discovery_source="html_link",
                        link_kind=next_link.kind,
                        family_key=next_link.family_key,
                        ordinal=next_link.ordinal,
                    )
                    req = self._candidate_request(cand)
                    if req is None and self.refresh is not None:
                        req = self._refresh_pagination_request(next_link.url)
                    if req is not None:
                        yield req
```

8. `on_request_error`:

```python
        if self.refresh is not None and request.meta.get("refresh_reason"):
            outcome = "access_gate" if isinstance(failure.value, IgnoreRequest) else "request_failed"
            self.refresh.finish_target(input_url, outcome, error=error_str[:500])
            return None
```

(chèn ngay sau dòng `error_name = getattr(failure.type, "__name__", "")`, trước khối `# 1. robots_disallowed`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q && uv run ruff check .`
Expected: tất cả PASS, ruff sạch.

- [ ] **Step 5: Commit**

```bash
git add src/hust_crawler/crawl/spider.py src/hust_crawler/crawl/middlewares.py tests/unit/crawl/test_refresh_spider.py tests/unit/crawl/test_middlewares.py
git commit -m "feat: add refresh mode to the unified spider"
```

---

### Task 7: Nối `--refresh` vào CLI và runner

**Files:**
- Modify: `src/hust_crawler/crawl/cli.py`
- Modify: `src/hust_crawler/crawl/runner.py`
- Test: `tests/unit/crawl/test_cli.py`, `tests/unit/crawl/test_runner.py`

**Interfaces:**
- Consumes: `preflight`, `prepare_refresh_run`, `complete_refresh_run`, `RefreshSession`, `RefreshSettings` (Task 4); `RefreshStore` (Task 2); spider kwargs (Task 6).
- Produces: `run_crawl(..., refresh: bool = False) -> int`; CLI `hust-crawl ... --refresh`.

- [ ] **Step 1: Write the failing tests**

`tests/unit/crawl/test_cli.py`:

```python
@pytest.mark.parametrize(
    "extra",
    [
        ["--resume"],
        ["--reuse-content-from", "other"],
        ["--retry-failed"],
        ["--retry-truncated"],
        ["--retry-access-gates"],
        ["--retry-policy-skips"],
    ],
)
def test_refresh_rejects_incompatible_flags(tmp_path: Path, extra: list[str], capsys) -> None:
    seeds = tmp_path / "seeds.txt"
    seeds.write_text("a.test\n", encoding="utf-8")

    code = cli.main(["--input", str(seeds), "--output", str(tmp_path / "out"), "--refresh", *extra])

    assert code == 2
    assert "--refresh cannot be combined" in capsys.readouterr().err
```

`tests/unit/crawl/test_runner.py`:

```python
def test_refresh_requires_existing_crawl(tmp_path: Path) -> None:
    seeds_file = tmp_path / "seeds.txt"
    seeds_file.write_text("a.test\n", encoding="utf-8")
    seeds = SeedSet(frozenset({"a.test"}), (), ())
    config = CrawlerConfig(hostnames=seeds.allowed_hostnames, contact="ops@example.com")

    with pytest.raises(ValueError, match="existing crawl"):
        run_crawl(
            input_path=seeds_file,
            output=tmp_path / "missing",
            config=config,
            seeds=seeds,
            options=CrawlOptions(),
            tuning=resolve_runtime_tuning(config=config, profile="safe-fast"),
            refresh=True,
        )
    assert not (tmp_path / "missing").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/crawl/test_cli.py tests/unit/crawl/test_runner.py -q`
Expected: FAIL (`unrecognized arguments: --refresh`, `unexpected keyword argument 'refresh'`).

- [ ] **Step 3: Implement**

`cli.py` — thêm argument (sau `--retry-policy-skips`):

```python
    parser.add_argument(
        "--refresh",
        action="store_true",
        default=False,
        help="Refetch hubs and recent pages of a finished crawl and crawl only new URLs",
    )
```

Trong `main`, ngay sau khối `parse_args` (trước mọi kiểm tra khác):

```python
    if args.refresh and (
        args.resume
        or args.reuse_content_from is not None
        or args.retry_failed
        or args.retry_truncated
        or args.retry_access_gates
        or args.retry_policy_skips
    ):
        print(
            "Error: --refresh cannot be combined with --resume, --reuse-content-from or --retry-* flags",
            file=sys.stderr,
        )
        return 2
```

và truyền `refresh=args.refresh` vào `run_crawl(...)`.

`runner.py`:

1. Import:

```python
from .refresh import (
    RefreshSession,
    RefreshSettings,
    complete_refresh_run,
    preflight,
    prepare_refresh_run,
)
from .refresh_store import RefreshStore
```

2. Thêm tham số `refresh: bool = False` vào cuối chữ ký `run_crawl`. Kiểm tra đầu tiên của hàm:

```python
    if refresh and (
        resume
        or reuse_content_from is not None
        or retry_failed
        or retry_truncated
        or retry_access_gates
        or retry_policy_skips
    ):
        raise ValueError(
            "--refresh cannot be combined with --resume, --reuse-content-from or --retry-* flags"
        )
```

3. Sau `state_dir = output / "state"` và khối `if resume: validate_resume_workflow(...)`:

```python
    continue_run_id: str | None = None
    if refresh:
        validate_resume_workflow(state_dir, expected="hybrid-unified")
        continue_run_id = preflight(state_dir)
```

4. `CrawlState.open(..., resume=resume or refresh, ...)`.

5. Ngay trước khối `try:` đầu tiên sau khi mở state, khai báo:

```python
    refresh_session: RefreshSession | None = None
    refresh_targets: list[tuple[str, str]] = []
```

và trong khối `try` đó, sau dòng `reused_targets = ...`:

```python
        if refresh:
            refresh_settings = RefreshSettings.from_config(config)
            refresh_store = RefreshStore(state.connection)
            run_id, refresh_targets = prepare_refresh_run(
                state,
                refresh_store,
                output,
                seeds.recursive_hostnames,
                refresh_settings,
                continue_run_id,
                now=time.time(),
            )
            refresh_session = RefreshSession(refresh_store, run_id, refresh_settings)
```

6. Frontier và khôi phục:

```python
        frontier = FrontierPolicy(
            seeds, options, route_families=() if refresh else state.iter_route_families()
        )
        resume_records: tuple[dict[str, object], ...] = ()

        if resume or refresh:
            ...  # thân khối `if resume:` hiện có giữ nguyên
```

7. `if resume: settings_map.pop("JOBDIR", None)` → `if resume or refresh:`.

8. `process.crawl(crawler, seeds=..., ...)` của `UnifiedSpider` thêm `refresh=refresh_session, refresh_targets=refresh_targets`.

9. Sau `truncated = sum(truncation_counts.values())`:

```python
        if refresh_session is not None:
            failed, truncated = refresh_session.store.failure_counts(
                refresh_session.run_id, TRUNCATION_REASONS
            )
```

10. Sau `writer.publish()` trong nhánh thành công:

```python
        refresh_summary = (
            complete_refresh_run(state, refresh_session, output, status)
            if refresh_session is not None
            else None
        )
```

và thêm `"refresh": refresh_summary,` vào dict `metrics`.

11. Trong nhánh `except KeyboardInterrupt:` và `except Exception as exc:`, ngay sau `writer.publish()`:

```python
        if refresh_session is not None:
            complete_refresh_run(state, refresh_session, output, "interrupted")
```

(nhánh `Exception` dùng `"failed"`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q && uv run ruff check .`
Expected: tất cả PASS.

- [ ] **Step 5: Commit**

```bash
git add src/hust_crawler/crawl/cli.py src/hust_crawler/crawl/runner.py tests/unit/crawl/test_cli.py tests/unit/crawl/test_runner.py
git commit -m "feat: wire --refresh into hust-crawl"
```

---

### Task 8: Kiểm thử tích hợp refresh end-to-end

**Files:**
- Create: `tests/fixtures/refresh_site_handler.py`
- Create: `tests/fixtures/run_refresh_fixture.py`
- Create: `tests/integration/test_refresh_crawl.py`

**Interfaces:**
- Consumes: CLI `--refresh` (Task 7).
- Produces: fixture site `news.test` có hai phiên bản và ETag.

- [ ] **Step 1: Write the fixtures**

`tests/fixtures/refresh_site_handler.py`:

```python
from __future__ import annotations

import hashlib
import time
from urllib.parse import urlsplit

from scrapy.http import HtmlResponse, Request, Response

RECENT = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - 3 * 86400))
OLD = "2020-01-01T08:00:00+07:00"


def article(slug: str, version: int, published: str, extra: str = "") -> str:
    return (
        f'<html><head><meta property="article:published_time" content="{published}"></head>'
        f"<body><main><h1>Bài {slug}</h1><p>Nội dung bài {slug} phiên bản {version}. "
        "Đây là đoạn văn bản đủ dài để trích xuất bài viết một cách ổn định.</p>"
        f"{extra}</main></body></html>"
    )


def listing(title: str, slugs: list[str], pages: list[int]) -> str:
    items = "".join(f'<li><a href="/bai/{slug}">Bài {slug}</a></li>' for slug in slugs)
    pager = "".join(
        f'<a href="/tin-tuc?page={number}">{number}</a>' if number > 1 else '<a href="/tin-tuc">1</a>'
        for number in pages
    )
    return (
        f"<html><body><main><h1>{title}</h1><p>Danh sách bài mới nhất của khoa: {', '.join(slugs)}.</p>"
        f"<ul>{items}</ul><nav>{pager}</nav></main></body></html>"
    )


HOME = (
    "<html><body><main><h1>Trang chủ</h1><p>Cổng thông tin tin tức của khoa.</p>"
    '<a href="/tin-tuc">Tin tức</a><a href="/gioi-thieu">Giới thiệu</a></main></body></html>'
)
ABOUT = (
    "<html><body><main><h1>Giới thiệu</h1>"
    "<p>Lịch sử hình thành và phát triển của khoa qua nhiều năm.</p></main></body></html>"
)
COMMON = {
    "/": HOME,
    "/gioi-thieu": ABOUT,
    **{f"/bai/a{n}": article(f"a{n}", 1, OLD) for n in range(1, 5)},
}
SITES = {
    1: {
        **COMMON,
        "/tin-tuc": listing("Tin tức", ["a5", "a4", "a3"], [2]),
        "/tin-tuc?page=2": listing("Tin tức trang 2", ["a2", "a1"], [1]),
        "/bai/a5": article("a5", 1, RECENT),
    },
    2: {
        **COMMON,
        "/tin-tuc": listing("Tin tức", ["n1", "a5", "a4"], [2]),
        "/tin-tuc?page=2": listing("Tin tức trang 2", ["n2", "a3", "a2"], [1, 3]),
        "/tin-tuc?page=3": listing("Tin tức trang 3", ["a1"], [2]),
        "/bai/a5": article("a5", 2, RECENT),
        "/bai/n1": article("n1", 1, RECENT, '<p><a href="/bai/n3">Bài liên quan n3</a></p>'),
        "/bai/n2": article("n2", 1, OLD),
        "/bai/n3": article("n3", 1, OLD),
    },
}


class RefreshSiteHandler:
    """Serve a versioned in-memory news site with ETag support."""

    lazy = True

    def __init__(self, settings, crawler=None):
        self.request_log = settings.get("FIXTURE_REQUEST_LOG")
        self.version = int(settings.get("FIXTURE_SITE_VERSION", 1))
        self.fail_paths = {p for p in str(settings.get("FIXTURE_FAIL_PATHS", "")).split(",") if p}

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler.settings, crawler)

    async def close(self):
        return None

    async def download_request(self, request: Request):
        if self.request_log:
            with open(self.request_log, "a", encoding="utf-8") as log:
                log.write(f"{request.url}\n")
        parts = urlsplit(request.url)
        key = parts.path + (f"?{parts.query}" if parts.query else "")
        if key in self.fail_paths:
            return HtmlResponse(
                request.url,
                status=500,
                headers={b"Content-Type": b"text/html"},
                body=b"<h1>Server error</h1>",
                encoding="utf-8",
                request=request,
            )
        body = SITES[self.version].get(key) if parts.hostname == "news.test" else None
        if body is None:
            return Response(request.url, status=404, body=b"", request=request)
        encoded = body.encode("utf-8")
        etag = '"' + hashlib.sha256(encoded).hexdigest()[:16] + '"'
        if request.headers.get(b"If-None-Match", b"").decode("latin1") == etag:
            return Response(request.url, status=304, headers={b"ETag": etag.encode()}, body=b"", request=request)
        return HtmlResponse(
            request.url,
            status=200,
            headers={b"Content-Type": b"text/html; charset=utf-8", b"ETag": etag.encode()},
            body=encoded,
            encoding="utf-8",
            request=request,
        )
```

`tests/fixtures/run_refresh_fixture.py`:

```python
from __future__ import annotations

from pathlib import Path
import sys

import yaml

sys.path.insert(0, str(Path(__file__).parents[2]))

from scrapy.utils.reactor import install_reactor

install_reactor("twisted.internet.asyncioreactor.AsyncioSelectorReactor")

from scrapy import signals  # noqa: E402
from hust_crawler.crawl import cli, runner  # noqa: E402
from hust_crawler.crawl.adaptive import AdaptiveConcurrencyMiddleware  # noqa: E402
import hust_crawler.crawl.settings as crawl_settings  # noqa: E402


class StopAfterResponse:
    target = "https://news.test/bai/n1"

    @classmethod
    def from_crawler(cls, crawler):
        extension = cls(crawler)
        crawler.signals.connect(extension.on_response, signal=signals.response_downloaded)
        return extension

    def __init__(self, crawler):
        self.crawler = crawler

    def on_response(self, response, **kwargs):
        if response.url == self.target:
            self.crawler.engine.close_spider(self.crawler.spider, "shutdown")


def main(root: Path, step: str, version: int, fail_paths: str = "") -> int:
    root.mkdir(parents=True, exist_ok=True)
    seeds_file = root / "seeds.txt"
    if not seeds_file.exists():
        seeds_file.write_text("news.test\n", encoding="utf-8")
    config_file = root / "config.yaml"
    if not config_file.exists():
        values = yaml.safe_load(Path("config/crawler.yaml").read_text(encoding="utf-8"))
        values.update(
            contact="ops@example.org", retry_backoff_base_seconds=0.01, download_delay_seconds=0.001
        )
        config_file.write_text(yaml.safe_dump(values), encoding="utf-8")
    request_log = root / "requested_urls.txt"
    with request_log.open("a", encoding="utf-8") as log:
        log.write(f"# {step}\n")

    original_build_settings = crawl_settings.build_settings

    def fixture_build_settings(cfg, *, state_dir, phase="crawl", tuning=None, extensions=None):
        values = original_build_settings(
            cfg, state_dir=state_dir, phase=phase, tuning=tuning, extensions=extensions
        )
        handler = "tests.fixtures.refresh_site_handler.RefreshSiteHandler"
        values["DOWNLOAD_HANDLERS"] = {"http": handler, "https": handler}
        values["FIXTURE_REQUEST_LOG"] = str(request_log)
        values["FIXTURE_SITE_VERSION"] = version
        values["FIXTURE_FAIL_PATHS"] = fail_paths
        values.setdefault("DOWNLOADER_MIDDLEWARES", {})[AdaptiveConcurrencyMiddleware] = None
        if step == "refresh_interrupt":
            values.setdefault("EXTENSIONS", {})[StopAfterResponse] = 50
        return values

    crawl_settings.build_settings = fixture_build_settings
    runner.build_settings = fixture_build_settings

    argv = [
        "--input", str(seeds_file),
        "--output", str(root / "crawl"),
        "--config", str(config_file),
        "--profile", "safe-fast",
        "--concurrency", "4",
        "--max-per-host", "2",
    ]
    if step != "initial":
        argv.append("--refresh")
    return cli.main(argv)


if __name__ == "__main__":
    raise SystemExit(
        main(Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]), sys.argv[4] if len(sys.argv) > 4 else "")
    )
```

- [ ] **Step 2: Write the integration tests**

`tests/integration/test_refresh_crawl.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import subprocess
import sys

URL = "https://news.test"


def run(root: Path, step: str, version: int, fail_paths: str = "") -> subprocess.CompletedProcess[str]:
    script = Path(__file__).parents[1] / "fixtures" / "run_refresh_fixture.py"
    return subprocess.run(
        [sys.executable, str(script), str(root), step, str(version), fail_paths],
        capture_output=True,
        text=True,
    )


def sessions(root: Path) -> list[list[str]]:
    result: list[list[str]] = []
    for line in (root / "requested_urls.txt").read_text(encoding="utf-8").splitlines():
        if line.startswith("# "):
            result.append([])
        elif line.strip():
            result[-1].append(line.strip())
    return result


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def run_dirs(root: Path) -> list[Path]:
    changes = root / "crawl" / "changes"
    if not changes.exists():
        return []
    return sorted(p for p in changes.iterdir() if p.is_dir() and not p.name.startswith("."))


def final_articles(root: Path) -> dict[str, dict[str, object]]:
    return {str(a["url"]): a for a in read_jsonl(root / "crawl" / "final" / "articles.jsonl")}


def test_refresh_fetches_hubs_and_new_pages_only(tmp_path: Path) -> None:
    initial = run(tmp_path, "initial", 1)
    assert initial.returncode == 0, initial.stderr
    assert f"{URL}/bai/a1" in final_articles(tmp_path)

    refresh = run(tmp_path, "refresh", 2)
    assert refresh.returncode == 0, refresh.stderr

    requested = set(sessions(tmp_path)[1])
    untouched = {f"{URL}/gioi-thieu", *(f"{URL}/bai/a{n}" for n in range(1, 5))}
    assert not requested & untouched
    [run_dir] = run_dirs(tmp_path)
    changes = {str(c["url"]): str(c["change"]) for c in read_jsonl(run_dir / "changes.jsonl")}
    assert {u for u, c in changes.items() if c == "new"} == {
        f"{URL}/bai/n1", f"{URL}/bai/n2", f"{URL}/bai/n3", f"{URL}/tin-tuc?page=3",
    }
    assert {u for u, c in changes.items() if c == "modified"} == {
        f"{URL}/bai/a5", f"{URL}/tin-tuc", f"{URL}/tin-tuc?page=2",
    }
    assert {str(a["url"]) for a in read_jsonl(run_dir / "articles.jsonl")} == set(changes)
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["outcomes"]["not_modified"] == 1
    final = final_articles(tmp_path)
    assert untouched | {f"{URL}/bai/n3"} <= set(final)
    assert "phiên bản 2" in str(final[f"{URL}/bai/a5"]["text"])
    readme = json.loads((tmp_path / "crawl" / "final" / "README.json").read_text(encoding="utf-8"))
    assert readme["last_refresh_run_id"] == run_dir.name == summary["run_id"]
    assert readme["crawl_id"] == summary["crawl_id"]


def test_second_refresh_without_site_changes_is_empty(tmp_path: Path) -> None:
    assert run(tmp_path, "initial", 1).returncode == 0
    assert run(tmp_path, "refresh", 2).returncode == 0
    second = run(tmp_path, "refresh", 2)
    assert second.returncode == 0, second.stderr

    latest = run_dirs(tmp_path)[-1]
    assert len(run_dirs(tmp_path)) == 2
    assert read_jsonl(latest / "changes.jsonl") == []
    summary = json.loads((latest / "summary.json").read_text(encoding="utf-8"))
    assert set(summary["outcomes"]) == {"not_modified"}


def test_failed_hub_refetch_keeps_previous_article(tmp_path: Path) -> None:
    assert run(tmp_path, "initial", 1).returncode == 0
    refresh = run(tmp_path, "refresh", 2, "/tin-tuc")

    assert refresh.returncode == 3, refresh.stderr
    text = str(final_articles(tmp_path)[f"{URL}/tin-tuc"]["text"])
    assert "a3" in text and "n1" not in text
    [run_dir] = run_dirs(tmp_path)
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert set(summary["outcomes"]) & {"http_500", "request_failed"}


def test_interrupted_refresh_continues_the_same_run(tmp_path: Path) -> None:
    assert run(tmp_path, "initial", 1).returncode == 0
    interrupted = run(tmp_path, "refresh_interrupt", 2)
    assert interrupted.returncode == 130, interrupted.stderr
    assert run_dirs(tmp_path) == []
    connection = sqlite3.connect(tmp_path / "crawl" / "state" / "index.sqlite3")
    run_id, status = connection.execute("SELECT run_id, status FROM refresh_runs").fetchone()
    done_before = {row[0] for row in connection.execute("SELECT url FROM refresh_targets WHERE done = 1")}
    connection.close()
    assert status == "interrupted"

    resumed = run(tmp_path, "refresh", 2)
    assert resumed.returncode == 0, resumed.stderr

    [run_dir] = run_dirs(tmp_path)
    assert run_dir.name == run_id
    assert not done_before & set(sessions(tmp_path)[2])
    changes = {str(c["url"]) for c in read_jsonl(run_dir / "changes.jsonl")}
    assert {f"{URL}/bai/n1", f"{URL}/bai/n2", f"{URL}/bai/n3"} <= changes
```

- [ ] **Step 3: Run the integration tests**

Run: `uv run pytest tests/integration/test_refresh_crawl.py -q`
Expected: PASS. Nếu `test_refresh_fetches_hubs_and_new_pages_only` fail ở assert sanity `bai/a1` (crawl đầu không đi sang trang 2), đó là lỗi fixture chứ không phải refresh: in `final_articles` và `url_records.jsonl` để xem link `?page=2` được phân loại thế nào, rồi sửa HTML fixture (ví dụ thêm `rel="next"` cho link trang) thay vì sửa logic phân trang của crawler. Mọi thất bại khác là lỗi triển khai của Task 2–7: sửa ở task tương ứng.

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q && uv run ruff check .`
Expected: tất cả PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/refresh_site_handler.py tests/fixtures/run_refresh_fixture.py tests/integration/test_refresh_crawl.py
git commit -m "test: cover refresh crawl end to end"
```

---

### Task 9: `ir_data` — nhận nguồn đã refresh và inventory theo nguồn

**Files:**
- Modify: `src/ir_data/state.py` (`WorkState.open`, `_init_schema`, `cursor`, `add_inventory_batch`, `inventory_done`; thêm `read_crawl_id`, `migrate_legacy_cursor`, `delete_refs_from`)
- Modify: `src/ir_data/inventory.py` (`index_inventory`; thêm `source_key`, `_modified_pages`)
- Modify: `src/ir_data/cli.py`
- Test: `tests/corpus/test_inventory.py` (file mới)

**Interfaces:**
- Consumes: `README.json`/`summary.json` có `crawl_id` (Task 3/4); `changes.jsonl` (Task 4).
- Produces:
  - `read_crawl_id(source: Path) -> str | None`
  - `WorkState.open(root, source_fingerprint, *, crawl_id: str | None = None, rebind: bool = False)`
  - `WorkState.cursor(source_key: str) -> int`, `add_inventory_batch(candidates, refs, cursor, done=False, *, source_key: str)`, `inventory_done(source_key: str) -> bool`, `migrate_legacy_cursor(source_key: str) -> None`, `delete_refs_from(source_urls: Iterable[str]) -> None`
  - `inventory.source_key(source: Path) -> str`
  - CLI: cờ `--rebind-source` trên `inventory`, `download`, `extract`, `enrich-documents`, `status`, `export`, `build-graph`, `package`.

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ir_data.inventory import index_inventory, source_key
from ir_data.state import WorkState, fingerprint_source, read_crawl_id


def write_source(folder: Path, rows: list[dict[str, object]], *, crawl_id: str | None = "c1") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "articles.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    if crawl_id is not None:
        (folder / "README.json").write_text(json.dumps({"crawl_id": crawl_id}), encoding="utf-8")
    return folder


def page(url: str, *links: str) -> dict[str, object]:
    return {"url": url, "links": [{"url": link, "text": link} for link in links], "assets": []}


def refs(state: WorkState) -> set[tuple[str, str]]:
    return {(r[0], r[1]) for r in state.conn.execute("SELECT source_url, target_url FROM refs")}


def test_read_crawl_id_from_readme_or_summary(tmp_path: Path) -> None:
    final = write_source(tmp_path / "final", [], crawl_id="c1")
    changes = write_source(tmp_path / "changes", [], crawl_id=None)
    (changes / "summary.json").write_text(json.dumps({"crawl_id": "c1"}), encoding="utf-8")

    assert read_crawl_id(final) == "c1"
    assert read_crawl_id(changes) == "c1"
    assert read_crawl_id(write_source(tmp_path / "plain", [], crawl_id=None)) is None


def test_work_dir_accepts_refreshed_source_of_same_crawl(tmp_path: Path) -> None:
    source = write_source(tmp_path / "final", [page("https://a.test/")])
    with WorkState.open(tmp_path / "work", fingerprint_source(source), crawl_id="c1"):
        pass
    write_source(source, [page("https://a.test/"), page("https://a.test/new")])

    with WorkState.open(tmp_path / "work", fingerprint_source(source), crawl_id="c1"):
        pass
    with pytest.raises(ValueError, match="--rebind-source"):
        with WorkState.open(tmp_path / "work", "other", crawl_id="c2"):
            pass


def test_rebind_adopts_legacy_work_dir(tmp_path: Path) -> None:
    with WorkState.open(tmp_path / "work", "old-fingerprint"):
        pass
    with pytest.raises(ValueError, match="source fingerprint changed"):
        with WorkState.open(tmp_path / "work", "new-fingerprint", crawl_id="c1"):
            pass

    with WorkState.open(tmp_path / "work", "new-fingerprint", crawl_id="c1", rebind=True):
        pass
    with WorkState.open(tmp_path / "work", "newer-fingerprint", crawl_id="c1"):
        pass


def test_inventory_keeps_a_cursor_per_source(tmp_path: Path) -> None:
    final = write_source(tmp_path / "final", [page("https://a.test/", "https://a.test/f.pdf")])
    changes = write_source(tmp_path / "changes" / "R", [page("https://a.test/new", "https://a.test/g.pdf")])
    with WorkState.open(tmp_path / "work", "fp", crawl_id="c1") as state:
        index_inventory(final, state)
        index_inventory(changes, state)
        index_inventory(final, state)

        assert state.inventory_done(source_key(final))
        assert state.inventory_done(source_key(changes))
        urls = {row[0] for row in state.conn.execute("SELECT url FROM inventory")}
        assert urls == {"https://a.test/f.pdf", "https://a.test/g.pdf"}


def test_modified_page_refs_are_replaced(tmp_path: Path) -> None:
    final = write_source(tmp_path / "final", [page("https://a.test/hub", "https://a.test/old")])
    changes = write_source(tmp_path / "changes" / "R", [page("https://a.test/hub", "https://a.test/new")])
    (changes / "changes.jsonl").write_text(
        json.dumps({"url": "https://a.test/hub", "change": "modified"}) + "\n", encoding="utf-8"
    )
    with WorkState.open(tmp_path / "work", "fp", crawl_id="c1") as state:
        index_inventory(final, state)
        index_inventory(changes, state)

        assert refs(state) == {("https://a.test/hub", "https://a.test/new")}


def test_resumed_changes_inventory_does_not_delete_refs_again(tmp_path: Path) -> None:
    changes = write_source(
        tmp_path / "changes" / "R",
        [page("https://a.test/hub", "https://a.test/new")],
    )
    (changes / "changes.jsonl").write_text(
        json.dumps({"url": "https://a.test/hub", "change": "modified"}) + "\n", encoding="utf-8"
    )
    with WorkState.open(tmp_path / "work", "fp", crawl_id="c1") as state:
        key = source_key(changes)
        size = (changes / "articles.jsonl").stat().st_size
        state.add_inventory_batch([], [], size, source_key=key)
        state.conn.execute(
            "INSERT INTO refs(source_url,target_url,relation) VALUES(?,?,?)",
            ("https://a.test/hub", "https://a.test/kept", "hyperlink"),
        )
        state.conn.commit()

        index_inventory(changes, state)

        assert ("https://a.test/hub", "https://a.test/kept") in refs(state)


def test_legacy_cursor_moves_to_default_source(tmp_path: Path) -> None:
    with WorkState.open(tmp_path / "work", "fp") as state:
        state.conn.execute("UPDATE inventory_cursor SET cursor=42, done=1 WHERE id=1")
        state.conn.commit()
        state.migrate_legacy_cursor("/data/crawl/final/articles.jsonl")
        state.migrate_legacy_cursor("/data/crawl/final/articles.jsonl")

        assert state.cursor("/data/crawl/final/articles.jsonl") == 42
        assert state.inventory_done("/data/crawl/final/articles.jsonl")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/corpus/test_inventory.py -q`
Expected: FAIL (`ImportError: cannot import name 'source_key'` / `read_crawl_id`).

- [ ] **Step 3: Implement**

`state.py`:

1. `from typing import Iterable, Iterator` (thay dòng `from typing import Iterator`).
2. Hàm module (sau `fingerprint_source`):

```python
def read_crawl_id(source: Path) -> str | None:
    folder = source if source.is_dir() else source.parent
    for name in ("README.json", "summary.json"):
        path = folder / name
        if not path.exists():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8")).get("crawl_id")
        except (json.JSONDecodeError, AttributeError):
            continue
        if value:
            return str(value)
    return None
```

3. `WorkState.open`:

```python
    def open(
        cls,
        root: Path,
        source_fingerprint: str,
        *,
        crawl_id: str | None = None,
        rebind: bool = False,
    ) -> Iterator["WorkState"]:
```

thay khối kiểm tra fingerprint:

```python
        old = obj._meta("source_fingerprint")
        same_crawl = crawl_id is not None and obj._meta("crawl_id") == crawl_id
        if old and old != source_fingerprint and not (same_crawl or rebind):
            obj.close()
            raise ValueError(
                "source fingerprint changed; use a new work directory, "
                "or pass --rebind-source if the source is a refresh of the same crawl"
            )
        obj._set_meta("source_fingerprint", source_fingerprint)
        if crawl_id is not None:
            obj._set_meta("crawl_id", crawl_id)
```

4. `_init_schema`, thêm sau dòng `INSERT OR IGNORE INTO inventory_cursor(id) VALUES (1);`:

```sql
        CREATE TABLE IF NOT EXISTS inventory_sources (path TEXT PRIMARY KEY, cursor INTEGER NOT NULL DEFAULT 0, done INTEGER NOT NULL DEFAULT 0);
```

5. Thay `cursor`, phần cập nhật con trỏ trong `add_inventory_batch`, và `inventory_done`:

```python
    def cursor(self, source_key: str) -> int:
        row = self.conn.execute(
            "SELECT cursor FROM inventory_sources WHERE path=?", (source_key,)
        ).fetchone()
        return int(row[0]) if row else 0
```

```python
    def add_inventory_batch(
        self,
        candidates: list[Candidate],
        refs: list[SourceRef],
        cursor: int,
        done: bool = False,
        *,
        source_key: str,
    ) -> None:
        ...  # hai executemany giữ nguyên
            self.conn.execute(
                "INSERT INTO inventory_sources(path,cursor,done) VALUES(?,?,?) "
                "ON CONFLICT(path) DO UPDATE SET cursor=excluded.cursor,done=excluded.done",
                (source_key, cursor, int(done)),
            )
```

```python
    def inventory_done(self, source_key: str) -> bool:
        row = self.conn.execute(
            "SELECT done FROM inventory_sources WHERE path=?", (source_key,)
        ).fetchone()
        return bool(row and row[0])

    def migrate_legacy_cursor(self, source_key: str) -> None:
        if self.conn.execute("SELECT 1 FROM inventory_sources LIMIT 1").fetchone():
            return
        row = self.conn.execute("SELECT cursor, done FROM inventory_cursor WHERE id=1").fetchone()
        if row and (row[0] or row[1]):
            with self.conn:
                self.conn.execute(
                    "INSERT INTO inventory_sources(path,cursor,done) VALUES(?,?,?)",
                    (source_key, row[0], row[1]),
                )

    def delete_refs_from(self, source_urls: Iterable[str]) -> None:
        with self.conn:
            self.conn.executemany(
                "DELETE FROM refs WHERE source_url=?", [(url,) for url in source_urls]
            )
```

`inventory.py`:

```python
def source_key(source: Path) -> str:
    articles = source / "articles.jsonl" if source.is_dir() else source
    return str(articles.resolve())


def _modified_pages(changes_path: Path) -> set[str]:
    if not changes_path.exists():
        return set()
    pages: set[str] = set()
    for line in changes_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        change = json.loads(line)
        if change.get("change") == "modified" and change.get("url"):
            pages.add(_clean(str(change["url"])))
    return pages
```

Trong `index_inventory`, thay `offset = state.cursor()` bằng:

```python
    key = source_key(source)
    offset = state.cursor(key)
    if offset == 0 and not state.inventory_done(key):
        modified = _modified_pages(articles.parent / "changes.jsonl")
        if modified:
            state.delete_refs_from(modified)
```

và thêm `source_key=key` vào cả hai lời gọi `state.add_inventory_batch(...)`.

`cli.py`:

1. Import `from .inventory import index_inventory, source_key` và `from .state import WorkState, fingerprint_source, read_crawl_id`.
2. Trong vòng `for name in ("inventory", ...)` và cho các subparser `export`, `build-graph`, `package`, thêm:

```python
        q.add_argument(
            "--rebind-source",
            action="store_true",
            help="accept a changed source for this work directory (once, after upgrading)",
        )
```

(với `export`/`build-graph`/`package`, thêm cùng lời gọi ngay sau `--output`.)

3. Mở work dir:

```python
        fp = fingerprint_source(source)
        with WorkState.open(
            work, fp, crawl_id=read_crawl_id(source), rebind=args.rebind_source
        ) as state:
            state.recover()
            state.migrate_legacy_cursor(
                source_key(settings.project_root / "data" / "crawl" / "final")
            )
```

4. `enrich-documents`: `if not state.inventory_done(source_key(source)):`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q && uv run ruff check .`
Expected: tất cả PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ir_data/state.py src/ir_data/inventory.py src/ir_data/cli.py tests/corpus/test_inventory.py
git commit -m "feat: accept refreshed crawl sources in enrichment"
```

---

### Task 10: Tài liệu vận hành

**Files:**
- Modify: `data_pipeline/README.md`
- Modify: `data_pipeline/docs/document-pipeline.md`

- [ ] **Step 1: README — thêm mục ngay sau mục "Interruption and Resume"**

````markdown
### Incremental Refresh

After a crawl has finished, refresh it in place instead of crawling again:

```bash
uv run hust-crawl --input docs/domain_active.txt --output data/crawl --refresh
```

A refresh refetches only hub pages (homepages, navigation pages, the first page
of each paginated listing, successful sitemaps and their robots.txt) plus recent
articles: those published or modified within `refresh_recent_days`, and those
first found in the last `refresh_recent_runs` refreshes, capped at
`refresh_recheck_limit`. Links to URLs already in the crawl index are not
fetched; new URLs are crawled and expanded normally. A listing advances to its
next page only while the current page shows a new link
(`refresh_pagination_empty_pages`). Stored `ETag`/`Last-Modified` values are
sent as conditional headers, so unchanged hubs answer `304`.

Before a new run, the index is copied to `archive/pre-refresh-<run_id>/`.
Existing articles are never removed: a hub or article that now fails, redirects
away or looks like a soft 404 keeps its previous content, and the failure is
recorded only for that run. `--refresh` cannot be combined with `--resume`,
`--reuse-content-from` or any `--retry-*` flag. If a refresh is interrupted,
run the same command again; it continues the same run.

Each finished run writes `changes/<run_id>/`:

- `changes.jsonl`: one line per URL with `change` (`new` or `modified`),
  `page_role`, `content_id` and, for modified pages, `previous_content_id`.
- `articles.jsonl`: the full records of those URLs, same schema as
  `final/articles.jsonl`.
- `summary.json`: target counts per reason, outcomes (`not_modified`,
  `unchanged`, `modified`, `http_500`, ...), new and modified counts, and the
  `crawl_id`.

`final/` is rebuilt as usual; `final/README.json` records `crawl_id` and
`last_refresh_run_id`. The four `refresh_*` settings in `config/crawler.yaml`
are runtime settings and do not affect resume compatibility.

Update the corpus after a refresh:

```bash
uv run ir-data inventory --source data/crawl/changes/<run_id> --work-dir data/enrichment
uv run ir-data download --work-dir data/enrichment
uv run ir-data extract --work-dir data/enrichment
uv run ir-data package --source data/crawl/final --work-dir data/enrichment --output data/corpus/latest
uv run ir-data import-mongo --corpus data/corpus/latest
```

A work directory created before this feature must accept the refreshed source
once with `--rebind-source` (for example
`uv run ir-data status --rebind-source`); later refreshes of the same crawl are
accepted automatically through `crawl_id`.
````

- [ ] **Step 2: `docs/document-pipeline.md` — thêm vào cuối**

```markdown
Inventory keeps one cursor per source file, so a refreshed crawl can be added
with `ir-data inventory --source data/crawl/changes/<run_id>`. For pages marked
`modified` in that folder's `changes.jsonl`, the previous outgoing references
are replaced by the new ones. A work directory is bound to one crawl through
the `crawl_id` in the source's `README.json` or `summary.json`; refreshed
sources of the same crawl are accepted, and `--rebind-source` adopts a changed
source explicitly.
```

- [ ] **Step 3: Commit**

```bash
git add README.md docs/document-pipeline.md
git commit -m "docs: document incremental refresh crawl"
```

---

## Sau khi hoàn thành

- `uv run pytest -q` và `uv run ruff check .` sạch trên toàn bộ dự án.
- Chưa chạy refresh trên `data/crawl` thật trong plan này. Lần chạy thật đầu tiên do người vận hành thực hiện, theo thứ tự:
  1. `uv run ir-data status --rebind-source` trên work dir hiện có;
  2. `hust-crawl ... --refresh`;
  3. đối chiếu `changes/<run_id>/summary.json` (số target theo reason, thời gian chạy) trước khi publish Kaggle.
