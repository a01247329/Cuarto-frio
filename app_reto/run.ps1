# Arranca el servidor de monitoreo en el puerto 8000 (accesible desde la red local)
Write-Host "Iniciando monitoreo de cuartos frios en http://localhost:8000 ..." -ForegroundColor Green
& "$PSScriptRoot\venv\Scripts\python.exe" "$PSScriptRoot\manage.py" runserver 0.0.0.0:8000
