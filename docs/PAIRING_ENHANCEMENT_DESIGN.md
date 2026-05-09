# D-InSAR 配对增强设计文档

**版本**: v2.0
**日期**: 2026-03-08
**状态**: 概念设计，部分能力已落地

> 更新说明（2026-04-26）：截至 2026-04-25，pairing cache、pairing health 状态和基础指标统计已经在现网落地。当前运行态事实请优先参考 `CURRENT_STATUS_20260425.md`、`DATABASE_SELF_MAINTENANCE_AUDIT_20260425.md` 和健康检查接口；本文档保留配对语义、策略命名、产品交互和后续演进方向的设计价值。

---

## 一、背景与目标

### 1.1 设计起点（2026-03-08 基线）

当前配对系统采用**单池全组合**模式：
- 所有影像放入同一个池子，两两组合，用时间基线范围过滤
- 无法控制主影像（master）和辅影像（slave）的时间段
- 无法指定配对策略（星型、链式、SBAS 网络等）
- 不支持手动选定参考影像

这种设计适合探索性分析，但不满足生产级 InSAR 处理需求。

### 1.2 行业标准实践

参考成熟 InSAR 软件（ISCE、LiCSBAS、MintPy、GAMMA）：

| 特性 | 行业标准 | 现有系统 |
|---|---|---|
| 主/辅影像分池 | 明确的 reference/secondary 选择 | ❌ 无 |
| 配对策略 | SBAS / Sequential / Star | ❌ 仅全组合 |
| 时间范围控制 | 主辅池独立时间段 | ❌ 单一 start_date |
| 参考影像指定 | 支持手动或自动选择 | ❌ 无 |
| 多卫星支持 | 预留扩展机制 | ⚠️ 硬编码 LT-1 |

### 1.3 设计目标

1. **双池配对机制**：主影像池和辅影像池独立时间范围
2. **多策略支持**：SBAS / Sequential / Star 三种标准策略
3. **参考影像控制**：自动选择或用户指定
4. **多卫星扩展性**：预留 Sentinel-1、ALOS-2、TerraSAR-X 等卫星接入
5. **向后兼容**：现有配对逻辑作为 SBAS 策略保留

---

## 二、配对策略详解

### 2.1 SBAS（短基线子集）

**定义**：在时空基线阈值内全组合配对，形成冗余网络。

**适用场景**：
- 大范围形变监测
- 需要高时间分辨率和空间覆盖
- 对大气误差敏感的区域

**参数**：
- `time_baseline_min/max`：时间基线范围（天）
- `spatial_baseline_max_meters`：footprint 中心距上限（米，兼容字段名保留）
- `overlap_threshold`：两景 footprint 最小重叠率（兼容字段名保留）
- `coverage_diversity_penalty`：覆盖多样性惩罚因子

**配对逻辑**：
```
主池影像 × 辅池影像 → 满足时空基线约束 → 覆盖优化 → 最终配对
```

**示例**：
```
主池: 2024-01-01 ~ 2024-06-30 (10 景)
辅池: 2024-07-01 ~ 2024-12-31 (12 景)
时间基线: 30~90 天
→ 生成 45 对（经覆盖优化后约 20 对）
```

### 2.2 Sequential（顺序配对）

**定义**：按时间排序，每景影像与后续 N 景配对。

**适用场景**：
- 连续监测（如滑坡、地面沉降）
- 需要时间连续性的形变序列
- 计算资源有限时

**参数**：
- `num_connections`：每景与后续几景配对（默认 1）
- 其他时空约束同 SBAS

**配对逻辑**：
```
1. 合并主辅池，按日期排序
2. 每景与后续 num_connections 景配对
3. 应用时空基线过滤
```

**示例**：
```
影像序列: [A, B, C, D, E, F]
num_connections = 2
→ 配对: A-B, A-C, B-C, B-D, C-D, C-E, D-E, D-F, E-F
```

### 2.3 Star（星型配对）

