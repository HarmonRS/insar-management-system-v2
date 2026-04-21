#!/usr/bin/env python3
"""
修复版MuRP算法 - 包含Linux系统中文显示支持
"""

import xarray as xr
import rioxarray
import os
import numpy as np
import matplotlib.pyplot as plt
from glob import glob
import argparse
import sys
from datetime import datetime
import rasterio
import warnings
import matplotlib.font_manager as fm
import platform

# 设置中文字体支持
def setup_chinese_font():
    """配置中文字体支持"""
    system = platform.system()
    
    if system == "Linux":
        # Linux系统字体配置
        chinese_fonts = [
            'WenQuanYi Micro Hei',  # 文泉驿微米黑
            'Noto Sans CJK SC',     # Google思源黑体
            'DejaVu Sans',          # 备选字体
            'Arial'                 # 最后备选
        ]
        
        # 查找可用的字体
        available_fonts = []
        for font in chinese_fonts:
            if any(f.name == font for f in fm.fontManager.ttflist):
                available_fonts.append(font)
        
        if available_fonts:
            plt.rcParams['font.sans-serif'] = available_fonts
            print(f"已设置中文字体: {available_fonts[0]}")
        else:
            plt.rcParams['font.sans-serif'] = ['DejaVu Sans']
            print("警告: 未找到中文字体，使用默认字体")
    else:
        # Windows或macOS
        plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial']
    
    # 解决负号显示问题
    plt.rcParams['axes.unicode_minus'] = False

# 调用字体设置函数
setup_chinese_font()
warnings.filterwarnings('ignore')

