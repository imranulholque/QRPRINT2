import os, sys, io, uuid, time, hmac, shutil, tempfile, threading, atexit
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
    return {"id": f["id"], "name": f["name"], "kind": f["kind"], "pages": f["pages"]}


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
                d.close()
            if n < 1:
                raise ValueError("empty PDF")
            kind = "pdf"
        elif ext in IMG_EXT:
            with Image.open(path) as im:
                im.verify()
            kind, n = "img", 1
        else:
            raise ValueError("only PDF and image files are allowed")
    except Exception as e:
        try:
            os.remove(path)
        except Exception:
            pass
        return jsonify(error=f"{name}: {e}"), 400
    FILES[fid] = {"id": fid, "name": name, "kind": kind, "pages": n, "path": path}
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
    """Draw one page fitted (and centered) on a white sheet of W x H pixels."""
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


@app.get("/api/thumb")
def thumb():
    f, p = get_page()
    if not f:
        return "", 404
    return send_img(compose(f, p, 240, 320, False, rotate=False))


@app.get("/api/preview")
def preview():
    f, p = get_page()
    if not f:
        return "", 404
    w = float(request.args.get("w", 210))
    h = float(request.args.get("h", 297))
    W = 640
    H = max(100, min(1400, int(W * h / w)))
    return send_img(compose(f, p, W, H, request.args.get("mono") == "1", rotate=True))


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
    jid = uuid.uuid4().hex[:8]
    JOBS[jid] = {"state": "run", "done": 0}
    mono = d.get("color") != "color"

    def run():
        try:
            printing.print_job(
                d["printer"], d.get("paper"), not mono, d.get("duplex", "off"), len(pages),
                lambda i, W, H, m: compose(pages[i][0], pages[i][1], W, H, m, True),
                lambda i: JOBS[jid].update(done=i),
                d.get("title", "Phone Print"))
            JOBS[jid]["state"] = "done"
        except Exception as e:
            JOBS[jid].update(state="error", error=str(e))

    threading.Thread(target=run, daemon=True).start()
    return jsonify(id=jid)


@app.get("/api/job/<jid>")
def job(jid):
    return jsonify(JOBS.get(jid, {"state": "error", "error": "unknown job"}))
