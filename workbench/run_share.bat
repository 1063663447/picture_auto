@echo off
chcp 936 >nul
setlocal
cd /d %~dp0

rem ============ 共享模式：一台机器当服务端，同事用浏览器访问 ============
rem 任务、并发、出图全部在这一台机器上排队执行；别人只提交和下载。

set "PY="
for %%P in (python.exe) do if not defined PY if exist "%%~$PATH:P" set "PY=%%~$PATH:P"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY if exist "C:\Python312\python.exe" set "PY=C:\Python312\python.exe"
if not defined PY (
  echo [x] 没找到 Python，请先安装 Python 3.11+ 并勾选 Add to PATH
  pause
  exit /b 1
)

rem 缺依赖就装（首次运行）
"%PY%" -c "import fastapi, uvicorn, httpx, PIL" 2>nul
if errorlevel 1 (
  echo [*] 首次运行，安装依赖...
  "%PY%" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo [*] 直连失败，换清华镜像重试...
    "%PY%" -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
  )
)

"%PY%" serve.py --share --open %*
pause
