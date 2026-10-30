# Information Retrieval Monorepo

Kho mã này gồm các dự án sau:

| Thư mục | Dự án | Công cụ |
| --- | --- | --- |
| [`crawler/`](crawler/README.md) | Năm khối thu thập (reader, downloader, mediator, merger, storage) cùng Mongo (`mongoose`), chạy trong một tiến trình hoặc thành năm dịch vụ HTTP theo slide của thầy; lệnh `crawler` | Python 3.13, `uv`, Docker |
| [`crawler/mediator`](https://github.com/bangoc/hust.edu.vn-2026/tree/main/crawler/mediator) (repo của lớp) | Service mediator (khối 3, cổng 5200) dùng chung cho cả lớp, tách từ khối mediator của `crawler/` | Python 3.13, `uv`, Docker |
| [`lucene/`](lucene/README.md) | Lập chỉ mục và tìm kiếm tài liệu với Apache Lucene | JDK 17+, Maven 3.9+ |
| [`frontend/`](frontend) | Trang demo: nhập link để crawl, xem dữ liệu đã thu thập, tìm kiếm | HTML, CSS, JS, nginx |

Chạy lệnh trong thư mục của từng dự án để các đường dẫn tương đối, cấu hình và dữ liệu đầu ra hoạt động đúng.

## Demo

Một [`docker-compose.yaml`](docker-compose.yaml) dựng cả hệ thống: MongoDB 7.0, năm khối crawler, lucene, frontend.

```bash
mkdir -p crawler/data/state-demo crawler/data/enrichment/cache   # để container ghi được bằng user của máy
docker compose up -d --build
docker compose logs -f lucene     # lần đầu tự lập chỉ mục toàn bộ, chờ tới dòng "API: http://..."
```

Mở http://localhost:8080:

- **Crawl**: nhập link, đặt số trang tối đa nếu muốn lan ít (để trống là lan hết các trang trong cùng host), bấm
  Crawl; số trang đã lưu tăng dần, có nút Dừng. Xong bấm **Tìm thử**: trang vừa crawl tìm được sau khoảng 10–20 giây.
- **Dữ liệu**: toàn bộ trang, tệp, ảnh đã thu thập, mới nhất trước; bấm một mục để xem chi tiết.
- **Tìm kiếm**: BM25 + PageRank, gợi ý sửa lỗi, mở rộng truy vấn, ẩn gần trùng.

Nếu `docker compose up` báo merger lỗi và log ghi `is in use by`: container cũ bị dừng đột ngột khi đang crawl, chờ
một phút rồi chạy lại lệnh `up`.

Máy mới nạp dữ liệu từ bản dump: `DUMP=<tên-file>.archive.gz docker compose run --rm restore` (tệp nằm trong
`crawler/data/submission/`).

## Crawler

`crawler/` là một project Python, một package `crawler`, một lệnh `crawler`:

- `src/crawler/domain/`, `application/`, `infra/`: các lớp của năm khối, theo clean architecture.
- `src/crawler/cli/`: các lệnh (`crawler crawl run`, `crawler corpus ...`, `crawler pictures fetch`, ...);
  `cli/pipeline/` nối năm khối trong một tiến trình.
- `src/crawler/api/`: API HTTP của từng khối, chạy bằng `crawler serve <khối>`.
- `Dockerfile`: image của năm khối; dịch vụ khai báo trong `docker-compose.yaml` ở gốc repo.

```bash
cd crawler
uv sync
uv run pytest -q
uv run crawler --help
```

Xem [hướng dẫn crawler](crawler/README.md) để cấu hình, chạy các khối bằng Docker và chia sẻ corpus
(MongoDB, Kaggle). `lucene/` lập chỉ mục corpus này (`html`, `other`) từ MongoDB chung trong `docker-compose.yaml`: xem
[lucene/README.md](lucene/README.md#tìm-kiếm-trên-corpus-của-crawler-mongodb). Schema:
[crawler/docs/data-schema.md](crawler/docs/data-schema.md#mongodb).

### Kiểm tra mã và pre-commit

`crawler/` có cấu hình ruff, pyrefly (strict) và pytest trong `pyproject.toml`. Hook pre-commit nằm ở thư mục gốc
repo, bỏ qua `lucene/` và `crawler/data/`; ruff, pyrefly và uv-lock chạy khi commit, pytest chạy khi push, chỉ khi
có thay đổi trong `crawler/`:

```bash
uv run --directory crawler pre-commit install
```

## Lucene

```bash
cd lucene
mvn test
mvn package
```

Xem [hướng dẫn Lucene](lucene/README.md) để cài VnCoreNLP và chạy tìm kiếm.
