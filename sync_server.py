#!/usr/bin/env python3
"""
Sync server for 安全月活动大屏 — with rate-limiting & connection cap.

Protection layers
-----------------
1. MAX_CLIENTS_PER_ROOM   – refuse new check-ins once the room is "full"
2. RATE_LIMIT_PER_IP      – drop repeated rapid-fire requests from the same IP
3. Graceful 429 / 503 responses instead of crashing
4. Stale-client cleanup every 60 s (client must heartbeat within 30 s)
"""

import json
import threading
import time
try:
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
except ImportError:
    from http.server import SimpleHTTPRequestHandler, HTTPServer
    from socketserver import ThreadingMixIn

    class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True

from urllib.parse import parse_qs, urlparse

# ── 可调参数 ──────────────────────────────────────────────────────────────────
MAX_CLIENTS_PER_ROOM  = 300      # 单个房间最大同时在线客户端数（保守上限）
# 限流说明：场馆 WiFi 下所有手机共用同一内网IP，因此以 clientId 为限流单元
# 每个 clientId 在 2s 窗口内最多 4 次请求（手机每 2s 轮询一次，正常应 ≤2 次）
RATE_LIMIT_WINDOW_S   = 2.0      # 限流窗口（秒）
RATE_LIMIT_MAX_REQS   = 4        # 窗口内同一 clientId 最多请求次数
CLIENT_STALE_S        = 35       # 超过此秒数无心跳则踢出
ROOM_STALE_S          = 3600     # 超过此秒数无活动则删除房间
# ─────────────────────────────────────────────────────────────────────────────

rooms: dict = {}
# client_requests: { clientId: [timestamp, ...] }  —以clientId限流，不以IP（因场馆WiFi共用IP）
client_requests: dict = {}
lock = threading.Lock()


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def get_room(room_id: str) -> dict:
    now = time.time()
    with lock:
        room = rooms.get(room_id)
        if room is None:
            room = {
                "host_state":     None,
                "audience_stats": {"total": 0, "correct": 0},
                "answers":        {},
                "clients_seen":   {},
                "created_at":     now,
            }
            rooms[room_id] = room
        room["updated_at"] = now
        return room


def is_rate_limited(client_id: str) -> bool:
    """以 clientId 为限流单元，防止单一客户端短时频繁请求。
    场馆WiFi下所有手机共用同一公网IP，以IP限流会把所有用户一起限制。
    """
    if not client_id:
        return False  # 没有clientId的请求（如大屏推送）不限流
    now = time.time()
    cutoff = now - RATE_LIMIT_WINDOW_S
    with lock:
        times = client_requests.get(client_id, [])
        times = [t for t in times if t > cutoff]
        if len(times) >= RATE_LIMIT_MAX_REQS:
            client_requests[client_id] = times
            return True
        times.append(now)
        client_requests[client_id] = times
        return False


def client_count(room: dict) -> int:
    with lock:
        return len(room.get("clients_seen", {}))


def clean_stale():
    """Background thread: remove stale clients & empty rooms every 60 s."""
    while True:
        time.sleep(60)
        now = time.time()
        with lock:
            to_del = []
            for rid, room in rooms.items():
                if now - room.get("updated_at", now) > ROOM_STALE_S:
                    to_del.append(rid)
                    continue
                seen = room.get("clients_seen", {})
                for cid in list(seen.keys()):
                    if now - seen[cid] > CLIENT_STALE_S:
                        seen.pop(cid, None)
            for rid in to_del:
                rooms.pop(rid, None)
            # prune client_requests
            cutoff2 = now - RATE_LIMIT_WINDOW_S * 2
            for cid in list(client_requests.keys()):
                client_requests[cid] = [t for t in client_requests[cid] if t > cutoff2]
                if not client_requests[cid]:
                    client_requests.pop(cid, None)


# ── HTTP handler ──────────────────────────────────────────────────────────────

