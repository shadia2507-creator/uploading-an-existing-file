#!/usr/bin/env python3
"""
El Ritual Secreto de la Puerta Plateada · Servidor de la campaña.

Usa solo la librería estándar de Python 3.9 o superior, sin dependencias externas.
Sirve la landing que está en ./public y expone una API pequeña:

  GET  /api/status?pid=...      sellos del participante, premio desbloqueado y si ya se inscribió
  POST /api/redeem              canjea el código de una tapa y otorga el sello
  POST /api/register            inscribe al adulto responsable en el sorteo (requiere 3 sellos)

  GET  /healthz                 chequeo de salud para la plataforma
  GET  /admin/stats             métricas de la campaña              (requiere RITUAL_ADMIN_TOKEN)
  POST /admin/generate?product=bonyurt&count=1000&batch=lote1   crea códigos y los descarga en CSV
  GET  /admin/registrations.csv inscritos al sorteo para Excel      (requiere RITUAL_ADMIN_TOKEN)
  POST /admin/draw?n=1          sortea ganadores entre los inscritos (requiere RITUAL_ADMIN_TOKEN)

Comandos:
  python3 server.py serve --port 8000
  python3 server.py generate --product bonyurt --count 50000 --batch lote1
  python3 server.py stats
  python3 server.py export --out inscritos.csv
  python3 server.py draw --n 1
"""
import argparse
import csv
import io
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

# ---------------------------------------------------------------- configuración
BASE = os.path.dirname(os.path.abspath(__file__))
PUBLIC = os.path.join(BASE, "public")
# En Railway, si hay un volumen conectado, la base vive ahí y sobrevive a cada despliegue.
_VOLUME = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "")
DB_PATH = os.environ.get("RITUAL_DB") or (os.path.join(_VOLUME, "ritual.db") if _VOLUME else os.path.join(BASE, "data", "ritual.db"))
ON_CLOUD = bool(os.environ.get("PORT") or os.environ.get("RAILWAY_ENVIRONMENT"))
ADMIN_TOKEN = os.environ.get("RITUAL_ADMIN_TOKEN", "")
SITE_URL = os.environ.get("RITUAL_SITE_URL", "https://TU-DOMINIO.com").rstrip("/")
TRUST_PROXY = os.environ.get("RITUAL_TRUST_PROXY", "0") == "1"
POLICY_VERSION = os.environ.get("RITUAL_POLICY_VERSION", "v1")

COT = timezone(timedelta(hours=-5))  # hora de Colombia
START = datetime(2026, 10, 1, 0, 0, 0, tzinfo=COT)
END = datetime(2026, 10, 31, 23, 59, 59, tzinfo=COT)

PRODUCTS = ("bonyurt", "alpinette", "yogoyogo")
PRODUCT_NAMES = {"bonyurt": "Bon Yurt", "alpinette": "Alpinette", "yogoyogo": "Yogo Yogo"}
# Sin 0, O, 1 ni I para que los niños no se confundan al escribir el código.
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_RE = re.compile(r"^[A-Z0-9]{8}$")
PID_RE = re.compile(r"^[a-f0-9-]{16,40}$")
EMAIL_RE = re.compile(r"^[^\s@]{1,64}@[^\s@]{1,190}\.[^\s@]{2,24}$")
MAX_REG_PER_EMAIL = 5  # un adulto puede inscribir a varios hermanos

# Premio seguro. Estos enlaces solo se entregan a quien tiene los 3 sellos.
UNLOCKS = {
    "secretFilter": os.environ.get("RITUAL_SECRET_FILTER_URL", ""),
    "seriesPreview": os.environ.get("RITUAL_SERIES_PREVIEW_URL", ""),
}


def now():
    return datetime.now(COT)


def iso(dt=None):
    return (dt or now()).isoformat(timespec="seconds")


def window_state():
    t = now()
    if t < START:
        return "not_started"
    if t > END:
        return "ended"
    return "open"


