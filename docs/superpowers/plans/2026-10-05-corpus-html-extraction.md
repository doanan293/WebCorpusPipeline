# HTML Content and Provenance Implementation Plan

**Goal:** Bóc tách tiêu đề, ngày đăng, tác giả, khối nội dung; giữ nguồn giới thiệu và văn bản liên kết theo yêu cầu giảng viên.

**Architecture:** Mở rộng crawler hiện có, giữ tương thích bản ghi cũ. Chọn khối nội dung bằng cấu trúc DOM và mật độ văn bản; thu thập quan hệ trước khi bỏ phần giao diện. Dữ liệu crawl cũ chỉ có thể tái xử lý các trường đã lưu, không giả lập HTML đã mất.

**Tech Stack:** Python, crawler hiện có, HTML selectors, pytest; không BM25 cho việc tìm khối nội dung.

**Spec:** [Thiết kế](../specs/2026-10-05-kaggle-mongodb-corpus-design.md). **Master:** [Thứ tự và hợp đồng chung](2026-10-05-corpus-pipeline.md).

## Global Constraints

- Không crawl lại toàn bộ để hoàn thành bước download tài liệu.
- Trường thiếu là null kèm nguồn/giới hạn; không suy đoán tác giả hoặc ngày đăng.
- Ngày chỉ có ngày/tháng/năm giữ đúng độ chính xác; không tự gán timezone hoặc giờ.
- Nội dung, alt, caption và anchor text là các trường khác nhau.
- Thay đổi thuật toán được đánh giá bằng HTML fixture có nhãn và regression tests.

## Review Focus

- Menu/footer nhiều liên kết không thắng nội dung bài ngắn.
- JSON-LD của bài liên quan không trở thành metadata của bài hiện tại.
- Thu thập provenance toàn DOM không khiến downloader tải ảnh giao diện.
- Resume crawl cũ không hỏng chỉ vì tùy chọn lưu HTML mới có giá trị mặc định false.

## B1 — Chọn khối nội dung và metadata

**Files:** Create `data_pipeline/src/hust_crawler/crawl/content_selection.py`, `article_metadata.py`,
`data_pipeline/tests/unit/crawl/test_content_selection.py`, `test_article_metadata.py`;
modify `data_pipeline/src/hust_crawler/crawl/content.py`, `article.py`, existing unit tests.

**Interfaces:** `select_content(selector) -> ContentSelection` chứa selector khối, score và lý do chọn;
`extract_metadata(selector, page_url: str) -> ArticleMetadata` chứa title, author, published_at, published_raw, date_precision và metadata_sources.
Giữ API public hiện tại của `content.py`/`article.py`; adapter chuyển kết quả mới về record schema tương thích.

- [ ] Đọc đường gọi hiện tại trước sửa; tạo fixtures bài dài, bài ngắn, menu dài, nhiều sidebar, nội dung bảng, nhiều article, thiếu metadata. Viết assertions phần văn bản cần giữ/loại và nguồn metadata.
- [ ] Từ thư mục `data_pipeline/`, chạy `uv run pytest tests/unit/crawl/test_content_selection.py tests/unit/crawl/test_article_metadata.py -q`; xác nhận các hành vi mới FAIL.
- [ ] Sinh ứng viên từ article/main/section/div và nhóm paragraph. Tính score từ lượng chữ hữu ích, mật độ chữ, đoạn văn liên tiếp và dấu hiệu semantic; phạt mật độ link, menu/footer/form. Gộp sibling liên quan có điều kiện, giữ heading/list/table trong khối. Tie-break xác định; fallback rõ khi không có ứng viên đủ tốt. Ghi công thức, trọng số và lý do trong docs để có thể giải thích thuật toán.
- [ ] Metadata ưu tiên nguồn có cấu trúc phù hợp URL hiện tại, rồi meta tags và byline/date trong vùng bài. JSON-LD list/graph xử lý đúng đối tượng; nguồn mâu thuẫn được ghi nhận. Không dùng ngày crawl làm ngày đăng. Giữ nội dung Unicode tiếng Việt và cấu trúc đoạn.
- [ ] Chạy tests mới và `uv run pytest tests/unit/crawl/test_article.py tests/unit/crawl/test_content.py -q` nếu file hiện có; dùng `rg --files tests/unit/crawl` để xác định đúng tên test. Chạy các regression tests chịu tác động, bổ sung ca lỗi phát hiện được.
- [ ] Commit riêng thay đổi thuật toán và metadata, kèm kết quả kiểm thử.

