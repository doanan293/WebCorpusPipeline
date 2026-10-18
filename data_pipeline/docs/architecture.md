# Kiến trúc

## 1. Các khối dịch vụ

`data_pipeline` cài đặt kiến trúc dịch vụ nhỏ trong slide của thầy: Điều khiển, Tải về, Đọc nội dung, Tổng
hợp dữ liệu, Lưu trữ và Mongo, mỗi khối một service HTTP/JSON. Giao diện, luồng dữ liệu, cách chạy và cách
thay khối bằng khối của nhóm khác nằm ở [services.md](services.md).

## 2. Cấu trúc package

Một package `data_pipeline` (trong `src/`) với một lệnh `data-pipeline`, tổ chức theo clean architecture và
**chia theo lớp trước**: ở gốc có bốn lớp. Trong mỗi lớp, code chia theo nghiệp vụ: năm khối `controller`,
`fetcher`, `parser`, `aggregator`, `storage`, cùng `corpus` (đóng gói, Kaggle), `hosts` (`domains audit`) và
`shared` (phần dùng chung).

```mermaid
flowchart TB
    PRES["cli/ và api/<br/>lệnh Typer, service aiohttp"]
    BOOT["bootstrap.py<br/>dựng adapter cho mọi cửa vào"]
    INFRA["infrastructure/<br/>SQLite, aiohttp, Playwright, parser tài liệu, LlamaParse, MongoDB, Kaggle<br/>(cài đặt các port)"]
    APP["application/<br/>use case + ports.py (Protocol)"]
    DOM["domain/<br/>luật thuần, kiểu dữ liệu"]

    PRES --> BOOT
    PRES --> APP
    BOOT --> INFRA
    INFRA -->|"chỉ ports"| APP
    APP --> DOM
    INFRA --> DOM
```

Quy tắc phụ thuộc (kiểm tra tự động bằng `tests/unit/test_architecture.py`):

1. **Lớp chỉ trỏ vào trong:** `domain` không import lớp nào khác; `application` chỉ dùng `domain`;
   `infrastructure` dùng `domain` và chỉ `application/<nghiệp vụ>/ports.py`. `cli/` và `api/` gọi use case và
   `bootstrap.py`, không import `infrastructure`; `cli/` được khởi chạy `api/` (lệnh `serve`). `domain` không
   import module I/O (`sqlite3`, `aiohttp`, `pymongo`, `playwright`, `llama_cloud`, `subprocess`, `os`, ...).
2. **Các khối không import lẫn nhau:** năm khối, `corpus` và `hosts` chỉ dùng phần của chính nó và `shared`.
   Các khối chỉ nói chuyện với nhau qua HTTP. `api/<khối>.py` chỉ dùng nghiệp vụ của khối đó.
3. Mọi module nằm trong `<lớp>/<nghiệp vụ>/`, `cli/`, `api/` hoặc là `bootstrap.py`. Use case nhận adapter qua
   tham số; `bootstrap.py` là nơi duy nhất dựng adapter.

```text
src/data_pipeline/
  domain/
    shared/        url, links (normalize_url), assets (nhận diện tài liệu), protocol (chuỗi và bản ghi /add),
                   hostnames, refs, errors, config
    controller/    models, kinds, scheduling, rotation
    fetcher/       access (cổng đăng nhập/CAPTCHA/soft 404), formats, models
    parser/        extract/ (thuật toán tìm khối nội dung), records, models, merge, cache_key, cloud_pages, key_pool
    aggregator/    link_policy, links, batching, records, content, config
    storage/       documents
    corpus/        manifest, records, kaggle_metadata
    hosts/         classify, canonical, models
  application/<nghiệp vụ>/   use case + ports.py
  infrastructure/
    shared/        settings, env, network, files, http_client
    controller/    sqlite_queue, http_downloader, config_file
    fetcher/       http_getter, browser, playwright_driver, parser_client, config_file
    parser/        parsers/, llamaparse, cache, job_log, aggregator_client, raw_pages, config_file
    aggregator/    sqlite_state, outbox, service_client, config_file
    storage/       mongo
    corpus/        files/corpus_files, mongo, kaggle_cli
    hosts/         http_probe, browser_probe, report_files
  api/             common, controller, fetcher, parser, aggregator, storage
  cli/             main, serve, reextract, corpus, domains, eval, context, errors
  bootstrap.py
```

