# Arranca el servidor (con el simulador) y abre la interfaz.
# Al cerrar la ventana de la interfaz, el servidor se apaga solo.
$ErrorActionPreference = 'Stop'
$raiz = $PSScriptRoot
$python = Join-Path $raiz 'venv\Scripts\python.exe'
$url = 'http://127.0.0.1:8000'

if (-not (Test-Path $python)) {
    Write-Host "No existe el entorno virtual. Revisa README.md, seccion 'Primera vez en una PC nueva'." -ForegroundColor Red
    Read-Host 'Presiona Enter para salir'
    exit 1
}

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
