@echo off
setlocal enabledelayedexpansion

:: =====================================================================
:: InSAR Management System - Conda Environment Packing Script
:: 该脚本用于将开发环境打包成离线可用的绿色版压缩包
:: =====================================================================

set ENV_NAME=InSAR
set OUTPUT_FILE=insar_env_packed.zip

echo [1/3] 正在检查 conda-pack 是否安装...
conda-pack --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] 未检测到 conda-pack, 正在尝试安装...
    pip install conda-pack
)

echo [2/3] 正在清理 Conda 缓存以减小包体积...
call conda clean -y --all

echo [3/3] 正在将环境 %ENV_NAME% 打包为 %OUTPUT_FILE%...
echo [*] 这可能需要几分钟时间，请稍候...

:: 使用 zip 格式在 Windows 上有更好的兼容性
if exist %OUTPUT_FILE% del %OUTPUT_FILE%
conda pack -n %ENV_NAME% -o %OUTPUT_FILE% --format zip --compress-level 9

if %errorlevel% equ 0 (
    echo.
    echo =====================================================================
    echo [+] 环境打包成功: %OUTPUT_FILE%
    echo [+] 你可以将此文件拷贝到内网服务器，解压后即可直接使用。
    echo [+] 提示: 解压后运行目录下的 scripts\python.exe 即可调用该环境。
    echo =====================================================================
) else (
    echo.
    echo [!] 打包失败，请检查环境 %ENV_NAME% 是否存在或是否被占用。
)

pause
