import os, sys, json, socket, threading
if sys.stdout is None: sys.stdout = open(os.devnull, "w")
if sys.stderr is None: sys.stderr = open(os.devnull, "w")
import logging
logging.getLogger("werkzeug").setLevel(logging.ERROR)

import tkinter as tk
from tkinter import messagebox
import qrcode
from PIL import Image, ImageDraw, ImageTk
from werkzeug.serving import make_server
import server

CFG_DIR = os.path.join(os.environ.get("APPDATA", "."), "PhonePrint")
CFG = os.path.join(CFG_DIR, "config.json")

W = 380
BG, CARD, INPUT = "#0b1020", "#151c33", "#0b1020"
FG, MUT, LINK = "#f1f5f9", "#8b95b0", "#22d3ee"
TEAL, TEAL_H = "#14b8a6", "#0d9488"
PURP, PURP_H = "#8b5cf6", "#7c3aed"
PINK, PINK_H = "#ec4899", "#db2777"
OK, ERR, IDLE = "#22c55e", "#ef4444", "#6b7280"
PILL = {OK: "#12301f", ERR: "#3a1519", IDLE: "#1f2638"}
FONT = "Segoe UI"
STOPS = [(99, 102, 241), (168, 85, 247), (236, 72, 153)]


def load_cfg():
    try:
        with open(CFG) as f:
            return json.load(f)
    except Exception:
        return {"pin": "", "port": 5050}


def save_cfg(c):
    os.makedirs(CFG_DIR, exist_ok=True)
    with open(CFG, "w") as f:
        json.dump(c, f)


def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def gradient(w, h):
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for x in range(w):
        t = x / max(1, w - 1) * (len(STOPS) - 1)
        i = min(int(t), len(STOPS) - 2)
        f = t - i
        a, b = STOPS[i], STOPS[i + 1]
        d.line((x, 0, x, h), fill=tuple(int(a[k] + (b[k] - a[k]) * f) for k in range(3)))
    return img


