# Corpus Storage and Publishing Implementation Plan

**Goal:** Tổng hợp corpus/đồ thị, lưu MongoDB local và phát hành Kaggle public để người mới pull và dùng ngay.

**Architecture:** Xuất JSONL và manifest bất biến từ nguồn crawl cùng enrichment. MongoDB và Kaggle dùng chung corpus đã verify. Snapshot chỉ ready sau khi tất cả dữ liệu tương ứng đã kiểm tra thành công.

**Tech Stack:** Python, SQLite cho graph streaming, PyMongo/GridFS, Docker Compose MongoDB 8.0, Kaggle CLI, pytest.

**Spec:** [Thiết kế](../specs/2026-10-05-kaggle-mongodb-corpus-design.md). **Master:** [Hợp đồng chung](2026-10-05-corpus-pipeline.md).

## Global Constraints

- Mọi file path/lệnh tương đối trong plan tính từ project `data_pipeline/`; repository root `.env` giữ vị trí hiện có. Storage, graph, MongoDB và Kaggle đều thuộc dự án này; tìm kiếm thuộc `lucene/`.

- Giữ nguyên source crawl và lỗi/coverage của nó; không biến `complete_with_failures` thành crawl đầy đủ.
- Không đưa `.env`, key, SQLite nội bộ, cloud job metadata hay binary gốc lên Kaggle.
- Owner và license phải là giá trị thực được chủ repo xác định; không tự bịa.
- Publish là thao tác chủ động; download/extract/package không tự publish.
- Kiểm tra streaming cho corpus lớn, không load toàn bộ JSONL vào RAM.
- Consumer chỉ cần base dependencies; không phải cài parser/cloud hoặc recrawl.

## Review Focus

- Tính snapshot không bị vòng phụ thuộc giữa record ID và checksum payload.
- Một URL được nhiều trang giới thiệu không mất referrer khi deduplicate.
- Bản ghi quá giới hạn BSON được round-trip qua GridFS, không cắt mất text.
- Import/pull lỗi không thay snapshot đã sẵn sàng bằng bản dở.
- Phiên bản Kaggle phải được xác nhận thực tế, không lấy latest trong luồng pull đã khóa.

## C1 — Schema xuất, graph, package và verify

**Files:** Create `src/ir_data/{schema,graph,package,verify}.py`,
`tests/corpus/{test_graph,test_package,test_verify}.py`, `docs/data-schema.md`; extend models/CLI.

**Interfaces:** `build_graph(source: Path, enrichment: Path, output: Path) -> dict`;
`package_corpus(source: Path, enrichment: Path, output: Path) -> Path`;
`verify_corpus(path: Path) -> VerificationReport` chứa valid, counts, checksums, errors.

- [ ] Viết fixtures trang/tài liệu/ảnh, source ngoài corpus, link tương đối, nhiều anchor cùng đích, URL redirect và payload hỏng. Test determinism, thiếu file, count sai, checksum sai và pending enrichment phải từ chối package. Chạy nhóm tests mới, xác nhận FAIL.
- [ ] Chốt JSONL schemas cho articles/documents/images/links và manifest/report với nullable fields, types, schema_version. Giữ `articles.jsonl`, `urls.txt`, `README.json` nguyên nguồn như spec; collection pages được tạo lúc import, giữ bản ghi gốc trong source_record. Adapter đọc record cũ (`links`, `assets`) và record mới (`observed_refs`), không giả lập provenance không có. Bảo toàn URL gốc và final_url riêng; không tùy tiện bỏ query hoặc đồng nhất redirect aliases.
- [ ] Graph cạnh `(source_url,target_url,relation,anchor_text,alt,caption)` dedup chính xác; anchor khác giữ riêng. Target chưa crawl là hợp lệ, target_type unknown khi chưa đủ bằng chứng. Dùng SQLite staging và stream JSONL để không tạo tập cạnh toàn corpus trong RAM.
- [ ] Export `record_id` cho records mới xác định từ loại record và định danh/nội dung quan hệ; JSONL không nhúng snapshot_id. Với articles gốc, tính định danh URL khi import, không sửa source record. Tính snapshot_id từ danh sách tên file + SHA-256 payload theo thứ tự canonical, không đưa manifest tự chứa snapshot vào phép hash. Mongo bổ sung snapshot_id và `_id` ở C2. Metadata thời gian đóng gói trong manifest không làm thay snapshot dữ liệu giống nhau.
- [ ] Package staging nguyên tử, allowlist payload và schema rõ ràng, extraction-report phân biệt terminal failures với pending/quota-blocked. Cho phép terminal failures được mô tả; từ chối còn pending. Verify checksum/count/schema/duplicate IDs/referrer consistency; external targets không bị coi là missing object lỗi.
- [ ] Chạy `uv run --extra producer --extra dev pytest tests/corpus/test_graph.py tests/corpus/test_package.py tests/corpus/test_verify.py -q`; test corpus lớn sinh tự động để kiểm tra đường streaming. Viết schema và ví dụ query logic; commit graph/package/verify.