**定义**：选择一个参考影像，所有其他影像与它配对。

**适用场景**：
- 时序形变分析（PS-InSAR 前处理）
- 需要统一参考基准
- 参考影像质量已知且优秀

**参数**：
- `reference_image_id`：指定参考影像 ID（可选）
- 自动选择策略：选择与其他影像平均重叠率最高的影像

**配对逻辑**：
```
1. 确定参考影像（用户指定 或 自动选择）
2. 参考影像 × 所有其他影像
3. 应用时空基线过滤
```

**示例**：
```
参考影像: 2024-06-15 (质量最优)
其他影像: 20 景
→ 配对: 20 对（全部以 2024-06-15 为主影像）
```

---

## 三、双池配对机制

### 3.1 时间范围控制

| 参数 | 说明 | 默认值 |
|---|---|---|
| `master_date_from` | 主影像起始日期 (YYYYMMDD) | None（不限） |
| `master_date_to` | 主影像截止日期 (YYYYMMDD) | None（不限） |
| `slave_date_from` | 辅影像起始日期 (YYYYMMDD) | None（不限） |
| `slave_date_to` | 辅影像截止日期 (YYYYMMDD) | None（不限） |

### 3.2 池子定义规则

**主影像池**：
```sql
WHERE imaging_date >= master_date_from
  AND imaging_date <= master_date_to
  AND (其他约束: 卫星、轨道、模式、极化、精轨...)
```

**辅影像池**：
```sql
WHERE imaging_date >= slave_date_from
  AND imaging_date <= slave_date_to
  AND (其他约束: 卫星、轨道、模式、极化、精轨...)
```

### 3.3 向后兼容

当四个日期参数全为 `None` 时：
- 主辅池合并为同一个池子
- 行为等效于现有系统（`m.imaging_date <= s.imaging_date` 避免重复）

---

## 四、多卫星扩展设计

### 4.1 当前硬编码问题

现有代码中卫星相关硬编码：
```python
# spatial_service.py
if master.satellite != slave.satellite:
    continue

# 前端显示
satellite: "LT-1"
```

### 4.2 扩展方案

**数据库层**：
- `radar_data.satellite` 字段已支持任意字符串，无需修改
- 配对函数增加 `p_allowed_satellites` 参数（数组）

**后端层**：
```python
class PairingRequest(BaseModel):
    # 新增
    allowed_satellites: Optional[List[str]] = None  # ["LT-1", "S1A", "S1B"]
    cross_satellite_pairing: bool = False           # 是否允许跨卫星配对
```

**前端层**：
- 配对 Modal 增加卫星多选框
- 从数据库动态获取可用卫星列表：`SELECT DISTINCT satellite FROM radar_data`

**配对逻辑**：
```python
# 同卫星配对（默认）
if not cross_satellite_pairing:
    if master.satellite != slave.satellite:
        continue

# 跨卫星配对（高级功能，需谨慎）
if allowed_satellites:
    if master.satellite not in allowed_satellites:
        continue
    if slave.satellite not in allowed_satellites:
        continue
```

### 4.3 卫星特性差异处理

不同卫星需要不同的配对参数：

| 卫星 | 典型时间基线 | 典型 footprint 中心距上限 | 波长 | 备注 |
|---|---|---|---|---|
| LT-1 | 30~90 天 | < 3000 m | L 波段 | 当前系统 |
| Sentinel-1 | 6~12 天 | < 150 m | C 波段 | 高重访频率 |
| ALOS-2 | 14 天 | < 500 m | L 波段 | 与 LT-1 类似 |
| TerraSAR-X | 11 天 | < 200 m | X 波段 | 高分辨率 |

**建议**：
- 前端提供"卫星预设"按钮，自动填充推荐参数
- 数据库增加 `satellite_config` 表存储默认配对参数

---

## 五、数据库函数设计

### 5.1 新函数签名

