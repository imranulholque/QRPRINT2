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


class App:
    def __init__(self):
        self.cfg = load_cfg()
        self.srv = None
        self.ip = local_ip()
        r = self.root = tk.Tk()
        r.title("Phone Print")
        r.resizable(False, False)
        self.qr = tk.Label(r)
        self.qr.pack(padx=20, pady=(20, 5))
        self.url = tk.Label(r, font=("Segoe UI", 12, "bold"))
        self.url.pack()
        f = tk.Frame(r)
        f.pack(pady=10)
        tk.Label(f, text="4-digit PIN").grid(row=0, column=0, sticky="e", padx=5, pady=3)
        self.pin = tk.Entry(f, width=8, font=("Segoe UI", 14), justify="center")
        self.pin.grid(row=0, column=1)
        self.pin.insert(0, self.cfg.get("pin", ""))
        tk.Label(f, text="Port").grid(row=1, column=0, sticky="e", padx=5, pady=3)
        self.port = tk.Entry(f, width=8, font=("Segoe UI", 14), justify="center")
        self.port.grid(row=1, column=1)
        self.port.insert(0, str(self.cfg.get("port", 5050)))
        tk.Button(r, text="Save & Start", font=("Segoe UI", 12), command=self.start).pack(pady=5)
        self.status = tk.Label(r, text="Set a PIN, then press Save & Start", fg="gray")
        self.status.pack(pady=(0, 15))
        self.draw()
        r.protocol("WM_DELETE_WINDOW", self.close)
        if self.cfg.get("pin"):
            self.start()
        r.after(5000, self.tick)

    def link(self):
        p = self.port.get().strip() or "5050"
        return f"http://{self.ip}:{p}"

    def draw(self):
        q = qrcode.QRCode(box_size=8, border=2)
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

    def stop(self):
        if self.srv:
            try:
                self.srv.shutdown()
                self.srv.server_close()
            except Exception:
                pass
            self.srv = None

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
            self.status.config(text="Port is busy", fg="red")
            messagebox.showerror("Port busy", f"Port {port} is already used by another program. Choose a different port.")
            return
        server.state["pin"] = pin
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        save_cfg({"pin": pin, "port": port})
        self.draw()
        self.status.config(text="Running. Scan with phone (same Wi-Fi)", fg="green")

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
