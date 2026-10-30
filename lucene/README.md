# Lucene search — bài tập Information Retrieval

Chương trình đơn giản minh hoạ **Apache Lucene**:

- **Đầu vào**: một thư mục văn bản (`.txt`, `.md`, `.csv`, `.log`, `.pdf`) **hoặc** corpus của crawler trong MongoDB
  (xem [Tìm kiếm trên corpus của crawler](#tìm-kiếm-trên-corpus-của-crawler-mongodb)) + một file truy vấn.
- **Đầu ra**:
  - chỉ mục Lucene trong `index/`
  - mô tả chỉ mục dạng đọc được trong `output/index-dump.txt` (tự sinh, chỉ khi chỉ mục ≤ 1000 tài liệu)
  - kết quả tìm kiếm: in ra console + `output/results.txt`

## Yêu cầu

- JDK 17+ (bản này pin Lucene 9.12.3; Lucene 10 cần JDK 21)
- Maven 3.9+
- Kết nối mạng cho lần cài VnCoreNLP đầu tiên (analyzer tiếng Việt là **mặc định** — xem [mục dưới](#analyzer-tiếng-việt-tách-từ-vncorenlp--mặc-định))
- Chạy được trên **Linux / macOS / Windows** — xem [Chạy trên Windows](#chạy-trên-windows)

## Chạy

```bash
bash scripts/setup-vncorenlp.sh    # 1 lần: tải bộ tách từ tiếng Việt (~28 MB)
mvn -q package                     # build ra target/lucene-search.jar
```

> Chưa chạy `setup-vncorenlp.sh` thì chương trình vẫn chạy được: nó cảnh báo rồi tự lùi về analyzer `standard` (tách theo âm tiết).

### Chạy trên Windows

Bản thân chương trình Java chạy bình thường trên Windows. Chỉ khác vài chỗ:

- **Cài VnCoreNLP** dùng script PowerShell (không cần Git Bash):
  ```powershell
  powershell -ExecutionPolicy Bypass -File scripts\setup-vncorenlp.ps1
  mvn -q package
  java -jar target\lucene-search.jar --mode batch --queries data\queries-vi.txt
  ```
  (Có Git Bash / WSL thì chạy `bash scripts/setup-vncorenlp.sh` cũng được.)
- **Hiển thị tiếng Việt trên console**: chương trình tự ép `System.out` sang UTF-8 nên chữ có dấu hiện đúng. Nếu vẫn thấy `?`, chạy `chcp 65001` trước, hoặc dùng **Windows Terminal** / terminal của VS Code.
- **Tô màu từ khoá**: chỉ bật khi terminal chắc chắn hỗ trợ ANSI (Windows Terminal, VS Code, ConEmu/Cmder). `cmd.exe` cổ điển → không tô màu, thay vào đó bọc `«...»` như trong file. Đặt biến môi trường `NO_COLOR=1` để tắt hẳn màu.
- File `output/results.txt` luôn là UTF-8 và luôn dùng `«...»`, không phụ thuộc terminal.

Chương trình có **3 kiểu chạy** qua tham số `--mode`:

### Kiểu 1 — interactive: nhập từng truy vấn ở console (mặc định)

```bash
java -jar target/lucene-search.jar                       # mode interactive là mặc định
java -jar target/lucene-search.jar --skip-index          # dùng lại chỉ mục có sẵn, không lập lại
```

Sau khi lập chỉ mục, chương trình hiện dấu nhắc `truy van>`. Gõ một truy vấn, Enter, xem kết quả ngay; lặp lại. Thoát bằng `:q` hoặc `Ctrl+D`. Cả phiên vẫn được ghi lại vào `output/results.txt`.

### Kiểu 2 — batch: đọc toàn bộ truy vấn từ file

```bash
java -jar target/lucene-search.jar --mode batch                          # đọc data/queries.txt
java -jar target/lucene-search.jar --mode batch --queries data/queries-vi.txt   # bộ truy vấn tiếng Việt
```

Đọc lần lượt mọi dòng trong file `--queries`, in kết quả từng truy vấn và ghi tất cả vào `output/results.txt`.

### Kiểu 3 — segment: xem kết quả tách từ (không cần chỉ mục)

```bash
java -jar target/lucene-search.jar --mode segment
```

Chế độ debug/kiểm tra riêng cho bước **tách từ tiếng Việt** (VnCoreNLP), tách biệt hẳn khỏi việc lập chỉ mục và tìm kiếm — không đọc `--docs`, không lập `index/`, không ghi `output/results.txt`. Hữu ích khi muốn biết VnCoreNLP tách một câu thành những từ nào *trước khi* các bước sau (hạ chữ thường, bỏ từ dừng, bỏ dấu) xử lý tiếp.

Chương trình hiện dấu nhắc `cau>`. Gõ một câu, Enter, xem ngay danh sách từ đã tách (nối âm tiết bằng `_`); lặp lại. Thoát bằng `:q` hoặc `Ctrl+D`.

```
cau> công nghệ thông tin
  So tu: 1
   1. công_nghệ_thông_tin
  => công_nghệ_thông_tin

cau> trí tuệ nhân tạo
  So tu: 1
   1. trí_tuệ_nhân_tạo
  => trí_tuệ_nhân_tạo
```

Chỉ dùng được với `--analyzer vietnamese` (mặc định) — báo lỗi và không tách nếu chọn `standard`/`english`. Cần đã cài VnCoreNLP (`bash scripts/setup-vncorenlp.sh`), nếu không phần "Analyzer tiếng Việt" phía dưới sẽ tự lùi về `standard` và mode `segment` không hoạt động.

### Kiểu 4 — web: API tìm kiếm cho frontend

```bash
java -jar target/lucene-search.jar --mode web --index index-mongo --skip-index
# API ở http://localhost:8080/api/info   (đổi cổng: --port 9000; dừng: Ctrl+C)
```

Trang web nằm ở [`frontend/`](../frontend) (gốc repo), do nginx trong `docker-compose.yaml` phục vụ tại
http://localhost:8080; khi đó lucene chạy trong container và chỉ mở API ở `127.0.0.1:8081`.

Trang có ô nhập truy vấn, giống một trang tìm kiếm thông thường. Mỗi kết quả hiện URL, tiêu đề (bấm để mở trang gốc), đoạn trích tô vàng từ khoá, điểm BM25, tf-idf và PageRank. Trang cũng hiện "Có phải bạn muốn tìm …" (bấm để tìm lại), cụm mở rộng viết tắt / đồng nghĩa, số kết quả gần trùng đã ẩn và danh sách tài liệu chỉ khớp từ riêng lẻ. Mục "Truy vấn Lucene" ở cuối trang cho xem truy vấn đã dựng.

Dùng `HttpServer` có sẵn trong JDK nên không thêm thư viện; trang nằm trong jar (`src/main/resources/web/index.html`). Máy chủ không cần MongoDB, chỉ đọc chỉ mục. Trước mỗi truy vấn chỉ mục được mở lại nếu đã đổi, nên khi `--mode sync` chạy song song thì dữ liệu mới hiện ngay trên trang. Có thể gọi thẳng API:

```bash
curl 'http://localhost:8080/api/search?q=ktx&limit=5'    # JSON: total, hits[], extra[], suggestion, expansions, ...
curl 'http://localhost:8080/api/info'                    # {"docs": số văn bản trong chỉ mục}
```

### Các tham số chung

| Tham số | Mặc định | Ý nghĩa |
|---|---|---|
| `--mode` | `interactive` | `interactive` \| `batch` \| `segment` \| `sync` \| `pagerank` \| `web` |
| `--port` | `8080` | cổng của `--mode web` |
| `--docs` | `data/docs` | thư mục văn bản đầu vào |
| `--queries` | `data/queries.txt` | file truy vấn (chỉ dùng ở mode batch) |
| `--index` | `index` | thư mục chứa chỉ mục Lucene |
| `--out` | `output/results.txt` | file ghi kết quả tìm kiếm |
| `--index-dump` | `output/index-dump.txt` | file mô tả chỉ mục (tự sinh sau khi lập chỉ mục) |
| `--topk` | `10` | số kết quả tối đa mỗi truy vấn |
| `--analyzer` | `vietnamese` | `vietnamese` (tách từ VnCoreNLP) \| `standard` \| `english` (stemming) |
| `--vn-home` | `.` | thư mục chứa `lib/VnCoreNLP-1.2.jar` và `models/` (cho `--analyzer vietnamese`) |
| `--skip-index` | (tắt) | bỏ qua lập chỉ mục nếu `index/` đã có sẵn |
| `--source` | `files` | `files` (đọc `--docs`) \| `mongo` (đọc corpus của crawler trong MongoDB) |
| `--mongo-uri` | `mongodb://localhost:27017` | MongoDB của `docker-compose.yaml` ở gốc repo |
| `--mongo-db` | `information_retrieval` | database chứa `html`, `other` |
| `--limit` | `0` (tất cả) | chỉ lập chỉ mục N tài liệu đầu (để thử nhanh với `--source mongo`) |
| `--synonyms` | `synonyms.txt` | từ điển viết tắt / đồng nghĩa để mở rộng truy vấn; `--no-synonyms` để tắt |
| `--pagerank-weight` | `0.5` | điểm = BM25 × (1 + w · PageRank chuẩn hoá); `--no-pagerank` để tắt |
| `--every` | `0` | `--mode sync`: lặp lại mỗi N giây (0 = chạy 1 lượt) |
| `--dedup` | `0.9` | ngưỡng Jaccard để ẩn kết quả gần trùng lặp; `--no-dedup` để tắt |

## Bổ sung quanh truy vấn

Ba bổ sung chạy tự động, không cần lệnh riêng:

**Mở rộng viết tắt / đồng nghĩa** — `synonyms.txt`, mỗi dòng một nhóm từ tương đương (`ký túc xá, ktx`). Truy
vấn "trơn" chứa một thành viên (so khớp không dấu, nguyên từ) thì các thành viên còn lại được thêm vào truy vấn
dạng OR với trọng số 0.5:

```
Truy van Q1: ktx
  Mo rong truy van (viet tat / dong nghia): + ký túc xá
```

Tránh đưa vào viết tắt trùng với từ thường gặp (ví dụ "ai" vừa là AI vừa là "ai là…"), sẽ mở rộng sai.

**Gợi ý sửa lỗi chính tả** — khi truy vấn trơn có ít hơn 20 kết quả. Bộ từ vựng là field `spell` (âm tiết còn
dấu của tiêu đề + nội dung). Mỗi âm tiết lấy tối đa 8 âm tiết cách nó 1–2 phép sửa (Levenshtein; 1 phép với âm
tiết ≤ 3 ký tự); rồi thay lần lượt từng âm tiết, đếm số văn bản chứa **nguyên cụm**, giữ phương án nhiều nhất
(tối đa 2 lượt). Nhờ kiểm tra trong ngữ cảnh mà "sử lý" được sửa thành "xử lý" dù "sử" là từ đúng chính tả. Chỉ
gợi ý khi cụm gốc không có trong văn bản nào, hoặc cụm mới nhiều gấp ≥ 10 lần (để không đổi viết tắt có thật như
"cntt"):

```
Truy van Q1: sử lý ngôn ngữ
  >> Co phai ban muon tim: "xử lý ngôn ngữ"  (2 van ban chua cum nay, so voi 0)
```

Chỉ mục lập trước khi có field `spell` thì không gợi ý (lập lại chỉ mục để bật).

**Ẩn kết quả gần trùng lặp** — nhiều trang khoa/viện đăng lại cùng một nội dung. Mỗi kết quả được biểu diễn
bằng tập chuỗi 4 từ liên tiếp; độ giống nhau (hệ số Jaccard) ước lượng bằng MinHash 128 hàm băm. Kết quả giống
một kết quả xếp trên với Jaccard ≥ 0.9 bị ẩn, chương trình lấy thêm ứng viên để vẫn đủ top-K:

```
  (da an 1 ket qua gan trung lap voi ket qua xep tren)
```

## Tìm kiếm trên corpus của crawler (MongoDB)

Crawler (`../crawler`) lưu kết quả vào MongoDB, bản dump `information_retrieval-<ngày>.archive.gz` đặt trong
`crawler/data/submission/` (không commit). `docker-compose.yaml` ở gốc repo dựng **một MongoDB chung** cho crawler và
lucene — volume `lucene_mongo_data`, cổng `127.0.0.1:27017`. Các lệnh `docker compose` chạy được từ gốc repo hoặc thư
mục con.

```bash
docker compose up -d mongo                                  # 1. bật MongoDB
DUMP=<tên-file>.archive.gz docker compose run --rm restore  # 2. nạp bản dump (một lần, đủ mọi collection)
mvn -q package
java -Xmx4g -jar target/lucene-search.jar --source mongo --index index-mongo --limit 5000 --mode batch \
     --queries data/queries-vi.txt                          # 3. thử trên 5000 tài liệu
java -Xmx4g -jar target/lucene-search.jar --source mongo --index index-mongo   # 4. lập chỉ mục toàn bộ
java -jar target/lucene-search.jar --index index-mongo --skip-index            # 5. các lần sau: chỉ tìm kiếm
```

- `restore` chạy `--drop` nên nạp lại
  sẽ thay dữ liệu cũ.
- Dùng `mongo:7.0` chứ không phải `8.0`: MongoDB 8.0 tự thoát trên kernel Linux 6.19–7.0.13
  ([SERVER-121912](https://jira.mongodb.org/browse/SERVER-121912)), mà Docker Desktop đang chạy kernel 7.0.12.
- Nên dùng thư mục chỉ mục riêng (`--index index-mongo`) để không ghi đè chỉ mục của bộ mẫu `data/docs`.

Tài liệu Lucene lấy từ hai collection ([schema](../crawler/docs/data-schema.md#mongodb)):

| Collection | Một tài liệu Lucene | `id` | `title` | `intro` | `content` |
|---|---|---|---|---|---|
| `html` | một trang (bỏ trang trùng nội dung — có `duplicate_of` — và trang rỗng) | `url` | `title` | `intro` | `content` |
| `other` | một tệp PDF/DOCX/XLSX… (bỏ tệp rỗng) | `url` | tên tệp trong URL | văn bản liên kết từ `graph` | `content` |

`graph` không được lập chỉ mục riêng mà dùng làm **văn bản liên kết** (anchor text): crawler đã gộp văn bản các liên
kết trỏ tới mỗi trang vào `html.intro`, nhưng không làm cho tệp. Với `other`, lucene tự tính `intro` từ `graph` theo
đúng quy tắc của crawler (`build_intro`: gộp khoảng trắng, bỏ rỗng / trùng, xếp theo số lần xuất hiện, tối đa 20
dòng). Có ích nhất với tệp PDF bóc chữ bị lỗi dấu: liên kết mô tả tệp bằng tiếng Việt chuẩn, ví dụ
`DHBK_ET-E5.pdf` (nội dung "VIÉN DIÉN TU … TUYÉN SINH") có liên kết "Ngành Kỹ thuật Y sinh". `picture` (ảnh base64)
không được nạp.

Truy vấn không ghi field sẽ tìm trên cả ba field với trọng số **`title` ×3, `intro` ×1.5, `content` ×1**: trang có
từ khoá trong tiêu đề, hoặc được các trang khác nhắc tới bằng từ khoá đó, xếp trên trang chỉ nhắc tới trong nội
dung. Muốn tìm một field: `title:"tuyển sinh"`. Đoạn trích (KWIC) vẫn cắt từ `content`.

Lập chỉ mục được đọc kiểu stream (cursor MongoDB), không nạp cả corpus vào RAM, và in tiến độ mỗi 1000 tài liệu.
Bước chậm nhất là **tách từ VnCoreNLP** (một luồng): xem tốc độ `tl/s` ở bước thử 3 để ước thời gian lập toàn bộ,
hoặc dùng `--analyzer standard` nếu cần nhanh.

## PageRank (`graph` → điểm chất lượng của trang)

Khi lập chỉ mục từ MongoDB, chương trình tính **PageRank** trên đồ thị liên kết (`PageRank`):

- **Đỉnh**: mọi trang `html` và tệp `other`. **Cạnh**: liên kết trong `graph` **giữa hai host khác nhau**. Khoảng 85%
  liên kết nằm trong cùng một site (menu, chân trang, phân trang); tính cả vào thì các trang điều hướng của thư viện
  số `dlib.hust.edu.vn` chiếm hết top. Liên kết từ site khác mới là "lời giới thiệu": PageRank cao nhất rơi vào trang
  chủ các site (thư viện, hust.edu.vn, svbk, ctsv…) và các thông báo tuyển sinh của ts.hust.
- **Tính**: phương pháp luỹ thừa, bước nhảy α = 0.1, trang không có liên kết ra nhảy đều tới mọi trang.
- **Chuẩn hoá** về g(d) ∈ [0, 1] theo thang log: 0 = không có liên kết nào từ site khác, 1 = cao nhất.
- **Xếp hạng**: `điểm = BM25 × (1 + w · g(d))`, `w` = `--pagerank-weight` (mặc định 0.5, tức trang có PageRank cao nhất
  được cộng tối đa 50%). Nhân thay vì cộng vì điểm BM25 dao động 10–200 tuỳ truy vấn; nhân thì tác động của PageRank
  theo tỉ lệ, như nhau ở mọi truy vấn. `--no-pagerank` để tắt. Mỗi kết quả in thêm `pagerank=…`.
- g(d) lưu thành doc values trong chỉ mục, nên **tính lại không phải lập lại chỉ mục**:
  ```bash
  java -jar target/lucene-search.jar --mode pagerank --index index-mongo     # vài chục giây
  ```
  `--mode sync` giữ nguyên g của tài liệu được ghi đè, tài liệu mới nhận 0 cho tới lần `--mode pagerank` sau.
  PageRank đổi chậm, chạy lại định kỳ (ví dụ mỗi ngày) là đủ.

## Đồng bộ khi MongoDB có dữ liệu mới (`--mode sync`)

Crawler không chỉ thêm trang mới: nó còn thu thập lại trang cũ (nội dung đổi), tính lại `intro` của các trang được
trỏ tới, và có thể đánh dấu trang thành trùng (`duplicate_of`). Mọi lần ghi đều đặt `updated_at`. Thay vì lập lại
toàn bộ (~10 phút), `--mode sync` chỉ đọc những bản ghi có `updated_at` mới hơn mốc lần trước:

```bash
java -jar target/lucene-search.jar --source mongo --index index-mongo     # 1 lần: lập toàn bộ, ghi mốc
java -jar target/lucene-search.jar --mode sync --index index-mongo        # 1 lượt đồng bộ
java -jar target/lucene-search.jar --mode sync --index index-mongo --every 60   # lặp mỗi 60 giây (Ctrl+C dừng)
```

| Bản ghi trong MongoDB | Trong chỉ mục |
|---|---|
| `url` chưa có trong chỉ mục | thêm |
| `url` đã có, title / intro / content đổi | ghi đè (`updateDocument` theo `url`) |
| `url` đã có, ghi lại nhưng không đổi gì | bỏ qua (so chữ ký SHA-1 `sig` lưu trong chỉ mục) |
| thành trang trùng (`duplicate_of`) hoặc nội dung rỗng | xoá |
| tệp được một trang vừa đổi trỏ tới | tính lại `intro` từ `graph`, ghi đè nếu đổi (`updated_at` của tệp không đổi khi có liên kết mới) |

- **Mốc** (`updated_at` đã đồng bộ tới) lưu ngay trong commit của chỉ mục, nên chỉ mục và mốc luôn khớp; đồng bộ
  chết giữa chừng thì lượt sau làm lại từ mốc cũ. Mốc lùi 60 giây so với lúc bắt đầu đọc để không lỡ bản ghi ghi
  đúng lúc đang đọc; đọc trùng thì bị bỏ qua nhờ chữ ký.
- Lần chạy đầu tạo index MongoDB trên `updated_at` của `html`, `other` để mỗi lượt không quét cả collection.
- Chỉ mục lập bằng `--limit` hoặc từ thư mục không có mốc → `sync` báo cần lập toàn bộ trước.
- **Phía tìm kiếm** (interactive) mở lại chỉ mục trước mỗi truy vấn nếu có commit mới, nên vừa đồng bộ xong là tìm
  thấy, không cần khởi động lại; `sync` và tìm kiếm chạy song song được (Lucene: 1 tiến trình ghi, nhiều tiến trình đọc).
- Nên thỉnh thoảng lập lại toàn bộ (ví dụ mỗi tuần) để dọn sai lệch tích luỹ và gộp segment.

### Demo: dữ liệu mới đổ vào MongoDB

Từ bản dump, 10% `html` và `other` (chọn theo băm `url`, cố định) đã được tách khỏi MongoDB và lưu thành 100 batch
trong `data/demo/batch-001 … batch-100.{html,other}.json` (mỗi batch ~140 trang + vài tệp; `lucene/data` không
commit). MongoDB và chỉ mục chứa 90% còn lại. `scripts/demo-upsert.sh` đưa batch trở lại MongoDB như crawler vừa
ghi (`updated_at` = lúc chạy, upsert theo `url`):

```bash
# cửa sổ 1: tìm kiếm (tự thấy dữ liệu mới trước mỗi truy vấn)
java -jar target/lucene-search.jar --index index-mongo --skip-index
# cửa sổ 2: đồng bộ mỗi 10 giây
java -jar target/lucene-search.jar --mode sync --index index-mongo --every 10
# cửa sổ 3: "crawler" đổ batch 1..100, cách nhau 30 giây
bash scripts/demo-upsert.sh 1 100 30
```

Chạy lại một batch đã đổ (`bash scripts/demo-upsert.sh 1`) thì MongoDB báo "cập nhật", còn `sync` báo "không đổi"
vì nội dung y hệt. Muốn quay về dữ liệu đầy đủ: `DUMP=<tên-file>.archive.gz docker compose run --rm restore` rồi lập lại chỉ mục.

## Đoạn trích chứa từ khoá (KWIC)

Mỗi kết quả in kèm một đoạn văn bản **cắt quanh vị trí từ khoá** (dùng `lucene-highlighter`). Từ khớp được làm nổi bật:

- **Console** (khi chạy trong terminal thật): tô **nền vàng, chữ đen** (mã ANSI).
- **File `output/results.txt`** (và console khi bị chuyển hướng/pipe): bọc bằng `«...»`.

```
  1. score=0.5645  id=ranking-bm25.txt
     title: ranking-bm25.txt
     nguon: data/docs/ranking-bm25.txt
     … Lucene, use «BM25» as the default ranking function. «BM25» is a probabilistic model … Modern search engines use «BM25» as the default …
```

Với `--analyzer english`, từ khoá được so khớp *sau khi stemming*, nên truy vấn `searches` vẫn làm nổi bật `searching` / `searched` / `search` trong văn bản.

## Analyzer tiếng Việt (tách từ VnCoreNLP) — mặc định

`--analyzer vietnamese` dùng bộ tách từ **VnCoreNLP** (RDRsegmenter). Token trong chỉ mục là **từ**, các âm tiết được nối bằng `_`:

```
"trí tuệ nhân tạo"  →  trí_tuệ_nhân_tạo        (1 token)
"công nghệ thông tin" → công_nghệ_thông_tin
"đồng bằng sông Cửu Long" → đồng_bằng · sông · cửu_long
```

Nhờ đó truy vấn `"trí tuệ nhân tạo"` chỉ khớp tài liệu thật sự nói về khái niệm đó, không khớp nhầm tài liệu tình cờ chứa `trí`, `tuệ`… rời rạc.

### Cài đặt (một lần)

```bash
bash scripts/setup-vncorenlp.sh                                        # Linux / macOS / Git Bash
powershell -ExecutionPolicy Bypass -File scripts\setup-vncorenlp.ps1   # Windows
```

Script tải 3 file (đều nằm trong `.gitignore`):

| File | Đích | Cỡ |
|---|---|---|
| `https://raw.githubusercontent.com/vncorenlp/VnCoreNLP/master/VnCoreNLP-1.2.jar` | `lib/VnCoreNLP-1.2.jar` | ~27 MB |
| `.../master/models/wordsegmenter/wordsegmenter.rdr` | `models/wordsegmenter/wordsegmenter.rdr` | ~128 KB |
| `.../master/models/wordsegmenter/vi-vocab` | `models/wordsegmenter/vi-vocab` | ~515 KB |

Tải tay cũng được — cứ đặt đúng 3 file vào đúng vị trí trên (thư mục gốc dự án).

### Cách hoạt động (không phải dependency lúc build)

- `VnSegmenter` nạp `lib/VnCoreNLP-1.2.jar` lúc chạy bằng `URLClassLoader` + reflection, chỉ bật annotator `wseg`. Vì vậy `mvn package` **không cần** VnCoreNLP; project vẫn build/chạy khi chưa cài.
- Chưa cài mà chọn `vietnamese` (kể cả do mặc định) → in cảnh báo và **tự lùi về `standard`**, chương trình vẫn chạy:
  ```
  !! Chua dung duoc analyzer 'vietnamese' (tach tu VnCoreNLP):
     thieu file cua VnCoreNLP ...
     -> Tam thoi dung 'standard' (tach theo am tiet).
  ```
- Kết quả tách từ được cache trong `VnSegmenter` (Highlighter re-analyze mỗi tài liệu nhiều lần).

### Chuỗi xử lý của `VietnameseAnalyzer`

```
VnWordTokenizer     tách TỪ bằng VnCoreNLP        → "trí_tuệ_nhân_tạo", "xử_lý"
LowerCaseFilter     chữ thường
StopFilter          bỏ từ chức năng + dấu câu     (và, của, là, ",", "." …)
ASCIIFoldingFilter  bỏ dấu                        "tiếng" → "tieng"
CompoundSplitFilter từ ghép → giữ nguyên + phát từng âm tiết LIÊN TIẾP đúng thứ tự
                    "tri_tue_nhan_tao"  +  tri · tue · nhan · tao (vị trí kề nhau)
```

Các filter này xử lý những ca thường gặp (đã kiểm bằng ~25 truy vấn):

| Ca | Không có filter | Có |
|---|---|---|
| Gõ **không dấu**: `tieng viet`, `hoc may` | 0 kết quả (`tieng` ≠ `tiếng`) | khớp bình thường |
| **Ngữ cảnh tách khác nhau**: `xử lý ngôn ngữ` vs tài liệu `…ngôn ngữ tự nhiên` | 0 (query `ngôn_ngữ`, index `ngôn_ngữ_tự_nhiên`) | khớp — âm tiết `ngon ngu` nằm kề nhau trong cả hai |
| **`đại học`** (âm tiết `học` cực phổ biến) | nhiễu: khớp cả "sinh học", "khoa học" | chỉ khớp nơi "đại" **ngay trước** "học" |
| Truy vấn toàn **từ dừng**: `của`, `và`, `một` | trả gần hết tài liệu | báo "không còn từ khoá" |
| **Dấu câu lọt vào**: `học máy, trí tuệ nhân tạo` | `,` thành term → khớp mọi tài liệu | dấu câu bị bỏ |

**Đánh đổi còn lại** (ghi rõ để không nhầm là lỗi):

- Bỏ dấu gộp thanh điệu: `cá` = `cà` = `cả` → `ca`; `phở` cũng khớp "phổ". Ưu tiên recall.
- `từ` **không** phải từ dừng (vì cụm "tách từ" cần) → gõ mỗi `từ` vẫn ra nhiều kết quả.
- Truy vấn nhiều khái niệm rời (`Apache Lucene tiếng Việt`) cho kết quả yếu — dùng `AND` để nối.

### Truy vấn — không cần ngoặc kép

Câu truy vấn **"trơn"** (chỉ chữ, không có `AND`/`OR`/`NOT`/`" "`/`( )`/`:`/`*`…) được chương trình **tự tách từ rồi dựng truy vấn** thay vì đưa thẳng vào `QueryParser`. Vì `CompoundSplitFilter` đưa các âm tiết vào chỉ mục **liên tiếp nhau đúng thứ tự**, truy vấn dựng ra là:

```
(content:"<âm tiết 1> <âm tiết 2> …"~2)^2   ← các âm tiết phải NẰM GẦN NHAU, đúng thứ tự (clause chính)
(content:<từ ghép>)^0.4 …                    ← từ ghép như một token — phụ, giúp xếp hạng
```

- `content:"dai hoc"~2` là **PhraseQuery**: 2 token `dai`, `hoc` cách nhau ≤ 2 vị trí, đúng thứ tự.
- `content:dai_hoc` là **TermQuery**: đúng 1 token `dai_hoc` (chỉ có nếu VnCoreNLP ghép "đại học" thành 1 từ lúc lập chỉ mục).
- Số `^` là **boost** — nhân điểm relevance của clause đó (không đổi tài liệu nào khớp, chỉ đổi thứ hạng).

| Gõ | Truy vấn dựng ra | Kết quả |
|---|---|---|
| `học máy` | `(content:"hoc may"~2)^2` | 2 tài liệu có "học máy" |
| `đại học` | `(content:"dai hoc"~2)^2 (content:dai_hoc)^0.4` | 0 — tập mẫu không có "đại học" (đúng, không kéo rác) |
| `xử lý ngôn ngữ` | `(content:"xu ly ngon ngu"~2)^2 (content:xu_ly)^0.4 (content:ngon_ngu)^0.4` | nlp #1, hoc-may #2 |

Nhờ so khớp **theo cụm âm tiết** (không phải OR từng âm tiết), `đại học` chỉ khớp nơi "đại" đứng ngay trước "học" — **không** dính "sinh học" / "khoa học".

**Truy vấn nhiều khái niệm rời** (vd `Apache Lucene tiếng Việt`) không phải một cụm liền → clause chính không khớp, kết quả yếu. Lúc đó dùng toán tử: `Lucene AND "tiếng Việt"`.

Khi câu có toán tử thì chương trình **không** can thiệp — tự đặt ngoặc kép quanh từng cụm:

```
phở OR "bánh mì"
"học máy" AND "trí tuệ nhân tạo"
```

Bộ truy vấn mẫu: `data/queries-vi.txt`.

```bash
java -jar target/lucene-search.jar --mode batch --queries data/queries-vi.txt
java -jar target/lucene-search.jar                    # interactive, gõ: học máy
```

Chạy nhanh khi phát triển, không cần đóng gói jar:

```bash
mvn -q compile exec:java                                 # interactive
mvn -q compile exec:java -Dexec.args="--mode batch"      # batch
```

## Định dạng file truy vấn (`data/queries.txt`)

- Mỗi dòng một truy vấn. Dòng trống hoặc bắt đầu bằng `#` bị bỏ qua.
- Dùng được cú pháp `QueryParser` của Lucene: `AND`, `OR`, `NOT`, `"cụm từ"`, `tiền tố*` (cả có dấu: `tuyển*`), `title:tên` (tiêu đề trang, hoặc tên file với `--source files`).
- Muốn tự đặt mã truy vấn: `<id><TAB><nội dung>` (ví dụ `Q99<TAB>elasticsearch OR solr`).

## Xem nội dung chỉ mục

Thư mục `index/` là file nhị phân (`.cfs`), phải xem qua công cụ.

### File mô tả tự sinh — `output/index-dump.txt`

**Mỗi lần lập chỉ mục (khi chỉ mục ≤ 1000 tài liệu), chương trình tự ghi** một file văn bản mô tả toàn bộ chỉ mục:

- **Tổng quan**: số tài liệu, số segment, dung lượng.
- **Bảng Field**: mỗi field được đánh chỉ mục kiểu gì (`DOCS` / `DOCS_AND_FREQS_AND_POSITIONS` / `NONE`), có `stored` / `norms` không.
- **Từ điển term = chỉ mục ngược**: với mỗi field, mỗi term → **danh sách tài liệu chứa nó** (kèm `(số lần)` khi > 1), cùng `df` (số tài liệu) và `tf` (tổng số lần).
- **Stored fields** của từng tài liệu (`content` cắt 200 ký tự).

```
== Field ==
  id       | index=DOCS                          | stored=co | norms=-  | ...
  path     | index=NONE                          | stored=co | norms=-  | ...   ← chỉ lưu, không đánh chỉ mục
  content  | index=DOCS_AND_FREQS_AND_POSITIONS  | stored=co | norms=co | ...

== Tu dien term (chi muc nguoc)  |  term -> [tai lieu chua no] ==
  [content]  so term = 615,  tong token = 1308
      trí_tuệ_nhân_tạo   df=2  tf=2  -> [hoc-may.txt, nlp-tieng-viet.txt]
      đồng_bằng          df=2  tf=4  -> [bien-doi-khi-hau.txt, dia-ly-viet-nam.txt(3)]
      lucene             df=2  tf=7  -> [apache-lucene-vi.txt(4), lucene.txt(3)]
```

Muốn xem thêm **vị trí** từng lần xuất hiện thì dùng `IndexDump --postings` (bên dưới).

### Tiện ích `IndexDump` (chạy tay, để soi kỹ hơn)

```bash
java -cp target/lucene-search.jar com.example.ir.IndexDump index --field content --limit 0   # tất cả term của content
java -cp target/lucene-search.jar com.example.ir.IndexDump index --postings content:lucene   # term này ở tài liệu nào
java -cp target/lucene-search.jar com.example.ir.IndexDump index --docs                       # stored fields từng tài liệu
java -cp target/lucene-search.jar com.example.ir.IndexDump index --report my-dump.txt         # ghi mô tả ra file khác
```

`--postings content:lucene` cho thấy trực tiếp cơ chế chỉ mục ngược:

```
== Postings cho content:lucene ==
  doc #2  [apache-lucene-vi.txt]  freq=4  vi_tri=[15, 35, 65, 133]
  doc #7  [lucene.txt]            freq=3  vi_tri=[30, 64, 86]
```

### Luke (GUI chính thức)

Tải bản `lucene-9.12.3` từ trang Apache, chạy `luke.sh` / `luke.bat`, mở thư mục `index/` — duyệt term, xem document, chạy thử truy vấn, xem giải thích điểm BM25.

## Luồng xử lý & các lớp

| Bước | Lớp | Việc làm |
|---|---|---|
| Rút văn bản | `TextExtractor` | đọc text thẳng; PDF qua Apache PDFBox |
| Nạp tài liệu | `DocLoader` | quét thư mục đệ quy → `List<Doc>` (id = đường dẫn tương đối) |
| Nạp từ MongoDB | `MongoDocLoader` | duyệt cursor `html` rồi `other` (`--source mongo`), từng tài liệu một; `changes(moc)` cho đồng bộ |
| PageRank | `PageRank` | tính trên liên kết khác host của `graph`, chuẩn hoá [0, 1], lưu doc values; `Indexer.updatePagerank` ghi đè |
| Đồng bộ | `Indexer.sync` | thêm / ghi đè / xoá theo `url`, bỏ qua bản ghi không đổi, lưu mốc trong commit |
| Tạo chỉ mục | `Indexer` | `IndexWriter` + analyzer; field `content` (phân tích **và lưu** để highlighter cắt được đoạn trích), `title`, `intro` (chỉ phân tích), `id`/`path` |
| Nạp truy vấn | `QueryLoader` | đọc `queries.txt` (mode batch) |
| Tìm kiếm | `Searcher` | `runBatch` (từ file) / `runInteractive` (REPL ở console); `MultiFieldQueryParser` trên `title`/`intro`/`content` → `IndexSearcher.search` → top-K BM25; `Highlighter` cắt đoạn trích, `teeSink` tô nền ANSI (console) / bọc `«...»` (file) |
| Giao diện web | `WebServer` | `--mode web`: API `/api/search` trả JSON từ `Searcher.search` (cùng logic với console); `DataApi` đọc dữ liệu thô từ MongoDB (`/api/stats`, `/api/docs`, `/api/doc`, `/api/picture`) |
| Mở rộng truy vấn | `Synonyms` | đọc `synonyms.txt`, thêm cụm viết tắt / đồng nghĩa vào truy vấn |
| Gợi ý chính tả | `SpellSuggester` | `DirectSpellChecker` trên field `spell` + đếm cụm trong field `content` |
| Ẩn trùng lặp | `NearDuplicates` | chuỗi 4 từ + MinHash, ước lượng Jaccard giữa các kết quả |
| Tách từ tiếng Việt | `VnSegmenter` + `VietnameseAnalyzer` + `CompoundSplitFilter` | nạp động VnCoreNLP từ `lib/`; token là **từ** (`trí_tuệ_nhân_tạo`) + âm tiết cùng vị trí; kèm bỏ dấu + lọc từ dừng |
| Xem chỉ mục | `IndexDump` | tiện ích riêng: in field, từ điển term, postings (`java -cp ... com.example.ir.IndexDump`) |

## Ghi chú

- Analyzer khi lập chỉ mục và khi phân tích truy vấn phải giống nhau — ở đây dùng chung một instance.
- `--analyzer english` bật stemming (searching/searched/searches → cùng gốc), chỉ hợp cho tiếng Anh.
- `--analyzer standard` xử lý tiếng Việt ở mức **âm tiết**; `vietnamese` (mặc định) tách theo **từ**.
- Mỗi lần chạy, chỉ mục được tạo lại từ đầu (`OpenMode.CREATE`); dùng `--skip-index` để giữ lại.
- `content` được lưu bản gốc (`Store.YES`) để `Highlighter` cắt đoạn trích. Nếu tập văn bản rất lớn và không cần snippet, đổi lại `Store.NO` trong `Indexer`.
- VnCoreNLP dùng log4j 1.x nên khi khởi tạo có thể in dòng `INFO ... Loading Word Segmentation model` — bình thường.
- Văn bản được chuẩn hoá Unicode NFC khi nạp (cả truy vấn) — văn bản web dạng NFD (dấu tách rời) vẫn khớp.

## Kịch bản demo (khoảng 10–15 phút)

Mọi lệnh chạy trong thư mục `lucene/`, Docker Desktop đang mở.

> Kịch bản này tập trung vào phần tìm kiếm. Demo cả hệ thống (crawl → dữ liệu → tìm kiếm): xem [README gốc](../README.md#demo). Các bước `sync` chạy tay bên dưới chỉ cần khi chạy lucene ngoài Docker.

### Chuẩn bị

```bash
docker compose up -d mongo          # MongoDB (cổng 27017)
mvn -q -DskipTests package          # chỉ cần nếu có sửa code
```

Mở 3 terminal và đặt trình duyệt bên cạnh:

- **T1** chạy máy chủ web.
- **T2** đẩy dữ liệu mới vào MongoDB.
- **T3** chạy đồng bộ.

### Phần 1: Tìm kiếm trên web (khoảng 5 phút)

```bash
# T1
docker compose up -d lucene frontend   # API và trang web; lucene trong container đã tự chạy sync mỗi 10 giây
```

Mở http://localhost:8080, tab Tìm kiếm. Đầu trang hiện số văn bản trong chỉ mục.

| Truy vấn | Điểm cần chỉ ra |
|---|---|
| `điểm chuẩn đại học bách khoa` | Gõ cụm tiếng Việt không cần ngoặc kép, tách từ ra `điểm_chuẩn`, tô sáng cả cụm. Mở mục "Truy vấn Lucene" ở cuối trang để xem PhraseQuery ×2 + TermQuery ×0.4 và hệ số title/intro/content 3/1.5/1. |
| `ktx` | Dòng "Đã mở rộng truy vấn: + ký túc xá" và "đã ẩn N kết quả gần trùng lặp" (MinHash). |
| `ky tuc xa` | Gõ không dấu vẫn ra đúng trang (ASCIIFolding). |
| `học bổng AND (thạc sĩ OR tiến sĩ)` | Toán tử Boolean. |
| `title:tuyển sinh` | Chỉ tìm trong tiêu đề. |
| `quy chế đào tạo` | Kết quả là tệp (ghi "· tệp") với intro lấy từ anchor text trong `graph`; cột PageRank; mục "không khớp cụm chính xác…" (chỉ có điểm tf-idf). |

### Phần 2: Crawler thêm dữ liệu, kết quả cập nhật theo (khoảng 5 phút)

Kịch bản: crawler liên tục ghi vào MongoDB, hệ thống tìm kiếm phải cập nhật mà không lập lại chỉ mục từ đầu.

1. Trên web, tìm `đổ xô đến philippines học tiếng anh`. Trang "Đổ xô đến Philippines học tiếng Anh" chưa có trong kết quả.
2. Giả lập crawler ghi batch 4:
   ```bash
   # T2
   bash scripts/demo-upsert.sh 4
   ```
   Lệnh in số bản ghi được thêm hoặc cập nhật trong `html`/`other`, với `updated_at` là thời điểm hiện tại.
3. Đồng bộ chỉ mục:
   ```bash
   # T3
   java -jar target/lucene-search.jar --mode sync --index index-mongo
   ```
   Kết quả là một dòng `them X, cap nhat Y, xoa Z, khong doi W`. Lệnh chỉ đọc bản ghi có `updated_at` từ mốc lần trước, upsert theo `url`, và bỏ qua văn bản không đổi nhờ mã băm SHA-1.
4. Bấm **Tìm** lại trên web (không cần khởi động lại máy chủ). Trang mới xuất hiện ở gần đầu, số văn bản ở đầu trang tăng lên.
5. (Tuỳ chọn) Chạy lại `demo-upsert.sh 4` rồi sync: mọi bản ghi báo `khong doi`, chạy hết khoảng 0,1 giây.

Chế độ liên tục, nếu còn thời gian:

```bash
# T3: đồng bộ mỗi 10 giây
java -jar target/lucene-search.jar --mode sync --index index-mongo --every 10
# T2: batch 5 đến 8, mỗi batch cách nhau 15 giây
bash scripts/demo-upsert.sh 5 8 15
```

### Phần 3: PageRank từ `graph` (khoảng 2 phút, tuỳ chọn)

```bash
# T3
java -jar target/lucene-search.jar --mode pagerank --index index-mongo
```

Lệnh in số đỉnh, số liên kết khác host, số vòng lặp đến khi hội tụ và 5 trang có PageRank cao nhất. Lệnh chỉ ghi đè giá trị `pagerank`, không tách từ lại. Điểm xếp hạng là BM25 × (1 + 0.5·g(d)); trên web, PageRank hiện ở dưới mỗi kết quả.

### Kết thúc

```bash
# Ctrl+C ở T1 (web) và T3 (nếu đang chạy sync --every)
docker compose stop mongo
```

### Lưu ý

- Batch 1–3 đã được đẩy vào MongoDB. Để dành batch 4 cho buổi demo thật; muốn tập trước thì dùng batch 5 đến 100. Dữ liệu batch nằm trong `data/demo/` (không đưa lên git).
- Gợi ý chính tả khó hiện trên dữ liệu đầy đủ, vì phần lớn truy vấn gõ sai vẫn ra từ 20 kết quả trở lên nhờ khớp từng từ riêng lẻ. Chỉ nên nhắc là hệ thống có tính năng này.
- Cần có mạng nếu muốn bấm vào tiêu đề kết quả, vì đường dẫn mở trang gốc trên hust.edu.vn.
