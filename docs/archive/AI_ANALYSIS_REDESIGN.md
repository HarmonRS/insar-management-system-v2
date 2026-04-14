# AI 分析模块重构设计文档

**版本**: v2.0
**日期**: 2026-03-02
**作者**: System Architect

---

## 1. 背景与目标

### 1.1 当前问题

- **架构混乱**: 同步/异步接口混用，用户体验不一致
- **Prompt 硬编码**: 无法灵活调整诊断策略
- **模型选择僵化**: 用户无法指定模型
- **结果存储不规范**: 诊断报告存在 `task.message`，无法检索和分析
- **缺少历史管理**: 无法查看、对比、导出历史诊断

### 1.2 设计目标

1. **统一异步架构**: 所有 AI 任务进队列，前端轮询状态
2. **配置化 Prompt**: 模板化管理，支持自定义
3. **灵活模型选择**: 用户可选模型，记住偏好
4. **规范化存储**: 独立表管理诊断记录
5. **完整历史管理**: 查询、筛选、导出、对比

---

## 2. 数据库设计

### 2.1 核心表：`ai_diagnosis`

存储所有 AI 诊断记录。

```sql
CREATE TABLE ai_diagnosis (
    id SERIAL PRIMARY KEY,

    -- 关联信息
    result_id INTEGER NOT NULL REFERENCES dinsar_results(id) ON DELETE CASCADE,
    task_id VARCHAR(50),  -- 关联 system_tasks，用于追踪任务状态

    -- 模型与配置
    model_name VARCHAR(100) NOT NULL,  -- 如 'qwen3-vl:8b'
    prompt_template VARCHAR(50) NOT NULL,  -- 'quick'/'standard'/'detailed'/'custom'
    prompt_text TEXT,  -- 实际使用的完整 prompt（用于审计）

    -- 诊断结果
    diagnosis_markdown TEXT NOT NULL,  -- Markdown 格式的诊断报告
    risk_level VARCHAR(20),  -- 'LOW'/'MEDIUM'/'HIGH'/NULL
    confidence_score FLOAT,  -- 0.0-1.0，模型自评置信度（可选）

    -- 上下文信息（快照，避免关联查询）
    result_name VARCHAR(255),
    date_range VARCHAR(100),  -- 如 '20240101-20240115'
    quality_score FLOAT,  -- 当时的 ai_score
    hazards_found INTEGER DEFAULT 0,  -- 覆盖范围内的隐患点数量
    hazards_snapshot JSONB,  -- 隐患点详情快照 [{name, type, location}]

    -- 元数据
    created_at TIMESTAMP DEFAULT NOW(),
    duration_seconds FLOAT,  -- 诊断耗时
    error_message TEXT,  -- 如果失败，记录错误

    -- 索引
    INDEX idx_result_id (result_id),
    INDEX idx_created_at (created_at DESC),
    INDEX idx_risk_level (risk_level)
);
```

**设计要点**：
- `prompt_text` 存储实际 prompt，便于审计和复现
- `hazards_snapshot` 用 JSONB 存快照，避免隐患点被删除后无法回溯
- `duration_seconds` 用于性能分析
- `error_message` 支持失败记录（不删除，便于调试）

### 2.2 配置表：`ai_prompt_templates`（可选）

如果需要 UI 管理 Prompt 模板，可建此表。否则用配置文件即可。

```sql
CREATE TABLE ai_prompt_templates (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) UNIQUE NOT NULL,  -- 'quick'/'standard'/'detailed'
    display_name_zh VARCHAR(100),
    display_name_en VARCHAR(100),
    template_text TEXT NOT NULL,
    is_system BOOLEAN DEFAULT FALSE,  -- 系统内置模板不可删除
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
```

**初始数据**：
```sql
INSERT INTO ai_prompt_templates (name, display_name_zh, display_name_en, template_text, is_system) VALUES
('quick', '快速诊断', 'Quick Diagnosis', '你是 InSAR 专家，用 200 字简述...', TRUE),
('standard', '标准诊断', 'Standard Diagnosis', '你是资深专家，按 4 步分析...', TRUE),
('detailed', '详细诊断', 'Detailed Diagnosis', '深度分析，包含地形、历史对比...', TRUE);
```

### 2.3 用户偏好表：`user_ai_preferences`（可选）

存储用户的 AI 偏好设置。

