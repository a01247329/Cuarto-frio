# Contratos del sistema de monitoreo de cuartos fríos

Este documento define cómo se conectan las tres partes del sistema.
Si algo de aquí cambia, hay que avisar a los demás antes de programarlo.

```
 [STM32] ──cable serial──> [Aplicación / backend] <──HTTP + JSON── [Interfaz]
          Contrato 1                                Contrato 2
```

---

## Contrato 1: STM32 → Aplicación (puerto serial)

### Parámetros del puerto
| Parámetro | Valor |
|---|---|
| Velocidad (baudrate) | **115200** (configurable en la aplicación) |
| Formato | 8 bits de datos, sin paridad, 1 bit de parada (8N1) |
| Codificación | Texto ASCII |
| Fin de línea | `\n` (se acepta también `\r\n`) |

### Formato del mensaje
Una línea por medición y por sensor:

```
$TH,<cuarto>,<sensor>,<temp_x100>,<hum_x10>*<checksum>\n
```

| Campo | Descripción | Ejemplo |
|---|---|---|
| `$` | Inicio del mensaje | `$` |
| `TH` | Tipo de mensaje: temperatura y humedad | `TH` |
| `cuarto` | Código del cuarto: mayúsculas y números, máximo 10 caracteres | `C01` |
| `sensor` | Código del sensor dentro del cuarto, mismas reglas | `S01` |
| `temp_x100` | Temperatura en °C **multiplicada por 100**, entero con signo | `435` = 4.35 °C, `-1850` = -18.50 °C |
| `hum_x10` | Humedad en % **multiplicada por 10**, entero. **Vacío** si el sensor no mide humedad | `872` = 87.2 % |
| `*` | Separador del checksum | `*` |
| `checksum` | XOR de todos los bytes **entre `$` y `*`** (sin incluirlos), en 2 dígitos hexadecimales | `03` |

### Ejemplos válidos
```
$TH,C01,S01,435,872*03     -> C01/S01: 4.35 °C, 87.2 %
$TH,C01,S01,912,900*0F     -> C01/S01: 9.12 °C, 90.0 %
$TH,C01,S02,-1850,*2E      -> C01/S02: -18.50 °C, sin humedad
```

### Reglas
- **La fecha y la hora las pone la aplicación** al recibir el mensaje. El STM32 no necesita reloj.
- **Frecuencia recomendada: un mensaje por sensor cada 5 segundos.** La aplicación guarda en el historial uno por minuto, pero usa todos para detectar problemas y mostrar datos en vivo.
- Rangos aceptados: temperatura de -40 a 85 °C y humedad de 0 a 100 %. Fuera de eso se descarta el mensaje como error del sensor.
- Si un sensor falla al leer, **no se manda la línea** de ese sensor. Si un sensor deja de mandar datos 5 minutos, la aplicación genera la alerta "sin datos".
- Los mensajes con checksum incorrecto o formato inválido se descartan y se cuentan como inválidos.
- El cuarto (`C01`) debe estar registrado en la aplicación. Los sensores nuevos de un cuarto registrado se dan de alta solos.

### Código de referencia para el STM32 (C)
```c
#include <stdint.h>
#include <stdio.h>
#include "main.h"   /* huart2 o la UART que se use */

/* XOR de todos los caracteres del cuerpo (lo que va entre '$' y '*') */
static uint8_t calcular_checksum(const char *cuerpo)
{
    uint8_t cs = 0;
    while (*cuerpo) {
        cs ^= (uint8_t)*cuerpo++;
    }
    return cs;
}

/* temperatura y humedad ya convertidas a enteros: 4.35 °C -> 435, 87.2 % -> 872 */
void enviar_medicion(const char *cuarto, const char *sensor, int temp_x100, int hum_x10)
{
    char cuerpo[48];
    char linea[64];
    snprintf(cuerpo, sizeof(cuerpo), "TH,%s,%s,%d,%d", cuarto, sensor, temp_x100, hum_x10);
    int n = snprintf(linea, sizeof(linea), "$%s*%02X\n", cuerpo, calcular_checksum(cuerpo));
    HAL_UART_Transmit(&huart2, (uint8_t *)linea, n, 100);
}
```
> Si el sensor no mide humedad, el formato del cuerpo es `"TH,%s,%s,%d,"` (último campo vacío).
>
> Se usan enteros porque el `printf` del STM32 no imprime decimales (`%f`) a menos que se active `-u _printf_float`, que ocupa mucha memoria.

