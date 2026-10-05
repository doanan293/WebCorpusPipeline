# Corpus Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hoàn thành yêu cầu bóc tách nội dung, tài liệu, nguồn giới thiệu và đồ thị liên kết; lưu MongoDB local và chia sẻ corpus có phiên bản qua Kaggle.

**Architecture:** Tái sử dụng dữ liệu crawl hiện có làm đầu vào bất biến. Pipeline làm giàu tài liệu dùng inventory và checkpoint SQLite riêng, download streaming song song, parser local trước và LlamaParse `fast` khi không ra chữ. Corpus JSONL độc lập là đầu vào chung của MongoDB và Kaggle.

**Tech Stack:** Python 3.13, uv, SQLite, aiohttp, PyMuPDF, python-docx, openpyxl, xlrd, python-pptx, LibreOffice headless, llama-cloud, PyMongo/GridFS, Docker Compose, Kaggle CLI; crawler Python hiện có.

**Spec:** [Bản thiết kế đầy đủ](../specs/2026-10-05-kaggle-mongodb-corpus-design.md).

## Global Constraints

- Không crawl lại HTML chỉ để tải tài liệu; nguồn `crawler/data/crawl/` giữ nguyên.
- Download mặc định 64 toàn cục, 2 mỗi host, khoảng cách khởi đầu 0,25 giây/host; cho cấu hình lại.
- File hoàn tất được kiểm tra local và bỏ qua trên mạng; file dở chỉ nối khi server trả Range hợp lệ cho cùng nội dung.
- PDF dùng PyMuPDF. XLSX/XLS/PPTX dùng parser native local; PPT cũ chuyển local sang PPTX.
- DOCX dùng python-docx đọc đoạn/bảng; DOC chuyển local sang DOCX bằng LibreOffice.
- Mục tiêu là text chính với thứ tự cơ bản, không tái dựng layout hay schema tọa độ ô/shape. Header/footer Word, notes PowerPoint và adapter XML cho thành phần phức tạp không bắt buộc.
- Word không có text hữu ích thì thử chuyển PDF và đọc PyMuPDF trước khi fallback theo trang; native Word dùng phần document, không gán số trang giả.
- Có chữ/số đọc được thì giữ local, dù ngắn hoặc định dạng xấu. Trang/sheet/slide trắng không gọi API.
- Chỉ fallback phần không ra chữ có nghĩa sang `tier="fast"`, `version="latest"`; không tự nâng tier.
- Đọc `LLAMAPARSE_KEY1`, `LLAMAPARSE_KEY2`, `LLAMAPARSE_KEY3` từ root `.env`; lỗi quota đổi key, rate limit backoff; không log/commit key.
- MongoDB `mongo:8.0`, loopback `127.0.0.1:27017`, volume `mongodb_data`, database `information_retrieval`.
- Kaggle public, publish chủ động, pull đúng phiên bản; payload chỉ gồm dữ liệu đã bóc tách và provenance cần thiết.
- Mọi yêu cầu của giảng viên nằm trong phạm vi; các phần dưới là cách chia việc, không phải các mục bị hoãn.
- Chỉ ghi nhận hoàn thành sau khi có bằng chứng kiểm thử; tác vụ mạng thực tế chưa chạy phải báo rõ.

## Review Focus

1. Chết tiến trình giữa ghi file và commit DB: không mất hoặc nhân bản file hoàn tất — A2/A3/A8.
2. Range hợp lệ về cú pháp nhưng khác nội dung/encoding: không nối hai phiên bản file — A3.
3. Ô Excel bằng 0/công thức chưa có cache, slide có text trong group: không bị coi là trống rồi tính phí cloud — A6.
4. API đã nhận job nhưng client timeout: không submit lại và tính phí hai lần — A7.
5. Duplicate owner ngoài corpus, metadata lớn và đích liên kết chưa crawl: dữ liệu không mất hoặc bị nhập sai quan hệ — C1/C2.

## Kế hoạch thành phần và thứ tự

| Phần | Plan | Đầu ra có thể kiểm thử độc lập |
| --- | --- | --- |
| A | [Download và bóc tách tài liệu](2026-10-05-corpus-download-and-extraction.md) | Chạy trên `final/`, resume download/parse, xuất tài liệu và coverage |
| B | [Nội dung HTML và provenance](2026-10-05-corpus-html-extraction.md) | Thuật toán chọn khối, metadata, liên kết, HTML nguồn tùy chọn và báo cáo đánh giá |
| C | [Corpus, MongoDB và Kaggle](2026-10-05-corpus-storage-and-publishing.md) | Graph, corpus kiểm chứng được, DB local, publish/pull và demo bàn giao |

Thực hiện A1 trước để chốt hợp đồng, ưu tiên A2–A3 vì download/resume là nhu cầu
hiện tại. Hoàn thành A4–A8, B1–B2 rồi C1–C4. B có thể phát triển độc lập sau A1
nếu phương thức thực thi được chọn có delegation; không tự giả định đã được chọn.

## Cấu trúc file chung

