# Information Retrieval Monorepo

Kho mã này gồm hai dự án độc lập:

| Thư mục | Dự án | Công cụ |
| --- | --- | --- |
| [`data_pipeline/`](data_pipeline/README.md) | Hệ thống thu thập theo kiến trúc dịch vụ nhỏ (Điều khiển, Tải về, Đọc nội dung, Tổng hợp, Lưu trữ, Mongo) | Python 3.13, `uv`, Docker |
| [`dieu_khien/`](dieu_khien/README.md) | Service Điều khiển (khối 3, cổng 5200) dùng chung cho cả lớp, copy từ khối Điều khiển của `data_pipeline` | Python 3.13, `uv`, Docker |
| [`lucene/`](lucene/README.md) | Lập chỉ mục và tìm kiếm tài liệu với Apache Lucene | JDK 17+, Maven 3.9+ |

Chạy lệnh trong thư mục của từng dự án để các đường dẫn tương đối, cấu hình và dữ liệu đầu ra hoạt động đúng.

## Data pipeline

```bash
cd data_pipeline
uv sync
uv run pytest -q
uv run data-pipeline --help
```

Xem [hướng dẫn data pipeline](data_pipeline/README.md) để chạy các khối bằng Docker, cấu hình và chia sẻ corpus
(MongoDB, Kaggle). `lucene/` hiện chưa đọc corpus này; dự kiến
sau này `lucene/` sẽ nhận corpus đã xử lý để lập chỉ mục và tìm kiếm.

`data_pipeline/` là một package Python (`src/data_pipeline`) với một lệnh `data-pipeline` (`data-pipeline serve
<khối>` chạy một khối), một `pyproject.toml`, một `uv.lock`, và `compose.yaml` dựng cả năm khối cùng MongoDB.

### Kiểm tra mã và pre-commit

Cấu hình ruff, pyrefly (strict) và pytest nằm trong `data_pipeline/pyproject.toml`. Hook pre-commit nằm ở thư
mục gốc repo, bỏ qua `lucene/` và `data_pipeline/data/`; ruff, pyrefly, uv-lock và pytest (pytest chỉ khi push) chỉ
chạy cho thay đổi trong `data_pipeline/`:

```bash
uv run --directory data_pipeline pre-commit install
```

## Lucene

```bash
cd lucene
mvn test
mvn package
```

Xem [hướng dẫn Lucene](lucene/README.md) để cài VnCoreNLP và chạy tìm kiếm.