Para ver líneas de ejemplo generadas por la aplicación:
`venv\Scripts\python.exe manage.py simular_lineas --cantidad 10`

---

## Contrato 2: Interfaz → Aplicación (API HTTP)

> **Implementación de referencia:** `interfaz/monitor_cuartos_frios.py` es la interfaz Tkinter
> del equipo, conectada a esta API. Su clase `ApiClient` muestra cómo llamar a cada endpoint.

### Reglas generales
- Dirección base: `http://<ip-de-la-pc>:8000/api/`
- Todas las respuestas son **JSON**.
- **Hay que iniciar sesión** con `POST /api/sesion/`. El servidor responde con una cookie de sesión que hay que mandar en cada petición (en Python: `urllib` con `HTTPCookieProcessor`, o `requests.Session`). Sin sesión, la respuesta es `401`.
- Errores: código `400`, `401` o `404` con el cuerpo `{"error": "mensaje explicando qué está mal"}`.
- Fechas: formato ISO 8601 con hora local, ej. `"2026-10-14T10:05:00-06:00"`.
- En los parámetros `desde`/`hasta` se acepta `2026-10-14` o `2026-10-14T10:00:00`.
- Para enviar datos (`POST`), el cuerpo es JSON con el encabezado `Content-Type: application/json`.

### Estados posibles de un sensor o cuarto
Los calcula la aplicación con los límites de cada cuarto. **La interfaz no debe tener límites fijos en su código**: solo colorea según el estado.

| Estado | Significado | Color en la interfaz |
|---|---|---|
| `normal` | Dentro de límites | Azul |
| `advertencia` | Dentro del rango, pero en o por encima de `temp_advertencia` | Amarillo |
| `fuera_de_rango` | Fuera de límites, pero todavía no pasa el tiempo para alertar (ej. puerta abierta) | Rojo |
| `alerta` | Hay una alerta activa | Rojo |
| `sin_datos` | No han llegado datos en `minutos_sin_datos` (alerta registrada) | Gris |

Aparte del estado, cada sensor trae **`en_linea`**: `false` si su último dato tiene más de 30 segundos. Con eso se muestra "Online/Offline" al instante, aunque la alerta "sin datos" se registre hasta los 5 minutos.

El estado de un cuarto es el **peor** de sus sensores.

### Sesión y registro (no requieren sesión previa)

#### `POST /api/sesion/`: iniciar sesión
Cuerpo: `{"usuario": "operador", "contrasena": "..."}`.
Responde `{"usuario": "operador", "es_admin": false}` y la cookie de sesión. Si los datos son incorrectos, responde `400` con `{"error": "Usuario o contraseña incorrectos."}`.

#### `GET /api/sesion/` y `DELETE /api/sesion/`
`GET` dice quién inició sesión (o `401`). `DELETE` cierra la sesión.

#### `POST /api/registro/`: crear cuenta
Cuerpo: `{"usuario": "nuevo", "contrasena": "..."}`. Responde `201`.
- Las cuentas creadas así son de **operador**, no de administrador.
- La contraseña debe cumplir las reglas de Django: al menos 8 caracteres, no solo números, no demasiado común y no parecida al usuario. Si no las cumple, responde `400` con el motivo en español.
- El administrador puede desactivar el registro (`PERMITIR_REGISTRO = False` en `config/settings.py`); entonces responde `403`.

### Endpoints de datos

