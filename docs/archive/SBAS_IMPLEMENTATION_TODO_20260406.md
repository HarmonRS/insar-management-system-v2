# SBAS 系统实施 TODO

更新日期：2026-04-06

## Phase 1：低侵入落地

目标：

- 不影响 D-InSAR
- 让系统开始承接 SBAS 生产记录与结果目录

### 后端模型

- [x] 新增 `PsTimeseriesRunORM`
- [x] 新增 `PsTimeseriesRun` schema
- [x] 为 `settings` 增加 `TIMESERIES_*` / `PSINSAR_PRODUCT_DIR` 配置

### 后端服务

- [x] 新增 `timeseries_service.py`
- [x] 新增 `psinsar_catalog_service.py`
- [x] 增加 SBAS prepare 任务处理逻辑

### 后端路由

- [x] 新增 `timeseries_production.py`
- [x] 新增 `ps_products.py`
- [x] 注册新路由

### 前端

- [x] 新增 `TimeseriesProductionPanel.jsx`
- [x] 新增 `PsinsarCatalogPanel.jsx`
- [x] 新增 `timeseriesProduction.js`
- [x] 新增 `psinsarProducts.js`
- [x] 替换 `ps_production` 占位页
- [x] 替换 `ps_products` 占位页
- [x] 替换 `psinsar_results` 占位页

### 校验

- [x] 后端语法检查
- [x] 前端 build 检查
- [ ] 验证 D-InSAR 页面未受影响

## Phase 2：串接实验处理链

目标：

- 在系统内真正触发 SBAS 处理

### 处理链

- [x] `prepare` 生成标准 stack manifest
- [x] `materialize` 生成 stack 工作目录
- [ ] 接入 ISCE2 stack 执行
- [ ] 接入 MintPy SBAS 执行
- [ ] 接入 geocode/export/publish

### 任务编排

- [x] 新增 workflow steps
- [ ] 新增失败重试策略
- [x] 增加运行日志归集

## Phase 3：结果展示增强

目标：

- 从“结果存在”升级到“结果可分析”

### 展示

- [ ] 地图叠加 `velocity.tif`
- [ ] 按 AOI/时间筛选产品
- [ ] 结果与 run 联动查看

### 分析

- [ ] `geo_timeseries.h5` 点位查询
- [ ] 时间曲线图
- [ ] 质量掩膜联动
- [ ] 热点区域分析

## 当前阶段实施顺序

推荐顺序：

1. 文档补齐
2. 运行记录模型
3. prepare 任务
4. `psinsar` catalog
5. 前端生产页
6. 前端结果页
7. 静态校验

## 当前不做

- [ ] 不重构现有 D-InSAR 结果服务为完全通用框架
- [ ] 不改现有配对算法
- [ ] 不继续保留 SBAS 使用 `*_envi_import` 的旧逻辑
- [ ] 不在当前 D-InSAR 生产环境中直接做依赖升级
