# Bóc tách nội dung, đồ thị liên kết, MongoDB local và chia sẻ Kaggle

Ngày: 2026-10-05 — sửa phạm vi theo yêu cầu thực hiện đầy đủ bài tập.

Cập nhật lựa chọn parser: local bằng PyMuPDF, LlamaParse `fast` chỉ dùng khi
local không ra chữ có nghĩa; chuyển key khi hết quota theo cấu hình `.env`.

## Mục tiêu và các quyết định đã thống nhất

Người mới clone repository có thể tải dữ liệu crawl từ Kaggle để làm các bước
bóc tách, xây đồ thị liên kết và tìm kiếm mà không phải crawl lại các trang.
Dataset Kaggle là public. Người vận hành chủ động chạy lệnh publish; crawler
không tự upload sau mỗi lần chạy. MongoDB chạy local bằng Docker Compose.

Người dùng đã cung cấp yêu cầu của giảng viên:

- Trang web: tiêu đề, ngày đăng, tác giả, nội dung, nguồn giới thiệu và văn bản liên kết.
- Tài liệu: nội dung văn bản, nguồn giới thiệu và văn bản liên kết.
- Ảnh: nguồn giới thiệu.
- Đồ thị liên kết: nguồn → đích, kèm văn bản mô tả.
- Có thuật toán tìm khối nội dung; tự xây dựng và mô tả lược đồ lưu trữ.

Kaggle là nơi phát hành các bản dữ liệu. MongoDB là nơi lưu dữ liệu có cấu trúc
để xử lý và truy vấn trên máy của từng thành viên. JSONL giữ nguyên thông tin
crawler cung cấp; chuyển sang các file TXT không còn là bước bắt buộc.

## Hiện trạng và phạm vi đầy đủ

`crawler/data/crawl/final/README.json` ghi 79.630 bài viết và 79.630 URL,
với nguồn crawl kết thúc ngày 2026-09-24. Ba file hiện có là `articles.jsonl`,
`urls.txt`, `README.json`. Manifest crawl ghi `complete_with_failures`,
1.074 URL thất bại và không có tệp nhị phân đã tải.

Crawler hiện có thuật toán chọn khối dựa trên `article`/`main`, gợi ý từ
id/class, độ dài văn bản, số đoạn và lượng văn bản liên kết. Đây là thuật toán
đã có trong `content.py`; chưa coi việc tồn tại thuật toán là bằng chứng về
chất lượng đáp ứng bài tập. `content_html` là HTML nội dung đã chọn và làm sạch,
không phải HTML gốc của toàn trang.

Triển khai đủ các yêu cầu đã được cung cấp trong cùng phạm vi công việc.
Chia thành các chặng có đầu vào/đầu ra rõ ràng để kiểm thử, nhưng không đẩy
bóc tách tài liệu hoặc kiểm chứng thuật toán sang một yêu cầu tương lai.

| Yêu cầu của giảng viên | Đầu ra phải có khi bàn giao |
| --- | --- |
| Thuật toán tìm khối nội dung | Mã thuật toán, giải thích cách chấm điểm/chọn khối, fixture có nhãn và báo cáo đánh giá |
| Trang: tiêu đề, ngày đăng, tác giả, nội dung | Bản ghi có cấu trúc, nguồn của từng metadata và quy tắc xử lý thiếu/xung đột |
| Tài liệu: nội dung văn bản | Tải tài liệu được tham chiếu, bóc tách văn bản, ghi kết quả hoặc lỗi cụ thể cho từng URL |
| Nguồn giới thiệu và văn bản liên kết | Truy vấn cạnh đi vào trang/tài liệu, giữ nhiều nguồn và nhiều văn bản liên kết |
| Ảnh: nguồn giới thiệu | Danh mục URL ảnh và cạnh từ các trang tham chiếu; không cần tải ảnh để đáp ứng yêu cầu này |
| Đồ thị nguồn → đích : mô tả | `links.jsonl`, collection `links` và truy vấn/demo đồ thị |
| MongoDB và lược đồ tự xây dựng | Compose local có volume, validator/index, importer và tài liệu schema với ví dụ |
| Chia sẻ dữ liệu của project | Dataset Kaggle public chứa kết quả đầy đủ; pull theo phiên bản và import trên máy mới |

Luồng producer: crawl/bộ crawl hiện có → bóc tách trang và tài liệu → tổng hợp
ảnh/liên kết → kiểm tra corpus → publish Kaggle → import MongoDB.
Luồng consumer: clone → pull corpus đã xử lý → chạy MongoDB → import → truy vấn.
Người dùng tải dữ liệu không cần tải lại tài liệu từ các website nguồn.

