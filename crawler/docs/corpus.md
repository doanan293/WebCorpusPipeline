# Corpus: đóng gói, truy vấn, Kaggle

Corpus là bản chụp có phiên bản của ba collection Mongo theo slide (`html`, `other`, `graph`). Schema ở
[data-schema.md](data-schema.md#gói-corpus).

```bash
uv run data-pipeline corpus package --output data/corpus/latest   # xuất MongoDB ra html/other/graph.jsonl
uv run data-pipeline corpus verify --corpus data/corpus/latest    # kiểm tra file, size, checksum, snapshot_id
uv run data-pipeline corpus query --url https://hust.edu.vn/ --direction incoming --limit 20
```

`package` dựng gói trong `<output>.tmp-<pid>` cạnh thư mục đích rồi mới thay vào bằng `rename`, nên lỗi giữa
chừng để nguyên gói cũ. `verify` trả mã 3 khi corpus không hợp lệ. `query` liệt kê cạnh `graph` đi vào
(`incoming`, mặc định) hoặc đi ra (`outgoing`) một URL, đã bỏ `#fragment`.

URI MongoDB và tên database nằm trong `config/pipeline.yaml` (`database`); biến môi trường `MONGO_URI` ghi đè URI.

## Kaggle

Metadata dataset ở `config/kaggle-dataset.json`; `KAGGLE_API_TOKEN` đọc từ `.env`.

```bash
uv run data-pipeline corpus publish --corpus data/corpus/latest [--update] [--message "ghi chú"]
```

`publish` kiểm tra corpus trước (từ chối mọi file không có trong manifest) và chỉ upload các file manifest liệt
kê cùng `manifest.json`.

`config/corpus.lock.json` ghim owner, slug, version và snapshot ID đã công bố; cập nhật mỗi khi công bố phiên bản
mới. Máy mới lấy đúng bản đó mà không phải crawl lại:

```bash
docker compose up -d mongodb
uv run data-pipeline corpus pull            # tải vào data/corpus/pulled rồi nạp vào MongoDB
uv run data-pipeline corpus pull --no-load  # chỉ tải
```

`pull` tải đúng phiên bản đã ghim, tự chuyển sang tải từng file khi endpoint gói không dùng được cho dataset lớn,
kiểm tra khớp `snapshot_id` của lock, rồi upsert `html`/`other` theo `url` và `graph` theo (`src`, `dest`, `text`).

Phiên bản đang ghim là **v3** (19/10/2026): định dạng của slide sau khi tách khối, gồm `html.jsonl` (141.353 trang),
`other.jsonl` (5.860 tài liệu), `graph.jsonl` (8.251.443 liên kết, văn bản là câu chứa liên kết), snapshot
`58c536de…`. Các phiên bản 1–2 trên Kaggle là định dạng cũ của pipeline Scrapy.
