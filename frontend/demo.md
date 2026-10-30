# Kịch bản demo (khoảng 5 phút)

**Link dùng để demo:** `https://soict.hust.edu.vn/` (8 trang trong khoảng 10 giây).

## Chuẩn bị (làm trước khi có người xem)

Chạy ở thư mục gốc repo:

```bash
docker compose up -d
```

1. Chờ `docker compose ps` báo các dịch vụ đã `healthy`.
2. Mở http://localhost:8080. Dòng đầu trang phải hiện "… văn bản trong chỉ mục" (khoảng 116.000).
3. Làm nóng: sang tab **Tìm kiếm**, tìm `học phí` và `title:tuyển sinh`. Lần đầu có thể chậm tới 15 giây, các lần sau nhanh.
4. Về tab **Crawl**, để trống các ô.

## Bước 1: Crawl một link (1 phút)

| Thao tác | Trên màn hình |
|---|---|
| Nhập `https://soict.hust.edu.vn/` vào ô link | |
| Nhập `8` vào **Số trang tối đa** | |
| Bấm **Crawl** | Dòng "Đã lưu N / 8 trang" tăng dần, danh sách URL hiện ra bên dưới |
| Chờ khoảng 10 giây | "Đã lưu 8 / 8 trang", "Đã xong", có nút **Tìm thử** |

**Nói:** link đi qua năm khối của crawler: mediator xếp hàng, downloader tải, reader bóc tách, merger lọc trùng và lan
link, storage ghi vào MongoDB. Hệ thống chỉ lan trong cùng host. Để trống số trang thì lan hết host.

## Bước 2: Tìm thấy ngay trang vừa crawl (1 phút)

| Thao tác | Trên màn hình |
|---|---|
| Chờ thêm khoảng 15 giây rồi bấm **Tìm thử** | Sang tab Tìm kiếm, ô truy vấn tự điền tiêu đề một trang vừa lưu |
| Chỉ vào các kết quả đầu | Các trang của `soict.hust.edu.vn`, từ khoá được tô sáng |

**Nói:** chỉ mục không lập lại từ đầu. Cứ 10 giây, lucene đọc các bản ghi mới trong MongoDB rồi cập nhật tăng dần.

## Bước 3: Xem dữ liệu đã thu thập (2 phút)

**Nói:** crawler không chỉ lấy HTML. Trang được bóc tách nội dung, ảnh và tệp đính kèm. Tệp PDF, Word, Excel, PowerPoint
đều được đọc thành văn bản nên tìm kiếm được, và hệ thống nhớ trang nào dẫn tới tệp nào.

| Thao tác | Trên màn hình |
|---|---|
| Sang tab **Dữ liệu** | Dòng tổng: khoảng 141.000 trang, 5.800 tệp, 114.000 ảnh. Bên dưới là thẻ **Trang tiêu biểu**: "SoICT Summer School on Edge AI" |
| Bấm **Xem trang này** | Toàn bộ nội dung trang, **8 tệp PDF đính kèm**, 56 ảnh, liên kết đi |
| Trong **Tệp đính kèm**, bấm `Meta-Learning-for-Adaptive-Edge-AI.pdf` (nhãn **LlamaParse · 51 trang**) | Toàn bộ văn bản của slide (khoảng 73.000 ký tự) |
| Bấm nút **TRANG** ở mục **Trang dẫn tới tệp này** | Quay lại trang, rồi bấm **Đóng** |
| Trong thẻ, mục **Tệp tiêu biểu theo định dạng**, bấm lần lượt **DOCX**, **XLSX**, **PPTX** (bấm **Đóng** sau mỗi tệp) | Tài liệu mô tả hệ thống, thời khóa biểu (tên sheet trong ngoặc vuông), hướng dẫn HUST Care: đều xem được toàn bộ văn bản |
| Kéo xuống bảng **Trang** | Các trang soict vừa crawl nằm trên cùng, cột Cập nhật là giờ hiện tại |
| Bấm **Ảnh** | Lưới ảnh đã tải về |

**Nói về LlamaParse:** slide có nhiều hình và chữ nằm trong ảnh, thư viện đọc PDF thông thường không lấy được. Hệ thống gửi
các trang đó sang LlamaParse để OCR, rồi lưu kết quả vào cache để không tốn phí lần sau.

Tích **Cả ảnh chưa tải** chỉ khi có người hỏi: ảnh của trang vừa crawl hiện ô xám, vì ảnh được tải riêng bằng
`crawler pictures fetch`, không nằm trong pipeline năm khối.

## Bước 4: Các tính năng tìm kiếm (2 phút)

Sang tab **Tìm kiếm**, gõ lần lượt:

| Gõ | Chỉ ra |
|---|---|
| `điểm chuẩn đại học bách khoa` | Kết quả đúng chủ đề, có điểm BM25 (tf-idf) và PageRank dưới mỗi kết quả. Mở **Truy vấn Lucene** cuối trang để thấy cách tách từ tiếng Việt |
| `ktx` | Dòng "Đã mở rộng truy vấn: + ký túc xá" (từ viết tắt) |
| `ky tuc xa` | Gõ không dấu vẫn ra trang "Ký túc xá" |
| `tuyển sinhh` | 0 kết quả, hiện "Có phải bạn muốn tìm: tuyển sinh". Bấm vào gợi ý để tìm lại |
| `học bổng AND (thạc sĩ OR tiến sĩ)` | Toán tử Boolean |
| `title:học bổng` | Chỉ tìm trong tiêu đề |

## Nếu có thời gian: nút Dừng (30 giây)

Về tab **Crawl**, nhập lại link soict, **để trống** số trang, bấm **Crawl**. Khi số trang đang tăng, bấm **Dừng**: số trang
ngừng tăng, hiện "Đã dừng".

## Nếu có sự cố

| Hiện tượng | Xử lý |
|---|---|
| Crawl đứng ở 0 trang | Mạng chập chờn, job tự thử lại sau 15 giây nên cứ chờ. Vẫn không được thì bấm **Dừng** và crawl lại |
| Tìm kiếm báo "Không kết nối được máy chủ tìm kiếm" | Lucene đang khởi động, chờ khoảng 30 giây rồi tìm lại |
| Chưa thấy trang vừa crawl khi tìm | Chỉ mục cập nhật chậm hơn dự kiến, chờ thêm 15 giây rồi tìm lại |
| Link `ctsv.hust.edu.vn`, `hust.edu.vn` chạy chậm hoặc thiếu trang | Do site chặn, không phải lỗi hệ thống. Dùng link soict |
| `docker compose up` báo merger lỗi, log ghi `is in use by` | Chờ một phút rồi chạy lại lệnh |