#### `GET /api/cuartos/`
Lista de cuartos con su último valor. Es la pantalla principal.
```json
{
  "cuartos": [{
    "codigo": "C01", "nombre": "Cuarto frío 1", "activo": true, "estado": "advertencia",
    "temperatura_promedio": 9.12,
    "limites": {"temp_min": 2.0, "temp_advertencia": 10.0, "temp_max": 13.0, "hum_min": 80.0, "hum_max": 95.0},
    "minutos_para_alerta": 10,
    "sensores": [{
      "codigo": "S01", "nombre": "Sensor 1", "descripcion": "Sensor 1", "activo": true,
      "en_linea": true, "estado": "advertencia",
      "temperatura": 10.35, "humedad": 87.2, "fecha_hora": "2026-10-14T10:05:00-06:00"
    }]
  }]
}
```
`temperatura_promedio` solo usa los sensores activos y en línea (`null` si no hay ninguno).

#### `GET /api/cuartos/<codigo>/` y `POST /api/cuartos/<codigo>/`
`GET` regresa un cuarto con el mismo formato de arriba. `POST` cambia sus límites; se manda solo lo que se quiere cambiar:
```json
{"temp_min": 2, "temp_advertencia": 10, "temp_max": 13, "hum_min": 85, "hum_max": 95, "minutos_para_alerta": 15, "nombre": "Cámara lácteos", "activo": true}
```
`temp_advertencia` puede ser `null` (sin advertencia); si tiene valor, debe quedar entre `temp_min` y `temp_max`. Campos desconocidos o límites incoherentes responden `400`.

#### `GET /api/lecturas/`
Historial para las gráficas, ordenado de la lectura más vieja a la más nueva.

| Parámetro | Opcional | Descripción |
|---|---|---|
| `cuarto` | sí | Ej. `C01` |
| `sensor` | sí | Ej. `S01` |
| `desde` | sí | Por defecto, 24 horas antes de `hasta` |
| `hasta` | sí | Por defecto, ahora |
| `limite` | sí | Máximo de filas; por defecto 2000, máximo 20000. Si hay más, se regresan las más recientes y `truncado` vale `true` |

```json
{
  "desde": "...", "hasta": "...", "total": 2, "truncado": false,
  "lecturas": [
    {"cuarto": "C01", "sensor": "S01", "fecha_hora": "2026-10-14T10:04:00-06:00", "temperatura": 4.31, "humedad": 87.0},
    {"cuarto": "C01", "sensor": "S01", "fecha_hora": "2026-10-14T10:05:00-06:00", "temperatura": 4.35, "humedad": 87.2}
  ]
}
```

#### `GET /api/lecturas/por_hora/`
Promedios por hora, para la gráfica de 7 días. Parámetros: `cuarto`, `sensor`, `desde` (por defecto, 7 días antes de `hasta`), `hasta`, `por_sensor` (`true` = un punto por sensor y hora; por defecto, el promedio del cuarto completo).
```json
{
  "desde": "...", "hasta": "...", "agrupado_por": "cuarto",
  "puntos": [
    {"hora": "2026-10-14T09:00:00-06:00", "total_lecturas": 480, "temp_promedio": 8.61, "temp_min": 7.9, "temp_max": 12.4, "hum_promedio": 87.3}
  ]
}
```
Con `por_sensor=true`, cada punto trae además `"sensor": "S01"`.

#### `GET /api/lecturas/resumen/`
Mínimo, máximo y promedio por sensor, más cuántas excursiones y alertas tuvo. Lo usa la página Statistics. Parámetros: `cuarto`, `desde`, `hasta`.
```json
{
  "desde": "...", "hasta": "...",
  "sensores": [{
    "cuarto": "C01", "sensor": "S01", "nombre": "Sensor 1", "total_lecturas": 1440,
    "temperatura": {"min": 7.8, "max": 14.1, "promedio": 8.71},
    "humedad": {"min": 78.2, "max": 90.1, "promedio": 87.5},
    "excursiones": 12, "alertas": 1
  }]
}
```

#### `GET /api/excursiones/`
**Cada salida de rango, aunque dure segundos** (por ejemplo, al abrir la puerta). Es la lista de "Alarmas" de la interfaz.

| Parámetro | Descripción |
|---|---|
| `cuarto`, `sensor`, `tipo` | Filtros |
| `activa` | `true`: el valor sigue fuera de rango |
| `larga` | `true`: duró lo suficiente para volverse alerta |
| `desde`, `hasta` | Por defecto, los últimos 7 días |
| `limite` | Por defecto 100 |

