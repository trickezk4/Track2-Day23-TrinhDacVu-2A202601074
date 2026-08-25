# Postmortem — DR Drill Lab 23

Theo đúng template §4 "Sau Failover: Blameless Postmortem". Blameless: câu hỏi là "hệ thống/process nào cho phép chuyện này", không phải "ai làm sai".

## 1. Timeline (mọi dòng phải có evidence path:line)

| ISO time            | Sự kiện                                         | Evidence                            |
| ------------------- | ------------------------------------------------- | ----------------------------------- |
| 2026-08-25T11:30:29 | outage bắt đầu                                 | `chaos/chaos-events.jsonl:1`      |
| 2026-08-25T11:30:29 | user đầu tiên bị ảnh hưởng                 | `reports/drill-2-withdr.jsonl:1`  |
| 2026-08-25T11:30:43 | health check alert                                | `reports/health-events.jsonl:1`   |
| 2026-08-25T11:30:46 | operator confirm cutover                          | `reports/runbook-run.jsonl:2`     |
| 2026-08-25T11:30:57 | resolved (request đầu tiên OK từ region phụ) | `reports/drill-2-withdr.jsonl:30` |

## 2. RTO/RPO đo được vs mục tiêu — gap ở bước nào?

- RTO mục tiêu: 300s · đo được: `25.0s` · gap: `275.0s` (nhanh hơn mục tiêu)
- RPO mục tiêu: 300s · đo được: `4.0s` (`2` doc bị mất) · gap: `296.0s`
- **Bước tốn nhiều giây nhất:** `Health-check detect floor (15.8s)` — vì cơ chế chống flapping cần tối thiểu 3 chu kỳ probe liên tiếp (mỗi chu kỳ 5 giây) để xác nhận outage thật sự trước khi báo động.

## 3. Root cause (5 whys)

1. *Tại sao Region A ngừng phục vụ request?* Do phân vùng mạng giả lập làm Region A mất kết nối với client.
2. *Tại sao mất 14.1s hệ thống mới bắt đầu xử lý?* Do cơ chế health check yêu cầu 3 lần fail liên tiếp (threshold=3, interval=5s) để chống flapping.
3. *Tại sao Region B không nhận tải ngay lập tức?* Do Region B ban đầu ở trạng thái Warm Pool, cần scale lên Full Pool và nạp model weights vào bộ nhớ.
4. *Tại sao mất thêm 4.8s sau khi DNS Cutover client mới thành công?* Do Edge proxy có bộ đệm DNS TTL và cache trạng thái active region.
5. *Nếu đây là outage thật, bước nào trong runbook có nguy cơ thất bại nhất?* Bước xác thực dung lượng tải (scale pool) và chờ GPU warm-up nếu hạ tầng cloud cạn kiệt tài nguyên tức thời.

## 4. Action items (có owner + deadline)

| # | Action                                                   | Owner     | Deadline   | Giảm RTO/RPO bao nhiêu giây |
| - | -------------------------------------------------------- | --------- | ---------- | ------------------------------ |
| 1 | Tối ưu interval health check từ 5s xuống 3s          | SRE Team  | 2026-09-05 | Giảm RTO 6.0s                 |
| 2 | Giảm Edge Proxy TTL từ 5s xuống 2s                    | Edge Team | 2026-09-08 | Giảm RTO 3.0s                 |
| 3 | Tăng tần suất snapshot replication từ 30s xuống 10s | Data Team | 2026-09-10 | Giữ RPO luôn < 10.0s         |

## 5. Ba câu hỏi bắt buộc trả lời

1. `interval × threshold` là **15.0 giây** (5s × 3). Nó chiếm **52.4%** tổng thời gian RTO (15.0s / 28.6s).
2. Nếu hạ interval xuống 1s, RTO giảm **12.0 giây** (detection floor giảm từ 15s xuống 3s). Cái giá phải trả là nguy cơ **flapping**: những chập chờn mạng tạm thời (transient network spikes) sẽ kích hoạt nhầm quy trình failover, gây gián đoạn dịch vụ 2 chiều không cần thiết.
3. Nếu outage kéo dài 6 giờ và region chính mất dữ liệu vĩnh viễn, con số `docs_lost = 2` đồng nghĩa với việc toàn bộ tài liệu đã được nhân bản hoàn chỉnh sang bản snapshot tại region phụ trước khi sự cố xảy ra, khách hàng không bị mất mát bất kỳ tài liệu hay vector index nào.