| Nghiệp vụ | Port (trong `application/<nghiệp vụ>/ports.py`) | Adapter |
|---|---|---|
| `controller` | `UrlQueue`, `Downloader`, `Clock` | `SqliteUrlQueue`, `HttpDownloader` |
| `fetcher` | `PageGetter`, `Renderer`, `ParserClient` | `AiohttpPageGetter`, `PlaywrightRenderer`, `HttpParserClient` |
| `parser` | `LocalParser`, `CloudParser`, `ParseCache`, `CloudJobLog`, `AggregatorClient`, `RawPages` | `LocalParserAdapter`, `LlamaParseCloud`, `FileParseCache`, `LoggedCloudJobs`, `HttpAggregatorClient`, `SqliteRawPages` |
| `aggregator` | `SeenUrls`, `ContentOwners` (`AggregatorState`), `Outbox`, `ServiceClient` | `SqliteAggregatorState`, `JsonlOutbox`, `HttpServiceClient` |
| `storage` | `CorpusStore` | `MongoCorpusStore` |
| `corpus` | `CorpusFiles`, `CorpusDatabase`, `DatasetPublisher` | `LocalCorpusFiles`, `MongoCorpusDatabase`, `KaggleCli` |
| `hosts` | `HostProber`, `BrowserProber`, `AuditReports` | `HttpHostProber`, `PlaywrightBrowserProber`, `FileAuditReports` |

Test có cùng cấu trúc (`tests/unit/<lớp>/<nghiệp vụ>/`) và dùng bản giả trong `tests/fakes/`;
`tests/fakes/test_fakes_satisfy_ports.py` kiểm tra mỗi bản giả vẫn khớp port của nó.
`tests/integration/services/test_end_to_end.py` dựng cả năm khối trên cổng ngẫu nhiên và cho chúng thu thập
một website giả lập cục bộ.

## 3. Yêu cầu của thầy và nơi đáp ứng

| # | Yêu cầu | Nơi đáp ứng | Collection Mongo |
|---|---|---|---|
| Y1 | Thuật toán tìm khối nội dung | `domain/parser/extract/`, báo cáo `data-pipeline eval extraction` | |
| Y2 | Trang: tiêu đề, ngày đăng, tác giả, nội dung | khối Đọc nội dung, bản ghi `html` | `html` |
| Y3 | Tài liệu: nội dung văn bản | khối Đọc nội dung (parser cục bộ, LlamaParse) | `other` |
| Y4, Y6 | Đồ thị nguồn → đích kèm văn bản liên kết | `out-links` của bản ghi, khối Lưu trữ | `graph` |
| Y5 | Ảnh của trang | trường `images` của bản ghi `html` | `html` |
| Y7 | MongoDB theo lược đồ của slide | khối Lưu trữ, [data-schema.md](data-schema.md) | `html`, `other`, `graph` |
| Y8 | Chia sẻ dữ liệu của project | `corpus package/publish/pull`, `config/corpus.lock.json` ([corpus.md](corpus.md)) | |

## 4. Kiểm tra host (`domains audit`)

Chạy trước khi bật các khối để biết host nào còn trả nội dung công khai. Kết quả là `config/domains/active.txt`:
Tổng hợp dữ liệu lấy trang chủ của các host trong đó làm URL mầm (xem [domains.md](domains.md)). Tiến độ in ra stderr.

