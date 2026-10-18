# Điều khiển — service dùng chung

Khối **Điều khiển** (khối 3, cổng 5200) trong slide "Thiết kế hệ thống thu thập dựa trên kiến trúc dịch vụ nhỏ"
(`docs/`). Service nhận URL cần thu thập, giữ hàng đợi, đặt thời gian chờ theo host, rồi gửi URL **xoay vòng**
sang các node **Tải về** của các nhóm. Code được copy từ khối Điều khiển của [`data_pipeline/`](../data_pipeline/)
và chạy độc lập.

```
Tổng hợp ──POST /add──► Điều khiển :5200 ──GET /ping, POST /load──► Tải về (node của các nhóm)
```

## Giao diện

### Nhận URL: `POST http://<ip>:5200/add`

```json
{"urls": ["https://hust.edu.vn/tin-tuc1", "https://hust.edu.vn/tin-tuc2"]}
```

Trả ngay (không chờ tải):

```json
{"accepted": 2, "duplicates": 0, "rejected": 0}
```

- `accepted`: URL mới được đưa vào hàng đợi.
- `duplicates`: URL đã có, giữ nguyên lịch.
- `rejected`: không phải URL web hợp lệ.
- Body không đúng dạng `{"urls": [chuỗi, ...]}` thì trả `400 {"error": "..."}`.

Đây là endpoint duy nhất của service.

### Node Tải về phải cài gì

Điều khiển gọi ra node của các nhóm theo đúng slide:

| Lệnh | Node trả |
|---|---|
| `GET http://<node>/ping` | `{"status": "đang chờ"}` khi rảnh, `{"status": "đang bận"}` khi đang tải |
| `POST http://<node>/load` với `{"url": "..."}` | mã `2xx` khi nhận việc (nội dung phản hồi không được đọc) |

- Chỉ node trả **đúng chuỗi** `"đang chờ"` (có dấu) mới được giao việc. Chuỗi khác bị bỏ qua và ghi vào log
  `unexpected ping status`.
- Node trả `409` cho `/load` (vừa nhận việc khác) được coi là đang bận: URL được gửi sang node kế, node không
  bị nghỉ. Chuỗi `/ping` được so sau khi chuẩn hoá Unicode (NFC), nên "đang chờ" gõ dạng tổ hợp vẫn được nhận.
- Node không trả lời `/ping`, hoặc `/load` lỗi hay quá `load_timeout_seconds`, sẽ bị nghỉ
  `node_cooldown_seconds` giây; URL vẫn được giữ để gửi node khác.
- Kết quả tải không gửi về Điều khiển: node Tải về gửi thẳng sang Đọc nội dung.

## Lập lịch

- Hai lần gửi vào cùng một host cách nhau ít nhất `host_delay_seconds` (thời gian chờ trong slide).
- Gửi thành công thì URL được đặt lịch **kiểm tra lại định kỳ**: trang chủ sau 1 ngày, trang sau 7 ngày, tài liệu
  sau 30 ngày (`recrawl_days`; đặt `0` để tắt).
- Gửi lỗi `max_attempts` lần liền thì URL được lùi một chu kỳ.
- Hàng đợi nằm trong SQLite (volume `dieu_khien_state`): tắt rồi bật lại vẫn chạy tiếp.

## Chạy

```bash
# Sửa danh sách node Tải về trong config/controller.yaml (downloaders), rồi:
docker compose up -d --build
curl -X POST localhost:5200/add -d '{"urls": ["https://hust.edu.vn/"]}'
docker compose logs -f dieu-khien
```

Thử với hai node giả lập (ghi log URL nhận được):

```bash
DIEU_KHIEN_CONFIG=config/controller.demo.yaml docker compose --profile demo up -d --build
curl -X POST localhost:5200/add -d '{"urls": ["https://hust.edu.vn/", "https://soict.hust.edu.vn/"]}'
docker compose logs fake-fetcher-1 fake-fetcher-2
```

Không có Docker: `uv sync && uv run dieu-khien serve --config config/controller.yaml`.

## Cấu hình (`config/controller.yaml`)

| Khoá | Mặc định | Ý nghĩa |
|---|---|---|
| `port` | 5200 | cổng của `/add` |
| `downloaders` | | danh sách URL node Tải về, vd. `http://10.0.0.11:8800` (bắt buộc) |
| `host_delay_seconds` | 1.0 | thời gian chờ giữa hai lần gửi vào cùng host |
| `ping_timeout_seconds`, `load_timeout_seconds` | 2, 10 | timeout khi gọi node |
| `node_cooldown_seconds` | 30 | thời gian nghỉ của node lỗi |
| `max_attempts` | 5 | số lần gửi lỗi trước khi lùi URL một chu kỳ |
| `tick_seconds` | 0.5 | nhịp kiểm tra khi không có việc |
| `recrawl_days` | `{hub: 1, page: 7, document: 30}` | chu kỳ kiểm tra lại |
| `database` | | đường dẫn SQLite (bắt buộc) |

Cấu hình sai thì service báo lỗi rõ và không khởi động (mã thoát 2).

## Phát triển

```bash
uv sync
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
uv run pyrefly check
```