Lucene là bước tiêu thụ tiếp theo ngoài các yêu cầu vừa được trích dẫn; phạm vi
này mô tả hợp đồng đọc dữ liệu cho nó, không mặc nhiên thêm tính năng tìm kiếm
mới. Không coi việc chỉ dựng MongoDB hoặc upload `final/` cũ là hoàn thành.

## Thuật toán tìm khối và bóc tách trang

Tận dụng thuật toán hiện có thay vì thay toàn bộ crawler. Ghi rõ phiên bản
extractor và kiểm chứng các quy tắc sau:

1. Parse DOM với encoding của phản hồi, bỏ script/style và thành phần phi nội dung
   khỏi việc tính điểm; thu thập liên kết nguồn trước khi cắt bỏ phần trang.
2. Ưu tiên candidate `article`/`main`, sau đó candidate có id/class gợi ý nội
   dung. Dùng điểm cơ sở `độ dài văn bản + 80 × số đoạn − độ dài văn bản liên kết`.
3. Nếu thiếu semantic tag và gợi ý, xét các khối DOM có văn bản/đoạn; loại menu,
   footer và các khối chủ yếu là liên kết trước khi chọn. Chỉ fallback về body
   khi không có candidate phù hợp, kèm cảnh báo để đánh giá chất lượng.
4. Giữ thứ tự đoạn, tiêu đề, danh sách, bảng và cấu trúc nội dung. Text được tạo
   từ cùng các block dùng sinh semantic HTML để hai đầu ra không mâu thuẫn.
5. Tiêu đề ưu tiên h1 của khối được chọn, rồi Open Graph và title của trang.
   Tác giả/ngày đăng đọc metadata, JSON-LD Article/NewsArticle phù hợp URL,
   microdata và byline/time có nhãn rõ trong bài. Không lấy ngày bất kỳ ở footer
   làm ngày đăng. Ghi giá trị raw, nguồn và cảnh báo khi có nhiều giá trị.

Không tự suy đoán tác giả hoặc ngày thiếu; không tự gán timezone cho ngày chỉ
có ngày/tháng/năm. Giữ giá trị gốc và mức chính xác; chỉ thêm thời điểm UTC khi
có đủ thông tin timezone để chuyển đổi chính xác.

Bổ sung lưu HTML đầu vào theo tùy chọn cho các lần crawl mới và tập đánh giá:
bytes nguồn hoặc DOM sau render, SHA-256, encoding, URL cuối và thời gian lấy.
Ghi rõ loại nguồn HTTP hay rendered DOM. Lưu ở local dưới đường dẫn theo hash;
không giả lập HTML gốc từ `content_html` và không bắt buộc crawl lại toàn bộ
79.630 trang. Corpus cũ giữ provenance extractor legacy/unknown; kết quả chạy
thuật toán mới có phiên bản riêng, không gắn nhãn hồi tố cho dữ liệu cũ.

Đánh giá bằng fixture HTML có nhãn khối/văn bản mong đợi, gồm semantic article,
div lồng nhau, menu dài, sidebar, trang không có semantic tag, bảng, tiếng Việt,
metadata thiếu/xung đột và HTML lỗi. Tách tập dùng chỉnh thuật toán và tập giữ
lại để đánh giá; báo precision/recall/F1 trên token nội dung với cùng quy tắc
chuẩn hóa Unicode/khoảng trắng, cùng độ đúng metadata theo nhãn. Báo cáo bao
gồm các ca sai và giới hạn; không chỉ nêu số bài crawl như thước đo chất lượng.

## Tải và bóc tách tệp tài liệu

Từ `assets` và các liên kết đã nhận diện, lập danh sách tài liệu duy nhất theo
URL chuẩn hóa. Thêm workflow làm giàu tài liệu từ corpus đã có, tái sử dụng
quy tắc URL/network/quota của crawler; chỉ yêu cầu tải tài liệu cần thiết,
không nâng toàn bộ phiên cũ sang chế độ tải cả ảnh và media.

Định dạng phải hỗ trợ tương ứng inventory crawler hiện có: PDF, DOC/DOCX,
XLS/XLSX, PPT/PPTX, ODT/ODS/ODP. MIME kiểm chứng từ phản hồi và nội dung tệp,
không chỉ từ đuôi URL. Tệp đã tải lưu local theo SHA-256; nhiều URL có cùng
bytes dùng chung kết quả parse nhưng vẫn giữ từng URL và nguồn giới thiệu.

### PyMuPDF local trước; điều kiện fallback nghiêm ngặt

PDF được đọc bằng PyMuPDF, lấy text theo từng trang, giữ số trang và thứ tự
nội dung. Dùng `get_text("text", sort=True)` và thông tin block khi cần kiểm
chứng thứ tự đọc. Không gọi chức năng OCR local của PyMuPDF.

