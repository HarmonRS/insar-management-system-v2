# WSL Shared Conda Runtime

此目录用于维护当前统一的 WSL 共享 conda 运行时文档与锁定文件。

## 当前策略

当前项目不再建议为 `isce2`、`timeseries`、`pyint` 分别维护多套相互漂移的 conda 环境，而是收口为：

- `base`
- `insar_wsl_v1`

目标是让以下链路共享同一套 Python 运行时：

- ISCE2 D-InSAR
- 时序 InSAR 当前 SBAS 流程
- Gamma / PyINT 的 Python 胶水层

说明：

- Gamma 二进制本体仍然保持固定安装位置，不建议放入 conda。
- Gamma 相关的 `PATH`、`GAMMA_HOME`、脚本目录注入，统一由 `deploy/wsl/profiles/gamma_env.sh` 负责。

## 当前约定产物

本目录建议维护以下文件：

- `insar_wsl_v1.environment.yml`
  人工可读、可维护的环境定义。

- `insar_wsl_v1.explicit.lock`
  由 `conda list --explicit` 导出的精确锁文件。

- `insar_wsl_v1.fingerprint.json`
  用于记录 Python 版本、关键包版本、生成时间等信息。

## 推荐工作流

### 1. 在 WSL 中核出现有环境

先查看现有环境：

```bash
conda env list
```

逐个导出候选环境包列表，用于做并集：

```bash
conda list -n isce2
conda list -n isce2_mintpy_v1
conda list -n pyint
```

### 2. 整理并集并创建新环境

推荐先人工整理 `insar_wsl_v1.environment.yml`，再创建环境：

```bash
conda env create -n insar_wsl_v1 -f deploy/wsl/conda/insar_wsl_v1.environment.yml
```

如果环境已存在：

```bash
conda env update -n insar_wsl_v1 -f deploy/wsl/conda/insar_wsl_v1.environment.yml --prune
```

### 3. 生成锁文件与指纹

```bash
conda env export --from-history -n insar_wsl_v1 > deploy/wsl/conda/insar_wsl_v1.environment.yml
conda list --explicit -n insar_wsl_v1 > deploy/wsl/conda/insar_wsl_v1.explicit.lock
```

```bash
python - <<'PY'
import json
import subprocess

def sh(*argv):
    return subprocess.check_output(argv, text=True).strip()

payload = {
    "python_version": sh("python", "--version"),
    "conda_env": "insar_wsl_v1",
    "isce_version": sh("python", "-c", "import isce, sys; print(getattr(isce, '__file__', 'unknown'))"),
}
print(json.dumps(payload, indent=2, ensure_ascii=False))
PY
```

将输出整理后保存为 `insar_wsl_v1.fingerprint.json`。

## .env 对齐要求

共享环境建好后，应确保 `.env` 中以下键值一致：

```env
WSL_DISTRO=Ubuntu-24.04
WSL_SHARED_CONDA_ENV=insar_wsl_v1
WSL_SHARED_PYTHON=/home/administrator/miniconda3/envs/insar_wsl_v1/bin/python

ISCE2_PYTHON=/home/administrator/miniconda3/envs/insar_wsl_v1/bin/python
TIMESERIES_ENV_NAME=insar_wsl_v1
TIMESERIES_PYTHON=/home/administrator/miniconda3/envs/insar_wsl_v1/bin/python
PYINT_WSL_PYTHON=/home/administrator/miniconda3/envs/insar_wsl_v1/bin/python
```

## 运维验证

系统启动后，运维自检中的 `wsl_runtime` 应满足：

- `shared_distro = Ubuntu-24.04`
- `shared_conda_env_name = insar_wsl_v1`
- `required_runtime_count == healthy_runtime_count`

如果健康面板显示 Python 路径不一致，说明仍然有旧环境残留在配置层或运行时注册层。
