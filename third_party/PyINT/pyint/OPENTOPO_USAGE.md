# OpenTopography DEM 下载使用示例

## 快速开始

### 1. 获取免费API Key

访问: https://opentopography.org/myOpenTopo
- 注册账户 (免费)
- 在 'My Account' 页面请求 API key
- API key 立即可用

### 2. 下载不同分辨率的 DEM

#### SRTM 30m (推荐)
```bash
python makedem.py -r 116/117/39/40 --dem-source srtm \
    --opentopo-api-key YOUR_KEY \
    --opentopo-dem-type SRTMGL1
```

#### SRTM 90m
```bash
python makedem.py -r 116/117/39/40 --dem-source srtm \
    --opentopo-api-key YOUR_KEY \
    --opentopo-dem-type SRTMGL3
```

#### NASADEM 30m
```bash
python makedem.py -r 116/117/39/40 --dem-source srtm \
    --opentopo-api-key YOUR_KEY \
    --opentopo-dem-type NASADEM
```

#### Copernicus 30m
```bash
python makedem.py -r 116/117/39/40 --dem-source srtm \
    --opentopo-api-key YOUR_KEY \
    --opentopo-dem-type COP30
```

#### Copernicus 90m
```bash
python makedem.py -r 116/117/39/40 --dem-source srtm \
    --opentopo-api-key YOUR_KEY \
    --opentopo-dem-type COP90
```

## 数据对比

| DEM类型 | 分辨率 | 覆盖范围 | 推荐用途 |
|---------|--------|----------|----------|
| **SRTMGL1** | **30m** | 60°S-60°N | **SRTM研究首选** |
| SRTMGL3 | 90m | 60°S-60°N | 快速预览 |
| **NASADEM** | **30m** | 60°S-60°N | **高精度研究** |
| **COP30** | **30m** | 全球 | **全球覆盖首选** |
| COP90 | 90m | 全球 | 快速预览 |

## 推荐选择

1. **SRTM研究**: SRTMGL1 (30m)
2. **高精度需求**: NASADEM 或 COP30 (30m)
3. **全球覆盖**: COP30 (30m)
4. **快速预览**: SRTMGL3 或 COP90 (90m)

## 注意事项

- **SRTMGL1/COP30/NASADEM** (30m): 最大支持 450,000 km²
- **SRTMGL3/COP90** (90m): 最大支持 4,050,000 km²
- 所有数据集需要 OpenTopography API key
- 免费配额足够大部分科研用途

## 输出文件命名

- SRTMGL1_116_117_39_40.tif
- NASADEM_116_117_39_40.tif
- COP30_116_117_39_40.tif

文件名格式: `{DEM类型}_{西经}_{东经}_{南纬}_{北纬}.tif`

---

**更新日期**: 2026-03-13