Local đã có chữ đọc được thì giữ kết quả, kể cả văn bản ngắn, bố cục nhiều
cột, bảng bị lệch hoặc định dạng chưa đẹp. Các yếu tố đó không phải điều kiện
để gọi API. Không dùng ngưỡng tối thiểu số ký tự để đẩy trang ít chữ lên cloud.

Một trang chỉ là ứng viên cloud khi kết quả local rỗng hoặc chỉ còn whitespace,
control/replacement characters và không có chữ/số hữu ích, đồng thời trang có
nội dung hiển thị cần nhận dạng. Trang trắng thực sự giữ `empty`, không gửi đi.
Ký tự hợp lệ nhưng nghi ngờ chất lượng được đánh dấu trong báo cáo để kiểm tra;
không tự coi văn bản đó là thất bại và phát sinh request.

PDF trộn text/scan được xử lý theo từng trang: chỉ tách các trang ứng viên
thành PDF tạm để gửi LlamaParse, giữ bảng ánh xạ số trang gốc và ghép kết quả
đúng thứ tự. Trang local đã có chữ không bị parse cloud lại. Cache theo hash
nội dung nguồn, tập trang, parser và cấu hình; không phụ thuộc key đang dùng.

PyMuPDF không phải parser Office phổ quát. Với DOC/DOCX, XLS/XLSX, PPT/PPTX,
ODT/ODS/ODP, adapter local dùng LibreOffice headless chuyển sang PDF rồi đi qua
cùng pipeline PyMuPDF. Kiểm chứng việc giữ toàn bộ sheet/slide/nội dung, không
bỏ dữ liệu do print area. Thiếu converter là lỗi cấu hình local, không phải
lý do gửi toàn bộ file lên API. Chỉ cài converter cho môi trường làm giàu;
consumer tải corpus đã xử lý không cần cài.

Tệp mã hóa, hỏng, hết thời gian hoặc không hỗ trợ có trạng thái riêng. Lỗi
local do thiếu dependency hoặc sai cấu hình không tự kích hoạt cloud. Nếu
local parse lỗi trên một tệp còn đọc được bằng provider, chỉ fallback khi
không có text sử dụng được và ghi rõ lý do; không cố giải mã tệp có mật khẩu.

### LlamaParse với tier rẻ nhất

Dùng SDK `llama-cloud` (`from llama_cloud import LlamaCloud`), upload qua
`client.files.create(..., purpose="parse")` và Parse v2 với `tier="fast"`,
`version="latest"`, lấy text/markdown và thông tin trang. Lưu cả văn bản local
và cloud cùng provenance parser/version; metadata remote chỉ giữ các trường
cần thiết cho corpus, không chứa thông tin credentials.

Theo tài liệu đã kiểm tra, `fast` là tier rẻ nhất (1 credit/trang), có OCR cơ
bản nhưng không chạy mô hình agentic. Không tự nâng sang `cost_effective`,
`agentic` hoặc `agentic_plus`. Khi `fast` vẫn không ra chữ, ghi `parse_failed`
hoặc `empty` nếu xác minh được trang trống; giữ file để xử lý lại có chủ đích.
Không biến đầu ra null thành thành công bằng cách chỉ ghi một file rỗng.

SDK/provider có khác biệt theo phiên bản về `markdown_full`, `text_full` và
output theo trang. Adapter kiểm tra kiểu response của phiên bản SDK đã khóa,
lấy kết quả theo trang để ghép, và ghi TXT/Markdown UTF-8; chỉ dùng trường full
khi được phiên bản đó hỗ trợ. Ghi phiên bản provider thực trả về nếu có;
`latest` không được mô tả là đảm bảo tái lập parser theo thời gian.

Không còn service Tika hoặc pipeline Tesseract trong thiết kế. Compose MongoDB
giữ nguyên vai trò lưu dữ liệu; cloud chỉ xử lý các trang không đọc được local.

### Chuyển key khi hết quota, retry và checkpoint

Đọc `.env` tại root repository, độc lập working directory. Các biến hiện có
là `LLAMAPARSE_KEY1`, `LLAMAPARSE_KEY2`, `LLAMAPARSE_KEY3`; hỗ trợ tiếp các số
thứ tự theo cách sắp xếp số, bỏ giá trị trống và key trùng. Biến môi trường
đã export ưu tiên hơn `.env` cùng tên. Không sửa hoặc ghi đè key của người dùng.
Log chỉ dùng nhãn biến; lọc credentials khỏi exception/request/response trước
khi log. File `.env` đã được Git bỏ qua; ví dụ cấu hình chỉ chứa giá trị trống.

