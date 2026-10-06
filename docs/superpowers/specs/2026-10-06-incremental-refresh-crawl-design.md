# Crawl tăng dần (refresh): phát hiện bài mới và bài vừa sửa

Ngày: 2026-10-06.

Đường dẫn tương đối tính từ `data_pipeline/`; lệnh vận hành chạy trong
`data_pipeline/`.

## Mục tiêu

Crawl định kỳ để lấy thông tin mới trên 77 host trong `docs/domain_active.txt`
mà không crawl lại phần đã có. Lần crawl đầy đủ hiện tại mất khoảng 15 ngày
(245.809 URL, 79.630 trang `extracted`); một lần refresh phải tính bằng giờ.

Phạm vi đã thống nhất:

- **A — bài mới:** phát hiện và crawl mọi URL chưa từng có trong
  `url_records` mà có thể tới được từ các trang hub.
- **B nhẹ — bài sửa:** chỉ kiểm tra lại các trang hub và một tập bài "gần
  đây"; không kiểm tra lại toàn bộ corpus.
- **Không làm:** phát hiện bài bị xoá/gỡ, cập nhật index Lucene tăng dần,
  upsert Mongo theo delta, lập lịch tự động. Bài cũ không có ngày đăng và bài
  không liên kết từ hub nào chỉ được bù bởi lần crawl đầy đủ định kỳ.

Tiêu chí thành công:

- Refresh không fetch lại URL đã `completed` ngoài tập hub và tập bài gần đây.
- Mỗi lần chạy xuất danh sách chính xác URL `new`/`modified`.
- `final/` sau refresh chứa đầy đủ bài cũ lẫn bài mới; pipeline enrichment chỉ
  xử lý phần mới.
- Lỗi khi fetch lại không bao giờ làm mất hoặc ghi đè bài tốt đã có.

## Hiện trạng liên quan

- `state/index.sqlite3` có `url_records` (khoá chính `url`, payload chứa
  `content_id` là hash nội dung trích xuất), `articles` (payload chứa
  `page_role`), `route_families` (3.352 family phân trang).
- 8.009 bài có `page_role = navigation`; chỉ 3/77 host có sitemap; khoảng 26%
  bài có `published_at`.
- Frontier đã có dedup URL chuẩn hoá và đóng phân trang theo `zero_novelty`.
- `ir-data inventory` đọc `articles.jsonl` bằng một con trỏ byte duy nhất trong
  bảng `inventory_cursor`, kèm cờ `done`; các insert là `INSERT OR IGNORE`.
- `export_final` ghi lại toàn bộ `final/articles.jsonl`, sắp theo URL.

## Kiến trúc

Thêm chế độ `--refresh` vào `hust-crawl`, chạy trên chính output và state của
crawl gốc:

```bash
uv run hust-crawl --input docs/domain_active.txt --output data/crawl --refresh
```

Logic riêng của refresh đặt trong module mới `src/hust_crawler/crawl/refresh.py`
(chọn hub, chọn bài gần đây, so sánh nội dung, ghi changes). `spider.py`,
`state.py`, `frontier.py` chỉ thêm các điểm móc tối thiểu để gọi module này.

### Luồng một lần refresh

1. **Kiểm tra điều kiện:** crawl gốc đã kết thúc, không có lock; `--input` trùng
   inventory gốc (dùng lại kiểm tra của `--resume`). `--refresh` loại trừ
   `--resume`, `--reuse-content-from`, `--retry-failed`, `--retry-policy-skips`,
   `--retry-truncated`. Vi phạm → exit code `2`.
2. **Snapshot:** sao `state/index.sqlite3` sang
   `archive/pre-refresh-<run_id>/index.sqlite3` bằng SQLite backup API.
3. **Tạo run:** `run_id` dạng `YYYYMMDDTHHMMSSZ`, ghi vào bảng mới
   `refresh_runs(run_id PK, started_at, finished_at, status, payload)`;
   `status ∈ {running, interrupted, complete, complete_with_failures}`.