```sql
CREATE OR REPLACE FUNCTION find_dinsar_pairs_v2(
    -- 时空约束
    p_time_baseline_min INTEGER,
    p_time_baseline_max INTEGER,
    p_spatial_baseline_max_meters NUMERIC,
    p_overlap_threshold NUMERIC,
    p_aoi_geom GEOMETRY DEFAULT NULL,
    p_require_orbit_data BOOLEAN DEFAULT TRUE,
    p_require_same_imaging_mode BOOLEAN DEFAULT TRUE,
    p_require_same_polarization BOOLEAN DEFAULT TRUE,
    p_aoi_overlap_threshold NUMERIC DEFAULT NULL,

    -- 双池日期（新增）
    p_master_date_from TEXT DEFAULT NULL,
    p_master_date_to TEXT DEFAULT NULL,
    p_slave_date_from TEXT DEFAULT NULL,
    p_slave_date_to TEXT DEFAULT NULL,

    -- 多卫星支持（新增）
    p_allowed_satellites TEXT[] DEFAULT NULL,
    p_cross_satellite_pairing BOOLEAN DEFAULT FALSE
)
RETURNS TABLE (
    master_id INTEGER,
    slave_id INTEGER,
    master_imaging_date TEXT,
    slave_imaging_date TEXT,
    time_baseline_days INTEGER,
    spatial_baseline_meters NUMERIC,
    overlap_ratio NUMERIC
)
```

### 5.2 核心 SQL 逻辑变化

```sql
-- 现有逻辑
WHERE m.id < s.id  -- 避免重复
  AND m.satellite = s.satellite

-- 新逻辑
WHERE m.id <> s.id
  AND m.imaging_date <= s.imaging_date  -- 主影像早于辅影像
  -- 主池日期约束
  AND (p_master_date_from IS NULL OR m.imaging_date >= p_master_date_from)
  AND (p_master_date_to IS NULL OR m.imaging_date <= p_master_date_to)
  -- 辅池日期约束
  AND (p_slave_date_from IS NULL OR s.imaging_date >= p_slave_date_from)
  AND (p_slave_date_to IS NULL OR s.imaging_date <= p_slave_date_to)
  -- 卫星约束
  AND (p_allowed_satellites IS NULL OR m.satellite = ANY(p_allowed_satellites))
  AND (p_allowed_satellites IS NULL OR s.satellite = ANY(p_allowed_satellites))
  AND (p_cross_satellite_pairing OR m.satellite = s.satellite)
```

### 5.3 部署策略

1. 创建 `003_pairing_enhancement.sql` 迁移文件
2. `init_db.py` 自动应用（已有机制，line 354-356）
3. 保留 `find_dinsar_pairs` 旧函数（向后兼容）
4. 后端优先调用 `find_dinsar_pairs_v2`，失败时回退

---

## 六、后端 API 设计

### 6.1 Schema 变更

```python
# models/schemas.py

class PairingRequest(BaseModel):
    """D-InSAR 配对请求参数（增强版）"""

    # === 时空约束（保留） ===
    time_baseline_min: int = Field(default=1, ge=0, le=3650)
    time_baseline_max: int = Field(default=90, ge=1, le=3650)
    overlap_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    spatial_baseline_max_meters: int = Field(default=3000, ge=0, le=100000)
    coverage_diversity_penalty: float = Field(default=0.3, ge=0.0, le=1.0)
    require_same_imaging_mode: bool = True
    require_same_polarization: bool = True
    aoi_overlap_threshold: Optional[float] = Field(default=None, ge=0.0, le=1.0)

    # === 双池日期（新增） ===
    master_date_from: Optional[str] = Field(default=None, pattern=r'^\d{8}$')
    master_date_to: Optional[str] = Field(default=None, pattern=r'^\d{8}$')
    slave_date_from: Optional[str] = Field(default=None, pattern=r'^\d{8}$')
    slave_date_to: Optional[str] = Field(default=None, pattern=r'^\d{8}$')

    # === 配对策略（新增） ===
    strategy: str = Field(default="sbas", pattern=r'^(sbas|sequential|star)$')
    num_connections: int = Field(default=1, ge=1, le=10)
    reference_image_id: Optional[int] = None

    # === 多卫星支持（新增） ===
    allowed_satellites: Optional[List[str]] = None
    cross_satellite_pairing: bool = False

    # === 向后兼容（保留） ===
    start_date: Optional[str] = Field(default=None, pattern=r'^\d{8}$')

    @field_validator('master_date_to')
    def validate_master_date_range(cls, v, info):
        if v and info.data.get('master_date_from'):
            if v < info.data['master_date_from']:
                raise ValueError('master_date_to must >= master_date_from')
        return v

    @field_validator('slave_date_to')
    def validate_slave_date_range(cls, v, info):
        if v and info.data.get('slave_date_from'):
            if v < info.data['slave_date_from']:
                raise ValueError('slave_date_to must >= slave_date_from')
        return v
```

