# Enrich: tải và bóc tách tài liệu

Giai đoạn này đọc đầu ra của crawl (`data/crawl/final`), tải các tài liệu mà trang tham chiếu và bóc tách văn
bản. Trạng thái nằm trong SQLite `data/enrichment/state.sqlite3`, nên mọi bước đều chạy lại được sau gián đoạn.

```bash
uv sync --extra enrich
uv run data-pipeline enrich inventory   # lập danh sách tài liệu và bảng nguồn giới thiệu
uv run data-pipeline enrich download    # tải (tiếp tục được)
uv run data-pipeline enrich extract     # bóc tách văn bản
uv run data-pipeline enrich status --json
```

`enrich run` làm cả ba bước (inventory nếu chưa xong, rồi download và extract). `enrich export --output <dir>`
ghi `documents.jsonl` và `extraction-report.json` của thư mục làm việc. Mặc định `--source` là
`data/crawl/final` và `--work-dir` là `data/enrichment` (tính theo thư mục project); đường dẫn bạn gõ là tương
đối theo thư mục hiện tại.

## Hành vi

- **Inventory** đọc `articles.jsonl` theo luồng, lập danh sách tài liệu duy nhất theo URL và bảng `refs` (trang
  nguồn, anchor text, alt, caption). Mỗi file nguồn có một con trỏ riêng.
- **Download** tối đa 64 luồng toàn cục và 2 mỗi host (xem `config/pipeline.yaml`). Tệp hoàn tất được kiểm
  tra cục bộ và bỏ qua ở lần sau. Tệp `.part` dang dở chỉ được nối tiếp khi ETag mạnh và phản hồi `Range` hợp lệ
  khớp nhau; nếu không, chỉ tệp đó bắt đầu lại. MIME được kiểm bằng nội dung, tệp lưu theo `sha256`.
- **Extract** dùng parser cục bộ trước: PyMuPDF cho PDF, `python-docx` cho DOCX, LibreOffice chuyển DOC/ODT/PPT/
  ODP/ODS sang định dạng Office Open XML, `openpyxl`/`xlrd` cho bảng tính, `python-pptx` cho trình chiếu. Kết
  quả là văn bản thuần với ranh giới trang, sheet, slide hoặc tài liệu. LlamaParse (`fast/latest`) chỉ chạy cho
  trang PDF trực quan không có chữ cục bộ; không bao giờ chạy cho trang trắng hay văn bản ngắn/xấu. Khoá API
  `LLAMAPARSE_KEY1..n` đọc từ `.env`, tự xoay khi hết hạn mức.
- Nếu một lần chạy dừng, chạy lại đúng lệnh. `status --json` báo số đã tải, đã parse, lỗi, đang chờ và bị chặn
  hạn mức (mã thoát 4 khi còn việc chờ hoặc bị chặn hạn mức). `reconcile-job` đối chiếu một job LlamaParse đã ghi.

## Sau một lần refresh

Inventory giữ một con trỏ cho mỗi file nguồn, nên phần refresh được thêm bằng
`--source data/crawl/changes/<run_id>`. Với trang `modified` trong `changes.jsonl` của thư mục đó, các tham chiếu
đi ra cũ được thay bằng tham chiếu mới.

```bash
uv run data-pipeline enrich inventory --source data/crawl/changes/<run_id>
uv run data-pipeline enrich download
uv run data-pipeline enrich extract
uv run data-pipeline corpus package --source data/crawl/final --output data/corpus/latest
uv run data-pipeline corpus import-mongo --corpus data/corpus/latest
```

Thư mục làm việc gắn với một crawl qua `crawl_id` trong `README.json` hoặc `summary.json` của nguồn: các nguồn
refresh của cùng crawl được chấp nhận. Một nguồn thay đổi cần `--rebind-source` để nhận một cách tường minh
(cần một lần với thư mục làm việc tạo trước tính năng refresh, ví dụ `enrich status --rebind-source`).
