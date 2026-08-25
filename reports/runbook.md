# Runbook 1 trang — Region chính down

Runbook phải chạy được lúc 3h sáng bởi người KHÔNG viết nó. Mỗi bước: lệnh copy-paste được + cách biết bước đó xong.

| # | Bước                       | Lệnh                                                                                                                                     | Biết là xong khi                                                        | Ai làm       |
| - | ---------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------- | ------------- |
| 1 | Xác nhận outage            | `curl -s localhost:8001/readyz`                                                                                                         | Trả về mã lỗi hoặc timeout 3 lần liên tiếp                        | On-call SRE   |
| 2 | Mở incident + bấm giờ RTO | `python3 dr/runbook.py --primary a --target b --auto`                                                                                   | Dòng`step:2_thong_bao_incident` ghi vào `reports/runbook-run.jsonl` | On-call SRE   |
| 3 | Restore state ở region phụ | `python3 state/snapshot.py get --region b --backend fs`                                                                                 | File`state/region-b/vectors.sqlite` có dữ liệu                       | On-call SRE   |
| 4 | Scale pool warm→full        | `echo "full" > state/region-b/pool_state`                                                                                               | `/readyz` của Region B trả về HTTP 200                               | On-call SRE   |
| 5 | DNS/LB cutover               | `echo "b" > edge/active_region`                                                                                                         | `curl -s localhost:8080/edge/state` hiển thị `active_region: b`     | On-call SRE   |
| 6 | Verify golden signals        | `python3 -c "import httpx; [print(httpx.post('http://localhost:8080/v1/infer', json={'query':'test'}).status_code) for _ in range(5)]"` | Tất cả trả về 200, p95 < 200ms, error rate = 0%                       | On-call SRE   |
| 7 | Đo RTO + postmortem         | `python3 tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl`                                                                   | `rto_verdict` trả về `PASS`                                         | Incident Lead |

---

**Rollback (failover ngược):**

* **Điều kiện trả traffic về Region A:**
  1. Region A đã được sửa chữa triệt để và endpoint `/readyz` trả về `200 OK` liên tục trong tối thiểu 15 phút.
  2. Dữ liệu mới phát sinh tại Region B đã được replicate ngược trở lại Region A (`state/snapshot.py get --region a`).
  3. Có sự phê duyệt trực tiếp từ Incident Lead.
* **Cơ chế chống flapping:** Tuyệt đối không bật failback tự động; mọi thao tác cutover ngược về Region A đều phải xác nhận thủ công.
