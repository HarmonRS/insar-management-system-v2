#!/bin/bash
# 描述：自动根据GRD文件生成地图（依赖GMT 6+）
# 用法：./auto_plot_grd.sh <输入.grd> [输出文件名] [选项]

# 默认参数
input_grd=$1
output="map.png"     # 默认输出文件名
projection="M15c"    # 默认投影（Mercator，宽度15厘米）
colormap="viridis"   # 默认色标
title="Topography"   # 默认标题
annotate="a"         # 默认标注间隔（自动）

# 解析命令行参数
while [[ $# -gt 0 ]]; do
    case $1 in
        -o|--output) output="$2"; shift ;;
        -P|--projection) projection="$2"; shift ;;
        -C|--colormap) colormap="$2"; shift ;;
        -T|--title) title="$2"; shift ;;
        -A|--annotate) annotate="$2"; shift ;;
        *) ;;
    esac
    shift
done

# 检查输入文件是否存在
if [[ ! -f $input_grd ]]; then
    echo "错误：输入文件 $input_grd 不存在！"
    exit 1
fi

# 从GRD文件获取地理范围
region=$(gmt grdinfo $input_grd -I- | awk -F'R' '{print $2}')

# 生成临时CPT色标文件
gmt makecpt -Cturbo -T$(gmt grdinfo $input_grd -T | awk -F'T' '{print $2}') > tmp.cpt

# 开始绘图
gmt begin ${output%.*} pdf,png # 去除扩展名
    # 绘制GRD数据
    gmt grdimage $input_grd -R$region -J$projection -Ctmp.cpt -B$annotate -BWSne+t"$title"
    # 添加海岸线
    gmt coast -R$region -J$projection -W0.5p,black -Df
    # 添加色标
    gmt colorbar -DJBC+w10c/0.5c+o0/1c -Bxaf -By+l"Elevation (m)"
gmt end show

# 清理临时文件
#rm -f tmp.cpt

echo "地图已生成：$output"