```sql
CREATE TABLE user_ai_preferences (
    user_id INTEGER PRIMARY KEY REFERENCES users(id),
    preferred_model VARCHAR(100),  -- 如 'qwen3-vl:8b'
    default_template VARCHAR(50),  -- 'standard'
    auto_diagnose BOOLEAN DEFAULT FALSE,  -- 新结果自动诊断
    updated_at TIMESTAMP DEFAULT NOW()
);
```

---

## 3. 后端 API 设计

### 3.1 路由结构

新建 `backend/app/routers/ai_diagnosis.py`，遵循 RESTful 规范。

#### 3.1.1 诊断记录管理

**列表查询**
```http
GET /api/ai-diagnosis?page=1&limit=20&result_id=123&risk_level=HIGH&sort=-created_at
```

**Query 参数**：
- `page`, `limit`: 分页
- `result_id`: 筛选特定结果的诊断
- `risk_level`: 筛选风险等级（`LOW`/`MEDIUM`/`HIGH`）
- `model_name`: 筛选模型
- `date_from`, `date_to`: 时间范围
- `sort`: 排序字段（`-created_at` 表示倒序）

**响应**：
```json
{
  "total": 156,
  "page": 1,
  "limit": 20,
  "items": [
    {
      "id": 42,
      "result_id": 123,
      "result_name": "Task_001_20240101_20240115",
      "model_name": "qwen3-vl:8b",
      "prompt_template": "standard",
      "risk_level": "HIGH",
      "hazards_found": 3,
      "created_at": "2026-03-02T10:30:00Z",
      "duration_seconds": 45.2
    }
  ]
}
```

**详情查询**
```http
GET /api/ai-diagnosis/{id}
```

**响��**：
```json
{
  "id": 42,
  "result_id": 123,
  "result_name": "Task_001_20240101_20240115",
  "model_name": "qwen3-vl:8b",
  "prompt_template": "standard",
  "prompt_text": "你是一位拥有 20 年经验...",
  "diagnosis_markdown": "## 形态学分析\n\n观察到...",
  "risk_level": "HIGH",
  "confidence_score": 0.85,
  "hazards_found": 3,
  "hazards_snapshot": [
    {"name": "XX滑坡", "type": "滑坡", "location": "XX县"}
  ],
  "quality_score": 0.72,
  "created_at": "2026-03-02T10:30:00Z",
  "duration_seconds": 45.2
}
```

**创建诊断任务**
```http
POST /api/ai-diagnosis
Content-Type: application/json

{
  "result_id": 123,
  "model_name": "qwen3-vl:8b",  // 可选，不传则自动选择
  "prompt_template": "standard"  // 'quick'/'standard'/'detailed'/'custom'
  "custom_prompt": "..."  // 仅当 template='custom' 时需要
}
```

**响应**：
```json
{
  "diagnosis_id": 42,  // 预创建的记录 ID（状态为 PENDING）
  "task_id": "task_abc123",
  "message": "诊断任务已进入队列"
}
```

**删除记录**
```http
DELETE /api/ai-diagnosis/{id}
```

**批量导出**
```http
POST /api/ai-diagnosis/export
Content-Type: application/json

{
  "ids": [42, 43, 44],
  "format": "markdown"  // 'markdown'/'pdf'/'json'
}
```

**响应**：返回文件流或下载链接。

#### 3.1.2 模型管理

**获取可用模型**
```http
GET /api/ai/models
```

**响应**：
```json
{
  "ollama_online": true,
  "models": [
    {
      "name": "qwen3-vl:8b",
      "size": "8.5 GB",
      "modified_at": "2026-03-01T12:00:00Z",
      "capabilities": ["vision", "text"],
      "recommended": true
    },
    {
      "name": "qwen2-vl:72b",
      "size": "72 GB",
      "capabilities": ["vision", "text"]
    }
  ],
  "default_model": "qwen3-vl:8b"
}
```

**预热模型**
```http
POST /api/ai/models/{model_name}/warmup
```

#### 3.1.3 Prompt 模板管理

**列表**
```http
GET /api/ai/prompt-templates
```

**响应**：
```json
{
  "templates": [
    {
      "name": "quick",
      "display_name": "快速诊断",
      "is_system": true
    },
    {
      "name": "custom_001",
      "display_name": "我的自定义模板",
      "is_system": false
    }
  ]
}
```