class FixedMuRP:
    """
    修复版MuRP算法 - 解决相干性数据形状不一致问题
    """
    
    def __init__(self, random_seed=42):
        """初始化MuRP处理器"""
        self.random_seed = random_seed
        np.random.seed(random_seed)
        self.ds = None
        self.ds_corrected = None
        self.refs = []
        self.fits = []
        self.crs = None
        self.transform = None
        self.bounds = None
        self.width = None
        self.height = None
        
    def load_hyp3_data_robust(self, data_path):
        """
        健壮的数据加载函数，处理形状不一致问题
        """
        print(f"正在加载Hyp3数据从: {data_path}")
        
        # 查找数据文件
        unw_files = glob(f'{data_path}/*/*unw_phase*.tif')
        corr_files = glob(f'{data_path}/*/*corr*.tif')
        dem_files = glob(f'{data_path}/*/*dem*.tif')
        
        if not unw_files:
            raise FileNotFoundError(f"在 {data_path} 中未找到解缠相位文件")
        if not dem_files:
            raise FileNotFoundError(f"在 {data_path} 中未找到DEM文件")
        
        print(f"找到解缠相位文件: {len(unw_files)} 个")
        print(f"找到相干性文件: {len(corr_files)} 个")
        print(f"找到DEM文件: {len(dem_files)} 个")
        
        # 使用第一个解缠相位文件作为参考
        reference_file = unw_files[0]
        with rasterio.open(reference_file) as src:
            self.crs = src.crs
            self.transform = src.transform
            self.width = src.width
            self.height = src.height
            self.bounds = src.bounds
            
        print(f"坐标参考系统: {self.crs}")
        print(f"图像尺寸: {self.width} x {self.height}")
        
        # 读取DEM数据 - 使用第一个DEM文件
        print("正在加载DEM数据...")
        with rasterio.open(dem_files[0]) as src:
            # 确保DEM尺寸与参考文件一致
            if src.width == self.width and src.height == self.height:
                elevation_data = src.read(1)
            else:
                print("警告: DEM尺寸不匹配，将使用零数组")
                elevation_data = np.zeros((self.height, self.width))
        
        # 读取解缠相位数据
        print("正在加载解缠相位数据...")
        unw_phase_data = []
        granule_names = []
        
        for unw_file in unw_files:
            with rasterio.open(unw_file) as src:
                # 检查尺寸是否匹配
                if src.width == self.width and src.height == self.height:
                    data = src.read(1)
                else:
                    print(f"警告: {unw_file} 尺寸不匹配，跳过")
                    continue
                    
                unw_phase_data.append(data)
                granule_name = os.path.basename(unw_file).replace('.tif', '')
                granule_names.append(granule_name)
                print(f"  已加载: {granule_name}")
        
        if not unw_phase_data:
            raise ValueError("没有成功加载任何解缠相位数据")
        
        # 转换为numpy数组
        unw_phase_array = np.array(unw_phase_data)
        print(f"解缠相位数据形状: {unw_phase_array.shape}")
        
        # 处理相干性数据 - 确保与解缠相位数据对应
        print("正在处理相干性数据...")
        coherence_data = []
        used_corr_files = []
        
        # 为每个解缠相位文件寻找匹配的相干性文件
        for i, granule_name in enumerate(granule_names):
            # 从granule名称提取基本名称
            base_name = granule_name.replace('_unw_phase', '')
            
            # 查找匹配的相干性文件
            matching_corr_files = [f for f in corr_files if base_name in f and f not in used_corr_files]
            
            if matching_corr_files:
                # 使用第一个匹配的文件
                corr_file = matching_corr_files[0]
                try:
                    with rasterio.open(corr_file) as src:
                        # 检查尺寸是否匹配
                        if src.width == self.width and src.height == self.height:
                            corr_data = src.read(1)
                            coherence_data.append(corr_data)
                            used_corr_files.append(corr_file)
                            print(f"  已加载相干性: {os.path.basename(corr_file)}")
                        else:
                            print(f"  警告: 相干性文件尺寸不匹配，使用默认值")
                            coherence_data.append(np.ones((self.height, self.width)))
                except Exception as e:
                    print(f"  加载相干性文件失败: {e}")
                    coherence_data.append(np.ones((self.height, self.width)))
            else:
                # 没有找到匹配的相干性文件，使用默认值
                print(f"  未找到匹配的相干性文件，使用默认值")
                coherence_data.append(np.ones((self.height, self.width)))
        
        # 如果相干性数据数量与解缠相位不匹配，补充默认值
        while len(coherence_data) < len(unw_phase_data):
            coherence_data.append(np.ones((self.height, self.width)))
        
        # 转换为numpy数组
        coherence_array = np.array(coherence_data)
        print(f"相干性数据形状: {coherence_array.shape}")
        
        # 创建xarray数据集
        ds = xr.Dataset(
            {
                'unw_phase': (['granule', 'y', 'x'], unw_phase_array),
                'coherence': (['granule', 'y', 'x'], coherence_array),
                'elevation': (['y', 'x'], elevation_data)
            },
            coords={
                'granule': granule_names,
                'y': np.arange(self.height),
                'x': np.arange(self.width)
            }
        )
        
        # 设置地理参考属性
        try:
            ds.rio.set_crs(self.crs)
            ds.rio.write_transform(self.transform, inplace=True)
        except:
            print("警告: 无法设置地理参考信息")
        
        print(f"成功加载数据: {len(granule_names)} 个干涉图")
        self.ds = ds
        return ds
    
    def select_reference_points(self, corr_thresh=0.6, n_refs=1000):
        """
        选择参考点
        """
        if self.ds is None:
            raise ValueError("请先加载数据")
            
        print(f"步骤 1: 选择参考点 (相干性阈值: {corr_thresh}, 数量: {n_refs})")
        
        # 计算平均相干性
        coh_mean = self.ds.coherence.mean(dim='granule').values
        
        # 获取高相干性像素
        valid_mask = coh_mean >= corr_thresh
        valid_coords = np.argwhere(valid_mask)
        
        if len(valid_coords) == 0:
            print(f"警告: 没有找到相干性大于{corr_thresh}的像素，降低阈值到0.3")
            valid_mask = coh_mean >= 0.3
            valid_coords = np.argwhere(valid_mask)
            
            if len(valid_coords) == 0:
                print("警告: 仍然没有找到高相干性像素，使用所有像素")
                valid_coords = np.array([[i, j] for i in range(self.height) for j in range(self.width)])
        
        print(f"  找到 {len(valid_coords)} 个候选像素点")
        
        # 调整参考点数量
        if len(valid_coords) < n_refs:
            n_refs = len(valid_coords)
            print(f"  可用点不足，调整为选择 {n_refs} 个参考点")
        
        # 使用网格策略选择参考点
        grid_size = int(np.sqrt(n_refs / 4))
        grid_size = max(5, min(grid_size, 50))
        
        x_bins = np.linspace(0, self.width, grid_size + 1, dtype=int)
        y_bins = np.linspace(0, self.height, grid_size + 1, dtype=int)
        
        ref_list = []
        points_per_cell = max(1, n_refs // (grid_size * grid_size))
        
        for i in range(grid_size):
            for j in range(grid_size):
                x_min, x_max = x_bins[i], x_bins[i+1]
                y_min, y_max = y_bins[j], y_bins[j+1]
                
                # 在当前网格内选择点
                cell_mask = ((valid_coords[:, 1] >= x_min) & (valid_coords[:, 1] < x_max) &
                           (valid_coords[:, 0] >= y_min) & (valid_coords[:, 0] < y_max))
                cell_points = valid_coords[cell_mask]
                
                if len(cell_points) > 0:
                    n_select = min(points_per_cell, len(cell_points))
                    selected_indices = np.random.choice(len(cell_points), n_select, replace=False)
                    
                    for idx in selected_indices:
                        y, x = cell_points[idx]
                        ref_list.append([int(x), int(y)])
        
        # 如果点数不足，随机补充
        if len(ref_list) < n_refs:
            remaining = n_refs - len(ref_list)
            additional_indices = np.random.choice(len(valid_coords), remaining, replace=False)
            for idx in additional_indices:
                y, x = valid_coords[idx]
                if [int(x), int(y)] not in ref_list:
                    ref_list.append([int(x), int(y)])
        
        self.refs = ref_list[:n_refs]
        print(f"  成功选择 {len(self.refs)} 个参考点")
        return self.refs
    
    def sample_reference_data(self):
        """采样参考点数据"""
        if not self.refs:
            raise ValueError("请先选择参考点")
            
        print("步骤 2: 采样参考点数据")
        
        # 验证参考点坐标
        valid_refs = []
        for ref in self.refs:
            x, y = ref[0], ref[1]
            if 0 <= x < self.width and 0 <= y < self.height:
                valid_refs.append(ref)
            else:
                print(f"警告: 参考点 ({x}, {y}) 超出图像范围")
        
        if not valid_refs:
            raise ValueError("没有有效的参考点")
        
        # 提取坐标
        x_coords = [ref[0] for ref in valid_refs]
        y_coords = [ref[1] for ref in valid_refs]
        
        # 采样高程数据
        ref_elevation = []
        for x, y in valid_refs:
            elev_val = self.ds.elevation.isel(x=x, y=y).values
            ref_elevation.append(float(elev_val))
        ref_elevation = np.array(ref_elevation)
        
        # 采样相位数据
        ref_values = []
        for i in range(len(self.ds.granule)):
            granule_phases = []
            for x, y in valid_refs:
                phase_val = self.ds.unw_phase.isel(granule=i, x=x, y=y).values
                granule_phases.append(float(phase_val))
            ref_values.append(granule_phases)
        ref_values = np.array(ref_values)
        
        print(f"  成功采样 {len(valid_refs)} 个参考点，{len(ref_values)} 个干涉图")
        return ref_values, ref_elevation
    
    def numpy_linear_regression(self, x, y):
        """
        使用NumPy实现线性回归
        """
        # 移除NaN值
        valid_mask = ~(np.isnan(x) | np.isnan(y))
        x_valid = x[valid_mask]
        y_valid = y[valid_mask]
        
        if len(x_valid) < 2:
            return np.nan, np.nan, 0, 0
        
        # 计算线性回归参数
        x_mean = np.mean(x_valid)
        y_mean = np.mean(y_valid)
        
        # 计算协方差和方差
        cov_xy = np.mean((x_valid - x_mean) * (y_valid - y_mean))
        var_x = np.mean((x_valid - x_mean) ** 2)
        
        if var_x == 0:
            return np.nan, np.nan, 0, 0
        
        beta = cov_xy / var_x
        alpha = y_mean - beta * x_mean
        
        # 计算R²
        y_pred = alpha + beta * x_valid
        ss_res = np.sum((y_valid - y_pred) ** 2)
        ss_tot = np.sum((y_valid - y_mean) ** 2)
        
        if ss_tot == 0:
            r_squared = 0
        else:
            r_squared = 1 - (ss_res / ss_tot)
        
        n_points = len(x_valid)
        
        return beta, alpha, r_squared, n_points
    
    def perform_linear_fits(self, ref_values, ref_elevation):
        """
        执行线性拟合
        """
        print("步骤 3: 线性拟合")
        
        fits = []
        fit_metrics = []
        elevations = np.array(ref_elevation)
        
        for i in range(len(ref_values)):
            phases = ref_values[i]
            
            # 执行线性回归
            slope, intercept, r2, n_points = self.numpy_linear_regression(elevations, phases)
            
            fits.append([slope, intercept])
            fit_metrics.append({
                'r_squared': r2,
                'n_points': n_points
            })
            
            if (i + 1) % 10 == 0 or (i + 1) == len(ref_values):
                print(f"    已完成 {i+1}/{len(ref_values)} 个干涉图")
        
        # 统计结果
        valid_fits = sum(1 for fit in fits if not np.isnan(fit[0]))
        valid_r2 = [m['r_squared'] for m in fit_metrics if not np.isnan(m['r_squared'])]
        avg_r2 = np.mean(valid_r2) if valid_r2 else 0
        
        self.fits = fits
        self.fit_metrics = fit_metrics
        
        print(f"  线性拟合完成: {valid_fits}/{len(fits)} 个成功, 平均R²: {avg_r2:.3f}")
        return fits, fit_metrics
    
    def apply_correction(self, min_r2=0.0):
        """应用相位校正"""
        print("步骤 4: 应用相位校正")
        
        if not self.fits:
            raise ValueError("请先进行线性拟合")
        
        # 过滤低质量拟合
        valid_fits = []
        valid_indices = []
        
        for i, fit in enumerate(self.fits):
            if np.isnan(fit[0]) or np.isnan(fit[1]):
                continue
                
            if self.fit_metrics[i]['r_squared'] < min_r2:
                continue
                
            valid_fits.append(fit)
            valid_indices.append(i)
        
        if not valid_fits:
            print("警告: 没有通过质量控制的拟合，使用所有有效拟合")
            valid_fits = [f for f in self.fits if not np.isnan(f[0]) and not np.isnan(f[1])]
            valid_indices = [i for i, f in enumerate(self.fits) 
                           if not np.isnan(f[0]) and not np.isnan(f[1])]
        
        if not valid_fits:
            raise ValueError("没有有效的拟合可用于校正")
        
        print(f"  使用 {len(valid_fits)}/{len(self.fits)} 个拟合进行校正")
        
        # 创建校正后的数据集
        self.ds_corrected = self.ds.copy()
        
        # 创建校正后的相位数组
        corrected_phase = np.zeros_like(self.ds.unw_phase.values)
        
        # 对每个干涉图应用校正
        for idx, granule_idx in enumerate(valid_indices):
            slope, intercept = valid_fits[idx]
            
            # 计算校正量: phase = slope * elevation + intercept
            elevation_data = self.ds.elevation.values
            correction = elevation_data * slope + intercept
            
            # 应用校正: corrected_phase = original_phase - correction
            original_phase = self.ds.unw_phase[granule_idx].values
            corrected_phase[granule_idx] = original_phase - correction
        
        # 添加校正后的变量
        self.ds_corrected['unw_phase_corrected'] = (('granule', 'y', 'x'), corrected_phase)
        
        return self.ds_corrected
    
    def save_results_as_geotiff(self, output_dir='.'):
        """保存结果为GeoTIFF格式"""
        if self.ds_corrected is None:
            raise ValueError("请先进行校正")
            
        print("步骤 5: 保存GeoTIFF格式结果")
        os.makedirs(output_dir, exist_ok=True)
        
        # 保存校正后的每个干涉图
        corrected_dir = os.path.join(output_dir, "corrected_phase")
        os.makedirs(corrected_dir, exist_ok=True)
        
        for i, granule in enumerate(self.ds_corrected.granule.values):
            # 获取校正后的相位数据
            phase_corrected = self.ds_corrected.unw_phase_corrected[i].values
            
            # 创建输出文件名
            output_file = os.path.join(corrected_dir, f"{granule}_corrected.tif")
            
            # 使用rasterio保存为GeoTIFF
            try:
                with rasterio.open(
                    output_file, 'w',
                    driver='GTiff',
                    height=self.height,
                    width=self.width,
                    count=1,
                    dtype=phase_corrected.dtype,
                    crs=self.crs,
                    transform=self.transform
                ) as dst:
                    dst.write(phase_corrected, 1)
                
                print(f"  已保存: {output_file}")
            except Exception as e:
                print(f"  保存失败 {output_file}: {e}")
        
        # 保存平均校正后相位
        try:
            mean_corrected = self.ds_corrected.unw_phase_corrected.mean(dim='granule').values
            mean_file = os.path.join(output_dir, "mean_corrected_phase.tif")
            
            with rasterio.open(
                mean_file, 'w',
                driver='GTiff',
                height=self.height,
                width=self.width,
                count=1,
                dtype=mean_corrected.dtype,
                crs=self.crs,
                transform=self.transform
            ) as dst:
                dst.write(mean_corrected, 1)
            
            print(f"  已保存平均校正相位: {mean_file}")
        except Exception as e:
            print(f"  保存平均校正相位失败: {e}")
        
        # 保存原始平均相位用于对比
        try:
            mean_original = self.ds.unw_phase.mean(dim='granule').values
            mean_orig_file = os.path.join(output_dir, "mean_original_phase.tif")
            
            with rasterio.open(
                mean_orig_file, 'w',
                driver='GTiff',
                height=self.height,
                width=self.width,
                count=1,
                dtype=mean_original.dtype,
                crs=self.crs,
                transform=self.transform
            ) as dst:
                dst.write(mean_original, 1)
            
            print(f"  已保存原始平均相位: {mean_orig_file}")
        except Exception as e:
            print(f"  保存原始平均相位失败: {e}")
        
        # 保存高程数据
        try:
            elev_file = os.path.join(output_dir, "elevation.tif")
            elevation_data = self.ds.elevation.values
            
            with rasterio.open(
                elev_file, 'w',
                driver='GTiff',
                height=self.height,
                width=self.width,
                count=1,
                dtype=elevation_data.dtype,
                crs=self.crs,
                transform=self.transform
            ) as dst:
                dst.write(elevation_data, 1)
            
            print(f"  已保存高程数据: {elev_file}")
        except Exception as e:
            print(f"  保存高程数据失败: {e}")
        
        print(f"所有GeoTIFF文件已保存至: {output_dir}")
    
    def create_diagnostic_plots(self, ref_values, ref_elevation, output_dir='.'):
        """创建诊断图表"""
        print("步骤 6: 生成诊断图表")
        os.makedirs(output_dir, exist_ok=True)
        
        # 创建综合诊断图
        fig, axes = plt.subplots(2, 3, figsize=(15, 10))
        
        # 1. 参考点分布
        ax1 = axes[0, 0]
        elevation_data = self.ds.elevation.values
        im1 = ax1.imshow(elevation_data, cmap='terrain')
        if self.refs:
            x_coords, y_coords = zip(*self.refs)
            ax1.scatter(x_coords, y_coords, c='red', s=5, alpha=0.7, label='Reference Points')
            ax1.legend()
        ax1.set_title('Reference Points Distribution')
        plt.colorbar(im1, ax=ax1, label='Elevation (m)')
        
        # 2. 第一个干涉图的相位-高程关系
        ax2 = axes[0, 1]
        if len(ref_values) > 0:
            phases = ref_values[0]
            valid_mask = ~(np.isnan(ref_elevation) | np.isnan(phases))
            
            if np.sum(valid_mask) > 0:
                ax2.scatter(ref_elevation[valid_mask], phases[valid_mask], 
                           alpha=0.5, s=10)
                
                # 绘制拟合线
                if not np.isnan(self.fits[0][0]):
                    elev_min, elev_max = np.min(ref_elevation[valid_mask]), np.max(ref_elevation[valid_mask])
                    elev_range = np.linspace(elev_min, elev_max, 100)
                    phase_fit = self.fits[0][0] * elev_range + self.fits[0][1]
                    ax2.plot(elev_range, phase_fit, 'r-', linewidth=2, 
                            label=f'Slope: {self.fits[0][0]:.4f}')
                    ax2.legend()
                
                ax2.set_xlabel('Elevation (m)')
                ax2.set_ylabel('Phase (rad)')
                ax2.set_title('Phase vs Elevation')
                ax2.grid(True, alpha=0.3)
        
        # 3. 拟合斜率分布
        ax3 = axes[0, 2]
        slopes = [f[0] for f in self.fits if not np.isnan(f[0])]
        if slopes:
            ax3.hist(slopes, bins=20, alpha=0.7, density=True)
            ax3.axvline(np.mean(slopes), color='r', linestyle='--',
                       label=f'Mean: {np.mean(slopes):.4f}')
            ax3.legend()
            ax3.set_xlabel('Slope')
            ax3.set_ylabel('Density')
            ax3.set_title('Slope Distribution')
            ax3.grid(True, alpha=0.3)
        
        # 4. 校正前后对比
        ax4 = axes[1, 0]
        phase_before = self.ds.unw_phase.mean(dim='granule').values
        vmin, vmax = -np.pi, np.pi
        im4 = ax4.imshow(phase_before, cmap='RdBu', vmin=vmin, vmax=vmax)
        ax4.set_title('Mean Phase (Before Correction)')
        plt.colorbar(im4, ax=ax4, label='Phase (rad)')
        
        ax5 = axes[1, 1]
        phase_after = self.ds_corrected.unw_phase_corrected.mean(dim='granule').values
        im5 = ax5.imshow(phase_after, cmap='RdBu', vmin=vmin, vmax=vmax)
        ax5.set_title('Mean Phase (After Correction)')
        plt.colorbar(im5, ax=ax5, label='Phase (rad)')
        
        # 5. 校正量
        ax6 = axes[1, 2]
        correction = phase_before - phase_after
        im6 = ax6.imshow(correction, cmap='viridis')
        ax6.set_title('Phase Correction')
        plt.colorbar(im6, ax=ax6, label='Correction (rad)')
        
        plt.tight_layout()
        plt.savefig(f'{output_dir}/MuRP_diagnostics.png', dpi=300, bbox_inches='tight')
        plt.close()
        
        print(f"  诊断图已保存至: {output_dir}/MuRP_diagnostics.png")
    
    def calculate_improvement(self):
        """计算改善统计"""
        if self.ds_corrected is None:
            return 0, 0, 0
        
        # 计算时间序列标准差
        std_before = float(self.ds.unw_phase.std(dim='granule').mean().values)
        std_after = float(self.ds_corrected.unw_phase_corrected.std(dim='granule').mean().values)
        
        improvement = (std_before - std_after) / std_before * 100 if std_before != 0 else 0
        
        return improvement, std_before, std_after
    
    def run_murp_correction(self, data_path, corr_thresh=0.6, n_refs=1000, 
                           output_dir='.', create_plots=True, save_geotiff=True):
        """
        运行完整的MuRP校正流程
        """
        start_time = datetime.now()
        print("="*60)
        print("修复版MuRP算法开始执行")
        print(f"开始时间: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print("="*60)
        
        try:
            # 1. 加载数据
            self.load_hyp3_data_robust(data_path)
            
            # 2. 选择参考点
            self.select_reference_points(corr_thresh, n_refs)
            
            # 3. 采样参考点数据
            ref_values, ref_elevation = self.sample_reference_data()
            
            # 4. 线性拟合
            self.perform_linear_fits(ref_values, ref_elevation)
            
            # 5. 应用校正
            self.apply_correction()
            
            # 6. 生成诊断图表
            if create_plots:
                self.create_diagnostic_plots(ref_values, ref_elevation, output_dir)
            
            # 7. 保存GeoTIFF格式结果
            if save_geotiff:
                self.save_results_as_geotiff(output_dir)
            
            # 计算改善统计
            improvement, std_before, std_after = self.calculate_improvement()
            
            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()
            
            print("="*60)
            print("修复版MuRP算法执行完成")
            print(f"结束时间: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"总耗时: {duration:.1f} 秒")
            print(f"时间序列标准差改善: {improvement:.1f}%")
            print(f"  校正前: {std_before:.4f} rad")
            print(f"  校正后: {std_after:.4f} rad")
            print("="*60)
            
            return self.ds_corrected
            
        except Exception as e:
            print(f"算法执行失败: {e}")
            import traceback
            traceback.print_exc()
            return None

def main():
    """命令行接口主函数"""
    parser = argparse.ArgumentParser(
        description='修复版MuRP算法 - 解决相干性数据形状不一致问题',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
使用示例:
  python fixed_murp.py /path/to/hyp3/data 
  python fixed_murp.py /path/to/hyp3/data --corr_thresh 0.6 --n_refs 500
  python fixed_murp.py /path/to/hyp3/data --output_dir ./results
        '''
    )
    
    parser.add_argument('data_path', help='Hyp3数据目录路径')
    parser.add_argument('--corr_thresh', type=float, default=0.6, 
                       help='相干性阈值 (默认: 0.6)')
    parser.add_argument('--n_refs', type=int, default=1000, 
                       help='参考点数量 (默认: 1000)')
    parser.add_argument('--output_dir', default='.', 
                       help='输出目录 (默认: 当前目录)')
    parser.add_argument('--no_plots', action='store_true', 
                       help='不生成诊断图表')
    parser.add_argument('--no_geotiff', action='store_true', 
                       help='不保存GeoTIFF格式')
    
    args = parser.parse_args()
    
    try:
        # 创建MuRP处理器
        murp = FixedMuRP(random_seed=42)
        
        # 运行算法
        ds_corrected = murp.run_murp_correction(
            data_path=args.data_path,
            corr_thresh=args.corr_thresh,
            n_refs=args.n_refs,
            output_dir=args.output_dir,
            create_plots=not args.no_plots,
            save_geotiff=not args.no_geotiff
        )
        
        if ds_corrected is not None:
            print("算法执行成功!")
        else:
            print("算法执行失败!")
            sys.exit(1)
        
    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
