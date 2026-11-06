# Arranca el servidor (con el simulador) y abre la interfaz.
# Al cerrar la ventana de la interfaz, el servidor se apaga solo.
$ErrorActionPreference = 'Stop'
$raiz = $PSScriptRoot
$python = Join-Path $raiz 'venv\Scripts\python.exe'
$url = 'http://127.0.0.1:8000'

function Salir-ConError($mensaje) {
    Write-Host $mensaje -ForegroundColor Red
    Read-Host 'Presiona Enter para salir'
    exit 1
}

# Busca un Python instalado en esta PC (el lanzador "py" primero, luego "python")
function Buscar-Python {
    foreach ($cand in @(@('py', '-3'), @('python'), @('python3'))) {
        $exe = $cand[0]
        $extra = @($cand | Select-Object -Skip 1)
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        try {
            $ver = & $exe @extra -c "import sys; print(sys.version_info[0]*100 + sys.version_info[1])" 2>$null
            if ($LASTEXITCODE -eq 0 -and [int]$ver -ge 310) { return , $cand }
        } catch { }
    }
    return $null
}

# El venv guarda la ruta del Python con el que se creo, asi que uno copiado de otra PC no sirve.
# Si falta o esta roto, se vuelve a crear aqui con el Python de esta PC.
$venvOk = $false
if (Test-Path $python) {
    try {
        & $python -c "import django, serial, tkinter" 2>$null | Out-Null
        $venvOk = ($LASTEXITCODE -eq 0)
    } catch { $venvOk = $false }
}

if (-not $venvOk) {
    Write-Host 'Preparando el entorno de Python (solo la primera vez en esta PC, puede tardar)...' -ForegroundColor Yellow
    $base = Buscar-Python
    if (-not $base) {
        Salir-ConError "No se encontro Python 3.10 o mas nuevo. Instalalo desde https://www.python.org/downloads/ (marca 'Add python.exe to PATH') y vuelve a abrir este archivo."
    }
    $exe = $base[0]; $extra = @($base | Select-Object -Skip 1)
    & $exe @extra -m venv --clear (Join-Path $raiz 'venv')
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $python)) { Salir-ConError 'No se pudo crear el entorno virtual.' }
    & $python -m pip install --disable-pip-version-check -r (Join-Path $raiz 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { Salir-ConError 'No se pudieron instalar las dependencias (revisa la conexion a internet).' }
    $tkOk = $false
    try { & $python -c "import tkinter" 2>$null | Out-Null; $tkOk = ($LASTEXITCODE -eq 0) } catch { }
    if (-not $tkOk) { Salir-ConError "Este Python no trae tkinter. Reinstala Python marcando 'tcl/tk and IDLE'." }
}

# Crea o actualiza la base de datos (no hace nada si ya esta al dia)
Push-Location $raiz
try {
    & $python manage.py migrate --noinput | Out-Null
    if ($LASTEXITCODE -ne 0) { Salir-ConError 'Fallo la preparacion de la base de datos (manage.py migrate).' }
} finally { Pop-Location }

Write-Host 'Iniciando servidor de monitoreo...' -ForegroundColor Green
# La ventana del servidor queda minimizada; ahi se ven los mensajes del lector
$servidor = Start-Process -FilePath $python -ArgumentList @('manage.py', 'runserver', '127.0.0.1:8000', '--noreload') `
    -WorkingDirectory $raiz -WindowStyle Minimized -PassThru

try {
    # Espera a que el servidor responda (401 tambien cuenta: significa que ya esta escuchando)
    $listo = $false
    for ($i = 0; $i -lt 30 -and -not $listo; $i++) {
        try {
            Invoke-WebRequest -UseBasicParsing "$url/api/sesion/" -TimeoutSec 2 | Out-Null
            $listo = $true
        } catch {
            if ($_.Exception.Response) { $listo = $true } else { Start-Sleep -Seconds 1 }
        }
        if ($servidor.HasExited) { break }
    }
    if (-not $listo) {
        Write-Host 'El servidor no arranco. Abre la ventana minimizada para ver el error.' -ForegroundColor Red
        Read-Host 'Presiona Enter para salir'
        exit 1
    }

    Write-Host 'Abriendo la interfaz...' -ForegroundColor Green
    $env:MONITOREO_SERVIDOR = $url
    & $python (Join-Path $raiz 'interfaz\monitor_cuartos_frios.py')
}
finally {
    if (-not $servidor.HasExited) {
        Write-Host 'Apagando servidor...'
        Stop-Process -Id $servidor.Id -Force -ErrorAction SilentlyContinue
    }
}