4. **Chọn tập refresh** và lưu vào bảng mới
   `refresh_targets(run_id, url, reason, done, PRIMARY KEY(run_id, url))`,
   `reason ∈ {homepage, sitemap, navigation, pagination_head, recent}`:
   - trang chủ của mọi hostname seed đệ quy;
   - robots/sitemap đã đăng ký thành công của host có sitemap;
   - mọi URL `extracted` có `page_role = navigation`;
   - URL đầu (ordinal nhỏ nhất đã thấy) của mỗi route family `pagination`;
   - tập bài gần đây (mục "Kiểm tra lại bài gần đây").
5. **Crawl:** URL trong `refresh_targets` được đưa vào hàng đợi với cờ
   `refresh=True`, cho phép fetch lại dù đã `completed`. Mọi link khác đi qua
   frontier như cũ: URL đã có trong `url_records` thì không fetch; URL mới được
   crawl và mở rộng link bình thường, ghi `first_seen_run = run_id` vào payload.
6. **Kết thúc:** export như hiện nay (`articles.jsonl`, `url_records.jsonl`,
   `final/`…), ghi `changes/<run_id>/`, cập nhật `refresh_runs`.

### Phân trang khi refresh

Trạng thái đóng của route family từ các lần trước không áp dụng trong run
refresh; frontier giữ trạng thái family riêng cho run. Trang đầu luôn được
fetch; trang kế tiếp chỉ được lên lịch khi trang hiện tại có ít nhất một link
nội dung chưa có trong `url_records`. Số trang trống liên tiếp trước khi dừng
là `refresh_pagination_empty_pages` (mặc định `1`).

### Kiểm tra lại bài gần đây

URL `extracted` có `page_role = content` được đưa vào tập `recent` nếu thoả
một trong hai:

- `published_at` hoặc `modified_at` trong `refresh_recent_days` ngày gần nhất
  (mặc định `60`);
- `first_seen_run` thuộc `refresh_recent_runs` lần refresh hoàn tất gần nhất
  (mặc định `4`).

Tập được giới hạn bởi `refresh_recheck_limit` (mặc định `5000`), ưu tiên theo
ngày (`modified_at`, `published_at`, rồi thứ tự run) mới nhất trước.

### Phát hiện thay đổi

Sau khi fetch lại một URL trong tập refresh và trích xuất thành công:

- `content_id` mới bằng cũ → không đổi; chỉ ghi `last_checked_run`.
- Khác → ghi đè bản ghi trong `articles`, cập nhật `content_id` (qua
  `claim_content`, nên trùng nội dung vẫn sinh `duplicate_of` như cũ), ghi một
  dòng `modified`.

Kết quả trích xuất rỗng hoặc bị phân loại là shell page (soft-404, login,
CAPTCHA) không được tính là thay đổi.

### Request có điều kiện

Khi fetch trang HTML, lưu header `ETag` và `Last-Modified` vào payload
`url_records` (`etag`, `last_modified`). Khi fetch lại URL trong tập refresh,
gửi `If-None-Match`/`If-Modified-Since` nếu có. Phản hồi `304` → coi như không
đổi, không trích xuất, không xét link. Lần refresh đầu chưa có header nên chưa
có lợi ích này.

### Cấu hình

Thêm vào `config/crawler.yaml` (schema kiểm tra chặt, phải có đủ khoá):

```yaml
refresh_pagination_empty_pages: 1
refresh_recent_days: 60
refresh_recent_runs: 4
refresh_recheck_limit: 5000
```

## Đầu ra

`data/crawl/changes/<run_id>/`, ghi atomic (file tạm rồi rename) khi run kết thúc:

- `changes.jsonl` — mỗi dòng:
  `{"run_id", "url", "change": "new" | "modified", "page_role", "content_id", "previous_content_id"?}`.
  Trang navigation đổi nội dung vẫn được ghi, với `page_role: "navigation"`.
- `articles.jsonl` — bản ghi đầy đủ của các URL trong `changes.jsonl` có trạng
  thái `extracted`, cùng schema `final/articles.jsonl` (duplicate được nhúng
  text như `export_final`).
- `summary.json` — số URL refresh theo `reason`, số `304`, số `new`, số
  `modified`, số lỗi theo lý do, thời gian bắt đầu/kết thúc.

`final/` được làm mới toàn bộ như hiện nay; `README.json` thêm
`last_refresh_run_id`.

