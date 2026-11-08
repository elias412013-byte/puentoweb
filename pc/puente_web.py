# -*- coding: utf-8 -*-
"""
PuenteWeb - lado PC (Windows)

La PC abre Edge o Chrome en segundo plano, carga las paginas y le manda al iPad
solo la imagen. El iPad manda de vuelta toques, teclado y botones (atras, adelante...).
Asi un iPad viejo puede navegar paginas modernas, porque el trabajo lo hace la PC.

Uso (cmd, en esta carpeta):
    py puente_web.py

Opciones:
    py puente_web.py --inicio https://www.youtube.com    pagina de inicio
    py puente_web.py --ventana                           muestra la ventana del navegador (para probar)
    py puente_web.py --escala 2                          mas nitido (usa mas datos)
    py puente_web.py --calidad 60                        calidad de imagen 1-100 (menos = mas rapido)
    py puente_web.py --ip 192.168.1.30                   conectar por Wi-Fi (IP que muestra el iPad)

Requisitos: Edge o Chrome instalado, y para el cable USB: iTunes (o "Dispositivos Apple").
No hace falta instalar nada con pip.
"""
import argparse
import atexit
import base64
import json
import os
import plistlib
import re
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

parser = argparse.ArgumentParser(description="PuenteWeb - lado PC")
parser.add_argument("--inicio", default="https://www.google.com", help="pagina de inicio")
parser.add_argument("--ventana", action="store_true", help="mostrar la ventana del navegador")
parser.add_argument("--escala", type=int, default=1, choices=[1, 2], help="1 normal, 2 mas nitido")
parser.add_argument("--calidad", type=int, default=70, help="calidad JPEG 1-100 (default 70)")
parser.add_argument("--buscador", default="google", choices=["google", "duckduckgo", "bing"])
parser.add_argument("--navegador", default=None, help="ruta de msedge.exe o chrome.exe")
parser.add_argument("--puerto", type=int, default=7777, help="puerto de la app del iPad")
parser.add_argument("--ip", default=None, help="IP del iPad para conectar por Wi-Fi (en vez de USB)")
parser.add_argument("--puerto-depuracion", type=int, default=9222)
args = parser.parse_args()

if os.name != "nt":
    print("Este script es para Windows.")
    sys.exit(1)

UA_ESCRITORIO = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                 "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
UA_MOVIL = ("Mozilla/5.0 (Linux; Android 12; Pixel 6) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36")


# ------------------------------------------------------------ navegador
def buscar_navegador():
    if args.navegador and os.path.exists(args.navegador):
        return args.navegador
    candidatos = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ]
    for c in candidatos:
        if os.path.exists(c):
            return c
    return None


NAVEGADOR = buscar_navegador()
if not NAVEGADOR:
    print("No encontre Edge ni Chrome. Instala uno o usa --navegador RUTA")
    input("Apreta Enter para salir")
    sys.exit(1)

PERFIL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "perfil_navegador")
proc_navegador = None


def abrir_navegador():
    global proc_navegador
    cmd = [NAVEGADOR,
           "--remote-debugging-port=%d" % args.puerto_depuracion,
           "--remote-allow-origins=*",
           "--user-data-dir=" + PERFIL,
           "--no-first-run", "--no-default-browser-check",
           "--mute-audio", "--hide-scrollbars",
           "--disable-background-timer-throttling",
           "--disable-renderer-backgrounding",
           "--disable-backgrounding-occluded-windows"]
    if not args.ventana:
        cmd.append("--headless=new")
    cmd.append("about:blank")
    proc_navegador = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def cerrar_navegador():
    try:
        if proc_navegador:
            proc_navegador.kill()
    except Exception:
        pass


atexit.register(cerrar_navegador)


def esperar_pagina():
    abridor = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(80):
        try:
            with abridor.open("http://127.0.0.1:%d/json" % args.puerto_depuracion, timeout=1) as r:
                paginas = [t for t in json.loads(r.read().decode("utf-8")) if t.get("type") == "page"]
                if paginas:
                    return paginas[0]["webSocketDebuggerUrl"]
        except Exception:
            pass
        time.sleep(0.5)
    return None


