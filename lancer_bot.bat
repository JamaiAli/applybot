@echo off
REM Lance le bot Telegram ApplyPilot. Laisse cette fenetre ouverte tant que
REM tu veux que le bot reponde. Ferme-la pour arreter le bot.
cd /d "%~dp0"
title ApplyPilot - bot Telegram
echo Demarrage du bot Telegram ApplyPilot...
echo (Laisse cette fenetre ouverte. Ferme-la pour arreter le bot.)
echo.
".venv\Scripts\python.exe" -m applypilot.telegram_bot
echo.
echo Le bot s'est arrete. Appuie sur une touche pour fermer.
pause >nul