# ---------------------------------------------------------------- base de datos
SCHEMA = """
CREATE TABLE IF NOT EXISTS codes(
  code TEXT PRIMARY KEY,
  product TEXT NOT NULL,
  batch TEXT,
  created_at TEXT NOT NULL,
  redeemed_by TEXT,
  redeemed_at TEXT
);
CREATE INDEX IF NOT EXISTS codes_redeemed ON codes(redeemed_by);
CREATE TABLE IF NOT EXISTS seals(
  pid TEXT NOT NULL,
  product TEXT NOT NULL,
  code TEXT NOT NULL,
  via TEXT,
  at TEXT NOT NULL,
  PRIMARY KEY(pid, product)
);
CREATE TABLE IF NOT EXISTS registrations(
  pid TEXT PRIMARY KEY,
  kid_name TEXT NOT NULL,
  adult_name TEXT NOT NULL,
  email TEXT NOT NULL,
  phone TEXT NOT NULL,
  city TEXT NOT NULL,
  policy_version TEXT NOT NULL,
  consent_at TEXT NOT NULL,
  ip TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS reg_email ON registrations(email);
CREATE TABLE IF NOT EXISTS winners(
  pid TEXT PRIMARY KEY,
  drawn_at TEXT NOT NULL
);
"""

_local = threading.local()


def db():
    conn = getattr(_local, "conn", None)
    if conn is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=15000")
        _local.conn = conn
    return conn


def init_db():
    db().executescript(SCHEMA)


def seals_of(pid):
    rows = db().execute("SELECT product FROM seals WHERE pid=?", (pid,)).fetchall()
    return [r["product"] for r in rows]


def status_payload(pid):
    s = seals_of(pid)
    complete = len(set(s)) == len(PRODUCTS)
    registered = db().execute("SELECT 1 FROM registrations WHERE pid=?", (pid,)).fetchone() is not None
    return {
        "ok": True,
        "seals": s,
        "complete": complete,
        "registered": registered,
        "unlocks": UNLOCKS if complete else None,
        "window": window_state(),
    }


# ---------------------------------------------------------------- límite de intentos
class Limiter:
    """Ventana deslizante en memoria. Frena a quien intenta adivinar códigos."""

    def __init__(self):
        self.lock = threading.Lock()
        self.hits = {}

    def allow(self, key, limit, window):
        t = time.time()
        with self.lock:
            arr = [x for x in self.hits.get(key, ()) if t - x < window]
            ok = len(arr) < limit
            if ok:
                arr.append(t)
            self.hits[key] = arr
            if len(self.hits) > 100000:
                self.hits = {k: v for k, v in self.hits.items() if v and t - v[-1] < 3600}
            return ok

    def blocked(self, key, limit, window):
        t = time.time()
        with self.lock:
            return len([x for x in self.hits.get(key, ()) if t - x < window]) >= limit


LIMIT = Limiter()


# ---------------------------------------------------------------- lógica de la API
class ApiError(Exception):
    def __init__(self, code, status=400, **extra):
        super().__init__(code)
        self.code = code
        self.status = status
        self.extra = extra


def api_redeem(body, ip):
    pid = str(body.get("pid", "")).lower()
    code = re.sub(r"[^A-Z0-9]", "", str(body.get("code", "")).upper())
    via = "qr" if body.get("via") == "qr" else "code"
    if not PID_RE.match(pid):
        raise ApiError("bad_request")
    if not CODE_RE.match(code):
        raise ApiError("invalid")
    w = window_state()
    if w != "open":
        raise ApiError(w, 403)
    # 20 códigos fallidos cada 15 minutos por IP y 10 por participante.
    if LIMIT.blocked("fail:ip:" + ip, 20, 900) or LIMIT.blocked("fail:pid:" + pid, 10, 900):
        raise ApiError("rate_limited", 429)

    conn = db()
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT product, redeemed_by FROM codes WHERE code=?", (code,)).fetchone()
        if row is None:
            conn.execute("ROLLBACK")
            LIMIT.allow("fail:ip:" + ip, 10**9, 900)
            LIMIT.allow("fail:pid:" + pid, 10**9, 900)
            raise ApiError("invalid")
        product = row["product"]  # la tapa real manda, aunque el niño elija otra
        if row["redeemed_by"] and row["redeemed_by"] != pid:
            conn.execute("ROLLBACK")
            LIMIT.allow("fail:ip:" + ip, 10**9, 900)
            LIMIT.allow("fail:pid:" + pid, 10**9, 900)
            raise ApiError("used", 409)
        has_seal = conn.execute("SELECT 1 FROM seals WHERE pid=? AND product=?", (pid, product)).fetchone()
        already = bool(has_seal)
        if not has_seal:
            # Solo se gasta el código cuando de verdad otorga un sello nuevo.
            t = iso()
            conn.execute("UPDATE codes SET redeemed_by=?, redeemed_at=? WHERE code=?", (pid, t, code))
            conn.execute("INSERT INTO seals(pid, product, code, via, at) VALUES(?,?,?,?,?)", (pid, product, code, via, t))
        conn.execute("COMMIT")
    except ApiError:
        raise
    except Exception:
        conn.execute("ROLLBACK")
        raise
    out = status_payload(pid)
    out.update({"granted": product, "already": already})
    return out