# ------------------------------------------------------------ WebSocket minimo (sin librerias)
class WS:
    def __init__(self, url):
        m = re.match(r"ws://([^/:]+):(\d+)(/.*)", url)
        if not m:
            raise ValueError("URL de WebSocket rara: " + url)
        host, port, path = m.group(1), int(m.group(2)), m.group(3)
        self.sock = socket.create_connection((host, port), timeout=5)
        clave = base64.b64encode(os.urandom(16)).decode()
        pedido = ("GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                  "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % (path, host, port, clave))
        self.sock.sendall(pedido.encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            trozo = self.sock.recv(4096)
            if not trozo:
                raise ConnectionError("el navegador cerro la conexion")
            resp += trozo
        cabecera, resto = resp.split(b"\r\n\r\n", 1)
        if b" 101 " not in cabecera.split(b"\r\n")[0]:
            raise ConnectionError("el navegador rechazo la conexion: " + cabecera.decode("latin-1")[:80])
        self.buf = resto
        self.sock.settimeout(None)
        self.lock = threading.Lock()

    def _marco(self, opcode, datos):
        cab = bytearray([0x80 | opcode])
        n = len(datos)
        if n < 126:
            cab.append(0x80 | n)
        elif n < 65536:
            cab.append(0x80 | 126)
            cab += struct.pack(">H", n)
        else:
            cab.append(0x80 | 127)
            cab += struct.pack(">Q", n)
        mascara = os.urandom(4)
        cab += mascara
        enmascarado = bytes(b ^ mascara[i % 4] for i, b in enumerate(datos))
        return bytes(cab) + enmascarado

    def enviar_texto(self, texto):
        marco = self._marco(0x1, texto.encode("utf-8"))
        with self.lock:
            self.sock.sendall(marco)

    def _leer(self, n):
        while len(self.buf) < n:
            trozo = self.sock.recv(65536)
            if not trozo:
                raise ConnectionError("conexion cerrada")
            self.buf += trozo
        datos = self.buf[:n]
        self.buf = self.buf[n:]
        return datos

    def recibir_mensaje(self):
        acumulado = b""
        while True:
            b1, b2 = self._leer(2)
            fin = b1 & 0x80
            opcode = b1 & 0x0F
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._leer(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._leer(8))[0]
            if b2 & 0x80:
                self._leer(4)
            datos = self._leer(n)
            if opcode == 0x8:
                return None
            if opcode == 0x9:
                with self.lock:
                    self.sock.sendall(self._marco(0xA, datos))
                continue
            if opcode == 0xA:
                continue
            acumulado += datos
            if fin:
                return acumulado.decode("utf-8", "replace")


class CDP:
    def __init__(self, url, al_evento):
        self.ws = WS(url)
        self.al_evento = al_evento
        self.siguiente = 1
        self.pend = {}
        self.cerrojo = threading.Lock()
        threading.Thread(target=self._bucle, daemon=True).start()

    def llamar(self, metodo, params=None, esperar=False, timeout=5):
        with self.cerrojo:
            i = self.siguiente
            self.siguiente += 1
        msg = {"id": i, "method": metodo}
        if params:
            msg["params"] = params
        ev = None
        if esperar:
            ev = threading.Event()
            self.pend[i] = [ev, None]
        try:
            self.ws.enviar_texto(json.dumps(msg))
        except OSError:
            return None
        if esperar:
            ev.wait(timeout)
            return self.pend.pop(i, [None, None])[1]
        return None

    def _bucle(self):
        while True:
            try:
                txt = self.ws.recibir_mensaje()
            except Exception:
                txt = None
            if txt is None:
                print("Se cerro el navegador. Cerrando...")
                os._exit(1)
            try:
                d = json.loads(txt)
            except ValueError:
                continue
            if "id" in d:
                p = self.pend.get(d["id"])
                if p is not None:
                    p[1] = d.get("result", {})
                    p[0].set()
            elif "method" in d:
                try:
                    self.al_evento(d["method"], d.get("params", {}))
                except Exception as e:
                    print("error en evento:", e)


# ------------------------------------------------------------ estado y envio al iPad
estado = {"w": 1024, "h": 700, "movil": False, "url": ""}
cdp = None


class Saliente:
    def __init__(self):
        self.cond = threading.Condition()
        self.frame = None
        self.seq = 0
        self.mensajes = []

    def poner_frame(self, f):
        with self.cond:
            self.frame = f
            self.seq += 1
            self.cond.notify_all()

    def poner_mensaje(self, t):
        with self.cond:
            self.mensajes.append(t)
            if len(self.mensajes) > 50:
                self.mensajes.pop(0)
            self.cond.notify_all()


sal = Saliente()
estadisticas = {"cuadros": 0, "bytes": 0}


def enviar_url(u):
    estado["url"] = u
    sal.poner_mensaje("URL," + u)


def al_evento(metodo, params):
    if metodo == "Page.screencastFrame":
        cdp.llamar("Page.screencastFrameAck", {"sessionId": params.get("sessionId")})
        jpg = base64.b64decode(params.get("data", ""))
        estadisticas["cuadros"] += 1
        estadisticas["bytes"] += len(jpg)
        sal.poner_frame(jpg)
    elif metodo == "Page.frameNavigated":
        f = params.get("frame", {})
        if not f.get("parentId"):
            enviar_url(f.get("url", ""))
    elif metodo == "Page.navigatedWithinDocument":
        enviar_url(params.get("url", ""))
    elif metodo == "Page.javascriptDialogOpening":
        cdp.llamar("Page.handleJavaScriptDialog", {"accept": True})


# ------------------------------------------------------------ control del navegador
JS_INICIAL = """
(function(){
  try {
    window.open = function(u){ if (u) { location.href = u; } return window; };
    document.addEventListener('click', function(e){
      var a = (e.target && e.target.closest) ? e.target.closest('a') : null;
      if (a && a.target && a.target !== '_self') { a.target = '_self'; }
    }, true);
  } catch (err) {}
})();
"""

JS_FOCO = """
(function(){
  var e = document.activeElement;
  if (!e) return 0;
  var t = (e.tagName || '').toLowerCase();
  if (t === 'textarea' || e.isContentEditable) return 1;
  if (t === 'input') {
    var ty = (e.type || 'text').toLowerCase();
    return ['button','checkbox','radio','submit','reset','file','image','range','color'].indexOf(ty) < 0 ? 1 : 0;
  }
  return 0;
})();
"""

TECLAS = {
    "Enter": ("Enter", "Enter", 13, "\r"),
    "Backspace": ("Backspace", "Backspace", 8, None),
    "Tab": ("Tab", "Tab", 9, None),
    "ArrowLeft": ("ArrowLeft", "ArrowLeft", 37, None),
    "ArrowUp": ("ArrowUp", "ArrowUp", 38, None),
    "ArrowRight": ("ArrowRight", "ArrowRight", 39, None),
    "ArrowDown": ("ArrowDown", "ArrowDown", 40, None),
}

BUSCADORES = {
    "google": "https://www.google.com/search?q=",
    "duckduckgo": "https://duckduckgo.com/?q=",
    "bing": "https://www.bing.com/search?q=",
}


def a_url(texto):
    t = texto.strip()
    if not t:
        return None
    if "://" in t or t.startswith("about:") or t.startswith("file:"):
        return t
    if " " not in t and ("." in t or t.startswith("localhost")):
        return "https://" + t
    return BUSCADORES[args.buscador] + urllib.parse.quote_plus(t)


def aplicar_viewport():
    cdp.llamar("Emulation.setDeviceMetricsOverride", {
        "width": estado["w"], "height": estado["h"],
        "deviceScaleFactor": args.escala, "mobile": estado["movil"]})
    cdp.llamar("Emulation.setUserAgentOverride", {
        "userAgent": UA_MOVIL if estado["movil"] else UA_ESCRITORIO})


def iniciar_screencast():
    cdp.llamar("Page.stopScreencast")
    cdp.llamar("Page.startScreencast", {
        "format": "jpeg", "quality": args.calidad,
        "maxWidth": int(estado["w"] * args.escala),
        "maxHeight": int(estado["h"] * args.escala),
        "everyNthFrame": 1})


def raton(tipo, x, y, boton=None, clics=0, dx=0.0, dy=0.0):
    p = {"type": tipo, "x": x, "y": y}
    if boton:
        p["button"] = boton
        p["buttons"] = 1 if tipo == "mousePressed" else 0
    if clics:
        p["clickCount"] = clics
    if tipo == "mouseWheel":
        p["deltaX"] = dx
        p["deltaY"] = dy
    cdp.llamar("Input.dispatchMouseEvent", p)


def revisar_foco():
    r = cdp.llamar("Runtime.evaluate", {"expression": JS_FOCO, "returnByValue": True}, esperar=True)
    try:
        valor = r["result"]["value"]
    except (TypeError, KeyError):
        return
    sal.poner_mensaje("KB,1" if valor == 1 else "KB,0")


def clic(x, y):
    raton("mouseMoved", x, y)
    raton("mousePressed", x, y, "left", 1)
    raton("mouseReleased", x, y, "left", 1)
    threading.Timer(0.15, revisar_foco).start()


def tecla(nombre):
    k = TECLAS.get(nombre)
    if not k:
        return
    key, code, vk, texto = k
    base = {"key": key, "code": code, "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk}
    d = dict(base)
    d["type"] = "keyDown"
    if texto:
        d["text"] = texto
        d["unmodifiedText"] = texto
    cdp.llamar("Input.dispatchKeyEvent", d)
    u = dict(base)
    u["type"] = "keyUp"
    cdp.llamar("Input.dispatchKeyEvent", u)


def historial(delta):
    r = cdp.llamar("Page.getNavigationHistory", esperar=True)
    if not r:
        return
    i = r.get("currentIndex", 0) + delta
    entradas = r.get("entries", [])
    if 0 <= i < len(entradas):
        cdp.llamar("Page.navigateToHistoryEntry", {"entryId": entradas[i]["id"]})


def manejar(linea):
    p = linea.strip().split(",")
    if not p or not p[0]:
        return
    c = p[0]
    try:
        if c == "SIZE":
            estado["w"] = int(p[1])
            estado["h"] = int(p[2])
            estado["movil"] = (p[3] == "1")
            aplicar_viewport()
            iniciar_screencast()
        elif c == "C":
            clic(float(p[1]) * estado["w"], float(p[2]) * estado["h"])
        elif c == "S":
            raton("mouseWheel", float(p[1]) * estado["w"], float(p[2]) * estado["h"],
                  dx=-float(p[3]), dy=-float(p[4]))
        elif c == "TXT":
            texto = urllib.parse.unquote(p[1]) if len(p) > 1 else ""
            if texto:
                cdp.llamar("Input.insertText", {"text": texto})
        elif c == "KEY":
            tecla(p[1])
        elif c == "NAV":
            url = a_url(urllib.parse.unquote(p[1]))
            if url:
                cdp.llamar("Page.navigate", {"url": url})
        elif c == "BACK":
            historial(-1)
        elif c == "FWD":
            historial(1)
        elif c == "RELOAD":
            cdp.llamar("Page.reload")
        elif c == "MODE":
            estado["movil"] = (p[1] == "1")
            aplicar_viewport()
            cdp.llamar("Page.reload")
    except (IndexError, ValueError):
        pass


# ------------------------------------------------------------ conexion con el iPad (USB o Wi-Fi)
USBMUXD = ("127.0.0.1", 27015)


class MuxNoDisponible(Exception):
    pass


class AppCerrada(Exception):
    pass


def _leer(s, n):
    datos = b""
    while len(datos) < n:
        trozo = s.recv(n - len(datos))
        if not trozo:
            raise ConnectionError("conexion cerrada")
        datos += trozo
    return datos


def _mux_enviar(s, tag, mensaje):
    cuerpo = plistlib.dumps(mensaje)
    s.sendall(struct.pack("<IIII", 16 + len(cuerpo), 1, 8, tag) + cuerpo)


def _mux_recibir(s):
    largo, version, tipo, tag = struct.unpack("<IIII", _leer(s, 16))
    return plistlib.loads(_leer(s, largo - 16))


def mux_dispositivos():
    try:
        s = socket.create_connection(USBMUXD, timeout=3)
    except OSError:
        raise MuxNoDisponible()
    try:
        _mux_enviar(s, 1, {"MessageType": "ListDevices", "ClientVersionString": "puente_web",
                           "ProgName": "puente_web"})
        resp = _mux_recibir(s)
    finally:
        s.close()
    return resp.get("DeviceList", [])


def mux_conectar(device_id, puerto):
    try:
        s = socket.create_connection(USBMUXD, timeout=3)
    except OSError:
        raise MuxNoDisponible()
    _mux_enviar(s, 2, {"MessageType": "Connect", "ClientVersionString": "puente_web",
                       "ProgName": "puente_web", "DeviceID": device_id,
                       "PortNumber": ((puerto & 0xFF) << 8) | (puerto >> 8)})
    resp = _mux_recibir(s)
    if resp.get("Number", -1) != 0:
        s.close()
        raise AppCerrada()
    s.settimeout(None)
    return s


def recibir_lineas(s):
    buf = b""
    while True:
        try:
            datos = s.recv(4096)
        except OSError:
            break
        if not datos:
            break
        buf += datos
        while b"\n" in buf:
            linea, buf = buf.split(b"\n", 1)
            try:
                manejar(linea.decode("utf-8", "ignore"))
            except Exception as e:
                print("error:", e)
    try:
        s.close()
    except OSError:
        pass


def enviar_loop(s):
    ultimo = -1
    while True:
        with sal.cond:
            while sal.seq == ultimo and not sal.mensajes:
                sal.cond.wait(timeout=1)
            msgs = sal.mensajes
            sal.mensajes = []
            frame = sal.frame if sal.seq != ultimo else None
            ultimo = sal.seq
        for m in msgs:
            d = m.encode("utf-8")
            s.sendall(struct.pack(">BI", 77, len(d)) + d)
        if frame is not None:
            s.sendall(struct.pack(">BI", 74, len(frame)) + frame)


_ultimo_aviso = [""]


def aviso(texto):
    if texto != _ultimo_aviso[0]:
        _ultimo_aviso[0] = texto
        print(texto)


def hilo_conexion():
    while True:
        try:
            if args.ip:
                s = socket.create_connection((args.ip, args.puerto), timeout=3)
            else:
                devs = mux_dispositivos()
                usb = [d for d in devs if d.get("Properties", {}).get("ConnectionType", "USB") == "USB"]
                if not usb:
                    aviso("No veo el iPad por USB. Conectalo, desbloquealo y toca 'Confiar'.")
                    time.sleep(2)
                    continue
                s = mux_conectar(usb[0]["DeviceID"], args.puerto)
        except MuxNoDisponible:
            aviso("No encuentro el servicio USB de Apple. Instala iTunes (o 'Dispositivos Apple').")
            time.sleep(3)
            continue
        except AppCerrada:
            aviso("iPad conectado. Abri la app PuenteWeb en el iPad.")
            time.sleep(1)
            continue
        except OSError:
            aviso("Esperando al iPad...")
            time.sleep(2)
            continue

        try:
            s.settimeout(None)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        print("Conectado al iPad!")
        _ultimo_aviso[0] = ""
        if estado["url"]:
            sal.poner_mensaje("URL," + estado["url"])
        threading.Thread(target=recibir_lineas, args=(s,), daemon=True).start()
        try:
            enviar_loop(s)
        except (OSError, ConnectionError):
            pass
        try:
            s.close()
        except OSError:
            pass
        print("Se perdio la conexion con el iPad. Reintento...")
        time.sleep(1)


def hilo_estadisticas():
    while True:
        c0, b0 = estadisticas["cuadros"], estadisticas["bytes"]
        time.sleep(10)
        c = estadisticas["cuadros"] - c0
        b = estadisticas["bytes"] - b0
        if c > 0:
            print("Imagen: %.1f cuadros/s, %.0f KB por cuadro" % (c / 10.0, b / c / 1024.0))


def main():
    global cdp
    print("PuenteWeb - lado PC")
    print("Navegador:", NAVEGADOR)
    abrir_navegador()
    url_ws = esperar_pagina()
    if not url_ws:
        print("No pude conectarme al navegador. Cerra otras ventanas de Edge/Chrome y proba de nuevo.")
        input("Apreta Enter para salir")
        return
    cdp = CDP(url_ws, al_evento)
    cdp.llamar("Page.enable")
    cdp.llamar("Page.addScriptToEvaluateOnNewDocument", {"source": JS_INICIAL})
    aplicar_viewport()
    iniciar_screencast()
    cdp.llamar("Page.navigate", {"url": args.inicio})
    threading.Thread(target=hilo_estadisticas, daemon=True).start()
    threading.Thread(target=hilo_conexion, daemon=True).start()
    print("Listo. Abri la app PuenteWeb en el iPad. Cerra esta ventana para salir.\n")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Chau.")


if __name__ == "__main__":
    main()
