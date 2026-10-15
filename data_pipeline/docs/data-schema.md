# Schema dữ liệu

Nơi duy nhất mô tả schema các file do crawl, enrich và corpus tạo ra. Cột "Có mặt" cho biết field luôn có hay chỉ
xuất hiện trong trường hợp nào; `null` nghĩa là key có mặt nhưng không có giá trị.

| Thư mục | Giai đoạn | File chính |
|---|---|---|
| `data/crawl/` | crawl | [`articles.jsonl`](#articlesjsonl), [`url_records.jsonl`](#url_recordsjsonl), [`errors.jsonl`](#errorsjsonl), [`files.jsonl`](#filesjsonl), [`manifest.json`](#manifestjson-crawl), [`final/`](#final), [`changes/<run_id>/`](#changesrun_id), [`state/`](#state), `html/` |
| `data/enrichment/` | enrich | [`state.sqlite3`](#statesqlite3), [`files/`, `parts/`, `cache/`](#tệp-trong-dataenrichment) |
| `data/corpus/<phiên bản>/` | corpus | [`documents.jsonl`](#documentsjsonl), [`links.jsonl`](#linksjsonl), [`images.jsonl`](#imagesjsonl), [`manifest.json`](#manifestjson-corpus), [`extraction-report.json`](#extraction-reportjson) |
| MongoDB | corpus | [collection](#mongodb) |

## Crawl (`data/crawl/`)

### `articles.jsonl`

Mỗi dòng là một trang HTML đã trích xuất. `data/crawl/articles.jsonl` là toàn bộ bảng `articles`, kể cả trang mà
trạng thái cuối không còn là `extracted`; bản dùng cho các bước sau là [`final/articles.jsonl`](#final).

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
  "assets": [
    {"url": "https://hust.edu.vn/uploads/campus.jpg", "role": "inline_image", "alt": "Khuôn viên Bách khoa", "caption": "Khuôn viên trường", "external": false},
    {"url": "https://hust.edu.vn/uploads/de-an-2026.pdf", "role": "document_attachment", "alt": "", "caption": "Tải về Đề án 2026 (PDF)", "external": false}
  ],
  "links": [{"url": "https://hust.edu.vn/gioi-thieu", "text": "Giới thiệu"}],
  "warnings": ["external_asset:https://cdn.partner.test/ad.jpg"],
  "page_role": "content",
  "status": 200,
  "content_type": "text/html; charset=utf-8",
  "content_id": "9c1f…64 ký tự hex",
  "text": "Thông tin tuyển sinh năm 2026\nĐại học Bách khoa Hà Nội công bố đề án tuyển sinh.\nKhuôn viên trường"
}
```

| Field | Kiểu | Có mặt | Ý nghĩa |
|---|---|---|---|
| `url` | str | luôn | URL được yêu cầu; có thể là `http` trong khi `final_url` là `https` |
| `final_url` | str | luôn | URL của phản hồi sau chuyển hướng |
| `title` | str | luôn | `h1` nhìn thấy trong khối bài → `og:title` → `<title>` → `h1` bất kỳ; `""` nếu không có (kèm cảnh báo `missing_title`) |
| `language` | str \| null | luôn | giá trị thô của `html@lang`/`xml:lang` hoặc meta `content-language`; không chuẩn hoá (`vi`, `vi-VN`, `en-US`…) |
| `canonical_url` | str \| null | luôn | `<link rel=canonical>` đầu tiên, đã chuẩn hoá |
| `author` | str \| null | luôn | meta `author` hoặc `article:author` |
| `published_at` | str \| null | luôn | ISO 8601 có múi giờ, chuẩn hoá từ meta hoặc `time@datetime`; `null` khi không có hoặc không parse được |
| `modified_at` | str \| null | luôn | như trên, từ `article:modified_time`, `og:updated_time`, `last-modified` |
| `summary` | str \| null | luôn | meta `description`, `og:description` hoặc `twitter:description` |
| `content_html` | str | luôn (cả bài trùng) | HTML đã làm sạch của phần nội dung chính |
| `headings` | list | luôn | các heading lấy từ `content_blocks` |
| `content_blocks` | list | luôn | khối nội dung theo thứ tự đọc |
| `assets` | list | luôn | tài nguyên trong phần nội dung chính |
| `links` | list | luôn | mọi `<a href>` trong **toàn trang**, không chỉ phần nội dung |
| `warnings` | list[str] | luôn | xem [cảnh báo](#cảnh-báo-warnings) |
| `page_role` | str | luôn | `content` hoặc `navigation` |
| `status` | int | luôn | mã HTTP; phản hồi ≥ 400 vào `errors.jsonl`, nên ở đây thực tế luôn là 200 |
| `content_type` | str | luôn | header `Content-Type` thô |
| `content_id` | str | luôn | SHA-256 của `text.strip()`; bài trùng mang `content_id` của bài gốc |
| `text` | str | **chỉ bài không trùng** | văn bản thuần sinh từ `content_blocks`, các dòng nối bằng `\n` |
| `duplicate_of` | str | **chỉ bài trùng** | URL bài gốc có cùng `content_id` |
| `observed_refs` | list | chỉ crawl từ bản mã mới | tham chiếu quan sát được: `{url, text, kind, rel}`, `kind` là `html`, `file` hoặc `unknown` |

**Chuẩn hoá và làm sạch**
- **`content_html`:** chỉ giữ phần tử ngữ nghĩa an toàn (`article`, `section`, `div`, `h1`–`h6`, `p`, `br`,
  `strong`, `em`, `b`, `i`, `mark`, `sub`, `sup`, `a`, `ul`, `ol`, `li`, `blockquote`, `pre`, `code`, `table`,
  `thead`, `tbody`, `tfoot`, `tr`, `th`, `td`, `figure`, `figcaption`, `picture`, `source`, `img`, `hr`); bỏ thẻ
  không an toàn (`script`, `style`, `form`, `iframe`, `svg`, `dialog`…), thuộc tính `on*`, `style`, `class`, `id`,
  `data-*`; URL tương đối được đổi sang tuyệt đối.
- **URL trong `links`:** chỉ `http`/`https`, host chữ thường, bỏ cổng mặc định, bỏ fragment và tham số theo dõi
  (`utm_*`, `gclid`, `fbclid`…).
- **Ngày tháng:** luôn là `YYYY-MM-DDTHH:MM:SS+HH:MM` (`crawl/extract/dates.py` `parse_page_date`). Dạng NukeViet
  `2020-02-23+0723:09:00` được đọc là `2020-02-23T23:09:00+07:00`; `Z` thành `+00:00`; giá trị không có múi giờ
  (`YYYY-MM-DD HH:MM:SS`, `YYYY-MM-DD`) được gán `+07:00`. Chuỗi không parse được cho `null` kèm cảnh báo
  `unparsed_date:<field>`. Bản ghi không có thời điểm tải; thời điểm crawl duy nhất là
  `source_crawl_finished_at_utc` trong `README.json`.

#### `headings`

`{"level": 1-6, "text": str, "heading_path": [str]}`. `heading_path` gồm cả chính heading đó.

#### `content_blocks`

Mọi khối có `type` và `heading_path` (các heading đang mở, theo cấp; heading mới đóng mọi heading cùng cấp hoặc
sâu hơn).

| `type` | Field riêng | Nguồn | Đóng góp vào `text` |
|---|---|---|---|
| `heading` | `level`, `text` | `h1`–`h6` có chữ | `text` |
| `paragraph` | `text` | `<p>` hoặc chữ rời trong phần tử chứa | `text` |
| `list` | `ordered` (bool), `items` (list[str]) | `ul`/`ol`; mọi `li` được làm phẳng, bỏ mục rỗng | mỗi mục một dòng |
| `quote` | `text` | `blockquote` | `text` |
| `code` | `text` | `pre` (dùng `<code>` bên trong nếu có), giữ nguyên chữ | `text` |
| `table` | `headers` (list[list[str]]), `rows` (list[list[str]]) | `thead/tr` là `headers`; không có `thead` thì hàng đầu chứa `th`; bỏ hàng rỗng | mỗi hàng: các ô không rỗng nối bằng dấu cách |
| `figure` | `asset_url`, `alt`, `caption` (str, `""` nếu thiếu) | `<figure>` (ảnh đầu + `figcaption`) hoặc `<img>` rời | `caption`, nếu rỗng thì `alt` |
| `separator` | (không có) | `<hr>` | không có |

#### `assets`

`{"url", "role", "alt", "caption", "external"}`. Mỗi cặp (`url`, `role`) chỉ xuất hiện một lần, theo thứ tự tài
liệu.

| `role` | Nguồn | `caption` lấy từ |
|---|---|---|
| `inline_image` | `img@src`, `img@srcset`, `picture > source`, `video@poster` | `figcaption` của `figure` bao ngoài |
| `media_reference` | `src` của `video`/`audio` hoặc `<source>` bên trong | thuộc tính `title` |
| `document_attachment` | `a@href`, `object@data`, `embed@src` khi MIME hoặc phần mở rộng là tài liệu | anchor text hoặc `title` |

`alt` chỉ có giá trị với `img`. `external` là `true` khi host của tài nguyên khác host của trang.

#### `links`

`{"url", "text"}`. `text` là chữ hiển thị, nếu không có thì lấy `alt` của ảnh con, `aria-label` hoặc `title`. Cặp
(`url`, `text`) trùng bị bỏ.

#### `page_role`

`navigation` khi liên kết là phân trang, query có khoá lọc hoặc phân trang (trừ `?p=` của WordPress trên liên kết
nội dung), đường dẫn là trang điều hướng hoặc kết thúc bằng mẫu phân trang. Còn lại là `content`.

#### Cảnh báo (`warnings`)

Thứ tự cố định như bảng:

| Giá trị | Ý nghĩa |
|---|---|
| `missing_title` | không tìm được tiêu đề, `title` là `""` |
| `ambiguous_canonical_url` | có nhiều thẻ canonical khác nhau |
| `ambiguous_published_at` | có nhiều ứng viên ngày đăng khác nhau; dùng ứng viên đầu tiên |
| `unparsed_date:<field>` | trang có `published_at`/`modified_at` nhưng không parse được; field đó là `null` |
| `external_asset:<url>` | một cảnh báo cho mỗi tài nguyên nằm ở host khác |
| `missing_text` | văn bản sinh từ các khối rỗng |

#### Bài trùng

Khi crawl, URL đầu tiên có một `content_id` được ghi trong bảng `content_owner` và giữ `text`. URL đến sau với cùng
văn bản được lưu dạng rút gọn: có `duplicate_of`, **không có** `text`, `content_html`, `content_blocks`, `headings` và
`observed_refs`; các field khác (kể cả `links`, `content_id`) vẫn giữ. Muốn lấy nội dung của bài trùng thì tra
`duplicate_of` (hoặc `content_id`) trong cùng file. Trang có văn bản rỗng không bao giờ là bài gốc hay bài trùng: nó
không có `duplicate_of`. Bài trùng không được mở rộng liên kết tiếp.

### `url_records.jsonl`

Chỉ mục trạng thái của mọi URL crawler đã gặp, sắp theo URL. Field `None` không được ghi.

```json
{"url": "https://hust.edu.vn/tin-tuc/abc", "final_url": "https://hust.edu.vn/tin-tuc/abc", "status": "extracted",
 "discovery_source": "html_link", "response_purpose": "page", "seed_type": "recursive",
 "discovered_from": "https://hust.edu.vn/", "frontier_action": "scheduled", "priority": 500, "link_kind": "content",
 "content_type": "text/html; charset=UTF-8", "http_status": 200, "content_id": "9c1f…"}
```

| Field | Kiểu | Có mặt | Ý nghĩa |
|---|---|---|---|
| `url` | str | luôn | URL chuẩn hoá, khoá chính |
| `status` | str | luôn | xem bảng dưới |
| `discovery_source` | str | luôn | `seed`, `sitemap`, `html_link`, `reused_content` (tài nguyên lấy từ `--reuse-content-from`) |
| `response_purpose` | str | luôn | `page`, `asset`, `sitemap`, `robots` |
| `final_url` | str | hầu hết | URL sau chuyển hướng; thiếu ở URL bị chặn bởi robots/đăng nhập/captcha, URL còn `scheduled`, seed không hợp lệ |
| `seed_type` | str | URL thuộc host seed | `recursive` (crawl cả host) hoặc `exact` (chỉ đúng URL đó) |
| `discovered_from` | str | trừ seed | trang hoặc sitemap đã dẫn tới URL này |
| `reason` | str | khi `skipped`/`failed` | lý do; xem bảng dưới. Có thể còn sót lại sau khi trạng thái đổi |
| `frontier_action` | str | URL được frontier nhận | luôn là `scheduled` |
| `priority` | int | URL được frontier nhận | 1000 seed/robots/exact, 800 sitemap, 500 nội dung hoặc tài nguyên, 100 điều hướng |
| `explicit` | bool | robots/sitemap của seed, seed exact | URL được chỉ định trực tiếp |
| `link_kind` | str | URL từ liên kết | `content`, `pagination`; `search_filter`, `calendar_archive`, `action_auth` chỉ gặp ở trang lấy từ sitemap |
| `content_type` | str | đã có phản hồi | header `Content-Type` thô |
| `http_status` | int | đã có phản hồi | mã HTTP của phản hồi (bằng `status` của bài); 404/410 với `absent`. Dữ liệu crawl trước bản sửa ghi 200 cho mọi trang `extracted` |
| `content_id` | str | trang đã trích xuất | khớp `content_id` của bài |
| `duplicate_of` | str \| null | bài trùng | URL bài gốc |
| `asset_role` | str | tài nguyên | `inline_image`, `media_reference`, `document_attachment` |
| `asset_group` | str | chỉ `--assets all` | `image`, `media`, `document` |
| `sha256` | str | `file_saved` | hash nội dung tệp |
| `family_key`, `ordinal` | str, int \| null | trang thuộc một nhóm route/phân trang | khoá nhóm và số thứ tự trang trong nhóm |
| `etag`, `last_modified` | str | bản mã mới, sau khi parse HTML | header kiểm tra thay đổi, dùng cho refresh |
| `first_seen_run`, `last_checked_run` | str | sau refresh | `run_id` lần refresh gặp lần đầu / kiểm tra gần nhất |
| `completed` | bool | dữ liệu cũ | cờ cũ do script sửa chữa thủ công ghi; cờ thật là cột `completed` trong SQLite |

**`status`**

| Giá trị | Ý nghĩa |
|---|---|
| `scheduled` | đã nhận và gửi request, chưa xong; robots/sitemap parse thành công cũng giữ trạng thái này |
| `extracted` | trang HTML đã trích xuất và lưu bài |
| `file_saved` | tệp nhị phân đã lưu vào `files/` (chỉ `--assets all`) |
| `discovered_not_downloaded` | tài nguyên không phải HTML đã thấy nhưng không tải (chế độ `content-only`) |
| `skipped` | bị chính sách loại; luôn có `reason` |
| `failed` | lỗi HTTP hoặc kết nối; `reason` bằng `error` trong `errors.jsonl` |
| `absent` | robots hoặc sitemap trả 404/410 |
| `discovered` | giá trị dự phòng khi bản ghi không có trạng thái |

**`reason`**

| Nhóm | Giá trị |
|---|---|
| Loại liên kết điều hướng | `search_filter` (tìm kiếm, bộ lọc, danh sách), `action_auth` (đăng nhập, giỏ hàng, in, chia sẻ), `calendar_archive` (lưu trữ theo ngày) |
| Phạm vi | `host_out_of_scope` (host không thuộc seed) |
| Bẫy URL | `api_endpoint_trap` (`wp-json`), `feed_trap` (feed/rss/atom), `auth_trap`, `calendar_export_trap` (`.ics`), `search_trap`, `action_endpoint_trap` |
| Hạn mức | `host_url_limit`, `total_url_limit` |
| Truy cập | `login_required`, `captcha_blocked` |
| Tài nguyên | `unsupported_asset_type`, `unexpected_asset_content` (URL tài nguyên trả HTML), `file_size_limit`, `storage_budget`, `non_public_address`, `unsupported_scheme`, `credentials_not_allowed`, `non_default_port`, `redirect_without_location`, `redirect_limit`, `redirect_loop` |
| Lỗi (`failed`) | `http_<mã>`, `request_failed`, `sitemap_parse_error`; seed không hợp lệ: `unsupported_scheme`, `invalid_hostname`, `invalid_url` |
| Dữ liệu cũ | `url_length_limit` (do script sửa chữa ghi, mã hiện tại không sinh) |

URL bị loại vì định dạng (`malformed_url`, `logout_path`, `repeated_path_trap`, `too_many_query_pairs`…) không tạo
bản ghi, trừ khi nằm trong file seed.

### `errors.jsonl`

Lỗi cuối cùng của mỗi URL; lỗi mới ghi đè lỗi cũ.

```json
{"url": "http://bme.hust.edu.vn/author/", "final_url": "http://bme.hust.edu.vn/author/", "error": "http_404"}
```

| Field | Có mặt | Ý nghĩa |
|---|---|---|
| `url` | luôn | URL lỗi |
| `error` | luôn | `http_<mã>` (mọi mã ≥ 400), `request_failed`, `file_size_limit`, `sitemap_parse_error`, lý do seed không hợp lệ |
| `final_url` | lỗi HTTP | URL sau chuyển hướng |
| `message` | `request_failed`, `file_size_limit`, seed lỗi | thông báo ngoại lệ (vd. `access_denied:http_403`) |
| `line` | seed lỗi | số dòng trong file seed |

Dòng lỗi không bị xoá khi URL đổi trạng thái, nên `errors.jsonl` có thể nhiều hơn số `url_records` `failed` (URL
sau đó bị `skipped`). Chỉ các lệnh requeue mới xoá lỗi. Lỗi khi refresh không vào đây mà vào
`refresh_targets.outcome`.

### `files.jsonl`

Ánh xạ tệp đính kèm đã tải tới vị trí trên đĩa (chỉ ở `--assets all`; ở `content-only` file rỗng):

```json
{
  "url": "https://hust.edu.vn/uploads/de-an-2026.pdf",
  "final_url": "https://hust.edu.vn/uploads/de-an-2026.pdf",
  "path": "files/a1b2c3d4e5f6...pdf",
  "content_type": "application/pdf",
  "sha256": "a1b2c3d4e5f6...",
  "size": 245120,
  "asset_group": "document",
  "asset_role": "document_attachment",
  "referring_pages": ["https://hust.edu.vn/tuyen-sinh-2026"]
}
```

Tệp trong `files/` đặt tên theo SHA-256 **nội dung** kèm phần mở rộng: nhiều URL trỏ cùng nội dung chỉ lưu một tệp
nhưng mỗi URL vẫn có một dòng. `asset_group`, `asset_role` giữ giá trị gặp đầu tiên; `referring_pages` là các trang
tham chiếu, đã sắp xếp. Tệp vượt `max_file_bytes` bị `skipped/file_size_limit`, vượt tổng `max_total_file_bytes` bị
`skipped/storage_budget`.

### `manifest.json` (crawl)

Bản gốc ở `state/manifest.json`, được chép ra thư mục đầu ra khi kết thúc. Thời gian là Unix epoch (giây, số thực).

| Key | Ý nghĩa |
|---|---|
| `state_version` | phiên bản schema trạng thái (hiện là 4) |
| `phase` | `crawl` hoặc `discover` |
| `input_sha256` | SHA-256 file seed |
| `semantic_config` | cấu hình ảnh hưởng kết quả: danh sách host, `recursive_hostnames`, `exact_urls`, `assets`, giới hạn URL/byte/chuyển hướng, `browser_user_agent`, `cookies_enabled`, `retry_statuses`… Đổi các giá trị này thì không resume được; `CRAWLER_CONTACT` không thuộc nhóm này |
| `runtime_history` | mỗi lần resume với tham số chạy khác thêm một mục: `profile`, `max_concurrency`, `max_per_host`, timeout, retry, throttle… |
| `status` | `running`, `complete`, `complete_with_failures`, `truncated`, `interrupted`, `failed` |
| `exit_code` | 0 complete, 3 có lỗi, 4 bị cắt bớt, 130 bị ngắt, 1 thất bại (xem [crawl.md](crawl.md#mã-thoát)) |
| `resumed_from_status` | trạng thái trước lần resume |
| `started_at`, `finished_at` | thời điểm bắt đầu lần đầu và kết thúc |
| `counts` | `url_status_<trạng thái>` cho từng trạng thái, `total_urls`, `completed_urls`, `articles`, `files`, `errors`, `targets` |
| `metrics` | `elapsed_seconds`, `average_rate`, `recent_rate`, `peak_queue`, `stats` (thống kê Scrapy thô), `frontier`, `zero_content_hosts`, `refresh` (tóm tắt refresh hoặc `null`); khi lỗi có `error` |
| `truncation`, `truncation_reasons` | `{lý do: số URL}` cho `file_size_limit`, `storage_budget`, `host_url_limit`, `total_url_limit`; `null` và `[]` nếu không bị cắt |
| `discovery` | `{strategy, hosts}`; mỗi host có `mode` (`pending`, `sitemap`, `sitemap_partial`, `no_sitemap`, `exact`), `robots_complete`, số sitemap thành công/lỗi/vắng, `sitemap_targets`, `html_targets` |
| `frontier` | `total_scheduled`, `host_scheduled`, `stats`, `route_families` (`total`, `closed`, `closure_reasons`: `repeated_content_fingerprint`, `repeated_target_set`, `zero_novelty`) |
| `assets` | `{image\|document\|media: {file_count, unique_bytes}}` (chỉ phase `crawl`) |
| `skipped` | `{reason: số URL}` trên các bản ghi `skipped` (chỉ phase `crawl`) |
| `host_completion` | với mỗi host recursive: `status` (`complete` hoặc `zero_content`), `extracted_pages`, `terminal_reasons` |
| `last_refresh_run_id` | `run_id` của lần refresh thành công gần nhất |

### `final/`

Tạo lại mỗi khi crawl kết thúc; đây là đầu vào của enrich và corpus.

| File | Nội dung |
|---|---|
| `articles.jsonl` | bài có trạng thái `extracted`, sắp theo URL. Bài trùng giữ nguyên dạng (không `text`, có `duplicate_of`) khi bài gốc cũng `extracted`; nếu bài gốc không có trong file thì `text`, `content_html`, `content_blocks`, `headings`, `observed_refs` của nó được chèn vào và `duplicate_of` bị bỏ |
| `urls.txt` | URL của các bài trên, cùng thứ tự |
| `README.json` | `description`, `crawl_id` (16 ký tự hex đầu của SHA-256 `input_sha256:started_at`), `source_crawl_finished_at_utc` (ISO 8601, `Z`), `article_count`, `url_count`, `files`, `crawl_failed_url_count`, `note`, `last_refresh_run_id` (nếu có). Bản crawl cũ không có `crawl_id` |

### `changes/<run_id>/`

Đầu ra của mỗi lần `--refresh` không bị ngắt hoặc lỗi. `run_id` có dạng `YYYYMMDDTHHMMSSZ` (UTC). Trước mỗi lần
refresh, SQLite được sao lưu vào `archive/pre-refresh-<run_id>/index.sqlite3`.

| File | Nội dung |
|---|---|
| `changes.jsonl` | `{run_id, url, change, page_role, content_id, previous_content_id}`; `change` là `new` (trang mới), `modified` (bài nội dung có `change_fingerprint` đổi, có `previous_content_id`) hoặc `removed` (trang đã có nay trả 404/410; `content_id` là bản cũ). Trang không đổi, hub và bài trùng không được ghi |
| `articles.jsonl` | bài (dạng `final/`) của các URL thay đổi còn `extracted`; bài trùng được chèn nội dung của bài gốc khi bài gốc không có trong file này |
| `summary.json` | `run_id`, `crawl_id`, `status`, `started_at`, `finished_at`, `targets_by_reason`, `outcomes`, `new`, `modified`, `removed` |

Lý do chọn mục tiêu (`targets_by_reason`): `homepage`, `sitemap`, `navigation`, `pagination_head`, `recent`,
`pagination_next`. Kết quả (`outcomes`): `new`, `unchanged`, `modified`, `removed` (404/410), `not_modified` (304),
`fetched` (robots/sitemap, hub và bài trùng), `http_<mã>`, `sitemap_parse_error`, `non_html`, `gate_soft_404`, `gate_login`, `gate_captcha`,
`gate_empty`, `access_gate`, `request_failed`, `pending`.

### `state/`

| File | Nội dung |
|---|---|
| `index.sqlite3` | trạng thái resume (WAL); bảng ở dưới |
| `manifest.json` | bản gốc của manifest |
| `run.lock` | PID của tiến trình đang chạy |

HTML gốc nằm ở `data/crawl/html/<2 ký tự đầu của sha256>/<sha256>.html.gz` (gzip mức 6). Tên là SHA-256 của
**body**, nên nhiều URL cùng nội dung chỉ lưu một tệp. Bảng `raw_pages` nối URL với tệp. Chỉ ghi khi
`store_html: true` trong `config/crawler.yaml`; `crawl reextract` đọc từ đây.

| Bảng | Khoá | Nội dung |
|---|---|---|
| `url_records` | `url` | `payload` (JSON của `url_records.jsonl`), `status`, `completed` (0/1) |
| `articles` | `url` | `payload` (JSON của bài) |
| `files` | `url` | `payload` (JSON của `files.jsonl`) |
| `errors` | `url` | `payload` (JSON của `errors.jsonl`) |
| `targets` | `url` | mục tiêu của chế độ `discover` |
| `content_owner` | `content_id` | URL đang giữ văn bản gốc |
| `asset_referrers` | (`url`, `page_url`, `role`) | tài nguyên → trang tham chiếu |
| `host_discovery` | `hostname` | trạng thái khám phá robots/sitemap của host |
| `route_families` | `family_key` | nhóm route/phân trang: ordinal kế tiếp, dấu vân tay nội dung, `closed`, `closure_reason` |
| `raw_pages` | `url` | HTML gốc của trang `extracted`: `final_url`, `status`, `content_type`, `fetched_at` (ISO 8601 UTC), `body_sha256` → tệp `html/<2 ký tự đầu>/<body_sha256>.html.gz` |
| `refresh_runs`, `refresh_targets`, `refresh_changes` | `run_id`… | chỉ có sau refresh: lần chạy, mục tiêu và kết quả, thay đổi |

### Chế độ `discover`

`crawl discover` chỉ đọc robots và sitemap. Đầu ra gồm `urls.txt` (mọi URL mục tiêu, đã sắp xếp), `errors.jsonl`
(thêm `invalid_sitemap`, `unsupported_content_type`), `manifest.json` (`"phase": "discover"`, không có `assets`,
`skipped`, `host_completion`) và `state/`. Không có `url_records.jsonl`, bài hay `final/`.

## Enrich (`data/enrichment/`)

### `state.sqlite3`

Nguồn sự thật của giai đoạn enrich (WAL). Thời gian là Unix epoch.

| Bảng | Khoá | Cột và ý nghĩa |
|---|---|---|
| `meta` | `key` | `crawl_id` của crawl mà thư mục làm việc gắn với |
| `inventory` | `url` | một dòng mỗi URL tài liệu: `status`, `attempts`, `part_path`, `final_path`, `sha256` (nội dung), `size`, `mime_type`, `final_url`, `etag` (chỉ ETag mạnh), `expected_size`, `retry_at`, `error` (vd. `HTTP 404`, `response is not a document`), `updated` |
| `refs` | 6 cột | cạnh trang → tài liệu (chỉ đích là tài liệu): `source_url`, `target_url`, `relation` (`hyperlink` hoặc `asset`), `anchor_text`, `alt`, `caption`; index theo `target_url` |
| `parse_results` | `url` | `status` (`extracted`, `failed`, `cloud_failed`, `quota_blocked`; hai loại sau được parse lại ở lần `extract` kế), `result_json` (kết quả parse), `cache_path` (chỉ khi có tệp cache LlamaParse), `used_cloud` (0/1), `updated` |
| `cloud_jobs` | `request_id` | job LlamaParse: `url` (URL tài liệu), `file_id`, `job_id` (giữ nguyên khi job lỗi), `key_label`, `key_fingerprint` (16 ký tự hex đầu của SHA-256 khoá), `status`, `payload` (lỗi, tối đa 500 ký tự), `updated` |
| `failures` | (`url`, `stage`) | `stage` (`download`, `parse`), `reason`, `retry_at`, `updated` |
| `inventory_sources` | `path` | con trỏ đọc mỗi `articles.jsonl` nguồn: `size`, `mtime_ns` (file đổi thì đọc lại từ đầu), `cursor` (byte offset), `done` |

**Trạng thái**

| Cột | Giá trị |
|---|---|
| `inventory.status` | `pending` → `in_progress` → `finalizing` → `downloaded`; lỗi thành `retry` (có `retry_at`) hoặc `failed`. Khi mở lại, `in_progress` trở về `pending` |
| `parse_results.status` | `extracted`, `failed` (parser cục bộ lỗi), `cloud_failed`, `quota_blocked` |
| `cloud_jobs.status` | `uploaded`, `accepted`, `completed`; lỗi: `rate_limit`, `quota`, `storage_limit`, `auth`, `invalid`, `transient`, `unknown` |

### Tệp trong `data/enrichment/`

| Đường dẫn | Nội dung |
|---|---|
| `files/<sha256(url)><ext>` | tài liệu đã tải xong. Tên là SHA-256 **của URL**, không phải nội dung; phần mở rộng lấy từ URL nếu là tài liệu đã biết, nếu không thì theo MIME, cuối cùng là `.bin` |
| `parts/<sha256(url)>.part` | tệp đang tải; thành công thì chuyển sang `files/`. Tệp còn lại thường thuộc URL tải lỗi |
| `cache/<key>.json` | kết quả LlamaParse: `{cache_key, result, checksum}`. `cache_key` là SHA-256 của `[sha256 tệp, các trang, phiên bản parser cục bộ, "fast/latest"]`; `result` có `source_sha256`, `parts`, `status`, `warnings`, `parser_versions`; `checksum` kiểm tra `cache_key` và `result` |
| `.writer.lock` | khoá chỉ cho một tiến trình ghi |

## Corpus (`data/corpus/<phiên bản>/`)

Gói do `data-pipeline corpus package` tạo ra. Lệnh từ chối đóng gói khi còn tài liệu `pending`, `retry`,
`in_progress`, `finalizing`, tài liệu đã tải chưa parse, hoặc kết quả `quota_blocked`.

| File | Nguồn |
|---|---|
| `articles.jsonl`, `urls.txt`, `README.json` | chép nguyên từ `data/crawl/final/` |
| `documents.jsonl` | từ `state.sqlite3` của enrich |
| `links.jsonl`, `images.jsonl` | dựng từ `articles.jsonl` |
| `extraction-report.json` | thống kê enrich |
| `manifest.json` | checksum của các file trên |

`enrich export --output <dir>` chỉ ghi `documents.jsonl` (sắp theo URL) và `extraction-report.json`; nội dung giống
`documents.jsonl` của gói, khác thứ tự dòng.

### `documents.jsonl`

Một dòng cho mỗi URL tài liệu đã tải (`inventory.status = downloaded`), key được sắp xếp.

```json
{
  "url": "http://bme.hust.edu.vn/wp-content/uploads/2021/04/DHBK_ET-E5.pdf",
  "final_url": "http://bme.hust.edu.vn/wp-content/uploads/2021/04/DHBK_ET-E5.pdf",
  "sha256": "…",
  "mime_type": "application/pdf",
  "size": 1048576,
  "extraction_status": "extracted",
  "parts": [
    {"index": 1, "kind": "page", "parser": "pymupdf", "text": "…", "has_visual_content": true}
  ],
  "warnings": ["page 2 has visual content but no local text"],
  "parser_versions": {"local": "pymupdf/1.28.2", "llamaparse": "fast/latest"},
  "referrers": [
    {"source_url": "http://bme.hust.edu.vn/?p=836", "target_url": "http://bme.hust.edu.vn/wp-content/uploads/2021/04/DHBK_ET-E5.pdf",
     "relation": "hyperlink", "anchor_text": "Ngành Kỹ thuật Y sinh-DHBK_ET-E5", "alt": "", "caption": ""}
  ]
}
```

| Field | Kiểu | Ý nghĩa |
|---|---|---|
| `url` | str | URL tài liệu (đã bỏ fragment) |
| `final_url` | str | URL sau chuyển hướng |
| `sha256` | str | hash nội dung tệp |
| `mime_type` | str | MIME từ header, không tham số (`application/pdf`, `application/msword`, `application/octet-stream`, docx, xlsx, xls, pptx, `text/plain`…) |
| `size` | int | số byte |
| `extraction_status` | str | `extracted`, `failed`, `cloud_failed`, `quota_blocked`, `pending` (chưa có kết quả parse) |
| `parts` | list | các phần văn bản theo thứ tự đọc; rỗng khi parse lỗi |
| `warnings` | list[str] | xem dưới |
| `parser_versions` | dict | `local` (vd. `pymupdf/1.28.2`, `python-docx`, `openpyxl`, `xlrd`, `python-pptx`) và `llamaparse` (`fast/latest`) nếu đã gọi; `{}` khi lỗi |
| `referrers` | list | các trang tham chiếu tới tài liệu, cùng 6 field như [`links.jsonl`](#linksjsonl) |

**`parts`**: `{index, kind, parser, text, has_visual_content}`, `index` bắt đầu từ 1. Văn bản thuần, giữ ranh giới
trang/sheet/slide, không tái tạo bố cục.

| `kind` | `parser` | Định dạng | `has_visual_content` |
|---|---|---|---|
| `page` | `pymupdf` | PDF | trang có ảnh hoặc hình vẽ |
| `page` | `llamaparse-fast` | trang PDF trực quan không có chữ cục bộ | có chữ từ LlamaParse |
| `document` | `python-docx` | DOCX (DOC/ODT chuyển qua LibreOffice) | không có văn bản dùng được |
| `sheet` | `openpyxl`, `xlrd` | XLSX/ODS, XLS | luôn `false` |
| `slide` | `python-pptx` | PPTX (PPT/ODP chuyển qua LibreOffice) | slide có ảnh và không có chữ |
| `document` | `text` | TXT, CSV, RTF | luôn `false` |

Trang PDF từ LlamaParse chỉ thay trang cục bộ khi có chữ; nếu LlamaParse cũng không trả chữ thì trang `pymupdf`
được giữ.

**`warnings`**: `page N has visual content but no local text` (trang PDF chỉ có hình, chưa lấy được chữ), thông báo
lỗi parser (vd. `File is not a zip file`), `<loại lỗi>: <chi tiết>` khi LlamaParse lỗi, `not parsed` (chỉ ở
`enrich export`, tài liệu chưa parse).

### `links.jsonl`

Đồ thị liên kết dựng từ `articles.jsonl`. Mỗi dòng là một cạnh, key sắp xếp; các dòng sắp theo cả 6 cột. Cạnh trùng
hoàn toàn bị bỏ, mô tả khác nhau là các cạnh riêng.

```json
{"alt": "", "anchor_text": "Giới thiệu", "caption": "", "relation": "hyperlink",
 "source_url": "https://hust.edu.vn/", "target_url": "https://hust.edu.vn/gioi-thieu"}
```

| Field | Ý nghĩa |
|---|---|
| `source_url` | trang chứa liên kết (đã bỏ fragment) |
| `target_url` | đích; đích ngoài các trang đã crawl vẫn là đỉnh hợp lệ |
| `relation` | `hyperlink` (từ `links`), `image` (asset `inline_image`), `asset` (asset khác), `observed` (từ `observed_refs`, không phải ảnh) |
| `anchor_text` | chữ của liên kết (`hyperlink`), `""` với loại khác |
| `alt`, `caption` | từ asset; `""` nếu không có |

Có cạnh tự trỏ (trang liên kết tới chính nó).

### `images.jsonl`

`{"url": "..."}`: các `target_url` khác nhau của cạnh `image`, đã sắp xếp. Gói không chứa byte ảnh.

### `manifest.json` (corpus)

| Key | Ý nghĩa |
|---|---|
| `schema_version` | `"1.0"` |
| `files` | `{tên file: {sha256, size}}` cho mọi file của gói |
| `snapshot_id` | SHA-256 của `files` dạng JSON gọn, sắp key; định danh bản corpus (khớp `config/corpus.lock.json` khi đã ghim) |
| `counts` | hiện luôn `{}` |
| `status` | `ready` |

`corpus verify` kiểm tra lại kích thước, SHA-256 từng file và `snapshot_id`.

### `extraction-report.json`

| Key | Ý nghĩa |
|---|---|
| `downloaded` | tài liệu đã tải |
| `parsed` | tài liệu parse thành công (`extracted`) |
| `failed` | dòng `failures` không còn hẹn thử lại (gồm lỗi tải và lỗi parse) |
| `pending` | tài liệu còn chờ |
| `quota_blocked` | tài liệu bị chặn hạn mức LlamaParse |
| `transferred_bytes` | tổng byte đã tải |

Chưa chạy enrich thì file là `{"status": "not_run"}`.

## MongoDB

Mặc định `mongodb://127.0.0.1:27017`, database `information_retrieval`. Mỗi bản ghi mang `snapshot_id`; nhập lại
một snapshot là idempotent (upsert theo `_id`).

| Collection | Nguồn | Nội dung |
|---|---|---|
| `datasets` | `manifest.json` | `{_id: snapshot_id, snapshot_id, status, manifest, error}`; `status` là `importing`, `ready` hoặc `failed` (`error` tối đa 1000 ký tự). Chỉ dataset `ready` được đọc |
| `pages` | `articles.jsonl` | chỉ giữ `url`, `final_url`, `title`, `author`, `published_at`, `modified_at`, `summary`, `text`, `language`, `canonical_url`, `page_role`, `status`, `content_type`, `content_id`, `duplicate_of`, `warnings` (bỏ `content_html`, `content_blocks`, `headings`, `assets`, `links`) |
| `documents` | `documents.jsonl` | nguyên dòng |
| `images` | `images.jsonl` | nguyên dòng |
| `links` | `links.jsonl` | nguyên dòng |

`_id` là `sha256(JSON định danh) + ":" + snapshot_id`; định danh của `links` là cả 6 field của cạnh, của các
collection khác là URL. Index: `snapshot_id` trên mọi collection; thêm (`snapshot_id`, `source_url`) và
(`snapshot_id`, `target_url`) trên `links`.

Bài trùng trong `pages` không có `text`; tra `duplicate_of` (hoặc `content_id`) để lấy văn bản của bài gốc.