def rrect(c, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return c.create_polygon(pts, smooth=True, **kw)


class RBtn(tk.Canvas):
    def __init__(self, parent, text, cmd, color, hover, w=130, h=38, bg=BG, fs=10):
        super().__init__(parent, width=w, height=h, bg=bg, highlightthickness=0, cursor="hand2")
        self.color, self.hover, self.cmd = color, hover, cmd
        self.shape = rrect(self, 1, 1, w - 1, h - 1, 12, fill=color, outline="")
        self.label = self.create_text(w / 2, h / 2, text=text, fill="white", font=(FONT, fs, "bold"))
        self.bind("<Enter>", lambda e: self.itemconfig(self.shape, fill=self.hover))
        self.bind("<Leave>", lambda e: self.itemconfig(self.shape, fill=self.color))
        self.bind("<ButtonRelease-1>", lambda e: self.cmd())

    def set_text(self, t):
        self.itemconfig(self.label, text=t)


class App:
    def __init__(self):
        self.cfg = load_cfg()
        self.srv = None
        self.ip = local_ip()
        self.pin_shown = False
        self.settings_open = False

        r = self.root = tk.Tk()
        r.title("Phone Print")
        r.configure(bg=BG)
        r.resizable(False, False)

        # header
        self.head_img = ImageTk.PhotoImage(gradient(W, 110))
        hd = tk.Canvas(r, width=W, height=110, highlightthickness=0, bg=BG)
        hd.pack()
        hd.create_image(0, 0, image=self.head_img, anchor="nw")
        hd.create_text(W / 2, 44, text="🖨  Phone Print", fill="white", font=(FONT, 22, "bold"))
        hd.create_text(W / 2, 78, text="Print from your phone, instantly", fill="#f5e9ff", font=(FONT, 10))

        # status pill
        self.pill = tk.Canvas(r, width=130, height=30, bg=BG, highlightthickness=0)
        self.pill.pack(pady=(14, 8))
        self.pill_bg = rrect(self.pill, 1, 1, 129, 29, 14, fill=PILL[IDLE], outline="")
        self.pill_dot = self.pill.create_oval(16, 10, 26, 20, fill=IDLE, outline="")
        self.pill_txt = self.pill.create_text(80, 15, text="Stopped", fill=FG, font=(FONT, 10, "bold"))

        # QR
        self.qr = tk.Label(r, bg=BG, bd=0)
        self.qr.pack(pady=(2, 8))
        self.url = tk.Label(r, bg=BG, fg=LINK, font=(FONT, 13, "bold"))
        self.url.pack()
        tk.Label(r, text="Scan with your phone (same Wi-Fi)", bg=BG, fg=MUT, font=(FONT, 9)).pack(pady=(2, 12))

        # buttons
        row = tk.Frame(r, bg=BG)
        row.pack()
        self.copy_btn = RBtn(row, "Copy link", self.copy, TEAL, TEAL_H)
        self.copy_btn.pack(side="left", padx=5)
        self.set_btn = RBtn(row, "⚙  Settings", self.toggle_settings, PURP, PURP_H)
        self.set_btn.pack(side="left", padx=5)

        # settings (inside a slot so layout never jumps around)
        self.slot = tk.Frame(r, bg=BG)
        self.slot.pack(fill="x", padx=28, pady=(12, 18))
        self.panel = tk.Frame(self.slot, bg=CARD, padx=18, pady=16)

        vpin = (r.register(lambda s: s == "" or (s.isdigit() and len(s) <= 4)), "%P")
        vport = (r.register(lambda s: s == "" or (s.isdigit() and len(s) <= 5)), "%P")

        tk.Label(self.panel, text="4-DIGIT PIN", bg=CARD, fg=MUT, font=(FONT, 8, "bold")).grid(row=0, column=0, sticky="w")
        self.pin = tk.Entry(self.panel, width=8, show="●", font=(FONT, 15), justify="center",
                            bg=INPUT, fg=FG, insertbackground=FG, relief="flat",
                            highlightthickness=1, highlightbackground="#2a3358", highlightcolor=PURP,
                            validate="key", validatecommand=vpin)
        self.pin.grid(row=1, column=0, pady=(4, 12), ipady=5, sticky="w")
        self.pin.insert(0, self.cfg.get("pin", ""))
        self.show_btn = RBtn(self.panel, "Show", self.toggle_pin, "#2a3358", "#364070", w=70, h=34, bg=CARD, fs=9)
        self.show_btn.grid(row=1, column=1, padx=(10, 0), pady=(4, 12))

        tk.Label(self.panel, text="PORT", bg=CARD, fg=MUT, font=(FONT, 8, "bold")).grid(row=2, column=0, sticky="w")
        self.port = tk.Entry(self.panel, width=8, font=(FONT, 15), justify="center",
                             bg=INPUT, fg=FG, insertbackground=FG, relief="flat",
                             highlightthickness=1, highlightbackground="#2a3358", highlightcolor=PURP,
                             validate="key", validatecommand=vport)
        self.port.grid(row=3, column=0, pady=(4, 14), ipady=5, sticky="w")
        self.port.insert(0, str(self.cfg.get("port", 5050)))

        RBtn(self.panel, "Save & Start", self.start, PINK, PINK_H, w=290, h=40, bg=CARD, fs=11).grid(
            row=4, column=0, columnspan=2)

        self.draw()
        r.protocol("WM_DELETE_WINDOW", self.close)

        if self.cfg.get("pin"):
            self.start()
        else:
            self.toggle_settings()
        r.after(5000, self.tick)
        self.center()

    # ---------- helpers ----------
    def center(self):
        self.root.update_idletasks()
        w, h = self.root.winfo_width(), self.root.winfo_height()
        x = (self.root.winfo_screenwidth() - w) // 2
        y = max(10, (self.root.winfo_screenheight() - h) // 3)
        self.root.geometry(f"+{x}+{y}")

    def set_status(self, text, color):
        self.pill.itemconfig(self.pill_bg, fill=PILL[color])
        self.pill.itemconfig(self.pill_dot, fill=color)
        self.pill.itemconfig(self.pill_txt, text=text)

    def toggle_settings(self):
        self.settings_open = not self.settings_open
        if self.settings_open:
            self.panel.pack(fill="x")
            self.set_btn.set_text("✕  Close")
        else:
            self.panel.pack_forget()
            self.set_btn.set_text("⚙  Settings")
            self.pin_shown = True
            self.toggle_pin()
        self.center()

    def toggle_pin(self):
        self.pin_shown = not self.pin_shown
        self.pin.config(show="" if self.pin_shown else "●")
        self.show_btn.set_text("Hide" if self.pin_shown else "Show")

    def copy(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.link())
        self.copy_btn.set_text("Copied ✓")
        self.root.after(1500, lambda: self.copy_btn.set_text("Copy link"))

    def link(self):
        p = self.port.get().strip() or str(self.cfg.get("port", 5050))
        return f"http://{self.ip}:{p}"

    def draw(self):
        q = qrcode.QRCode(box_size=6, border=0)
        q.add_data(self.link())
        q.make(fit=True)
        qi = q.make_image(fill_color="black", back_color="white").get_image().convert("RGB")
        S, pad, bd = 3, 14, 4
        qw = qi.width
        size = qw + 2 * pad + 2 * bd
        big = size * S
        qi = qi.resize((qw * S, qw * S), Image.NEAREST)
        card = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        m1 = Image.new("L", (big, big), 0)
        ImageDraw.Draw(m1).rounded_rectangle((0, 0, big - 1, big - 1), radius=20 * S, fill=255)
        card.paste(gradient(big, big), (0, 0), m1)
        m2 = Image.new("L", (big, big), 0)
        ImageDraw.Draw(m2).rounded_rectangle((bd * S, bd * S, big - 1 - bd * S, big - 1 - bd * S),
                                             radius=17 * S, fill=255)
        card.paste(Image.new("RGB", (big, big), "white"), (0, 0), m2)
        card.paste(qi, ((bd + pad) * S, (bd + pad) * S))
        card = card.resize((size, size), Image.LANCZOS)
        self.photo = ImageTk.PhotoImage(card)
        self.qr.config(image=self.photo)
        self.url.config(text=self.link())

    def tick(self):
        ip = local_ip()
        if ip != self.ip:
            self.ip = ip
            self.draw()
        self.root.after(5000, self.tick)

    # ---------- server ----------
    def stop(self):
        if self.srv:
            try:
                self.srv.shutdown()
                self.srv.server_close()
            except Exception:
                pass
            self.srv = None
        self.set_status("Stopped", IDLE)

    def start(self):
        pin = self.pin.get().strip()
        port = self.port.get().strip()
        if not (pin.isdigit() and len(pin) == 4):
            messagebox.showerror("PIN", "PIN must be exactly 4 digits.")
            return
        if not (port.isdigit() and 1024 <= int(port) <= 65535):
            messagebox.showerror("Port", "Port must be a number between 1024 and 65535.")
            return
        port = int(port)
        self.stop()
        try:
            self.srv = make_server("0.0.0.0", port, server.app, threaded=True)
        except OSError:
            self.set_status("Port busy", ERR)
            if not self.settings_open:
                self.toggle_settings()
            messagebox.showerror("Port busy", f"Port {port} is already used by another program.\nChoose a different port.")
            return
        server.state["pin"] = pin
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.cfg = {"pin": pin, "port": port}
        save_cfg(self.cfg)
        self.draw()
        self.set_status("Running", OK)
        if self.settings_open:
            self.toggle_settings()
        self.center()

    def close(self):
        self.stop()
        server.cleanup()
        self.root.destroy()


if __name__ == "__main__":
    try:
        App().root.mainloop()
    except Exception as e:
        try:
            messagebox.showerror("Error", str(e))
        except Exception:
            pass
