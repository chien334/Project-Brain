@echo off
SETLOCAL EnableDelayedExpansion

echo =============================================
echo   ProjectBrain Codegraph Synchronization CLI
echo =============================================

:: Default Configuration
set DEFAULT_SERVER_URL=http://5.104.85.38:8080
set DEFAULT_PROJECT_PATH=.

:: Check Python
where python >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo Error: Python is not installed or not in PATH.
    exit /b 1
)

:: 1. Project ID
set PROJECT_ID=%1
if "%PROJECT_ID%"=="" (
    set /p PROJECT_ID="Enter Project ID: "
    if "!PROJECT_ID!"=="" (
        echo Error: Project ID is required.
        exit /b 1
    )
)

:: 2. Server URL
set SERVER_URL=%2
if "%SERVER_URL%"=="" (
    set /p SERVER_URL="Enter Server URL [%DEFAULT_SERVER_URL%]: "
    if "!SERVER_URL!"=="" set SERVER_URL=%DEFAULT_SERVER_URL%
)

:: 3. Project Path
set PROJECT_PATH=%3
if "%PROJECT_PATH%"=="" (
    set /p PROJECT_PATH="Enter Local Project Path [%DEFAULT_PROJECT_PATH%]: "
    if "!PROJECT_PATH!"=="" set PROJECT_PATH=%DEFAULT_PROJECT_PATH%
)

:: 4. Branch Detection
set BRANCH=%4
if "%BRANCH%"=="" (
    :: Detect git branch if available
    set DETECTED_BRANCH=main
    if exist "%PROJECT_PATH%\.git" (
        for /f "tokens=*" %%i in ('git -C "%PROJECT_PATH%" rev-parse --abbrev-ref HEAD 2^>nul') do set DETECTED_BRANCH=%%i
    )
    set /p BRANCH="Enter Git Branch [!DETECTED_BRANCH!]: "
    if "!BRANCH!"=="" set BRANCH=!DETECTED_BRANCH!
)

:: 5. Sync Memories
set SYNC_MEMORIES=%5
if "%SYNC_MEMORIES%"=="" (
    set /p SYNC_ANS="Sync codebase files as memories to RAG? (y/n) [y]: "
    if "!SYNC_ANS!"=="" set SYNC_ANS=y
    if /i "!SYNC_ANS!"=="y" (
        set SYNC_MEMORIES=--sync-memories
    ) else (
        set SYNC_MEMORIES=
    )
)

echo.
echo Sync Configuration:
echo -------------------
echo Project ID:   %PROJECT_ID%
echo Server URL:   %SERVER_URL%
echo Project Path: %PROJECT_PATH%
echo Branch:       %BRANCH%
echo Sync Files:   %SYNC_MEMORIES%
echo -------------------
echo Running sync...
echo.

python -m projectbrain.main codegraph-sync %PROJECT_ID% %SERVER_URL% %PROJECT_PATH% %BRANCH% %SYNC_MEMORIES%

pause
