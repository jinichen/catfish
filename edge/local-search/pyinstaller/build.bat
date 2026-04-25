@echo off
rem 在 Windows 上打 catfish-search.exe 单文件包。
rem 产物：dist\catfish-search.exe
rem
rem 优先用 Python 3.12（找不到往下退到 3.11 / 3.10）。
rem 这个 Python 会被塞进 exe 里，员工机器上是否装 Python 跟打包无关。

setlocal enabledelayedexpansion

cd /d "%~dp0.."

echo [1/5] 选 Python
set PYTHON_CMD=
for %%v in (3.12 3.11 3.10) do (
    py -%%v --version >nul 2>&1
    if not errorlevel 1 (
        set PYTHON_CMD=py -%%v
        echo     使用 py -%%v
        goto py_found
    )
)
echo 错误：找不到 Python 3.10+（推荐 3.12）。
echo 从 https://www.python.org/downloads/ 下一个 Python 3.12 安装。
echo 安装时务必勾上 "Install launcher for all users"。
exit /b 1

:py_found

echo [2/5] 准备 venv
rem 如果旧 venv 用的是别的 Python 版本，删掉重建。
if exist ".build-venv" (
    .build-venv\Scripts\python --version | findstr /r "3.1[0-2]" >nul
    if errorlevel 1 (
        echo     旧 venv 版本不匹配，重建
        rmdir /s /q .build-venv
    )
)
if not exist ".build-venv" (
    %PYTHON_CMD% -m venv .build-venv
    if errorlevel 1 (
        echo 创建 venv 失败。
        exit /b 1
    )
)
call .build-venv\Scripts\activate.bat

echo [3/5] 装依赖
python -m pip install -q -U pip
python -m pip install -q -U pyinstaller pyyaml watchdog "markitdown[all]"

echo [4/5] 清 dist / build
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build

echo [5/5] PyInstaller 打包
pyinstaller --clean --noconfirm pyinstaller\catfish-search.spec
if errorlevel 1 (
    echo 打包失败。
    exit /b 1
)

if exist "dist\catfish-search.exe" (
    echo.
    echo 完成。
    echo 产物：dist\catfish-search.exe
    python --version
    echo 验证：dist\catfish-search.exe --help
) else (
    echo 构建失败，请看上面的 PyInstaller 输出。
    exit /b 1
)