## B2 — Provenance, lưu nguồn tùy chọn và đánh giá

**Files:** Create `data_pipeline/src/hust_crawler/crawl/source_archive.py`, `evaluation.py`,
`data_pipeline/tests/unit/crawl/test_source_archive.py`, `test_evaluation.py`,
`data_pipeline/tests/fixtures/content_extraction/`, `docs/content-extraction.md`;
modify `article.py`, `assets.py`, `options.py`, `cli.py`, `runner.py`, `spider.py`, `writer.py` khi đường gọi thực tế cần;
add CLI adapter `data_pipeline/src/ir_data/evaluate.py` và tests trong cùng project.

**Interfaces:** `collect_observed_refs(selector, page_url) -> list[dict]`;
`save_source(root, page_url, content: bytes, kind: str) -> dict` trả hash, relative_path, kind;
`evaluate(fixtures: Path, output: Path) -> dict` trả metrics và lỗi từng trang.

- [ ] Viết tests một tài liệu được giới thiệu bởi hai trang, hai anchor khác nhau cùng đích, ảnh ngoài khối bài, link tương đối và đích chưa crawl. Viết tests archive ghi nguyên tử và option mới không phá resume checkpoint cũ.
- [ ] Chạy tests mới để xác nhận FAIL. Implement `observed_refs` từ DOM trước khi clean, phân biệt hyperlink/image/document và giữ anchor/alt/caption. Giữ `assets` hiện có tương thích; phát hiện link tài liệu ngoài khối bài không mở rộng phạm vi tải sang ảnh hoặc video. Bản ghi cũ vẫn đọc được qua adapter ở C1.
- [ ] Thêm tùy chọn lưu nguồn mặc định tắt. Phân biệt response HTML bytes với rendered DOM của Playwright; ghi hash, URL và loại nguồn. Option tắt/missing tương đương đối với crawl state cũ; kiểm tra semantic fingerprint hiện tại trước migration. Không tuyên bố HTML cũ có thể phục hồi nếu chưa được lưu.
- [ ] Chuẩn bị tập HTML cố định có expected main text/metadata, gồm bài ngắn và layout khó; tách tập điều chỉnh với tập đánh giá giữ lại. Report token precision/recall/F1 của main text, độ đúng metadata, số trang thiếu trường, ca thất bại. Công bố size/split và cách gán nhãn; không đặt số đo giả hoặc sửa nhãn để tăng điểm.
- [ ] Wire `evaluate-extraction` trong CLI pipeline, chỉ yêu cầu producer extra. Tài liệu bàn giao `docs/content-extraction.md` nằm trong `data_pipeline/`. Viết docs thuật toán, ví dụ tính score, giới hạn bản crawl hiện tại và cách kiểm chứng yêu cầu của thầy.
- [ ] Chạy tests provenance/archive/evaluation, existing crawler unit suite và integration fixtures chịu tác động. Xác nhận không tải thêm binary ảnh, không thay corpus gốc. Commit phần provenance và báo cáo đánh giá.

## Acceptance

- [ ] Có thể chỉ ra khối nội dung được chọn và giải thích bằng các thành phần score.
- [ ] Title/date/author/text được kiểm chứng trên fixtures; trường thiếu được báo trung thực.
- [ ] Nguồn giới thiệu và anchor khác nhau được giữ ngay cả khi target không có trong corpus.
- [ ] Báo cáo phân biệt kết quả từ HTML fixture mới với khả năng phục hồi dữ liệu crawl cũ.
