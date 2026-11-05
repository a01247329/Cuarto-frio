@echo off
rem Doble clic aqui para arrancar el servidor y la interfaz
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0iniciar_demo.ps1"
if errorlevel 1 pause