**详情**
```http
GET /api/ai/prompt-templates/{name}
```

**创建/更新**（仅非系统模板）
```http
POST /api/ai/prompt-templates
PUT /api/ai/prompt-templates/{name}

{
  "name": "custom_001",
  "display_name": "我的模板",
  "template_text": "..."
}
```

---

## 4. 任务处理流程

### 4.1 异步任务架构

**流程图**：
```
用户点击"诊断"
  ↓
前端 POST /api/ai-diagnosis
  ↓
后端创建 diagnosis 记录（状态 PENDING）+ system_task + system_job
  ↓
返回 diagnosis_id + task_id
  ↓
前端轮询 GET /api/tasks/{task_id}
  ↓
job_worker 执行 _handle_ai_analyze
  ↓
调用 Ollama VLM
  ↓
更新 diagnosis 记录（状态 COMPLETED，写入 markdown）
  ↓
更新 system_task（状态 COMPLETED）
  ↓
前端检测到完成，跳转到诊断详情页
```

### 4.2 Handler 改造

`backend/app/services/job_handlers.py` 中的 `_handle_ai_analyze` 改为：

```python
async def _handle_ai_analyze(job: SystemJobORM) -> None:
    payload = job.payload or {}
    diagnosis_id = payload.get("diagnosis_id")

    if not diagnosis_id:
        raise ValueError("AI_ANALYZE requires diagnosis_id")

    async with AsyncSessionLocal() as db:
        # 1. 查询 diagnosis 记录
        diag = await db.get(AiDiagnosisORM, diagnosis_id)
        if not diag:
            raise ValueError(f"Diagnosis {diagnosis_id} not found")

        # 2. 查询关联的 result
        result = await db.get(DinsarResultORM, diag.result_id)

        # 3. 查询隐患点（PostGIS）
        hazards = await db.execute(
            select(HazardPointORM).where(ST_Covers(result.geom, HazardPointORM.geom))
        )
        hazards = hazards.scalars().all()

        # 4. 准备图片
        img_path = data_service.get_dinsar_cache_path(result.id, result.name)
        img_base64 = _load_image_as_base64(img_path)

        # 5. 构建 prompt
        prompt = _build_prompt(diag.prompt_template, diag.prompt_text, result, hazards)

        # 6. 调用 VLM
        start_time = time.time()
        try:
            analysis = await analyze_map_with_vlm(
                images_base64=[img_base64],
                prompt=prompt,
                model_name=diag.model_name
            )
            duration = time.time() - start_time

            # 7. 解析风险等级（从 Markdown 中提取）
            risk_level = _extract_risk_level(analysis)

            # 8. 更新 diagnosis 记录
            diag.diagnosis_markdown = analysis
            diag.risk_level = risk_level
            diag.hazards_found = len(hazards)
            diag.hazards_snapshot = [
                {"name": h.hazard_name, "type": h.hazard_type, "location": f"{h.city}{h.county}"}
                for h in hazards
            ]
            diag.duration_seconds = duration
            await db.commit()

            # 9. 更新 task 状态
            await task_service.update_task(
                job.task_id,
                status="COMPLETED",
                message=f"诊断完成，风险等级: {risk_level}",
                progress=100
            )
        except Exception as e:
            diag.error_message = str(e)
            await db.commit()
            await task_service.update_task(
                job.task_id,
                status="FAILED",
                message=f"诊断失败: {str(e)}"
            )
```

---

## 5. 前端设计

### 5.1 组件结构

```
src/
├── panels/
│   └── AiAnalysisPanel.jsx          # 新增：AI 分析主面板
├── components/
│   ├── AiDiagnosisTable.jsx         # 诊断历史表格
│   ├── AiDiagnosisDetailModal.jsx   # 诊断详情 Modal
│   ├── AiModelSelector.jsx          # 模型选择器
│   ├── AiPromptTemplateEditor.jsx   # Prompt 模板编辑器
│   └── AiQuickDiagnoseCard.jsx      # 快速诊断卡片
├── api/
│   └── aiDiagnosis.js               # API 封装
└── hooks/
    └── useAiDiagnosis.js            # 自定义 Hook
```

### 5.2 AiAnalysisPanel 布局