def clean_text(v, lo, hi):
    v = re.sub(r"\s+", " ", str(v or "")).strip()
    if not (lo <= len(v) <= hi):
        return None
    if re.search(r"[<>{}\\]", v):
        return None
    return v


def api_register(body, ip):
    pid = str(body.get("pid", "")).lower()
    if not PID_RE.match(pid):
        raise ApiError("bad_request")
    if window_state() == "ended":
        raise ApiError("ended", 403)
    if not LIMIT.allow("reg:ip:" + ip, 30, 3600):
        raise ApiError("rate_limited", 429)
    fields = {
        "kid": clean_text(body.get("kid"), 1, 60),
        "adult": clean_text(body.get("adult"), 3, 80),
        "city": clean_text(body.get("city"), 2, 60),
    }
    email = str(body.get("email", "")).strip().lower()
    fields["email"] = email if EMAIL_RE.match(email) and len(email) <= 160 else None
    phone = re.sub(r"\D", "", str(body.get("phone", "")))
    if phone.startswith("57") and len(phone) == 12:
        phone = phone[2:]
    fields["phone"] = phone if re.match(r"^3\d{9}$", phone) else None
    for k, v in fields.items():
        if v is None:
            raise ApiError("bad_field", field=k)
    if body.get("consentAdult") is not True or body.get("consentData") is not True:
        raise ApiError("bad_field", field="consent")

    conn = db()
    if len(set(seals_of(pid))) < len(PRODUCTS):
        raise ApiError("need_seals", 403)
    if conn.execute("SELECT 1 FROM registrations WHERE pid=?", (pid,)).fetchone():
        raise ApiError("already_registered", 409)
    n = conn.execute("SELECT COUNT(*) FROM registrations WHERE email=?", (fields["email"],)).fetchone()[0]
    if n >= MAX_REG_PER_EMAIL:
        raise ApiError("email_limit", 409)
    t = iso()
    try:
        conn.execute(
            "INSERT INTO registrations(pid, kid_name, adult_name, email, phone, city, policy_version, consent_at, ip, created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (pid, fields["kid"], fields["adult"], fields["email"], fields["phone"], fields["city"], POLICY_VERSION, t, ip, t),
        )
    except sqlite3.IntegrityError:
        raise ApiError("already_registered", 409)
    return status_payload(pid)


# ---------------------------------------------------------------- administración
def stats():
    conn = db()
    per = {}
    for p in PRODUCTS:
        total = conn.execute("SELECT COUNT(*) FROM codes WHERE product=?", (p,)).fetchone()[0]
        used = conn.execute("SELECT COUNT(*) FROM codes WHERE product=? AND redeemed_by IS NOT NULL", (p,)).fetchone()[0]
        per[p] = {"codes": total, "redeemed": used}
    participants = conn.execute("SELECT COUNT(DISTINCT pid) FROM seals").fetchone()[0]
    complete = conn.execute(
        "SELECT COUNT(*) FROM (SELECT pid FROM seals GROUP BY pid HAVING COUNT(DISTINCT product)=?)", (len(PRODUCTS),)
    ).fetchone()[0]
    regs = conn.execute("SELECT COUNT(*) FROM registrations").fetchone()[0]
    winners = conn.execute("SELECT COUNT(*) FROM winners").fetchone()[0]
    return {
        "ok": True,
        "window": window_state(),
        "products": per,
        "participants_with_a_seal": participants,
        "participants_complete": complete,
        "registrations": regs,
        "winners": winners,
    }


def safe_cell(v):
    v = "" if v is None else str(v)
    return "'" + v if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v


def registrations_csv():
    buf = io.StringIO()
    buf.write("﻿")  # para que Excel abra bien las tildes
    w = csv.writer(buf)
    w.writerow(["id_participante", "nombre_guardian", "adulto", "correo", "celular", "ciudad", "version_politica", "fecha_consentimiento", "ganador"])
    rows = db().execute(
        "SELECT r.*, (w.pid IS NOT NULL) AS winner FROM registrations r LEFT JOIN winners w ON w.pid=r.pid ORDER BY r.created_at"
    ).fetchall()
    for r in rows:
        w.writerow([safe_cell(x) for x in (r["pid"], r["kid_name"], r["adult_name"], r["email"], r["phone"], r["city"], r["policy_version"], r["consent_at"], "sí" if r["winner"] else "")])
    return buf.getvalue()


