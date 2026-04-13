#!/usr/bin/env python3
import json
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


rooms = {}
lock = threading.Lock()


def get_room(room_id: str):
  now = time.time()
  with lock:
    room = rooms.get(room_id)
    if room is None:
      room = {
        "host_state": None,
        "audience_stats": {"total": 0, "correct": 0},
        "answers": {},
        "clients_seen": {}
      }
      rooms[room_id] = room
    room["updated_at"] = now
    return room


def clean_stale():
  while True:
    time.sleep(60)
    now = time.time()
    with lock:
      to_del = []
      for rid, room in rooms.items():
        if now - room.get("updated_at", now) > 3600:
          to_del.append(rid)
          continue
        seen = room.get("clients_seen", {})
        for cid in list(seen.keys()):
          if now - seen[cid] > 30:
            seen.pop(cid, None)
      for rid in to_del:
        rooms.pop(rid, None)


class Handler(SimpleHTTPRequestHandler):
  def _cors_headers(self):
    self.send_header("Access-Control-Allow-Origin", "*")
    self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    self.send_header("Access-Control-Allow-Headers", "Content-Type")
    self.send_header("Access-Control-Max-Age", "86400")

  def do_OPTIONS(self):
    self.send_response(204)
    self._cors_headers()
    self.send_header("Content-Length", "0")
    self.end_headers()

  def _json(self, code, data):
    raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
    self.send_response(code)
    self._cors_headers()
    self.send_header("Content-Type", "application/json; charset=utf-8")
    self.send_header("Content-Length", str(len(raw)))
    self.end_headers()
    self.wfile.write(raw)

  def _get_room_and_params(self):
    parsed = urlparse(self.path)
    qs = parse_qs(parsed.query)
    room_id = (qs.get("room") or [""])[0].strip()
    return parsed.path, qs, room_id

  def do_GET(self):
    path, qs, room_id = self._get_room_and_params()
    if not path.startswith("/__sync__/"):
      return super().do_GET()
    if not room_id:
      return self._json(400, {"ok": False, "error": "room required"})
    room = get_room(room_id)
    client_id = (qs.get("clientId") or [""])[0].strip()
    if client_id:
      with lock:
        room["clients_seen"][client_id] = time.time()
    if path == "/__sync__/host_state":
      return self._json(200, {
        "ok": True,
        "payload": room.get("host_state"),
        "audienceStats": room.get("audience_stats", {"total": 0, "correct": 0})
      })
    if path == "/__sync__/answers":
      round_id = (qs.get("roundId") or [""])[0].strip()
      with lock:
        answers = []
        if round_id and round_id in room["answers"]:
          answers = list(room["answers"][round_id].values())
        clients = list(room["clients_seen"].keys())
      return self._json(200, {"ok": True, "answers": answers, "clients": clients})
    return self._json(404, {"ok": False, "error": "not found"})

  def do_POST(self):
    path, qs, room_id = self._get_room_and_params()
    if not path.startswith("/__sync__/"):
      return self._json(404, {"ok": False, "error": "not found"})
    if not room_id:
      return self._json(400, {"ok": False, "error": "room required"})
    length = int(self.headers.get("Content-Length", "0"))
    body = self.rfile.read(length) if length > 0 else b"{}"
    try:
      payload = json.loads(body.decode("utf-8"))
    except Exception:
      payload = {}
    room = get_room(room_id)

    if path == "/__sync__/host_state":
      with lock:
        room["host_state"] = payload
      return self._json(200, {"ok": True})

    if path == "/__sync__/answer":
      client_id = (qs.get("clientId") or [""])[0].strip()
      if not client_id:
        return self._json(400, {"ok": False, "error": "clientId required"})
      round_id = str(payload.get("roundId", ""))
      selected = payload.get("selectedOptions", [])
      answer = {"clientId": client_id, "roundId": payload.get("roundId"), "selectedOptions": selected}
      with lock:
        room["clients_seen"][client_id] = time.time()
        bucket = room["answers"].setdefault(round_id, {})
        bucket[client_id] = answer
      return self._json(200, {"ok": True})

    if path == "/__sync__/audience_stats":
      with lock:
        room["audience_stats"] = {
          "total": int(payload.get("total", 0) or 0),
          "correct": int(payload.get("correct", 0) or 0)
        }
      return self._json(200, {"ok": True})

    return self._json(404, {"ok": False, "error": "not found"})


if __name__ == "__main__":
  cleaner = threading.Thread(target=clean_stale, daemon=True)
  cleaner.start()
  server = ThreadingHTTPServer(("0.0.0.0", 8080), Handler)
  print("Serving on http://0.0.0.0:8080")
  server.serve_forever()
