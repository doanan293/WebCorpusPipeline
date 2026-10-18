# Các khối dịch vụ (theo slide của thầy)

Hệ thống thu thập gồm 5 khối dịch vụ nhỏ và MongoDB. Các khối giao tiếp bằng HTTP và JSON, mỗi khối chạy
trong một container riêng (cùng một image) bằng lệnh `dich-vu serve <khối>` (lõi nằm ở [`data_pipeline/`](../data_pipeline/README.md)).

```mermaid
flowchart LR
    AGG["Tổng hợp dữ liệu (4)<br/>:3300 POST /add"]
    CTRL["Điều khiển (3)<br/>:5200 POST /add"]
    F1["Tải về (2)<br/>:8800 POST /load, GET /ping"]
    F2["Tải về (2)<br/>:8801"]
    PARSE["Đọc nội dung (1)<br/>:6600 POST /parse"]
    STORE["Lưu trữ (5)<br/>:2200 POST /add"]
    MONGO[("Mongo (6)<br/>html, other, graph")]

    AGG -- "URL mầm, URL mới" --> CTRL
    CTRL -- "xoay vòng" --> F1
    CTRL -- "xoay vòng" --> F2
    F1 -- "dữ liệu tải về" --> PARSE
    F2 -- "dữ liệu tải về" --> PARSE
    PARSE -- "bản ghi bóc tách" --> AGG
    AGG -- "bản ghi" --> STORE --> MONGO
```

| # | Khối | Cổng | Endpoint | Lệnh |
|---|---|---|---|---|
| 1 | Đọc nội dung | 6600 | `POST /parse` | `serve parser` |
| 2 | Tải về | 8800, 8801 | `POST /load`, `GET /ping` | `serve fetcher --port N` |
| 3 | Điều khiển | 5200 | `POST /add` | `serve controller` |
| 4 | Tổng hợp dữ liệu | 3300 | `POST /add` | `serve aggregator` |
| 5 | Lưu trữ | 2200 | `POST /add` | `serve storage` |
| 6 | Mongo | 27017 | | |

Không có endpoint nào khác. Body sai định dạng luôn trả `400 {"error": "<lý do>"}`.

## Giao diện

### Điều khiển: `POST :5200/add`

```json
{"urls": ["https://hust.edu.vn/tin-tuc1", "https://hust.edu.vn/tin-tuc2"]}
```

Trả ngay `200 {"accepted": 2, "duplicates": 0, "rejected": 0}`. URL đã có trong hàng đợi là `duplicates`
và giữ nguyên lịch; URL không hợp lệ là `rejected`.

### Tải về: `POST /load`, `GET /ping`

- `POST /load {"url": "..."}` → `202 {"status": "đã nhận"}`; đang bận → `409 {"status": "đang bận"}`.
- `GET /ping` → `{"status": "đang chờ"}` hoặc `{"status": "đang bận"}`.

Mỗi node tải một URL một lúc: trang HTML bằng aiohttp, trang chỉ có khung JS thì mở lại bằng Playwright
(Chromium headless, thay cho Puppeteer), tài liệu thì tải nguyên tệp. Mọi bước redirect đều phải là địa chỉ
công khai. Trang bị chặn (đăng nhập, CAPTCHA, soft 404) và lỗi HTTP chỉ được ghi log.

### Đọc nội dung: `POST :6600/parse`

Body là byte thô đã tải. Header: `X-Url` (URL cuối), `X-Format: html|other`, `Content-Type` (của response
gốc). Trả `202`, bóc tách ở nền rồi gửi bản ghi sang Tổng hợp. Body tối đa khoảng 101 MiB.

### Tổng hợp và Lưu trữ: `POST /add`

```json
{
  "url": "https://hust.edu.vn/tin-tuc1",
  "format": "html",
  "title": "Tin tức 1",
  "content": "Nội dung tin tức bóc tách được",
  "out-links": [["https://hust.edu.vn/tin-tuc2", "Tin tức 2"]]
}
```

- 5 trường trên là bắt buộc. `out-links` là danh sách cặp `[url đi ra, văn bản liên kết]`.
- `format`: `html`, hoặc đuôi tài liệu (`pdf`, `docx`, ...) cho loại "other".
- Trường thêm, tuỳ chọn: `intro`, `published_at`, `modified_at`, `author`, `language`, `headings`,
  `content_blocks`, `images`, `content_id`, `duplicate_of`. Trường lạ bị bỏ qua.
