# Domains: danh sách host

`config/domains/` giữ danh sách host làm đầu vào cho crawler:

| File | Nội dung |
|---|---|
| `all.txt` | mọi hostname ứng viên (một dòng một host) |
| `active.txt` | host còn trả nội dung công khai; đây là `--input` mặc định khi crawl |
| `status.jsonl` | báo cáo thăm dò từng host (`active`, `reason`, `status`, `final_url`, `title`, …) |

Hash nội dung của `active.txt` được ghi vào state của crawl (`input_sha256`); resume chỉ chấp nhận đúng nội dung này.

```bash
uv run data-pipeline domains audit            # thăm dò DNS/HTTP, ghi lại active.txt và status.jsonl
uv run data-pipeline domains audit --no-browser --workers 16 --timeout 10
```

`audit` thăm dò từng host (DNS an toàn, HTTP, theo dõi chuyển hướng cùng phạm vi, phát hiện trang mặc định,
trang đăng nhập, endpoint dịch vụ, môi trường dev/staging). Host chỉ là vỏ JavaScript được thử lại bằng
Playwright (bỏ bằng `--no-browser`). Trang trùng nội dung được gộp, chuyển hướng ra ngoài `hust.edu.vn` bị loại.
Tùy chọn: `--workers`, `--browser-workers`, `--timeout`, `--browser-timeout`, `--retries`, `--all-domains`,
`--active-domains`, `--report`.