class Handler(SimpleHTTPRequestHandler):

    def _client_ip(self) -> str:
        xff = self.headers.get("X-Forwarded-For", "")
        if xff:
            return xff.split(",")[0].strip()
        return self.client_address[0]

    def _get_client_id_from_qs(self, qs) -> str:
        return (qs.get("clientId") or [""])[0].strip()

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age",       "86400")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _json(self, code: int, data: dict):
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self._cors_headers()
        self.send_header("Content-Type",   "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _get_room_and_params(self):
        parsed = urlparse(self.path)
        qs     = parse_qs(parsed.query)
        room_id = (qs.get("room") or [""])[0].strip()
        return parsed.path, qs, room_id

    # ── GET ──────────────────────────────────────────────────────────────────

    def do_GET(self):
        path, qs, room_id = self._get_room_and_params()
        if not path.startswith("/__sync__/"):
            return super().do_GET()

        # 以 clientId 限流（手机端心跳）
        client_id = self._get_client_id_from_qs(qs)
        if is_rate_limited(client_id):
            return self._json(429, {
                "ok":    False,
                "error": "请求过于频繁，请稍后重试",
                "retry_after": RATE_LIMIT_WINDOW_S
            })

        if not room_id:
            return self._json(400, {"ok": False, "error": "room required"})

        room = get_room(room_id)
        if client_id:
            with lock:
                room["clients_seen"][client_id] = time.time()

        if path == "/__sync__/host_state":
            return self._json(200, {
                "ok":           True,
                "payload":      room.get("host_state"),
                "audienceStats": room.get("audience_stats", {"total": 0, "correct": 0}),
                "onlineCount":  client_count(room),
            })

        if path == "/__sync__/answers":
            round_id = (qs.get("roundId") or [""])[0].strip()
            with lock:
                answers = []
                if round_id and round_id in room["answers"]:
                    answers = list(room["answers"][round_id].values())
                clients = list(room["clients_seen"].keys())
            return self._json(200, {"ok": True, "answers": answers, "clients": clients})

        # 管理接口：查看房间状态
        if path == "/__sync__/admin/status":
            with lock:
                summary = {
                    rid: {
                        "clients": len(r.get("clients_seen", {})),
                        "max_cap": MAX_CLIENTS_PER_ROOM,
                        "updated_ago_s": round(time.time() - r.get("updated_at", 0), 1),
                    }
                    for rid, r in rooms.items()
                }
            return self._json(200, {"ok": True, "rooms": summary, "total_rooms": len(summary)})

        return self._json(404, {"ok": False, "error": "not found"})

    # ── POST ─────────────────────────────────────────────────────────────────

    def do_POST(self):
        path, qs, room_id = self._get_room_and_params()
        if not path.startswith("/__sync__/"):
            return self._json(404, {"ok": False, "error": "not found"})

        # 以 clientId 限流
        client_id_qs = self._get_client_id_from_qs(qs)
        if is_rate_limited(client_id_qs):
            return self._json(429, {
                "ok":    False,
                "error": "请求过于频繁，请稍后重试",
                "retry_after": RATE_LIMIT_WINDOW_S
            })

        if not room_id:
            return self._json(400, {"ok": False, "error": "room required"})

        length  = int(self.headers.get("Content-Length", "0"))
        body    = self.rfile.read(length) if length > 0 else b"{}"
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            payload = {}

        room = get_room(room_id)

        # ── host pushes its state ───────────────────────────────────────────
        if path == "/__sync__/host_state":
            with lock:
                room["host_state"] = payload
            return self._json(200, {"ok": True})

        # ── mobile submits an answer ────────────────────────────────────────
        if path == "/__sync__/answer":
            client_id = (qs.get("clientId") or [""])[0].strip()
            if not client_id:
                return self._json(400, {"ok": False, "error": "clientId required"})

            # 连接数上限检查（仅对首次出现的客户端生效）
            with lock:
                is_new = client_id not in room["clients_seen"]
                if is_new and len(room["clients_seen"]) >= MAX_CLIENTS_PER_ROOM:
                    return self._json(503, {
                        "ok":    False,
                        "error": f"观众组已满（最多 {MAX_CLIENTS_PER_ROOM} 人），请联系工作人员",
                        "capacity_exceeded": True,
                    })
                room["clients_seen"][client_id] = time.time()

            round_id = str(payload.get("roundId", ""))
            selected = payload.get("selectedOptions", [])
            answer = {
                "clientId":       client_id,
                "roundId":        payload.get("roundId"),
                "selectedOptions": selected,
                "role":           payload.get("role"),
                "empId":          payload.get("empId"),
                "empName":        payload.get("empName"),
                "teamId":         payload.get("teamId"),
                "teamName":       payload.get("teamName"),
                "teamRegion":     payload.get("teamRegion"),
            }
            with lock:
                bucket = room["answers"].setdefault(round_id, {})
                bucket[client_id] = answer
            return self._json(200, {"ok": True})

        # ── host updates aggregate audience stats ───────────────────────────
        if path == "/__sync__/audience_stats":
            with lock:
                room["audience_stats"] = {
                    "total":   int(payload.get("total",   0) or 0),
                    "correct": int(payload.get("correct", 0) or 0),
                }
            return self._json(200, {"ok": True})

        return self._json(404, {"ok": False, "error": "not found"})

    def log_message(self, fmt, *args):
        # Suppress per-request stdout spam; uncomment for debugging
        # print(f"[{self.client_address[0]}] " + fmt % args)
        pass


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cleaner = threading.Thread(target=clean_stale, daemon=True)
    cleaner.start()

    server = ThreadingHTTPServer(("0.0.0.0", 8080), Handler)
    print(f"✅ Serving on http://0.0.0.0:8080")
    print(f"   最大在线人数/房间: {MAX_CLIENTS_PER_ROOM}")
    print(f"   限流: {RATE_LIMIT_MAX_REQS} 次/{RATE_LIMIT_WINDOW_S}s / IP")
    print(f"   管理状态接口: http://localhost:8080/__sync__/admin/status?room=<roomId>")
    server.serve_forever()
