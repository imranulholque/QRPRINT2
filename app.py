import os, sys, json, socket, threading
if sys.stdout is None: sys.stdout = open(os.devnull, "w")
if sys.stderr is None: sys.stderr = open(os.devnull, "w")
import logging
logging.getLogger("werkzeug").setLevel(logging.ERROR)

import tkinter as tk
from tkinter import messagebox
import qrcode
from PIL import ImageTk
from werkzeug.serving import make_server
import server

CFG_DIR = os.path.join(os.environ.get("APPDATA", "."), "PhonePrint")
CFG = os.path.join(CFG_DIR, "config.json")

BG, CARD, FG, MUT = "#0f1419", "#1a212b", "#e8eaed", "#8b95a3"
PRI, PRI_H = "#2563eb", "#1d4ed8"
OK, ERR, IDLE = "#22c55e", "#ef4444", "#6b7280"
FONT = "Segoe UI"


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


def button(parent, text, cmd, primary=False, **kw):
    bg = PRI if primary else CARD
    hv = PRI_H if primary else "#242d3a"
    b = tk.Button(parent, text=text, command=cmd, bg=bg, fg=FG, activebackground=hv,
                  activeforeground=FG, relief="flat", bd=0, cursor="hand2",
                  font=(FONT, 10, "bold" if primary else "normal"), padx=14, pady=7, **kw)
    b.bind("<Enter>", lambda e: b.config(bg=hv))
    b.bind("<Leave>", lambda e: b.config(bg=bg))
    return b


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

        tk.Label(r, text="Phone Print", bg=BG, fg=FG, font=(FONT, 16, "bold")).pack(pady=(18, 2))

        st = tk.Frame(r, bg=BG)
        st.pack()
        self.dot = tk.Canvas(st, width=12, height=12, bg=BG, highlightthickness=0)
        self.dot.pack(side="left", padx=(0, 6))
        self.dot_id = self.dot.create_oval(1, 1, 11, 11, fill=IDLE, outline="")
        self.status = tk.Label(st, text="Stopped", bg=BG, fg=MUT, font=(FONT, 10))
        self.status.pack(side="left")

        frame = tk.Frame(r, bg="white", padx=10, pady=10)
        frame.pack(padx=30, pady=(14, 8))
        self.qr = tk.Label(frame, bg="white")
        self.qr.pack()

        self.url = tk.Label(r, bg=BG, fg=FG, font=(FONT, 12, "bold"))
        self.url.pack()
        tk.Label(r, text="Scan with your phone (same Wi-Fi)", bg=BG, fg=MUT,
                 font=(FONT, 9)).pack(pady=(2, 10))

        row = tk.Frame(r, bg=BG)
        row.pack(pady=(0, 6))
        self.copy_btn = button(row, "Copy link", self.copy)
        self.copy_btn.pack(side="left", padx=4)
        self.set_btn = button(row, "⚙ Settings", self.toggle_settings)
        self.set_btn.pack(side="left", padx=4)

        # settings panel (hidden unless needed)
        self.panel = tk.Frame(r, bg=CARD, padx=16, pady=14)
        vcmd = (r.register(lambda s: s == "" or (s.isdigit() and len(s) <= 4)), "%P")
        vport = (r.register(lambda s: s == "" or (s.isdigit() and len(s) <= 5)), "%P")

        tk.Label(self.panel, text="4-digit PIN", bg=CARD, fg=MUT, font=(FONT, 9)).grid(row=0, column=0, sticky="w")
        self.pin = tk.Entry(self.panel, width=8, show="●", font=(FONT, 14), justify="center",
                            bg=BG, fg=FG, insertbackground=FG, relief="flat",
                            validate="key", validatecommand=vcmd)
        self.pin.grid(row=1, column=0, pady=(2, 10), ipady=4, sticky="w")
        self.pin.insert(0, self.cfg.get("pin", ""))
        self.show_btn = button(self.panel, "Show", self.toggle_pin)
        self.show_btn.grid(row=1, column=1, padx=(8, 0), pady=(2, 10))

        tk.Label(self.panel, text="Port", bg=CARD, fg=MUT, font=(FONT, 9)).grid(row=2, column=0, sticky="w")
        self.port = tk.Entry(self.panel, width=8, font=(FONT, 14), justify="center",
                             bg=BG, fg=FG, insertbackground=FG, relief="flat",
                             validate="key", validatecommand=vport)
        self.port.grid(row=3, column=0, pady=(2, 12), ipady=4, sticky="w")
        self.port.insert(0, str(self.cfg.get("port", 5050)))

        button(self.panel, "Save & Start", self.start, primary=True).grid(row=4, column=0, columnspan=2, sticky="ew")

        tk.Frame(r, bg=BG, height=14).pack()
        self.draw()
        r.protocol("WM_DELETE_WINDOW", self.close)

        if self.cfg.get("pin"):
            self.start()
        else:
            self.toggle_settings()
            self.pin_shown = False
        r.after(5000, self.tick)
        self.center()

    # ---------- helpers ----------
    def center(self):
        self.root.update_idletasks()
        w, h = self.root.winfo_width(), self.root.winfo_height()
        x = (self.root.winfo_screenwidth() - w) // 2
        y = (self.root.winfo_screenheight() - h) // 3
        self.root.geometry(f"+{x}+{y}")

    def set_status(self, text, color):
        self.dot.itemconfig(self.dot_id, fill=color)
        self.status.config(text=text, fg=FG if color == OK else MUT)

    def toggle_settings(self):
        self.settings_open = not self.settings_open
        if self.settings_open:
            self.panel.pack(padx=30, pady=(4, 0), before=self.panel.master.pack_slaves()[-1])
            self.set_btn.config(text="✕ Close")
        else:
            self.panel.pack_forget()
            self.set_btn.config(text="⚙ Settings")
            self.pin_shown = True
            self.toggle_pin()

    def toggle_pin(self):
        self.pin_shown = not self.pin_shown
        self.pin.config(show="" if self.pin_shown else "●")
        self.show_btn.config(text="Hide" if self.pin_shown else "Show")

    def copy(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.link())
        self.copy_btn.config(text="Copied ✓")
        self.root.after(1500, lambda: self.copy_btn.config(text="Copy link"))

    def link(self):
        p = self.port.get().strip() or str(self.cfg.get("port", 5050))
        return f"http://{self.ip}:{p}"

    def draw(self):
        q = qrcode.QRCode(box_size=7, border=1)
        q.add_data(self.link())
        q.make(fit=True)
        img = q.make_image(fill_color="black", back_color="white").get_image().convert("RGB")
        self.photo = ImageTk.PhotoImage(img)
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
