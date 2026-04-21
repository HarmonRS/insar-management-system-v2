# SRTM 自动下载功能说明

## 功能概述

`makedem.py` 现在支持自动下载SRTM数据,无需手动准备数据文件!

## 使用方法

### 方法1: 自动下载 (推荐)

**步骤1: 获取OpenTopography API key** (免费)

访问: https://opentopography.org/myOpenTopo
- 注册免费账户
- 在 'My Account' 页面请求API key
- API key立即可用,免费配额足够大部分用途

**步骤2: 运行命令**

```bash
# 使用API key自动下载SRTM数据
python makedem.py -r 116/117/39/40 --dem-source srtm --opentopo-api-key YOUR_API_KEY

# 或设置环境变量
export OPENTOPO_API_KEY="your_api_key_here"
python makedem.py -r 116/117/39/40 --dem-source srtm
```

### 方法2: 使用预下载的.hgt文件

如果已有SRTM .hgt文件:

```bash
python makedem.py -r 116/117/39/40 --dem-source srtm --srtm-data-dir /path/to/srtm_data
```

## SRTM数据规格

- **分辨率**: 90米 (SRTM GL3)
- **覆盖**: 全球 (60°S - 60°N)
- **数据源**: OpenTopography API
- **格式**: GeoTIFF
- **费用**: 免费 (需要免费API key)

## 对比

| 方法 | 优点 | 缺点 |
|------|------|------|
| 自动下载 | ✓ 全自动<br>✓ 无需准备数据<br>✓ 快速便捷 | ✗ 需要API key<br>✗ 需要网络 |
| 预下载文件 | ✓ 离线使用<br>✓ 不依赖API | ✗ 需要手动下载<br>✗ 需要管理文件 |

## 示例

### 北京地区 (116-117°E, 39-40°N)

```bash
# 自动下载
python makedem.py -r 116/117/39/40 --dem-source srtm --opentopo-api-key YOUR_KEY

# 输出文件: SRTM_116_117_39_40.tif
```

### 使用SLC参数文件

```bash
# 自动确定区域范围
python makedem.py -s /path/to/slc.par --dem-source srtm --opentopo-api-key YOUR_KEY
```

## 常见问题

**Q: 为什么需要API key?**

A: OpenTopography要求API key来管理数据访问配额。免费API key配额足够大部分科研和教育用途。

**Q: 如何获取API key?**

A: 
1. 访问 https://opentopography.org/myOpenTopo
2. 注册账户 (免费)
3. 在账户页面点击 "Request API Key"
4. API key会立即显示,可以直接使用

**Q: 免费API key有什么限制?**

A: 
- SRTM GL3: 最大4,050,000 km² per request
- 对于大部分研究区域完全够用
- 如果需要更大范围,可以分块下载

**Q: SRTM vs Copernicus vs NASADEM?**

A:
- **Copernicus (推荐)**: 30m分辨率,全球覆盖,全自动下载
- **NASADEM**: 30m分辨率,NASA官方,60°S-60°N覆盖
- **SRTM**: 90m分辨率,历史数据,适合对比研究

**Q: 没有.hgt文件怎么办?**

A: 直接使用方法1自动下载,无需准备任何数据文件!

## 故障排除

### 错误: HTTP 401

**原因**: API key无效或已过期

**解决**: 
- 检查API key是否正确
- 确认账户状态正常
- 如需要,重新生成API key

### 错误: HTTP 403

**原因**: 超出配额或访问限制

**解决**:
- 检查请求区域大小
- 分块下载大区域
- 确认API key配额

### 错误: 文件下载失败

**原因**: 网络问题

**解决**:
- 检查网络连接
- 重试命令
- 使用预下载方法

## Python版本兼容性

- **方法1 (自动下载)**: 支持所有Python版本
- **方法2 (使用.hgt文件)**:
  - Python >= 3.12: 使用srtm库自动查询
  - Python < 3.12: 使用GDAL手动处理

两种方法结果相同,只是处理方式不同。

## 相关资源

- OpenTopography: https://opentopography.org/
- API文档: https://portal.opentopography.org/apidocs/
- 获取API key: https://opentopography.org/myOpenTopo
- SRTM数据介绍: https://www2.jpl.nasa.gov/srtm/

---

**更新日期**: 2026-03-13  
**功能版本**: v1.0
