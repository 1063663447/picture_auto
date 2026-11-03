@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "PY="

rem ---- 1. 找 Python：py 启动器 / PATH 里的 python / 常见安装目录 ----
for %%C in ("py -3" "python") do (
  if not defined PY (
    %%~C -c "import sys" >nul 2>nul && set "PY=%%~C"
  )
)
if not defined PY (
  for %%D in (
    "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    "C:\Python313\python.exe"
    "C:\Python312\python.exe"
  ) do (
    if not defined PY if exist %%D set "PY=%%~D"
  )
)

if not defined PY (
  echo.
  echo [x] 没有找到可用的 Python 3。
  echo     Python 3 not found. Install it once:
  echo       winget install -e --id Python.Python.3.12
  echo     or https://www.python.org/downloads/  ^(check "Add to PATH"^)
  echo.
  pause
  exit /b 1
)

echo [*] 使用 Python: %PY%

rem ---- 2. 依赖检查（缺失才装） ----
%PY% -c "import fastapi,uvicorn,httpx,PIL,multipart" >nul 2>nul
if errorlevel 1 (
  echo [*] 首次运行，安装依赖 fastapi / uvicorn / httpx / Pillow / python-multipart ...
  %PY% -m pip install --disable-pip-version-check -q -r requirements.txt
  if errorlevel 1 (
    echo [*] 默认源失败，改用清华镜像重试 ...
    %PY% -m pip install --disable-pip-version-check -q -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
  )
  %PY% -c "import fastapi,uvicorn,httpx,PIL,multipart" >nul 2>nul
  if errorlevel 1 (
    echo [x] 依赖安装失败，请手动执行: %PY% -m pip install -r requirements.txt
    pause
    exit /b 1
  )
)

rem ---- 3. 启动工作台 ----
%PY% serve.py %*
if errorlevel 1 pause
endlocal
