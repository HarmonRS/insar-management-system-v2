# 项目代码审计报告（2026-03-04）

审计范围：后端核心链路、前端鉴权调用链、启动与脚本安全边界。  
审计方式：静态只读审计（未修改业务代码）。

## 1. 高危问题

### 1.1 ENVI 主流程成功后不返回结果，导致下游按 `dict` 使用时崩溃
- 证据：
  - `backend/app/services/envi_service.py:1568`
  - `backend/app/services/envi_service.py:1630`
  - `backend/app/services/envi_runner_cli.py:44`
  - `backend/app/services/envi_runner_cli.py:51`
  - `backend/app/services/job_handlers.py:1020`
  - `backend/app/services/job_handlers.py:1031`
- 影响：任务可能在实际执行后仍被标记失败或抛异常。

### 1.2 只读用户可通过 `GET` 触发写操作和高开销构建，绕过读写分离意图
- 证据：
  - `backend/app/routers/radar.py:619`
  - `backend/app/routers/radar.py:365`
  - `backend/app/routers/radar.py:346`
  - `backend/app/routers/dinsar.py:214`
  - `backend/app/routers/dinsar.py:146`
  - `backend/app/routers/dependencies.py:196`
  - `backend/app/routers/dependencies.py:315`
- 影响：读账号可诱发缓存重建/数据库写入，存在资源滥用风险。

### 1.3 AOI token 内存存储无容量上限，且 token 可续期
- 证据：
  - `backend/app/routers/dependencies.py:131`
  - `backend/app/routers/dependencies.py:825`
  - `backend/app/routers/dependencies.py:850`
  - `backend/app/routers/dependencies.py:59`
  - `backend/app/routers/radar.py:439`
  - `backend/app/routers/radar.py:509`
- 影响：认证后低权限账号可通过频繁检索制造进程内存膨胀（DoS 面）。

## 2. 中危问题

### 2.1 `extract_disp_results` 失败计数重复累加
- 证据：
  - `backend/app/services/envi_service.py:1717`
  - `backend/app/services/envi_service.py:1726`
  - `backend/app/services/envi_service.py:1729`
- 影响：统计报表和告警判断失真。

### 2.2 AOI 文件解析异常未统一转为 4xx，用户输入可触发 500
- 证据：
  - `backend/app/routers/dependencies.py:792`
  - `backend/app/routers/dependencies.py:800`
- 影响：可用性下降，错误语义不一致。

### 2.3 路径归属判断使用 `startswith`，存在前缀误匹配
- 证据：
  - `backend/app/services/data_service.py:530`
- 影响：可能将不属于监控根目录的路径误判为合法。

### 2.4 解包安全校验仅检查 `member.name`，仍使用 `extractall`
- 证据：
  - `scripts/unpack_archives.py:98`
  - `scripts/unpack_archives.py:107`
  - `scripts/unpack_archives.py:181`
- 影响：若归档文件不可信，仍可能存在目录逃逸/覆盖风险（尤其符号链接/硬链接场景）。

## 3. 低危问题

### 3.1 启动脚本写 `nginx.conf` 未显式编码
- 证据：
  - `scripts/start_app.ps1:388`
- 影响：在 Windows PowerShell 5 环境可能写出 UTF-16，导致 Nginx 配置解析异常。

## 4. 已复核并排除

### 4.1 `task_service.create_task` 的 `task_id` 未定义问题为误报
- 证据：
  - `backend/app/services/task_service.py:153`
- 结论：`task_id` 已正常赋值。

## 5. 假设与待确认

1. 解包问题是否上调为“高危”，取决于归档输入是否可被外部/低信任来源控制。  
2. “只读账号触发缓存构建”是否视为权限缺陷，取决于产品策略是否允许此行为。

## 6. 说明

- 本报告对应一次只读审计，不包含代码修复提交。  
- 可在确认后按优先级先修复：1.1 / 1.2 / 1.3。