- `scripts/data.py`: entry point mỏng gọi `ir_data.cli.main`; không chứa logic nghiệp vụ.
- `scripts/ir_data/`: các module tập trung theo nhiệm vụ ở từng plan A/C.
- Root `pyproject.toml`, `uv.lock`: môi trường công cụ dữ liệu độc lập với crawler.
- `config/data.yaml`: tuning downloader, parser và DB; không chứa secrets.
- `config/kaggle-dataset.json`, `config/corpus.lock.json`: metadata và phiên bản phát hành, được điền giá trị thực trước publish.
- `tests/corpus/`: unit/integration tests của công cụ dữ liệu; không thay pytest root của crawler.
- `compose.yaml`: MongoDB local. Không thêm Tika/Tesseract.
- `docs/data-schema.md`, `docs/document-pipeline.md`, `docs/content-extraction.md`: tài liệu bàn giao.
- `data/enrichment/`: SQLite checkpoint, parts, files, parse cache và báo cáo local; Git bỏ qua.
- `data/corpus/<snapshot_id>/`: bản corpus đã verify; Git bỏ qua.

Root package có base dependencies cho `pull`, `verify`, `import-mongo`; extras
`producer` chứa downloader/parsers/llama-cloud và nguồn local crawler. Base
install không kéo Scrapy/Playwright hoặc LibreOffice vào máy consumer.
Khóa versions thực tế tương thích Python 3.13 trong `uv.lock`, không đoán số
version trong plan. Trước khi khóa phải kiểm tra API SDK LlamaParse thật.

## Hợp đồng CLI và mã thoát

Entry point `ir-data`, chạy từ repo bằng `uv run ir-data ...` sau cài base;
producer dùng `uv run --extra producer ir-data ...`.

| Lệnh | Hợp đồng |
| --- | --- |
| `inventory --source crawler/data/crawl/final --work-dir data/enrichment` | Quét JSONL có cursor; gộp URL, giữ mọi referrer |
| `download --work-dir data/enrichment [--retry-failed]` | Resume mặc định; chỉ tải tài liệu chưa hoàn tất |
| `extract --work-dir data/enrichment [--retry-failed]` | Resume parser, áp dụng local-first và quota fallback |
| `enrich-documents --source ... --work-dir ...` | Inventory → download → extract; dùng cùng checkpoints như chạy riêng |
| `status --work-dir data/enrichment --json` | Số job từng stage, bytes, rate, lỗi, quota-blocked, pending |
| `reconcile-job --work-dir ... --request-id ... --job-id ... --key-label ...` | Đối chiếu job đã nhận nhưng chưa lưu được ID; kiểm tra file/request và scope trước tiếp tục poll |
| `build-graph --source ... --work-dir ... --output ...` | Xuất objects/cạnh và provenance, không request web |
| `package --source ... --work-dir ... --output ...` | Verify và tạo payload bất biến; từ chối stage chưa hoàn tất |
| `verify --corpus ...` | Verify checksum/count/schema/tham chiếu; không cần network |
| `publish --corpus ... --create` hoặc `--update --message ...` | Tạo public/version mới và chỉ khóa phiên bản đã xác minh |
| `pull [--lock config/corpus.lock.json]` | Download đúng version, staging, verify, atomic publish local |
| `import-mongo --corpus ...` | Idempotent theo snapshot; ready chỉ sau verify/import đầy đủ |
| `query --snapshot ... --incoming URL` hoặc `--outgoing URL` | Demo nguồn giới thiệu/đồ thị; chỉ đọc snapshot ready |
| `evaluate-extraction --fixtures ... --output ...` | Báo cáo thuật toán HTML từ plan B |

Exit: `0` hoàn tất không có lỗi nội dung; `2` cấu hình/đầu vào không hợp lệ;
`3` đã xử lý inventory nhưng còn lỗi nguồn/parser kết thúc; `4` chưa hoàn tất
do quota/storage/submission chưa rõ; `130` ngắt bởi người dùng; `1` lỗi nội bộ.
Không đánh đồng exit 3 với 4: package có thể ghi nhận lỗi nguồn cuối cùng,
nhưng không được đóng gói giả hoàn tất khi còn công việc chờ.

## Nghiệm thu toàn bộ

- [ ] Fixture end-to-end: inventory → ngắt/resume download → local/cloud mock → graph → package → MongoDB → query.
- [ ] Chạy lại: file đã tải không có request mới, file đã parse không có lời gọi cloud mới, số DB records không tăng.
- [ ] Báo cáo thuật toán HTML có tập đánh giá giữ lại và ca sai.
- [ ] Inventory/corpus thật được làm giàu tới trạng thái kết thúc; coverage báo rõ tài liệu thất bại và nguyên nhân.
- [ ] Publish public rồi pull chính version vừa publish, verify và import trên DB sạch; nếu thiếu owner/license/credentials, báo bước chưa thực hiện.
- [ ] README hướng dẫn producer/consumer, schema, truy vấn mẫu, resume và giới hạn dữ liệu.

## Trạng thái bàn giao kế hoạch

Đây là kế hoạch, chưa phải code triển khai. User đã yêu cầu lập plan và thêm
download/resume bắt buộc. Trước thực thi, review các plan và chọn cách thực thi.
Đề xuất native/inline vì các thành phần phụ thuộc chặt vào cùng schema và
checkpoint; subagent-driven là lựa chọn nếu muốn review độc lập từng task.
