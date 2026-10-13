# Data pipeline

Thu thập, xử lý và chia sẻ corpus các trang web và tài liệu của Đại học Bách khoa Hà Nội (HUST).
Project này lo phần dữ liệu: crawl, tải và bóc tách tài liệu, đồ thị liên kết, MongoDB và Kaggle. Lập
chỉ mục và tìm kiếm nằm ở project [`lucene/`](../lucene/README.md). Mọi lệnh chạy trong `data_pipeline/`.

```mermaid
flowchart LR
    A["crawl<br/>trang web"] --> B["enrich<br/>tải + bóc tách tài liệu"]
    B --> C["corpus<br/>đóng gói, verify"]
    C --> D["MongoDB"]
    C --> E["Kaggle"]
    C --> F["lucene/"]
```

## Cài đặt

Cần Python 3.13 và [uv](https://docs.astral.sh/uv/).

```bash
uv sync --extra enrich                 # crawler + bóc tách tài liệu (đủ cho mọi lệnh)
uv run playwright install chromium     # trình duyệt cho trang JavaScript
cp .env.example .env                   # rồi điền CRAWLER_CONTACT và các khoá API
```

Extra của dependency: không có (nền) cho người dùng corpus (`corpus pull|verify|import-mongo|query`);
`--extra crawl` cho crawler; `--extra enrich` thêm bóc tách tài liệu (gồm cả `crawl`). Lệnh cần extra chưa cài sẽ
báo cách cài. Nhóm `dev` (pytest, ruff, pyrefly, pre-commit) được `uv sync` cài mặc định.

## Bắt đầu nhanh

Một lệnh `data-pipeline` với các nhóm con; xem `uv run data-pipeline --help`.

```bash
# 1. Crawl (danh sách host trong config/domains/active.txt)
uv run data-pipeline crawl run -i config/domains/active.txt -o data/crawl
uv run data-pipeline crawl run -i config/domains/active.txt -o data/crawl --refresh   # cập nhật tăng dần

# 2. Tải và bóc tách tài liệu (đọc data/crawl/final, ghi data/enrichment)
uv run data-pipeline enrich run
uv run data-pipeline enrich status --json

# 3. Đóng gói corpus và kiểm tra
uv run data-pipeline corpus package --output data/corpus/latest
uv run data-pipeline corpus verify --corpus data/corpus/latest

# 4. Chia sẻ
docker compose up -d mongodb
uv run data-pipeline corpus import-mongo --corpus data/corpus/latest
uv run data-pipeline corpus publish --corpus data/corpus/latest   # lên Kaggle

# Máy mới: lấy bản đã công bố thay vì crawl lại
uv run data-pipeline corpus pull && uv run data-pipeline corpus import-mongo --corpus data/corpus/pulled
```

| Nhóm lệnh | Việc làm |
|---|---|
| `crawl run`, `crawl discover` | crawl chính (resume, refresh) và chẩn đoán sitemap |
| `enrich inventory\|download\|extract\|run\|status\|export\|reconcile-job` | tải và bóc tách tài liệu |
| `corpus build-graph\|package\|verify\|import-mongo\|query\|publish\|pull` | corpus, MongoDB, Kaggle |
| `domains audit` | kiểm tra host nào còn trả nội dung |
| `eval extraction` | chấm điểm thuật toán trích xuất nội dung |

## Cấu hình

| Vị trí | Nội dung |
|---|---|
| `.env` | `CRAWLER_CONTACT` (bắt buộc để crawl), `LLAMAPARSE_KEY1..n`, `KAGGLE_API_TOKEN`. Không commit |
| `config/crawler.yaml` | chính sách và hiệu năng crawler; kiểm tra chặt, phải đủ schema. Cờ CLI ghi đè khi chạy |
| `config/pipeline.yaml` | tải xuống, parser, MongoDB |
| `config/domains/` | `all.txt`, `active.txt`, `status.jsonl` (xem [docs/domains.md](docs/domains.md)) |
| `config/kaggle-dataset.json`, `config/corpus.lock.json` | metadata dataset Kaggle và bản đã ghim |
| `data/` | toàn bộ dữ liệu sinh ra: `crawl/`, `enrichment/`, `corpus/` (không đưa vào git) |

Biến môi trường đã export trong shell luôn thắng giá trị trong `.env`. Đường dẫn mặc định tính theo thư mục
project, không phụ thuộc thư mục bạn đang đứng; đường dẫn bạn gõ là tương đối theo thư mục hiện tại.

## Phát triển

```bash
uv sync                              # nhóm dev gồm cả extras crawl và enrich
uv run pytest -q                     # bỏ qua test hiệu năng (marker `performance`)
uv run ruff check . && uv run ruff format --check .
uv run pyrefly check                 # kiểm tra kiểu (preset strict)
uv run pre-commit install            # cài hook (chạy ruff, pyrefly khi commit; pytest khi push)
```

Hook `pre-commit` nằm ở thư mục gốc repo và chỉ chạy cho thay đổi trong `data_pipeline/`. Cấu trúc package và
quy tắc phụ thuộc được kiểm tra tự động (`tests/unit/test_architecture.py`).

## Tài liệu

| Tài liệu | Nội dung |
|---|---|
| [docs/architecture.md](docs/architecture.md) | cấu trúc package, sơ đồ luồng crawl và luồng xử lý dữ liệu |
| [docs/crawl.md](docs/crawl.md) | seed, phạm vi, chính sách, frontier, resume, refresh, mã thoát, trích xuất HTML |
| [docs/enrich.md](docs/enrich.md) | tải, bóc tách tài liệu, cập nhật sau refresh |
| [docs/corpus.md](docs/corpus.md) | đóng gói, MongoDB, Kaggle |
| [docs/domains.md](docs/domains.md) | danh sách host và kiểm tra host |
| [docs/data-schema.md](docs/data-schema.md) | schema mọi file đầu ra |
