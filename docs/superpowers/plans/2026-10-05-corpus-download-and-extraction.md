# Document Download and Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bổ sung tài liệu từ corpus hiện có với download nhanh, resume bền vững, parser local và LlamaParse tiết kiệm.

**Architecture:** Inventory/checkpoint SQLite riêng cho enrichment; downloader streaming có kiểm chứng HTTP Range và hàng đợi theo host. Parser có cache theo nội dung/cấu hình, lưu kết quả theo từng trang/sheet/slide; cloud chỉ xử lý phần không ra chữ.

**Tech Stack:** Python 3.13, uv, SQLite, aiohttp, PyMuPDF, openpyxl, xlrd, python-pptx, LibreOffice, llama-cloud, pytest.

**Spec:** [Design](../specs/2026-10-05-kaggle-mongodb-corpus-design.md). [Master plan và CLI](2026-10-05-corpus-pipeline.md).

## Global Constraints

- Giữ nguyên `crawler/data/crawl/`; không request lại HTML để lấy inventory.
- Download: 64 toàn cục, 2 mỗi host, 0,25 giây giữa lần bắt đầu cùng host; max file `104857600`, max storage `107374182400` bytes; resource threshold 80%.
- Request timeout đọc 60 giây; retry tối đa 3 sau lần đầu; redirect tối đa 20. Tuning được lưu trong `config/data.yaml` và cho override CLI.
- Chỉ nối `.part` có strong ETag khi response Range xác nhận cùng bytes nguồn. Server không hỗ trợ thì restart riêng file đó.
- PDF local PyMuPDF; XLSX/openpyxl, XLS/xlrd, PPTX/python-pptx. Không OCR local.
- Chỉ LlamaParse `fast`/`latest` khi không ra chữ/số hữu ích; không nâng tier. Key quota fallback theo thứ tự số trong `.env`.
- Mọi giới hạn/chưa hoàn tất phải xuất trạng thái; không âm thầm bỏ tài liệu hoặc tính phí lại sau timeout.
- Consumers chỉ pull/import không cần producer extras hoặc LibreOffice.

## Review Focus

1. Rename đã xong nhưng DB chưa commit — A2/A3/A8.
2. Server trả 200 thay vì 206, đổi ETag hoặc content encoding — A3.
3. Một host 429 nhiều lần làm nghẽn tất cả host còn lại — A4.
4. Số 0, công thức thiếu cache và text trong group bị coi là rỗng — A6.
5. Job API chưa rõ kết quả hoặc quota cả ba key hết — A7/A8.

## File map và hợp đồng dùng chung

`scripts/ir_data/models.py` định nghĩa dataclass có serialization JSON:

- `SourceRef(source_url: str, target_url: str, relation: str, anchor_text: str, alt: str, caption: str)`.
- `Candidate(url: str, mime_hint: str | None)`.
- `DownloadJob(url: str, part_path: Path, durable_offset: int, strong_etag: str | None, final_url: str | None, expected_size: int | None, attempts: int)`.
- `DownloadedFile(url: str, path: Path, sha256: str, size: int, mime_type: str, final_url: str)`.
- `ExtractedPart(index: int, kind: str, text: str, has_visual_content: bool, structured: list[dict], notes: str, parser: str)`; index bắt đầu từ 1, kind là page/sheet/slide.
- `LocalResult(source_sha256: str, parts: list[ExtractedPart], warnings: list[str], parser_version: str)`.
- `ParseResult(source_sha256: str, parts: list[ExtractedPart], status: str, warnings: list[str], parser_versions: dict[str, str])`.
- `RunSummary(downloaded: int, parsed: int, failed: int, pending: int, quota_blocked: int, transferred_bytes: int, exit_code: int)`.
- `InventorySummary(candidates: int, referrers: int, completed: bool)`.

State store giữ riêng `download_status` và `parse_status`; exporter mới quy về
`extraction_status` của corpus. Hai tiến trình không cùng sửa một work-dir.
File khác nhau nhưng cùng SHA và cùng cấu hình dùng chung parse cache.

## A1 — Package công cụ, cấu hình và hợp đồng dữ liệu

**Files:** Create root `pyproject.toml`, `uv.lock`, `scripts/data.py`,
`scripts/ir_data/{__init__,models,settings,cli}.py`, `config/data.yaml`,
`.env.example`, `tests/corpus/test_settings.py`; modify `.gitignore`.