## C2 — MongoDB Compose, import idempotent và truy vấn

**Files:** Create `compose.yaml`, `src/ir_data/{mongo_store,mongo_import,query}.py`,
`tests/corpus/{test_mongo_import,test_mongo_large_records,test_query}.py`; modify config/CLI/schema docs.

**Interfaces:** `import_corpus(path: Path, settings: Settings) -> dict`;
`read_payload(record: dict, database) -> dict` giải tham chiếu GridFS;
`query_links(snapshot: str, url: str, direction: str, database) -> Iterator[dict]`.

- [ ] Tests integration trên Mongo test riêng: import hai lần count không tăng; crash rồi resume; snapshot khác độc lập; record lớn round-trip; query chỉ đọc dataset ready. Xác nhận tests FAIL trước implementation.
- [ ] Compose `mongo:8.0`, port mặc định `127.0.0.1:27017:27017`, named volume `mongodb_data`, healthcheck. DB mặc định `information_retrieval`. Test dùng project/port/volume riêng; tuyệt đối không xóa volume dữ liệu người dùng khi dọn test.
- [ ] Collections datasets/pages/documents/images/links; `_id` hash(snapshot_id,record_type,record_id), unique index `(snapshot_id,url)` cho objects, source/target indexes cho links. Dataset trạng thái importing/failed/ready; validate package trước import, ghi ready sau kiểm tra counts/references. Bulk upsert bounded và resume xác định.
- [ ] Single importer lock theo DB/snapshot, có owner token; giải phóng an toàn khi lỗi và có cơ chế phục hồi lock sau crash. Test hai tiến trình tranh cùng snapshot. Không dựa vào transaction nhiều document trên standalone Mongo để giả định atomic import; readers lọc ready.
- [ ] Đo BSON trước insert; chuyển payload lớn sang GridFS với checksum/size/reference, dùng reader chung phục hồi nguyên vẹn. Blob identity xác định để retry không tạo bản sao vô hạn. Test text lớn và source_record lớn, thiếu blob phải fail verification.
- [ ] Wire import-mongo và incoming/outgoing queries; document text và image referrers lấy được qua links. Chạy integration suite trên Compose test; kiểm tra `docker compose config` và restart giữ volume. Commit Mongo implementation/docs.

## C3 — Kaggle public publish và pinned pull

**Files:** Create `src/ir_data/{kaggle,publish,pull}.py`,
`config/kaggle-dataset.json`, `config/corpus.lock.json` theo metadata thực khi sẵn sàng,
`tests/corpus/{test_publish,test_pull}.py`; modify settings/CLI/root README.

**Interfaces:** `publish_corpus(path: Path, metadata: dict, mode: str, message: str | None) -> dict`;
`pull_corpus(lock_path: Path, destination: Path) -> Path`;
lock chứa owner/slug, version_number, snapshot_id, manifest checksum, schema_version.

