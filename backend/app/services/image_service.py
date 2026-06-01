"""
图像处理服务

提供 D-InSAR 结果图像处理功能：
- 生成可视化图像
- 提取影像 footprint
- 加载颜色表
- 生成缩略图

优化策略：
- 使用 GDAL 降采样提取 footprint，减少内存占用
- 颜色表使用单例模式缓存
- 支持自动透明边缘裁剪
"""
import os
import time
import json
from collections import deque
from typing import Tuple, Optional, Dict, Any, List
from PIL import Image
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import transform as transform_coords, transform_bounds
from rasterio.features import shapes
from rasterio.transform import Affine
import numpy as np
import matplotlib.cm as matplotlib_cm
import matplotlib.colors as mcolors
from shapely.geometry import shape, Polygon, mapping
from shapely.ops import unary_union, transform as shapely_transform
from pyproj import Transformer

from ..config import settings


class ImageService:
    """
    图像处理服务
    
    提供 D-InSAR 结果图像的读取、处理和可视化功能。
    使用 GDAL 降采样策略优化大数据量图像处理。
    """
    
    # 颜色表缓存（单例模式）
    _colormap_cache: Optional[Tuple[mcolors.LinearSegmentedColormap, float, float]] = None
    _colormap_filename: Optional[str] = None
    
    # 默认缩略图尺寸
    DEFAULT_THUMBNAIL_SIZE = (
        settings.DINSAR_THUMBNAIL_MAX_SIZE,
        settings.DINSAR_THUMBNAIL_MAX_SIZE
    )
    
    @staticmethod
    def create_dinsar_image(
        file_path: str,
        auto_stretch: bool = False,
        max_size: Optional[Tuple[int, int]] = None
    ) -> Image.Image:
        """
        读取 D-InSAR 结果文件，应用颜色表，返回 PIL.Image 对象。
        
        Args:
            file_path: 结果文件路径
            auto_stretch: 是否使用分位数自动拉伸
            
        Returns:
            处理后的 PIL.Image 对象
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"结果文件不存在: {file_path}")
        
        # 加载颜色表
        try:
            custom_cmap, vmin, vmax = ImageService.load_colormap()
            if auto_stretch:
                vmin, vmax = None, None
        except (FileNotFoundError, ValueError) as e:
            print(f"错误: 无法加载自定义颜色表, 将回退到默认值. 错误: {e}")
            try:
                custom_cmap = mcolors.colormaps.get('viridis')
            except AttributeError:
                custom_cmap = matplotlib_cm.get_cmap('viridis')
            vmin, vmax = None, None
        
        with rasterio.open(file_path) as dataset:
            if max_size:
                max_w, max_h = max_size
                scale = max(dataset.width / max_w, dataset.height / max_h, 1)
                out_w = max(1, int(dataset.width / scale))
                out_h = max(1, int(dataset.height / scale))
                data = dataset.read(
                    1,
                    out_shape=(out_h, out_w),
                    resampling=Resampling.bilinear
                ).astype(float)
                mask = dataset.dataset_mask(out_shape=(out_h, out_w))
            else:
                data = dataset.read(1).astype(float)
                mask = dataset.dataset_mask()
            
            if np.all(mask == 0):
                img_array = np.zeros((data.shape[0], data.shape[1], 4), dtype=np.uint8)
            else:
                valid_data = data[mask == 255]
                
                if vmin is not None and vmax is not None and vmax > vmin:
                    data_normalized = (data - vmin) / (vmax - vmin)
                else:
                    p2, p98 = np.nanpercentile(valid_data, (2, 98))
                    if p98 > p2:
                        data_normalized = (data - p2) / (p98 - p2)
                    else:
                        data_normalized = np.zeros_like(data, dtype=float)
                
                data_normalized = np.clip(data_normalized, 0, 1)
                colored_data = custom_cmap(data_normalized)
                
                # 强制 NoData 区域透明
                colored_data[mask == 0] = (0, 0, 0, 0)
                img_array = (colored_data * 255).astype(np.uint8)
        
        return Image.fromarray(img_array)
    
    @staticmethod
    def generate_thumbnail(
        image: Image.Image,
        max_size: Optional[Tuple[int, int]] = None
    ) -> Image.Image:
        """
        生成缩略图。
        
        Args:
            image: 原始 PIL.Image
            max_size: 最大尺寸 (宽, 高)，默认使用 DEFAULT_THUMBNAIL_SIZE
            
        Returns:
            缩略图
        """
        if max_size is None:
            max_size = ImageService.DEFAULT_THUMBNAIL_SIZE
        
        # 自动裁剪透明边缘
        bbox = image.getbbox()
        if bbox:
            image = image.crop(bbox)
        
        # 生成缩略图
        thumb = image.copy()
        thumb.thumbnail(max_size, Image.Resampling.LANCZOS)
        return thumb
    
    @staticmethod
    def load_colormap(
        file_name: str = "qgis_color.txt"
    ) -> Tuple[mcolors.LinearSegmentedColormap, float, float]:
        """
        加载 QGIS 导出的颜色表（带缓存）。
        
        Args:
            file_name: 颜色表文件名
            
        Returns:
            (colormap, vmin, vmax) 元组
        """
        # 检查缓存
        if (
            ImageService._colormap_cache is not None and
            ImageService._colormap_filename == file_name
        ):
            return ImageService._colormap_cache
        
        # 构建文件路径
        cmap_path = os.path.join(settings.COLORMAPS_DIR, file_name)
        
        if not os.path.exists(cmap_path):
            raise FileNotFoundError(f"色彩映射文件未找到: {cmap_path}")
        
        # 解析颜色表
        colors_data = []
        with open(cmap_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or line.startswith('INTERPOLATION'):
                    continue
                
                parts = line.split(',')
                try:
                    value = float(parts[0])
                    r, g, b, a = [int(p) for p in parts[1:5]]
                    colors_data.append((value, (r / 255.0, g / 255.0, b / 255.0, a / 255.0)))
                except (ValueError, IndexError):
                    print(f"警告: 无法解析颜色行: {line}")
                    continue
        
        if not colors_data:
            raise ValueError("色彩映射文件中没有有效的颜色数据。")
        
        # 按值排序
        colors_data.sort(key=lambda x: x[0])
        
        vmin = colors_data[0][0]
        vmax = colors_data[-1][0]
        
        # 归一化颜色点位置
        if vmax == vmin:
            norm_points = [0.0] * len(colors_data)
        else:
            norm_points = [(item[0] - vmin) / (vmax - vmin) for item in colors_data]
        
        colors = [item[1] for item in colors_data]
        
        # 构建颜色列表
        cmap_list = []
        last_pos = -1.0
        for i, pos in enumerate(norm_points):
            pos = max(pos, last_pos + 1e-6)
            cmap_list.append((min(pos, 1.0), colors[i]))
            last_pos = pos
        
        custom_cmap = mcolors.LinearSegmentedColormap.from_list('qgis_custom', cmap_list)
        
        # 更新缓存
        ImageService._colormap_cache = (custom_cmap, vmin, vmax)
        ImageService._colormap_filename = file_name
        
        return custom_cmap, vmin, vmax
    
    @staticmethod
    def clear_colormap_cache():
        """清除颜色表缓存（用于测试或强制刷新）"""
        ImageService._colormap_cache = None
        ImageService._colormap_filename = None
    
    @staticmethod
    def extract_footprint(file_path: str) -> Dict[str, Any]:
        """
        提取 D-InSAR 结果的 footprint（使用 GDAL 降采样策略）。
        
        极速优化版 V2：借鉴 QGIS/GDAL 降采样策略，仅读取缩略图掩码计算 Footprint。
        
        Args:
            file_path: 影像文件路径
            
        Returns:
            {
                "min_lon": float,
                "min_lat": float,
                "max_lon": float,
                "max_lat": float,
                "coverage_polygon": dict  # GeoJSON 格式
            }
        """
        start_time = time.time()
        
        with rasterio.open(file_path) as dataset:
            # 1. 智能降采样读取掩码
            MAX_SIZE = settings.DINSAR_FOOTPRINT_MAX_SIZE
            if dataset.width > MAX_SIZE or dataset.height > MAX_SIZE:
                scale = max(dataset.width, dataset.height) / MAX_SIZE
                new_width = max(1, int(dataset.width / scale))
                new_height = max(1, int(dataset.height / scale))
                mask = dataset.dataset_mask(out_shape=(new_height, new_width))
                rescale_transform = dataset.transform * Affine.scale(
                    dataset.width / new_width, 
                    dataset.height / new_height
                )
            else:
                mask = dataset.dataset_mask()
                rescale_transform = dataset.transform
            
            # 2. 提取有效区域多边形
            mask_shapes = list(shapes(mask, mask=(mask == 255), transform=rescale_transform))
            
            if not mask_shapes:
                # 回退到全图范围
                footprint_poly = Polygon([
                    dataset.transform * (0, 0),
                    dataset.transform * (dataset.width, 0),
                    dataset.transform * (dataset.width, dataset.height),
                    dataset.transform * (0, dataset.height),
                    dataset.transform * (0, 0)
                ])
            else:
                # 合并形状并取凸包
                polys = [shape(s) for s, v in mask_shapes]
                footprint_poly = unary_union(polys).convex_hull
                
                if footprint_poly.geom_type == 'Polygon':
                    tolerance = abs(rescale_transform[0]) * 1.5
                    footprint_poly = footprint_poly.simplify(
                        tolerance, 
                        preserve_topology=True
                    )
            
            elapsed = (time.time() - start_time) * 1000
            print(f"  [性能] Footprint 提取耗时: {elapsed:.1f}ms (文件: {os.path.basename(file_path)})")
            
            # 3. 坐标系转换到 WGS84
            if dataset.crs and dataset.crs.to_epsg() != 4326:
                transformer = Transformer.from_crs(
                    dataset.crs, 
                    "EPSG:4326", 
                    always_xy=True
                )
                footprint_poly = shapely_transform(
                    transformer.transform, 
                    footprint_poly
                )
            
            # 4. 返回紧凑边界框
            left, bottom, right, top = footprint_poly.bounds
            
            return {
                "min_lon": left,
                "min_lat": bottom,
                "max_lon": right,
                "max_lat": top,
                "coverage_polygon": mapping(footprint_poly)
            }
    
    @staticmethod
    def save_image_as_webp(
        image: Image.Image,
        output_path: str,
        quality: int = 80
    ) -> None:
        """
        保存图像为 WebP 格式。
        
        Args:
            image: PIL.Image 对象
            output_path: 输出文件路径
            quality: WebP 质量 (1-100)
        """
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        image.save(output_path, format='WEBP', quality=quality)

    @staticmethod
    def make_edge_dark_transparent(
        image: Image.Image,
        *,
        threshold: int = 6,
    ) -> Image.Image:
        """Make edge-connected near-black preview background transparent."""
        rgba = image.convert("RGBA")
        arr = np.array(rgba, dtype=np.uint8, copy=True)
        if arr.ndim != 3 or arr.shape[2] < 4:
            return rgba

        alpha = arr[:, :, 3]
        dark = (alpha > 0) & (arr[:, :, :3].max(axis=2) <= int(threshold))
        if not dark.any():
            return rgba

        h, w = dark.shape
        edge = np.zeros_like(dark, dtype=bool)
        edge[0, :] = dark[0, :]
        edge[h - 1, :] = dark[h - 1, :]
        edge[:, 0] |= dark[:, 0]
        edge[:, w - 1] |= dark[:, w - 1]
        if not edge.any():
            return rgba

        visited = np.zeros_like(dark, dtype=bool)
        ys, xs = np.where(edge)
        queue = deque(zip(ys.tolist(), xs.tolist()))
        visited[ys, xs] = True

        while queue:
            y, x = queue.popleft()
            if y > 0 and dark[y - 1, x] and not visited[y - 1, x]:
                visited[y - 1, x] = True
                queue.append((y - 1, x))
            if y + 1 < h and dark[y + 1, x] and not visited[y + 1, x]:
                visited[y + 1, x] = True
                queue.append((y + 1, x))
            if x > 0 and dark[y, x - 1] and not visited[y, x - 1]:
                visited[y, x - 1] = True
                queue.append((y, x - 1))
            if x + 1 < w and dark[y, x + 1] and not visited[y, x + 1]:
                visited[y, x + 1] = True
                queue.append((y, x + 1))

        arr[:, :, 3][visited] = 0
        return Image.fromarray(arr, "RGBA")
    
    @staticmethod
    def create_cached_image(
        file_path: str,
        cache_path: str,
        thumbnail_size: Optional[Tuple[int, int]] = None
    ) -> bool:
        """
        创建并保存缓存图像。
        
        Args:
            file_path: 原始文件路径
            cache_path: 缓存文件路径
            thumbnail_size: 缩略图尺寸
            
        Returns:
            是否成功
        """
        try:
            # 生成图像
            full_res_img = ImageService.create_dinsar_image(
                file_path,
                max_size=thumbnail_size or ImageService.DEFAULT_THUMBNAIL_SIZE
            )
            
            # 自动裁剪透明边缘
            bbox = full_res_img.getbbox()
            if bbox:
                full_res_img = full_res_img.crop(bbox)
            
            # 生成缩略图
            thumbnail = ImageService.generate_thumbnail(
                full_res_img, 
                thumbnail_size or ImageService.DEFAULT_THUMBNAIL_SIZE
            )
            
            # 保存为 WebP
            ImageService.save_image_as_webp(thumbnail, cache_path, quality=80)
            
            return True
            
        except Exception as e:
            print(f"创建缓存图像失败: {file_path}, 错误: {e}")
            return False

    @staticmethod
    def _extract_quadrilateral_points(coverage_polygon: Any) -> Optional[np.ndarray]:
        points: List[Tuple[float, float]] = []
        if isinstance(coverage_polygon, dict):
            coordinates = coverage_polygon.get("coordinates") if coverage_polygon else None
            if isinstance(coordinates, list) and coordinates:
                first_ring = coordinates[0]
                if isinstance(first_ring, list):
                    for point in first_ring:
                        if isinstance(point, (list, tuple)) and len(point) >= 2:
                            points.append((float(point[0]), float(point[1])))
        elif isinstance(coverage_polygon, list):
            for point in coverage_polygon:
                if isinstance(point, (list, tuple)) and len(point) >= 2:
                    points.append((float(point[0]), float(point[1])))

        if len(points) < 4:
            return None

        dedup: List[Tuple[float, float]] = []
        for lon, lat in points:
            if not dedup:
                dedup.append((lon, lat))
                continue
            prev_lon, prev_lat = dedup[-1]
            if abs(prev_lon - lon) < 1e-10 and abs(prev_lat - lat) < 1e-10:
                continue
            dedup.append((lon, lat))

        if len(dedup) >= 5:
            first_lon, first_lat = dedup[0]
            last_lon, last_lat = dedup[-1]
            if abs(first_lon - last_lon) < 1e-10 and abs(first_lat - last_lat) < 1e-10:
                dedup = dedup[:-1]

        if len(dedup) != 4:
            return None

        return np.asarray(dedup, dtype=np.float64)

    @staticmethod
    def _extract_source_corner_mapping_points(source_corner_mapping: Any) -> Optional[np.ndarray]:
        if not isinstance(source_corner_mapping, dict):
            return None

        ordered_keys = ["bottom_left", "bottom_right", "top_right", "top_left"]
        points: List[Tuple[float, float]] = []
        for key in ordered_keys:
            value = source_corner_mapping.get(key)
            if not isinstance(value, (list, tuple)) or len(value) < 2:
                return None
            try:
                points.append((float(value[0]), float(value[1])))
            except (TypeError, ValueError):
                return None
        return np.asarray(points, dtype=np.float64)

    @staticmethod
    def _compute_homography(src_points: np.ndarray, dst_points: np.ndarray) -> Optional[np.ndarray]:
        if src_points.shape != (4, 2) or dst_points.shape != (4, 2):
            return None

        matrix_a: List[List[float]] = []
        matrix_b: List[float] = []
        for (sx, sy), (dx, dy) in zip(src_points, dst_points):
            matrix_a.append([sx, sy, 1.0, 0.0, 0.0, 0.0, -dx * sx, -dx * sy])
            matrix_b.append(float(dx))
            matrix_a.append([0.0, 0.0, 0.0, sx, sy, 1.0, -dy * sx, -dy * sy])
            matrix_b.append(float(dy))

        try:
            solved, _, rank, _ = np.linalg.lstsq(
                np.asarray(matrix_a, dtype=np.float64),
                np.asarray(matrix_b, dtype=np.float64),
                rcond=None,
            )
        except np.linalg.LinAlgError:
            return None

        if rank < 8:
            return None

        return np.array(
            [
                [solved[0], solved[1], solved[2]],
                [solved[3], solved[4], solved[5]],
                [solved[6], solved[7], 1.0],
            ],
            dtype=np.float64,
        )

    @staticmethod
    def _estimate_geo_canvas_size(
        bbox: Tuple[float, float, float, float],
        max_size: Tuple[int, int],
    ) -> Optional[Tuple[int, int]]:
        min_lon, min_lat, max_lon, max_lat = bbox
        lon_span = max_lon - min_lon
        lat_span = max_lat - min_lat
        if lon_span <= 0 or lat_span <= 0:
            return None

        mean_lat_rad = np.deg2rad((min_lat + max_lat) * 0.5)
        x_span_scaled = max(lon_span * max(np.cos(mean_lat_rad), 1e-3), 1e-9)
        y_span_scaled = max(lat_span, 1e-9)
        ratio = x_span_scaled / y_span_scaled

        limit = max(64, int(max(max_size[0], max_size[1])))
        if ratio >= 1:
            width = limit
            height = max(64, int(round(width / ratio)))
        else:
            height = limit
            width = max(64, int(round(height * ratio)))
        return width, height

    @staticmethod
    def _warp_preview_to_geo_bbox(
        source_rgba: np.ndarray,
        inverse_h: np.ndarray,
        bbox: Tuple[float, float, float, float],
        out_size: Tuple[int, int],
    ) -> np.ndarray:
        src_h, src_w = source_rgba.shape[:2]
        out_w, out_h = out_size
        min_lon, min_lat, max_lon, max_lat = bbox
        lon_span = max_lon - min_lon
        lat_span = max_lat - min_lat

        if src_h < 1 or src_w < 1 or out_h < 1 or out_w < 1:
            return np.zeros((max(out_h, 1), max(out_w, 1), 4), dtype=np.uint8)

        grid_x, grid_y = np.meshgrid(
            np.arange(out_w, dtype=np.float64),
            np.arange(out_h, dtype=np.float64),
        )
        lon = min_lon + ((grid_x + 0.5) / out_w) * lon_span
        lat = max_lat - ((grid_y + 0.5) / out_h) * lat_span

        lon_flat = lon.reshape(-1)
        lat_flat = lat.reshape(-1)
        denom = inverse_h[2, 0] * lon_flat + inverse_h[2, 1] * lat_flat + inverse_h[2, 2]
        valid = np.abs(denom) > 1e-8

        u = np.zeros_like(lon_flat)
        v = np.zeros_like(lat_flat)
        u[valid] = (
            inverse_h[0, 0] * lon_flat[valid]
            + inverse_h[0, 1] * lat_flat[valid]
            + inverse_h[0, 2]
        ) / denom[valid]
        v[valid] = (
            inverse_h[1, 0] * lon_flat[valid]
            + inverse_h[1, 1] * lat_flat[valid]
            + inverse_h[1, 2]
        ) / denom[valid]

        if src_w >= 2 and src_h >= 2:
            valid &= (u >= 0) & (u < (src_w - 1)) & (v >= 0) & (v < (src_h - 1))
        else:
            valid &= (u >= 0) & (u <= (src_w - 1)) & (v >= 0) & (v <= (src_h - 1))

        output = np.zeros((out_h, out_w, 4), dtype=np.uint8)
        if not np.any(valid):
            return output

        valid_idx = np.where(valid)[0]
        u_valid = u[valid_idx]
        v_valid = v[valid_idx]

        if src_w >= 2 and src_h >= 2:
            x0 = np.floor(u_valid).astype(np.int32)
            y0 = np.floor(v_valid).astype(np.int32)
            x1 = np.clip(x0 + 1, 0, src_w - 1)
            y1 = np.clip(y0 + 1, 0, src_h - 1)
            du = (u_valid - x0).astype(np.float32)
            dv = (v_valid - y0).astype(np.float32)

            src_float = source_rgba.astype(np.float32, copy=False)
            s00 = src_float[y0, x0]
            s10 = src_float[y0, x1]
            s01 = src_float[y1, x0]
            s11 = src_float[y1, x1]
            samples = (
                s00 * (1 - du)[:, None] * (1 - dv)[:, None]
                + s10 * du[:, None] * (1 - dv)[:, None]
                + s01 * (1 - du)[:, None] * dv[:, None]
                + s11 * du[:, None] * dv[:, None]
            )
            rgba = np.clip(samples, 0, 255).astype(np.uint8)
        else:
            nearest_x = np.clip(np.round(u_valid).astype(np.int32), 0, src_w - 1)
            nearest_y = np.clip(np.round(v_valid).astype(np.int32), 0, src_h - 1)
            rgba = source_rgba[nearest_y, nearest_x]

        flat = output.reshape(-1, 4)
        flat[valid_idx] = rgba
        return output

    @staticmethod
    def create_geocorrected_radar_cached_image(
        source_image_path: str,
        cache_path: str,
        coverage_polygon: Any,
        bbox: Tuple[float, float, float, float],
        source_corner_mapping: Optional[Dict[str, Any]] = None,
        thumbnail_size: Optional[Tuple[int, int]] = None,
        quality: Optional[int] = None,
    ) -> Tuple[bool, Optional[str]]:
        try:
            if not os.path.exists(source_image_path):
                return False, "preview_source_not_found"

            polygon_points = ImageService._extract_source_corner_mapping_points(source_corner_mapping)
            if polygon_points is None:
                polygon_points = ImageService._extract_quadrilateral_points(coverage_polygon)
            if polygon_points is None:
                return False, "invalid_coverage_polygon"

            max_size = thumbnail_size or (
                settings.RADAR_THUMBNAIL_MAX_SIZE,
                settings.RADAR_THUMBNAIL_MAX_SIZE,
            )
            out_size = ImageService._estimate_geo_canvas_size(bbox, max_size)
            if out_size is None:
                return False, "invalid_bbox"

            with Image.open(source_image_path) as image:
                source = ImageService.make_edge_dark_transparent(image)
                source_rgba = np.asarray(source, dtype=np.uint8)

            src_h, src_w = source_rgba.shape[:2]
            if src_h < 1 or src_w < 1:
                return False, "invalid_source_image_size"

            source_points = np.asarray(
                [
                    [0.0, float(src_h - 1)],
                    [float(src_w - 1), float(src_h - 1)],
                    [float(src_w - 1), 0.0],
                    [0.0, 0.0],
                ],
                dtype=np.float64,
            )
            homography = ImageService._compute_homography(source_points, polygon_points)
            if homography is None:
                return False, "homography_solve_failed"

            try:
                inverse_h = np.linalg.inv(homography)
            except np.linalg.LinAlgError:
                return False, "homography_invert_failed"

            warped_rgba = ImageService._warp_preview_to_geo_bbox(
                source_rgba=source_rgba,
                inverse_h=inverse_h,
                bbox=bbox,
                out_size=out_size,
            )
            target_quality = quality if quality is not None else settings.RADAR_GEO_CACHE_QUALITY
            output_image = Image.fromarray(warped_rgba, mode="RGBA")
            ImageService.save_image_as_webp(output_image, cache_path, quality=target_quality)
            return True, None
        except Exception as e:
            return False, str(e)

    @staticmethod
    def create_radar_cached_image(
        source_image_path: str,
        cache_path: str,
        thumbnail_size: Optional[Tuple[int, int]] = None
    ) -> bool:
        """
        为源雷达数据包中的预览图（jpg/png 等）创建 WebP 缓存。

        Args:
            source_image_path: 原始预览图路径
            cache_path: 缓存输出路径
            thumbnail_size: 缩略图尺寸

        Returns:
            是否成功
        """
        try:
            if not os.path.exists(source_image_path):
                return False

            max_size = thumbnail_size or (
                settings.RADAR_THUMBNAIL_MAX_SIZE,
                settings.RADAR_THUMBNAIL_MAX_SIZE,
            )

            with Image.open(source_image_path) as image:
                image = ImageService.make_edge_dark_transparent(image)
                image.thumbnail(max_size, Image.Resampling.LANCZOS)
                ImageService.save_image_as_webp(image, cache_path, quality=82)
            return True
        except Exception as e:
            print(f"创建源影像缓存失败: {source_image_path}, 错误: {e}")
            return False


# 全局服务实例
image_service = ImageService()