**Interfaces:** `load_settings(repo_root: Path, overrides: dict) -> Settings`;
`load_api_keys(repo_root: Path) -> list[tuple[str, str]]`; `main(argv: list[str] | None = None) -> int`.
`Settings` chứa work/source paths, tuning trong Global Constraints, Mongo URI
và parser settings; secrets không nằm trong repr/serialization của Settings.

- [ ] Viết tests: `test_defaults_match_crawler` assert concurrency 64/per_host 2/max_file_bytes 104857600; `test_env_loaded_from_root_not_cwd` assert cùng keys khi đổi cwd; `test_key_order_numeric_dedup_and_env_precedence` assert labels KEY2 đứng trước KEY10 và secret không có trong repr.
- [ ] Chạy `uv run --extra dev pytest tests/corpus/test_settings.py -q`, xác nhận FAIL vì module/hành vi chưa có.
- [ ] Tạo package `ir-corpus`, wheel `scripts/ir_data`, entry `ir-data`; base dependencies PyMongo, Kaggle CLI, python-dotenv, PyYAML. Extra producer: aiohttp, PyMuPDF, openpyxl, xlrd, python-pptx, llama-cloud, filelock và local `hust-public-crawler` qua uv path source `crawler`; dev: pytest, pytest-asyncio, Ruff. Khóa các bản tương thích Python 3.13 bằng uv; consumer không cài extra producer.
- [ ] Implement interfaces và validation positive integers, per_host ≤ global, repo-relative paths. Load dotenv không override biến đã export; tên KEY được sort số và bỏ secret trùng; `.env.example` chỉ có chỗ trống. Ignore generated `/data/` nhưng giữ data fixtures tracked của `lucene/`.
- [ ] Chạy test trên và `uv run ir-data --help`; help base chạy được khi producer deps không cài. Ghi README setup tối thiểu ở task cần nó; chưa thêm subcommand giả hoạt động.
- [ ] Commit `feat: add corpus tool configuration and data contracts` với đúng files A1, không stage `.env`/skills người dùng.

## A2 — Inventory và checkpoint SQLite bền vững

**Files:** Create `scripts/ir_data/{inventory,state}.py`,
`tests/corpus/{test_inventory,test_state}.py`, `tests/corpus/fixtures/articles.jsonl`.

**Interfaces:** `index_inventory(source: Path, state: WorkState) -> InventorySummary`;
`WorkState.open(root: Path, source_fingerprint: str) -> WorkState` (context manager có file lock);
`add_inventory_batch(candidates: list[Candidate], refs: list[SourceRef], cursor: int) -> None`;
`claim_download() -> DownloadJob | None`; `checkpoint_download(job: DownloadJob) -> None`;
`mark_finalizing(file: DownloadedFile) -> None`; `mark_downloaded(file: DownloadedFile) -> None`;
`pending_parse() -> Iterator[DownloadedFile]`; `mark_parse(url: str, result: ParseResult, cache_path: Path) -> None`;
`record_failure(url: str, stage: str, reason: str, retry_at: float | None) -> None`;
`recover() -> None`; `summary() -> RunSummary`.

- [ ] Viết tests với fixture cùng PDF được hai trang giới thiệu: assert 1 candidate và đủ 2 referrers/anchor text; ảnh không vào hàng đợi tải. `test_inventory_resume_cursor_atomic` assert crash không mất batch/cursor; `test_changed_source_rejected` assert fingerprint khác bị từ chối; `test_second_writer_rejected` assert không có hai writer.
- [ ] Chạy `uv run --extra producer --extra dev pytest tests/corpus/test_inventory.py tests/corpus/test_state.py -q`, xác nhận FAIL.
- [ ] Tạo SQLite WAL, schema version và bảng inventory/downloads/referrers/parse_results/cloud_jobs/run_metadata. Fingerprint gồm SHA-256 inputs; hash nguồn trước quét và xác nhận nguồn không đổi khi hoàn tất. Cursor là byte offset ngay sau dòng JSONL, transaction batch cùng cursor; batch mặc định 500. Không nạp corpus vào RAM.
- [ ] Tái sử dụng chuẩn hóa URL/nhận diện tài liệu từ `hust_crawler.crawl.assets` và `policies.network`. Giữ raw source URL cho provenance. Thu candidates từ assets document và links nhận diện được; URL không có bằng chứng loại giữ ở graph, không tải mọi hyperlink HTML. `recover` hạ in-flight về pending; chuẩn bị xử lý finalizing ở A3, không xóa file đã tốt.
- [ ] Chạy lại tests; kiểm tra source fixture có checksum không đổi và DB không chứa bí mật. Thêm CLI `inventory`, `status` dùng cùng state; test chạy lại không tăng số job.
- [ ] Commit `feat: persist document inventory and enrichment checkpoints`.

