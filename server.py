import os, sys, io, uuid, time, hmac, json, shutil, tempfile, threading, atexit
from flask import Flask, request, jsonify, session, send_file
import fitz
from PIL import Image, ImageOps
import printing


def res(p):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, p)


app = Flask(__name__, static_folder=None)
app.secret_key = os.urandom(24)
state = {"pin": "0000"}
UP = tempfile.mkdtemp(prefix="phoneprint_")
FILES = {}
ORDER = []
JOBS = {}
LOCK = threading.Lock()
fails = {"n": 0, "until": 0}
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}

# ---- printer profiles (saved by this app only, never written to Windows printer settings)
PROF_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "PhonePrint")
PROF_FILE = os.path.join(PROF_DIR, "profiles.json")
PLOCK = threading.Lock()
DIALOG_LOCK = threading.Lock()  # only one driver window at a time


def load_profiles():
    try:
        with open(PROF_FILE, "r", encoding="utf-8") as fh:
            d = json.load(fh)
            return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_profiles(d):
    os.makedirs(PROF_DIR, exist_ok=True)
    tmp = PROF_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh)
    os.replace(tmp, PROF_FILE)


def find_profile(printer, pid):
    if not pid:
        return None
    with PLOCK:
        for p in load_profiles().get(printer, []):
            if p["id"] == pid:
                return p["data"]
    return None


def cleanup():
    shutil.rmtree(UP, ignore_errors=True)


atexit.register(cleanup)


@app.before_request
def guard():
    if request.path.startswith("/api/") and request.path != "/api/login" and not session.get("ok"):
        return jsonify(error="login"), 401


@app.get("/")
def index():
    return send_file(res("static/index.html"))


@app.post("/api/login")
def login():
    now = time.time()
    if now < fails["until"]:
        return jsonify(error="Too many tries. Wait 30 seconds."), 429
    pin = str((request.get_json(silent=True) or {}).get("pin", ""))
    if hmac.compare_digest(pin, state["pin"]):
        session["ok"] = True
        fails["n"] = 0
        return jsonify(ok=True)
    fails["n"] += 1
    if fails["n"] >= 5:
        fails["n"] = 0
        fails["until"] = now + 30
    return jsonify(error="Wrong PIN"), 403


def pub(f):
    return {"id": f["id"], "name": f["name"], "kind": f["kind"], "pages": f["pages"], "lands": f["lands"]}


@app.get("/api/files")
def files():
    return jsonify([pub(FILES[i]) for i in ORDER if i in FILES])


@app.post("/api/upload")
def upload():
    up = request.files.get("file")
    if not up:
        return jsonify(error="No file"), 400
    name = up.filename or "file"
    ext = os.path.splitext(name)[1].lower()
    fid = uuid.uuid4().hex[:10]
    path = os.path.join(UP, fid + ext)
    up.save(path)
    try:
        if ext == ".pdf":
            with LOCK:
                d = fitz.open(path)
                n = d.page_count
                lands = []
                for i in range(n):
                    r = d.load_page(i).rect
                    lands.append(r.width > r.height)
                d.close()
            if n < 1:
                raise ValueError("empty PDF")
            kind = "pdf"
        elif ext in IMG_EXT:
            with Image.open(path) as im:
                im.verify()
            with Image.open(path) as im:
                w, h = im.size
                if im.getexif().get(274, 1) in (5, 6, 7, 8):
                    w, h = h, w
            kind, n, lands = "img", 1, [w > h]
        else:
            raise ValueError("only PDF and image files are allowed")
    except Exception as e:
        try:
            os.remove(path)
        except Exception:
            pass
        return jsonify(error=f"{name}: {e}"), 400
    FILES[fid] = {"id": fid, "name": name, "kind": kind, "pages": n, "lands": lands, "path": path}
    ORDER.append(fid)
    return jsonify(pub(FILES[fid]))