```mermaid
flowchart TD
    ALL["config/domains/all.txt<br/>mọi hostname ứng viên"]
    PROBE["Thăm dò song song (--workers)<br/>DNS an toàn: chặn IP private, loopback;<br/>HTTPS rồi HTTP, có retry (--retries)"]
    HTTP{"Mã HTTP"}
    BADHTTP["Không hoạt động<br/>access_denied, login_only, rate_limited,<br/>not_found, gone, server_error,<br/>client_error, redirect_error"]
    REDIR{"Chuyển hướng<br/>(HTTP, meta, JS)?"}
    EXT["external_redirect<br/>ra ngoài hust.edu.vn"]
    BODY{"Nội dung trang"}
    REJ["Không hoạt động<br/>empty_response, trang mặc định,<br/>endpoint dịch vụ, non_production,<br/>trang đăng nhập"]
    SHELL["javascript_shell<br/>trang chỉ là vỏ JS"]
    BROWSER{"Playwright<br/>(bỏ bằng --no-browser)"}
    BERR["browser_error<br/>trình duyệt không khởi động:<br/>giữ kết quả HTTP, ghi error"]
    PUBLIC["public_response<br/>host hoạt động"]
    CANON["Gộp chuyển hướng<br/>host trỏ sang host khác: redirect_duplicate"]
    DEDUP["Gộp trùng nội dung<br/>cùng hash trang: content_duplicate"]
    OUT["Ghi lại all.txt, active.txt (host active),<br/>status.jsonl (active, reason, status,<br/>final_url, title cho mỗi host)"]
    ERR["probe_error, network_error,<br/>dns_error, non_public_address<br/>một host lỗi không dừng cả lượt"]

    ALL --> PROBE
    PROBE -- "ngoại lệ, mất mạng" --> ERR
    PROBE --> HTTP
    HTTP -- "401, 403, 429, 4xx, 5xx" --> BADHTTP
    HTTP -- "2xx, 3xx" --> REDIR
    REDIR -- "ra ngoài phạm vi" --> EXT
    REDIR -- "trong phạm vi hoặc không" --> BODY
    BODY -- "bị loại" --> REJ
    BODY -- "vỏ JS" --> SHELL --> BROWSER
    BROWSER -- "render ra nội dung" --> PUBLIC
    BROWSER -- "vẫn rỗng hoặc bị chặn" --> REJ
    BROWSER -- "không khởi động được" --> BERR --> OUT
    BODY -- "có nội dung" --> PUBLIC
    PUBLIC --> CANON --> DEDUP --> OUT
    BADHTTP --> OUT
    EXT --> OUT
    REJ --> OUT
    ERR --> OUT

    classDef step fill:#dbeafe,stroke:#2563eb,color:#0f172a;
    classDef out fill:#dcfce7,stroke:#16a34a,color:#0f172a;
    classDef bad fill:#fee2e2,stroke:#dc2626,color:#0f172a;
    class ALL,PROBE,HTTP,REDIR,BODY,SHELL,BROWSER,CANON,DEDUP step;
    class PUBLIC,OUT out;
    class BADHTTP,EXT,REJ,ERR,BERR bad;
```

## 5. Chấm điểm trích xuất (`eval extraction`)

Đánh giá thuật toán tìm khối nội dung (Y1) trên các trang mẫu có đáp án, không cần mạng.

```mermaid
flowchart LR
    FIX["--fixtures thư mục<br/>mỗi mẫu: tên.html và tên.json<br/>(url, title, text mong đợi)"]
    RUN["analyze_article<br/>cùng mã trích xuất với khối Đọc nội dung"]
    SCORE["So token của text<br/>precision, recall, F1;<br/>title_ok: tiêu đề khớp đúng"]
    OUT["--output báo cáo JSON<br/>fixtures, rows, mean_f1"]
    FIX --> RUN --> SCORE --> OUT

    classDef step fill:#dbeafe,stroke:#2563eb,color:#0f172a;
    classDef out fill:#dcfce7,stroke:#16a34a,color:#0f172a;
    class FIX,RUN,SCORE step;
    class OUT out;
```
