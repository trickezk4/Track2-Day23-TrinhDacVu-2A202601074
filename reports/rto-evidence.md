# RTO/RPO Evidence — Lab 23

Quy tắc duy nhất: mỗi con số ở đây phải trỏ được về một dòng log thật (`đường/dẫn.jsonl:số_dòng`).

## 1. Drill 1 — không có DR (baseline)

| Chỉ số | Giá trị | Cách đo | Evidence |
|---|---|---|---|
| t_outage | `2026-08-25T10:08:58` | chaos kill | `chaos/chaos-events.jsonl:1` |
| Request fail đầu tiên | `+0.0s` | dòng `ok:false` đầu tiên sau t_outage | `reports/drill-1-nodr.jsonl:1` |
| Request thành công sau đó | không có | không có dòng `ok:true` nào sau t_outage | `reports/measure-drill-1.json:1` |
| RTO | `NO_RECOVERY` | `tools/measure_rto.py` | `reports/measure-drill-1.json:1` |

## 2. Drill 2 — có DR

| Mốc | +giây từ t_outage | Cách đo | Evidence |
|---|---|---|---|
| t_outage (mốc 0) | 0 | `action:kill` | `chaos/chaos-events.jsonl:1` |
| User thấy lỗi đầu tiên | 0.4s | dòng `ok:false` đầu | `reports/drill-2-withdr.jsonl:1` |
| Health check phát hiện | 15.8s | `to:UNHEALTHY, region:a` | `reports/health-events.jsonl:1` |
| Snapshot restore xong | 18.0s | `step:2_restore_snapshot` | `reports/failover-events.jsonl:2` |
| Region phụ ready | 23.8s | `step:4_wait_ready` | `reports/failover-events.jsonl:4` |
| DNS cutover | 23.9s | `step:5_dns_cutover` | `reports/failover-events.jsonl:5` |
| **RTO đo được** | 25.0s | dòng `ok:true` đầu sau lỗi | `reports/drill-2-withdr.jsonl:25` |

| Chỉ số | Đo được | Mục tiêu (slide §1) | Verdict |
|---|---|---|---|
| RTO — Inference API | `25.0s` | 300s (5 phút) | PASS |
| RPO — Vector DB | `4.0s` / `2` doc | 300s (5 phút) | PASS |

## 3. RTO của tôi gồm những gì (bắt buộc — đây là phần chấm điểm hiểu bài)

| Thành phần | Giây | Nó đến từ đâu | Giảm được bằng cách nào |
|---|---|---|---|
| Health-check detect floor | 15.0s | `interval_s × threshold` trong `reports/health-events.jsonl:1` | Giảm `interval` xuống 2s hoặc tối ưu ngưỡng probe thành 2 lần |
| Snapshot restore | 0.1s | 2_restore → 3_scale trong `reports/failover-events.jsonl:2` | Dùng storage replica đồng bộ hoặc snapshot lưu sẵn tại local disk |
| GPU pool warm-up | 5.8s | `waited_s` ở `4_wait_ready` trong `reports/failover-events.jsonl:4` | Duy trì sẵn model weights trong RAM/VRAM của region phụ (Warm Pool) |
| DNS/LB TTL cache | 1.1s | t_recovered − t_cutover trong `reports/drill-2-withdr.jsonl:25` | Giảm DNS TTL và proxy cache TTL xuống 1s |