@app.delete("/api/files/<fid>")
def del_one(fid):
    f = FILES.pop(fid, None)
    if f:
        ORDER.remove(fid)
        try:
            os.remove(f["path"])
        except Exception:
            pass
    return jsonify(ok=True)


@app.delete("/api/files")
def del_all():
    for fid in list(ORDER):
        del_one(fid)
    return jsonify(ok=True)


def compose(f, idx, W, H, mono, rotate=True):
    """Draw one page, fitted and centered, on a white sheet of W x H pixels."""
    if f["kind"] == "pdf":
        with LOCK:
            doc = fitz.open(f["path"])
            try:
                pg = doc[idx]
                pw, ph = pg.rect.width, pg.rect.height
                rot = rotate and ((pw > ph) != (W > H))
                fw, fh = (ph, pw) if rot else (pw, ph)
                s = min(W / fw, H / fh)
                pix = pg.get_pixmap(matrix=fitz.Matrix(s, s),
                                    colorspace=fitz.csGRAY if mono else fitz.csRGB, alpha=False)
                img = Image.frombytes("L" if mono else "RGB", (pix.width, pix.height), pix.samples)
            finally:
                doc.close()
    else:
        img = Image.open(f["path"])
        img = ImageOps.exif_transpose(img)
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            bg = Image.new("RGB", img.size, "white")
            bg.paste(img, mask=img.split()[3])
            img = bg
        else:
            img = img.convert("RGB")
        iw, ih = img.size
        rot = rotate and ((iw > ih) != (W > H))
        fw, fh = (ih, iw) if rot else (iw, ih)
        s = min(W / fw, H / fh)
        img = img.resize((max(1, int(iw * s)), max(1, int(ih * s))), Image.LANCZOS)
        if mono:
            img = img.convert("L")
    if rot:
        img = img.rotate(90, expand=True)
    canvas = Image.new(img.mode, (W, H), 255 if img.mode == "L" else (255, 255, 255))
    canvas.paste(img, ((W - img.width) // 2, (H - img.height) // 2))
    return canvas


def get_page():
    f = FILES.get(request.args.get("f", ""))
    p = int(request.args.get("p", 0))
    if not f or not (0 <= p < f["pages"]):
        return None, None
    return f, p


def send_img(img):
    b = io.BytesIO()
    img.save(b, "JPEG", quality=85)
    b.seek(0)
    r = send_file(b, mimetype="image/jpeg")
    r.headers["Cache-Control"] = "no-store"
    return r


def get_area(q):
    """Paper + printable area from the real driver; falls back to plain paper size."""
    orient = q.get("orient", "portrait")
    pw = float(q.get("w", 210))
    ph = float(q.get("h", 297))
    if (orient == "landscape") != (pw > ph):
        pw, ph = ph, pw
    a = {"pw": pw, "ph": ph, "ox": 0, "oy": 0, "aw": pw, "ah": ph, "driver": False}
    try:
        a = printing.area(q.get("printer", ""), q.get("paper"), q.get("color") == "color",
                          q.get("duplex", "off"), orient)
        a["driver"] = True
    except Exception:
        pass
    return a


@app.get("/api/thumb")
def thumb():
    f, p = get_page()
    if not f:
        return "", 404
    return send_img(compose(f, p, 240, 320, False, rotate=False))


@app.get("/api/area")
def area():
    return jsonify(get_area(request.args))


@app.get("/api/preview")
def preview():
    f, p = get_page()
    if not f:
        return "", 404
    a = get_area(request.args)
    W = 560
    s = W / a["pw"]
    H = max(100, int(a["ph"] * s))
    ox, oy = int(a["ox"] * s), int(a["oy"] * s)
    AW = max(1, min(W - ox, int(a["aw"] * s)))
    AH = max(1, min(H - oy, int(a["ah"] * s)))
    mono = request.args.get("color") != "color"
    page = compose(f, p, AW, AH, mono, rotate=True).convert("RGB")
    sheet = Image.new("RGB", (W, H), (214, 214, 214))  # gray = area the printer cannot print on
    sheet.paste(page, (ox, oy))
    return send_img(sheet)


@app.get("/api/printers")
def printers():
    try:
        return jsonify(printing.list_printers())
    except Exception as e:
        return jsonify(error=str(e)), 500


@app.get("/api/printer")
def printer():
    try:
        return jsonify(printing.caps(request.args.get("name", "")))
    except Exception as e:
        return jsonify(error=str(e)), 500


# ------------------------------------------------------------------ profiles
@app.get("/api/profiles")
def profiles_list():
    name = request.args.get("printer", "")
    with PLOCK:
        items = load_profiles().get(name, [])
    return jsonify([{"id": p["id"], "name": p["name"]} for p in items])


@app.post("/api/profiles/new")
def profiles_new():
    """Opens the driver's own settings window on the PC (like Word's Printer Properties).
    Returns a job id; poll /api/job/<id>. State 'done' = saved, 'cancel' = user pressed Cancel."""
    d = request.get_json(silent=True) or {}
    printer_name = str(d.get("printer", ""))
    pname = str(d.get("name", "")).strip()[:40]
    if not printer_name or not pname:
        return jsonify(error="Printer and profile name are required"), 400
    if DIALOG_LOCK.locked():
        return jsonify(error="A settings window is already open on the PC"), 409
    base = find_profile(printer_name, d.get("base"))
    jid = uuid.uuid4().hex[:8]
    JOBS[jid] = {"state": "run", "kind": "profile"}

    def run():
        with DIALOG_LOCK:
            try:
                data = printing.driver_dialog(printer_name, base)
                if data is None:
                    JOBS[jid]["state"] = "cancel"
                    return
                pid = uuid.uuid4().hex[:8]
                with PLOCK:
                    allp = load_profiles()
                    allp.setdefault(printer_name, []).append({"id": pid, "name": pname, "data": data})
                    save_profiles(allp)
                JOBS[jid].update(state="done", profile={"id": pid, "name": pname})
            except Exception as e:
                JOBS[jid].update(state="error", error=str(e))

    threading.Thread(target=run, daemon=True).start()
    return jsonify(id=jid)


@app.delete("/api/profiles/<pid>")
def profiles_delete(pid):
    name = request.args.get("printer", "")
    with PLOCK:
        allp = load_profiles()
        allp[name] = [p for p in allp.get(name, []) if p["id"] != pid]
        save_profiles(allp)
    return jsonify(ok=True)


@app.post("/api/print")
def do_print():
    d = request.get_json(silent=True) or {}
    try:
        pages = [(FILES[f], int(p)) for f, p in d["pages"]]
    except Exception:
        return jsonify(error="Files not found. Upload again."), 400
    if not pages:
        return jsonify(error="No pages selected"), 400
    if len({f["kind"] for f, _ in pages}) != 1:
        return jsonify(error="Do not mix PDF and images in one print."), 400
    try:
        copies = max(1, min(99, int(d.get("copies", 1))))
    except Exception:
        copies = 1
    orient = "landscape" if d.get("orient") == "landscape" else "portrait"
    jid = uuid.uuid4().hex[:8]
    JOBS[jid] = {"state": "run", "done": 0, "total": len(pages) * copies}
    color = d.get("color") == "color"
    profile = find_profile(d.get("printer", ""), d.get("profile"))  # None = normal way

    def run():
        try:
            printing.print_job(
                d["printer"], d.get("paper"), color, d.get("duplex", "off"), orient, copies, len(pages),
                lambda i, W, H, m: compose(pages[i][0], pages[i][1], W, H, m, True),
                lambda done, total: JOBS[jid].update(done=done, total=total),
                d.get("title", "Phone Print"), profile=profile)
            JOBS[jid]["state"] = "done"
        except Exception as e:
            JOBS[jid].update(state="error", error=str(e))

    threading.Thread(target=run, daemon=True).start()
    return jsonify(id=jid)


@app.get("/api/job/<jid>")
def job(jid):
    return jsonify(JOBS.get(jid, {"state": "error", "error": "unknown job"}))
