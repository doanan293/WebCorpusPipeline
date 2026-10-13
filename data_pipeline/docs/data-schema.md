# Schema dữ liệu

Nơi duy nhất mô tả schema các file do crawl và corpus tạo ra.

## Crawl (`data/crawl/`)

### `articles.jsonl`

Mỗi dòng là một trang HTML đã trích xuất:

```json
{
  "url": "https://hust.edu.vn/tuyen-sinh-2026",
  "final_url": "https://hust.edu.vn/tuyen-sinh-2026",
  "title": "Thông tin tuyển sinh năm 2026",
  "language": "vi",
  "canonical_url": "https://hust.edu.vn/tuyen-sinh-2026",
  "author": "Ban Tuyển sinh",
  "published_at": "2026-09-08T08:00:00+07:00",
  "modified_at": "2026-09-08T10:00:00+07:00",
  "summary": "Đề án tuyển sinh đại học chính quy năm 2026 của Đại học Bách khoa Hà Nội.",
  "content_html": "<h1>Thông tin tuyển sinh năm 2026</h1>\n<p>Đại học Bách khoa Hà Nội công bố đề án tuyển sinh.</p>",
  "headings": [
    {"level": 1, "text": "Thông tin tuyển sinh năm 2026", "heading_path": ["Thông tin tuyển sinh năm 2026"]}
  ],
  "content_blocks": [
    {"type": "heading", "level": 1, "text": "Thông tin tuyển sinh năm 2026", "heading_path": ["Thông tin tuyển sinh năm 2026"]},
    {"type": "paragraph", "text": "Đại học Bách khoa Hà Nội công bố đề án tuyển sinh.", "heading_path": ["Thông tin tuyển sinh năm 2026"]},
    {"type": "figure", "asset_url": "https://hust.edu.vn/uploads/campus.jpg", "alt": "Khuôn viên Bách khoa", "caption": "Khuôn viên trường", "heading_path": ["Thông tin tuyển sinh năm 2026"]}
  ],
  "text": "Thông tin tuyển sinh năm 2026\nĐại học Bách khoa Hà Nội công bố đề án tuyển sinh.\nKhuôn viên trường",
  "assets": [
    {"url": "https://hust.edu.vn/uploads/campus.jpg", "role": "inline_image", "alt": "Khuôn viên Bách khoa", "caption": "Khuôn viên trường", "external": false},
    {"url": "https://hust.edu.vn/uploads/de-an-2026.pdf", "role": "document_attachment", "alt": "", "caption": "Tải về Đề án 2026 (PDF)", "external": false}
  ],
  "links": [{"url": "https://hust.edu.vn/gioi-thieu", "text": "Giới thiệu"}],
  "warnings": ["external_asset:https://cdn.partner.test/ad.jpg"],
  "status": 200,
  "page_role": "content",
  "fetched_at": "2026-09-08T08:05:00Z",
  "content_type": "text/html; charset=utf-8"
}
```

- **Tiêu đề:** ưu tiên `h1` nhìn thấy trong khối bài, rồi `og:title`, rồi `<title>`. Ngày đăng mơ hồ hoặc nhiều
  thẻ canonical sinh cảnh báo `ambiguous_published_at`, `ambiguous_canonical_url`.
- **`content_html`:** chỉ giữ phần tử ngữ nghĩa an toàn (`article`, `section`, `div`, `h1`–`h6`, `p`, `br`,
  `strong`, `em`, `b`, `i`, `mark`, `sub`, `sup`, `a`, `ul`, `ol`, `li`, `blockquote`, `pre`, `code`, `table`,
  `thead`, `tbody`, `tfoot`, `tr`, `th`, `td`, `figure`, `figcaption`, `picture`, `source`, `img`, `hr`); bỏ thẻ
  không an toàn (`script`, `style`, `form`, `iframe`, `svg`, `dialog`…), thuộc tính `on*`, `style`, `class`, `id`,
  `data-*`; URL tương đối được đổi sang tuyệt đối.
- **`content_blocks`:** theo thứ tự đọc, mỗi khối kèm `heading_path`; bảng giữ ma trận `headers`, `rows`.
- **`text`:** sinh trực tiếp từ các khối, nên văn bản, cấu trúc và HTML không bao giờ lệch nhau.
- **Khử trùng:** bài trùng (cùng hash văn bản) bỏ `content_html` và ghi `"duplicate_of": "<url đầu tiên>"`.

### `files.jsonl`

Ánh xạ tệp đính kèm đã tải tới vị trí trên đĩa (chỉ ở `--assets all`):

```json
{
  "url": "https://hust.edu.vn/uploads/de-an-2026.pdf",
  "final_url": "https://hust.edu.vn/uploads/de-an-2026.pdf",
  "path": "files/a1b2c3d4e5f6...pdf",
  "content_type": "application/pdf",
  "sha256": "a1b2c3d4e5f6...",
  "size": 245120,
  "asset_role": "document_attachment"
}
```

Tệp đặt tên theo `sha256`: nhiều URL trỏ cùng một nội dung nhị phân chỉ lưu một tệp nhưng mỗi URL vẫn có một dòng.

### `manifest.json`

Ngoài trạng thái, thời gian và số lượng, manifest ghi dung lượng theo nhóm tài nguyên:

```json
{
  "phase": "crawl",
  "assets": {
    "image": {"file_count": 142, "unique_bytes": 15420000},
    "document": {"file_count": 12, "unique_bytes": 48291000},
    "media": {"file_count": 3, "unique_bytes": 12400000}
  },
  "skipped": {"file_size_limit": 2, "storage_budget": 0}
}
```

Ở `content-only` (mặc định) không có tệp nào được tải: `files.jsonl` rỗng và số lượng/byte tài nguyên bằng 0.

## Corpus (`data/corpus/<phiên bản>/`)

Gói do `data-pipeline corpus package` tạo ra chứa `articles.jsonl`, `urls.txt` và `README.json` gốc, cùng
`documents.jsonl`, `images.jsonl`, `links.jsonl`, `extraction-report.json` và `manifest.json`.

- **`documents.jsonl`:** một bản ghi cho mỗi URL tài liệu đã tải: URL gốc và cuối, SHA-256, MIME, kích thước,
  `extraction_status`, các phần văn bản thuần (`page`, `sheet`, `slide` hoặc `document`), phiên bản parser, cảnh
  báo và `referrers`. Thứ tự đọc và nguồn gốc được giữ, không cố tái tạo bố cục.
- **`images.jsonl`:** danh mục URL ảnh; byte ảnh không có trong gói.
- **`links.jsonl`:** mỗi cạnh lưu `source_url`, `target_url`, `relation`, `anchor_text`, `alt`, `caption`; mô tả
  khác nhau là các cạnh riêng. Đích ngoài các trang đã crawl vẫn là đỉnh hợp lệ của đồ thị.
- **`extraction-report.json`:** số đã tải, đã parse, lỗi, đang chờ, bị chặn hạn mức và số byte truyền.
- **`manifest.json`:** `sha256` và kích thước từng file, `snapshot_id` (hash của danh sách file) và `status`.

## MongoDB

Collection: `datasets`, `pages`, `documents`, `images`, `links`. Mỗi bản ghi mang `snapshot_id` và có `_id` xác
định, nên nhập lại một snapshot là idempotent. Chỉ dataset có trạng thái `ready` được đọc. Index theo
`snapshot_id`, `source_url`, `target_url`.
