# Information Retrieval Monorepo

Kho mã này gồm các dự án sau:

| Thư mục | Dự án | Công cụ |
| --- | --- | --- |
| [`crawler/core/`](crawler/core/README.md) | Lõi năm khối thu thập (mediator, downloader, reader, merger, storage) và CLI của nhóm (`data-pipeline`) | Python 3.13, `uv` |
| [`crawler/server/`](crawler/server/README.md) | Cùng lõi đó chạy thành năm dịch vụ HTTP theo slide của thầy, cộng mongoose (`services serve`) | Python 3.13, `uv`, Docker |
| [`crawler/mediator`](https://github.com/bangoc/hust.edu.vn-2026/tree/main/crawler/mediator) (repo của lớp) | Service mediator (khối 3, cổng 5200) dùng chung cho cả lớp, tách từ khối mediator của `crawler/core` | Python 3.13, `uv`, Docker |
| [`lucene/`](lucene/README.md) | Lập chỉ mục và tìm kiếm tài liệu với Apache Lucene | JDK 17+, Maven 3.9+ |

Chạy lệnh trong thư mục của từng dự án để các đường dẫn tương đối, cấu hình và dữ liệu đầu ra hoạt động đúng.

## Crawler

`crawler/` có hai phần dùng chung một lõi:

- `crawler/core/`: package `data_pipeline`, gồm lõi các khối (`domain/`, `application/`, `infrastructure/`) và lệnh
  `data-pipeline` (crawl cả năm khối trong một tiến trình, đóng gói corpus, bóc tách lại...).
- `crawler/server/`: package `services`, gồm API HTTP của từng khối (lệnh `services serve <khối>`), `Dockerfile` và
  `compose.yaml` dựng cả năm khối cùng MongoDB.

```bash
cd crawler/core
uv sync
uv run pytest -q
uv run data-pipeline --help
```

Xem [hướng dẫn core](crawler/core/README.md) để cấu hình và chia sẻ corpus, và
[hướng dẫn server](crawler/server/README.md) để chạy các khối bằng Docker
(MongoDB, Kaggle). `lucene/` hiện chưa đọc corpus này; dự kiến
sau này `lucene/` sẽ nhận corpus đã xử lý để lập chỉ mục và tìm kiếm.

### Kiểm tra mã và pre-commit

Mỗi package Python (`crawler/core/`, `crawler/server/`) có cấu hình ruff, pyrefly (strict) và pytest
trong `pyproject.toml` của nó. Hook pre-commit nằm ở thư mục gốc repo, bỏ qua `lucene/` và `crawler/core/data/`;
ruff, pyrefly, uv-lock và pytest (pytest chỉ khi push) chạy cho package có thay đổi. `crawler/server/` dùng lõi của
`crawler/core/`, nên thay đổi trong `crawler/core/` cũng chạy kiểm tra của `crawler/server/`:

```bash
uv run --directory crawler/core pre-commit install
```

## Lucene

```bash
cd lucene
mvn test
mvn package
```

Xem [hướng dẫn Lucene](lucene/README.md) để cài VnCoreNLP và chạy tìm kiếm.