## A3 — Download streaming và resume byte đúng nội dung

**Files:** Create `scripts/ir_data/{http_transport,download,artifact_store}.py`,
`tests/corpus/{conftest,test_download_resume,test_download_recovery}.py`.

**Interfaces:** `PublicTransport` async context manager dùng aiohttp;
`download_one(job: DownloadJob, state: WorkState, settings: Settings, transport: PublicTransport) -> DownloadedFile`;
`verify_file(file: DownloadedFile) -> bool`; `recover_finalizing(state: WorkState) -> None`.
Transport contract trả status/headers/final URL và async iterator bytes; được
inject trong tests. `RetryableDownloadError(retry_after: float | None)` khác
`PermanentDownloadError(reason: str)`; scheduler A4 xử lý retry, không lồng retry ở A3.

- [ ] Tạo local HTTP fixture có route trả strong ETag/Range, route bỏ qua Range, ETag đổi, 416, stream bị cắt, 404, HTML giả PDF và redirect. `test_resume_206` assert request thứ hai có offset đã checkpoint và SHA cuối đúng; `test_200_never_appends` assert không có bytes lặp; `test_changed_etag_restarts_only_one_file` assert file hoàn tất khác không được request lại.
- [ ] Chạy `uv run --extra producer --extra dev pytest tests/corpus/test_download_resume.py tests/corpus/test_download_recovery.py -q`, xác nhận FAIL.
- [ ] Implement transport pooled sessions, TLS verify, redirect thủ công. Reuse URL policy; resolver của connector lọc địa chỉ thực được dùng để kết nối, không chỉ kiểm DNS rồi resolve lại không kiểm. Test resolver được inject để dùng fixture localhost; production không có cờ tắt public-only. Request header `Accept-Encoding: identity`, streaming 256 KiB/chunk, checkpoint fsync mỗi 1 MiB hoặc 1 giây. Nếu server vẫn content-encode, không nối offset giữa encoded/decoded representations; restart không-resume có decode rõ ràng hoặc báo unsupported encoding.
- [ ] Implement `.part` theo hash URL, truncate về durable offset sau crash; Range chỉ với strong ETag, Content-Range bắt đầu đúng offset và total hợp lệ. 200/đổi validator/missing validator restart file; 416 chỉ finalize khi validator/known length/chunks đã ghi đầy đủ khớp, không khớp thì một lần fresh GET hoặc lỗi hữu hạn. Kiểm tra content length/truncation, MIME bằng header và signature/container; không lưu trang lỗi HTML như tài liệu.
- [ ] Finalize theo intent DB → atomic rename cùng filesystem → DB downloaded; fsync parent directory nơi hỗ trợ. Tên lưu SHA-256 + đuôi xác định từ MIME, không dùng path server cấp. Thêm tests kill tại từng bước finalize, corrupted completed blob, path traversal trong filename, hai URL cùng bytes; assert recovered file đúng hash và không nhân đôi storage.
- [ ] Chạy lại toàn bộ tests A3; assert peak in-flight buffer có bound theo chunk, không body-read toàn file. Commit `feat: stream and resume document downloads safely`.

## A4 — Scheduler song song, backoff và shutdown

**Files:** Create `scripts/ir_data/{scheduler,progress}.py`,
`tests/corpus/{test_scheduler,test_download_interrupt}.py`; modify `cli.py`, `state.py`.

**Interfaces:** `async run_downloads(state: WorkState, settings: Settings, *, transport_factory: Callable, stop: asyncio.Event) -> RunSummary`;
`progress_snapshot(state: WorkState, now: float) -> dict`.
Scheduler gọi `download_one`, kiểm tra completed artifacts bằng `verify_file`,
và lưu errors/retry_at qua WorkState. Chỉ scheduler sở hữu retry/network slots.

