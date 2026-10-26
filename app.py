"""
ScleraScope — eye (sclera) wellness scan app.
Flask + SQLite + Gemini vision. Disclaimers and consent are enforced before any
photo can be uploaded; monetization runs through license codes (Gumroad or local).
"""
import base64
import json
import os
import re
import secrets
import sqlite3
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

from flask import (Flask, abort, g, jsonify, redirect, render_template, request,
                   session, url_for)

import sclerology
import vision

ROOT = Path(__file__).parent


def _default_data_dir() -> Path:
    """Prefer a persistent disk when one is mounted.

    Render blueprints mount the persistent disk at /var/data. If DATA_DIR is
    not set we must still use that mount, otherwise SQLite and the photos land
    on the ephemeral filesystem and every restart wipes scans and unlock codes.
    """
    for cand in (os.environ.get("DATA_DIR"), "/var/data"):
        if cand and Path(cand).is_dir() and os.access(cand, os.W_OK):
            return Path(cand)
    return ROOT / "data"


DATA = _default_data_dir()
SCANS_DIR = DATA / "scans"
DB_PATH = DATA / "sclerascope.db"
WAIVER_VERSION = "2026-10-25"
PRICE_FULL = os.environ.get("PRICE_FULL", "19")
PRICE_MONTH = os.environ.get("PRICE_MONTH", "9.99")
GUMROAD_PRODUCT_ID = os.environ.get("GUMROAD_PRODUCT_ID", "").strip()
SUPPORT_EMAIL = os.environ.get("SUPPORT_EMAIL", "support@dfyai.online")
FREE_SCANS = int(os.environ.get("FREE_SCANS", "1"))

DATA.mkdir(exist_ok=True)
SCANS_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 24 * 1024 * 1024
_secret_file = DATA / "secret_key"
if not _secret_file.exists():
    _secret_file.write_text(secrets.token_hex(32))
app.secret_key = os.environ.get("SECRET_KEY") or _secret_file.read_text().strip()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=bool(os.environ.get("HTTPS_ONLY")))

_admin_file = ROOT / ".admin_key"
if not _admin_file.exists():
    _admin_file.write_text(secrets.token_urlsafe(24))
ADMIN_KEY = os.environ.get("ADMIN_KEY") or _admin_file.read_text().strip()