### 6.2 服务层变更

```python
# services/spatial_service.py

async def find_dinsar_pairs(
    self,
    db: AsyncSession,
    params: PairingRequest,
    aoi_wkt: Optional[str] = None,
    require_orbit_data: bool = True
) -> Tuple[List[RadarPair], List[str], bool]:
    """
    增强版配对逻辑：
    1. 调用 find_dinsar_pairs_v2 SQL 函数
    2. 根据 strategy 后处理结果
    3. 回退路径同步更新
    """

    # 1. 调用数据库函数
    pairs = await self._call_db_pairing_function(db, params, aoi_wkt, require_orbit_data)

    # 2. 策略后处理
    if params.strategy == "sbas":
        # 覆盖优化（现有逻辑）
        pairs = self._optimize_coverage_diversity(pairs, params.coverage_diversity_penalty)

    elif params.strategy == "sequential":
        # 顺序配对
        pairs = self._apply_sequential_strategy(pairs, params.num_connections)

    elif params.strategy == "star":
        # 星型配对
        pairs = self._apply_star_strategy(pairs, params.reference_image_id)

    # 3. 生成 task_name 并去重
    pairs = self._generate_task_names(pairs)

    return pairs, warnings, fallback_used
```

---

## 七、前端 UI 设计

### 7.1 配对 Modal 布局

```
┌─────────────────────────────────────────────────┐
│ D-InSAR 配对参数                                 │
├─────────────────────────────────────────────────┤
│ 配对策略: ○ SBAS  ○ Sequential  ○ Star          │
│                                                  │
│ [策略说明]                                       │
│ SBAS: 短基线子集，形成冗余网络，适合大范围监测    │
│                                                  │
├─────────────────────────────────────────────────┤
│ 主影像时间范围                                   │
│  从: [日期选择器 YYYYMMDD]  至: [日期选择器]     │
│                                                  │
│ 辅影像时间范围                                   │
│  从: [日期选择器 YYYYMMDD]  至: [日期选择器]     │
│                                                  │
│ ☑ 使用双池模式（不勾选则主辅池合并）             │
├─────────────────────────────────────────────────┤
│ 时间基线: [1] ~ [90] 天                         │
│ footprint 中心距上限: [3000] 米                  │
│ 两景 footprint 最小重叠率: [0.5]                 │
│ 覆盖多样性惩罚: [0.3]                            │
│                                                  │
│ ☑ 成像模式一致  ☑ 极化一致  ☑ 仅精轨影像        │
├─────────────────────────────────────────────────┤
│ AOI 来源: ○ 上传 SHP  ○ 行政区选择              │
│ [AOI 选择区域...]                                │
│                                                  │
│ AOI 覆盖率阈值: [0] (0 表示不限制)               │
├─────────────────────────────────────────────────┤
│ 高级选项 [展开 ▼]                                │
│  卫星选择: ☑ LT-1  ☐ Sentinel-1  ☐ ALOS-2      │
│  ☐ 允许跨卫星配对（谨慎使用）                    │
│                                                  │
│  Sequential 参数:                                │
│    每景配对数: [1]                               │
│                                                  │
│  Star 参数:                                      │
│    参考影像: [自动选择 ▼] 或 手动指定 ID: [___]  │
├─────────────────────────────────────────────────┤
│ [取消]                            [开始配对]     │
└─────────────────────────────────────────────────┘
```

