# Kiến trúc và luồng dữ liệu

## 0. Cấu trúc package

Một package `data_pipeline` (trong `src/`) với một lệnh `data-pipeline`. Cây thư mục phản ánh
luồng dữ liệu; mỗi package con ứng với một nhóm lệnh:

```mermaid
flowchart LR
    CLI["cli<br/>data-pipeline crawl | enrich | corpus | domains | eval"]
    CRAWL["crawl<br/>Scrapy: frontier, spider, extract, state"]
    ENRICH["enrich<br/>inventory, download, parsing (PDF, Office, LlamaParse)"]
    CORPUS["corpus<br/>package, verify, graph, MongoDB, Kaggle"]
    DOMAINS["domains<br/>kiểm tra host còn sống"]
    CORE["core<br/>đường dẫn, .env, settings, chính sách URL/mạng"]

    CLI --> CRAWL
    CLI --> ENRICH
    CLI --> CORPUS
    CLI --> DOMAINS
    CORPUS --> ENRICH
    CRAWL --> CORE
    ENRICH --> CORE
    CORPUS --> CORE
    DOMAINS --> CORE
```

Quy tắc phụ thuộc (kiểm tra tự động bằng `tests/unit/test_architecture.py`):

- `core` không import package nào khác; `crawl`, `enrich`, `corpus`, `domains` không import `cli`.
- `crawl`, `enrich`, `domains` độc lập với nhau; `corpus` chỉ được dùng `enrich`.
- Trong `crawl`: `run` → `spider` → `state`/`extract`/`frontier`/`runtime`/`output`; `spider` không
  import `run`; `state` không import `run` hay `spider`; `extract` chỉ phụ thuộc `core`.

Chi tiết từng package con:

| Package | Nội dung |
|---|---|
| `crawl/run` | các chế độ chạy: `full` (crawl từ đầu, `--resume`), `refresh`, `reuse`, `discovery`; mã thoát |
| `crawl/spider` | `UnifiedSpider` và các bộ xử lý: `requests`, `html`, `assets`, `refresh`, `routing` |
| `crawl/state` | SQLite `CrawlState`: `store`, `requeue`, `discovery`, `export`, `resume`, `refresh_store` |
| `crawl/extract` | bóc tách HTML: `content`, `metadata`, `sanitize`, `blocks`, `assets`, `links`, `article` |
| `crawl/frontier` | frontier, chính sách link, sitemap, seed, chính sách tải asset |
| `crawl/runtime` | cấu hình Scrapy, middleware, điều chỉnh đồng thời thích ứng, tiến độ |
| `enrich` | `inventory` → `download` → `parsing` (cục bộ và LlamaParse) → `export`; `state` SQLite |
| `corpus` | `package` (đóng gói, verify), `graph` (`links.jsonl`, `images.jsonl`), `mongo`, `kaggle` (publish, pull), `query` |
| `domains` | `audit`, `probe`, `browser`: thăm dò host và viết lại danh sách host |
| `core` | `paths`, `env`, `settings`, `hostnames`; `policies`: chuẩn hoá URL, chính sách truy cập và mạng |
| `cli` | lệnh `data-pipeline` (Typer): `crawl`, `enrich`, `corpus`, `domains`, `eval` |

Các sơ đồ:

