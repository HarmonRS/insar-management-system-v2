# 源影像叠加稳定化方案（Scheme B）

目标：解决“覆盖面是斜四边形，源影像是矩形包围框”的错位问题，优先保障长期稳定与前端流畅。

---

## 1. 问题本质

当前地图展示中：
- 覆盖范围来自 `coverage_polygon`（真实斜四边形）
- 源图叠加常用 `imageOverlay(url, bounds)`（矩形边界）

当源图本身有旋转/倾斜时，直接矩形贴图会产生明显错位。

---

## 2. 方案选择

### A（前端实时变换）
- 在浏览器端做逐帧几何变换
- 优点：改动快
- 缺点：前端负载高，图层增多后容易卡顿

### B（后端预纠正缓存）✅
- 扫描/构建阶段在后端完成几何纠正，前端只显示结果图
- 优点：渲染轻、稳定性高、易观测
- 缺点：后端实现复杂度更高

本项目已确定采用 **B 方案**。

---

## 3. 当前实现（2026-02-10）

## 3.1 缓存分层
- `backend/image_cache/radar_geo/`：地理纠正后的主缓存
- `backend/image_cache/radar_raw/`：原图回退缓存

## 3.2 后端能力
- 默认由手动扫描任务触发增量构建（前端“立即扫描”或 `POST /api/monitor/run-now`）
- 扫描时增量构建纠正缓存（`radar_geo`）
- 同步维护原图缓存（`radar_raw`）作为兜底
- 方向判定优先使用 XML 的 `sceneCornerCoord/refRow/refColumn`，避免仅靠角点名称导致镜像/翻转误判
- `GET /api/radar-data/{id}/thumb`：
  - 优先返回 `radar_geo`
  - 失败自动回退 `radar_raw`
- 新增状态接口：
  - `GET /api/radar-data/{id}/preview-status`
  - `POST /api/radar-data/{id}/rebuild-preview-cache`（管理员）

## 3.3 前端能力
- 每条源影像显示预览状态（纠正/回退/失败/未建）
- 管理员支持“重建”按钮
- 预览请求增加缓存键，避免浏览器长期缓存旧图

---

## 4. 关键配置项

```env
RADAR_GEO_CACHE_WORKERS=2
RADAR_GEO_CACHE_VERSION=b1
RADAR_GEO_CACHE_QUALITY=84
RADAR_PREVIEW_BUILD_ON_DEMAND=true
```

建议：
- 小规模先用 `RADAR_GEO_CACHE_WORKERS=1~2`
- 算法升级时提高 `RADAR_GEO_CACHE_VERSION` 触发重建

---

## 5. 验收标准

- 同一场景下，源图与覆盖面边界明显趋于一致
- 缩放/平移时交互平滑，无明显掉帧
- 构建失败可在状态接口与日志中定位原因
- 失败场景可回退原图缓存，不阻塞业务操作

---

## 6. 后续建议

1. 采集 3 类典型数据（升轨/降轨/大倾斜）做对齐验收
2. 统计 `radar_geo` 构建失败原因并分级治理
3. 发布前执行一次全量重建，提升线上命中率
