"""
Interfaz de escritorio del monitoreo de cuartos fríos.

Basada en Interfaz_litro.py (diseño del equipo de interfaz). El diseño visual es el mismo;
lo que cambió:
  - Los datos vienen de la API de la aplicación (antes eran aleatorios). Ver CONTRATOS.md.
  - Inicio de sesión y registro con los usuarios de Django (antes: credentials.txt con SHA-256).
  - Los colores salen del estado que calcula la aplicación con los límites del cuarto
    (antes: 10 °C y 13 °C fijos en el código). Se cambian desde /admin/ sin tocar código.
  - Las peticiones se hacen en un hilo aparte, así la ventana nunca se congela.
  - Statistics muestra el resumen por periodo y User permite cerrar sesión.

Uso:  python monitor_cuartos_frios.py
      El servidor debe estar encendido. Su dirección se toma de la variable de entorno
      MONITOREO_SERVIDOR (por defecto http://127.0.0.1:8000).
"""
import http.cookiejar
import json
import os
import queue
import threading
import time
import tkinter as tk
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from tkinter import ttk, messagebox

# ----------------------------------------------------------------------------
# Configuración general
# ----------------------------------------------------------------------------
SERVIDOR = os.environ.get("MONITOREO_SERVIDOR", "http://127.0.0.1:8000")
CUARTO = os.environ.get("MONITOREO_CUARTO", "C01")
REFRESH_MS = 2000          # cada cuánto se actualizan los sensores
SEG_ALARMAS = 5            # cada cuánto se actualiza la lista de alarmas
SEG_HISTORIAL = 60         # cada cuánto se actualiza la gráfica de 7 días
SEG_ESTADISTICAS = 60
MAX_ALARMAS = 50

COLOR_BLUE = "#0A47B1"
COLOR_BLUE_LIGHT = "#5B9BE6"
COLOR_BLUE_ACTIVE = "#2F66C9"
COLOR_BG = "#EDEDED"
COLOR_CARD = "#F7F7F7"
COLOR_BORDER = "#BDBDBD"
COLOR_GREEN = "#14B35A"
COLOR_YELLOW = "#F5B942"
COLOR_RED = "#D9372B"
COLOR_GRAY = "#9A9A9A"
COLOR_ORANGE = "#F4A460"
COLOR_TEXT = "#333333"

# Color y texto de cada estado que calcula la aplicación (ver CONTRATOS.md)
COLOR_ESTADO = {
    "normal": COLOR_BLUE_LIGHT,
    "advertencia": COLOR_YELLOW,
    "fuera_de_rango": COLOR_RED,
    "alerta": COLOR_RED,
    "sin_datos": COLOR_GRAY,
}
TEXTO_ESTADO = {
    "normal": "Normal",
    "advertencia": "Advertencia",
    "fuera_de_rango": "Fuera de rango",
    "alerta": "Alerta",
    "sin_datos": "Sin datos",
}
PERIODOS = [("24 horas", 24), ("7 días", 24 * 7), ("30 días", 24 * 30)]


# ----------------------------------------------------------------------------
# Cliente de la API (reemplaza a credentials.txt y a los datos aleatorios)
# ----------------------------------------------------------------------------
class ErrorConexion(Exception):
    """No se pudo llegar al servidor (apagado, dirección equivocada, sin red)."""


class SesionExpirada(Exception):
    """El servidor respondió 401: hay que volver a iniciar sesión."""


class ErrorApi(Exception):
    """El servidor rechazó la petición; el mensaje explica por qué."""