- [ ] Viết `test_concurrency_and_host_limits`: active_total ≤ 64, active_host ≤ 2 và hai host có overlap thực; `test_slow_host_does_not_starve_others`: host B hoàn tất trong khi A cooldown; `test_retry_after_persists_restart`: không retry sớm sau reopen; `test_completed_files_make_zero_requests`.
- [ ] Chạy `uv run --extra producer --extra dev pytest tests/corpus/test_scheduler.py tests/corpus/test_download_interrupt.py -q`, xác nhận FAIL.
- [ ] Implement bounded task queue, pooled connections, fair selection theo host. Không giữ global slot khi chờ retry_at/per-host delay. Retry các status trong spec, exponential backoff base 2 giây + jitter; honor Retry-After; max retry 3. Không retry 404/410 tự động. `--retry-failed` chỉ requeue failed do thao tác chủ động.
- [ ] Đặt storage budget semaphore/atomic accounting cho partial và final files; không để workers vượt quota do race. Dùng `ResourceSampler` hiện có để giảm active target khi CPU/RAM >80%, tăng lại tối đa config khi hạ; không kill transfer đang ghi. Stop nhận jobs khi disk full/quota, giữ checkpoint. Signal handler set stop, drain/cancel có kiểm soát, không bỏ durable bytes.
- [ ] Kiểm thử subprocess SIGTERM giữa hai transfers, restart và assert checksum/zero requests completed. Dùng fake clock/barrier cho concurrency/backoff thay vì test dựa vào sleep dài. Test dung lượng còn thấp và mất mạng; status hiển thị bytes transferred thực, file cached riêng, pending/retry/failed. Chạy lại tests và CLI `download/status` fixture.
- [ ] Commit `feat: schedule concurrent document downloads with resumable shutdown`.

## A5 — PyMuPDF và điều kiện fallback tối thiểu

**Files:** Create `scripts/ir_data/parsers/{__init__,routing,pdf}.py`,
`scripts/ir_data/parse_cache.py`, `tests/corpus/{test_pdf_parser,test_parse_cache}.py`;
create generated fixture helpers trong `tests/corpus/fixtures/build_documents.py`.

**Interfaces:** `parse_pdf(file: DownloadedFile) -> LocalResult`;
`has_useful_text(text: str) -> bool`; `cloud_part_indices(result: LocalResult) -> list[int]`;
`build_pdf_subset(path: Path, indices: list[int], destination: Path) -> dict[int, int]`;
`cache_key(sha256: str, indices: list[int], parser: str, settings_digest: str) -> str`;
`read_cached(root: Path, key: str) -> ParseResult | None`; `write_cached(root: Path, key: str, result: ParseResult) -> None`.

- [ ] Viết tests generated PDF: 1 trang chỉ có “A”, 1 có số “0”, 1 bảng xấu, 1 scan text Việt, 1 trắng. Assert `cloud_part_indices` chỉ gồm trang scan; không lọc text ngắn. `test_corrupt_cache_is_not_reused` assert cache lỗi checksum bị từ chối; `test_cache_independent_of_key_label` assert đổi key không đổi cache identity.
- [ ] Chạy `uv run --extra producer --extra dev pytest tests/corpus/test_pdf_parser.py tests/corpus/test_parse_cache.py -q`, xác nhận FAIL.
- [ ] Dùng PyMuPDF text/blocks/sort, giữ Unicode nguyên nội dung sau normalize NFC cho đánh giá. `has_useful_text` nhận chữ/số Unicode, không ngưỡng ký tự. Visual-content probe xem image/drawing khi không có text; phân biệt trang trắng bằng rendered-page content khi cần. Không gọi `get_textpage_ocr`; không bật Tesseract. Ký tự lạ nhưng hợp lệ chỉ warning, không tự gọi API.
- [ ] Tách đúng subset, giữ original page mapping. Password-protected/corrupt/configuration failures có typed errors. Cache atomic JSON kèm checksum và cấu hình; ghi provenance parser version thật. Không coi output null/empty unknown là extracted. Test mixed pages ghép có đủ thứ tự và cache survives restart.
- [ ] Chạy lại tests A5; test encrypted PDF và page không chữ nhưng chỉ có vector vẽ. Commit `feat: extract PDF locally with conservative cloud fallback`.

## A6 — Excel, PowerPoint và Office local adapters

