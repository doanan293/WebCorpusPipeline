# Thiết kế hệ thống thu thập dựa trên kiến trúc dịch vụ nhỏ

Tóm tắt slide của thầy (bản 15 trang). Các mô-đun giao tiếp bằng HTTP và JSON.

## Các mô-đun

| # | Mô-đun | Tên | Cổng | Đầu vào | Xử lý | Đầu ra |
|---|---|---|---|---|---|---|
| 1 | Đọc nội dung | reader | 6600 | url, định dạng (html, other), nội dung tải về | html: tiêu đề, nội dung, liên kết đi ra kèm văn bản liên kết (câu hoàn chỉnh chứa thẻ a); other: văn bản tách được | bản ghi |
| 2 | Tải về | downloader | 8800 | url | Puppeteer + Chrome headless cho trang web; tải thẳng tài liệu | gửi sang reader |
| 3 | Điều khiển | mediator | 5200 | gói url | thời gian chờ, lập lịch, cân bằng tải xoay vòng | gửi url sang downloader |
| 4 | Tổng hợp dữ liệu | merger | 3300 | bản ghi | lọc url trong miền hust, loại url đã có | bản ghi sang storage, url mới sang mediator |
| 5 | Lưu trữ | storage | 2200 | bản ghi | lưu vào Mongo đúng cấu trúc | |
| 6 | Mongo | mongoose | 27017 | | dựng MongoDB | |

Luồng: merger nạp url mầm → mediator → downloader → reader → merger → storage (bản ghi) và mediator (url mới).

## Giao diện

| Mô-đun | Endpoint | Body |
|---|---|---|
| mediator | `POST /add` | `{"urls": ["https://...", ...]}` |
| downloader | `POST /load` | `{"url": "https://..."}` |
| downloader | `GET /ping` | trả `{"status": "đang chờ"}` |
| reader | `POST /parse` | `{"url", "format": "html", "content_type": "text/html; charset=utf-8", "encoding": "text", "content": "<!DOCTYPE html>…"}` |
| merger | `POST /add` | `{"url", "format", "title", "content", "out-links"}` |
| storage | `POST /add` | xem dưới |

### storage `POST /add`

```json
{
  "url": "https://hust.edu.vn/tin-tuc1",
  "format": "html",
  "title": "Tin tức 1",
  "content": "Cơ hội thực tập có lương tại Nhật cho sinh viên chương trình HUST - KIOXIA.",
  "base64": "database64://........",
  "out-links": [
    {"url": "https://hust.edu.vn/tin-tuc2", "text": "Thông tin tuyển dụng"},
    {"url": "https://hust.edu.vn/tai-lieu.pdf", "text": "Tải tài liệu hướng dẫn"}
  ]
}
```

Trường `intro` bị gạch khỏi body: storage tự tính. Ba việc của storage:

1. Lưu bản ghi html, `intro` ban đầu để rỗng.
2. Thêm các cạnh vào `graph`.
3. Tạo hoặc cập nhật bản ghi đích của từng out-link, cập nhật trường `intro` của nó.

## Mongo

| Collection | Trường |
|---|---|
| `html` | `url`, `format` (`html`), `title`, `content`, `intro` (văn bản giới thiệu nếu có) |
| `picture` | `url` (ảnh), `origin` (trang chứa ảnh), `format` (`jpg`, ...), `b64` |
| `other` | `url`, `format` (`docx`, ...), `origin` (trang dẫn tới), `content` (văn bản tách được) |
| `graph` | `src`, `dest`, `intro` (văn bản liên kết) |

## Ghép thật của lớp (trang cuối)

downloader `10.177.29.215:8800` → reader → storage → Mongo; reader gửi url mới về mediator `10.177.29.102:5200`,
mediator gửi sang downloader.
