# Information Retrieval Monorepo

Kho mã này gồm hai dự án độc lập:

| Thư mục | Dự án | Công cụ |
| --- | --- | --- |
| [`data_pipeline/`](data_pipeline/README.md) | Thu thập, tải tài liệu, bóc tách văn bản, lưu trữ và chia sẻ corpus | Python 3.13, `uv` |
| [`lucene/`](lucene/README.md) | Lập chỉ mục và tìm kiếm tài liệu với Apache Lucene | JDK 17+, Maven 3.9+ |

Chạy lệnh trong thư mục của từng dự án để các đường dẫn tương đối, cấu hình và dữ liệu đầu ra hoạt động đúng.

## Data pipeline

```bash
cd data_pipeline
uv sync --extra dev
uv run pytest -q
```

Xem [hướng dẫn data pipeline](data_pipeline/README.md) để cấu hình và chạy crawl.
Các bước download/extract, MongoDB và Kaggle đang được triển khai trong cùng
dự án này. `lucene/` nhận corpus đã xử lý để lập chỉ mục và tìm kiếm.

`data_pipeline/` dùng một `pyproject.toml` và một `uv.lock`. Extra `crawl`
cài crawler, `producer` cài các công cụ tạo corpus, còn base dành cho consumer.
Extra `dev` bao gồm crawler để chạy bộ test hiện có.

## Lucene

```bash
cd lucene
mvn test
mvn package
```

Xem [hướng dẫn Lucene](lucene/README.md) để cài VnCoreNLP và chạy tìm kiếm.
