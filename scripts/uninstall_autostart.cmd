@echo off
REM 信号塔 XinHaoTa - 取消开机自启(停止后台服务)
REM 双击运行：移除登录自启项并停止当前后台运行的平台
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   信号塔 XinHaoTa - 取消开机自启
echo ============================================
python "%~dp0autostart.py" uninstall
echo.
pause
