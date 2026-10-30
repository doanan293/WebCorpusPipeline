# Quy tắc viết báo cáo

Quy tắc chung để trình bày báo cáo học phần, bài tập lớn hoặc đồ án theo phong cách HUST, dùng lại cho mọi báo cáo.
Khi giảng viên hoặc học phần có mẫu riêng, ưu tiên mẫu đó.

Báo cáo biên soạn và chỉnh sửa trên Word (`.docx`). File này chỉ ghi quy tắc, không chứa nội dung báo cáo.

## 1. Bố cục

| Phần | Nội dung |
| --- | --- |
| Bìa | Tên trường, logo, loại báo cáo, tên và mã học phần, giảng viên hướng dẫn, bảng thành viên (họ tên, mã số sinh viên), địa điểm và thời gian. Có viền trang. |
| Phần đầu | Mục lục, danh mục bảng, danh mục hình. |
| Nội dung | Các chương đánh số (1, 2, 3…); mục con đánh số theo chương (1.1, 1.2…). |

Chỉ giữ những phần đề bài cần. Báo cáo bài tập không phải bài nghiên cứu thì bỏ tóm tắt, từ khóa, tài liệu tham khảo và
các phần không phục vụ nội dung.

Phần do thành viên khác viết: để sẵn tiêu đề chương, mục và một dòng chữ giữ chỗ để họ điền.

## 2. Trang và chữ

| Thành phần | Quy định |
| --- | --- |
| Khổ giấy | A4, hướng dọc |
| Lề trang | Trên 2 cm, dưới 2 cm, trái 3 cm, phải 1,5 cm |
| Phông chữ | Times New Roman cho toàn bộ báo cáo, kể cả tên trường, lệnh và khối mã; không dùng phông monospace |
| Cỡ chữ | Thân bài 13 pt; tiêu đề cấp 1: 20 pt, cấp 2: 16 pt, cấp 3: 14 pt |
| Giãn dòng | 1,5 dòng |
| Khoảng cách đoạn | Trước 6 pt, sau 6 pt; không chèn dòng trống giữa các đoạn |
| Căn đoạn | Thân bài căn đều hai bên; chú thích bảng, hình căn giữa |
| Số trang | Giữa chân trang, không hiện trên bìa; bắt đầu từ 1 ở trang ngay sau bìa |

Dùng Styles của Word (Heading, Caption…) để định dạng thống nhất. Không căn chỉnh bằng dấu cách hay dòng trống; dùng
ngắt trang khi cần.

## 3. Màu sắc

Toàn bộ chữ của báo cáo **màu đen**: thân bài, tiêu đề, chú thích, mục lục, chữ trên bìa, chữ giữ chỗ. Viền bìa màu đen.

Bảng tự tạo cũng đen hết: chữ đen, đường kẻ đen, không tô nền hàng tiêu đề hay ô; hàng tiêu đề chỉ in đậm. Khối mã chữ
đen, viền đen, không tô nền.

Màu chỉ xuất hiện trong hình ảnh và sơ đồ.

## 4. Văn phong

- Ngắn gọn, đúng độ dài giảng viên yêu cầu; khi nhiều người cùng viết, chừa đủ trang cho phần của người khác.
- Mỗi mục vài câu dẫn vào bảng hoặc sơ đồ; chi tiết để sơ đồ thể hiện.
- Viết tự nhiên, không có dấu hiệu văn AI: không nhãn in đậm đầu mỗi gạch đầu dòng, không in đậm để trang trí, không câu
  tự nói về tài liệu ("các mục sau sẽ…"), không dùng gạch ngang dài. Rà lại bằng skill `humanizer` trước khi nộp.
- Hạn chế mở ngoặc. Không ghi kiểu "Điều phối (Mediator)": tên đã rõ thì ghi thẳng tên ("Mediator"). Ý trong ngoặc
  đáng nói thì viết thành câu; không đáng thì bỏ. Ngoặc chỉ dùng để dẫn số hình, bảng ("(Hình 2)").
