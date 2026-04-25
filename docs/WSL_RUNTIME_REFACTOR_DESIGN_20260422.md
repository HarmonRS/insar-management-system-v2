# WSL Runtime 改造设计 2026-04-22

## 1. 背景

当前 `ISCE2` 与 `Gamma/PyINT` 的 WSL 执行存在三个根问题：

1. 执行边界仍是 shell 字符串，后端直接把命令交给 `wsl.exe ... bash -lc`。
2. `.env` 直接持有 WSL Python、pipeline、Gamma 环境脚本等执行型配置，配置权和执行权耦合。
3. WSL 内的 conda 环境没有被定义成正式运行时资产，实际运行依赖“当前机器上碰巧可用的环境”。

这会带来四类风险：

- 参数或路径一旦逃逸 quote，直接进入 shell 解释边界。
- 引擎服务自己拼命令，执行治理无法集中落地。
- `ISCE2` 和 `Gamma/PyINT` 虽然物理上可以共用一套 WSL/conda，但逻辑上没有独立运行时标识，审计和迁移困难。
- WSL 环境漂移无法被健康检查和运维流程正式识别。

## 2. 目标

本次改造采用：

- 单 WSL distro
- 单共享 conda env
- 双逻辑 runtime
- 单一 WSL broker 出口

目标形态：

```text
业务服务
-> Engine Adapter
-> WslBroker
-> RuntimeRegistry
-> JobManifest
-> WSL Runner
-> shared conda env: insar_wsl_v1
```

其中：

- `ISCE2` 使用 `isce2_runtime_v1`
- `Gamma/PyINT` 使用 `gamma_pyint_runtime_v1`

二者可以共用：

- `WSL_DISTRO=Ubuntu-24.04`
- `WSL_SHARED_CONDA_ENV=insar_wsl_v1`

但不能共用“逻辑运行时定义”。

## 3. 运行时模型

### 3.1 共享运行时

共享运行时负责：

- distro 选择
- 共享 conda env 名称
- 共享 Python 路径
- broker 作业落盘目录

建议配置：

```env
WSL_DISTRO=Ubuntu-24.04
WSL_SHARED_CONDA_ENV=insar_wsl_v1
WSL_SHARED_PYTHON=/home/administrator/miniconda3/envs/insar_wsl_v1/bin/python
WSL_BROKER_JOB_ROOT=<backend/runtime/wsl_jobs>
```

### 3.2 逻辑 runtime

每个逻辑 runtime 固定：

- `runtime_id`
- `engine_code`
- `runner`
- `allowed_operations`
- 固定 profile 路径
- 固定审计标识

当前规划：

| runtime_id | engine | runner | 说明 |
| --- | --- | --- | --- |
| `isce2_runtime_v1` | `isce2` | `deploy/wsl/runners/isce2_runner.py` | LT-1 条带 D-InSAR |
| `gamma_pyint_runtime_v1` | `pyint` | `deploy/wsl/runners/gamma_pyint_runner.py` | Gamma / PyINT |

Gamma 固定 profile：

- `deploy/wsl/profiles/gamma_env.sh`

注意：Gamma profile 属于部署资产，不再建议继续由 `.env` 动态指定脚本路径。

## 4. 执行模型

### 4.1 Broker

Broker 负责：

- 解析 `runtime_id`
- 写入结构化 `job manifest`
- 生成固定 `argv`
- 通过 `wsl.exe --exec` 执行 runner

禁止的旧模式：

- 服务层直接拼 `bash -lc "..."`
- 服务层直接决定 `source` 哪个环境脚本
- 服务层直接把 `.env` 中的 Python 绝对路径当成执行入口

### 4.2 Manifest

建议 manifest 固定包含：

- `job_id`
- `runtime_id`
- `engine_code`
- `operation`
- `created_at`
- `payload`

payload 中再放：

- 输入路径
- 工作路径
- 输出路径
- 参数
- 操作人
- 引擎内部业务上下文

### 4.3 Runner