def draw(n):
    conn = db()
    pool = [r["pid"] for r in conn.execute("SELECT pid FROM registrations WHERE pid NOT IN (SELECT pid FROM winners)").fetchall()]
    picks = secrets.SystemRandom().sample(pool, min(n, len(pool)))
    t = iso()
    for p in picks:
        conn.execute("INSERT INTO winners(pid, drawn_at) VALUES(?,?)", (p, t))
    out = []
    for p in picks:
        r = conn.execute("SELECT kid_name, adult_name, email, phone, city FROM registrations WHERE pid=?", (p,)).fetchone()
        out.append({"pid": p, **dict(r)})
    return {"ok": True, "drawn_at": t, "pool": len(pool), "winners": out}


def make_codes(product, count, batch):
    conn = db()
    made = []
    t = iso()
    conn.execute("BEGIN")
    while len(made) < count:
        code = "".join(secrets.choice(ALPHABET) for _ in range(8))
        try:
            conn.execute("INSERT INTO codes(code, product, batch, created_at) VALUES(?,?,?,?)", (code, product, batch, t))
            made.append(code)
        except sqlite3.IntegrityError:
            continue
    conn.execute("COMMIT")
    return made


def codes_csv(made, product, batch):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["codigo", "producto", "lote", "url_qr"])
    for c in made:
        w.writerow([c, PRODUCT_NAMES[product], batch, "%s/?tapa=%s&c=%s" % (SITE_URL, product, c)])
    return buf.getvalue()


def generate(product, count, batch, out_path):
    if product not in PRODUCTS:
        sys.exit("Producto inválido. Usa: " + ", ".join(PRODUCTS))
    made = make_codes(product, count, batch)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        f.write(codes_csv(made, product, batch))
    print("Listo: %d códigos de %s en %s" % (len(made), PRODUCT_NAMES[product], out_path))


# ---------------------------------------------------------------- servidor HTTP
CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com; img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'self'; base-uri 'self'"
)


