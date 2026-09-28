"""
Anonymous incident reporting backend.
Pure Python standard library only -- no pip install required.
Run:      python3 server.py
Deploy:   works on any host that can run `python3 server.py` (Render, Railway,
          Fly.io, a plain VPS with systemd). See README.md.
"""

import json, sqlite3, random, string, time, os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timezone

DB_PATH = os.environ.get("DB_PATH", "reports.db")
DASH_TOKEN = os.environ.get("DASH_TOKEN", "demo-token-change-me")
ROUTING_PATH = os.environ.get("ROUTING_PATH", "routing_table.json")
PORT = int(os.environ.get("PORT", 8080))

URGENCY_KEYWORDS = {
    "high": ["now", "right now", "ongoing", "trapped", "weapon", "gun", "knife",
              "bleeding", "unconscious", "fire", "burning", "child", "help me"],
    "medium": ["injured", "shouting", "fighting", "blocked", "stuck", "crying"],
}
NIGHT_HOURS = set(range(22, 24)) | set(range(0, 6))

RATE_LIMIT = {}  # device_fp -> [timestamps]
RATE_LIMIT_WINDOW = 600   # seconds
RATE_LIMIT_MAX = 6        # reports per window


def load_routing():
    with open(ROUTING_PATH) as f:
        return json.load(f)


def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS reports(
        ref TEXT PRIMARY KEY, ts TEXT, category TEXT, description TEXT,
        tags TEXT, location_text TEXT, lat REAL, lng REAL,
        urgency TEXT, spam_score REAL, status TEXT, routed_to TEXT,
        device_fp TEXT, has_photo INTEGER, has_audio INTEGER
    )""")
    con.commit()
    con.close()


def gen_ref():
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "RPT-" + "".join(random.choice(chars) for _ in range(6))


def score_urgency(text, tags):
    text_l = (text or "").lower()
    tag_l = " ".join(tags or []).lower()
    combined = text_l + " " + tag_l
    score = 0
    for kw in URGENCY_KEYWORDS["high"]:
        if kw in combined:
            score += 2
    for kw in URGENCY_KEYWORDS["medium"]:
        if kw in combined:
            score += 1
    if datetime.now(timezone.utc).hour in NIGHT_HOURS:
        score += 1
    if score >= 4:
        return "high"
    if score >= 2:
        return "medium"
    return "low"


def score_spam(description, device_fp):
    score = 0.0
    if not description or len(description.strip()) < 5:
        score += 0.6
    now = time.time()
    hist = [t for t in RATE_LIMIT.get(device_fp, []) if now - t < RATE_LIMIT_WINDOW]
    hist.append(now)
    RATE_LIMIT[device_fp] = hist
    if len(hist) > RATE_LIMIT_MAX:
        score += 0.5
    return min(score, 1.0)


def find_duplicates(con, category, lat, lng):
    if lat is None or lng is None:
        return 0
    cur = con.execute(
        """SELECT COUNT(*) FROM reports WHERE category=? AND
           ts > datetime('now', '-15 minutes') AND
           lat BETWEEN ? AND ? AND lng BETWEEN ? AND ?""",
        (category, lat - 0.01, lat + 0.01, lng - 0.01, lng + 0.01),
    )
    return cur.fetchone()[0]


def handle_incoming_report(payload):
    """Core pipeline shared by the web form and any future SMS/WhatsApp webhook."""
    if payload.get("hp"):  # honeypot tripped
        return {"ref": gen_ref(), "status": "received"}, 200

    category = payload.get("category")
    routing = load_routing()
    if category not in routing:
        return {"error": "unknown category"}, 400

    description = payload.get("description", "")
    tags = payload.get("tags", [])
    coords = payload.get("coords") or [None, None]
    lat, lng = (coords + [None, None])[:2]
    device_fp = payload.get("device_fp", "unknown")

    con = sqlite3.connect(DB_PATH)
    urgency = score_urgency(description, tags)
    spam = score_spam(description, device_fp)
    dup_count = find_duplicates(con, category, lat, lng)
    if dup_count > 0:
        spam = max(0, spam - 0.2)  # corroborated reports are less likely spam

    ref = gen_ref()
    route = routing[category]
    routed_names = [route["primary"]["name"]] + [s["name"] for s in route.get("secondary", [])]

    if spam >= 0.6:
        status = "rejected_spam"
    elif urgency == "low" or spam >= 0.3:
        status = "pending_review"
    else:
        status = "dispatched"

    con.execute(
        """INSERT INTO reports VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (ref, datetime.now(timezone.utc).isoformat(), category, description,
         json.dumps(tags), payload.get("location_text"), lat, lng,
         urgency, spam, status, json.dumps(routed_names), device_fp,
         int(bool(payload.get("photo"))), int(bool(payload.get("audio")))),
    )
    con.commit()
    con.close()

    return {
        "ref": ref,
        "status": status,
        "urgency": urgency,
        "routed_to": routed_names if status != "rejected_spam" else [],
        "corroborating_reports_nearby": dup_count,
    }, 200


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _json(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            return self._json(200, {"ok": True})

        if self.path.startswith("/api/report/"):
            ref = self.path.rsplit("/", 1)[-1]
            con = sqlite3.connect(DB_PATH)
            row = con.execute(
                "SELECT ref, ts, category, status, urgency FROM reports WHERE ref=?", (ref,)
            ).fetchone()
            con.close()
            if not row:
                return self._json(404, {"error": "not found"})
            keys = ["ref", "ts", "category", "status", "urgency"]
            return self._json(200, dict(zip(keys, row)))

        if self.path.startswith("/api/reports"):
            if self.headers.get("X-Token") != DASH_TOKEN:
                return self._json(401, {"error": "unauthorized"})
            con = sqlite3.connect(DB_PATH)
            rows = con.execute(
                """SELECT ref, ts, category, description, tags, location_text,
                          urgency, status, routed_to FROM reports
                   WHERE status != 'rejected_spam' ORDER BY ts DESC LIMIT 100"""
            ).fetchall()
            con.close()
            keys = ["ref", "ts", "category", "description", "tags",
                    "location_text", "urgency", "status", "routed_to"]
            out = []
            for r in rows:
                item = dict(zip(keys, r))
                item["tags"] = json.loads(item["tags"] or "[]")
                item["routed_to"] = json.loads(item["routed_to"] or "[]")
                out.append(item)
            return self._json(200, out)

        if self.path in ("/", "/index.html", "/dashboard"):
            name = "dashboard.html" if self.path == "/dashboard" else "report.html"
            try:
                here = os.path.dirname(os.path.abspath(__file__))
                with open(os.path.join(here, name), "rb") as f:
                    data = f.read()
            except FileNotFoundError:
                return self._json(404, {"error": "page missing"})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        self._json(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return self._json(400, {"error": "invalid json"})

        if self.path == "/api/report":
            body, code = handle_incoming_report(payload)
            return self._json(code, body)

        if self.path == "/webhook/sms":
            # Stub: wire this to your SMS aggregator (e.g. Africa's Talking, Termii).
            # Parse their payload shape into {category, description, tags, coords,
            # device_fp} and call handle_incoming_report(parsed) as above.
            return self._json(501, {"error": "SMS webhook not yet configured -- see README"})

        if self.path == "/webhook/whatsapp":
            # Stub: wire this to the WhatsApp Business Cloud API webhook.
            return self._json(501, {"error": "WhatsApp webhook not yet configured -- see README"})

        self._json(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        pass  # quiet request logging; use a real logger in production


if __name__ == "__main__":
    init_db()
    print(f"Listening on 0.0.0.0:{PORT}  (DB: {DB_PATH})")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
