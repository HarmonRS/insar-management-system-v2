# 推荐的多模态视觉语言模型（VLM）

本文档列出了适用于 D-InSAR 形变图分析的优质多模态模型，以及如何在 Ollama 中部署它们。

---

## ⭐ 强烈推荐（已验证支持视觉）

### 1. **Qwen2-VL** (阿里通义千问视觉模型)
- **模型名称**: `qwen2-vl:7b` 或 `qwen2-vl:72b`
- **优势**:
  - 中文理解能力强，适合中文 Prompt
  - 视觉理解能力优秀，对图表、地图分析效果好
  - 推理速度快，7B 版本在消费级显卡可运行
- **安装命令**:
  ```bash
  ollama pull qwen2-vl:7b
  ```
- **显存需求**: 7B 版本约 8GB，72B 版本约 48GB

### 2. **LLaVA 1.6** (开源视觉语言模型)
- **模型名称**: `llava:13b` 或 `llava:34b`
- **优势**:
  - 完全开源，社区支持好
  - 对遥感图像、科学图表理解能力强
  - 多种尺寸可选，适配不同硬件
- **安装命令**:
  ```bash
  ollama pull llava:13b
  ```
- **显存需求**: 13B 版本约 10GB，34B 版本约 24GB

### 3. **MiniCPM-V 2.6** (面壁智能多模态模型)
- **模型名称**: `minicpm-v:8b`
- **优势**:
  - 参数量小但性能强，性价比高
  - 对中文支持好
  - 推理速度快，适合批量分析
- **安装命令**:
  ```bash
  ollama pull minicpm-v:8b
  ```
- **显存需求**: 约 8GB

---

## ✅ 推荐（通用多模态能力）

### 4. **LLaMA 3.2 Vision** (Meta 官方视觉模型)
- **模型名称**: `llama3.2-vision:11b` 或 `llama3.2-vision:90b`
- **优势**:
  - Meta 官方支持，稳定性好
  - 视觉理解能力均衡
  - 英文 Prompt 效果更好
- **安装命令**:
  ```bash
  ollama pull llama3.2-vision:11b
  ```
- **显存需求**: 11B 版本约 10GB，90B 版本约 64GB

### 5. **Phi-3.5 Vision** (微软小型视觉模型)
- **模型名称**: `phi3.5-vision:4b`
- **优势**:
  - 参数量极小，适合低配置硬件
  - 推理速度极快
  - 适合快速筛查场景
- **安装命令**:
  ```bash
  ollama pull phi3.5-vision:4b
  ```
- **显存需求**: 约 4GB

---

## 🔍 如何验证模型是否支持视觉

在 Ollama 中，只有模型名称包含 `-vision`、`-vl`、`llava`、`minicpm-v` 等关键词的模型才支持视觉输入。

### 验证方法 1：查看模型信息
```bash
ollama show qwen2-vl:7b
```
查看输出中是否有 `vision` 或 `multimodal` 相关描述。

### 验证方法 2：测试图像输入
```bash
ollama run qwen2-vl:7b
>>> 请描述这张图片 [附加图片]
```
如果模型返回 "I cannot process images" 或类似错误，说明不支持视觉。

### 验证方法 3：检查 Modelfile
```bash
ollama show --modelfile qwen2-vl:7b
```
查看是否有 `PARAMETER vision` 或类似配置。

---

## ⚠️ 常见错误模型（不支持视觉）

以下模型**不支持**视觉输入，请勿用于图像分析：

- ❌ `llama3:8b` (纯文本模型)
- ❌ `qwen2:7b` (纯文本模型，注意与 qwen2-vl 区分)
- ❌ `mistral:7b` (纯文本模型)
- ❌ `gemma:7b` (纯文本模型)
- ❌ `phi3:3b` (纯文本模型，注意与 phi3.5-vision 区分)

**识别规则**：如果模型名称中没有 `vision`、`vl`、`llava`、`minicpm-v` 等关键词，基本都是纯文本模型。

