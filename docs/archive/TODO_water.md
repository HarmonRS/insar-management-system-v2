# 水体提取与洪涝监测 — 开发 TODO

## 架构概览

```
Phase 1: 水体提取  → WaterMaskORM（每景影像的水体掩膜）
Phase 2: 水体配对  → WaterPairORM（参考期 + 监测期）
Phase 3: 变化检测  → FloodEventORM（洪涝事件 + 告警）
```

依赖库（InSAR conda 环境已有）：rasterio, shapely, numpy, scipy, Pillow

---

## 任务列表

### Step 1：ORM 建表
- [x] W01 `orm.py` 新增 WaterMaskORM、WaterPairORM、FloodEventORM
- [x] W02 `models/__init__.py` 导出新 ORM
- [x] W03 Alembic migration 0002_water_tables.py

### Step 2：核心服务
- [x] W04 `water_service.py` Phase 1 — 单景水体提取（OTSU + 形态学 + 矢量化）
- [x] W05 `water_service.py` Phase 2 — 水体配对逻辑（空间重叠 + 时间筛选）
- [x] W06 `water_service.py` Phase 3 — 变化检测（掩膜差值 + 面积统计 + 告警）

### Step 3：Job 集成
- [x] W07 `job_handlers.py` 新增 WATER_EXTRACT / WATER_DETECT job 类型

### Step 4：路由
- [x] W08 `routers/water.py` 全部端点
- [x] W09 `routers/__init__.py` 注册 water router

### Step 5：前端
- [x] W10 `api/water.js` API 层
- [x] W11 `WaterMonitorPanel.jsx` 三 Tab 面板（提取/配对/事件）
- [x] W12 `App.jsx` 注册新面板入口

---

## 端点设计

```
POST /water/extract              # 批量提取（传 radar_data_ids 列表）
GET  /water/masks                # 查询水体掩膜列表（分页）
GET  /water/masks/{id}/preview   # 水体掩膜预览图

POST /water/pairs                # 创建配对（reference_id + monitor_id）
GET  /water/pairs                # 查询配对列表
POST /water/pairs/{id}/detect    # 对指定配对执行变化检测

GET  /water/events               # 查询洪涝事件列表
GET  /water/events/{id}/preview  # 变化图预览
```

---

## 关键算法备忘

### OTSU（纯 numpy 实现，不依赖 scikit-image）
```python
def _otsu_threshold(arr_db):
    hist, bin_edges = np.histogram(arr_db[np.isfinite(arr_db)], bins=256)
    bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2
    total = hist.sum()
    w0 = np.cumsum(hist) / total
    w1 = 1 - w0
    mu0 = np.cumsum(hist * bin_centers) / (np.cumsum(hist) + 1e-10)
    mu1 = (np.sum(hist * bin_centers) - np.cumsum(hist * bin_centers)) / (np.cumsum(hist[::-1])[::-1] + 1e-10)
    sigma_b = w0 * w1 * (mu0 - mu1) ** 2
    return bin_centers[np.argmax(sigma_b)]
```

### 水体掩膜预览图
- 原始影像灰度拉伸为背景（半透明）
- 水体区域叠加蓝色（RGBA: 0, 100, 255, 180）
- 新增水体叠加红色（洪涝变化图）

---

## 状态

**全部完成** ✓ — 等待测试

### 测试步骤
1. 重启后端（新表由 create_all 自动创建）
2. 前端强制刷新，进入"结果分析"→"水体监测"
3. Phase 1：输入已入库的雷达数据 ID，提交提取任务，等待完成后刷新列表
4. Phase 2：选参考期和监测期掩膜 ID，创建配对
5. Phase 3：对配对点击"执行变化检测"，在"洪涝事件"Tab 查看结果

### 已知限制
- `_find_geocoded_path` 依赖 `preview_cache_path` 或 `file_path`，若雷达数据没有地理编码文件则提取失败
- 告警阈值默认 30%，可通过 `.env` 中 `FLOOD_ALERT_THRESHOLD=0.3` 调整
