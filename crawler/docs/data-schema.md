# Schema dữ liệu

Nơi duy nhất mô tả dữ liệu mà các khối lưu lại. Bản ghi các khối gửi nhau (`POST /add`, `/parse`, `/report`,
...) mô tả ở [services.md](services.md#giao-diện).

| Nơi lưu | Khối ghi | Nội dung |
|---|---|---|
| MongoDB `html`, `picture`, `other`, `graph` | storage, `pictures fetch` | [collection](#mongodb) |
| `data/state/mediator.sqlite3` | mediator | [hàng đợi, lịch crawl lại, `etag`](#mediator-mediatorsqlite3) |
| `data/state/merger.sqlite3`, `data/state/outbox.jsonl` | merger | [URL đã biết, chủ nội dung, fingerprint, outbox](#merger-mergersqlite3-outboxjsonl) |
| `data/enrichment/cache/` | reader | kết quả LlamaParse đã trả tiền, khoá theo `sha256` tệp, trang và phiên bản reader |
| `data/crawl/`, `data/enrichment/files/` | (chỉ đọc) | [dữ liệu thô giữ lại](#dữ-liệu-thô-giữ-lại) |
| `data/corpus/<phiên bản>/` | `corpus package` | [gói corpus](#gói-corpus) |
| `data/submission/` | `mongodump` (tay) | [bản dump để nộp](#bản-dump-để-nộp), script kiểm tra `check_submission.py` |

## MongoDB

Cơ sở dữ liệu `information_retrieval` (đổi bằng `MONGODB_DATABASE`). Bốn collection theo format chung của slide
([system-design.md](system-design.md#mongo)); các trường in đậm là trường của format chung, còn lại là trường
thêm của nhóm. Volume của Mongo trong compose ở gốc repo là `mongo_data`, tên thật `lucene_mongo_data`.

### `html`

Mỗi trang HTML một document, khoá duy nhất `url`.

| Trường | Kiểu | Ý nghĩa |
|---|---|---|
| **`url`** | string | URL cuối của trang |
| **`format`** | string | luôn `"html"` |
| **`title`** | string | tiêu đề; `""` nếu không tìm được |
| **`content`** | string | văn bản của khối nội dung chính; `""` với trang trùng |
| **`intro`** | string | văn bản các liên kết trỏ tới trang này ([cách tính](#cách-tính-intro)); `""` khi chưa có trang nào trỏ tới |
| `published_at`, `modified_at` | string \| null | ngày đăng, ngày sửa (ISO 8601) |
| `author` | string \| null | tác giả |
| `language` | string \| null | ngôn ngữ khai báo của trang |
| `headings` | list | `{level, text, heading_path}` |
| `content_blocks` | list | các khối `heading`, `paragraph`, `list`, `table`, `figure`, ... kèm `heading_path` |
| `images` | list[string] | URL ảnh trong khối nội dung (logo, icon, banner bị loại lúc bóc tách) |
| `content_id` | string | `sha256` của văn bản đã bỏ khoảng trắng hai đầu |
| `duplicate_of` | string | có khi văn bản trùng với trang khác: URL của trang chủ sở hữu văn bản |
| `updated_at` | string | lần ghi gần nhất (UTC, ISO 8601) |
| `checked_at` | string | lần kiểm tra gần nhất, kể cả khi nội dung không đổi |
| `changed_at` | string | lần nội dung đổi gần nhất, hoặc lần đầu lưu |

### `picture`

Mỗi ảnh một document, khoá duy nhất `url`. storage tạo bản ghi (chưa có `b64`) khi lưu trang `html` có ảnh chưa
gặp; `crawler pictures fetch` tải ảnh và điền `b64`.

| Trường | Kiểu | Ý nghĩa |
|---|---|---|
| **`url`** | string | URL ảnh, như trong `html.images` |
| **`origin`** | string | trang chứa ảnh ([cách chọn](#origin)) |
| **`format`** | string | `jpg`, `png`, `webp`, `gif`, `svg`, ...: từ `Content-Type` khi tải được (`image/jpeg` → `jpg`), không có thì từ đuôi URL, không xác định thì `""` |
| **`b64`** | string | base64 thuần (không có tiền tố `data:...;base64,`) của byte ảnh; `""` khi chưa tải hoặc tải không được |
| `size` | int \| null | số byte gốc; `null` khi chưa tải được |
| `error` | string \| null | lý do không có `b64`: `too_large` (trên 2 MB), `empty`, `not_image`, `http_<mã>`, `timeout`, `unreachable`; `null` khi thành công hoặc chưa thử |
| `fetched_at` | string \| null | lần tải gần nhất; `null` khi chưa thử |
| `updated_at` | string | lần ghi gần nhất |

### `other`

Mỗi tài liệu một document, khoá duy nhất `url`. Tài liệu Office (`doc`, `docx`, `ppt`, `pptx`, `xls`, `xlsx`),
PDF, `txt`, `csv`, ... đều ở đây, khác nhau ở `format`.

| Trường | Kiểu | Ý nghĩa |
|---|---|---|
| **`url`** | string | URL của tài liệu |
| **`format`** | string | đuôi tài liệu, vd. `pdf`, `docx`; theo `Content-Type` khi URL không có đuôi; `bin` khi không xác định được |
| **`origin`** | string | trang dẫn tới tài liệu ([cách chọn](#origin)) |
| **`content`** | string | văn bản bóc tách, các trang/slide/sheet nối bằng dòng trống |
| `title` | string | `""` (tài liệu chưa có tiêu đề riêng) |
| `content_id` | string | như `html` |
| `updated_at`, `checked_at`, `changed_at` | string | như `html` |

### `graph`

Mỗi liên kết (thẻ `a`, `area`, `link rel=next/alternate`) một document; ảnh và tài nguyên nhúng không phải
liên kết, chúng nằm ở `html.images` và `picture`. Một trang có mỗi cặp (`dest`, `intro`) một lần. Khi trang được
ghi lại, cạnh mới được ghi trước rồi các cạnh cũ khác của nó mới bị xoá (dừng giữa chừng chỉ để thừa cạnh, không
bao giờ mất hết). Trang trùng nội dung (`duplicate_of`) vẫn có cạnh, nhưng link của nó không được crawl tiếp.

| Trường | Kiểu | Ý nghĩa |
|---|---|---|
| **`src`** | string | trang chứa liên kết |
| **`dest`** | string | đích của liên kết (trang, tài liệu, hoặc ngoài miền) |
| **`intro`** | string | văn bản liên kết theo slide: **câu hoàn chỉnh chứa thẻ `a`** khi liên kết nằm trong đoạn văn (`p`, `li`, `td`, tiêu đề, ...); chữ của chính thẻ `a` khi nó đứng riêng (menu, số trang) hoặc khối quá dài (trên 400 ký tự) |

Index: `url` (unique) trên `html`, `picture` và `other`; `src` và `dest` trên `graph`.

### Cách tính `intro`

`html.intro` của trang P (`domain/storage/intro.py`, `build_intro`):

- Lấy `intro` của mọi cạnh `graph` có `dest = P`, chuẩn hoá khoảng trắng, bỏ chuỗi rỗng, bỏ trùng.
- Sắp theo số lần xuất hiện giảm dần; bằng nhau thì theo thứ tự chữ.
- Giữ tối đa 20 đoạn, nối bằng xuống dòng `\n`. Không có cạnh nào thì `""`.

Đoạn mô tả meta của trang không được lưu (vẫn lấy lại được từ HTML đã lưu bằng `reextract`).

Khi lưu trang P, storage làm ba việc của slide: ghi `html` của P và tính `intro` của P từ các cạnh đang có; ghi
các cạnh của P vào `graph`; tính lại `intro` của mỗi đích D đã có bản ghi `html`. Đích chưa crawl không được tạo
bản ghi rỗng; khi D được lưu sau đó, `intro` của nó được tính từ các cạnh đã có.

### `origin`

Một ảnh hoặc tài liệu thường có nhiều trang trỏ tới. `origin` là **trang đầu tiên** được lưu có chứa ảnh
(`picture`) hoặc dẫn tới tài liệu (`other`); các trang khác vẫn tra được trong `html.images` (ảnh) hoặc `graph`
(tài liệu). Đặt một lần khi tạo bản ghi, các lần sau giữ nguyên.

- `picture`: trang đang được lưu khi ảnh xuất hiện lần đầu.
- `other`: trong các trang có cạnh `graph` trỏ tới tài liệu, trang có bản ghi `html` được tạo sớm nhất (`_id`
  nhỏ nhất); không có trang nào trỏ tới (vd. URL mầm) thì `""`.

## mediator (`mediator.sqlite3`)

| Bảng | Cột |
|---|---|
| `urls` | `url` (khoá, đã chuẩn hoá), `host`, `kind` (`hub` \| `page` \| `document`), `next_due_at` (epoch giây; `NULL` = không gửi lại), `last_sent_at`, `attempts`, `etag`, `last_modified` (của lần tải `200` gần nhất, gửi lại trong `/load`), `interval_days` (khoảng cách crawl lại hiện tại; `NULL` = mức gốc) |
| `hosts` | `host` (khoá), `last_sent_at` (lần gửi gần nhất vào host đó; khoảng chờ hiện tính trong bộ nhớ) |
| `lease` | người đang ghi trạng thái (`crawler serve mediator@...` hoặc `crawler crawl@...`) và nhịp tim |

`interval_days` đổi theo kết quả mỗi lần tải ([architecture.md](architecture.md#4-crawl-lại-theo-thay-đổi)):
`hub` luôn giữ mức gốc 1 ngày; `page` và `document` nhân đôi khi không đổi (tối đa 90 ngày), chia đôi khi đổi
(không dưới mức gốc 7 và 30 ngày).

## merger (`merger.sqlite3`, `outbox.jsonl`)

| Nơi | Nội dung |
|---|---|
| bảng `seen_urls` | `url`: mọi URL đã biết (đã gửi sang mediator hoặc đã nhận bản ghi) |
| bảng `content_owners` | `content_id` (khoá), `url`: trang đầu tiên có văn bản đó (theo URL cuối sau redirect) |
| bảng `scope_hosts` | `host`: các host được lan (host của mọi mầm, cộng host của crawl cũ nạp một lần) |
| bảng `fingerprints` | `url` (khoá), `fingerprint`: dấu vân tay nội dung lần lưu gần nhất, để xếp lần sau vào `new`/`modified`/`unchanged` |
| bảng `lease` | người đang ghi trạng thái và nhịp tim |
| `outbox.jsonl` | mỗi dòng `{"id", "target": "storage" \| "mediator" \| "outcome", "payload"}`: request chưa gửi được (`outcome` là `POST /report` của mediator) |
| `outbox.dead.jsonl` | request khối kia từ chối hẳn (`4xx`), giữ lại để xem, không gửi lại |

## Gói corpus

`crawler corpus package --output data/corpus/<phiên bản>` xuất bốn collection và gói các thư mục của `data/`:

| Tệp | Nội dung |
|---|---|
| `html.jsonl`, `picture.jsonl`, `other.jsonl`, `graph.jsonl` | mỗi dòng một document của collection cùng tên (không có `_id`) |
| `data-state.tar.gz.bin`, `data-crawl.tar.gz.bin`, `data-enrichment.tar.gz.bin`, `data-submission.tar.gz.bin` | tar gzip của `data/<thư mục>` (không có `--no-data`); `submission` bỏ các bản dump `*.archive.gz` |
| `manifest.json` | `schema_version` (`"2.0"`), `snapshot_id` (sha256 của danh sách tệp), `files` (`sha256`, `size` mỗi tệp), `counts` (số dòng), `archives` (tệp gói → thư mục trong `data/`) |

`corpus verify` kiểm tra `schema_version`, `sha256`, kích thước và tệp lạ; gói khác `"2.0"` bị từ chối.
`corpus pull` tải bản được pin trong `config/corpus.lock.json`, kiểm tra, nạp vào MongoDB rồi bung các thư mục
`data/` chưa có ([corpus.md](corpus.md)).

## Bản dump để nộp

Bản nộp là dump của database `information_retrieval`, nằm ở `data/submission/` (không nằm trong gói corpus). Kiểm
tra rồi dump, trong `crawler/`:

```bash
uv run python data/submission/check_submission.py     # mã 1 nếu dữ liệu sai format
docker exec webcorpus-mongo-1 mongodump --db information_retrieval --archive=/tmp/ir.archive.gz --gzip
docker cp webcorpus-mongo-1:/tmp/ir.archive.gz data/submission/information_retrieval-$(date +%Y%m%d).archive.gz
docker exec webcorpus-mongo-1 rm /tmp/ir.archive.gz
```

## Dữ liệu thô giữ lại

Không phải dữ liệu nộp, nhưng là bảo đảm không phải tải lại:

| Thư mục | Nội dung, dùng cho |
|---|---|
| `data/crawl/html/`, `data/crawl/state/index.sqlite3` | HTML đã lưu của các trang và chỉ mục của chúng; `crawler reextract` bóc lại từ đây rồi lưu lại qua các khối (trong một tiến trình, hoặc gửi sang dịch vụ merger bằng `--merger URL`) |
| `data/enrichment/files/` | tệp gốc của tài liệu đã tải (tên là `sha256` cộng đuôi). Không code nào đọc, nhưng là bản gốc duy nhất để bóc tách lại tài liệu mà không tải lại |
| `data/enrichment/cache/` | cache LlamaParse của khối reader |