- Trả `200 {"status": "ok"}`. Lưu trữ trả `503` khi Mongo không kết nối được.

## Luồng dữ liệu

1. Tổng hợp khởi động, gửi trang chủ của mỗi host trong `config/domains/active.txt` sang Điều khiển.
2. Điều khiển giữ hàng đợi SQLite. Mỗi bước: lấy URL đến hạn mà host đã hết thời gian chờ
   (`host_delay_seconds`), chọn node Tải về tiếp theo đang `"đang chờ"` (xoay vòng), gửi `/load`.
   Gửi được thì đặt lịch crawl lại: trang chủ 1 ngày, trang 7 ngày, tài liệu 30 ngày (`recrawl_days`).
3. Tải về gửi dữ liệu sang Đọc nội dung; Đọc nội dung gửi bản ghi sang Tổng hợp.
4. Tổng hợp gửi bản ghi sang Lưu trữ (trang trùng nội dung với trang khác chỉ gửi bản ghi rút gọn có
   `duplicate_of`), lọc `out-links` trong miền hust, bỏ bẫy URL và URL đã biết, gom URL mới (500 URL hoặc
   1 giây) gửi sang Điều khiển.
5. Lưu trữ upsert `html`/`other` theo `url` và thay toàn bộ cạnh `graph` của trang đó.

Gửi giữa các khối thử lại 3 lần (chờ 1, 2, 4 giây) khi lỗi kết nối hoặc `5xx`. Chặng Tổng hợp → Lưu trữ và
Tổng hợp → Điều khiển còn có outbox (`/state/outbox.jsonl`): request gửi không được nằm lại đó và được gửi
lại mỗi 30 giây.

## Chạy

```bash
docker compose up -d --build        # 5 khối + MongoDB
docker compose logs -f controller   # theo dõi
curl -X POST localhost:5200/add -d '{"urls": ["https://ctsv.hust.edu.vn/"]}'
```

Thứ tự khởi động đi ngược chiều dữ liệu (Lưu trữ, Đọc nội dung, Tải về, Điều khiển, rồi Tổng hợp), vì
Điều khiển cần node Tải về sống và Tổng hợp gửi URL mầm ngay khi khởi động.

Không có Docker: chạy từng khối bằng `uv run dich-vu serve <khối>` (trong `dich_vu/`) với biến môi trường trỏ tới khối
kề bên (mặc định là tên service trong compose):

| Khối | Biến môi trường |
|---|---|
| Tải về | `PARSER_URL` (`http://parser:6600`) |
| Đọc nội dung | `AGGREGATOR_URL` (`http://aggregator:3300`) |
| Tổng hợp | `STORAGE_URL` (`http://storage:2200`), `CONTROLLER_URL` (`http://controller:5200`) |
| Lưu trữ | `MONGODB_URI`, `MONGODB_DATABASE` |

Danh sách node Tải về của Điều khiển nằm trong `config/controller.yaml`.

## Thay khối bằng khối của nhóm khác

Mỗi khối chỉ biết địa chỉ khối kề bên. Muốn dùng Đọc nội dung của nhóm N1 thì đặt
`PARSER_URL=http://<ip-nhóm-N1>:6600` cho các container Tải về; muốn dùng node Tải về của nhóm N5 thì thêm
địa chỉ đó vào `downloaders` của `config/controller.yaml`. Khối của nhóm khác chỉ cần cài đúng endpoint và
định dạng ở trên.

## Cấu hình

| Tệp | Khối | Nội dung chính |
|---|---|---|
| `config/controller.yaml` | Điều khiển | `downloaders`, `host_delay_seconds`, timeout, `node_cooldown_seconds`, `max_attempts`, `recrawl_days`, `database` |
| `config/fetcher.yaml` | Tải về | user agent, timeout, redirect, giới hạn kích thước |
| `config/parser.yaml` | Đọc nội dung | `llamaparse` (bật OCR đám mây, khoá trong `.env`), `cache_dir` |
| `config/aggregator.yaml` | Tổng hợp | `hosts_file`, `allowed_suffixes`, kích thước lô, chu kỳ outbox, `database`, `outbox` |

Cấu hình sai thì khối báo lỗi rõ và không khởi động.
