# Domains: danh sách host

`config/domains/` giữ danh sách host làm đầu vào cho hệ thống thu thập:

| File | Nội dung |
|---|---|
| `all.txt` | mọi hostname ứng viên (một dòng một host) |
| `active.txt` | host còn trả nội dung công khai; merger lấy trang chủ của chúng làm URL mầm (`hosts_file` trong `config/merger.yaml`) và chỉ coi chúng cùng `*.hust.edu.vn` là trong miền |
| `status.jsonl` | báo cáo thăm dò từng host (`active`, `reason`, `status`, `final_url`, `title`, `error`, …) |

```bash
uv run data-pipeline domains audit            # thăm dò DNS/HTTP, ghi lại all.txt, active.txt và status.jsonl
uv run data-pipeline domains audit --no-browser --workers 16 --timeout 10
```

`audit` thăm dò từng host (DNS an toàn, HTTP, theo dõi chuyển hướng cùng phạm vi, phát hiện trang mặc định,
trang đăng nhập, endpoint dịch vụ, môi trường dev/staging). Host chỉ là vỏ JavaScript được thử lại bằng
Playwright (bỏ bằng `--no-browser`). Trang trùng nội dung được gộp, chuyển hướng ra ngoài `hust.edu.vn` bị loại.

- **Ghi đè cả ba file:** `all.txt` được viết lại (sắp xếp, thêm host đích của chuyển hướng mới gặp), cùng
  `active.txt` và `status.jsonl`. Mỗi file được ghi qua tệp tạm rồi `rename`.
- **Đầu ra:** tiến độ (`checked N/M; active K`) in ra stderr; stdout chỉ có một dòng JSON tóm tắt
  (`active`, `checked`, `report`).
- **Trình duyệt:** Playwright chỉ được import khi dùng trình duyệt. Nếu trình duyệt không khởi động được, kết quả HTTP vẫn được giữ và mỗi host vỏ JavaScript ghi
  `error: "browser_error: ..."`.
- **Mã thoát:** 0 khi xong; 2 khi tham số không hợp lệ (`--workers`, timeout ≤ 0 hoặc `--retries` < 0).

Tùy chọn: `--workers` (mặc định 32), `--browser-workers` (6), `--timeout` (8 giây), `--browser-timeout` (15 giây),
`--retries` (2), `--all-domains`, `--active-domains`, `--report`.