- Dùng key đầu tiên còn khả dụng, không round-robin mọi request.
- Chỉ chuyển sang key kế tiếp khi có bằng chứng quota/credits đã hết: HTTP 402
  kèm lỗi credits/quota được nhận diện, hoặc job thất bại với cùng lỗi đã xác
  minh. HTTP 402 do giới hạn lưu file không bị nhầm thành hết credits.
- Key hết quota bị bỏ qua cho các job mới trong lần chạy đó. Mỗi job thử tối
  đa một lần trên mỗi key đối với lỗi quota; không vòng lặp vô hạn.
- HTTP 429 giới hạn tốc độ: exponential backoff có jitter, dùng `Retry-After`
  nếu có; không xoay key để xử lý rate limit. HTTP 5xx/lỗi kết nối đọc kết quả
  cũng retry hữu hạn. Một lớp quản lý retry để tránh nhân retries của SDK.
- 401/403, request sai, tệp hỏng hoặc không hỗ trợ được báo theo nguyên nhân;
  không diễn giải thành quota và không thử cả ba key một cách vô ích.
- Khi chuyển key thuộc project khác, upload lại file bằng key mới. File ID và
  job ID đã tạo phải đi kèm đúng nhãn key/phạm vi; không poll job bằng key khác
  nếu chưa có bằng chứng cùng quyền truy cập.
- Ghi checkpoint file ID, job ID và nhãn key ngay khi có; tách create → poll →
  get để resume. Timeout khi poll không tự tạo job mới. Nếu submit mất phản hồi
  và chưa biết job đã được tạo chưa, ghi `submission_unknown` để đối chiếu;
  không mù quáng submit lại và có thể tính phí hai lần.
- Nếu tất cả key hết quota, giữ phần cloud đang chờ với `quota_blocked`, vẫn
  có thể hoàn thành tài liệu local; báo cách chạy tiếp sau khi quota có lại.
  Không đánh dấu tài liệu thiếu trang là đã bóc tách hoàn chỉnh.

Ba key khác nhau không đồng nghĩa có ba ngân sách độc lập. Nếu chúng dùng
chung quota tổ chức thì đổi key không tạo thêm credits. Chỉ dùng keys do người
dùng cung cấp; không tạo tài khoản hoặc tăng gói/bật overage tự động. Chưa gọi
API với key thật ở giai đoạn thiết kế; kiểm thử fallback dùng lỗi giả lập,
không cố tiêu hết quota thật để kiểm tra.

Trạng thái xử lý gồm `pending`, `downloaded`, `extracted`, `empty`,
`download_failed`, `parse_failed`, `encrypted`, `unsupported`, `limit_exceeded`,
`quota_blocked`, `submission_unknown`.
`pending`/`downloaded`/`quota_blocked`/`submission_unknown` là trạng thái chưa
hoàn tất và phải bằng 0 trước khi đóng
gói bản đầy đủ. Lỗi mạng có retry hữu hạn, timeout và quota được cấu hình.
Giữ URL gốc/cuối, HTTP status, MIME, checksum, thời gian tải, extractor version,
route local/cloud, tập trang fallback, tier, job ID local và lỗi. Job ID và
nhãn key chỉ nằm trong checkpoint local; không phát hành lên Kaggle.
Chạy lại tiếp tục phần còn thiếu, không tải/parse lại kết quả
hợp lệ có cùng checksum và phiên bản extractor.

Lỗi do nguồn thực tế được ghi nhận trong báo cáo coverage theo định dạng và
nguyên nhân. Đầy đủ chức năng không có nghĩa khẳng định mọi URL đều còn truy
cập được; nhưng không được bỏ qua một định dạng đã cam kết hoặc để mọi tài liệu
ở trạng thái chưa bóc tách rồi coi bài tập hoàn thành.

## Ảnh và nguồn giới thiệu

Với trang mới, thu thập tham chiếu trên toàn DOM trước khi chọn khối nội dung:
hyperlink, img/srcset và picture/source có ngữ cảnh ảnh. Giữ URL chuẩn hóa,
URL trang nguồn, anchor text (nếu có), alt, caption và loại quan hệ riêng biệt.
CSS background không thuộc tập tham chiếu DOM này và được nêu rõ trong mô tả.

Corpus cũ chỉ có asset trong khối nội dung và các hyperlink đã lưu. Ghi phạm
vi quan sát này trong manifest; không tuyên bố dữ liệu cũ chứa toàn bộ ảnh của
trang. Không cần tải bytes ảnh hay OCR ảnh độc lập vì yêu cầu ảnh chỉ là nguồn
giới thiệu. OCR PDF scan thuộc workflow tài liệu.

## MongoDB local

- Thêm `compose.yaml` tại root, service `mongodb`, dùng image chính thức
  `mongo:8.0`; khóa dòng phiên bản thay vì dùng `latest`.
