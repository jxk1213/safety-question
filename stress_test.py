#!/usr/bin/env python3
"""
sync_server 压力测试脚本
模拟 N 个手机端用户并发轮询，统计响应时间与错误率。

用法:
  python3 stress_test.py                   # 默认 200 并发，持续 30 秒
  python3 stress_test.py --users 100 --duration 20 --url http://localhost:8080
"""

import argparse
import random
import string
import threading
import time
import urllib.request
import urllib.error
import json
from collections import defaultdict

# ── 参数 ──────────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(description="Sync server stress test")
parser.add_argument("--url",      default="http://localhost:8080", help="服务器地址")
parser.add_argument("--users",    type=int, default=200,  help="模拟并发用户数")
parser.add_argument("--duration", type=int, default=30,   help="测试持续时间(秒)")
parser.add_argument("--room",     default="stress-test",  help="房间ID")
parser.add_argument("--interval", type=float, default=2.0, help="每用户轮询间隔(秒)")
args = parser.parse_args()

BASE_URL  = args.url.rstrip("/")
ROOM      = args.room
USERS     = args.users
DURATION  = args.duration
INTERVAL  = args.interval

# ── 统计 ─────────────────────────────────────────────────────────────────────
stats_lock = threading.Lock()
stats = defaultdict(int)   # ok, error_4xx, error_5xx, timeout, exception
latencies = []

stop_event = threading.Event()


def rand_id(n=10):
    return "m-" + "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def poll_once(client_id: str):
    url = f"{BASE_URL}/__sync__/host_state?room={ROOM}&clientId={client_id}"
    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "StressTest/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            code = resp.getcode()
            _ = resp.read()
            elapsed = (time.perf_counter() - t0) * 1000
            with stats_lock:
                if code == 200:
                    stats["ok"] += 1
                elif 400 <= code < 500:
                    stats["error_4xx"] += 1
                else:
                    stats["error_5xx"] += 1
                latencies.append(elapsed)
    except urllib.error.HTTPError as e:
        elapsed = (time.perf_counter() - t0) * 1000
        with stats_lock:
            if e.code == 429:
                stats["rate_limited_429"] += 1
            elif e.code == 503:
                stats["capacity_exceeded_503"] += 1
            elif 400 <= e.code < 500:
                stats["error_4xx"] += 1
            else:
                stats["error_5xx"] += 1
            latencies.append(elapsed)
    except Exception:
        with stats_lock:
            stats["timeout_or_exception"] += 1


def user_loop(client_id: str):
    while not stop_event.is_set():
        poll_once(client_id)
        stop_event.wait(timeout=INTERVAL)


def print_progress():
    while not stop_event.is_set():
        time.sleep(5)
        with stats_lock:
            total = sum(stats.values())
            ok = stats["ok"]
            rl = stats["rate_limited_429"]
            cap = stats["capacity_exceeded_503"]
            err = stats["error_4xx"] + stats["error_5xx"] + stats["timeout_or_exception"]
            avg_ms = (sum(latencies) / len(latencies)) if latencies else 0
        print(f"  [进度] 总请求: {total:>5} | 成功: {ok:>5} | "
              f"限流429: {rl:>4} | 满员503: {cap:>4} | 错误: {err:>4} | "
              f"平均响应: {avg_ms:.1f}ms")


# ── 运行 ─────────────────────────────────────────────────────────────────────
print(f"\n🚀 压力测试启动")
print(f"   服务器: {BASE_URL}")
print(f"   用户数: {USERS}  |  时长: {DURATION}s  |  轮询间隔: {INTERVAL}s")
print(f"   理论最大 QPS: {USERS / INTERVAL:.0f} req/s")
print(f"   (按 Ctrl+C 提前停止)\n")

threads = []

# 进度打印线程
t = threading.Thread(target=print_progress, daemon=True)
t.start()

# 模拟用户加入（分批，避免瞬间全部连入）
for i in range(USERS):
    cid = rand_id()
    t = threading.Thread(target=user_loop, args=(cid,), daemon=True)
    t.start()
    threads.append(t)
    # 每 10 人间隔 50ms，模拟逐步扫码
    if i % 10 == 9:
        time.sleep(0.05)

try:
    time.sleep(DURATION)
except KeyboardInterrupt:
    pass
finally:
    stop_event.set()
    for t in threads:
        t.join(timeout=3)

# ── 输出报告 ──────────────────────────────────────────────────────────────────
with stats_lock:
    total    = sum(stats.values())
    ok       = stats["ok"]
    rl       = stats["rate_limited_429"]
    cap      = stats["capacity_exceeded_503"]
    err4     = stats["error_4xx"]
    err5     = stats["error_5xx"]
    timeout  = stats["timeout_or_exception"]
    lats     = latencies[:]

lats.sort()
p50  = lats[int(len(lats) * 0.50)] if lats else 0
p95  = lats[int(len(lats) * 0.95)] if lats else 0
p99  = lats[int(len(lats) * 0.99)] if lats else 0
avg  = sum(lats) / len(lats) if lats else 0

success_rate = ok / total * 100 if total else 0

print(f"\n{'='*55}")
print(f"  📊 压测报告 ({USERS} 用户 × {DURATION}s)")
print(f"{'='*55}")
print(f"  总请求数    : {total:>6}")
print(f"  成功 (200)  : {ok:>6}  ({success_rate:.1f}%)")
print(f"  限流 (429)  : {rl:>6}")
print(f"  满员 (503)  : {cap:>6}")
print(f"  其他4xx     : {err4:>6}")
print(f"  5xx 错误    : {err5:>6}")
print(f"  超时/异常   : {timeout:>6}")
print(f"{'─'*55}")
print(f"  响应延迟 avg: {avg:.1f}ms")
print(f"  响应延迟 p50: {p50:.1f}ms")
print(f"  响应延迟 p95: {p95:.1f}ms")
print(f"  响应延迟 p99: {p99:.1f}ms")
print(f"{'='*55}")

if success_rate >= 95:
    print("  ✅ 服务器压测通过，可承载当前用户规模")
elif success_rate >= 80:
    print("  ⚠️  服务器有压力，建议减少并发数或降低轮询频率")
else:
    print("  ❌ 服务器压力过大，需要优化或扩容")
print()
