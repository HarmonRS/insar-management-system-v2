# INIT

## 1. 当前开发机已确认的本机环境

- Windows 开发机 Python 解释器：`C:\ProgramData\anaconda3\envs\InSAR\python.exe`
- 当前项目运行时应优先从 `.env` 读取路径，不允许在代码中写死开发机盘符或目录。
- 与 D-InSAR / ISCE2 / WSL 相关的运行目录、输出目录、轨道目录、DEM 路径都应通过 `.env` 配置维护。

## 2. 本轮已落地的修复

### 2.1 D-InSAR 生产与产物任务日志管理

- 后端新增任务运行日志专用路由：
  - `backend/app/routers/tasks_runtime.py`
- `backend/app/routers/tasks.py` 改为兼容导出入口。
- `backend/app/services/task_service.py` 新增：
  - 单条任务日志删除
  - 当前任务日志清空
- 前端已接入：
  - `frontend/src/api/tasks.js`
  - `frontend/src/DinsarProductionPanel.jsx`
  - `frontend/src/DinsarProductsPanel.jsx`

当前已支持：

- 查看任务日志
- 删除单条任务日志
- 清空当前任务全部日志

### 2.2 D-InSAR 提交链路与运行时问题

- 修复 `backend/app/services/job_handlers.py` 中缺失 `datetime` 导入导致的控制器异常。
- 修复 D-InSAR 运行时目录处理逻辑，避免继续依赖历史开发机 `Z:\` 路径。
- 当前原则：路径统一走 `.env`，不允许把开发机专用盘符写入跟踪代码。

### 2.3 前端乱码修复

本轮已清理以下面板中的残留乱码与混杂英文：

- `frontend/src/DinsarProductionPanel.jsx`
- `frontend/src/DinsarProductsPanel.jsx`

日志管理原文件 `frontend/src/LogManagementPanel.jsx` 本身存在历史乱码内容，而且开发过程中目标文件一度被占用，当前采用的稳定方案是：

- 新增干净版本：`frontend/src/LogManagementPanel.clean.jsx`
- 由 `frontend/src/HealthCheckPanel.jsx` 暂时改为导入 `LogManagementPanel.clean`

这样做的目的：

- 先保证页面可用、无乱码
- 不在文件被占用时强行覆盖旧文件
- 后续如果旧文件占用解除，再考虑把 clean 版本回收为正式文件名

## 3. PowerShell 使用注意事项

### 3.1 查看中文文件时

Windows PowerShell 5.x 下，终端读取 UTF-8 中文文件时可能出现乱码，这不一定代表源码文件本身已坏。

推荐先执行：

```powershell
chcp 65001
$OutputEncoding = [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
```

如果仍然显示异常，优先用编辑器直接查看源码，不要仅凭 PowerShell 控制台输出判断文件编码是否损坏。

### 3.2 写入源码文件时

Windows PowerShell 5.x 的 `Set-Content -Encoding UTF8` 默认会写入 BOM。对脚本、配置或前端源码，BOM 可能继续引出兼容问题。

因此本项目内不建议直接用这类命令覆盖源码文件。更稳妥的方式：

- 优先使用编辑器或补丁方式修改源码
- 如果必须在 PowerShell 中写文件，使用无 BOM UTF-8

示例：

```powershell
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText("D:\Code\Insar_management_system_v2\somefile.txt", $content, $utf8NoBom)
```

## 4. WSL / ISCE2 相关约束

- 当前项目已经按“Windows 业务登记 + WSL 执行 ISCE2”模式接入。
- 是否继续维持双环境桥接，只能作为 fallback 方案讨论；默认正式链路仍应以单一、可维护、可配置的环境为主。
- 后续若继续收敛环境，优先原则是：
  - 先保证正式生产链稳定
  - 再讨论实验期环境是否下线
  - 所有环境差异都必须记录到文档，不能只留在口头结论里

## 5. 本轮验证结果

- 前端已重新执行 `npm run build`，构建通过。
- 当前界面实际已恢复以下中文区域：
  - 日志管理
  - 运行监控
  - 处理模板
  - D-InSAR 产物说明文案

