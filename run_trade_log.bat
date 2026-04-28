@echo off
chcp 65001
REM ===================================
REM 自动启动 Python 期权交易日志
REM ===================================

REM 进入项目目录
cd /d D:\apps\OptionsTradingLog

REM 激活虚拟环境
call .venv\Scripts\activate

REM 提示
echo 正在启动期权交易日志，按 Ctrl+C 可退出...

REM 运行 Python 脚本
streamlit run app.py

REM 脚本结束后提示
echo 脚本已退出
pause