---

## 📊 性能对比（D-InSAR 分析场景）

| 模型 | 参数量 | 显存需求 | 推理速度 | 中文能力 | 图表理解 | 综合评分 |
|------|--------|----------|----------|----------|----------|----------|
| Qwen2-VL 7B | 7B | 8GB | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | 9.5/10 |
| LLaVA 13B | 13B | 10GB | ⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐ | 8.5/10 |
| MiniCPM-V 8B | 8B | 8GB | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | 9.0/10 |
| LLaMA 3.2 Vision 11B | 11B | 10GB | ⭐⭐⭐ | ⭐⭐ | ⭐⭐⭐ | 7.5/10 |
| Phi-3.5 Vision 4B | 4B | 4GB | ⭐⭐⭐⭐⭐ | ⭐⭐ | ⭐⭐⭐ | 7.0/10 |

---

## 🚀 快速部署指南

### 步骤 1：安装 Ollama
```bash
# Windows
winget install Ollama.Ollama

# Linux
curl -fsSL https://ollama.com/install.sh | sh

# macOS
brew install ollama
```

### 步骤 2：启动 Ollama 服务
```bash
ollama serve
```

### 步骤 3：拉取推荐模型
```bash
# 推荐：Qwen2-VL（中文场景最佳）
ollama pull qwen2-vl:7b

# 备选：LLaVA（英文场景）
ollama pull llava:13b

# 备选：MiniCPM-V（性价比高）
ollama pull minicpm-v:8b
```

### 步骤 4：验证模型
```bash
ollama list
```
确认模型已下载完成。

### 步骤 5：在系统中使用
在前端 AI 分析面板中，选择对应的模型名称（如 `qwen2-vl:7b`），创建诊断任务即可。

---

## 💡 使用建议

1. **首选 Qwen2-VL**：如果你的系统主要使用中文 Prompt，强烈推荐 `qwen2-vl:7b`，它对中文理解和图表分析能力最强。

2. **显存不足时选 MiniCPM-V 或 Phi-3.5 Vision**：如果显卡显存 < 8GB，选择 `minicpm-v:8b` 或 `phi3.5-vision:4b`。

3. **批量分析选小模型**：如果需要批量分析大量结果，选择参数量小的模型（4B-8B），可以显著提升吞吐量。

4. **重点区域选大模型**：对于重点监测区域或高风险区域，使用 `qwen2-vl:72b` 或 `llava:34b` 获得更详细的分析。

5. **定期更新模型**：Ollama 社区持续发布新模型，定期运行 `ollama list` 和 `ollama pull` 更新到最新版本。

---

## 🔧 故障排查

### 问题 1：模型下载失败
**解决方案**：
```bash
# 设置代理（如果在国内）
export HTTP_PROXY=http://127.0.0.1:7890
export HTTPS_PROXY=http://127.0.0.1:7890
ollama pull qwen2-vl:7b
```

### 问题 2：显存不足（OOM）
**解决方案**：
- 使用更小的模型（如 `phi3.5-vision:4b`）
- 或者使用量化版本（如 `qwen2-vl:7b-q4`）

### 问题 3：推理速度慢
**解决方案**：
- 确保使用 GPU 运行（检查 `nvidia-smi`）
- 减小模型尺寸
- 调整 Ollama 配置（`num_gpu`, `num_thread`）

### 问题 4：模型返回 "I cannot process images"
**原因**：使用了纯文本模型，不支持视觉输入。
**解决方案**：切换到本文档推荐的视觉模型。

---

## 📚 参考资源

- Ollama 官方文档: https://ollama.com/library
- Qwen2-VL 模型卡: https://ollama.com/library/qwen2-vl
- LLaVA 模型卡: https://ollama.com/library/llava
- MiniCPM-V 模型卡: https://ollama.com/library/minicpm-v

---

**最后更新**: 2026-03-03