class Handler(SimpleHTTPRequestHandler):
    server_version = "Ritual/1.0"
    sys_version = ""

    def __init__(self, *a, **k):
        super().__init__(*a, directory=PUBLIC, **k)

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Content-Security-Policy", CSP)
        super().end_headers()

    def list_directory(self, path):
        self.send_error(404)
        return None

    def ip(self):
        if TRUST_PROXY:
            # El proxy de la plataforma agrega la IP real al final. La primera la puede inventar el visitante.
            real = self.headers.get("X-Real-IP", "").strip()
            if real:
                return real
            fwd = [x.strip() for x in self.headers.get("X-Forwarded-For", "").split(",") if x.strip()]
            if fwd:
                return fwd[-1]
        return self.client_address[0]

    def send_json(self, status, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > 8192:
            raise ApiError("bad_request")
        try:
            body = json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            raise ApiError("bad_request")
        if not isinstance(body, dict):
            raise ApiError("bad_request")
        return body

    def is_admin(self, q):
        if not ADMIN_TOKEN:
            return False
        auth = self.headers.get("Authorization", "")
        tok = auth[7:] if auth.startswith("Bearer ") else (q.get("token", [""])[0])
        return secrets.compare_digest(tok.encode(), ADMIN_TOKEN.encode())

    def handle_api(self, method):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        ip = self.ip()
        try:
            if u.path == "/healthz":
                return self.send_json(200, {"ok": True})
            if not LIMIT.allow("req:" + ip, 240, 60):
                raise ApiError("rate_limited", 429)
            if u.path == "/api/status" and method == "GET":
                pid = q.get("pid", [""])[0].lower()
                if not PID_RE.match(pid):
                    raise ApiError("bad_request")
                return self.send_json(200, status_payload(pid))
            if u.path == "/api/redeem" and method == "POST":
                return self.send_json(200, api_redeem(self.read_json(), ip))
            if u.path == "/api/register" and method == "POST":
                return self.send_json(200, api_register(self.read_json(), ip))
            if u.path.startswith("/admin/"):
                if not self.is_admin(q):
                    raise ApiError("not_found", 404)
                if u.path == "/admin/stats" and method == "GET":
                    return self.send_json(200, stats())
                if u.path == "/admin/registrations.csv" and method == "GET":
                    data = registrations_csv().encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/csv; charset=utf-8")
                    self.send_header("Content-Disposition", 'attachment; filename="inscritos-ritual.csv"')
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return None
                if u.path == "/admin/generate" and method == "POST":
                    product = q.get("product", [""])[0]
                    batch = re.sub(r"[^A-Za-z0-9_-]", "", q.get("batch", ["lote1"])[0])[:40] or "lote1"
                    try:
                        count = int(q.get("count", ["0"])[0])
                    except ValueError:
                        count = 0
                    if product not in PRODUCTS or not (1 <= count <= 500000):
                        raise ApiError("bad_request")
                    data = codes_csv(make_codes(product, count, batch), product, batch).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/csv; charset=utf-8")
                    self.send_header("Content-Disposition", 'attachment; filename="codigos-%s-%s.csv"' % (product, batch))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return None
                if u.path == "/admin/draw" and method == "POST":
                    n = max(1, min(50, int(q.get("n", ["1"])[0] or 1)))
                    return self.send_json(200, draw(n))
            raise ApiError("not_found", 404)
        except ApiError as e:
            return self.send_json(e.status, {"ok": False, "error": e.code, **e.extra})
        except Exception as e:  # nunca mostrar detalles internos al público
            sys.stderr.write("ERROR %s %s: %r\n" % (method, u.path, e))
            return self.send_json(500, {"ok": False, "error": "server"})

    def do_GET(self):
        p = urlparse(self.path).path
        if p.startswith("/api/") or p.startswith("/admin/") or p == "/healthz":
            return self.handle_api("GET")
        if "/." in p:
            return self.send_error(404)
        return super().do_GET()

    def do_HEAD(self):
        p = urlparse(self.path).path
        if p.startswith("/api/") or p.startswith("/admin/") or "/." in p:
            return self.send_error(404)
        return super().do_HEAD()

    def do_POST(self):
        p = urlparse(self.path).path
        if p.startswith("/api/") or p.startswith("/admin/"):
            return self.handle_api("POST")
        self.send_error(405)


def main():
    ap = argparse.ArgumentParser(description="Servidor del Ritual Secreto de la Puerta Plateada")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("serve", help="Arranca la web y la API")
    # En la nube la plataforma define PORT y hay que escuchar en todas las interfaces.
    s.add_argument("--host", default=os.environ.get("HOST") or ("0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"))
    s.add_argument("--port", type=int, default=int(os.environ.get("PORT") or 8000))
    g = sub.add_parser("generate", help="Crea códigos únicos para imprimir en las tapas")
    g.add_argument("--product", required=True, choices=PRODUCTS)
    g.add_argument("--count", type=int, required=True)
    g.add_argument("--batch", default="lote1")
    g.add_argument("--out", default=None)
    sub.add_parser("stats", help="Muestra métricas")
    e = sub.add_parser("export", help="Exporta los inscritos al sorteo")
    e.add_argument("--out", default="inscritos-ritual.csv")
    d = sub.add_parser("draw", help="Sortea ganadores entre los inscritos")
    d.add_argument("--n", type=int, default=1)
    a = ap.parse_args()

    init_db()
    if a.cmd == "generate":
        generate(a.product, a.count, a.batch, a.out or "codigos-%s-%s.csv" % (a.product, a.batch))
    elif a.cmd == "stats":
        print(json.dumps(stats(), ensure_ascii=False, indent=2))
    elif a.cmd == "export":
        with open(a.out, "w", encoding="utf-8", newline="") as f:
            f.write(registrations_csv())
        print("Listo: " + a.out)
    elif a.cmd == "draw":
        print(json.dumps(draw(a.n), ensure_ascii=False, indent=2))
    else:
        host = getattr(a, "host", None) or os.environ.get("HOST") or ("0.0.0.0" if os.environ.get("PORT") else "127.0.0.1")
        port = getattr(a, "port", None) or int(os.environ.get("PORT") or 8000)
        print("Base de datos: " + DB_PATH)
        if ON_CLOUD and not (_VOLUME or os.environ.get("RITUAL_DB")):
            print("ALERTA: no hay volumen conectado. Los códigos y registros se BORRAN en cada despliegue.")
        if not ADMIN_TOKEN:
            print("Aviso: RITUAL_ADMIN_TOKEN está vacío, así que el panel /admin está apagado.")
        srv = ThreadingHTTPServer((host, port), Handler)
        srv.daemon_threads = True
        print("El ritual está abierto en http://%s:%d  (Ctrl+C para cerrar)" % (host, port))
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