**Files:** Create `scripts/ir_data/parsers/{excel,powerpoint,office}.py`,
`tests/corpus/{test_excel_parser,test_powerpoint_parser,test_office_conversion}.py`;
fixtures `.xls/.ppt` thật có nguồn/giấy phép được ghi trong `tests/corpus/fixtures/README.md`.

**Interfaces:** `parse_excel(file: DownloadedFile) -> LocalResult`;
`parse_powerpoint(file: DownloadedFile) -> LocalResult`;
`convert_office(source: Path, destination_dir: Path, target: str) -> Path`;
`render_office_subset(source: Path, indices: list[int], destination: Path) -> dict[int, int]`;
`parse_local(file: DownloadedFile) -> LocalResult` dispatcher trong `parsers/__init__.py`.

- [ ] Viết tests XLSX sheet có 0, công thức không cache, hidden sheet và merged cells: assert structured cell giữ tọa độ/kiểu/formula, 0 không thành empty, cloud candidates rỗng. PPTX có text/table trong group, notes, slide trắng và ảnh-only: assert nội dung/notes không trộn và chỉ slide ảnh-only được chọn.
- [ ] Chạy `uv run --extra producer --extra dev pytest tests/corpus/test_excel_parser.py tests/corpus/test_powerpoint_parser.py tests/corpus/test_office_conversion.py -q`, xác nhận FAIL.
- [ ] Implement openpyxl read_only data/formula passes, xlrd cho XLS, python-pptx duyệt group đệ quy/text/table/notes. XLS legacy chỉ ghi những formula/value có thể đọc, không giả lập công thức thiếu. Trích title/labels có sẵn ở chart/diagram bằng adapter XML khi API high-level không lộ; không suy luận nội dung từ hình ảnh đã có local text.
- [ ] LibreOffice headless cho PPT→PPTX; DOC/DOCX/ODT/ODS/ODP theo spec. Subprocess timeout và profile temp riêng mỗi worker; lỗi dependency không kích hoạt cloud. Không ghi đè nguồn. Converter cho sheet fallback chỉ render sheet được chọn với phạm vi cần thiết, không giới hạn theo print area; slide fallback chỉ export slide được chọn. Chạy parser local trên bản convert trước khi quyết định cloud.
- [ ] Tests kiểm tra công thức giữ raw/cache riêng, XLS fixture, PPT fixture, XLSX chỉ nhúng ảnh, charts/notes không làm mất thứ tự, timeout converter không gửi API. `render_office_subset` có image-only fixture và original mapping. Chạy lại tests, ghi hướng dẫn cài LibreOffice producer.
- [ ] Commit `feat: extract Excel and PowerPoint content with local adapters`.

## A7 — LlamaParse fast, key quota fallback và checkpoint job

**Files:** Create `scripts/ir_data/cloud/{__init__,errors,key_pool,llamaparse}.py`,
`tests/corpus/{test_cloud_errors,test_key_pool,test_llamaparse_jobs}.py`; extend `state.py` cloud_jobs.

**Interfaces:** `classify_cloud_error(status_code: int | None, payload: dict | str) -> str` returns quota/rate_limit/storage_limit/auth/invalid/transient/unknown;
`KeyPool(keys: list[tuple[str, str]])`, `current() -> tuple[str, str]`, `mark_quota_exhausted(label: str) -> None`;
`parse_cloud_subset(file: Path, mapping: dict[int, int], state: WorkState, settings: Settings, pool: KeyPool) -> ParseResult`;
`merge_parts(local: LocalResult, remote: ParseResult) -> ParseResult`.