class ApiClient:
    def __init__(self, servidor):
        self.servidor = servidor.rstrip("/")
        # La cookie de sesión se guarda aquí después de iniciar sesión
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def _pedir(self, metodo, ruta, params=None, datos=None):
        url = self.servidor + ruta
        if params:
            url += "?" + urllib.parse.urlencode(params)
        cuerpo = json.dumps(datos).encode("utf-8") if datos is not None else None
        peticion = urllib.request.Request(url, data=cuerpo, method=metodo, headers={
            "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with self._opener.open(peticion, timeout=8) as respuesta:
                return json.loads(respuesta.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                mensaje = json.loads(e.read()).get("error")
            except Exception:
                mensaje = None
            if e.code == 401:
                raise SesionExpirada(mensaje or "La sesión expiró.")
            raise ErrorApi(mensaje or f"El servidor respondió con error {e.code}.")
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ErrorConexion(f"No hay conexión con el servidor ({self.servidor}). ¿Está encendido?")

    def get(self, ruta, params=None):
        return self._pedir("GET", ruta, params=params)

    def iniciar_sesion(self, usuario, contrasena):
        return self._pedir("POST", "/api/sesion/", datos={"usuario": usuario, "contrasena": contrasena})

    def cerrar_sesion(self):
        return self._pedir("DELETE", "/api/sesion/")

    def registrar(self, usuario, contrasena):
        return self._pedir("POST", "/api/registro/", datos={"usuario": usuario, "contrasena": contrasena})


def hace_horas(horas):
    return (datetime.now().astimezone() - timedelta(hours=horas)).isoformat(timespec="seconds")


def formato_duracion(segundos):
    if segundos < 60:
        return f"{segundos:02d}s"
    if segundos < 3600:
        return f"{segundos // 60} min"
    horas, resto = divmod(segundos, 3600)
    return f"{horas} h {resto // 60} min"


def formato_valor(valor, unidad):
    return f"{valor:.1f}°C" if unidad == "°C" else f"{valor:.1f}%"


# ----------------------------------------------------------------------------
# Fuente de datos: pide todo a la API en segundo plano
# ----------------------------------------------------------------------------
class Actualizador(threading.Thread):
    """
    Pide los datos a la API en un hilo aparte y los deja en una cola.
    La ventana los recoge con after(), porque Tkinter solo se puede tocar desde el hilo principal.
    """

    def __init__(self, cliente, cola, generacion, horas_estadisticas):
        super().__init__(name="Actualizador", daemon=True)
        self._cliente = cliente
        self._cola = cola
        self._generacion = generacion
        self.horas_estadisticas = horas_estadisticas
        self._forzar_estadisticas = False
        self._detener = threading.Event()
        self._despertar = threading.Event()

    def detener(self):
        self._detener.set()
        self._despertar.set()

    def pedir_estadisticas(self, horas):
        self.horas_estadisticas = horas
        self._forzar_estadisticas = True
        self._despertar.set()

    def _enviar(self, tipo, datos=None):
        self._cola.put((self._generacion, tipo, datos))

    def run(self):
        proximo = {"alarmas": 0.0, "historial": 0.0, "estadisticas": 0.0}
        while not self._detener.is_set():
            ahora = time.monotonic()
            try:
                self._enviar("cuarto", self._cliente.get(f"/api/cuartos/{CUARTO}/"))
                if ahora >= proximo["alarmas"]:
                    self._enviar("alarmas", self._alarmas())
                    proximo["alarmas"] = ahora + SEG_ALARMAS
                if ahora >= proximo["historial"]:
                    self._enviar("historial", self._cliente.get(
                        "/api/lecturas/por_hora/", {"cuarto": CUARTO, "desde": hace_horas(24 * 7)}))
                    proximo["historial"] = ahora + SEG_HISTORIAL
                if self._forzar_estadisticas or ahora >= proximo["estadisticas"]:
                    self._forzar_estadisticas = False
                    horas = self.horas_estadisticas
                    datos = self._cliente.get("/api/lecturas/resumen/", {"cuarto": CUARTO, "desde": hace_horas(horas)})
                    datos["horas"] = horas
                    self._enviar("estadisticas", datos)
                    proximo["estadisticas"] = ahora + SEG_ESTADISTICAS
                self._enviar("conectado")
            except SesionExpirada:
                self._enviar("sesion_expirada")
                return
            except (ErrorConexion, ErrorApi) as e:
                self._enviar("error", str(e))
            self._despertar.wait(REFRESH_MS / 1000)
            self._despertar.clear()

    def _alarmas(self):
        """Excursiones (cada salida de rango) + sensores sin datos, de la más reciente a la más vieja."""
        excursiones = self._cliente.get("/api/excursiones/", {"cuarto": CUARTO, "limite": MAX_ALARMAS})["excursiones"]
        sin_datos = self._cliente.get("/api/alertas/", {"cuarto": CUARTO, "tipo": "sin_datos", "limite": MAX_ALARMAS})["alertas"]
        items = [{
            "inicio": datetime.fromisoformat(e["inicio"]),
            "texto": f"{e['sensor_nombre']} {formato_valor(e['valor_extremo'], e['unidad'])}",
            "duracion_seg": e["duracion_seg"],
            "activa": e["activa"],
            "larga": e["es_larga"],
        } for e in excursiones]
        items += [{
            "inicio": datetime.fromisoformat(a["inicio"]),
            "texto": f"{a['sensor_nombre']} sin datos",
            "duracion_seg": a["duracion_seg"],
            "activa": a["activa"],
            "larga": True,
        } for a in sin_datos]
        items.sort(key=lambda item: item["inicio"], reverse=True)
        return items[:MAX_ALARMAS]


# ----------------------------------------------------------------------------
# Ventanas de login y registro
# ----------------------------------------------------------------------------
class LoginWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Iniciar sesión")
        self.geometry("626x431")
        self.resizable(False, False)
        self.configure(bg="#708090")
        self.protocol("WM_DELETE_WINDOW", app.salir)

        card = tk.Frame(self, bg=COLOR_BLUE, relief="groove", bd=2)
        card.place(relx=0.5, rely=0.5, anchor="center", width=420, height=190)

        title_font = ("Trebuchet MS", 16, "bold")
        text_style = {"font": ("Verdana", 12), "bg": COLOR_BLUE, "fg": "#E1FFFF"}

        tk.Label(card, text="Login", font=title_font, bg=COLOR_BLUE, fg="white").grid(
            row=0, column=0, columnspan=2, pady=(12, 10))
        tk.Label(card, text="Usuario:", **text_style).grid(row=1, column=0, padx=10, pady=5, sticky="e")
        tk.Label(card, text="Contraseña:", **text_style).grid(row=2, column=0, padx=10, pady=5, sticky="e")

        self.entry_user = ttk.Entry(card, width=30)
        self.entry_user.grid(row=1, column=1, pady=5)
        self.entry_pw = ttk.Entry(card, width=30, show="*")
        self.entry_pw.grid(row=2, column=1, pady=5)

        buttons = tk.Frame(card, bg=COLOR_BLUE)
        buttons.grid(row=3, column=0, columnspan=2, pady=15)
        ttk.Button(buttons, text="Entrar", command=self.login).pack(side="left", padx=8)
        ttk.Button(buttons, text="Registrar", command=lambda: RegisterWindow(self, app.cliente)).pack(side="left", padx=8)

        self.bind("<Return>", lambda e: self.login())
        self.entry_user.focus_set()

    def login(self):
        username = self.entry_user.get().strip()
        password = self.entry_pw.get()
        if not username or not password:
            messagebox.showerror("Error", "Escribe tu usuario y contraseña.", parent=self)
            return
        try:
            datos = self.app.cliente.iniciar_sesion(username, password)
        except ErrorConexion as e:
            messagebox.showerror("Sin conexión", str(e), parent=self)
            return
        except ErrorApi as e:
            messagebox.showerror("Error", str(e), parent=self)
            return
        self.app.username = datos["usuario"]
        self.app.es_admin = datos["es_admin"]
        self.app.start_dashboard()
        self.destroy()


class RegisterWindow(tk.Toplevel):
    def __init__(self, parent, cliente):
        super().__init__(parent)
        self.parent = parent
        self.cliente = cliente
        self.title("Registro")
        self.geometry("300x170")
        self.resizable(False, False)
        self.configure(bg="#3F6BAA")
        self.transient(parent)
        self.grab_set()

        style = {"font": ("Verdana", 10), "bg": "#3F6BAA", "fg": "#E1FFFF"}
        tk.Label(self, text="Nuevo usuario:", **style).grid(row=0, column=0, padx=10, pady=(20, 5), sticky="e")
        tk.Label(self, text="Contraseña:", **style).grid(row=1, column=0, padx=10, pady=5, sticky="e")

        self.entry_user = ttk.Entry(self, width=18)
        self.entry_user.grid(row=0, column=1, pady=(20, 5))
        self.entry_pw = ttk.Entry(self, width=18, show="*")
        self.entry_pw.grid(row=1, column=1, pady=5)

        ttk.Button(self, text="Crear cuenta", command=self.signup).grid(row=2, column=0, columnspan=2, pady=15)
        self.bind("<Return>", lambda e: self.signup())
        self.entry_user.focus_set()

    def signup(self):
        user = self.entry_user.get().strip()
        pw = self.entry_pw.get()
        if not user or not pw:
            messagebox.showerror("Error", "Escribe un nombre de usuario y una contraseña.", parent=self)
            return
        try:
            # El servidor valida el nombre y la seguridad de la contraseña
            self.cliente.registrar(user, pw)
        except ErrorConexion as e:
            messagebox.showerror("Sin conexión", str(e), parent=self)
            return
        except ErrorApi as e:
            messagebox.showerror("No se pudo crear la cuenta", str(e), parent=self)
            return
        messagebox.showinfo("Listo", "Cuenta creada correctamente. Ya puedes iniciar sesión.", parent=self)
        self.parent.entry_user.delete(0, "end")
        self.parent.entry_user.insert(0, user)
        self.parent.entry_pw.focus_set()
        self.destroy()


# ----------------------------------------------------------------------------
# Widgets del dashboard
# ----------------------------------------------------------------------------
def make_card(parent):
    return tk.Frame(parent, bg=COLOR_CARD, highlightthickness=1, highlightbackground=COLOR_BORDER)


class SensorCard(tk.Frame):
    """Círculo de color con la temperatura, el nombre y el estado online/offline."""

    def __init__(self, parent, nombre):
        super().__init__(parent, bg=COLOR_CARD)
        self.canvas = tk.Canvas(self, width=84, height=84, bg=COLOR_CARD, highlightthickness=0)
        self.canvas.pack()
        self.circle = self.canvas.create_oval(4, 4, 80, 80, fill=COLOR_BLUE_LIGHT, outline="")
        self.text = self.canvas.create_text(42, 42, text="--", fill="white", font=("Arial", 14, "bold"))
        tk.Label(self, text=nombre, bg=COLOR_CARD, fg=COLOR_TEXT,
                 font=("Arial", 9)).pack()
        self.lbl_status = tk.Label(self, text="Offline", bg=COLOR_CARD, fg=COLOR_GRAY,
                                   font=("Arial", 9, "bold"))
        self.lbl_status.pack()

    def update_value(self, sensor):
        temp = sensor["temperatura"]
        if not sensor["en_linea"] or temp is None:
            color, text, status, status_color = COLOR_GRAY, "--", "Offline", COLOR_RED
        else:
            # El color lo decide la aplicación con los límites del cuarto
            color = COLOR_ESTADO.get(sensor["estado"], COLOR_BLUE_LIGHT)
            text, status, status_color = f"{temp:.1f}°C", "Online", COLOR_GREEN
        self.canvas.itemconfig(self.circle, fill=color)
        self.canvas.itemconfig(self.text, text=text)
        self.lbl_status.config(text=status, fg=status_color)


class HistoryChart(tk.Canvas):
    """Gráfica de línea dibujada con Canvas (sin librerías externas)."""

    def __init__(self, parent):
        super().__init__(parent, bg=COLOR_CARD, highlightthickness=0, height=80)
        self.data = []
        self.last_update = ""
        self.bind("<Configure>", lambda e: self.redraw())

    def set_data(self, data):
        self.data = data
        self.last_update = datetime.now().strftime("%H:%M")
        self.redraw()

    def redraw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if len(self.data) < 2:
            self.create_text(w / 2, h / 2, text="Aún no hay suficientes datos", fill=COLOR_GRAY, font=("Arial", 9))
            return
        left, right, top, bottom = 12, 12, 28, 14
        lo, hi = min(self.data), max(self.data)
        span = (hi - lo) or 1
        step = (w - left - right) / (len(self.data) - 1)

        pts = []
        for i, v in enumerate(self.data):
            x = left + i * step
            y = top + (hi - v) / span * (h - top - bottom)
            pts.append((x, y))

        # área bajo la curva
        poly = [(pts[0][0], h - bottom)] + pts + [(pts[-1][0], h - bottom)]
        self.create_polygon([c for p in poly for c in p], fill="#D6E6F7", outline="")
        # línea
        self.create_line([c for p in pts for c in p], fill=COLOR_BLUE, width=2, smooth=True)
        # eje base
        self.create_line(left, h - bottom, w - right, h - bottom, fill=COLOR_BORDER)
        # mínimo y máximo de la semana
        self.create_text(left, top - 14, text=f"Máx {hi:.1f}°C  ·  Mín {lo:.1f}°C",
                         anchor="w", font=("Arial", 7), fill=COLOR_TEXT)
        # punto final y línea vertical
        ex, ey = pts[-1]
        self.create_line(ex, top - 6, ex, h - bottom, fill=COLOR_TEXT)
        self.create_oval(ex - 4, ey - 4, ex + 4, ey + 4, fill=COLOR_BLUE, outline="")
        self.create_text(ex - 6, top - 14, text=f"Última act.\n{self.last_update}",
                         anchor="ne", font=("Arial", 7), fill=COLOR_TEXT, justify="right")


# ----------------------------------------------------------------------------
# Páginas
# ----------------------------------------------------------------------------
class HomePage(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=COLOR_BG)
        self.app = app

        self.grid_columnconfigure(0, weight=3)
        self.grid_columnconfigure(1, weight=2)
        # uniform: las filas se reparten exactamente 1:2, sin importar cuánto "pida" cada widget
        self.grid_rowconfigure(1, weight=1, uniform="filas")
        self.grid_rowconfigure(2, weight=2, uniform="filas")

        # --- Temperatura promedio + estado ---
        top = tk.Frame(self, bg=COLOR_BG)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", padx=15, pady=(15, 8))
        top.grid_columnconfigure(0, weight=1)

        avg_box = tk.Frame(top, bg=COLOR_BLUE, height=130)
        avg_box.grid(row=0, column=0, sticky="ew")
        avg_box.pack_propagate(False)
        avg_box.grid_propagate(False)
        tk.Label(avg_box, text="Avg\nTemp", bg=COLOR_BLUE, fg="white",
                 font=("Arial", 20, "bold"), justify="left").place(relx=0.07, rely=0.5, anchor="w")
        self.lbl_avg = tk.Label(avg_box, text="--°C", bg=COLOR_BLUE, fg="white",
                                font=("Arial", 54, "bold"))
        self.lbl_avg.place(relx=0.62, rely=0.5, anchor="center")

        status_box = tk.Frame(top, bg=COLOR_BG)
        status_box.grid(row=0, column=1, padx=(25, 10))
        tk.Label(status_box, text="Status", bg=COLOR_BG, fg=COLOR_TEXT,
                 font=("Arial", 13, "bold")).pack()
        self.status_canvas = tk.Canvas(status_box, width=70, height=70, bg=COLOR_BG, highlightthickness=0)
        self.status_canvas.pack(pady=5)
        self.status_circle = self.status_canvas.create_oval(4, 4, 66, 66, fill=COLOR_GRAY, outline="")

        # --- Historial 7 días ---
        chart_card = make_card(self)
        chart_card.grid(row=1, column=0, sticky="nsew", padx=(15, 8), pady=8)
        tk.Label(chart_card, text="Historial de temperatura (7 días)", bg=COLOR_CARD, fg=COLOR_TEXT,
                 font=("Arial", 11, "bold")).pack(pady=(6, 0))
        self.chart = HistoryChart(chart_card)
        self.chart.pack(fill="both", expand=True, padx=6, pady=6)

        # --- Alarmas ---
        alarm_card = make_card(self)
        alarm_card.grid(row=1, column=1, rowspan=2, sticky="nsew", padx=(8, 15), pady=8)
        tk.Label(alarm_card, text="Alarmas", bg=COLOR_CARD, fg=COLOR_TEXT,
                 font=("Arial", 11, "bold")).pack(pady=(6, 4))
        self.alarm_text = tk.Text(alarm_card, bg=COLOR_CARD, relief="flat", font=("Arial", 9),
                                  width=30, cursor="arrow", state="disabled", highlightthickness=0, wrap="word")
        self.alarm_text.tag_configure("center", justify="center", spacing1=4, spacing3=4)
        self.alarm_text.tag_configure("long", background=COLOR_ORANGE)
        self.alarm_text.pack(fill="both", expand=True, padx=6, pady=(0, 8))

        # --- Sensores (los que tenga el cuarto; 8 en la demo) ---
        self.sensors_card = make_card(self)
        self.sensors_card.grid(row=2, column=0, sticky="nsew", padx=(15, 8), pady=(8, 15))
        for c in range(4):
            self.sensors_card.grid_columnconfigure(c, weight=1)
        self.sensor_cards = {}

    def _asegurar_tarjetas(self, sensores):
        codigos = [s["codigo"] for s in sensores]
        if codigos == list(self.sensor_cards):
            return
        for card in self.sensor_cards.values():
            card.destroy()
        self.sensor_cards = {}
        for i, sensor in enumerate(sensores):
            card = SensorCard(self.sensors_card, sensor["nombre"])
            card.grid(row=i // 4, column=i % 4, padx=4, pady=6)
            self.sensor_cards[sensor["codigo"]] = card

    def refresh_cuarto(self, cuarto):
        sensores = [s for s in cuarto["sensores"] if s["activo"]]
        avg = cuarto["temperatura_promedio"]
        self.lbl_avg.config(text=f"{avg:.1f}°C" if avg is not None else "--°C")

        self._asegurar_tarjetas(sensores)
        for sensor in sensores:
            self.sensor_cards[sensor["codigo"]].update_value(sensor)

        # estado general: rojo si algún sensor está offline o en alarma, amarillo si alguno está en advertencia
        if any(not s["en_linea"] or s["estado"] in ("fuera_de_rango", "alerta", "sin_datos") for s in sensores):
            color = COLOR_RED
        elif any(s["estado"] == "advertencia" for s in sensores):
            color = COLOR_YELLOW
        else:
            color = COLOR_GREEN
        self.status_canvas.itemconfig(self.status_circle, fill=color)

    def refresh_historial(self, datos):
        self.chart.set_data([p["temp_promedio"] for p in datos["puntos"]])

    def refresh_alarmas(self, alarmas):
        self.alarm_text.config(state="normal")
        self.alarm_text.delete("1.0", "end")
        if not alarmas:
            self.alarm_text.insert("end", "Sin alarmas en los últimos 7 días\n", ("center",))
        for alarma in alarmas:
            inicio = alarma["inicio"].astimezone()
            duracion = formato_duracion(alarma["duracion_seg"]) + (" (en curso)" if alarma["activa"] else "")
            line = f"{alarma['texto']} • {inicio:%H:%M} - {duracion} • {inicio:%d/%m/%Y}\n"
            tags = ("center", "long") if alarma["larga"] else ("center",)
            self.alarm_text.insert("end", line, tags)
        self.alarm_text.config(state="disabled")


class SensorsPage(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=COLOR_BG)
        tk.Label(self, text="Sensores", bg=COLOR_BG, fg=COLOR_TEXT,
                 font=("Arial", 20, "bold")).pack(pady=(20, 10))
        cols = ("Sensor", "Temperatura", "Humedad", "Estado", "Condición", "Último dato")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", height=9)
        for c in cols:
            self.tree.heading(c, text=c)
            self.tree.column(c, width=125, anchor="center")
        self.tree.pack(padx=20, pady=10)

    def refresh(self, cuarto):
        self.tree.delete(*self.tree.get_children())
        for s in cuarto["sensores"]:
            if not s["activo"]:
                continue
            ultimo = datetime.fromisoformat(s["fecha_hora"]).astimezone().strftime("%H:%M:%S") if s["fecha_hora"] else "--"
            self.tree.insert("", "end", values=(
                s["nombre"],
                "--" if s["temperatura"] is None else f"{s['temperatura']:.1f}°C",
                "--" if s["humedad"] is None else f"{s['humedad']:.1f}%",
                "Online" if s["en_linea"] else "Offline",
                TEXTO_ESTADO.get(s["estado"], s["estado"]),
                ultimo))


class StatisticsPage(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=COLOR_BG)
        self.app = app
        tk.Label(self, text="Estadísticas", bg=COLOR_BG, fg=COLOR_TEXT,
                 font=("Arial", 20, "bold")).pack(pady=(20, 10))

        selector = tk.Frame(self, bg=COLOR_BG)
        selector.pack()
        tk.Label(selector, text="Periodo:", bg=COLOR_BG, fg=COLOR_TEXT, font=("Arial", 11)).pack(side="left", padx=6)
        self.horas = tk.IntVar(value=PERIODOS[0][1])
        for texto, horas in PERIODOS:
            ttk.Radiobutton(selector, text=texto, value=horas, variable=self.horas,
                            command=lambda: app.cambiar_periodo(self.horas.get())).pack(side="left", padx=6)

        cols = ("Sensor", "Lecturas", "T mín", "T máx", "T prom", "H prom", "Excursiones", "Alertas")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", height=9)
        for c in cols:
            self.tree.heading(c, text=c)
            self.tree.column(c, width=110 if c == "Excursiones" else 95, anchor="center")
        self.tree.pack(padx=20, pady=12)
        self.lbl_info = tk.Label(self, text="Cargando…", bg=COLOR_BG, fg=COLOR_GRAY, font=("Arial", 10))
        self.lbl_info.pack()

    def refresh(self, datos):
        if datos["horas"] != self.horas.get():
            return  # llegó la respuesta de un periodo que ya no está seleccionado
        self.tree.delete(*self.tree.get_children())

        def grados(v):
            return "--" if v is None else f"{v:.1f}°C"

        total_exc = total_alertas = 0
        for s in datos["sensores"]:
            total_exc += s["excursiones"]
            total_alertas += s["alertas"]
            hum = s["humedad"]["promedio"]
            self.tree.insert("", "end", values=(
                s["nombre"], s["total_lecturas"],
                grados(s["temperatura"]["min"]), grados(s["temperatura"]["max"]), grados(s["temperatura"]["promedio"]),
                "--" if hum is None else f"{hum:.1f}%",
                s["excursiones"], s["alertas"]))
        if datos["sensores"]:
            self.lbl_info.config(text=f"Total del periodo: {total_exc} excursiones fuera de rango, {total_alertas} alertas")
        else:
            self.lbl_info.config(text="No hay lecturas en este periodo.")


class UserPage(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=COLOR_BG)
        tk.Label(self, text="Usuario", bg=COLOR_BG, fg=COLOR_TEXT,
                 font=("Arial", 20, "bold")).pack(pady=20)
        self.lbl_user = tk.Label(self, text="", bg=COLOR_BG, fg=COLOR_TEXT, font=("Arial", 13))
        self.lbl_user.pack(pady=5)
        self.lbl_rol = tk.Label(self, text="", bg=COLOR_BG, fg=COLOR_GRAY, font=("Arial", 10))
        self.lbl_rol.pack()
        ttk.Button(self, text="Cerrar sesión", command=app.cerrar_sesion).pack(pady=(15, 5))
        ttk.Button(self, text="Cerrar aplicación", command=app.salir).pack(pady=5)

    def set_user(self, username, es_admin):
        self.lbl_user.config(text=f"Sesión iniciada como: {username}")
        self.lbl_rol.config(text="Administrador" if es_admin else "Operador")


# ----------------------------------------------------------------------------
# Aplicación principal
# ----------------------------------------------------------------------------
class SidebarButton(tk.Frame):
    def __init__(self, parent, icon, text, command):
        super().__init__(parent, bg=COLOR_BLUE, cursor="hand2")
        self.command = command
        self.lbl_icon = tk.Label(self, text=icon, bg=COLOR_BLUE, fg="white", font=("Segoe UI Emoji", 26))
        self.lbl_icon.pack(pady=(10, 0))
        self.lbl_text = tk.Label(self, text=text, bg=COLOR_BLUE, fg="white", font=("Arial", 11))
        self.lbl_text.pack(pady=(0, 10))
        for w in (self, self.lbl_icon, self.lbl_text):
            w.bind("<Button-1>", lambda e: self.command())

    def set_active(self, active):
        color = COLOR_BLUE_ACTIVE if active else COLOR_BLUE
        for w in (self, self.lbl_icon, self.lbl_text):
            w.config(bg=color)


class MyApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Monitor de Temperatura")
        self.geometry("1100x640")
        self.minsize(900, 560)
        self.configure(bg=COLOR_BG)
        self.protocol("WM_DELETE_WINDOW", self.salir)
        self.username = ""
        self.es_admin = False
        self.cliente = ApiClient(SERVIDOR)
        self.cola = queue.Queue()
        self.actualizador = None
        self.generacion = 0
        self.horas_estadisticas = PERIODOS[0][1]
        self.pages = None
        self.withdraw()               # oculta la ventana hasta iniciar sesión
        LoginWindow(self)
        self.after(200, self._procesar_cola)

    def start_dashboard(self):
        if self.pages is None:
            self._construir()
        self.pages["user"].set_user(self.username, self.es_admin)
        self.show_page("home")
        self.deiconify()
        self._iniciar_actualizador()

    def _construir(self):
        # --- barra lateral ---
        sidebar = tk.Frame(self, bg=COLOR_BLUE, width=120)
        sidebar.pack(side="left", fill="y", padx=(10, 0), pady=10)
        sidebar.pack_propagate(False)

        self.content = tk.Frame(self, bg=COLOR_BG)
        self.content.pack(side="left", fill="both", expand=True, padx=10, pady=10)
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)

        self.pages = {
            "home": HomePage(self.content, self),
            "sensors": SensorsPage(self.content, self),
            "stats": StatisticsPage(self.content, self),
            "user": UserPage(self.content, self),
        }
        for page in self.pages.values():
            page.grid(row=0, column=0, sticky="nsew")

        self.nav_buttons = {}
        for key, icon, text in (("home", "⌂", "Home"), ("sensors", "📡", "Sensors"),
                                ("stats", "📊", "Statistics"), ("user", "👤", "User")):
            btn = SidebarButton(sidebar, icon, text, lambda k=key: self.show_page(k))
            btn.pack(fill="x", pady=8)
            self.nav_buttons[key] = btn

        # estado de la conexión con el servidor
        self.lbl_conexion = tk.Label(sidebar, text="Conectando…", bg=COLOR_BLUE, fg="white",
                                     font=("Arial", 8), wraplength=110, justify="center")
        self.lbl_conexion.pack(side="bottom", pady=10)

    def show_page(self, key):
        self.pages[key].tkraise()
        for k, btn in self.nav_buttons.items():
            btn.set_active(k == key)

    def _iniciar_actualizador(self):
        self._detener_actualizador()
        self.generacion += 1
        self.actualizador = Actualizador(self.cliente, self.cola, self.generacion, self.horas_estadisticas)
        self.actualizador.start()

    def _detener_actualizador(self):
        if self.actualizador is not None:
            self.actualizador.detener()
            self.actualizador = None

    def cambiar_periodo(self, horas):
        self.horas_estadisticas = horas
        if self.actualizador is not None:
            self.actualizador.pedir_estadisticas(horas)

    def _procesar_cola(self):
        # Recoge lo que dejó el Actualizador (Tkinter solo se toca desde este hilo)
        try:
            while True:
                generacion, tipo, datos = self.cola.get_nowait()
                if generacion == self.generacion and self.actualizador is not None:
                    self._mostrar(tipo, datos)
        except queue.Empty:
            pass
        self.after(200, self._procesar_cola)

    def _mostrar(self, tipo, datos):
        if tipo == "cuarto":
            self.title(f"Monitor de Temperatura — {datos['nombre']}")
            self.pages["home"].refresh_cuarto(datos)
            self.pages["sensors"].refresh(datos)
        elif tipo == "historial":
            self.pages["home"].refresh_historial(datos)
        elif tipo == "alarmas":
            self.pages["home"].refresh_alarmas(datos)
        elif tipo == "estadisticas":
            self.pages["stats"].refresh(datos)
        elif tipo == "conectado":
            self.lbl_conexion.config(text="● Conectado", fg="#9BF0B8")
        elif tipo == "error":
            self.lbl_conexion.config(text=f"● {datos}", fg="#FFB3AD")
        elif tipo == "sesion_expirada":
            messagebox.showwarning("Sesión", "Tu sesión expiró. Vuelve a iniciar sesión.", parent=self)
            self.cerrar_sesion()

    def cerrar_sesion(self):
        self._detener_actualizador()
        try:
            self.cliente.cerrar_sesion()
        except (ErrorConexion, ErrorApi, SesionExpirada):
            pass
        self.withdraw()
        LoginWindow(self)

    def salir(self):
        self._detener_actualizador()
        self.destroy()


if __name__ == "__main__":
    MyApp().mainloop()