- [ ] Đọc skill Kaggle CLI và xác minh flags/version download của CLI đã khóa trước implement. Fake subprocess tests create public, update không tạo dataset mới khi lỗi, publish chưa processing xong, pull đúng version, checksum sai và staging cleanup. Chạy tests để xác nhận FAIL.
- [ ] Dùng subprocess argv list, không shell interpolation. Credentials theo cơ chế Kaggle chính thức, không in giá trị. Root `.env` chỉ nạp ở CLI entry khi cần; không để logging/env dump lộ key. Staging upload chỉ allowlist corpus đã verify và metadata dataset.
- [ ] Create public rõ ràng; update dataset hiện hữu đúng owner/slug. Chờ trạng thái phát hành, xác nhận phiên bản thực và manifest tương ứng rồi mới ghi lock nguyên tử. Không suy số phiên bản mới bằng cộng 1 khi có khả năng publisher khác chạy. Lỗi để lại trạng thái có thể kiểm tra, không publish lặp tự động.
- [ ] Pull theo version đã khóa, staging riêng và verify trước rename. Từ chối archive traversal, symlink, duplicate paths và kích thước giải nén quá giới hạn cấu hình. Corpus đã tồn tại và verify đúng thì bỏ qua network. Không ghi đè snapshot hợp lệ khi bản mới lỗi.
- [ ] Chạy `uv run --extra dev pytest tests/corpus/test_publish.py tests/corpus/test_pull.py -q` với base dependencies và fake Kaggle transport. Live publish/pull cần owner/license/auth thật; ghi rõ nếu chưa có, không claim đã chạy. Commit integration cùng lock thực chỉ khi đã xác nhận phát hành.

## C4 — CLI end-to-end, nghiệm thu corpus và tài liệu bàn giao

**Files:** Create `tests/corpus/test_cli_e2e.py`; modify `README.md`,
`docs/document-pipeline.md`, `docs/content-extraction.md`, `docs/data-schema.md`, `.gitignore`;
add báo cáo nghiệm thu chứa số đo thật, không credentials.

- [ ] Fixture E2E chạy inventory → download bị ngắt → resume → extract local/cloud fake → graph/package → verify → import/query. Assert source không đổi, SHA file đúng, lần hai không thêm download/cloud calls/DB records. Chạy FAIL trước khi hoàn thiện wiring.
- [ ] Hoàn thiện CLI exit codes theo master; `--help` hữu ích, producer thiếu dependency báo cách cài, consumer base chạy pull/verify/import được. `.gitignore` phủ work-dir/parts/cache/archive và `.env`, giữ `.env.example` chỉ tên biến/mẫu trống nếu cần.
- [ ] README có hai luồng: người mới pull bản pinned + Compose + import/query; producer inventory/download/extract/status/package/publish. Ghi disk limits, retry, Range có điều kiện, quota rotation, reconcile-job, cách chạy lại và cách nhận biết còn pending.
- [ ] Chạy corpus suite và affected crawler suite trong cùng `data_pipeline/`, lint và Compose validation. Đo fixture download nhiều host để chứng minh concurrency/per-host cap; không đặt cam kết tăng tốc thiếu số đo. Ghi số request và bytes tiết kiệm khi restart.
- [ ] Khi triển khai thực tế, inventory nguồn hiện có trước, chạy enrichment vào work-dir riêng trong giới hạn config và theo dõi coverage. Không sửa/xóa source crawl. Quota hết giữ pending; không đóng gói giả hoàn thành. Kiểm tra thủ công mẫu PDF/Excel/PPT đã có text, scan fallback và tài liệu thất bại.
- [ ] Với metadata/auth Kaggle đã có, publish public rồi pull chính phiên bản vào thư mục sạch, verify/import trên DB test và chạy truy vấn mẫu. Nếu chưa có metadata/auth hoặc còn blocked jobs, báo đúng phần chưa nghiệm thu và checkpoint để tiếp tục.
- [ ] Ghi kết quả thực và giới hạn: tổng trang/tài liệu/ảnh/cạnh, trạng thái crawl gốc, extraction coverage, local/cloud pages, lỗi nguồn, kiểm tra resume, version Kaggle. Commit docs/tests bàn giao sau review diff.

## Acceptance

- [ ] Người mới dùng được corpus mà không recrawl hoặc cần LlamaParse key.
- [ ] Truy vấn được nội dung trang/tài liệu, nguồn giới thiệu ảnh và đồ thị có anchor text.
- [ ] Import idempotent, snapshot isolation và GridFS round-trip có kiểm thử.
- [ ] Dataset public và pull phiên bản khóa được xác nhận thật, hoặc nêu rõ chưa thực hiện.