# ---------------------------------------------------------------- database ---
def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS consents (
  id TEXT PRIMARY KEY, created_at TEXT, ip TEXT, user_agent TEXT,
  waiver_version TEXT, age_confirmed INTEGER, waiver_accepted INTEGER,
  email TEXT, name TEXT, marketing_opt_in INTEGER
);
CREATE TABLE IF NOT EXISTS scans (
  id TEXT PRIMARY KEY, consent_id TEXT, created_at TEXT, ip TEXT,
  waiver_version TEXT, report_json TEXT, unlocked INTEGER DEFAULT 0,
  right_path TEXT, left_path TEXT, model TEXT, mode TEXT DEFAULT 'free'
);
CREATE TABLE IF NOT EXISTS codes (
  code TEXT PRIMARY KEY, kind TEXT, scans_left INTEGER, days INTEGER,
  created_at TEXT, used_at TEXT, used_by TEXT, note TEXT
);
CREATE TABLE IF NOT EXISTS unlocks (
  id TEXT PRIMARY KEY, scan_id TEXT, code TEXT, created_at TEXT, ip TEXT
);
CREATE INDEX IF NOT EXISTS idx_scans_created ON scans(created_at);
"""


def init_db():
    with sqlite3.connect(DB_PATH) as c:
        c.executescript(SCHEMA)


def now():
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def client_ip():
    fwd = request.headers.get("X-Forwarded-For", "")
    return (fwd.split(",")[0].strip() if fwd else request.remote_addr) or "unknown"


# ------------------------------------------------------------------ routes ---
@app.route("/")
def index():
    return render_template("index.html", price=PRICE_FULL, price_month=PRICE_MONTH,
                           free_scans=FREE_SCANS, disclaimer=sclerology.DISCLAIMER_SHORT)


@app.route("/start", methods=["GET", "POST"])
def start():
    if request.method == "POST":
        age = request.form.get("age") == "on"
        waiver = request.form.get("waiver") == "on"
        privacy = request.form.get("privacy") == "on"
        if not (age and waiver and privacy):
            return render_template("waiver.html", error="You need to tick all three boxes to continue.",
                                   waiver_version=WAIVER_VERSION), 400
        cid = uuid.uuid4().hex
        db().execute(
            "INSERT INTO consents (id,created_at,ip,user_agent,waiver_version,age_confirmed,"
            "waiver_accepted,email,name,marketing_opt_in) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (cid, now(), client_ip(), request.headers.get("User-Agent", "")[:400], WAIVER_VERSION,
             1, 1, (request.form.get("email") or "").strip()[:200],
             (request.form.get("name") or "").strip()[:120],
             1 if request.form.get("marketing") == "on" else 0))
        db().commit()
        session["consent_id"] = cid
        session["waiver_version"] = WAIVER_VERSION
        return redirect(url_for("capture"))
    return render_template("waiver.html", waiver_version=WAIVER_VERSION)


def require_consent():
    if session.get("waiver_version") != WAIVER_VERSION or not session.get("consent_id"):
        return redirect(url_for("start"))
    return None


@app.route("/capture")
def capture():
    r = require_consent()
    if r:
        return r
    return render_template("capture.html", disclaimer=sclerology.DISCLAIMER_SHORT)


def decode_image(data_url):
    if not isinstance(data_url, str) or "," not in data_url:
        raise ValueError("bad image payload")
    head, b64 = data_url.split(",", 1)
    mime = "image/jpeg"
    m = re.search(r"data:([^;]+);base64", head)
    if m:
        mime = m.group(1)
    if mime not in ("image/jpeg", "image/png", "image/webp"):
        raise ValueError("unsupported image type")
    raw = base64.b64decode(b64, validate=True)
    if len(raw) < 5_000:
        raise ValueError("image too small — retake closer to the eye")
    if len(raw) > 12 * 1024 * 1024:
        raise ValueError("image too large")
    return raw, mime


@app.route("/api/scan", methods=["POST"])
def api_scan():
    if session.get("waiver_version") != WAIVER_VERSION:
        return jsonify({"error": "consent required"}), 403
    payload = request.get_json(silent=True) or {}
    if not (payload.get("right") and payload.get("left")):
        return jsonify({"error": "both eye photos are required"}), 400

    scan_id = uuid.uuid4().hex[:12]
    images, paths, mimes = {}, {}, {}
    try:
        for side in ("right", "left"):
            raw, mime = decode_image(payload[side])
            images[side], mimes[side] = raw, mime
            ext = "png" if mime == "image/png" else "jpg"
            p = SCANS_DIR / f"{scan_id}_{side}.{ext}"
            p.write_bytes(raw)
            paths[side] = str(p)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    started = time.time()
    t_vision_start = time.time()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {s: pool.submit(vision.observe_eye, images[s], s, mimes[s]) for s in ("right", "left")}
        obs = {}
        for side, fut in futures.items():
            try:
                obs[side] = fut.result(timeout=180)
            except Exception as e:  # noqa: BLE001
                obs[side] = dict(vision.EMPTY, usable=False,
                                 usable_reasons=[f"analysis failed: {str(e)[:80]}"],
                                 _error=str(e)[:200])

    if all(obs[s].get("_error") for s in ("right", "left")):
        # provider problem, not a bad photo: never hand the user a fake "unclear" report
        for p in paths.values():
            try:
                Path(p).unlink(missing_ok=True)
            except OSError:
                pass
        return jsonify({"error": "Our eye-analysis service is busy right now. Your photos were not kept "
                                 "and you were not charged — try again in a minute."}), 503

    t_vision = time.time() - t_vision_start
    t_sum_start = time.time()
    trad_lines, all_findings = [], []
    for side in ("right", "left"):
        fs = sclerology.build_findings(obs[side], side)
        all_findings += fs
        trad_lines += [f["trad"] for f in fs]
    needs_pro = [f for f in all_findings if f["needs_pro"]]
    unreadable = [s for s in ("right", "left") if not obs[s].get("usable")]

    # Optional model-written polish, hard-capped at 8s. Off by default: it costs a
    # second API call per scan and the deterministic summary is just as accurate.
    summary = ""
    if all_findings and vision.GEMINI_KEY and os.environ.get("ENABLE_LLM_SUMMARY") == "1":
        box = {}

        def _attempt():
            try:
                box["text"] = vision.write_summary(obs, trad_lines)
            except Exception:
                pass

        import threading
        th = threading.Thread(target=_attempt, daemon=True)
        th.start()
        th.join(timeout=8)
        cand = (box.get("text") or "").strip()
        if sclerology.summary_is_safe(cand) and "medical exam" in cand.lower():
            summary = cand
    if not summary:
        summary = sclerology.build_summary(obs, all_findings, needs_pro, unreadable)

    t_summary = time.time() - t_sum_start
    report = sclerology.build_report(obs, summary)
    report["timing"] = {"vision_s": round(t_vision, 1), "summary_s": round(t_summary, 1),
                        "llm_summary": bool(summary and all_findings and "medical exam" in summary.lower())}
    report["scan_id"] = scan_id
    report["created_at"] = now()
    report["elapsed_s"] = round(time.time() - started, 1)
    report["waiver_version"] = WAIVER_VERSION

    db().execute(
        "INSERT INTO scans (id,consent_id,created_at,ip,waiver_version,report_json,"
        "right_path,left_path,model) VALUES (?,?,?,?,?,?,?,?,?)",
        (scan_id, session.get("consent_id"), now(), client_ip(), WAIVER_VERSION,
         json.dumps(report), paths["right"], paths["left"],
         obs.get("right", {}).get("_model", "")))
    db().commit()
    session.setdefault("scans", []).append(scan_id)
    try:
        purge_old()  # keeps the 30-day privacy promise without a scheduler
    except Exception:  # noqa: BLE001
        pass
    return jsonify({"scan_id": scan_id, "elapsed": report["elapsed_s"]})


def is_unlocked(scan_id, consent_id):
    if session.get("unlocked_until"):
        try:
            if datetime.fromisoformat(session["unlocked_until"]) > datetime.utcnow():
                return True
        except ValueError:
            pass
    row = db().execute("SELECT unlocked FROM scans WHERE id=?", (scan_id,)).fetchone()
    if row and row["unlocked"]:
        return True
    row = db().execute(
        "SELECT 1 FROM unlocks WHERE scan_id=? OR (scan_id IS NULL AND code IN "
        "(SELECT code FROM codes WHERE used_by=?)) LIMIT 1",
        (scan_id, consent_id)).fetchone()
    return bool(row)


@app.route("/report/<scan_id>")
def report(scan_id):
    row = db().execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone()
    if not row:
        abort(404)
    rep = json.loads(row["report_json"])
    unlocked = is_unlocked(scan_id, row["consent_id"])
    return render_template("report.html", r=rep, unlocked=unlocked, scan_id=scan_id,
                           price=PRICE_FULL, price_month=PRICE_MONTH,
                           disclaimer=sclerology.DISCLAIMER_SHORT, support=SUPPORT_EMAIL)


@app.route("/preview/<scan_id>")
def preview(scan_id):
    """Admin-only: view any stored report fully unlocked (for design checks)."""
    if not admin_authed():
        return "admin key required: /preview/ID?key=YOUR_KEY", 401
    row = db().execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone()
    if not row:
        abort(404)
    return render_template("report.html", r=json.loads(row["report_json"]), unlocked=True,
                           scan_id=scan_id, price=PRICE_FULL, price_month=PRICE_MONTH,
                           disclaimer=sclerology.DISCLAIMER_SHORT, support=SUPPORT_EMAIL)


@app.route("/redeem", methods=["GET", "POST"])
def redeem():
    msg, ok = None, False
    if request.method == "POST":
        code = (request.form.get("code") or "").strip().upper()
        scan_id = request.form.get("scan_id") or ""
        if not code:
            msg = "Enter the code from your purchase email."
        else:
            result = check_code(code)
            if result["ok"]:
                ok = True
                db().execute("INSERT INTO unlocks (id,scan_id,code,created_at,ip) VALUES (?,?,?,?,?)",
                             (uuid.uuid4().hex, scan_id or None, code, now(), client_ip()))
                if scan_id:
                    db().execute("UPDATE scans SET unlocked=1 WHERE id=?", (scan_id,))
                days = result.get("days") or 0
                if days:
                    session["unlocked_until"] = (datetime.utcnow() + timedelta(days=days)).isoformat()
                db().commit()
                msg = "Unlocked. Your full report is open."
                if scan_id:
                    return redirect(url_for("report", scan_id=scan_id))
            else:
                msg = result["error"]
    return render_template("redeem.html", msg=msg, ok=ok, scan_id=request.args.get("scan_id", ""),
                           price=PRICE_FULL, price_month=PRICE_MONTH, support=SUPPORT_EMAIL)


def normalize_code(raw):
    """Make codes forgiving: any spacing or case, and accept a missing SCL- prefix.

    Only 12-hex-character inputs are rebuilt into the SCL- form, so Gumroad
    license keys (which are longer and mixed-case) pass through untouched.
    """
    compact = re.sub(r"[^A-Z0-9]", "", (raw or "").upper())
    body = compact[3:] if compact.startswith("SCL") else compact
    if re.fullmatch(r"[0-9A-F]{12}", body):
        return "SCL-" + "-".join(body[i:i + 4] for i in (0, 4, 8))
    return (raw or "").strip().upper().replace(" ", "")


def check_code(code):
    """Local codes first, then Gumroad license verification if configured."""
    code = normalize_code(code)
    row = db().execute("SELECT * FROM codes WHERE code=?", (code,)).fetchone()
    if row:
        if row["used_at"] and (row["scans_left"] or 0) <= 0:
            return {"ok": False, "error": "That code has already been used."}
        left = row["scans_left"]
        db().execute("UPDATE codes SET scans_left=?, used_at=?, used_by=? WHERE code=?",
                     ((left - 1) if left and left > 0 else 0, now(), session.get("consent_id"), code))
        db().commit()
        return {"ok": True, "days": row["days"] or 0}
    if GUMROAD_PRODUCT_ID:
        import urllib.error
        import urllib.parse
        import urllib.request
        try:
            data = urllib.parse.urlencode({"product_id": GUMROAD_PRODUCT_ID,
                                           "license_key": code,
                                           "increment_uses_count": "true"}).encode()
            req = urllib.request.Request("https://api.gumroad.com/v2/licenses/verify", data=data)
            with urllib.request.urlopen(req, timeout=20) as resp:
                d = json.load(resp)
            if d.get("success"):
                purchase = d.get("purchase") or {}
                if purchase.get("refunded") or purchase.get("chargebacked"):
                    return {"ok": False, "error": "That license was refunded."}
                return {"ok": True, "days": 365}
            return {"ok": False, "error": "That code is not valid. Check for typos — it looks like SCL-1234-5678-9ABC."}
        except urllib.error.HTTPError as e:
            # Gumroad answers 404 for a key it doesn't know — that is a typo, not an outage.
            if e.code == 404:
                return {"ok": False, "error": "That code is not valid. Check for typos — it looks like SCL-1234-5678-9ABC."}
            return {"ok": False, "error": "The license server didn't answer. Try again in a minute."}
        except Exception:
            return {"ok": False, "error": "The license server didn't answer. Try again in a minute."}
    return {"ok": False, "error": "That code is not valid. Check for typos — it looks like SCL-1234-5678-9ABC."}


@app.route("/terms")
def terms():
    return render_template("legal.html", kind="terms", support=SUPPORT_EMAIL,
                           waiver_version=WAIVER_VERSION)


@app.route("/privacy")
def privacy():
    return render_template("legal.html", kind="privacy", support=SUPPORT_EMAIL,
                           waiver_version=WAIVER_VERSION)


@app.route("/waiver-text")
def waiver_text():
    return render_template("legal.html", kind="waiver", support=SUPPORT_EMAIL,
                           waiver_version=WAIVER_VERSION)


def purge_old(days: int = 30):
    """Privacy policy promises 30-day deletion — actually do it."""
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat(timespec="seconds") + "Z"
    rows = db().execute("SELECT id, right_path, left_path FROM scans WHERE created_at < ?",
                        (cutoff,)).fetchall()
    for row in rows:
        for p in (row["right_path"], row["left_path"]):
            if p:
                try:
                    Path(p).unlink(missing_ok=True)
                except OSError:
                    pass
        db().execute("DELETE FROM scans WHERE id=?", (row["id"],))
    if rows:
        db().commit()
    return len(rows)


@app.route("/admin/purge")
def admin_purge():
    if not admin_authed():
        abort(401)
    return jsonify({"deleted_scans": purge_old()})


@app.route("/healthz")
def healthz():
    return jsonify({"ok": True, "vision_key": bool(vision.GEMINI_KEY), "t": now(),
                    "data_dir": str(DATA), "persistent": str(DATA) != str(ROOT / "data")})


# ------------------------------------------------------------------- admin ---
def admin_authed():
    key = request.args.get("key") or request.headers.get("X-Admin-Key") or ""
    if key and secrets.compare_digest(key, ADMIN_KEY):
        session["admin"] = True
        return True
    return bool(session.get("admin"))


@app.route("/admin")
def admin():
    if not admin_authed():
        return "admin key required: /admin?key=YOUR_KEY", 401
    scans = db().execute("SELECT id,created_at,unlocked,mode,ip,model FROM scans "
                         "ORDER BY created_at DESC LIMIT 100").fetchall()
    codes = db().execute("SELECT * FROM codes ORDER BY created_at DESC LIMIT 100").fetchall()
    consents = db().execute("SELECT COUNT(*) c FROM consents").fetchone()["c"]
    return render_template("admin.html", scans=scans, codes=codes, consents=consents,
                           vision_key=bool(vision.GEMINI_KEY), admin_key=ADMIN_KEY,
                           gumroad=GUMROAD_PRODUCT_ID or "(not configured)")


@app.route("/admin/make-codes", methods=["POST"])
def make_codes():
    if not admin_authed():
        abort(401)
    n = max(1, min(100, int(request.form.get("n") or 1)))
    scans_left = int(request.form.get("scans") or 5)
    days = int(request.form.get("days") or 0)
    note = (request.form.get("note") or "")[:120]
    made = []
    for _ in range(n):
        code = "SCL-" + "-".join(secrets.token_hex(2).upper() for _ in range(3))
        db().execute("INSERT INTO codes (code,kind,scans_left,days,created_at,note) VALUES (?,?,?,?,?,?)",
                     (code, "manual", scans_left, days, now(), note))
        made.append(code)
    db().commit()
    return redirect(url_for("admin"))


@app.route("/admin/make-gumroad-note")
def gumroad_note():
    if not admin_authed():
        abort(401)
    return jsonify({"product_id": GUMROAD_PRODUCT_ID,
                    "verify_endpoint": "https://api.gumroad.com/v2/licenses/verify"})


@app.errorhandler(413)
def too_large(_e):
    return jsonify({"error": "That upload was too big. Try a smaller photo."}), 413


init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("DEBUG") == "1")