- Tên riêng của các thành phần, dịch vụ trong hệ thống viết hoa chữ cái đầu khi nhắc trong câu và nhãn sơ đồ. Tên thật
  trong mã (tệp, lệnh, hostname) giữ nguyên cách viết của mã.
- Chỉ ghi điều hệ thống thực sự làm và công nghệ thực sự dùng. Số liệu lấy từ dữ liệu, kết quả chạy thật, không ước đoán.

## 5. Bảng và hình

- Mỗi bảng, hình có số thứ tự và tên mô tả, đánh số liên tục trong toàn báo cáo (Bảng 1, Hình 1…), và được nhắc tới
  trong phần chữ.
- Chú thích bảng đặt **trên** bảng; chú thích hình đặt **dưới** hình.
- Dùng Caption và Cross-reference của Word để số bảng, hình tự cập nhật.
- Hình rõ nét, vừa vùng in, cao tối đa khoảng 21 cm để hình và chú thích nằm cùng một trang.
- Đặt phần chữ giải thích **trước** hình để hình lớn bị đẩy sang trang sau không để lại khoảng trắng.

## 6. Sơ đồ

- Vẽ bằng **drawio**; giữ tệp nguồn `.drawio` cạnh ảnh xuất ra để sửa được về sau.
- Sơ đồ phải thể hiện **đủ mọi luồng** quan trọng của hệ thống. Khi cần bớt trang thì gộp nhiều luồng vào một sơ đồ,
  không bỏ luồng.
- Nhãn viết **bằng lời mô tả cho người đọc hiểu, không theo kiểu code**: không ghi endpoint, tên biến, tên trường, mã
  trạng thái hay công thức kiểu lập trình. Ví dụ ghi "Giao URL cho máy tải" thay cho `POST /load {url}`. Riêng sơ đồ lược
  đồ dữ liệu được giữ tên trường.
- Thông số kỹ thuật (cổng, endpoint…) ghi trong bảng, không đưa vào sơ đồ.
- Có logo cho các công nghệ, thành phần chính.
- Màu theo quy ước: xanh dương là bước xử lý, vàng là điểm rẽ nhánh, xanh lá là kết quả tốt, đỏ là nhánh bị loại hoặc
  lỗi, tím là đầu vào, đầu ra.
- Chữ không tràn khung, không ngắt dòng giữa từ; xuất ảnh xong phải mở ra kiểm tra.

### Sơ đồ công nghệ

- Chỉ liệt kê **công nghệ thật**: ngôn ngữ, thư viện, công cụ, cơ sở dữ liệu, dịch vụ ngoài. Không đưa vào những gì tự cài
  đặt (thuật toán, cơ chế, hàm tự viết); những thứ đó thuộc sơ đồ luồng.
- Mỗi công nghệ một ô gồm logo, tên, loại (ngôn ngữ, thư viện, công cụ, CSDL, dịch vụ đám mây…) và việc nó làm, chia
  theo từng thành phần của hệ thống.

## 7. Mục lục

Mục lục tạo tự động từ Heading cấp 1 và cấp 2; danh mục bảng, hình tạo từ Caption. Trước khi nộp, chọn **Update Field**
để cập nhật số trang.

## 8. Rà soát trước khi nộp

- Đọc lại chính tả, thuật ngữ, thông tin trên bìa và các chỗ còn để trống.
- Kiểm tra số bảng, hình trong phần chữ khớp với chú thích.
- Cập nhật mục lục và danh mục (Update Field).
- Mở bằng Word để xem bìa, mục lục, các trang có bảng và hình; ảnh không mất, chữ không tràn lề.
- Xuất PDF bằng chính Microsoft Word (Save As PDF), không dùng LibreOffice: LibreOffice dàn chữ, đặt dấu tiếng Việt
  khác Word nên chữ trông lạ dù cùng phông. Trên WSL, gọi Word qua PowerShell (COM `Word.Application`,
  `ExportAsFixedFormat`). Kiểm tra lại bằng `pdfinfo`: Producer là Microsoft Word, `pdffonts` chỉ thấy `TimesNewRoman…`.
- Kiểm tra tổng số trang đúng giới hạn của học phần.
