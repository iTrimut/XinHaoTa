@echo off
REM 信号塔 XinHaoTa - 远程访问一键启动
REM 启动: 平台(8090) + 密码代理(8095) + cloudflared 公网隧道(需自备 cloudflared)
chcp 65001 >nul
echo 正在启动信号塔 XinHaoTa(含远程访问)...
cd /d "%~dp0"
python scripts\start_remote.py
pause