### 7.2 策略切换交互

- 选择 SBAS：显示"覆盖多样性惩罚"参数
- 选择 Sequential：显示"每景配对数"参数
- 选择 Star：显示"参考影像选择"下拉框（从已有影像列表动态加载）

### 7.3 卫星选择

- 从 `GET /api/radar-data/available-satellites` 动态获取
- 默认只勾选 LT-1
- 跨卫星配对显示警告提示

---

## 八、实施计划

### Phase 1: 数据库层（1 天）

- [ ] 创建 `003_pairing_enhancement.sql`
- [ ] 实现 `find_dinsar_pairs_v2` 函数
- [ ] 更新 `init_db.py` 应用迁移
- [ ] 测试 SQL 函数（手动执行验证）

### Phase 2: 后端层（2 天）

- [ ] 更新 `PairingRequest` Schema
- [ ] 实现策略后处理逻辑（Sequential / Star）
- [ ] 更新 `spatial_service.py` 调用新函数
- [ ] 更新回退路径逻辑
- [ ] 新增 `GET /api/radar-data/available-satellites` 端点
- [ ] 单元测试（pytest）

### Phase 3: 前端层（2 天）

- [ ] 更新 `pairingStore.js` 增加新字段
- [ ] 重构 `PairingModal.jsx` UI
- [ ] 实现策略切换交互
- [ ] 实现卫星多选
- [ ] 更新 `usePairingLogic.js` 传参
- [ ] 前端构建测试

### Phase 4: 集成测试（1 天）

- [ ] 端到端测试（SBAS / Sequential / Star）
- [ ] 双池配对测试
- [ ] 多卫星配对测试
- [ ] 向后兼容测试（旧参数仍可用）
- [ ] 性能测试（大数据量）

### Phase 5: 文档与部署（0.5 天）

- [ ] 更新 `CLAUDE.md` 记录改动
- [ ] 更新用户手册（如有）
- [ ] 部署到测试环境
- [ ] 用户验收测试

**总计**: 约 6.5 天

---

## 九、风险与缓解

| 风险 | 影响 | 缓解措施 |
|---|---|---|
| SQL 函数性能下降 | 配对速度变慢 | 保留旧函数，性能对比测试 |
| 前端 UI 复杂度增加 | 用户学习成本 | 提供"快速模式"和"高级模式"切换 |
| 多卫星配对参数不当 | 生成无效配对 | 前端参数校验 + 后端警告提示 |
| 向后兼容性破坏 | 旧配对失效 | 保留 `start_date` 参数，自动转换 |

---

## 十、未来扩展

1. **基线网络可视化**：时间-中心距散点图（D3.js / ECharts）
2. **配对质量评分**：根据相干性、大气条件预估配对质量
3. **自动参数推荐**：基于历史配对结果的机器学习推荐
4. **批量配对模板**：保存常用配对参数为模板
5. **Sentinel-1 轨道框架**：支持 Sentinel-1 的 Track/Frame 概念

---

## 附录 A：术语对照表

| 中文 | 英文 | 说明 |
|---|---|---|
| 主影像 | Master / Reference | 配对中的参考影像 |
| 辅影像 | Slave / Secondary | 配对中的从属影像 |
| 时间基线 | Temporal Baseline | 两景影像的时间间隔 |
| footprint 中心距 | Footprint Center Distance | 两景影像 footprint 的中心距离 |
| 短基线子集 | SBAS (Small Baseline Subset) | 配对策略之一 |
| 星型配对 | Star Graph | 单主影像配对策略 |
| 顺序配对 | Sequential Pairing | 时间顺序配对策略 |

---

**文档结束**
