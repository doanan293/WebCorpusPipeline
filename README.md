# Information Retrieval Monorepo

Kho mã này gồm các dự án sau:

| Thư mục | Dự án | Công cụ |
| --- | --- | --- |
| [`data_pipeline/`](data_pipeline/README.md) | Lõi năm khối thu thập (Điều khiển, Tải về, Đọc nội dung, Tổng hợp, Lưu trữ) và CLI của nhóm | Python 3.13, `uv` |
| [`dich_vu/`](dich_vu/README.md) | Cùng lõi đó chạy thành năm dịch vụ HTTP theo slide của thầy, cộng Mongo | Python 3.13, `uv`, Docker |
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

Xem [hướng dẫn data pipeline](data_pipeline/README.md) để cấu hình và chia sẻ corpus, và
[hướng dẫn dịch vụ](dich_vu/README.md) để chạy các khối bằng Docker
(MongoDB, Kaggle). `lucene/` hiện chưa đọc corpus này; dự kiến
sau này `lucene/` sẽ nhận corpus đã xử lý để lập chỉ mục và tìm kiếm.

`data_pipeline/` là một package Python (`src/data_pipeline`) với lệnh `data-pipeline`. `dich_vu/` là package
`dich_vu` (lệnh `dich-vu serve <khối>`) phụ thuộc `data_pipeline`, có `Dockerfile` và `compose.yaml` dựng cả năm
khối cùng MongoDB.

### Kiểm tra mã và pre-commit

Mỗi package Python (`data_pipeline/`, `dich_vu/`, `dieu_khien/`) có cấu hình ruff, pyrefly (strict) và pytest
trong `pyproject.toml` của nó. Hook pre-commit nằm ở thư mục gốc repo, bỏ qua `lucene/` và `data_pipeline/data/`;
ruff, pyrefly, uv-lock và pytest (pytest chỉ khi push) chạy cho package có thay đổi. `dich_vu/` dùng lõi của
`data_pipeline/`, nên thay đổi trong `data_pipeline/` cũng chạy kiểm tra của `dich_vu/`:

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