- Publish cổng mặc định `127.0.0.1:27017`; cho đổi cổng host qua `MONGO_PORT`.
- Named volume `mongodb_data` gắn vào `/data/db`, theo namespace project Compose.
- Healthcheck dùng `mongosh` chạy lệnh `ping`, có thời gian chờ khởi động.
- Dùng một standalone MongoDB cho môi trường học tập local, không cần replica set.
- Mặc định local không bật authentication; đây là cấu hình chỉ publish qua
  loopback. Importer nhận `MONGODB_URI` để có thể kết nối cấu hình khác.
- Database mặc định `information_retrieval`.
- `docker compose up -d mongodb` khởi động; `docker compose down` giữ volume.
  Hướng dẫn dọn dẹp phân biệt rõ thao tác xóa volume với dừng container.

Schema và index được tạo bởi công cụ import có thể chạy lại, không phụ thuộc
vào init script chỉ chạy khi volume trống. Không thêm giao diện quản trị web.

## Bản dữ liệu dùng chung

Payload gồm dữ liệu trang, kết quả làm giàu và manifest:

- `articles.jsonl`: giữ nguyên từng bản ghi crawler, bao gồm nội dung có cấu
  trúc, metadata, links, assets, cảnh báo và thông tin bài trùng.
- `urls.txt`: tập URL tương ứng với các bản ghi bài viết.
- `README.json`: mô tả và số lượng từ lần xuất dữ liệu trang nguồn.
- `documents.jsonl`: văn bản và metadata/trạng thái bóc tách cho từng URL tài liệu.
- `images.jsonl`: danh mục URL ảnh đã quan sát.
- `links.jsonl`: các cạnh nguồn → đích, loại quan hệ và văn bản mô tả.
- `extraction-report.json`: coverage trang/tài liệu/ảnh, tỷ lệ thiếu metadata,
  thống kê lỗi, phiên bản extractor và phạm vi quan sát của corpus.
- `manifest.json`: `schema_version`, `snapshot_id`, thời gian đóng gói,
  thời gian crawl, số lượng từng loại, trạng thái hoàn tất làm giàu,
  SHA-256 + kích thước của tất cả file payload bên trên.

`snapshot_id` là SHA-256 của danh sách tên file và checksum được serialize theo
quy tắc cố định. Số phiên bản Kaggle được ghi trong cấu hình khóa của repository
sau khi publish thành công, không ghi ngược vào payload đã upload.

Thư mục đóng gói chỉ chứa các file trong danh sách trên cùng metadata cần cho
Kaggle. Dữ liệu SQLite resume, log, bản phục hồi và thông tin môi trường không
thuộc gói phát hành. Bytes tài liệu và HTML gốc giữ local để có thể chạy lại
extractor; Kaggle chia sẻ kết quả có cấu trúc, không bắt người tải tải thêm
các file nhị phân. Tập fixture phục vụ chứng minh thuật toán nằm trong repo.
Nguồn crawl được đọc nguyên trạng; đầu ra làm giàu đặt ở thư mục riêng.

Đóng gói kiểm tra JSONL, URL duy nhất, tập URL khớp `urls.txt`, số lượng khớp
README, checksum và tham chiếu `duplicate_of`. Bài trùng phải có nội dung trực
tiếp hoặc trỏ tới chủ sở hữu hợp lệ có nội dung; tham chiếu thiếu/vòng lặp là lỗi.
Thiếu tác giả hoặc ngày đăng được giữ là giá trị thiếu, không suy đoán.
Đồng thời kiểm tra mọi tài liệu ứng viên có bản ghi trạng thái kết thúc,
không còn trạng thái chưa hoàn tất, số liệu báo cáo khớp payload và mỗi cạnh có
nguồn truy vết. Bản chỉ có ba file final cũ được nhận làm đầu vào làm giàu,
không được gắn nhãn bản phát hành đáp ứng đầy đủ bài tập.

## Publish và pull

Công cụ Python trong `scripts/` cung cấp các thao tác `enrich-documents`,
`build-graph`, `evaluate-extraction`, `package`, `publish`, `pull`, `import-mongo`;
dùng Kaggle CLI cho giao tiếp Kaggle. Môi trường công cụ
dữ liệu cài riêng, không buộc người chỉ tải dữ liệu cài Scrapy hoặc Playwright.

`config/` lưu metadata dataset và file khóa gồm owner/slug, phiên bản Kaggle,
snapshot ID cùng checksum manifest. Chủ dataset và giấy phép là tham số cấu
hình bắt buộc trước lần publish đầu; không tự suy ra quyền cấp phép từ việc
nội dung nguồn có thể truy cập công khai. Giá trị thực sẽ được điền khi cấu
hình tài khoản publish, không phải điều kiện để phát triển và kiểm thử local.
Credentials dùng cơ chế của Kaggle CLI và không được ghi vào Git.

