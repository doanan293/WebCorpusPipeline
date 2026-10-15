# Crawl

Lệnh: `data-pipeline crawl run` (crawl chính), `data-pipeline crawl discover` (chẩn đoán sitemap),
`data-pipeline crawl reextract` (trích xuất lại không cần mạng). `run` và `discover` bắt buộc `-i/--input` (file seed,
không có mặc định) và `-o/--output`; `reextract` chỉ nhận `-o`.
Cấu hình chính sách và hiệu năng nằm trong `config/crawler.yaml`; file được kiểm tra chặt và phải có
đủ schema. Chỉ `.env` chứa `CRAWLER_CONTACT` (xem [README](../README.md#cấu-hình)).

## Chế độ tài nguyên (asset)

```bash
# Mặc định, nhẹ (content-only): không tải tệp nhị phân
uv run data-pipeline crawl run -i config/domains/active.txt -o data/crawl

# Tải cả tài nguyên ngữ nghĩa (ảnh, media, tài liệu)
uv run data-pipeline crawl run -i config/domains/active.txt -o data/crawl-all --assets all

# Nâng cấp một crawl nhẹ đã xong mà không crawl lại HTML
uv run data-pipeline crawl run -i config/domains/active.txt -o data/crawl-all \
  --assets all --reuse-content-from data/crawl
```

- **`content-only` (mặc định):** giữ đủ nội dung bài, cấu trúc và tham chiếu tài nguyên (`assets` trong
  `articles.jsonl`) nhưng không tải tệp nhị phân. Phản hồi nhị phân mơ hồ bị dừng ngay ở header
  `Content-Type`, nên `files.jsonl` rỗng và không có thư mục `files/`.
- **`all`:** tìm và tải tài nguyên được trang tham chiếu trực tiếp (ảnh, audio/video, tài liệu), qua kiểm tra
  an toàn mạng công khai (DNS an toàn, chặn IP private/loopback) và hạn mức lưu trữ.
- **`--reuse-content-from`:** sao chép bài và chỉ mục từ một output `content-only` đã xong, tương thích, rồi
  chỉ tải các tài nguyên đã ghi. Nguồn chỉ đọc, output mới độc lập. Không crawl lại HTML, sitemap hay
  `robots.txt`. Nguồn phải dùng cùng nội dung `--input`, không được đang chạy; đường dẫn đích chưa tồn tại.

## Seed và phạm vi

File seed là UTF-8, mỗi dòng một mục; dòng trống và dòng bắt đầu bằng `#` bị bỏ qua. Mỗi dòng là:

- **Hostname** (vd. `soict.hust.edu.vn`): seed đệ quy. Crawler khởi động bằng `robots.txt`, ba sitemap ứng viên
  (`sitemap.xml`, `sitemap_index.xml`, `sitemap-index.xml`) và trang chủ `https://<host>/`, rồi mở rộng mọi link
  HTML cùng host.
- **URL tuyệt đối** (vd. `https://hust.edu.vn/news/one`): seed chính xác. URL được tải và trích xuất nhưng link
  đi ra từ nó không được theo. Seed nhị phân chính xác tuân theo chế độ asset.

Phạm vi dựa trên so khớp **host đúng nguyên văn**: domain cha không cấp quyền cho subdomain
(`lms.hust.edu.vn` không bao gồm `www.lms.hust.edu.vn` hay `hust.edu.vn`); redirect sang host khác bị ghi
nhận và loại. Ở `--assets all`, URL tài nguyên ngoài host được trang hợp lệ tham chiếu trực tiếp vẫn được nhận
trong phạm vi tài nguyên riêng, kiểm tra từng bước chuyển hướng.

## Chính sách và an toàn

- **Robots:** không áp quy tắc `Allow`/`Disallow`. Vẫn có thể tải `robots.txt` để lấy khai báo `Sitemap`.
- **Truy cập tương thích trình duyệt:** dùng User-Agent của trình duyệt; trang JavaScript shell và từ chối có thể
  khắc phục bằng trình duyệt được thử một lần với Playwright.
- **Chỉ truy cập công khai:** cổng đăng nhập và CAPTCHA là điểm dừng, không bao giờ đăng nhập hay giải CAPTCHA.
- **Liên hệ vận hành:** `CRAWLER_CONTACT` (email thật, đặt trong `.env`) là bắt buộc và được gửi trong header
  `From` của mọi request để quản trị site liên hệ được. Đổi email không ảnh hưởng resume.
- **Cổng mở rộng:** trang trùng (hash nội dung) và trang vỏ (soft 404, form đăng nhập, CAPTCHA, trang rỗng)
  không mở rộng link.

## Frontier và điều kiện dừng hữu hạn

Crawl dừng theo bằng chứng của đồ thị, không theo thời gian:

- **Khử trùng URL chuẩn hoá:** mỗi URL chuẩn hoá được tải tối đa một lần; bản trùng trả `record_only`.
- **Loại link sinh trang vô hạn:** lịch/lưu trữ (`calendar_archive`), tìm kiếm/lọc (`search_filter`) và endpoint
  hành động/đăng nhập (`action_auth`) bị phân loại và loại trước khi vào frontier.
- **Phân trang theo novelty:** đi từng bước bằng `select_semantic_next`. Mỗi route family theo dõi vân tay nội dung
  và tập đích, đóng khi cạn: `repeated_content_fingerprint`, `repeated_target_set`, `zero_novelty`
  (`--pagination-empty-pages`, mặc định 3).

Cầu chì (cắt crawl và ghi lý do vào `manifest.json`):

| Cờ | Ý nghĩa | Mặc định |
|---|---|---|
| `--max-urls-per-host` | URL tối đa mỗi host | 100000 |
| `--max-total-urls` | tổng URL | 1000000 |
| `--max-file-bytes` | kích thước tệp tối đa | 100 MiB |
| `--max-total-file-bytes` | tổng dung lượng tệp | 100 GiB |
| `--concurrency`, `--max-per-host`, `--resource-limit-percent` | ghi đè YAML | theo YAML |

Mặc định trong YAML: 64 request toàn cục, 4 request mỗi host (`concurrent_per_host`), không có delay cố định
(`download_delay_seconds: 0`), AutoThrottle mục tiêu `4.0` (bắt đầu 0,25 giây, tối đa 5 giây), ngưỡng CPU/RAM 90 %
(`resource_limit_percent`; lấy mẫu mỗi 5 giây, chỉ giảm tốc sau hai mẫu liên tiếp vượt ngưỡng), `store_html: true`.
429, `Retry-After`, 5xx và timeout vẫn làm host bị giãn nhịp. Tiến trình đang chạy trong tmux phải dừng và chạy lại
để nhận mã hoặc YAML mới.

## Đầu ra (`data/crawl/`)

| File | Nội dung |
|---|---|
| `urls.txt` | URL đã crawl thành công (`extracted`, `file_saved`), đã sắp xếp |
| `url_records.jsonl` | chỉ mục trạng thái của mọi URL (`extracted`, `file_saved`, `discovered_not_downloaded`, `skipped`, `failed`) |
| `articles.jsonl` | bài trích xuất, schema ở [data-schema.md](data-schema.md) |
| `files.jsonl`, `files/` | tệp tải về, đặt tên theo `sha256` (chỉ ở chế độ `all`) |
| `errors.jsonl` | lỗi cuối cùng và lý do loại |
| `manifest.json` | trạng thái, thời gian, số lượng, chỉ số tài nguyên, chẩn đoán route family, lý do bỏ qua |
| `state/` | SQLite có thể resume (`index.sqlite3`), `manifest.json` gốc và khoá `run.lock` |
| `html/` | HTML gốc nén gzip của mọi trang `extracted`, mỗi nội dung một tệp (`store_html: true`); xem [Trích xuất lại](#trích-xuất-lại-không-cần-mạng-crawl-reextract) |
| `final/` | tạo lại khi crawl kết thúc: `articles.jsonl` (chỉ bài `extracted`; bài trùng giữ `duplicate_of` và không có `text`, trừ khi bản gốc không nằm trong file thì văn bản gốc được chèn vào), `urls.txt`, `README.json` (số bài, thời gian, `crawl_id`) |
| `changes/<run_id>/` | đầu ra của mỗi lần refresh |
| `logs/`, `archive/` | log của người vận hành và snapshot thủ công; `--resume` không dùng |

## Gián đoạn và resume

- **Ctrl-C:** Scrapy đóng request đang chạy, đánh dấu `interrupted`, thoát mã 130. Hàng đợi không lưu riêng: mọi
  URL đã lên lịch nằm trong chỉ mục SQLite.
- **Resume:** chạy lại đúng lệnh với `--resume`; hàng đợi dựng lại từ chỉ mục SQLite, URL đã xong không bị tải lại.
- `--retry-failed`: thử lại lỗi mạng/timeout cuối cùng (giữ nguyên lỗi HTTP và loại theo chính sách).
- `--retry-access-gates`: xếp lại trang từng bị bỏ qua vì CAPTCHA hoặc cổng đăng nhập.
- Mọi cờ `--retry-*` đều yêu cầu `--resume`. `--resume` phải trỏ đúng output và danh sách input gốc; không resume
  output của `discover` hay một danh sách khác.

## Refresh tăng dần

Sau khi crawl xong, cập nhật tại chỗ thay vì crawl lại:

```bash
uv run data-pipeline crawl run -i config/domains/active.txt -o data/crawl --refresh
```

Refresh chỉ tải lại các trang hub (trang chủ, navigation, trang đầu mỗi danh mục phân trang, sitemap thành công
và robots.txt của chúng) cùng các bài gần đây: đăng/sửa trong `refresh_recent_days`, hoặc mới thấy trong
`refresh_recent_runs` lần refresh gần nhất, tối đa `refresh_recheck_limit`. Link tới URL đã có trong chỉ mục không
được tải; URL mới được crawl và mở rộng như thường. Một danh mục chỉ sang trang kế khi trang hiện tại có link mới
(`refresh_pagination_empty_pages`). `ETag`/`Last-Modified` đã lưu được gửi làm header điều kiện nên hub không đổi
trả `304`.

- Trước mỗi run, chỉ mục được sao sang `archive/pre-refresh-<run_id>/`.
- Hub/bài nay lỗi, chuyển hướng đi nơi khác hoặc giống soft 404 vẫn giữ nội dung cũ, lỗi chỉ ghi cho run đó. Riêng
  trang đã trích xuất mà nay trả `404`/`410` được ghi change `removed`, chuyển sang `absent` và rời khỏi `final/`.
- Chỉ bài nội dung mới có thể `modified`, và chỉ khi `change_fingerprint` đổi: văn bản sau khi thay chữ số bằng `0`,
  gộp tên tháng tiếng Việt/Anh và bỏ dòng bắt đầu bằng `Lượt xem`, `Lượt tải`, `Viewed`, `Views`. Bài chỉ đổi số đếm
  hay ngày là `unchanged`. Hub (trang chủ, navigation, trang phân trang) và bài trùng được lưu bản mới với kết quả
  `fetched`, không ghi change. `content_id` vẫn là sha256 chính xác của văn bản, dùng để khử trùng.
- `--refresh` không kết hợp được với `--resume`, `--reuse-content-from` hay `--retry-*`. Nếu bị ngắt, chạy lại
  cùng lệnh để tiếp tục đúng run đó.

Mỗi run xong ghi `changes/<run_id>/`: `changes.jsonl` (mỗi URL một dòng: `change` là `new`, `modified` hoặc
`removed`, `page_role`, `content_id`, và `previous_content_id` với trang sửa), `articles.jsonl` (bản ghi đầy đủ của
trang mới và sửa, cùng schema với `final/articles.jsonl`; bài trùng được chèn nội dung của bài gốc), `summary.json`
(số target theo lý do, kết quả `not_modified`/`unchanged`/`fetched`/`modified`/`removed`/`http_500`…, số mới, sửa và
bị gỡ, `crawl_id`). `final/` được dựng lại như thường; `final/README.json` ghi `crawl_id` và
`last_refresh_run_id`. Bốn thiết lập `refresh_*` trong `config/crawler.yaml` là thiết lập runtime, không ảnh
hưởng khả năng resume.

Cập nhật corpus sau refresh: xem [enrich.md](enrich.md#sau-một-lần-refresh).

## Trích xuất lại không cần mạng (`crawl reextract`)

```bash
uv run data-pipeline crawl reextract -o data/crawl
```

Khi `store_html: true` (mặc định), mỗi response HTML được trích xuất cũng được lưu nén vào `html/`, kể cả bài trùng
và bản tải lại khi refresh. `crawl reextract` chạy lại bộ trích xuất hiện tại trên các tệp đó, **không gửi request
nào**:

- Chỉ xét URL đang `extracted` có HTML gốc; viết lại bản ghi trong `articles`.
- Tính lại bài gốc/bài trùng theo thứ tự URL. Bài gốc cũ giữ quyền nếu `content_id` của nó không đổi, nên chạy hai
  lần cho cùng kết quả.
- Dựng lại `final/`. In `pages`, `changed` (số trang đổi `content_id`) và `duplicates`.
- Từ chối khi crawl chưa kết thúc hoặc `state/run.lock` đang bị giữ.

Nhờ vậy, sửa thuật toán trích xuất (khối nội dung, ngày, `observed_refs`…) chỉ cần `reextract` thay vì crawl lại.

## Mã thoát

| Mã | Ý nghĩa |
|---|---|
| 0 | xong sạch theo chính sách, không có URL lỗi |
| 3 | xong nhưng có URL lỗi cuối cùng |
| 4 | dừng vì chạm giới hạn kích thước/dung lượng (truncated) |
| 130 | bị ngắt, còn việc có thể resume |
| 1 | lỗi nội bộ của crawler |
| 2 | tham số, cấu hình hoặc input không hợp lệ |

`manifest.json` còn có `host_completion` và `metrics.zero_content_hosts`, nên mỗi seed đệ quy kiểm tra được độ
phủ mà không phải quét toàn bộ `url_records`. Host ở trạng thái `zero_content` khi nội dung công khai không có
theo chính sách truy cập, là cổng đăng nhập/CAPTCHA thật, chuyển hướng ra ngoài phạm vi host, hoặc còn lỗi mạng cuối.

## Trích xuất nội dung HTML

Crawler phân tích mỗi phản hồi HTML một lần. Khối nội dung được chọn theo thứ tự: khối ngữ nghĩa `article`/`main`,
rồi phần tử có id/class gợi ý nội dung, cuối cùng là `body`. Điểm số thưởng văn bản hữu ích và số đoạn, phạt khối
nhiều link và gợi ý điều hướng. Khối được chọn được làm sạch menu, form, script, header, footer và phần tử ẩn.

Bản ghi giữ tiêu đề, ngày đăng, tác giả, tóm tắt, văn bản, các khối nội dung cơ bản và metadata nguồn; không bịa
metadata khi trang không có, và ngày giữ nguyên độ chính xác và múi giờ. Link và tham chiếu tài nguyên được thu
thập trước khi làm sạch: `observed_refs` giữ nguồn gốc trang→đích, `assets` nhận diện ảnh và tệp đính kèm. Byte
ảnh không được tải; đồ thị giữ trang tham chiếu, alt và caption.

`data-pipeline eval extraction --fixtures <thư mục> --output <file>` chấm điểm trích xuất trên fixture
(precision/recall/F1 theo token và độ chính xác tiêu đề). Đây là báo cáo chẩn đoán, không khẳng định rằng crawl
lịch sử còn byte HTML để xử lý lại.

## Chẩn đoán sitemap (`crawl discover`)

```bash
uv run data-pipeline crawl discover -i config/domains/active.txt -o data/discovery
```

Chỉ khám phá URL qua sitemap, không theo link HTML và không trích xuất nội dung: thăm dò `robots.txt` và các
sitemap ứng viên, mở rộng sitemap index lồng nhau, không đuôi và `.xml.gz`, ghi URL chuẩn hoá vào `urls.txt`
không giới hạn số lượng. Host không có sitemap không được crawl link HTML. Chế độ khám phá của mỗi host nằm trong
`manifest.json` (`discovery.hosts`): `sitemap`, `sitemap_partial`, `no_sitemap`, `exact`.

Đầu ra (`data/discovery/`): `urls.txt`, `errors.jsonl`, `manifest.json` (`"phase": "discover"`), `state/`.
