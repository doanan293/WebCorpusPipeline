# Schema dữ liệu

Nơi duy nhất mô tả dữ liệu mà các khối lưu lại. Bản ghi các khối gửi nhau (`POST /add`, `/parse`, ...) mô tả ở
[dich_vu/README.md](../../dich_vu/README.md#giao-diện).

| Nơi lưu | Khối ghi | Nội dung |
|---|---|---|
| MongoDB `html`, `other`, `graph` | Lưu trữ | [collection](#mongodb) |
| `data/state/controller.sqlite3` | Điều khiển | [hàng đợi và lịch crawl lại](#điều-khiển-controllersqlite3) |
| `data/state/aggregator.sqlite3`, `data/state/outbox.jsonl` | Tổng hợp | [URL đã biết, chủ nội dung, outbox](#tổng-hợp-aggregatorsqlite3-outboxjsonl) |
| `data/enrichment/cache/` | Đọc nội dung | kết quả LlamaParse đã trả tiền, khoá theo `sha256` tệp, trang và phiên bản parser |
| `data/corpus/<phiên bản>/` | `corpus package` | [gói corpus](#gói-corpus) |

## MongoDB

Cơ sở dữ liệu `information_retrieval` (đổi bằng `MONGODB_DATABASE`). Ba collection theo slide; các trường in
đậm là trường của slide, còn lại là trường thêm.

### `html`

Mỗi trang HTML một document, khoá duy nhất `url`.

| Trường | Kiểu | Ý nghĩa |
|---|---|---|
| **`url`** | string | URL cuối của trang |
| **`format`** | string | luôn `"html"` |
| **`title`** | string | tiêu đề; `""` nếu không tìm được |
| **`content`** | string | văn bản của khối nội dung chính; `""` với trang trùng |
| **`intro`** | string | đoạn giới thiệu (meta description); `""` nếu không có |
| `published_at`, `modified_at` | string \| null | ngày đăng, ngày sửa (ISO 8601) |
| `author` | string \| null | tác giả |
| `language` | string \| null | ngôn ngữ khai báo của trang |
| `headings` | list | `{level, text, heading_path}` |
| `content_blocks` | list | các khối `heading`, `paragraph`, `list`, `table`, `figure`, ... kèm `heading_path` |
| `images` | list[string] | URL ảnh trong khối nội dung |
| `content_id` | string | `sha256` của văn bản đã bỏ khoảng trắng hai đầu |
| `duplicate_of` | string | có khi văn bản trùng với trang khác: URL của trang chủ sở hữu văn bản |
| `updated_at` | string | lần ghi gần nhất (UTC, ISO 8601) |

### `other`

Mỗi tài liệu (pdf, docx, xlsx, pptx, ...) một document, khoá duy nhất `url`.

| Trường | Kiểu | Ý nghĩa |
|---|---|---|
| **`url`** | string | URL của tài liệu |
| **`format`** | string | đuôi tài liệu, vd. `pdf`, `docx`; `bin` khi không xác định được |
| **`content`** | string | văn bản bóc tách, các trang/slide/sheet nối bằng dòng trống |
| `title` | string | `""` (tài liệu chưa có tiêu đề riêng) |
| `content_id` | string | như `html` |
| `updated_at` | string | lần ghi gần nhất |

### `graph`

Mỗi liên kết một document. Khi một trang được ghi lại, mọi cạnh có `src` là trang đó được thay mới.

| Trường | Kiểu | Ý nghĩa |
|---|---|---|
| **`src`** | string | trang chứa liên kết |
| **`dest`** | string | đích của liên kết (trang, tài liệu, hoặc ngoài miền) |
| `text` | string | văn bản liên kết |

Index: `url` (unique) trên `html` và `other`; `src` và `dest` trên `graph`.

## Điều khiển (`controller.sqlite3`)

| Bảng | Cột |
|---|---|
| `urls` | `url` (khoá, đã chuẩn hoá), `host`, `kind` (`hub` \| `page` \| `document`), `next_due_at` (epoch giây; `NULL` = không gửi lại), `last_sent_at`, `attempts` |
| `hosts` | `host` (khoá), `last_sent_at` (lần gửi gần nhất vào host đó; khoảng chờ hiện tính trong bộ nhớ) |
| `lease` | người đang ghi trạng thái (`dich-vu controller@...` hoặc `data-pipeline crawl@...`) và nhịp tim |

## Tổng hợp (`aggregator.sqlite3`, `outbox.jsonl`)

| Nơi | Nội dung |
|---|---|
| bảng `seen_urls` | `url`: mọi URL đã biết (đã gửi sang Điều khiển hoặc đã nhận bản ghi) |
| bảng `content_owners` | `content_id` (khoá), `url`: trang đầu tiên có văn bản đó (theo URL cuối sau redirect) |
| bảng `scope_hosts` | `host`: các host được lan (host của mọi mầm, cộng host của crawl cũ nạp một lần) |
| bảng `lease` | người đang ghi trạng thái và nhịp tim |
| `outbox.jsonl` | mỗi dòng `{"id", "target": "storage" \| "controller", "payload"}`: request chưa gửi được |
| `outbox.dead.jsonl` | request khối kia từ chối hẳn (`4xx`), giữ lại để xem, không gửi lại |

## Gói corpus

`data-pipeline corpus package --output data/corpus/<phiên bản>` xuất ba collection ra:

| Tệp | Nội dung |
|---|---|
| `html.jsonl`, `other.jsonl`, `graph.jsonl` | mỗi dòng một document của collection cùng tên (không có `_id`) |
| `manifest.json` | `schema_version`, `snapshot_id` (sha256 của danh sách tệp), `files` (`sha256`, `size` mỗi tệp), `counts` (số dòng) |

`corpus verify` kiểm tra `sha256`, kích thước và tệp lạ; `corpus pull` tải bản được pin trong
`config/corpus.lock.json`, kiểm tra rồi nạp vào MongoDB.

## Dữ liệu của bản crawl lô cũ

Bản crawl trước khi tách khối vẫn nằm trên đĩa, chỉ đọc:

| Thư mục | Dùng cho |
|---|---|
| `data/crawl/state/index.sqlite3`, `data/crawl/html/` | `data-pipeline reextract` (bóc lại từ HTML đã lưu, gửi vào Tổng hợp) |
| `data/enrichment/cache/` | cache LlamaParse của khối Đọc nội dung |
| `data/crawl/` còn lại, `data/enrichment/state.sqlite3`, `data/corpus/latest/` (định dạng cũ) | đã chuyển sang các khối bằng script một lần; có thể xoá khi không cần đối chiếu nữa |
