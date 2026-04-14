# 紧急修复：Nginx UTF-8 BOM 问题（2026-03-04）

## 问题描述

在测试第一阶段修复时，发现 Nginx 启动失败：

```
nginx: [emerg] unknown directive "﻿worker_processes" in Z:/Code/Insar_management_system_v2/nginx/nginx.conf:1
```

注意 `worker_processes` 前面有一个不可见字符 `﻿`（UTF-8 BOM）。

## 根本原因

1. `nginx.conf` 文件包含 UTF-8 BOM（字节序标记：`ef bb bf`）
2. Nginx 无法识别 BOM，将其视为非法字符
3. PowerShell 的 `Set-Content -Encoding UTF8` 默认会添加 BOM

## 影响

- 🔴 **严重**：Nginx 无法启动，整个系统无法访问
- 这是第一阶段修复中 1.3 PowerShell 编码问题的延伸

## 解决方案

### 步骤 1：删除现有文件的 BOM

```bash
cd "Z:\Code\Insar_management_system_v2\nginx"
tail -c +4 nginx.conf > nginx.conf.tmp
mv nginx.conf.tmp nginx.conf
```

**验证**：
```bash
xxd -l 16 nginx.conf
# 应该显示：
# 00000000: 776f 726b 6572 5f70 726f 6365 7373 6573  worker_processes
# 而不是：
# 00000000: efbb bf77 6f72 6b65 725f 7072 6f63 6573  ...worker_proces
```

### 步骤 2：修改 PowerShell 脚本

**文件**：`scripts/start_app.ps1:388`

**修改前**：
```powershell
$NewConfContent | Set-Content -LiteralPath "$NginxConfPath" -NoNewline -Encoding UTF8
```

**修改后**：
```powershell
# 使用 UTF8 无 BOM 编码写入
$Utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText("$NginxConfPath", $NewConfContent, $Utf8NoBom)
```

**说明**：
- `System.Text.UTF8Encoding $false` 创建无 BOM 的 UTF-8 编码器
- `[System.IO.File]::WriteAllText()` 直接写入文件，不添加 BOM
- 这在 PowerShell 5 和 PowerShell 7+ 中都有效

## 验证

### 1. 检查文件编码
```bash
xxd -l 3 nginx/nginx.conf
# 应该不包含 ef bb bf
```

### 2. 启动系统
```powershell
.\scripts\start_app.ps1
```

### 3. 验证 Nginx 启动
```bash
curl http://localhost:8080
# 应该返回前端页面
```

## 经验教训

1. **PowerShell 编码陷阱**：
   - `Set-Content -Encoding UTF8` 会添加 BOM
   - 需要使用 `[System.IO.File]::WriteAllText()` 或 PowerShell 6+ 的 `utf8NoBOM`

2. **Nginx 对 BOM 敏感**：
   - Nginx 配置文件必须是纯 ASCII 或无 BOM 的 UTF-8
   - BOM 会导致解析失败

3. **测试的重要性**：
   - 如果没有实际测试，这个问题不会被发现
   - 代码审查和静态分析无法发现编码问题

## 更新的文档

- ✅ `SECURITY_FIX_PROGRESS.md` - 记录问题和解决方案
- ✅ `scripts/start_app.ps1` - 修复编码问题
- ✅ `nginx/nginx.conf` - 删除 BOM

## 状态

- ✅ 问题已解决
- ✅ 代码已修改
- ⏳ 等待重新测试

## 下一步

1. 重新运行启动脚本
2. 验证 Nginx 正常启动
3. 继续第一阶段的其他测试