```jsx
<div className="ai-analysis-panel">
  {/* 顶部状态卡片 */}
  <div className="ai-status-cards">
    <OllamaStatusCard />
    <ModelSelectorCard />
  </div>

  {/* 快速诊断 */}
  <AiQuickDiagnoseCard />

  {/* 诊断历史 */}
  <div className="ai-diagnosis-history">
    <div className="history-toolbar">
      <input placeholder="搜索结果名称..." />
      <select>
        <option>全部风险等级</option>
        <option>高风险</option>
        <option>中风险</option>
        <option>低风险</option>
      </select>
      <button>导出选中</button>
    </div>
    <AiDiagnosisTable />
  </div>
</div>
```

### 5.3 核心交互流程

**快速诊断**：
1. 用户在下拉框选择 D-InSAR 结果
2. 选择诊断模式（快速/标准/详细）
3. 点击"开始诊断"
4. 前端 POST `/api/ai-diagnosis`，获得 `task_id`
5. 显示进度条，轮询 `/api/tasks/{task_id}`
6. 完成后自动打开详情 Modal

**查看历史**：
1. 表格显示所有诊断记录（分页）
2. 点击行打开详情 Modal
3. Modal 显示完整 Markdown 报告（支持复制、导出）

**模型管理**：
1. 顶部卡片显示当前选中模型
2. 点击"切换模型"打开下拉列表
3. 选择后保存到 localStorage（或用户配置）

---

## 6. 配置管理

### 6.1 Prompt 模板文件

`backend/app/ai_prompts.py`：

```python
PROMPT_TEMPLATES = {
    'quick': {
        'name_zh': '快速诊断',
        'name_en': 'Quick Diagnosis',
        'template': """
你是 InSAR 专家。请用 200 字简述以下形变图的关键发现：
- 是否存在明显形变区？
- 与已知隐患点是否吻合？
- 风险等级（低/中/高）？

背景：{context}
""",
    },
    'standard': {
        'name_zh': '标准诊断',
        'name_en': 'Standard Diagnosis',
        'template': """
你是拥有 20 年经验的 InSAR 专家。请按以下步骤分析：
1. 形态学分析
2. 时空一致性
3. 风险演化预测
4. 综合风险评级

背景：{context}
隐患点：{hazards}
""",
    },
    'detailed': {
        'name_zh': '详细诊断',
        'name_en': 'Detailed Diagnosis',
        'template': """
深度分析，包含：
- 地形地貌分析
- 历史形变趋势
- 潜在触发因素
- 监测建议

背景：{context}
""",
    },
}

def build_prompt(template_name: str, context: dict) -> str:
    template = PROMPT_TEMPLATES.get(template_name, PROMPT_TEMPLATES['standard'])
    return template['template'].format(**context)
```

### 6.2 环境变量

`.env` 新增：
```bash
# Ollama 配置
OLLAMA_API_URL=http://127.0.0.1:11434/api/generate
OLLAMA_TIMEOUT=600
OLLAMA_DEFAULT_MODEL=qwen3-vl:8b

# AI 诊断配置
AI_DIAGNOSIS_AUTO_RETRY=true
AI_DIAGNOSIS_MAX_RETRIES=2
AI_DIAGNOSIS_STREAM_OUTPUT=false  # 未来支持流式
```

---

## 7. 成熟方案参考

### 7.1 Prompt 管理

参考 **LangChain PromptTemplate**：
- 支持变量插值 `{variable}`
- 支持条件渲染 `{% if condition %}`
- 支持模板继承

可引入 `Jinja2` 作为模板引擎：
```python
from jinja2 import Template

template = Template(PROMPT_TEMPLATES['standard']['template'])
prompt = template.render(context=context, hazards=hazards)
```

### 7.2 任务队列

当前使用自研的 `job_queue_service`，可考虑迁移到：
- **Celery** + Redis：成熟的分布式任务队列
- **Dramatiq**：轻量级替代方案
- **ARQ**：基于 asyncio 的现代方案

### 7.3 诊断报告导出

- **Markdown → PDF**：使用 `weasyprint` 或 `pdfkit`
- **Markdown → Word**：使用 `python-docx` + `markdown` 解析
- **模板化报告**：使用 `Jinja2` + HTML 模板

### 7.4 前端状态管理

如果 AI 功能复杂度增加，可考虑：
- **Zustand**（已使用）：继续用 `useAiStore`
- **React Query**：管理服务端状态（诊断列表、模型列表）

---

## 8. 迁移计划

### 8.1 数据迁移

