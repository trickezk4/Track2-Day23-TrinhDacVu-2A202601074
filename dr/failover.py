"""BƯỚC 3b — SINH VIÊN VIẾT. Cutover sang region phụ.

5 bước, THỨ TỰ QUAN TRỌNG (§2 Kiến Trúc Tham Chiếu: DNS/LB, compute, state là 3 lớp riêng):
  1_verify_target    — /v1/state của region phụ: weights? vector count? pool_state?
  2_restore_snapshot — gọi state/snapshot.py get + state/snapshot.py rpo()
                       Log BẮT BUỘC: rpo_seconds, docs_lost, embed_model_version.
                       (§3: "backup index nhưng quên backup embedding model version
                        -> index không tương thích khi restore")
  3_scale_pool       — ghi "full" vào state/region-<t>/pool_state (warm -> full)
  4_wait_ready       — POLL /readyz tới khi 200. Region phụ có WARMUP_SECONDS —
                       đây là GPU pool warm-up của §4, nó nằm trong RTO của bạn.
  5_dns_cutover      — ghi region đích vào edge/active_region

BẪY: nếu bạn đổi edge/active_region TRƯỚC bước 4, user sẽ nhận 503 từ CẢ HAI region
và RTO của bạn dài hơn, không ngắn hơn. Nếu bước 4 timeout -> ABORT, KHÔNG cutover.

Mỗi bước ghi 1 dòng vào reports/failover-events.jsonl với ts + step.
Không có dòng 5_dns_cutover = tools/measure_rto.py không tìm được t_cutover = mất điểm.

Chạy:  python dr/failover.py --target b --backend fs
"""
import argparse
import json
import pathlib
import sys
import time
import datetime

import httpx

sys.path.insert(0, ".")
from state import snapshot  # noqa: E402

URL = {"a": "http://127.0.0.1:8001", "b": "http://127.0.0.1:8002"}
LOG = pathlib.Path("reports/failover-events.jsonl")


def emit(**kw):
    """Ghi 1 dòng JSONL có ts + iso vào LOG và in ra stdout."""
    LOG.parent.mkdir(parents=True, exist_ok=True)
    ts = time.time()
    iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    record = {"ts": ts, "iso": iso, **kw}
    
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    
    print(f"[FAILOVER] {json.dumps(record)}")
    return record


def failover(target: str, backend: str, wait: float) -> dict:
    """Thực hiện đúng 5 bước failover."""
    result = {"target": target, "backend": backend, "status": "failed"}

    # Bước 1: verify_target
    try:
        resp = httpx.get(f"{URL[target]}/v1/state", timeout=5.0)
        state_data = resp.json() if resp.status_code == 200 else {}
    except Exception as exc:
        state_data = {"error": str(exc)}
    
    emit(step="1_verify_target", target=target, target_state=state_data)

    # Bước 2: restore_snapshot
    primary = "a" if target == "b" else "b"
    primary_db = pathlib.Path(f"state/region-{primary}/vectors.sqlite")
    restored_db = pathlib.Path(f"state/region-{target}/vectors.sqlite")
    
    snapshot_meta = snapshot.get(region=target, backend=backend)
    rpo_info = snapshot.rpo(primary_db=primary_db, restored_db=restored_db)
    
    rpo_seconds = rpo_info.get("rpo_seconds", 0.0)
    docs_lost = rpo_info.get("docs_lost", 0)
    embed_model_version = snapshot_meta.get("embed_model_version", "unknown")
    
    emit(
        step="2_restore_snapshot",
        target=target,
        rpo_seconds=rpo_seconds,
        docs_lost=docs_lost,
        embed_model_version=embed_model_version,
    )

    # Bước 3: scale_pool
    pool_file = pathlib.Path(f"state/region-{target}/pool_state")
    pool_file.parent.mkdir(parents=True, exist_ok=True)
    pool_file.write_text("full", encoding="utf-8")
    emit(step="3_scale_pool", target=target, pool_state="full")

    # Bước 4: wait_ready (dùng httpx.get trực tiếp để test monkeypatch bắt được)
    start_wait = time.time()
    ready = False
    while time.time() - start_wait < wait:
        try:
            resp = httpx.get(f"{URL[target]}/readyz", timeout=2.0)
            if resp.status_code == 200:
                ready = True
                break
        except Exception:
            pass
        time.sleep(0.5)

    if not ready:
        emit(step="4_wait_ready", target=target, ready=False, error="timeout_waiting_ready")
        result.update({
            "status": "failed",
            "error": "timeout_waiting_ready",
            "rpo_seconds": rpo_seconds,
            "docs_lost": docs_lost,
            "embed_model_version": embed_model_version,
            "target_state": state_data,
        })
        return result

    emit(step="4_wait_ready", target=target, ready=True, elapsed_s=round(time.time() - start_wait, 2))

    # Bước 5: dns_cutover (chỉ chạy khi ready == True)
    edge_active = pathlib.Path("edge/active_region")
    edge_active.parent.mkdir(parents=True, exist_ok=True)
    edge_active.write_text(target, encoding="utf-8")
    
    emit(step="5_dns_cutover", active_region=target)

    result.update({
        "status": "success",
        "rpo_seconds": rpo_seconds,
        "docs_lost": docs_lost,
        "embed_model_version": embed_model_version,
        "target_state": state_data,
    })
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--target", default="b", choices=["a", "b"])
    p.add_argument("--backend", default="fs", choices=["fs", "minio"])
    p.add_argument("--wait", type=float, default=60)
    a = p.parse_args()
    print(json.dumps(failover(a.target, a.backend, a.wait), indent=2))
