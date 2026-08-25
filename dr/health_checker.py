"""BƯỚC 3a — SINH VIÊN VIẾT. Health checker cho 2 region.

Yêu cầu (đọc §4 "Kiến Trúc Health-Check-Based Failover" + §2 "DNS Failover"):
  1. Poll /readyz của CẢ HAI region mỗi `interval` giây (mặc định 5s).
     Dùng /readyz, KHÔNG dùng /healthz. /healthz chỉ nói "process còn sống" —
     region có process sống nhưng vector DB rỗng thì vẫn không serve được.
  2. Chỉ đổi trạng thái sau `threshold` lần fail LIÊN TIẾP (mặc định 3).
     Một lần fail không phải outage. Đây là chống flapping (§4 Anti-Patterns).
  3. Ghi 1 dòng JSONL MỖI LẦN ĐỔI TRẠNG THÁI (không ghi mỗi lần poll — log sẽ ngập).
     Dòng bắt buộc có: ts, region, to (HEALTHY|UNHEALTHY), reason,
     interval_s, threshold. Thiếu interval_s/threshold thì tools/measure_rto.py
     không tính được detect floor -> mất điểm.

Chạy:  python dr/health_checker.py --interval 5 --threshold 3 --duration 300 \
              --out reports/health-events.jsonl

CÂU HỎI PHẢI TRẢ LỜI TRƯỚC KHI VIẾT (ghi câu trả lời vào reports/postmortem.md):
  interval=5s, threshold=3 -> sớm nhất bạn có thể phát hiện outage là bao nhiêu giây?
  Con số đó nằm TRONG RTO của bạn. Muốn RTO 5 phút thì được phép chọn interval bao nhiêu?
"""
import argparse
import json
import pathlib
import time
import datetime

import httpx

URL = {"a": "http://127.0.0.1:8001", "b": "http://127.0.0.1:8002"}


def probe(region: str, timeout: float) -> tuple[bool, str]:
    """Kiểm tra endpoint /readyz của region với timeout cụ thể."""
    url = f"{URL[region]}/readyz"
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url)
            if resp.status_code == 200:
                return True, "ok"
            return False, f"status_{resp.status_code}"
    except httpx.TimeoutException:
        return False, "timeout"
    except httpx.RequestError as exc:
        return False, f"connection_error: {type(exc).__name__}"


def run(interval: float, timeout: float, threshold: int, duration: float, out: pathlib.Path):
    """Vòng lặp poll định kỳ, phát hiện chuyển trạng thái và ghi JSONL."""
    out.parent.mkdir(parents=True, exist_ok=True)
    
    states = {"a": "HEALTHY", "b": "HEALTHY"}
    fail_counts = {"a": 0, "b": 0}
    
    start_time = time.time()
    
    while time.time() - start_time < duration:
        loop_start = time.time()
        
        for region in ["a", "b"]:
            is_ready, reason = probe(region, timeout)
            
            if is_ready:
                fail_counts[region] = 0
                if states[region] == "UNHEALTHY":
                    states[region] = "HEALTHY"
                    event = {
                        "event": "state_change",
                        "ts": time.time(),
                        "iso": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        "region": region,
                        "from": "UNHEALTHY",
                        "to": "HEALTHY",
                        "reason": reason,
                        "interval_s": interval,
                        "threshold": threshold,
                        "consecutive_fails": 0,
                    }
                    with open(out, "a", encoding="utf-8") as f:
                        f.write(json.dumps(event) + "\n")
                    print(f"[HEALTH] Region {region} -> HEALTHY ({reason})")
            else:
                fail_counts[region] += 1
                if states[region] == "HEALTHY" and fail_counts[region] >= threshold:
                    states[region] = "UNHEALTHY"
                    event = {
                        "event": "state_change",
                        "ts": time.time(),
                        "iso": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        "region": region,
                        "from": "HEALTHY",
                        "to": "UNHEALTHY",
                        "reason": reason,
                        "interval_s": interval,
                        "threshold": threshold,
                        "consecutive_fails": fail_counts[region],
                    }
                    with open(out, "a", encoding="utf-8") as f:
                        f.write(json.dumps(event) + "\n")
                    print(f"[HEALTH] Region {region} -> UNHEALTHY ({reason}) sau {fail_counts[region]} lần lỗi")
        
        elapsed = time.time() - loop_start
        sleep_time = max(0.0, interval - elapsed)
        time.sleep(sleep_time)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--interval", type=float, default=5.0)
    p.add_argument("--timeout", type=float, default=2.0)
    p.add_argument("--threshold", type=int, default=3)
    p.add_argument("--duration", type=float, default=300)
    p.add_argument("--out", default="reports/health-events.jsonl")
    a = p.parse_args()
    run(a.interval, a.timeout, a.threshold, a.duration, pathlib.Path(a.out))
