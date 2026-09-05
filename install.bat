@echo off
REM ============================================================
REM  信号塔 XinHaoTa - 一键安装(Windows)
REM  作用: 检查 Python -> (可选)装远程二维码依赖 -> 启动并开浏览器
REM ============================================================
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   信号塔 XinHaoTa  一键安装 / 启动
echo ============================================

REM 1) 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
  echo [X] 没找到 Python。请先安装 Python 3.10+ 并勾选 "Add to PATH"：
  echo      https://www.python.org/downloads/
  pause
  exit /b 1
)
echo [OK] Python 已安装:
python -c "import sys;print('   ',sys.version.split()[0])"

REM 2) 可选依赖：远程二维码(qrcode)。只有要用"远程访问-二维码"才需要。
echo.
echo [i] 远程访问的二维码功能需要 qrcode 库，正在安装(可跳过错误)...
python -m pip install --quiet qrcode 2>nul
echo [i] 依赖检查完成。

REM 3) 启动服务并在浏览器打开
echo.
echo [OK] 正在启动信号塔... 首次请稍候(会自动拉取自选股行情)
start http://127.0.0.1:8090/
python web_server.py 8090

pause
