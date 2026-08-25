"""BƯỚC 3c — SINH VIÊN VIẾT. Tự động hoá runbook §4 "Runbook: Region Chính Down".

7 bước trên slide, mỗi bước 1 dòng log có ts. Log này CHÍNH LÀ timeline của postmortem.
  1 xac_nhan_outage          — probe cả 2 region, đừng tin 1 lần fail (dùng nhiều lần
                              hoặc gọi health_checker.probe nếu đã viết xong 3a)
  2 thong_bao_incident       — ts của dòng này là mốc "operator biết tin", LUÔN LUÔN
                              SAU t_outage trong chaos-events (không thể trùng — operator
                              không thể biết ngay giây outage xảy ra). Ghi cả 2 ts vào
                              log để postmortem tính được "độ trễ thông báo".
  3 scale_gpu_pool           — gọi HÀM `failover.failover(...)` MỘT LẦN DUY NHẤT. Hàm
                              đó tự làm đủ 5 bước con (verify/restore/scale/wait/cutover)
                              và tự ghi log riêng vào reports/failover-events.jsonl.
  4 verify_state_replica     — KHÔNG gọi lại failover — chỉ ĐỌC kết quả (vector count +
                              weights ở region phụ) từ dict mà bước 3 trả về, để log vào
                              runbook-run.jsonl cho postmortem đọc 1 chỗ duy nhất.
  5 dns_cutover              — cũng chỉ đọc lại: kết quả cutover có ok hay không.
  6 verify_golden_signals    — 10 request thật vào region phụ: p95 latency + error rate
  7 post_incident            — elapsed_s + lệnh đo RTO

BÁN TỰ ĐỘNG, KHÔNG FULL-AUTO (§4: "failover đầu tiên nên là bán tự động — alert +
1-click confirm — tránh flapping gây failover 2 chiều liên tục"). Mặc định phải hỏi
người vận hành confirm; --auto chỉ dùng trong CI/khi chấm điểm.

Chạy:  python dr/runbook.py --primary a --target b --backend fs
"""
import argparse
import json
import pathlib
import sys
import time
import datetime

import httpx

sys.path.insert(0, ".")
from dr import failover as fo  # noqa: E402
from dr.health_checker import probe  # noqa: E402

LOG = pathlib.Path("reports/runbook-run.jsonl")
URL = {"a": "http://127.0.0.1:8001", "b": "http://127.0.0.1:8002"}


def step(n, name, **kw):
    """Ghi 1 dòng {ts, iso, step, name, ...} vào LOG."""
    LOG.parent.mkdir(parents=True, exist_ok=True)
    ts = time.time()
    iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    record = {"ts": ts, "iso": iso, "step": n, "name": name, **kw}
    
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    
    print(f"[RUNBOOK STEP {n}] {name}: {json.dumps(kw)}")
    return record


def confirm(auto: bool, msg: str) -> bool:
    """Xác nhận trước khi thực hiện hành động."""
    if auto:
        return True
    ans = input(f"{msg} [y/N]: ").strip().lower()
    return ans in ("y", "yes")


def run(primary: str, target: str, backend: str, auto: bool) -> dict:
    """Thực thi 7 bước theo runbook."""
    t_start = time.time()
    
    # 1. Xác nhận sự cố (probe 3 lần liên tiếp để chống báo động giả)
    consecutive_fails = 0
    reason_p = "ok"
    for _ in range(3):
        primary_ready, reason_p = probe(primary, timeout=2.0)
        if not primary_ready:
            consecutive_fails += 1
            time.sleep(3.0)
        else:
            consecutive_fails = 0
            break

    target_ready, reason_t = probe(target, timeout=2.0)
    step(
        1,
        "xac_nhan_outage",
        primary_ready=(consecutive_fails < 3),
        consecutive_fails=consecutive_fails,
        reason=reason_p,
        target_ready=target_ready,
    )
    
    if consecutive_fails < 3:
        print(f"Region {primary} vẫn phản hồi, không kích hoạt failover.")
        return {"status": "aborted", "reason": "primary_healthy"}

    if not confirm(auto, f"Xác nhận kích hoạt Failover từ Region {primary} sang Region {target}?"):
        print("Huỷ bỏ bởi người vận hành.")
        return {"status": "cancelled"}

    # 2. Thông báo sự cố
    step(2, "thong_bao_incident", operator_noticed_ts=time.time(), primary=primary, target=target)

    # 3. Kích hoạt failover
    step(3, "scale_gpu_pool", action="calling_failover")
    fo_result = fo.failover(target=target, backend=backend, wait=60.0)

    # 4. Xác nhận bản sao dữ liệu
    step(4, "verify_state_replica", rpo_seconds=fo_result.get("rpo_seconds"), docs_lost=fo_result.get("docs_lost"))

    # 5. Xác nhận cutover DNS
    step(5, "dns_cutover", active_region=target, status=fo_result.get("status"))

    # 6. Kiểm tra Golden Signals với 10 requests thật
    latencies = []
    errors = 0
    with httpx.Client(timeout=5.0) as client:
        for _ in range(10):
            t0 = time.time()
            try:
                r = client.post("http://127.0.0.1:8080/v1/infer", json={"query": "test"})
                if r.status_code == 200:
                    latencies.append((time.time() - t0) * 1000)
                else:
                    errors += 1
            except Exception:
                errors += 1
            time.sleep(0.1)

    latencies.sort()
    p95 = latencies[int(len(latencies) * 0.95)] if latencies else 0.0
    error_rate = errors / 10.0
    step(6, "verify_golden_signals", p95_latency_ms=round(p95, 2), error_rate=error_rate, total_probes=10)

    # 7. Tổng kết sự cố
    elapsed_total = time.time() - t_start
    step(7, "post_incident", elapsed_s=round(elapsed_total, 2), rto_measure_cmd="python3 tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl")

    return {
        "status": "completed",
        "elapsed_s": round(elapsed_total, 2),
        "p95_latency_ms": round(p95, 2),
        "error_rate": error_rate,
        "failover": fo_result,
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--primary", default="a")
    p.add_argument("--target", default="b")
    p.add_argument("--backend", default="fs", choices=["fs", "minio"])
    p.add_argument("--auto", action="store_true")
    a = p.parse_args()
    print(json.dumps(run(a.primary, a.target, a.backend, a.auto), indent=2))
