# Monitoreo de cuartos fríos

Recibe la temperatura y la humedad que manda un STM32 por cable serial, las guarda
en una base de datos, detecta cuando algo sale de rango y entrega los datos por una API.
Incluye la interfaz de escritorio del equipo, ya conectada a esa API.

Usa la misma tecnología y estructura que el proyecto de la báscula (Django + SQLite).
**Para tus compañeros:** el documento importante es [CONTRATOS.md](CONTRATOS.md).

## Demo: cómo arrancar todo

**Doble clic en `iniciar_demo.bat`.** Esto hace tres cosas:
1. Arranca el servidor con el simulador de 8 sensores (queda una ventana minimizada con sus mensajes).
2. Abre la interfaz.
3. Al cerrar la interfaz, apaga el servidor.

La primera vez, crea tu cuenta con el botón **Registrar** de la ventana de inicio de sesión.
Para entrar también a `/admin/` (http://localhost:8000/admin/) necesitas una cuenta de administrador:
```powershell
.\venv\Scripts\python.exe manage.py createsuperuser
```

**Antes de la demo:** para que la gráfica de 7 días y las estadísticas tengan datos, genera una
semana de historial simulado. Tarda unos 8 minutos y solo se hace una vez:
```powershell
.\venv\Scripts\python.exe manage.py generar_historial --dias 7 --reemplazar
```
> `--reemplazar` borra las lecturas, excursiones y alertas que existan. Las cuentas y la configuración no se tocan.

### Solo el servidor (sin interfaz)
```powershell
.\run.ps1
```

### Primera vez en una PC nueva
```powershell
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe manage.py migrate
```

Por defecto arranca en **modo simulador**: no necesita el STM32. Para usar el real, en
`/admin/` → Configuración, cambia el modo a "Puerto serial" y pon el puerto COM correcto.

## Comandos útiles
| Comando (anteponer `.\venv\Scripts\python.exe manage.py`) | Qué hace |
|---|---|
| `test monitoreo` | Corre las 60 pruebas automáticas |
| `generar_historial --dias 7 --reemplazar` | Llena la base de datos con días de datos simulados |
| `simular_lineas` | Muestra mensajes de ejemplo como los que mandará el STM32 |
| `limpiar_datos` | Borra ya los datos de más de 60 días (también ocurre solo cada hora) |

## Cómo viaja un dato

```
STM32 ──"$TH,C01,S01,435,872*03"──> lector.py ──> protocolo.py ──> procesador.py ──> base de datos
(o simulador.py)                    (escucha)     (¿es válido?)    (¿guardar? ¿excursión? ¿alerta?)
                                                                                         │
                       interfaz/monitor_cuartos_frios.py <──── JSON ──── api.py <────────┘
                       (ventana Tkinter)                                  (consultas)
```

## Mapa de archivos

| Archivo | Para qué sirve |
|---|---|
| `config/settings.py` | Configuración general: base de datos, zona horaria, si se permite el registro |
| `config/urls.py` | Las direcciones del sitio: `/admin/` y `/api/` |
| `monitoreo/models.py` | **Las tablas de la base de datos** |
| `monitoreo/protocolo.py` | Entiende y valida los mensajes del STM32 (contrato 1) |
| `monitoreo/procesador.py` | **Las reglas**: qué guardar, cuándo hay excursión o alerta, qué borrar |
| `monitoreo/lector.py` | Hilo en segundo plano que escucha el puerto serial o el simulador |
| `monitoreo/simulador.py` | Finge ser el STM32 con 8 sensores para probar sin hardware |
| `monitoreo/en_vivo.py` | Guarda el último estado en memoria para los datos en vivo |
| `monitoreo/api.py` + `urls.py` | La API para la interfaz (contrato 2) |
| `monitoreo/admin.py` | Cómo se ven las tablas en `/admin/` |
| `monitoreo/migrations/` | Instrucciones para crear las tablas; `0002` y `0004` crean el cuarto C01 con sus 8 sensores |
| `monitoreo/tests/` | Pruebas automáticas (incluye pruebas de la interfaz contra un servidor real) |
| `monitoreo/management/commands/` | Los comandos `generar_historial`, `simular_lineas` y `limpiar_datos` |
| `interfaz/monitor_cuartos_frios.py` | La interfaz del equipo, conectada a la API |
| `iniciar_demo.bat` / `.ps1` | Arranca servidor + interfaz |

## Las tablas

| Tabla | Qué guarda |
|---|---|
| **Configuracion** | Una sola fila: modo, puerto, baudrate, cada cuánto guardar, días de retención, sensores simulados |
| **CuartoFrio** | Cada cuarto con sus límites: mínimo, **advertencia** y máximo de temperatura, y rango de humedad |
| **Sensor** | Cada sensor, su cuarto, su nombre y su último valor recibido |
| **Lectura** | El historial: una fila por minuto por sensor |
| **Excursion** | **Cada** salida de rango, aunque dure segundos: inicio, fin, peor valor |
| **Alerta** | Salidas de rango que duraron demasiado, o sensores que dejaron de mandar datos |

## Las reglas (procesador.py)
1. Cada mensaje actualiza el último valor del sensor (más o menos cada 5 s).
2. En el historial se guarda **una lectura por minuto**, porque la temperatura cambia despacio.
3. Desde la **advertencia** (10 °C en la demo) el sensor se ve amarillo; por encima del **máximo** (13 °C), rojo.
4. Cada vez que un valor sale del rango se registra una **excursión**, aunque dure segundos.
5. Si la excursión dura **10 minutos seguidos**, además se registra una **alerta** (en la interfaz aparece resaltada en naranja).
6. Cuando el valor regresa al rango, la excursión y la alerta se cierran solas.
7. Si un sensor no manda nada en 30 segundos, la interfaz lo muestra **Offline**. Si sigue así **5 minutos**, se registra la alerta "sin datos".
8. Cada hora se borran los datos de más de **60 días**.

Todos esos números se pueden cambiar en `/admin/` sin tocar código.

## Qué se adaptó de la interfaz del equipo
La interfaz original (`Interfaz_litro.py`) no se modificó; la versión conectada está en `interfaz/`.

| La interfaz original… | Ahora… | Por qué |
|---|---|---|
| Generaba datos al azar | Pide los datos a la API | Es el punto que el equipo dejó para conectar la base de datos |
| Guardaba usuarios en `credentials.txt` (SHA-256 simple) | Usa los usuarios de Django | Cifrado de contraseñas mucho más fuerte, reglas de seguridad y un solo lugar para las cuentas |
| Tenía 10 °C y 13 °C fijos en el código | Colorea según el estado que calcula la app | Los límites se cambian en `/admin/` sin reprogramar |
| Pedía datos en el mismo hilo que dibuja | Los pide en un hilo aparte | La ventana nunca se congela si la red tarda |
| Mostraba `4°C` (sin decimales) | Muestra `4.3°C` | En un cuarto frío, medio grado importa |
| No distinguía "sin conexión" de "contraseña incorrecta" | Muestra mensajes distintos | El operador sabe qué está fallando |
| Statistics estaba vacía | Resumen por periodo: mín/máx/promedio, excursiones y alertas | Lo pedía la interfaz |
| Solo podía cerrar la aplicación | También puede cerrar sesión | Con usuarios reales hace falta |
| Mostraba 8 sensores fijos | Muestra los sensores que tenga el cuarto | Funciona igual si se agregan o quitan sensores |

## Pendientes
- [ ] Confirmar el **modelo de sensor**: define precisión y si mide humedad. El sistema ya acepta sensores sin humedad.
- [ ] Confirmar los **límites reales** de los cuartos fríos (la demo usa los de la interfaz: advertencia 10 °C, alarma 13 °C).
- [ ] Probar con el STM32 real (contrato 1).
- [ ] La interfaz supone un solo cuarto (`C01`). Si hay más, hará falta un selector de cuarto.
- [ ] Antes de usarlo en producción: poner `DEBUG = False`, limitar `ALLOWED_HOSTS` y decidir si se deja el registro abierto.
