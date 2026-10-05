# Chia sẻ dữ liệu qua Kaggle và lưu trữ MongoDB local

Ngày: 2026-10-05

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

## Hiện trạng và phạm vi đợt đầu

`crawler/data/crawl/final/README.json` ghi 79.630 bài viết và 79.630 URL,
với nguồn crawl kết thúc ngày 2026-09-24. Ba file hiện có là `articles.jsonl`,
`urls.txt`, `README.json`. Manifest crawl ghi `complete_with_failures`,
1.074 URL thất bại và không có tệp nhị phân đã tải.

Crawler hiện có thuật toán chọn khối dựa trên `article`/`main`, gợi ý từ
id/class, độ dài văn bản, số đoạn và lượng văn bản liên kết. Đây là thuật toán
đã có trong `content.py`; chưa coi việc tồn tại thuật toán là bằng chứng về
chất lượng đáp ứng bài tập. `content_html` là HTML nội dung đã chọn và làm sạch,
không phải HTML gốc của toàn trang.

Đợt đầu gồm một luồng hoàn chỉnh: đóng gói dữ liệu trang hiện có → publish/pull
Kaggle → import MongoDB → truy vấn nội dung và các quan hệ đã quan sát được.
Bao gồm Compose, schema, công cụ dữ liệu, kiểm thử và hướng dẫn sử dụng.

Các phần sau được giữ thành công việc tiếp theo, chưa được đánh dấu hoàn thành:

1. Tải tệp tài liệu từ các URL đã ghi nhận và bóc tách nội dung văn bản.
2. Đánh giá/cải tiến thuật toán tìm khối; lưu HTML gốc nếu cần tái chạy thuật toán
   trên cùng đầu vào. Bộ dữ liệu hiện tại không thể tái tạo đầy đủ HTML gốc.
3. Tích hợp Lucene với nguồn dữ liệu mới. Lucene hiện chỉ đọc thư mục tài liệu;
   việc import MongoDB không tự làm Lucene đọc được dữ liệu.

Các phần này cần thiết cho bài tập đầy đủ, nhưng không cần ghép chung vào lần
triển khai nền tảng chia sẻ và lưu dữ liệu. Tệp tài liệu được ghi nhận ở trạng
thái chưa bóc tách, không được coi là đã có nội dung chỉ vì có URL.

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

Payload gồm ba file hiện có và `manifest.json` mới:

- `articles.jsonl`: giữ nguyên từng bản ghi crawler, bao gồm nội dung có cấu
  trúc, metadata, links, assets, cảnh báo và thông tin bài trùng.
- `urls.txt`: tập URL tương ứng với các bản ghi bài viết.
- `README.json`: mô tả và số lượng từ lần xuất dữ liệu nguồn.
- `manifest.json`: `schema_version`, `snapshot_id`, thời gian đóng gói,
  thời gian crawl, số bài/URL và SHA-256 + kích thước của ba file nguồn.

`snapshot_id` là SHA-256 của danh sách tên file và checksum được serialize theo
quy tắc cố định. Số phiên bản Kaggle được ghi trong cấu hình khóa của repository
sau khi publish thành công, không ghi ngược vào payload đã upload.

Thư mục đóng gói chỉ chứa các file trong danh sách trên cùng metadata cần cho
Kaggle. Dữ liệu SQLite resume, log, bản phục hồi và thông tin môi trường không
thuộc gói phát hành. Nguồn crawl được đọc nguyên trạng.

Đóng gói kiểm tra JSONL, URL duy nhất, tập URL khớp `urls.txt`, số lượng khớp
README, checksum và tham chiếu `duplicate_of`. Bài trùng phải có nội dung trực
tiếp hoặc trỏ tới chủ sở hữu hợp lệ có nội dung; tham chiếu thiếu/vòng lặp là lỗi.
Thiếu tác giả hoặc ngày đăng được giữ là giá trị thiếu, không suy đoán.

## Publish và pull

Công cụ Python trong `scripts/` cung cấp các thao tác `package`, `publish`,
`pull`, `import-mongo`; dùng Kaggle CLI cho giao tiếp Kaggle. Môi trường công cụ
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
| `documents` | `_id`, `snapshot_id`, `url`, `text: null`, `extraction_status: "not_extracted"` ở đợt đầu |
| `images` | `_id`, `snapshot_id`, `url`; nguồn giới thiệu lấy từ `links` |
| `links` | `_id`, `snapshot_id`, `source_url`, `target_url`, `relation`, `target_type`, `anchor_text`, `alt`, `caption` |

`pages.text` là nội dung đã giải quyết tham chiếu bài trùng, để các bước sau
đọc trực tiếp; `source_record` giữ dữ liệu gốc và `duplicate_of` để truy vết.
Không tự điền lại HTML hoặc metadata thiếu của một trang từ trang khác.

Validator mô tả kiểu dữ liệu bắt buộc và các trường được null. Index duy nhất
trên `(snapshot_id, url)` ở ba collection đối tượng; index trên
`(snapshot_id, source_url)` và `(snapshot_id, target_url)` ở `links`.
Index nội dung tìm kiếm đầy đủ chưa thuộc đợt đầu.

### Quan hệ nguồn giới thiệu và đồ thị

- Mỗi phần tử `page.links` tạo cạnh `relation = hyperlink`; `anchor_text` giữ
  văn bản crawler đã ghi nhận, kể cả chuỗi rỗng.
- Mỗi phần tử `page.assets` tạo cạnh tham chiếu asset từ URL trang tới URL asset.
  `alt` và `caption` giữ riêng; không giả lập anchor text từ caption.
- Tài liệu/ảnh được xác định bằng vai trò asset đã ghi nhận. Liên kết không có
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
báo URL và nguyên nhân, đánh dấu snapshot chưa sẵn sàng; không cắt nội dung
hay bỏ qua âm thầm. Lỗi mạng, xác thực hoặc healthcheck không đạt được báo
thành lỗi thao tác, không làm mất bộ dữ liệu local đã kiểm tra.

## Tài liệu và kiểm chứng

README root mô tả luồng mới: cài công cụ dữ liệu → pull → khởi động MongoDB →
import → chạy các truy vấn mẫu. Mô tả schema riêng giải thích từng trường,
null, ID, index, snapshot, nguồn giới thiệu và giới hạn của bộ dữ liệu hiện tại.
Hướng dẫn producer nêu rõ package/publish là thao tác chủ động.

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
7. Khi owner/slug và giấy phép đã cấu hình, kiểm chứng một vòng publish rồi
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

## Trạng thái review

Đây là spec để người dùng review. Chưa triển khai Compose hoặc công cụ dữ liệu.
Sau khi spec được duyệt, viết implementation plan và chọn cách thực thi theo
workflow brainstorming đã được yêu cầu.