**Step 1**：创建新表
```bash
alembic revision -m "add_ai_diagnosis_table"
alembic upgrade head
```

**Step 2**：迁移历史数据（可选）
从 `system_tasks` 的 `message` 字段提取历史诊断，写入新表：
```python
# 迁移脚本
async def migrate_old_diagnoses():
    tasks = await db.execute(
        select(SystemTaskORM).where(
            SystemTaskORM.task_type == 'AI_ANALYZE',
            SystemTaskORM.status == 'COMPLETED'
        )
    )
    for task in tasks.scalars():
        msg = json.loads(task.message)
        diagnosis = AiDiagnosisORM(
            result_id=msg['result_id'],
            diagnosis_markdown=msg['analysis'],
            created_at=task.completed_at,
            ...
        )
        db.add(diagnosis)
    await db.commit()
```

### 8.2 API 兼容性

**向后兼容**：
- 保留旧接口 `/ai/analyze-result/{id}` 3 个月
- 内部重定向到新接口
- 响应格式保持一致

**废弃通知**：
```json
{
  "message": "诊断任务已进入队列",
  "task_id": "...",
  "diagnosis_id": 42,
  "_deprecated": "此接口将在 2026-06-01 废弃，请使用 POST /api/ai-diagnosis"
}
```

---

## 9. 性能优化

### 9.1 缓存策略

- **模型列表缓存**：`GET /api/ai/models` 结果缓存 5 分钟（Redis）
- **Prompt 模板缓存**：启动时加载到内存
- **诊断列表分页**：使用游标分页（`cursor-based`）而非 offset

### 9.2 并发控制

- **Ollama 并发限制**：同时最多 2 个 VLM 任务（避免显存溢出）
- **任务优先级**：用户手动触发 > 自动诊断

### 9.3 超时与重试

```python
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=60),
    retry=retry_if_exception_type(httpx.TimeoutException)
)
async def call_ollama_with_retry(...):
    ...
```

---

## 10. 安全考虑

### 10.1 输入验证

- **Prompt 注入防护**：限制自定义 prompt 长度（8000 字符）
- **模型名称白名单**：只允许已知模型
- **结果 ID 权限检查**：确保用户有权访问该结果

### 10.2 输出过滤

- **敏感信息脱敏**：诊断报告中的坐标、地址模糊化
- **XSS 防护**：Markdown 渲染时使用 `DOMPurify`

---

## 11. 监控与日志

### 11.1 关键指标

- **诊断成功率**：`completed / (completed + failed)`
- **平均耗时**：按模型、模板统计
- **模型使用分布**：哪个模型最受欢迎
- **风险等级分布**：高/中/低风险占比

### 11.2 日志记录

```python
logger.info(
    "AI diagnosis completed",
    extra={
        "diagnosis_id": diag.id,
        "result_id": diag.result_id,
        "model": diag.model_name,
        "duration": diag.duration_seconds,
        "risk_level": diag.risk_level,
    }
)
```

---

## 12. 未来扩展

### 12.1 多模态输入

- 支持上传多张图片（时序对比）
- 支持附加文本描述（用户观察）

### 12.2 对比分析

- 同一结果的多次诊断对比
- 不同模型的诊断结果对比

### 12.3 自动化工作流

- 新结果自动触发诊断
- 高风险自动发送邮件/钉钉通知

### 12.4 Fine-tuning

- 收集用户反馈（诊断是否准确）
- 定期 fine-tune 模型

---

## 13. 总结

### 13.1 核心改进

| 维度 | 当前 | 重构后 |
|------|------|--------|
| 架构 | 同步/异步混用 | 统一异步任务 |
| Prompt | 硬编码 | 模板化 + 可自定义 |
| 模型 | 自动选择 | 用户可选 + 偏好记忆 |
| 存储 | task.message | 独立表 + 完整字段 |
| 历史 | 无法查询 | 完整 CRUD + 导出 |

### 13.2 实施优先级

**P0（必须）**：
- 创建 `ai_diagnosis` 表
- 改造 `_handle_ai_analyze`
- 实现基础 CRUD API
- 创建 `AiAnalysisPanel` 前端面板

**P1（重要）**：
- Prompt 模板管理
- 模型选择器
- 诊断详情 Modal
- 导出功能

**P2（可选）**：
- 用户偏好保存
- 对比分析
- 自动化工作流

---

**文档结束**
