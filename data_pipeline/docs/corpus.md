# Corpus: đóng gói, MongoDB, Kaggle

Corpus là bộ dữ liệu có phiên bản gồm bài, tài liệu, ảnh và đồ thị liên kết. Schema các file ở
[data-schema.md](data-schema.md).

```bash
uv run data-pipeline corpus package --output data/corpus/latest   # đóng gói từ crawl/final + enrichment
uv run data-pipeline corpus verify --corpus data/corpus/latest    # kiểm tra file, size, checksum, snapshot_id
```

`package` từ chối khi inventory của nguồn chưa chạy xong, hoặc pipeline còn việc dở (tải dở, tệp chưa parse,
`cloud_failed` hay `quota_blocked`), và nêu rõ phần còn lại. Gói được dựng trong `<output>.tmp-<pid>` cạnh thư mục
đích rồi mới thay vào bằng `rename`, nên lỗi giữa chừng để nguyên gói cũ; thư mục có quyền `0755`. Mọi file được
ghi theo thứ tự URL, nên cùng dữ liệu cho cùng `snapshot_id` bất kể thứ tự xử lý. `verify` trả mã 3 khi corpus
không hợp lệ. `build-graph --output <dir>` chỉ dựng đồ thị liên kết.

## MongoDB

```bash
docker compose up -d mongodb
uv run data-pipeline corpus import-mongo --corpus data/corpus/latest
uv run data-pipeline corpus query --snapshot <snapshot_id> --url https://hust.edu.vn/ --incoming
```

`compose.yaml` chạy MongoDB 8 ở `127.0.0.1:27017`; URI và tên database nằm trong `config/pipeline.yaml`
(`database`). Mỗi bản ghi mang `snapshot_id` và có `_id` xác định, nên nhập lại không nhân đôi; chỉ dataset có
trạng thái `ready` được đọc, và snapshot đã `ready` bị bỏ qua. Nhập theo lô (tối đa 100 bản ghi hoặc 4 MB); lỗi
ghi trạng thái `failed` kèm lý do. `query` liệt kê cạnh đi vào (`--incoming`, mặc định) hoặc đi ra (`--outgoing`).

## Kaggle

Metadata dataset ở `config/kaggle-dataset.json`; `KAGGLE_API_TOKEN` đọc từ `.env`. Corpus công bố chỉ chứa văn bản
đã xử lý và nguồn gốc; tệp nhị phân và thông tin xác thực ở lại máy cục bộ.

```bash
uv run data-pipeline corpus publish --corpus data/corpus/latest [--update] [--message "ghi chú"]
```

`publish` kiểm tra corpus trước (`verify` từ chối mọi file không có trong manifest, như `.env` lỡ để trong thư mục)
và chỉ upload các file manifest liệt kê cùng `manifest.json`. Thiếu key trong `config/kaggle-dataset.json` thì báo
rõ key nào; lỗi của `kaggle` CLI được báo kèm stderr của nó.

`config/corpus.lock.json` ghim owner, slug, version và snapshot ID đã công bố; cập nhật mỗi khi có phiên bản mới.
Máy mới kéo đúng bản đó và nhập mà không phải crawl lại:

```bash
uv run data-pipeline corpus pull            # theo config/corpus.lock.json vào data/corpus/pulled
docker compose up -d mongodb
uv run data-pipeline corpus import-mongo --corpus data/corpus/pulled
```

`pull` tải đúng phiên bản đã ghim (`owner/slug/<version>`), thử tải cả gói trước và tự chuyển sang tải từng file
khi endpoint gói không dùng được cho dataset lớn. Bản tải về phải khớp `snapshot_id` của lock, và được thay vào thư
mục đích một cách nguyên tử như `package`.
`lucene/` dùng corpus này để lập chỉ mục và tìm kiếm.
