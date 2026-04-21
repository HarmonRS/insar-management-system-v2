# DEM 数据下载指南

本文档介绍如何使用 `makedem.py` 下载和处理不同来源的 DEM 数据。

## 支持的 DEM 数据源

### 1. Copernicus DEM (默认推荐)

**特点:**
- 分辨率: 30米
- 覆盖: 全球 (60°S - 80°N)
- 数据质量: 高精度,全球一致性
- 下载方式: 自动从 AWS/OpenTopography 下载
- 费用: 免费

**使用方法:**
```bash
# 基本使用
python makedem.py -r 116/117/39/40 --dem-source copernicus

# 指定并行下载数量(加速下载)
python makedem.py -r 116/117/39/40 --dem-source copernicus --num-workers 8

# 指定输出目录
python makedem.py -r 116/117/39/40 --dem-source copernicus --dir /path/to/output
```

**优点:**
- ✓ 全自动下载
- ✓ 高精度数据
- ✓ 全球覆盖
- ✓ 支持并行下载加速

**缺点:**
- ✗ 需要网络连接
- ✗ 下载时间取决于网络速度

---

### 2. NASADEM

**特点:**
- 分辨率: 30米
- 覆盖: 全球 (60°S - 60°N)
- 数据质量: SRTM + ICESat 数据融合
- 下载方式: 通过 NASADEM Python 库自动下载
- 费用: 免费

**依赖安装:**
```bash
pip install nasadem rasters
```

**使用方法:**
```bash
# 基本使用
python makedem.py -r 116/117/39/40 --dem-source nasadem

# 指定输出目录
python makedem.py -r 116/117/39/40 --dem-source nasadem --dir /path/to/output
```

**优点:**
- ✓ 全自动下载
- ✓ NASA官方数据
- ✓ 融合多源数据,精度提升

**缺点:**
- ✗ 需要网络连接
- ✗ 首次下载可能较慢
- ✗ 需要额外安装库
- ✗ 覆盖范围小于 Copernicus (南北纬60°限制)

---

### 3. SRTM (需要预下载数据)

**特点:**
- 分辨率: 30米 (SRTM 1 arc-second) 或 90米 (SRTM 3 arc-second)
- 覆盖: 全球 (60°S - 60°N)
- 数据质量: 良好
- 下载方式: 需要手动预下载 .hgt 文件
- 费用: 免费

**依赖安装:**
```bash
# 注意: srtm库需要 Python >= 3.12
pip install srtm

# 检查Python版本
python --version  # 应该显示 3.12 或更高版本
```

**Python版本要求:**
- srtm 库要求 Python >= 3.12
- 如果您的Python版本 < 3.12，建议使用 Copernicus 或 NASADEM

**数据准备步骤:**

1. **下载 SRTM .hgt 文件**
   
   从以下网站之一下载所需区域的 .hgt 或 .hgt.zip 文件:
   - **CSI-CGIAR SRTM**: https://srtm.csi.cgiar.org/ (推荐,无需注册)
   - **USGS EarthExplorer**: https://earthexplorer.usgs.gov/ (需要注册)
   - **NASA EarthData**: https://urs.earthdata.nasa.gov/ (需要注册)

2. **文件命名规则**
   
   SRTM 文件按 1°×1° 分块命名:
   - `N39E116.hgt` → 北纬39-40°, 东经116-117°
   - `N39W110.hgt` → 北纬39-40°, 西经110-109°
   - `S10E120.hgt` → 南纬10-9°, 东经120-121°

3. **数据组织**
   
   将所有 .hgt 文件放在同一目录下,例如:
   ```
   /path/to/srtm_data/
   ├── N39E116.hgt
   ├── N39E117.hgt
   ├── N40E116.hgt
   └── N40E117.hgt
   ```

**使用方法:**
```bash
# 指定SRTM数据目录
python makedem.py -r 116/117/39/40 --dem-source srtm --srtm-data-dir /path/to/srtm_data

# 注意:如果不指定 --srtm-data-dir,程序会报错并提示
python makedem.py -r 116/117/39/40 --dem-source srtm
# 错误: 使用 SRTM 数据源需要指定 --srtm-data-dir 参数
```

**优点:**
- ✓ 离线使用(数据预下载后)
- ✓ 不依赖实时网络下载
- ✓ 数据源多样化