Publish tạo dataset public khi chạy chế độ tạo mới; chế độ cập nhật tạo phiên
bản mới cho dataset đã cấu hình. Không tự chuyển từ cập nhật sang tạo mới khi
gặp lỗi mạng hoặc lỗi xác thực. Không xóa các phiên bản cũ. Chỉ cập nhật file
khóa sau khi phiên bản mới ở trạng thái sẵn sàng và đối chiếu được metadata
của đúng lần publish; lỗi hoặc timeout phải báo rõ, không tuyên bố thành công.

Pull tải đúng phiên bản trong file khóa vào thư mục tạm, xác minh manifest và
các file rồi mới công bố tại `data/corpus/<snapshot_id>/`. Bản đã tồn tại hợp
lệ được tái sử dụng; dữ liệu sai checksum hoặc tải dở không được dùng làm đầu
vào. Không tự lấy `latest` thay cho phiên bản đã khóa. Dữ liệu đặt dưới `data/`
và thư mục staging được Git bỏ qua.

## Lược đồ MongoDB

Mỗi collection dữ liệu chứa `snapshot_id`. ID bản ghi được tạo xác định từ
snapshot ID, loại đối tượng và định danh nguồn. Import lại cùng snapshot là
idempotent; các snapshot khác không trộn lẫn kết quả.

| Collection | Nội dung và trường chính |
| --- | --- |
| `datasets` | `_id = snapshot_id`, `schema_version`, nguồn Kaggle hoặc nguồn local, checksum, thời gian crawl/import, `status`, số lượng từng loại và lỗi import |
| `pages` | `_id`, `snapshot_id`, `url`, `title`, `author`, `published_at`, `text`, `source_record` chứa toàn bộ bản ghi crawler gốc |
| `documents` | `_id`, `snapshot_id`, `url`, `final_url`, `mime_type`, `sha256`, `text`, `extraction_status`, parser/version, OCR mode, timestamps, lỗi và metadata nguồn |
| `images` | `_id`, `snapshot_id`, `url`; nguồn giới thiệu lấy từ `links` |
| `links` | `_id`, `snapshot_id`, `source_url`, `target_url`, `relation`, `target_type`, `anchor_text`, `alt`, `caption` |

`pages.text` là nội dung đã giải quyết tham chiếu bài trùng, để các bước sau
đọc trực tiếp; `source_record` giữ dữ liệu gốc và `duplicate_of` để truy vết.
Không tự điền lại HTML hoặc metadata thiếu của một trang từ trang khác.
Tài liệu bóc tách thành công có `text`; trường hợp thất bại có `text = null`
và lỗi cụ thể. Giá trị `empty` dùng chuỗi rỗng và lý do không có văn bản.
Các bản ghi đối tượng luôn có thể truy vấn nguồn giới thiệu qua collection
`links`, kể cả đối tượng đích không tải được.

Validator mô tả kiểu dữ liệu bắt buộc và các trường được null. Index duy nhất
trên `(snapshot_id, url)` ở ba collection đối tượng; index trên
`(snapshot_id, source_url)` và `(snapshot_id, target_url)` ở `links`.
Index tìm kiếm toàn văn mới không thuộc các yêu cầu đã trích dẫn; downstream
có thể đọc text từ pages/documents theo hợp đồng schema.

### Quan hệ nguồn giới thiệu và đồ thị

- Mỗi phần tử `page.links` tạo cạnh `relation = hyperlink`; `anchor_text` giữ
  văn bản crawler đã ghi nhận, kể cả chuỗi rỗng.
- Mỗi phần tử `page.assets` và các tham chiếu toàn DOM bổ sung tạo cạnh asset
  từ URL trang tới URL asset; gộp cạnh trùng theo quy tắc bên dưới.
  `alt` và `caption` giữ riêng; không giả lập anchor text từ caption.
- Tài liệu/ảnh được xác định bằng vai trò asset, MIME kiểm chứng và kết quả
  làm giàu. Liên kết không có
  bằng chứng về loại đích giữ `target_type = unknown`; không coi mọi đích là trang.
- Cạnh cùng nguồn, đích, relation và nội dung mô tả được gộp; các mô tả khác
  nhau cho cùng cặp URL được giữ. ID cạnh là hash của bộ trường này và snapshot.
- Giữ cạnh tới URL ngoài corpus hoặc ngoài host; không yêu cầu đích phải có
  bản ghi `pages`. Crawl thành công một trang không có nghĩa mọi đích đã tải.
