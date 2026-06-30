# LT1AssetTool

独立 EXE 工具，只做三件事：

1. 扫描服务器资产路径，列出服务器现在有哪些资产，生成一个随身 JSON。
2. 扫描一个或多个 UNC 路径，读取随身 JSON，服务器已有资产不参与复制，剩下的复制到一个或多个指定路径。
3. 将一个或多个磁盘路径下的资产复制或剪切到服务器资产路径，并提示重新执行第 1 步。

## 资产识别

当前只识别平铺文件：

- `LT1*.tar.gz`
- `LT1*.tgz`
- `LT1*.tar`
- `LT1*.zip`
- `LT1*.txt`

判断是否已有资产使用：

```text
资产类型 + 文件名 + 文件大小
```

## 第 1 步：扫描服务器资产路径

输入：

```text
服务器资产路径：
D:\LuTan1_Image_Pool_Zip
D:\LT1_data_lsarorbit

服务器资产 JSON 保存为：
E:\server_assets.json
```

输出 JSON 示例：

```json
{
  "schema": "lt1_asset_inventory.v2",
  "generated_at": "2026-06-22 16:00:00",
  "roots": [
    "D:\\LuTan1_Image_Pool_Zip",
    "D:\\LT1_data_lsarorbit"
  ],
  "asset_count": 2,
  "assets": [
    {
      "kind": "lt1_archive",
      "name": "LT1A_xxx.tar.gz",
      "path": "D:\\LuTan1_Image_Pool_Zip\\LT1A_xxx.tar.gz",
      "size": 123456789,
      "mtime": 1782100000.0
    },
    {
      "kind": "lt1_orbit",
      "name": "LT1A_GpsData_GAS_C_20240101.txt",
      "path": "D:\\LT1_data_lsarorbit\\LT1A_GpsData_GAS_C_20240101.txt",
      "size": 345678,
      "mtime": 1782100100.0
    }
  ]
}
```

以后就带着这个 JSON 去内网机器。

## 第 2 步：从 UNC 补拷缺失资产

输入：

```text
读取服务器资产 JSON：
E:\server_assets.json

UNC 源路径：
\\server01\lt1_archives
\\server02\lt1_orbits

复制目标路径：
E:\LT1_TRANSFER
F:\LT1_TRANSFER
```

规则：

- JSON 中已有且大小一致：跳过
- JSON 中有同名资产但大小不同：报告冲突，不复制
- JSON 中没有：复制到指定目标路径
- 多个目标路径时，选择第一个空间足够的路径
- 如果目标路径已有同名文件且大小一致：跳过
- 复制过程先写 `.part`，完成并校验大小后再改名

## 第 3 步：磁盘导入服务器

输入：

```text
磁盘资产路径：
E:\LT1_TRANSFER
F:\LT1_TRANSFER

服务器资产路径：
D:\LuTan1_Image_Pool_Zip
D:\LT1_data_lsarorbit
```

可选：

```text
剪切到服务器
```

规则：

- 服务器已有且大小一致：跳过
- 服务器有同名资产但大小不同：报告冲突，不覆盖
- 服务器没有：复制或剪切到服务器路径
- 多个服务器路径时，选择第一个空间足够的路径

执行第 3 步之后，回到服务器重新执行第 1 步，生成新的随身 JSON。

## 报告和日志

需要指定 `报告/日志目录`，例如：

```text
E:\LT1AssetToolReports
```

工具会生成：

```text
E:\LT1AssetToolReports\
  logs\
    run_YYYYMMDD_HHMMSS.log
  reports\
    unc_copy_report_YYYYMMDD_HHMMSS.csv
    disk_import_report_YYYYMMDD_HHMMSS.csv
```

CSV 可以用 Excel 打开。

## 命令行

扫描服务器：

```powershell
python lt1_data_sync_cli.py scan-server `
  --report-dir "E:\LT1AssetToolReports" `
  --server-roots "D:\LuTan1_Image_Pool_Zip;D:\LT1_data_lsarorbit" `
  --output-json "E:\server_assets.json"
```

从 UNC 复制缺失资产：

```powershell
python lt1_data_sync_cli.py copy-unc `
  --report-dir "E:\LT1AssetToolReports" `
  --server-json "E:\server_assets.json" `
  --unc-roots "\\server01\lt1_archives;\\server02\lt1_orbits" `
  --targets "E:\LT1_TRANSFER;F:\LT1_TRANSFER" `
  --execute
```

磁盘导入服务器：

```powershell
python lt1_data_sync_cli.py import-disk `
  --report-dir "E:\LT1AssetToolReports" `
  --disk-roots "E:\LT1_TRANSFER;F:\LT1_TRANSFER" `
  --server-roots "D:\LuTan1_Image_Pool_Zip;D:\LT1_data_lsarorbit" `
  --execute
```

## 打包 EXE

```powershell
cd D:\Code\Insar_management_system_v2\tools\lt1_data_sync
.\build_exe.ps1 -Python "C:\ProgramData\anaconda3\envs\InSAR\python.exe"
```

输出：

```text
dist\LT1DataSync.exe
```
