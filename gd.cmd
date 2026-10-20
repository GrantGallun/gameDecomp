@echo off
rem From Windows: run ./gd inside WSL, in this directory. Example: gd status
wsl.exe --cd "%~dp0." -e bash -l ./gd %*
