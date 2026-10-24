# Information Retrieval Monorepo

Kho mã này gồm các dự án sau:

| Thư mục | Dự án | Công cụ |
| --- | --- | --- |
| [`crawler/`](crawler/README.md) | Năm khối thu thập (reader, downloader, mediator, merger, storage) cùng Mongo (`mongoose`), chạy trong một tiến trình hoặc thành năm dịch vụ HTTP theo slide của thầy; lệnh `crawler` | Python 3.13, `uv`, Docker |
| [`crawler/mediator`](https://github.com/bangoc/hust.edu.vn-2026/tree/main/crawler/mediator) (repo của lớp) | Service mediator (khối 3, cổng 5200) dùng chung cho cả lớp, tách từ khối mediator của `crawler/` | Python 3.13, `uv`, Docker |
| [`lucene/`](lucene/README.md) | Lập chỉ mục và tìm kiếm tài liệu với Apache Lucene | JDK 17+, Maven 3.9+ |

Chạy lệnh trong thư mục của từng dự án để các đường dẫn tương đối, cấu hình và dữ liệu đầu ra hoạt động đúng.

## Crawler

`crawler/` là một project Python, một package `crawler`, một lệnh `crawler`:

- `src/crawler/domain/`, `application/`, `infra/`: các lớp của năm khối, theo clean architecture.
- `src/crawler/cli/`: các lệnh (`crawler crawl run`, `crawler corpus ...`, `crawler pictures fetch`, ...);
  `cli/pipeline/` nối năm khối trong một tiến trình.
- `src/crawler/api/`: API HTTP của từng khối, chạy bằng `crawler serve <khối>`.
- `Dockerfile`, `compose.yaml`: dựng cả năm khối cùng MongoDB.

```bash
cd crawler
uv sync
uv run pytest -q
uv run crawler --help
```

Xem [hướng dẫn crawler](crawler/README.md) để cấu hình, chạy các khối bằng Docker và chia sẻ corpus
(MongoDB, Kaggle). `lucene/` hiện chưa đọc corpus này; dự kiến sau này `lucene/` sẽ nhận corpus đã xử lý để lập
chỉ mục và tìm kiếm.

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
