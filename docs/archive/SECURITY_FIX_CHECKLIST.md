# 安全修复快速检查清单

## 使用说明
- 每次修复前检查此清单
- 每次修复后更新状态
- 测试失败立即停止，分析原因

---

## 第一阶段检查清单

### ✅ 1.1 ENVI 主流程返回值
- [x] 代码已修改
- [ ] 本地测试通过
- [ ] 集成测试通过
- [ ] 无副作用
- [ ] 可以继续下一项

**快速测试命令**：
```bash
# 运行一个 D-InSAR 工作流
# 在前端 IDL 自动化面板中提交任务
# 观察任务状态和日志
```

---

### ✅ 1.2 失败计数重复累加
- [x] 代码已修改
- [ ] 本地测试通过
- [ ] 集成测试通过
- [ ] 无副作用
- [ ] 可以继续下一项

**快速测试命令**：
```bash
# 在前端 IDL 自动化面板中
# 使用 "Step 3: 提取 Disp 结果" 功能
# 故意触发一些失败（如修改文件权限）
# 检查返回的统计数据
```

---

### ✅ 1.3 PowerShell 编码
- [x] 代码已修改
- [ ] 本地测试通过
- [ ] 集成测试通过
- [ ] 无副作用
- [ ] 可以继续下一项

**快速测试命令**：
```powershell
# 运行启动脚本
.\scripts\start_app.ps1

# 检查 nginx.conf 编码
Get-Content .\nginx\conf\nginx.conf -Encoding UTF8

# 验证 Nginx 启动
curl http://localhost:8080
```

---

## 第二阶段检查清单

### ⏳ 2.1 AOI token 容量上限
- [ ] 代码已修改
- [ ] 环境变量已配置
- [ ] 本地测试通过
- [ ] 压力测试通过
- [ ] 内存监控正常
- [ ] 无副作用
- [ ] 可以继续下一项

**快速测试脚本**：
```python
# 创建测试脚本 test_aoi_token_limit.py
import requests
import json

base_url = "http://localhost:8000"
token = "your_auth_token"

# 创建 1500 个 AOI token
for i in range(1500):
    geojson = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [100 + i * 0.001, 30 + i * 0.001]
            }
        }]
    }
    response = requests.post(
        f"{base_url}/api/aoi/upload",
        headers={"Authorization": f"Bearer {token}"},
        json=geojson
    )
    if i % 100 == 0:
        print(f"Created {i} tokens")

print("Test completed")
```

---

### ⏳ 2.2 AOI 解析异常处理
- [ ] 代码已修改
- [ ] 本地测试通过
- [ ] 前端兼容性测试通过
- [ ] 错误消息清晰
- [ ] 无副作用
- [ ] 可以继续下一项

**快速测试数据**：
```json
// 无效 GeoJSON 1：缺少 type
{
  "features": []
}

// 无效 GeoJSON 2：错误的坐标
{
  "type": "FeatureCollection",
  "features": [{
    "type": "Feature",
    "geometry": {
      "type": "Point",
      "coordinates": [999, 999]
    }
  }]
}

// 无效 GeoJSON 3：格式错误
{
  "type": "FeatureCollection",
  "features": "not an array"
}
```

---

### ⏳ 2.3 路径归属判断
- [ ] 代码已修改
- [ ] 本地测试通过
- [ ] 边界情况测试通过
- [ ] 扫描结果准确
- [ ] 无副作用
- [ ] 可以继续下一项

**快速测试步骤**：
```bash
# 1. 创建测试目录结构
mkdir -p /data/radar_backup
mkdir -p /data/radar/subdir

# 2. 放入测试文件
touch /data/radar_backup/test.tif
touch /data/radar/subdir/test.tif

# 3. 运行数据扫描
# 在前端数据管理面板中点击"刷新"

# 4. 验证结果
# radar_backup 的文件不应该出现在列表中
# radar/subdir 的文件应该出现在列表中
```

---

## 第三阶段检查清单

### ⏳ 3.1 解包安全校验
- [ ] 代码已修改
- [ ] 本地测试通过
- [ ] 安全测试通过
- [ ] 性能测试通过
- [ ] 无副作用
- [ ] 可以继续下一项

**快速测试脚本**：
```python
# 创建测试脚本 test_tar_security.py
import tarfile
import os

# 测试 1：创建包含符号链接的 tar
with tarfile.open("test_symlink.tar", "w") as tar:
    # 创建一个符号链接指向 /etc/passwd
    info = tarfile.TarInfo(name="link_to_passwd")
    info.type = tarfile.SYMTYPE
    info.linkname = "/etc/passwd"
    tar.addfile(info)

# 测试 2：创建包含 .. 路径的 tar
with tarfile.open("test_escape.tar", "w") as tar:
    info = tarfile.TarInfo(name="../../../etc/passwd")
    info.size = 0
    tar.addfile(info)

# 测试 3：创建正常的 tar
with tarfile.open("test_normal.tar", "w") as tar:
    # 创建一个正常文件
    info = tarfile.TarInfo(name="normal_file.txt")
    info.size = 5
    tar.addfile(info, fileobj=io.BytesIO(b"hello"))

print("Test archives created")
print("test_symlink.tar - should be rejected")
print("test_escape.tar - should be rejected")
print("test_normal.tar - should be accepted")
```

---

## 紧急回滚程序

如果任何测试失败，立即执行：

```bash
# 1. 查看最近的提交
git log --oneline -5

# 2. 回滚到上一个提交
git revert HEAD

# 3. 或者硬回滚（谨慎使用）
git reset --hard HEAD~1

# 4. 重启服务
# 停止所有服务
# 重新运行 start_app.ps1

# 5. 验证系统恢复正常
curl http://localhost:8080
```

---

## 测试环境要求

### 最小测试环境
- [ ] Python 3.9+
- [ ] PostgreSQL 运行中
- [ ] Ollama 运行中（如果测试 AI 功能）
- [ ] 至少 1 个 D-InSAR 结果数据
- [ ] 至少 1 个雷达数据

### 推荐测试环境
- [ ] 完整的开发环境
- [ ] 测试数据库（非生产）
- [ ] 监控工具（内存、CPU）
- [ ] 日志查看工具

---

## 测试数据准备

### D-InSAR 测试数据
```
需要准备：
- 至少 1 个完整的 Task_* 目录
- 包含 dinsar_results/out_ISARPTD_*_rsp_disp
- 用于测试 extract_disp_results 功能
```

### 雷达数据测试
```
需要准备：
- 至少 2 个 .tif 文件
- 放在 MONITOR_RADAR_DIRS 配置的目录中
- 用于测试预览图生成和路径判断
```

### AOI 测试数据
```
需要准备：
- 有效的 GeoJSON 文件
- 无效的 GeoJSON 文件（多种错误类型）
- 用于测试异常处理
```

---

## 性能基准

记录修复前后的性能指标：

### ENVI 工作流
- 修复前成功率：_%
- 修复后成功率：_%
- 平均耗时：_秒

### AOI token
- 修复前内存占用：_MB
- 修复后内存占用：_MB
- Token 数量上限：_个

### 解包性能
- 修复前平均耗时：_秒
- 修复后平均耗时：_秒
- 性能差异：_%

---

## 签名确认

每完成一个阶段，填写以下信息：

### 第一阶段
- 修复人：_______
- 测试人：_______
- 完成日期：_______
- 签名：_______

### 第二阶段
- 修复人：_______
- 测试人：_______
- 完成日期：_______
- 签名：_______

### 第三阶段
- 修复人：_______
- 测试人：_______
- 完成日期：_______
- 签名：_______
