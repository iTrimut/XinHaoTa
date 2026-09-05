@echo off
REM 信号塔 XinHaoTa - Web 一键启动
REM 双击本脚本即可在浏览器打开看板
chcp 65001 >nul
cd /d "%~dp0"
echo 正在启动信号塔 XinHaoTa ...
python web_server.py 8090
REM 若上面阻塞说明服务已在前台运行