Runner 是 WSL 内的固定可信入口。

阶段划分：

1. V1：只承担 manifest 读取、参数校验和 dry-run 输出
2. V2：接入实际 pipeline / PyINT / Gamma
3. V3：补齐固定 profile、版本指纹、错误分类与审计摘要

## 5. Conda 环境治理

### 5.1 基本原则

- 不原地继续污染旧环境
- 先冻结现有环境
- 再建立共享正式环境 `insar_wsl_v1`
- 旧环境只保留回滚用途

### 5.2 版本资产

`deploy/wsl/conda/` 下至少维护：

- `insar_wsl_v1.environment.yml`
- `insar_wsl_v1.explicit.lock`
- `insar_wsl_v1.fingerprint.json`

说明：

- `environment.yml` 用于人工维护和审阅
- `explicit.lock` 用于精确重建
- `fingerprint.json` 用于健康检查、审计和漂移检测

### 5.3 Gamma

Gamma 通常不作为 conda 包管理。

因此建议：

- Gamma 以固定安装路径存在于 WSL
- 由 `deploy/wsl/profiles/gamma_env.sh` 统一注入环境变量
- 不再由 `.env` 直接提供动态 profile 路径

## 6. 配置迁移策略

### 6.1 新增配置

新增统一配置：

```env
WSL_DISTRO=Ubuntu-24.04
WSL_SHARED_CONDA_ENV=insar_wsl_v1
WSL_SHARED_PYTHON=/home/administrator/miniconda3/envs/insar_wsl_v1/bin/python
WSL_BROKER_JOB_ROOT=...
ISCE2_RUNTIME_ID=isce2_runtime_v1
PYINT_RUNTIME_ID=gamma_pyint_runtime_v1
```

### 6.2 旧配置处理

旧配置短期保留，用于兼容现有业务链路：

- `ISCE2_PYTHON`
- `PYINT_WSL_PYTHON`
- `PYINT_GAMMA_ENV_SCRIPT`
- `ISCE2_WSL_DISTRO`
- `PYINT_WSL_DISTRO`

但应逐步降级为：

- 历史兼容字段
- 启动检查告警来源
- 回滚开关

而不再是长期正式执行入口。

## 7. 目录规划

新增目录：

```text
deploy/wsl/
  conda/
  profiles/
  runners/
backend/app/services/
  wsl_runtime_registry.py
  wsl_broker.py
```

运行时作业目录：

```text
backend/runtime/wsl_jobs/<runtime_id>/<operation>/<job_id>.json
```

## 8. 实施阶段

### Phase 1

- 新增 `WSL shared runtime` 配置
- 新增 `RuntimeRegistry`
- 新增 `WslBroker`
- 新增 runner/profile/conda 目录落点

### Phase 2

- 迁移 `ISCE2` 到 `runtime_id + manifest + broker`
- 去掉服务层 shell 字符串拼接

### Phase 3

- 迁移 `Gamma/PyINT`
- 把 `PYINT_GAMMA_ENV_SCRIPT` 收敛到固定 profile

### Phase 4

- 增加环境指纹校验
- 健康检查显示 runtime 漂移
- 审计日志记录 runtime_id / fingerprint / runner / manifest

## 9. 本轮代码落点

本轮先完成 V1 骨架：

- `backend/app/services/wsl_service.py`
  - 新增 `run_wsl_exec()`，支持 `wsl.exe --exec`
- `backend/app/services/wsl_runtime_registry.py`
  - 新增共享 runtime 与双逻辑 runtime 定义
- `backend/app/services/wsl_broker.py`
  - 新增 manifest staging 与 broker 执行骨架
- `deploy/wsl/runners/*`
  - 新增 runner scaffold
- `deploy/wsl/profiles/gamma_env.sh`
  - 固定 profile 落点
- `deploy/wsl/conda/README.md`
  - 约束 conda 环境治理落点

下一步迁移优先级建议：

1. `ISCE2`
2. `Gamma/PyINT`
3. 健康检查与审计