```json
{
  "total": 1,
  "excursiones": [{
    "id": 31, "tipo": "temp_alta", "tipo_texto": "Temperatura alta",
    "cuarto": "C01", "sensor": "S03", "sensor_nombre": "Sensor 3",
    "inicio": "2026-10-14T10:05:00-06:00", "fin": "2026-10-14T10:05:40-06:00", "activa": false,
    "duracion_seg": 40, "valor_limite": 13.0, "valor_extremo": 13.8, "unidad": "°C",
    "es_larga": false, "alerta_id": null
  }]
}
```
**Excursión o alerta:** toda salida de rango es una excursión. Si dura `minutos_para_alerta` minutos seguidos, además se crea una alerta, y la excursión queda con `es_larga: true` y el `alerta_id`.

#### `GET /api/alertas/`
| Parámetro | Descripción |
|---|---|
| `activa` | `true`: el problema sigue; `false`: ya se normalizó |
| `atendida` | `true` / `false`: si un operador ya la revisó |
| `cuarto`, `tipo` | Filtros. Tipos: `temp_alta`, `temp_baja`, `hum_alta`, `hum_baja`, `sin_datos` |
| `limite` | Por defecto 200 |

```json
{
  "total": 1,
  "alertas": [{
    "id": 15, "tipo": "temp_alta", "tipo_texto": "Temperatura alta",
    "cuarto": "C01", "sensor": "S01", "sensor_nombre": "Sensor 1",
    "mensaje": "Cuarto frío 1 (C01/S01): temperatura alta durante 10 min o más. Valor: 14.2 °C (máximo permitido: 13.0 °C).",
    "inicio": "2026-10-14T10:05:00-06:00", "fin": null, "activa": true, "duracion_seg": 742,
    "valor_limite": 13.0, "valor_extremo": 15.5,
    "atendida": false, "atendida_en": null, "atendida_por": "", "nota": ""
  }]
}
```
Una alerta **activa** se cierra sola cuando el valor regresa al rango. **Atendida** significa que un operador la revisó; son dos cosas distintas.

#### `POST /api/alertas/<id>/atender/`
Marca una alerta como atendida. Cuerpo opcional: `{"nota": "Se cerró la puerta"}`. Regresa la alerta actualizada.

#### `GET /api/configuracion/` y `POST /api/configuracion/`
```json
{"modo": "simulador", "puerto_serial": "COM3", "baudrate": 115200,
 "intervalo_guardado_seg": 60, "dias_retencion": 60, "minutos_sin_datos": 5, "sensores_simulados": 8}
```
`modo` puede ser `simulador` o `serial`; `sensores_simulados` va de 1 a 20. Con `POST` se manda solo lo que se cambia, y el lector lo aplica en unos 5 segundos.

#### `GET /api/estado/`
Estado de la conexión con el STM32:
```json
{
  "hora_servidor": "...",
  "lector": {"modo": "serial", "conectado": true, "puerto": "COM3", "error": null,
             "mensajes_validos": 5321, "mensajes_invalidos": 3, "ultimo_error_protocolo": "Checksum incorrecto: ..."},
  "configuracion": { ... }
}
```

#### `GET /api/stream/`: datos en vivo (Server-Sent Events)
Se mantiene abierta y envía un evento cada vez que algo cambia:
```js
const fuente = new EventSource('/api/stream/');
fuente.onmessage = (e) => {
  const datos = JSON.parse(e.data);
  // datos.sensores["C01/S01"] -> {cuarto, sensor, nombre, en_linea, temperatura, humedad, fecha_hora, estado}
  // datos.lector              -> igual que en /api/estado/
  // datos.alertas_version     -> si cambió respecto al evento anterior, volver a pedir /api/alertas/ y /api/excursiones/
};
```
Al conectarse llega el estado actual. Después llegan los cambios. Los sensores aparecen en `sensores` cuando mandan su primer dato desde que arrancó el servidor; para la carga inicial, usar `/api/cuartos/`.