## Nối vào pipeline enrichment

- Thay `inventory_cursor` bằng bảng `inventory_sources(path PK, cursor, done)`
  khoá theo đường dẫn tuyệt đối của file `articles.jsonl`. Migration: nếu
  `inventory_cursor` còn tồn tại, chuyển giá trị thành một dòng cho nguồn đã
  ghi trong `meta`, hoặc `data/crawl/final/articles.jsonl` nếu không có.
- `ir-data inventory --source data/crawl/changes/<run_id>` đọc nguồn mới với
  con trỏ riêng. Với trang có `change = modified` (đọc từ `changes.jsonl` cùng
  thư mục), xoá các dòng `refs` có `source_url` là trang đó trước khi ghi refs mới.
- `download`, `extract`, `package`, `import-mongo`, `publish` giữ nguyên;
  `package` vẫn đọc `--source data/crawl/final`. Tài liệu đã tải/parse được bỏ
  qua nhờ checkpoint hiện có.

Quy trình cập nhật:

```bash
uv run hust-crawl --input docs/domain_active.txt --output data/crawl --refresh
uv run ir-data inventory --source data/crawl/changes/<run_id> --work-dir data/enrichment
uv run ir-data download --work-dir data/enrichment
uv run ir-data extract --work-dir data/enrichment
uv run ir-data package --source data/crawl/final --work-dir data/enrichment --output data/corpus/latest
uv run ir-data import-mongo --corpus data/corpus/latest
```

## Xử lý lỗi và an toàn dữ liệu

- Bài cũ không bao giờ bị xoá hoặc hạ trạng thái trong refresh. Khi URL đã
  `extracted` được fetch lại mà gặp 404, 5xx, timeout, login/CAPTCHA,
  soft-404 hoặc chuyển hướng ra ngoài scope: giữ nguyên `status`, `articles` và
  `content_id`; ghi lỗi vào `errors` với `run_id` và vào `summary.json`.
- URL mới bị lỗi được ghi `failed`/`skipped` như crawl thường.
- Exit code giống crawl thường: `0`, `3` (có lỗi URL), `130` (bị ngắt), `1`, `2`.

## Ngắt giữa chừng và chạy tiếp

Ctrl-C → `refresh_runs.status = interrupted`, exit `130`. Chạy lại
`--refresh` khi run gần nhất đang `interrupted` sẽ tiếp tục cùng `run_id`:
không chọn lại tập refresh, chỉ lên lịch các `refresh_targets` có `done = 0` và
các URL mới còn `scheduled`. Không snapshot lại. `changes/<run_id>/` chỉ xuất
hiện khi run kết thúc.

## Kiểm thử

Dùng `tests/fixtures/generated_site.py`.

Integration:

1. Crawl đầy đủ site giả.
2. Thêm 3 bài mới: một ở trang 1 danh mục, một đẩy sang trang 2, một chỉ được
   link từ bài mới khác. Sửa nội dung 1 bài gần đây.
3. Chạy `--refresh`. Kiểm tra: đúng 3 dòng `new`, 1 dòng `modified`; không có
   request tới bài cũ ngoài tập refresh; phân trang dừng sau trang đầu tiên
   không có link mới; `final/` có đủ bài cũ và mới; `changes/<run_id>/articles.jsonl`
   khớp `changes.jsonl`.
4. Chạy `--refresh` lần hai không đổi site: `changes.jsonl` rỗng; hub nhận `304`
   khi fixture trả ETag.
5. Hub trả 500 khi refresh: bài cũ giữ nguyên trong `final/`.
6. Ngắt giữa run rồi chạy lại: cùng `run_id`, không fetch trùng.

Unit: chọn hub; chọn bài gần đây theo ngày, theo `first_seen_run` và giới hạn;
so sánh `content_id`; shell page không thành `modified`; gửi header điều kiện;
kiểm tra cờ loại trừ của CLI; migration `inventory_cursor` →
`inventory_sources`; xoá refs cũ của trang `modified`.

## Tài liệu

Cập nhật `data_pipeline/README.md` (mục refresh, cấu hình, quy trình cập nhật)
và `docs/document-pipeline.md` (inventory theo nguồn).
