# Corpus: đóng gói, truy vấn, Kaggle

Corpus là bản chụp có phiên bản của bốn collection Mongo theo format chung (`html`, `picture`, `other`, `graph`),
kèm các thư mục của `data/`: trạng thái crawl (`state`), HTML đã lưu (`crawl`), tệp gốc và cache LlamaParse
(`enrichment`), script kiểm tra bài nộp (`submission`). Dữ liệu nộp là bản dump Mongo, nên gói corpus chỉ để chia
sẻ và lấy lại dữ liệu: máy mới clone repo rồi `corpus pull` là có lại Mongo và `data/`, không phải crawl lại. Schema ở
[data-schema.md](data-schema.md#gói-corpus).

```bash
uv run crawler corpus package --output data/corpus/latest   # MongoDB ra *.jsonl, data/ ra data-*.tar.gz.bin
uv run crawler corpus package --output data/corpus/latest --no-data   # chỉ bốn collection
uv run crawler corpus verify --corpus data/corpus/latest    # kiểm tra schema_version, file, size, checksum, snapshot_id
uv run crawler corpus query --url https://hust.edu.vn/ --direction incoming --limit 20
```

Mỗi thư mục của `data/` thành một tệp `data-<thư mục>.tar.gz.bin`: đuôi không phải `.tar.gz` vì Kaggle tự bung
tệp `.tar.gz` thành thư mục (và mất checksum). Bản dump Mongo trong `submission/` (`*.archive.gz`) không được gói,
vì đã có trong bốn collection; dump lại sau khi pull bằng lệnh ở [data-schema.md](data-schema.md#bản-dump-để-nộp).
`data/corpus/` (chính gói) cũng không được gói. Không chạy crawl trong lúc `package`, để SQLite của các khối
không đổi giữa chừng.

`package` dựng gói trong `<output>.tmp-<pid>` cạnh thư mục đích rồi mới thay vào bằng `rename`, nên lỗi giữa
chừng để nguyên gói cũ. Manifest ghi `schema_version` `"2.0"`. `verify` trả mã 3 khi corpus không hợp lệ, kể cả
gói có `schema_version` khác `"2.0"`. `query` liệt kê cạnh `graph` đi vào (`incoming`, mặc định) hoặc đi ra
(`outgoing`) một URL, đã bỏ `#fragment`.

URI MongoDB và tên database nằm trong `config/pipeline.yaml` (`database`); biến môi trường `MONGO_URI` ghi đè URI.

## Kaggle

Metadata dataset ở `config/kaggle-dataset.json`; `KAGGLE_API_TOKEN` đọc từ `.env`.

```bash
uv run crawler corpus publish --corpus data/corpus/latest [--update] [--message "ghi chú"]
```

`publish` kiểm tra corpus trước (từ chối mọi file không có trong manifest) và chỉ upload các file manifest liệt
kê cùng `manifest.json`.

`config/corpus.lock.json` ghim owner, slug, version và snapshot ID đã công bố; cập nhật mỗi khi công bố phiên bản
mới. Máy mới lấy đúng bản đó mà không phải crawl lại:

```bash
docker compose up -d mongoose
uv run crawler corpus pull            # tải vào data/corpus/pulled rồi nạp vào MongoDB
uv run crawler corpus pull --no-load  # chỉ tải
```

`pull` tải đúng phiên bản đã ghim, tự chuyển sang tải từng file khi endpoint gói không dùng được cho dataset lớn,
kiểm tra gói (cả `schema_version` `"2.0"`) và khớp `snapshot_id` của lock, rồi upsert `html`/`picture`/`other`
theo `url` và `graph` theo (`src`, `dest`, `intro`). Sau đó bung các thư mục `data/` còn thiếu; thư mục đã có tệp
được giữ nguyên, không ghi đè (`--no-data` để bỏ bước này, `--no-load` chỉ tải về). Thư mục tạm của `publish` và
`pull` nằm cạnh gói chứ không ở `/tmp` (có máy để `/tmp` trong RAM); `publish` dùng hard link nên không chép gói.

Phiên bản đang ghim là **v3** (19/10/2026), định dạng cũ trước format chung (chỉ ba tệp `html`, `other`, `graph`,
chưa có `picture`). `pull` từ chối bản này cho tới khi có bản `2.0` được publish và ghim lại trong
`config/corpus.lock.json`. Các phiên bản 1–2 trên Kaggle là định dạng của pipeline Scrapy cũ.
