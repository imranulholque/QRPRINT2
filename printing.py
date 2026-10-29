import win32print, win32ui, win32gui
from PIL import ImageWin

DM_PAPERSIZE, DM_PAPERLENGTH, DM_PAPERWIDTH = 0x2, 0x4, 0x8
DM_COLOR, DM_DUPLEX, DM_FORMNAME = 0x800, 0x1000, 0x10000
DC_PAPERS, DC_PAPERSIZE, DC_DUPLEX, DC_PAPERNAMES, DC_COLORDEVICE = 2, 3, 7, 16, 32
HORZRES, VERTRES, LOGPIXELSX = 8, 10, 88


def list_printers():
    default = None
    try:
        default = win32print.GetDefaultPrinter()
    except Exception:
        pass
    flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    out = []
    for p in win32print.EnumPrinters(flags, None, 2):
        port = p.get("pPortName", "") or ""
        out.append({"name": p["pPrinterName"], "port": port,
                    "usb": port.upper().startswith("USB"),
                    "default": p["pPrinterName"] == default})
    out.sort(key=lambda x: (not x["usb"], x["name"].lower()))
    return out


def caps(name):
    h = win32print.OpenPrinter(name)
    try:
        info = win32print.GetPrinter(h, 2)
        try:
            forms = win32print.EnumForms(h)
        except Exception:
            forms = []
    finally:
        win32print.ClosePrinter(h)
    port = info.get("pPortName")
    dm = info.get("pDevMode")

    def cap(c):
        try:
            return win32print.DeviceCapabilities(name, port, c)
        except Exception:
            return None

    names = [str(n).strip("\x00").strip() for n in (cap(DC_PAPERNAMES) or [])]
    ids = cap(DC_PAPERS) or []
    sizes = cap(DC_PAPERSIZE) or []
    papers, seen = [], set()
    for i, n in enumerate(names):
        if not n or n in seen:
            continue
        seen.add(n)
        w = hh = None
        try:
            w, hh = sizes[i][0] / 10, sizes[i][1] / 10
        except Exception:
            pass
        papers.append({"name": n, "id": int(ids[i]) if i < len(ids) else None,
                       "w": w, "h": hh, "custom": False})
    # custom paper forms added by the user in Windows / driver
    for fm in forms:
        n = fm.get("Name")
        if n and n not in seen and fm.get("Flags", 1) in (0, 2):
            sz = fm.get("Size") or {}
            papers.append({"name": n, "id": None,
                           "w": (sz.get("cx") or 0) / 1000 or None,
                           "h": (sz.get("cy") or 0) / 1000 or None, "custom": True})
            seen.add(n)

    d = {"paper": None, "color": True, "duplex": "off"}
    if dm is not None:
        try:
            pid = dm.PaperSize
            for p in papers:
                if p["id"] == pid:
                    d["paper"] = p["name"]
            if pid == 256:  # driver has a user-defined custom size set
                pw, pl = int(dm.PaperWidth), int(dm.PaperLength)
                nm = f"Custom {pw/10:g} x {pl/10:g} mm"
                papers.append({"name": nm, "id": 256, "pw": pw, "pl": pl,
                               "w": pw / 10, "h": pl / 10, "custom": True})
                d["paper"] = nm
            elif d["paper"] is None:
                fn = (dm.FormName or "").strip("\x00")
                if fn:
                    if fn not in seen:
                        papers.append({"name": fn, "id": None, "w": None, "h": None, "custom": True})
                    d["paper"] = fn
        except Exception:
            pass
        try:
            d["color"] = dm.Color != 1
            d["duplex"] = {2: "long", 3: "short"}.get(dm.Duplex, "off")
        except Exception:
            pass
    return {"papers": papers, "color": cap(DC_COLORDEVICE) == 1,
            "duplex": cap(DC_DUPLEX) == 1, "defaults": d}


def print_job(name, paper, color, duplex, n, provider, progress, title="Phone Print"):
    c = caps(name)
    h = win32print.OpenPrinter(name)
    try:
        dm = win32print.GetPrinter(h, 2).get("pDevMode")
        if dm is None:
            dm = win32print.DocumentProperties(0, h, name, None, None, 2)
    finally:
        win32print.ClosePrinter(h)

    p = next((x for x in c["papers"] if x["name"] == paper), None)
    if p:
        if p.get("pw"):
            dm.PaperSize, dm.PaperWidth, dm.PaperLength = 256, p["pw"], p["pl"]
            dm.Fields |= DM_PAPERSIZE | DM_PAPERWIDTH | DM_PAPERLENGTH
        elif p["id"]:
            dm.PaperSize = p["id"]
            dm.Fields = (dm.Fields | DM_PAPERSIZE) & ~DM_FORMNAME
        else:
            dm.FormName = p["name"]
            dm.Fields = (dm.Fields | DM_FORMNAME) & ~DM_PAPERSIZE
    if c["color"]:
        dm.Color = 2 if color else 1
        dm.Fields |= DM_COLOR
    else:
        color = False
    if c["duplex"]:
        dm.Duplex = {"long": 2, "short": 3}.get(duplex, 1)
        dm.Fields |= DM_DUPLEX

    hdc = win32ui.CreateDCFromHandle(win32gui.CreateDC("WINSPOOL", name, dm))
    try:
        W, H = hdc.GetDeviceCaps(HORZRES), hdc.GetDeviceCaps(VERTRES)
        dpi = hdc.GetDeviceCaps(LOGPIXELSX) or 300
        f = min(1.0, 300.0 / dpi)
        rw, rh = max(1, int(W * f)), max(1, int(H * f))
        hdc.StartDoc(title)
        for i in range(n):
            img = provider(i, rw, rh, not color)
            hdc.StartPage()
            ImageWin.Dib(img).draw(hdc.GetHandleOutput(), (0, 0, W, H))
            hdc.EndPage()
            progress(i + 1)
        hdc.EndDoc()
    except Exception:
        try:
            hdc.AbortDoc()
        except Exception:
            pass
        raise
    finally:
        hdc.DeleteDC()
