@echo off
SETLOCAL EnableDelayedExpansion

:: Corporate Proxy Bypass Configuration
set NO_PROXY=localhost,127.0.0.1,192.168.0.0/16,10.0.0.0/8,172.16.0.0/12,5.104.85.38
set no_proxy=localhost,127.0.0.1,192.168.0.0/16,10.0.0.0/8,172.16.0.0/12,5.104.85.38

echo =============================================
echo   ProjectBrain Codegraph Synchronization CLI
echo =============================================

:: Default Configuration
set DEFAULT_SERVER_URL=http://localhost:8080
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
) else (
    if /i "%SYNC_MEMORIES%"=="y" set SYNC_MEMORIES=--sync-memories
    if /i "%SYNC_MEMORIES%"=="true" set SYNC_MEMORIES=--sync-memories
    if /i "%SYNC_MEMORIES%"=="1" set SYNC_MEMORIES=--sync-memories
    if /i "%SYNC_MEMORIES%"=="--sync-memories" set SYNC_MEMORIES=--sync-memories
)

:: 6. Sync DB File Upload
set UPLOAD_DB=%6
if "%UPLOAD_DB%"=="" (
    set /p DB_ANS="Use SQLite database file upload instead of JSON payload (bypasses WAF blocks)? (y/n) [n]: "
    if "!DB_ANS!"=="" set DB_ANS=n
    if /i "!DB_ANS!"=="y" (
        set UPLOAD_DB=--upload-db
    ) else (
        set UPLOAD_DB=
    )
) else (
    if /i "%UPLOAD_DB%"=="y" set UPLOAD_DB=--upload-db
    if /i "%UPLOAD_DB%"=="true" set UPLOAD_DB=--upload-db
    if /i "%UPLOAD_DB%"=="1" set UPLOAD_DB=--upload-db
    if /i "%UPLOAD_DB%"=="--upload-db" set UPLOAD_DB=--upload-db
)

:: 7. Pure Python Parser Toggle
set USE_PURE_PYTHON=%7
if "%USE_PURE_PYTHON%"=="" (
    set /p PY_ANS="Use pure Python codebase scanner instead of native codegraph CLI? (y/n) [y]: "
    if "!PY_ANS!"=="" set PY_ANS=y
    if /i "!PY_ANS!"=="y" (
        set PB_USE_PURE_PYTHON_PARSER=true
    ) else (
        set PB_USE_PURE_PYTHON_PARSER=false
    )
) else (
    if "%USE_PURE_PYTHON%"=="true" (
        set PB_USE_PURE_PYTHON_PARSER=true
    ) else if "%USE_PURE_PYTHON%"=="1" (
        set PB_USE_PURE_PYTHON_PARSER=true
    ) else if "%USE_PURE_PYTHON%"=="y" (
        set PB_USE_PURE_PYTHON_PARSER=true
    ) else (
        set PB_USE_PURE_PYTHON_PARSER=false
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
echo Upload DB:    %UPLOAD_DB%
echo Pure Python:  %PB_USE_PURE_PYTHON_PARSER%
echo -------------------
echo Running sync...
echo.

python -m projectbrain.main codegraph-sync %PROJECT_ID% %SERVER_URL% %PROJECT_PATH% %BRANCH% %SYNC_MEMORIES% %UPLOAD_DB%

pause
