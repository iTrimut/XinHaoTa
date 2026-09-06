@echo off
REM 信号塔 XinHaoTa - 安装开机自启(后台常驻)
REM 双击运行：登录后自动后台启动平台，随时打开 http://127.0.0.1:8090 即可用
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   信号塔 XinHaoTa - 安装开机自启
echo ============================================
python "%~dp0autostart.py" install
echo.
echo [提示] 已常驻后台。取消自启请运行同目录的 uninstall_autostart.cmd
pause