- Nguồn giới thiệu của đối tượng X là các cạnh có `target_url = X` trong cùng
  snapshot. Không lưu một mảng referrer tăng không giới hạn trong mỗi đối tượng.
- Đồ thị chỉ phản ánh liên kết đã quan sát từ các trang có trong bản dữ liệu;
  không được mô tả là đồ thị đầy đủ của mọi website nguồn.

## Import và xử lý lỗi

Importer kiểm tra bộ dữ liệu trước khi ghi DB, đọc JSONL theo luồng và ghi theo
batch. Tránh nạp toàn bộ corpus khoảng 2,1 GB vào RAM; bảng tra cứu bài trùng
có thể dùng chỉ mục tạm trên đĩa. Không crawl lại hoặc tải asset trong import.

`datasets.status` chuyển `importing` → `ready` sau khi đối chiếu số lượng và
hoàn thành index/validation. Lỗi chuyển trạng thái `failed`; chạy lại upsert
cùng ID để hoàn tất. Downstream chỉ dùng snapshot `ready`. Import đồng thời
cùng snapshot bị từ chối bằng khóa có thể giải phóng khi tiến trình kết thúc;
snapshot khác có thể được xử lý riêng.

Nếu bản ghi không vừa giới hạn BSON của MongoDB hoặc không phù hợp schema,
không được cắt nội dung hay bỏ qua âm thầm. Payload lớn được chuyển sang GridFS
với checksum và ID xác định; bản ghi đối tượng lưu `text_ref`/`source_record_ref`
thay cho trường inline tương ứng. Reader chung phục hồi đúng UTF-8/nội dung
gốc, nên truy vấn/demo/downstream không tự xử lý hai cách lưu. Validator quy
định chính xác trường inline hoặc tham chiếu, không cho tồn tại hai giá trị
mâu thuẫn. Import lại không nhân bản GridFS blob; chỉ đánh dấu ready khi các
tham chiếu đều đọc được và đúng checksum.

Bản ghi sai schema vẫn làm import thất bại. Lỗi mạng, xác thực hoặc healthcheck
không đạt được báo thành lỗi thao tác, không làm mất corpus local đã kiểm tra.
`datasets.status = ready` nghĩa là import hoàn chỉnh; coverage tài liệu thành
công/thất bại là số liệu riêng, không bị che bởi trạng thái import.

## Các chặng triển khai trong cùng phạm vi

1. Chuẩn hóa hợp đồng dữ liệu, schema MongoDB, Compose và reader chung.
2. Hoàn thiện thuật toán chọn khối, metadata, lưu nguồn tùy chọn và tập đánh giá.
3. Tải tài liệu theo inventory, đọc local bằng PyMuPDF, fallback LlamaParse
   `fast` khi không ra chữ, chuyển key khi hết quota và tiếp tục khi chạy lại.
4. Tổng hợp ảnh, cạnh liên kết và báo cáo coverage trên corpus thật.
5. Đóng gói/publish/pull Kaggle, import MongoDB và truy vấn minh họa.
6. Kiểm thử tích hợp, báo cáo đánh giá và tài liệu bàn giao đủ từng yêu cầu.

Các chặng là thứ tự thực thi, không phải cắt bỏ chức năng khỏi lần bàn giao.
Implementation plan phải ánh xạ mọi yêu cầu vào code, kiểm thử và bằng chứng.

## Tài liệu và kiểm chứng

README root mô tả luồng mới: cài công cụ dữ liệu → pull → khởi động MongoDB →
import → chạy các truy vấn mẫu. Mô tả schema riêng giải thích từng trường,
null, ID, index, snapshot, nguồn giới thiệu và giới hạn của bộ dữ liệu hiện tại.
Hướng dẫn producer nêu rõ package/publish là thao tác chủ động.
Tài liệu thuật toán trình bày candidate, công thức chấm điểm, fallback, độ phức
tạp thực tế và các ca sai; hướng dẫn bóc tách tệp mô tả định dạng, OCR,
status, resume và cách đọc coverage. Có lệnh/demo truy vấn đủ: bài có metadata,
text tài liệu, nguồn giới thiệu của ảnh, cạnh đi vào/đi ra với anchor text.

Kiểm chứng cần có:

1. `docker compose config`; khởi động và healthcheck/ping MongoDB.
2. Ghi bản ghi thử vào database test, recreate container, xác nhận volume giữ
   bản ghi rồi xóa dữ liệu test. Không thao tác xóa volume dữ liệu thật.
3. Fixture corpus gồm trang bình thường, bài trùng, metadata thiếu, tài liệu,
   ảnh, liên kết ngoài corpus và nhiều anchor text cùng đích.
4. Package/pull thất bại đúng khi checksum sai, thiếu file, count sai hoặc
   tham chiếu trùng hỏng; lỗi tải không ảnh hưởng snapshot hợp lệ đã có.