- [ ] Viết fake SDK tests: key1 credits402 → key2 accepted; key2 exhausted → key3; all exhausted -> quota_blocked; 402 storage không đổi key; 429 retry cùng key; 401 không thử mọi key. Capture log assert không chứa bất kỳ key fixture nào, kể cả exception body lặp lại header.
- [ ] Chạy `uv run --extra producer --extra dev pytest tests/corpus/test_cloud_errors.py tests/corpus/test_key_pool.py tests/corpus/test_llamaparse_jobs.py -q`, xác nhận FAIL.
- [ ] Khóa SDK sau đọc signatures/errors chính thức; dùng upload/create/wait/get tách rời thay helper che job ID để checkpoint ngay sau create. Tier fast, latest, không layout surcharge/agentic upgrade. Dùng per-page outputs cho merge; full text/markdown chỉ ở phiên bản hỗ trợ. Nhận diện quota bằng status + nội dung lỗi cụ thể, không chỉ status402/429. SDK retry cấu hình phối hợp một lớp retry; no broad `except` xoay key.
- [ ] Lưu `file_id`, `job_id`, key label, credential fingerprint local để nhận biết key ở label đã thay, project/region nếu biết và request digest; không secret. Cache identity không có key label. Key mới/project khác upload lại; job đã accepted tiếp tục poll bằng key sở hữu. Poll timeout giữ job; submit timeout không có ID -> submission_unknown, không tự submit lại. Cung cấp lệnh reconcile theo job ID để tiếp tục khi đối chiếu được, không đánh dấu parse lỗi terminal cho job chưa rõ.
- [ ] Test crash sau create trước get, restart poll không create thêm; remote result page thiếu thì không extracted; đổi KEY1 value không poll bằng secret mới nhầm scope. Test merge giữ trang local nguyên vẹn và không thêm lại text local lần hai. API live chưa cần cho test quota; có test opt-in bằng tài liệu nhỏ, ghi số trang/tier và kết quả thực.
- [ ] Chạy lại tests và secret-redaction tests; commit `feat: add quota-aware LlamaParse fallback with job recovery`.

## A8 — Orchestrate download → parse, status và xuất enrichment

**Files:** Create `scripts/ir_data/{pipeline,enrichment_export}.py`,
`tests/corpus/{test_pipeline_resume,test_enrichment_export}.py`, `docs/document-pipeline.md`;
modify `cli.py`, `state.py`, `config/data.yaml`.

**Interfaces:** `run_enrichment(source: Path, work_dir: Path, settings: Settings, stop: asyncio.Event) -> RunSummary`;
`run_extraction(state: WorkState, settings: Settings) -> RunSummary`;
`export_enrichment(state: WorkState, destination: Path) -> Path` returns directory containing `documents.jsonl` and `extraction-report.json`.

- [ ] Integration fixture 4 files: one downloaded/parsed, one half-download, one downloaded/not parsed, one cloud job accepted. Interrupt/reopen, assert request/create counts unchanged for completed/accepted work; final file hashes/part ordering đúng. `test_quota_exhausted_still_finishes_local_work` assert local completed, pending cloud còn và exit4.
- [ ] Chạy `uv run --extra producer --extra dev pytest tests/corpus/test_pipeline_resume.py tests/corpus/test_enrichment_export.py -q`, xác nhận FAIL.
- [ ] Implement bounded stages (download async; local parse mặc định 2 process workers; cloud 1 job mặc định). Không chia sẻ PyMuPDF document giữa threads/processes. Mỗi stage đọc/ghi checkpoint, parse cache atomic và metadata; source URL aliases cùng bytes lấy cùng parse output. Inventory hoàn tất mới bắt đầu run có thể tiếp tục ở stage nào cần.
- [ ] Wire CLI inventory/download/extract/enrich-documents/status/reconcile-job; default resume cùng work-dir, explicit retry-failed, explicit converter/retry config. Export loại bỏ local path/key/job scope; provenance công khai gồm URL/hash/parser/status/referrers, không lộ credentials. Report format counts/coverage/page routes/source-crawl status, phân biệt terminal failure với công việc blocked.
- [ ] Run tests A1–A8: `uv run --extra producer --extra dev pytest tests/corpus -q`; run Ruff trên scripts/tests. Demo restart fixture bằng CLI, assert source checksum giữ nguyên. Viết docs về Range có điều kiện, file restart riêng, quota resume và cách đọc status.
- [ ] Commit `feat: complete resumable document enrichment workflow`.

## Acceptance riêng cho download/resume

- [ ] Process bị SIGTERM hoặc kill khi streaming: `.part` và durable offset không lệch; restart verify SHA đúng.
- [ ] Cùng invocation chạy lần hai không fetch lại file hoàn tất và không parse/cloud lại cache hoàn tất.
- [ ] Server không Range/đổi ETag/416 không gây file nối sai hoặc vòng retry vô hạn.
- [ ] Một host chậm/retry không chặn các host khỏe; tuning concurrency thật sự có hiệu lực.
- [ ] Disk/quota hết giữ checkpoint và báo pending chính xác; không coi corpus đã đủ.
- [ ] Chỉ thử performance bằng fixture/tài liệu cần tải trong phạm vi; không dùng một đợt tải lại toàn bộ làm benchmark.