**缺点:**
- ✗ 需要手动下载数据文件
- ✗ 需要自行管理数据文件
- ✗ 需要确保覆盖目标区域的所有分块
- ✗ srtm 库对 .hgt.zip 压缩文件支持不稳定,建议解压后使用

---

## 使用建议

### 推荐使用顺序:

1. **首选: Copernicus DEM**
   - 全自动,高精度,全球覆盖
   - 使用 `--num-workers 8` 加速下载

2. **备选: NASADEM**
   - 全自动,NASA官方数据
   - 适合北纬60°以南,南纬60°以北区域

3. **离线环境: SRTM**
   - 仅在无网络环境或已有SRTM数据时使用
   - 需要提前准备数据文件

### 性能对比:

| DEM源 | 下载速度 | 数据精度 | 全球覆盖 | 离线使用 | 推荐指数 |
|-------|---------|---------|---------|---------|---------|
| Copernicus | ★★★★☆ | ★★★★★ | ★★★★★ | ✗ | ★★★★★ |
| NASADEM | ★★★☆☆ | ★★★★☆ | ★★★☆☆ | ✗ | ★★★★☆ |
| SRTM | N/A (离线) | ★★★★☆ | ★★★☆☆ | ✓ | ★★★☆☆ |

### 常见问题:

**Q: 如何确定需要下载哪些 SRTM 文件?**

A: 根据目标区域范围计算所需文件:
```python
# 示例: 区域 116/117/39/40 (东经116-117°, 北纬39-40°)
west, east, south, north = 116, 117, 39, 40

# 需要下载的文件:
# N39E116.hgt (39-40°N, 116-117°E)
# 如果区域更大,需要下载多个文件
```

**Q: SRTM .hgt.zip 文件可以直接使用吗?**

A: srtm 库对压缩文件支持不稳定,建议先解压:
```bash
cd /path/to/srtm_data/
unzip "*.hgt.zip"
```

**Q: 如何加速 Copernicus DEM 下载?**

A: 增加并行下载数量:
```bash
# 默认使用 4 个线程
python makedem.py -r 116/117/39/40 --dem-source copernicus --num-workers 4

# 加速到 8-16 个线程(根据网络情况调整)
python makedem.py -r 116/117/39/40 --dem-source copernicus --num-workers 16
```

**Q: 下载的数据格式是什么?**

A: 所有DEM源最终都会转换为 GAMMA/ROI_PAC 格式:
- `.dem` 文件: 二进制高程数据 (big-endian for GAMMA)
- `.dem.par` 文件: 参数文件

---

## 示例工作流程

### 场景1: 处理Sentinel-1数据(推荐Copernicus)

```bash
# 1. 确定研究区域范围
# 使用 SLC 参数文件自动确定
python makedem.py -s /path/to/slc.par --dem-source copernicus --num-workers 8

# 或手动指定区域
python makedem.py -r 116/117/39/40 --dem-source copernicus --num-workers 8
```

### 场景2: 离线处理(使用SRTM)

```bash
# 1. 提前下载SRTM数据
# 从 https://srtm.csi.cgiar.org/ 下载 N39E116.hgt, N39E117.hgt 等

# 2. 组织数据
mkdir -p ~/data/SRTM
mv N*.hgt ~/data/SRTM/

# 3. 使用预下载数据生成DEM
python makedem.py -r 116/117/39/40 --dem-source srtm --srtm-data-dir ~/data/SRTM
```

### 场景3: NASADEM对比测试

```bash
# 下载Copernicus DEM
python makedem.py -r 116/117/39/40 --dem-source copernicus --dir ./copernicus

# 下载NASADEM
python makedem.py -r 116/117/39/40 --dem-source nasadem --dir ./nasadem

# 对比两个DEM文件
# 使用 GMT 或其他工具进行可视化和分析
```

---

## 参考资料

- **Copernicus DEM**: https://copernicus-dem-90m.s3.amazonaws.com/readme.html
- **NASADEM**: https://github.com/DFS-iData/NASADEM
- **SRTM CSI-CGIAR**: https://srtm.csi.cgiar.org/
- **GAMMA Software**: https://gamma-rs.ch/

---

**最后更新**: 2026-03-13
**作者**: iFlow CLI