- [Tổng quan](#tổng-quan-các-giai-đoạn): thứ tự các lệnh từ danh sách host đến corpus.
- [Sơ đồ 1](#1-luồng-crawl-từ-đầu-và-crawl-tăng-dần): một lượt crawl, từ đầu và refresh.
- [Sơ đồ 1b](#1b-các-chế-độ-chạy-crawl): chọn chế độ chạy: mới, resume, retry, refresh, reuse, discover.
- [Sơ đồ 2](#2-luồng-xử-lý-dữ-liệu-theo-yêu-cầu-của-thầy): từ `final/` đến corpus, MongoDB và Kaggle.
- [Sơ đồ 3](#3-kiểm-tra-host-domains-audit): kiểm tra host, chạy trước crawl.
- [Sơ đồ 4](#4-chấm-điểm-trích-xuất-eval-extraction): chấm điểm thuật toán trích xuất.

Sơ đồ 1 **sinh ra** `final/` (và `changes/<run_id>/` khi refresh), sơ đồ 2 **tiêu thụ** chúng để tạo corpus theo
yêu cầu của thầy.

## Tổng quan các giai đoạn

Nét liền là luồng chính, nét đứt là bước tuỳ chọn hoặc lặp lại.

```mermaid
flowchart LR
    ALL["config/domains/all.txt<br/>host ứng viên"]
    AUDIT["domains audit<br/>(sơ đồ 3)"]
    ACTIVE["config/domains/active.txt<br/>status.jsonl"]
    DISC["crawl discover<br/>chỉ robots và sitemap"]
    CRAWL["crawl run<br/>(sơ đồ 1, 1b)"]
    FINAL["data/crawl/final/<br/>changes/run_id/"]
    REFRESH["crawl run --refresh"]
    ENRICH["enrich run<br/>inventory, download, extract"]
    PACK["corpus package<br/>corpus verify"]
    CORPUS["data/corpus/latest/"]
    MONGO["corpus import-mongo<br/>corpus query"]
    KAGGLE["corpus publish<br/>corpus pull"]
    LUCENE["lucene/"]
    EVAL["eval extraction<br/>(sơ đồ 4)"]

    ALL --> AUDIT --> ACTIVE --> CRAWL --> FINAL --> ENRICH --> PACK --> CORPUS
    ACTIVE -.-> DISC
    FINAL -.-> REFRESH -.-> FINAL
    CORPUS --> MONGO
    CORPUS -.-> KAGGLE
    KAGGLE -. "máy mới" .-> MONGO
    CORPUS --> LUCENE
    EVAL -. "đánh giá crawl/extract" .-> CRAWL
```

---

## 1. Luồng crawl: từ đầu và crawl tăng dần

Hai chế độ của `data-pipeline crawl run` **dùng chung** khối Frontier, Tải trang và Xử lý trang; khác
nhau ở cách chọn URL đầu vào và cách xử lý URL đã biết.

- Màu xanh dương: crawl từ đầu.
- Màu cam: refresh (`--refresh`).
- Màu xám: khối dùng chung.
- Màu xanh lá: đầu ra.
- Màu đỏ: bị loại, lỗi hoặc từ chối.

```mermaid
flowchart TD
    %% ============ CRAWL TỪ ĐẦU ============
    subgraph FULL["Crawl từ đầu (data-pipeline crawl run)"]
        direction TB
        SEEDS["Danh sách seed<br/>config/domains/active.txt, 77 host"]
        BOOT["Khởi động mỗi host<br/>robots.txt, 3 sitemap, trang chủ"]
        SEEDS --> BOOT
    end

    %% ============ KHỐI DÙNG CHUNG ============
    subgraph SHARED["Khối dùng chung"]
        direction TB
        FRONTIER{"Frontier.consider<br/>chuẩn hoá URL"}
        REJECT["Từ chối<br/>ngoài scope, bẫy lịch/lọc/login,<br/>vượt giới hạn host hoặc tổng"]
        KNOWN["record_only<br/>URL đã có trong url_records<br/>KHÔNG fetch"]
        FETCH["Tải trang<br/>2 request/host, AutoThrottle,<br/>Playwright khi trang JS"]
        KIND{"Loại response"}
        ROBOTS["robots.txt, sitemap<br/>lấy danh sách URL"]
        GATE{"Cổng truy cập<br/>login, CAPTCHA, soft 404, rỗng"}
        EXTRACT["Trích xuất bài<br/>chọn khối nội dung, title, ngày đăng,<br/>tác giả, text, links, assets,<br/>content_id = sha256 của text"]
        DUP{"content_id đã có chủ?"}
        EXPAND["Phân loại link trong trang<br/>nội dung, phân trang, tài nguyên"]
        PAGE["Phân trang theo route family<br/>đi tiếp khi còn link mới"]
        ASSET{"Tài nguyên<br/>chế độ --assets?"}
        ASSETREF["content-only (mặc định)<br/>chỉ ghi tham chiếu:<br/>discovered_not_downloaded"]
        ASSETDL["all<br/>kiểm tra DNS an toàn, kích thước,<br/>hạn mức dung lượng;<br/>lưu files/ theo sha256 nội dung,<br/>ghi files.jsonl, file_saved"]
        ASSET -- "content-only" --> ASSETREF
        ASSET -- "all" --> ASSETDL
        ASSETDL --> STATE
        STATE[("SQLite state/index.sqlite3<br/>url_records, articles,<br/>content_owner")]

        FRONTIER -- "bị loại" --> REJECT
        FRONTIER -- "đã biết" --> KNOWN
        FRONTIER -- "URL mới: lên lịch" --> FETCH
        FETCH --> KIND
        KIND -- "robots, sitemap" --> ROBOTS
        KIND -- "HTML" --> GATE
        KIND -- "tệp nhị phân" --> ASSET
        ROBOTS -- "URL từ sitemap" --> FRONTIER
        GATE -- "bị chặn" --> SKIPPED["Ghi skipped hoặc failed<br/>không mở rộng link"]
        GATE -- "công khai" --> EXTRACT
        EXTRACT --> DUP
        DUP -- "trùng: duplicate_of,<br/>không mở rộng" --> STATE
        DUP -- "mới" --> STATE
        DUP -- "mới" --> EXPAND
        EXPAND -- "link nội dung" --> FRONTIER
        EXPAND -- "link phân trang" --> PAGE
        PAGE -- "trang kế" --> FRONTIER
        EXPAND -- "tệp đính kèm" --> ASSET
    end

    BOOT --> FRONTIER

    %% ============ KẾT THÚC CRAWL TỪ ĐẦU ============
    EMPTY{"Hàng đợi rỗng?"}
    FETCH -.-> EMPTY
    EMPTY -- "xong" --> EXPORT["Xuất kết quả<br/>urls.txt, articles.jsonl,<br/>url_records.jsonl, errors.jsonl,<br/>files.jsonl, manifest.json, final/"]
    EXPORT --> EXIT{"Mã thoát"}
    EXIT -- "0: sạch<br/>3: có URL lỗi<br/>4: chạm giới hạn" --> EXPORTED["Crawl kết thúc"]
    EMPTY -- "Ctrl-C" --> FINTR["interrupted, exit 130<br/>chạy lại với --resume (sơ đồ 1b)"]

    %% ============ REFRESH ============
    subgraph REFRESH["Refresh (data-pipeline crawl run --refresh)"]
        direction TB
        PRE{"Kiểm tra điều kiện<br/>crawl gốc đã kết thúc,<br/>không bị khoá, cờ hợp lệ?"}
        CONT{"Có run đang dở?"}
        BACKUP["Snapshot index sang<br/>archive/pre-refresh-run_id/"]
        SELECT["Chọn tập refresh<br/>trang chủ, sitemap, navigation,<br/>đầu danh mục phân trang,<br/>bài gần đây"]
        TARGETS[("refresh_runs<br/>refresh_targets")]
        RREQ["Request có điều kiện<br/>If-None-Match, If-Modified-Since"]
        RRESP{"Kết quả fetch lại"}
        R304["304 không đổi<br/>không trích xuất, không xét link"]
        RFAIL["Lỗi, 5xx, soft 404, login<br/>GIỮ NGUYÊN bài cũ<br/>chỉ ghi lỗi vào target"]
        RCMP{"So content_id<br/>mới với cũ"}
        RSAME["unchanged<br/>chỉ ghi last_checked_run"]
        RMOD["modified<br/>chuyển quyền chủ nội dung,<br/>ghi đè bài, ghi change"]
        RNEW["new<br/>URL chưa từng thấy,<br/>đóng dấu first_seen_run, ghi change"]
        RPAGE["Phân trang trong refresh<br/>đi tiếp CHỈ KHI trang có link mới,<br/>dừng ở trang trống đầu tiên"]

        PRE -- "không" --> PREFAIL["Từ chối, exit 2<br/>không sửa state"]
        PRE -- "có" --> CONT
        CONT -- "có" --> TARGETS
        CONT -- "không" --> BACKUP --> SELECT --> TARGETS
        TARGETS --> RREQ --> RRESP
        RRESP -- "304" --> R304
        RRESP -- "lỗi hoặc bị chặn" --> RFAIL
        RRESP -- "200" --> RCMP
        RCMP -- "giống" --> RSAME
        RCMP -- "khác" --> RMOD
    end

    EXPORT -. "crawl đã kết thúc" .-> PRE
    RSAME -- "vẫn xét link" --> EXPAND
    RMOD --> EXPAND
    EXPAND -- "link mới sinh ra URL mới" --> RNEW
    RNEW -. "crawl như URL mới ở trên" .-> FETCH
    RMOD --> STATE
    PAGE -. "trong refresh" .-> RPAGE
    RPAGE -- "trang kế đã biết:<br/>fetch lại như target" --> RREQ

    %% ============ KẾT THÚC REFRESH ============
    RDONE{"Hết target và<br/>hết URL mới?"}
    RFAIL --> RDONE
    R304 --> RDONE
    RNEW --> RDONE
    RDONE -- "xong" --> CHANGES["changes/run_id/<br/>changes.jsonl, articles.jsonl,<br/>summary.json"]
    RDONE -- "xong" --> FINAL["Tạo lại final/<br/>README.json có crawl_id,<br/>last_refresh_run_id"]
    RDONE -- "Ctrl-C" --> INTR["Đánh dấu interrupted, exit 130<br/>chạy lại lệnh để tiếp tục cùng run"]
    INTR -.-> CONT

    %% ============ SANG SƠ ĐỒ 2 ============
    NEXT["Đầu vào của sơ đồ 2<br/>xử lý dữ liệu"]
    EXPORT --> NEXT
    CHANGES -- "data-pipeline enrich inventory --source changes/run_id" --> NEXT
    FINAL -- "data-pipeline corpus package --source final" --> NEXT

    %% ============ MÀU ============
    classDef full fill:#dbeafe,stroke:#2563eb,color:#0f172a;
    classDef refresh fill:#ffedd5,stroke:#ea580c,color:#0f172a;
    classDef shared fill:#f1f5f9,stroke:#64748b,color:#0f172a;
    classDef out fill:#dcfce7,stroke:#16a34a,color:#0f172a;
    classDef bad fill:#fee2e2,stroke:#dc2626,color:#0f172a;

    class SEEDS,BOOT,EMPTY,EXIT,FINTR full;
    class PRE,CONT,BACKUP,SELECT,TARGETS,RREQ,RRESP,R304,RCMP,RSAME,RMOD,RNEW,RPAGE,RDONE,INTR refresh;
    class FRONTIER,KNOWN,FETCH,KIND,ROBOTS,GATE,EXTRACT,DUP,EXPAND,PAGE,ASSET,ASSETREF,ASSETDL,STATE shared;
    class EXPORT,EXPORTED,CHANGES,FINAL,NEXT out;
    class REJECT,SKIPPED,RFAIL,PREFAIL bad;
```

### Điểm cần nhớ (crawl)

| Câu hỏi | Trả lời ngắn |
|---|---|
| Làm sao không fetch lại bài cũ? | `Frontier.consider` trả `record_only` cho URL chuẩn hoá đã có trong `url_records`. Chỉ tập hub và bài gần đây của refresh được fetch lại, bằng request riêng. |
| Làm sao biết bài bị sửa? | So `content_id` (sha256 của văn bản trích xuất) trước và sau khi fetch lại. Header `ETag`/`Last-Modified` giúp hub không đổi nhận 304. |
| Bài cũ có bị mất khi fetch lại lỗi? | Không. Lỗi, 5xx, soft 404, login chỉ ghi vào `refresh_targets`; bản cũ giữ nguyên. |
| Khi nào dừng? | Hàng đợi rỗng: hết target và không còn URL mới. Phân trang dừng ở trang đầu tiên không có link mới. Các giới hạn host/tổng vẫn là cầu chì. |
| Đầu ra của refresh? | `changes/<run_id>/` (`changes.jsonl`, `articles.jsonl`, `summary.json`) và `final/` được tạo lại đầy đủ. |

## 1b. Các chế độ chạy crawl

`crawl run` kiểm tra cờ và state đã lưu trước khi vào sơ đồ 1. Mọi lỗi kiểm tra đều thoát mã 2 và không sửa state.
`crawl discover` là lệnh riêng, chỉ đọc robots và sitemap.

```mermaid
flowchart TD
    START["data-pipeline crawl run<br/>-i active.txt -o output"]
    CONTACT{"CRAWLER_CONTACT hợp lệ,<br/>config/crawler.yaml đủ schema?"}
    FLAGS{"Cờ chế độ"}
    BAD["Từ chối, exit 2<br/>không sửa state"]

    START --> CONTACT
    CONTACT -- "không" --> BAD
    CONTACT -- "có" --> FLAGS
    FLAGS -- "--refresh kèm --resume,<br/>--reuse-content-from hoặc --retry-*;<br/>--retry-* thiếu --resume" --> BAD

    %% ===== MỚI =====
    FLAGS -- "không có cờ" --> NEWCHK{"output/state<br/>đã tồn tại?"}
    NEWCHK -- "có" --> BAD
    NEWCHK -- "chưa" --> NEWRUN["Tạo state mới<br/>ghi input_sha256, semantic_config,<br/>Scrapy JOBDIR"]

    %% ===== RESUME =====
    FLAGS -- "--resume" --> RESCHK{"Cùng input_sha256, phase,<br/>state_version, semantic_config?<br/>run.lock không bị giữ?"}
    RESCHK -- "không" --> BAD
    RESCHK -- "có" --> RETRY{"Có cờ --retry-*?"}
    RETRY -- "--retry-failed" --> RQ1["Xếp lại lỗi mạng/timeout cuối<br/>(giữ lỗi HTTP)"]
    RETRY -- "--retry-truncated" --> RQ2["Xếp lại query_variant_limit<br/>nay là nội dung hoặc phân trang"]
    RETRY -- "--retry-access-gates" --> RQ3["Xếp lại trang bị bỏ vì CAPTCHA"]
    RETRY -- "--retry-policy-skips" --> RQ4["Chuyển chính sách truy cập mới,<br/>xếp lại mục bỏ qua do chính sách,<br/>host không có nội dung"]
    RETRY -- "không" --> REBUILD
    RQ1 --> REBUILD
    RQ2 --> REBUILD
    RQ3 --> REBUILD
    RQ4 --> REBUILD
    REBUILD["Bỏ JOBDIR cũ, dựng hàng đợi<br/>từ url_records còn scheduled;<br/>URL đã xong không tải lại"]

    %% ===== REFRESH =====
    FLAGS -- "--refresh" --> REFCHK{"Crawl gốc đã kết thúc,<br/>không bị khoá?"}
    REFCHK -- "không" --> BAD
    REFCHK -- "có" --> REFRUN["Phần Refresh của sơ đồ 1"]

    %% ===== REUSE =====
    FLAGS -- "--assets all<br/>--reuse-content-from nguồn" --> REUSECHK{"Nguồn content-only đã xong,<br/>cùng input, không đang chạy;<br/>output chưa tồn tại?"}
    REUSECHK -- "không" --> BAD
    REUSECHK -- "có" --> REUSE["Sao chép bài và chỉ mục từ nguồn (chỉ đọc),<br/>chỉ tải các tài nguyên đã ghi;<br/>không crawl lại HTML hay sitemap"]

    LOOP["Vòng crawl của sơ đồ 1<br/>Frontier, Tải trang, Xử lý trang"]
    NEWRUN --> LOOP
    REBUILD --> LOOP
    REUSE --> LOOP

    %% ===== DISCOVER =====
    DSTART["data-pipeline crawl discover<br/>-i active.txt -o data/discovery"]
    DPROBE["Mỗi host: robots.txt và các sitemap ứng viên,<br/>mở rộng sitemap index, .xml.gz;<br/>không theo link HTML, không trích xuất"]
    DMODE["discovery.hosts mode:<br/>sitemap, sitemap_partial, no_sitemap, exact"]
    DOUT["urls.txt (bảng targets), errors.jsonl,<br/>manifest.json phase discover, state/"]
    DSTART --> DPROBE --> DMODE --> DOUT

    classDef run fill:#dbeafe,stroke:#2563eb,color:#0f172a;
    classDef retry fill:#ffedd5,stroke:#ea580c,color:#0f172a;
    classDef out fill:#dcfce7,stroke:#16a34a,color:#0f172a;
    classDef bad fill:#fee2e2,stroke:#dc2626,color:#0f172a;
    classDef disc fill:#ede9fe,stroke:#7c3aed,color:#0f172a;

    class START,CONTACT,FLAGS,NEWCHK,NEWRUN,RESCHK,REBUILD,REFCHK,REFRUN,REUSECHK,REUSE,LOOP run;
    class RETRY,RQ1,RQ2,RQ3,RQ4 retry;
    class BAD bad;
    class DSTART,DPROBE,DMODE disc;
    class DOUT out;
```

| Chế độ | Khi nào dùng |
|---|---|
| không cờ | crawl lần đầu vào một output mới |
| `--resume` | tiếp tục sau Ctrl-C (exit 130) hoặc sự cố; có thể thêm một hoặc nhiều `--retry-*` |
| `--refresh` | cập nhật tăng dần sau khi crawl đã kết thúc |
| `--assets all --reuse-content-from` | nâng một crawl `content-only` đã xong thành bản có tệp tài nguyên |
| `crawl discover` | chẩn đoán sitemap: xem host nào có sitemap và sitemap liệt kê bao nhiêu URL |

---

## 2. Luồng xử lý dữ liệu theo yêu cầu của thầy

Đầu vào là `final/` (và `changes/<run_id>/` sau mỗi lần refresh). Đầu ra là corpus có phiên bản,
được nhập vào MongoDB local và chia sẻ qua Kaggle. Mỗi yêu cầu của thầy là một nhánh có số
thứ tự, và kết thúc ở collection tương ứng.

Yêu cầu của thầy:

| # | Yêu cầu | Đầu ra | Collection Mongo |
|---|---|---|---|
| Y1 | Thuật toán tìm khối nội dung | mã `crawl/extract/`, báo cáo từ `data-pipeline eval extraction` | không có (nằm trong crawl) |
| Y2 | Trang: tiêu đề, ngày đăng, tác giả, nội dung | `articles.jsonl` | `pages` |
| Y3 | Tài liệu: nội dung văn bản | `documents.jsonl` | `documents` |
| Y4 | Nguồn giới thiệu và văn bản liên kết (trang, tài liệu) | trường `referrers` và `links.jsonl` | `documents`, `links` |
| Y5 | Ảnh: nguồn giới thiệu | `images.jsonl` (chỉ URL) và cạnh `image` | `images`, `links` |
| Y6 | Đồ thị nguồn → đích kèm mô tả | `links.jsonl` | `links` |
| Y7 | MongoDB với lược đồ tự xây dựng | `compose.yaml`, `import-mongo`, `data-schema.md` | cả 5 collection |
| Y8 | Chia sẻ dữ liệu của project | dataset Kaggle public, `corpus.lock.json` | không có |

```mermaid
flowchart TD
    %% ============ ĐẦU VÀO ============
    IN["Đầu vào từ crawl<br/>final/articles.jsonl, urls.txt, README.json<br/>và changes/run_id sau mỗi refresh"]

    %% ============ Y1, Y2: TRANG ============
    subgraph PAGES["Y1, Y2: Trang web (làm trong lúc crawl, ghi vào articles.jsonl)"]
        direction TB
        ALGO["Y1: Thuật toán tìm khối nội dung<br/>ưu tiên article hoặc main,<br/>rồi id/class gợi ý;<br/>điểm = độ dài text + 80 x số đoạn<br/>trừ độ dài text trong link;<br/>loại menu, footer, sidebar;<br/>body chỉ là phương án cuối, kèm cảnh báo"]
        META["Y2: Metadata<br/>title: h1, rồi og:title, rồi title<br/>tác giả, ngày: meta, JSON-LD, microdata, byline<br/>không đoán khi thiếu, ghi cảnh báo khi xung đột"]
        REC["Bản ghi trang<br/>title, published_at, author,<br/>text, content_html, content_blocks,<br/>links kèm text, assets kèm alt và caption"]
        ALGO --> META --> REC
    end
    IN --> REC

    %% ============ INVENTORY ============
    subgraph ENRICH["Y3: Tài liệu (data-pipeline enrich, tiếp tục được sau gián đoạn)"]
        direction TB
        INV["inventory<br/>đọc articles.jsonl theo luồng,<br/>lập danh sách tài liệu duy nhất theo URL<br/>và bảng refs: trang nguồn, anchor text"]
        WORK[("data/enrichment/state.sqlite3<br/>inventory, refs, parse_results,<br/>failures, cloud_jobs")]
        DL["download<br/>64 luồng toàn cục, 2 mỗi host,<br/>tải vào parts/sha256(url).part,<br/>nối tiếp bằng Range khi ETag mạnh"]
        DLCHK{"Phản hồi hợp lệ?<br/>HTTP 2xx, MIME từ nội dung<br/>là tài liệu"}
        DLOK["files/sha256(url).ext<br/>inventory: downloaded"]
        DLFAIL["inventory: retry (có retry_at)<br/>hoặc failed; bảng failures<br/>stage download"]
        PARSE{"extract<br/>chọn parser theo định dạng<br/>(8 luồng cục bộ)"}
        PDF["PDF<br/>PyMuPDF"]
        WORD["DOCX: python-docx<br/>DOC, ODT: LibreOffice chuyển sang DOCX"]
        SHEET["XLSX, ODS: openpyxl<br/>XLS: xlrd"]
        SLIDE["PPTX: python-pptx<br/>PPT, ODP: LibreOffice chuyển sang PPTX"]
        TEXT["TXT, CSV, RTF<br/>đọc thẳng"]
        PFAIL["Parser lỗi<br/>parse_results: failed,<br/>failures stage parse"]
        CACHE{"cache/ có kết quả<br/>cho đúng tệp và tập trang?"}
        CLOUD["LlamaParse fast<br/>chỉ trang PDF trực quan không có chữ;<br/>tối đa cloud_workers job song song,<br/>xoay key khi hết quota"]
        CLOUDRES{"Kết quả cloud"}
        RESULT["Kết quả mỗi URL<br/>text theo page, sheet, slide hoặc document,<br/>trạng thái, cảnh báo, phiên bản parser"]
        INV --> WORK
        WORK --> DL --> DLCHK
        DLCHK -- "có" --> DLOK --> PARSE
        DLCHK -- "lỗi, 404, không phải tài liệu" --> DLFAIL
        DLFAIL -. "chạy lại download" .-> DL
        PARSE --> PDF
        PARSE --> WORD
        PARSE --> SHEET
        PARSE --> SLIDE
        PARSE --> TEXT
        PARSE -- "ngoại lệ" --> PFAIL
        PDF -- "trang có hình, không có chữ" --> CACHE
        CACHE -- "có" --> RESULT
        CACHE -- "chưa" --> CLOUD --> CLOUDRES
        CLOUDRES -- "extracted: ghi cache,<br/>trang có chữ thay trang cục bộ" --> RESULT
        CLOUDRES -- "cloud_failed: không cache,<br/>lần chạy sau thử lại" --> RESULT
        CLOUDRES -- "quota_blocked: hết mọi key" --> RESULT
        PDF --> RESULT
        WORD --> RESULT
        SHEET --> RESULT
        SLIDE --> RESULT
        TEXT --> RESULT
        RESULT --> WORK
        REPARSE["extract --reparse-cloud<br/>xoá kết quả đã qua LlamaParse<br/>để parse lại"]
        REPARSE -.-> WORK
    end

    %% ============ LỆNH PHỤ CỦA ENRICH ============
    subgraph ENRICHOPS["Lệnh phụ của enrich"]
        direction TB
        ESTATUS["enrich status<br/>đã tải, đã parse, lỗi, chờ, bị chặn quota;<br/>exit 4 khi còn việc"]
        EEXPORT["enrich export --output dir<br/>documents.jsonl, extraction-report.json"]
        ERECON["enrich reconcile-job<br/>đối chiếu một job LlamaParse đã ghi"]
    end
    WORK -.-> ESTATUS
    WORK -.-> EEXPORT
    WORK -.-> ERECON
    IN --> INV

    %% ============ Y4, Y5, Y6: ĐỒ THỊ ============
    subgraph LINKGRAPH["Y4, Y5, Y6: Liên kết, ảnh và đồ thị (build-graph)"]
        direction TB
        EDGES["Gom cạnh từ mỗi trang<br/>hyperlink kèm anchor text<br/>image và asset kèm alt, caption<br/>observed_refs"]
        DEDUP[("SQLite trên đĩa<br/>khử trùng theo<br/>nguồn, đích, quan hệ, anchor, alt, caption")]
        LINKS["Y6: links.jsonl<br/>source_url, target_url, relation,<br/>anchor_text, alt, caption<br/>nhiều mô tả khác nhau là nhiều cạnh"]
        IMAGES["Y5: images.jsonl<br/>chỉ danh mục URL ảnh,<br/>không tải ảnh; nguồn giới thiệu<br/>nằm ở cạnh image"]
        EDGES --> DEDUP --> LINKS
        DEDUP --> IMAGES
    end
    IN --> EDGES

    %% ============ ĐÓNG GÓI ============
    subgraph PACK["Đóng gói corpus (package, verify)"]
        direction TB
        GATE{"Enrichment xong chưa?<br/>không còn tải dở, file chưa parse,<br/>hay quota bị chặn"}
        DOCS["Y3, Y4: documents.jsonl<br/>url, sha256, mime, parts text,<br/>trạng thái, cảnh báo,<br/>referrers: trang nguồn và anchor text"]
        REPORT["extraction-report.json<br/>đã tải, đã parse, lỗi, chờ, byte"]
        MANIFEST["manifest.json<br/>sha256 từng file,<br/>snapshot_id = hash của danh sách file,<br/>status ready"]
        VERIFY{"verify<br/>đủ file, đúng size,<br/>đúng checksum, đúng snapshot_id?"}
        CORPUS["Corpus: data/corpus/latest<br/>articles, urls, README, documents,<br/>images, links, extraction-report, manifest"]
        GATE -- "chưa" --> STOP["Từ chối đóng gói<br/>nêu rõ phần còn dở"]
        GATE -- "rồi" --> DOCS --> REPORT --> MANIFEST --> VERIFY
        VERIFY -- "sai" --> BAD["Corpus không hợp lệ"]
        VERIFY -- "đúng" --> CORPUS
    end
    RESULT --> GATE
    REC --> GATE
    LINKS --> GATE
    IMAGES --> GATE

    %% ============ Y7: MONGODB ============
    subgraph MONGO["Y7: MongoDB local (Docker Compose, mongo 8)"]
        direction TB
        IMP["import-mongo<br/>verify trước khi nhập"]
        READY{"snapshot_id đã ready?"}
        STATUS["datasets: importing<br/>nhập theo lô, tối đa 100 bản ghi<br/>hoặc 4 MB mỗi lô"]
        COLS[("Collection, mỗi bản ghi mang snapshot_id,<br/>_id xác định nên nhập lại không nhân đôi<br/>pages: metadata và text, bỏ HTML và khối;<br/>documents, images, links: nguyên dòng<br/>index theo snapshot_id, source_url, target_url")]
        DONE["datasets: ready<br/>lỗi thì ghi failed kèm lý do"]
        IMP --> READY
        READY -- "rồi" --> SKIP["Bỏ qua, idempotent"]
        READY -- "chưa" --> STATUS --> COLS --> DONE
    end
    CORPUS --> IMP

    %% ============ Y8: KAGGLE ============
    subgraph KAGGLE["Y8: Chia sẻ qua Kaggle (người vận hành chủ động chạy)"]
        direction TB
        PUB["publish<br/>dataset public, tạo version mới<br/>từ corpus đã verify"]
        LOCK["config/corpus.lock.json<br/>ghim owner, slug, version, snapshot_id"]
        PULL["pull theo lock<br/>thử tải cả gói, lỗi thì tải từng file"]
        PUB --> LOCK
        LOCK --> PULL
    end
    CORPUS --> PUB
    PULL -- "máy mới: không cần crawl lại" --> IMP

    %% ============ TIÊU THỤ ============
    subgraph USE["Tiêu thụ"]
        direction TB
        QUERY["corpus query<br/>cạnh đi vào hoặc đi ra một URL,<br/>văn bản của tài liệu,<br/>ảnh nằm trên trang nào"]
        LUCENE["lucene/<br/>lập chỉ mục và tìm kiếm<br/>từ corpus"]
    end
    COLS --> QUERY
    CORPUS --> LUCENE

    %% ============ MÀU ============
    classDef crawl fill:#dbeafe,stroke:#2563eb,color:#0f172a;
    classDef enrich fill:#ffedd5,stroke:#ea580c,color:#0f172a;
    classDef links fill:#ede9fe,stroke:#7c3aed,color:#0f172a;
    classDef pack fill:#f1f5f9,stroke:#64748b,color:#0f172a;
    classDef out fill:#dcfce7,stroke:#16a34a,color:#0f172a;
    classDef bad fill:#fee2e2,stroke:#dc2626,color:#0f172a;

    class IN,ALGO,META,REC crawl;
    class INV,WORK,DL,DLCHK,DLOK,PARSE,PDF,WORD,SHEET,SLIDE,TEXT,CACHE,CLOUD,CLOUDRES,RESULT,REPARSE,ESTATUS,EEXPORT,ERECON enrich;
    class EDGES,DEDUP,LINKS,IMAGES links;
    class GATE,DOCS,REPORT,MANIFEST,VERIFY pack;
    class CORPUS,COLS,DONE,PUB,LOCK,PULL,QUERY,LUCENE,IMP,READY,STATUS,SKIP out;
    class STOP,BAD,DLFAIL,PFAIL bad;
```

### Điểm cần nhớ (xử lý dữ liệu)

| Câu hỏi | Trả lời ngắn |
|---|---|
| Ai làm Y1 và Y2? | Crawler, ngay lúc trích xuất bài. Kết quả đã nằm trong `articles.jsonl`, nên `enrich` chỉ đọc lại. |
| Tài liệu được tải khi nào? | Sau crawl, trong `data-pipeline enrich download`, chỉ cho tài liệu được trang tham chiếu. Crawl không tải tệp nhị phân. |
| Nguồn giới thiệu và anchor text lưu ở đâu? | Bảng `refs` lúc inventory, rồi thành `referrers` trong `documents.jsonl` và các dòng trong `links.jsonl`. Nhiều nguồn và nhiều mô tả được giữ riêng. |
| Ảnh có được tải không? | Không. Chỉ có danh mục URL và cạnh từ trang tham chiếu. |
| Sau một lần refresh thì làm gì? | Chạy `inventory --source changes/<run_id>` để chỉ nhận phần mới, rồi `download`, `extract`, `package --source final`. Tài liệu đã tải hoặc đã parse được bỏ qua nhờ checkpoint. |
| Chạy lại có hỏng dữ liệu không? | Không. `_id` xác định theo nội dung bản ghi và `snapshot_id`; snapshot đã `ready` thì bị bỏ qua. |
| Người mới clone làm gì? | `pull` theo `corpus.lock.json`, bật MongoDB, `import-mongo`, rồi truy vấn. Không phải crawl hay tải lại tài liệu. |

---

## 3. Kiểm tra host (`domains audit`)

Chạy trước crawl để biết host nào còn trả nội dung công khai. Kết quả là `config/domains/active.txt`, đầu vào mặc
định của `crawl run` và `crawl discover` (xem [domains.md](domains.md)).

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
    PUBLIC["public_response<br/>host hoạt động"]
    CANON["Gộp chuyển hướng<br/>host trỏ sang host khác: redirect_duplicate"]
    DEDUP["Gộp trùng nội dung<br/>cùng hash trang: content_duplicate"]
    OUT["Ghi lại all.txt, active.txt (host active),<br/>status.jsonl (active, reason, status,<br/>final_url, title cho mỗi host)"]
    ERR["probe_error, network_error<br/>một host lỗi không dừng cả lượt"]

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
    class BADHTTP,EXT,REJ,ERR bad;
```

## 4. Chấm điểm trích xuất (`eval extraction`)

Đánh giá thuật toán tìm khối nội dung (Y1) trên các trang mẫu có đáp án, không cần mạng.

```mermaid
flowchart LR
    FIX["--fixtures thư mục<br/>mỗi mẫu: tên.html và tên.json<br/>(url, title, text mong đợi)"]
    RUN["analyze_article<br/>cùng mã trích xuất với crawl"]
    SCORE["So token của text<br/>precision, recall, F1;<br/>title_ok: tiêu đề khớp đúng"]
    OUT["--output báo cáo JSON<br/>fixtures, rows, mean_f1"]
    FIX --> RUN --> SCORE --> OUT

    classDef step fill:#dbeafe,stroke:#2563eb,color:#0f172a;
    classDef out fill:#dcfce7,stroke:#16a34a,color:#0f172a;
    class FIX,RUN,SCORE step;
    class OUT out;
```