5. Import hai lần cho cùng kết quả; truy vấn nguồn giới thiệu và đồ thị khớp
   fixture; snapshot lỗi không được đánh dấu `ready`.
6. Kiểm tra bộ final thật trước publish và báo rõ số lượng, dữ liệu thiếu,
   kích thước bất thường. Không coi test giả lập Kaggle là xác nhận upload thật.
7. Thuật toán chọn khối được kiểm thử trên tập có nhãn giữ lại; báo cáo
   precision/recall/F1 cùng ví dụ sai. Các fixture hồi quy có văn bản mong đợi
   chính xác; không sửa nhãn để hợp thức hóa đầu ra của thuật toán.
8. Parser được kiểm thử bằng tài liệu thật dạng fixture cho từng định dạng đã
   cam kết; có PDF scan tiếng Việt và PDF trộn text/scan. Kiểm chứng text mong
   đợi, nguồn giới thiệu, lỗi tệp hỏng/mã hóa và tiếp tục sau gián đoạn.
   Bắt buộc kiểm thử không gọi API khi local có chữ (kể cả ngắn, nhiều cột,
   bảng lệch), không gọi với trang trắng, chỉ gửi trang không đọc được và ghép
   đúng số trang. Kiểm thử quota key 1 → 2 → 3, hết toàn bộ, 402 storage không
   bị phân loại sai, 429 backoff không đổi key, timeout không tạo lại job,
   cache tránh tính phí lại và log không lộ key. Kiểm thử API mock được báo
   riêng với kiểm chứng live trên tài liệu mẫu nhỏ.
9. Test nội dung vượt giới hạn BSON: import và đọc lại nguyên vẹn qua GridFS,
   import lần hai không tăng số blob. Không gọi `ready` khi blob thiếu.
10. Workflow làm giàu chạy trên inventory tài liệu corpus thật và xuất báo cáo
    trạng thái kết thúc. Mọi giới hạn/quota hoặc lỗi nguồn phải được phản ánh
    trong coverage; nếu chưa chạy xong thì chưa tuyên bố hoàn thành dữ liệu.
11. Khi owner/slug và giấy phép đã cấu hình, kiểm chứng một vòng publish rồi
    pull đúng phiên bản và import; ghi rõ phần nào chưa chạy được nếu tài khoản
    hoặc kết nối là điều kiện còn thiếu.

## Dọn repo và bảo toàn dữ liệu

Đã dọn các đầu ra có thể tạo lại `lucene/index/`, `lucene/output/` và phiên
làm việc cũ `crawler/.superpowers/`. Giữ các skill mới thêm, mã nguồn, seed,
lịch sử thiết kế đã được theo dõi và toàn bộ dữ liệu crawl/resume/phục hồi.
Không xóa dữ liệu nguồn chỉ vì đã có bản upload Kaggle.

## Tham chiếu kỹ thuật

- MongoDB Docker: https://www.mongodb.com/docs/v8.0/tutorial/install-mongodb-community-with-docker/
- Docker Official Image: https://hub.docker.com/_/mongo
- Compose volumes: https://docs.docker.com/reference/compose-file/volumes/
- Kaggle CLI: https://github.com/Kaggle/kaggle-cli/blob/main/docs/datasets.md
- Download theo phiên bản: https://github.com/Kaggle/kaggle-cli/blob/main/src/kaggle/api/kaggle_api_extended.py
- PyMuPDF text/page APIs: https://pymupdf.readthedocs.io/en/latest/page.html
- LibreOffice CLI: https://help.libreoffice.org/latest/en-US/text/shared/guide/start_parameters.html
- LlamaParse Parse: https://developers.llamaindex.ai/llamaparse/parse/
- LlamaParse tiers: https://developers.llamaindex.ai/llamaparse/parse/guides/tiers/
- LlamaParse OCR: https://developers.llamaindex.ai/llamaparse/parse/features/ocr-and-languages/
- LlamaParse pricing: https://developers.llamaindex.ai/llamaparse/general/pricing/
- LlamaParse errors: https://developers.llamaindex.ai/llamaparse/general/troubleshooting/
- LlamaParse rate limits: https://developers.llamaindex.ai/llamaparse/general/rate_limits/
- MongoDB GridFS: https://www.mongodb.com/docs/manual/core/gridfs/

## Trạng thái review

Đây là spec mở rộng để người dùng review, thay thế phạm vi đợt đầu trước đó.
Chưa triển khai Compose, extractor mới hoặc công cụ dữ liệu.
Sau khi spec được duyệt, viết implementation plan và chọn cách thực thi theo
workflow brainstorming đã được yêu cầu.
