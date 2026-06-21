import os
import time
import asyncio
import base64
import json
import httpx
import numpy as np
from PIL import Image
import joblib
from sklearn.ensemble import RandomForestClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from scipy.stats import entropy
from concurrent.futures import ProcessPoolExecutor
import functools
from typing import List, Optional

from .config import settings

# 模型保存路径 (使用统一配置)
MODEL_PATH = settings.MODEL_PATH

def extract_features(image_path: str) -> np.ndarray:
    """
    从图像中提取用于质量评估的特征向量。
    特征包括：统计特征（均值、方差）、熵、边缘强度等。
    """
    try:
        if not os.path.exists(image_path):
            return np.zeros(8)
            
        with Image.open(image_path) as img:
            # 转换为灰度图
            img_gray = img.convert('L')
            # 调整大小以加快处理速度，同时保持特征
            img_small = img_gray.resize((256, 256))
            arr = np.array(img_small)

            # 1. 基础统计特征
            mean = np.mean(arr)
            std = np.std(arr)
            min_val = np.min(arr)
            max_val = np.max(arr)
            
            # 2. 直方图熵 (反映纹理丰富度)
            # 计算直方图
            hist, _ = np.histogram(arr, bins=256, range=(0, 256), density=True)
            # 计算熵 (使用 base=2)
            img_entropy = entropy(hist, base=2)

            # 3. 简单的边缘/梯度特征 (反映条纹清晰度)
            # 使用简单的差分来近似梯度
            dy, dx = np.gradient(arr)
            gradient_magnitude = np.sqrt(dx**2 + dy**2)
            mean_gradient = np.mean(gradient_magnitude)
            std_gradient = np.std(gradient_magnitude)

            # 4. 对比度 (RMS Contrast)
            contrast = std / (mean + 1e-6) # 避免除以零

            # 组合特征向量
            features = np.array([
                mean, std, min_val, max_val,
                img_entropy,
                mean_gradient, std_gradient,
                contrast
            ], dtype=np.float32)
            
            # 处理可能的 NaN/Inf
            features = np.nan_to_num(features)
            
            return features

    except Exception as e:
        # 在多进程中，print 可能不会直接显示在主进程终端，但这里保留作为记录
        # print(f"Error extracting features from {image_path}: {e}")
        return np.zeros(8)

def train_quality_model(labeled_data: list, progress_callback=None):
    """
    训练质量评估模型。
    
    Args:
        labeled_data: list of tuples (image_path, label)
                      label: 0 (Bad), 1 (Good)
        progress_callback: 可选的回调函数，用于报告进度 (0-100)
    
    Returns:
        dict: {"accuracy": float, "sample_count": int}
    """
    X = []
    y = []
    
    img_paths = [item[0] for item in labeled_data]
    labels = [item[1] for item in labeled_data]
    
    total = len(img_paths)
    if total == 0:
        raise ValueError("No training data provided.")

    # 使用多进程并行提取特征
    # 注意：在 Windows 上，ProcessPoolExecutor 必须在 if __name__ == "__main__": 保护下或由主进程调用
    # FastAPI 的后台任务环境通常可以正常工作
    max_workers = min(os.cpu_count() or 4, 8)
    
    features_list = []
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        # 提交所有任务
        futures = [executor.submit(extract_features, path) for path in img_paths]
        
        for i, future in enumerate(futures):
            feat = future.result()
            if not np.all(feat == 0):
                X.append(feat)
                y.append(labels[i])
            
            if progress_callback and i % 5 == 0:
                # 训练阶段特征提取占 80% 的进度
                progress_callback(int((i / total) * 80))

    valid_count = len(X)
    if valid_count < 2:
        raise ValueError("Not enough valid training data. Need at least 2 samples.")
    
    if len(set(y)) < 2:
        raise ValueError("Training data must contain both 'Good' and 'Bad' examples.")

    X = np.array(X)
    y = np.array(y)

    if progress_callback:
        progress_callback(90)

    # 创建管道：标准化 -> 随机森林
    clf = make_pipeline(StandardScaler(), RandomForestClassifier(n_estimators=100, random_state=42))
    clf.fit(X, y)
    
    # 保存模型
    joblib.dump(clf, MODEL_PATH)
    
    if progress_callback:
        progress_callback(100)
        
    return {
        "accuracy": float(clf.score(X, y)),
        "sample_count": valid_count
    }

def predict_quality(image_paths: list, progress_callback=None) -> dict:
    """
    预测一组图像的质量分数。
    使用多进程并行加速特征提取。
    """
    if not os.path.exists(MODEL_PATH):
        return {}
    
    try:
        clf = joblib.load(MODEL_PATH)
    except Exception:
        return {}

    results = {}
    total = len(image_paths)
    if total == 0:
        return {}

    valid_paths = []
    features_list = []
    
    max_workers = min(os.cpu_count() or 4, 8)
    
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(extract_features, path) for path in image_paths]
        
        for i, future in enumerate(futures):
            feat = future.result()
            path = image_paths[i]
            if not np.all(feat == 0):
                valid_paths.append(path)
                features_list.append(feat)
            else:
                results[path] = 0.0
            
            if progress_callback and i % 10 == 0:
                # 预测阶段特征提取占 95% 的进度
                progress_callback(int((i / total) * 95))

    if not valid_paths:
        if progress_callback: progress_callback(100)
        return results

    X = np.array(features_list)
    # predict_proba 返回 [[prob_0, prob_1], ...]
    probs = clf.predict_proba(X)[:, 1]
    
    for path, prob in zip(valid_paths, probs):
        results[path] = float(prob)
        
    if progress_callback:
        progress_callback(100)
        
    return results

def is_model_trained() -> bool:
    return os.path.exists(MODEL_PATH)

def get_model_info() -> dict:
    """
    获取模型文件的元数据。
    """
    if not os.path.exists(MODEL_PATH):
        return None
    
    try:
        stats = os.stat(MODEL_PATH)
        return {
            "last_modified": time.ctime(stats.st_mtime),
            "size_bytes": stats.st_size
        }
    except Exception:
        return None

# --- Ollama VLM Integration ---

OLLAMA_BASE_URL = settings.OLLAMA_BASE_URL
OLLAMA_API_URL = settings.OLLAMA_API_URL
DEFAULT_VLM_MODEL = settings.DEFAULT_VLM_MODEL
VLM_MODEL_MARKERS = (
    "qwen3-vl",
    "qwen2-vl",
    "minicpm-v",
    "llama3.2-vision",
    "llava",
    "vision",
    "-vl",
    "_vl",
)

def _normalize_model_name(model_name: Optional[str]) -> Optional[str]:
    normalized = str(model_name or "").strip()
    return normalized or None

def is_likely_vlm_model(model_name: Optional[str]) -> bool:
    lower = str(model_name or "").strip().lower()
    return bool(lower and any(marker in lower for marker in VLM_MODEL_MARKERS))

async def get_ollama_models(timeout: float = 2.0) -> List[str]:
    """Return model names reported by the local Ollama service."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(f"{OLLAMA_BASE_URL.rstrip('/')}/api/tags")
        resp.raise_for_status()
        return [
            str(model.get("name") or "").strip()
            for model in resp.json().get("models", [])
            if str(model.get("name") or "").strip()
        ]

async def get_ollama_vlm_models(timeout: float = 2.0) -> List[str]:
    """Return installed Ollama models whose names indicate image-input support."""
    return [
        model_name
        for model_name in await get_ollama_models(timeout=timeout)
        if is_likely_vlm_model(model_name)
    ]

async def _get_available_vlm_model(preferred_model: Optional[str] = None) -> str:
    """自动检测本地可用的 VLM 模型"""
    preferred = _normalize_model_name(preferred_model)
    try:
        models = await get_ollama_models(timeout=2.0)
        if preferred and is_likely_vlm_model(preferred):
            if preferred in models:
                return preferred
            for model in models:
                if is_likely_vlm_model(model) and (model.startswith(preferred) or preferred in model):
                    return model
        # 优先级：qwen3-vl > qwen2-vl > minicpm-v > 任何包含 vl/vision/llava 的模型
        for target in ["qwen3-vl", "qwen2-vl", "minicpm-v", "llama3.2-vision", "llava"]:
            for model in models:
                if target in model.lower():
                    return model
        for model in models:
            if is_likely_vlm_model(model):
                return model
    except Exception:
        pass
    return DEFAULT_VLM_MODEL

async def analyze_map_with_vlm(
    images_base64: list,
    prompt: str,
    progress_callback=None,
    model_name: Optional[str] = None,
    raise_on_error: bool = False,
) -> str:
    """
    使用本地 Ollama 部署的多模态大模型分析地图截图。
    已改为一次性返回模式，以提高连接稳定性。
    """
    if not images_base64:
        return "未接收到有效的地图截图。"

    resolved_model_name = await _get_available_vlm_model(model_name)

    payload = {
        "model": resolved_model_name,
        "prompt": prompt,
        "images": [img.split(",")[1] if "," in img else img for img in images_base64],
        "stream": False, # 关闭流式传输，改为一次性返回
        "options": {
            "num_predict": -1, # 彻底放开字数限制，允许生成长报告
            "temperature": 0.6,
            "top_p": 0.9
        },
        "keep_alive": "30m"
    }

    timeout_seconds = 600.0 # 保持长超时，确保复杂诊断不中断

    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(OLLAMA_API_URL, json=payload)
            response.raise_for_status()
            
            result = response.json()
            full_thinking = result.get("thinking", "")
            full_response = result.get("response", "")

            # 最终组合结果
            final_output = ""
            if full_thinking:
                final_output += f"> [!NOTE] 思考过程\n> {full_thinking}\n\n"
            final_output += full_response
            
            return final_output.strip() if final_output else f"模型 ({resolved_model_name}) 未返回任何内容。"

    except Exception as e:
        if raise_on_error:
            raise RuntimeError(f"Ollama VLM request failed: {str(e)}") from e
        return f"AI 分析过程中发生错误: {str(e)}"

async def generate_dinsar_diagnosis(
    images_base64: list,
    record_name: str,
    date_str: str,
    quality_context: str,
    hazard_info: str,
    progress_callback=None,
    model_name: Optional[str] = None,
) -> str:
    """
    针对 VLM 优化的 D-InSAR 专家诊断逻辑。
    """
    resolved_model_name = await _get_available_vlm_model(model_name)
    
    prompt = (
        f"你是一位拥有 20 年经验的资深 InSAR 地质灾害解译专家。请根据提供的 D-InSAR 形变图及背景信息，撰写一份专业的诊断报告。\n\n"
        f"### 1. 基础背景\n"
        f"- **任务标识**: `{record_name}`\n"
        f"- **监测周期**: {date_str}\n"
        f"- **数据质量**: {quality_context}\n\n"
        f"### 2. 空间上下文（已知灾害点）\n"
        f"影像覆盖范围内的已知灾害点信息如下：\n"
        f"{hazard_info}\n\n"
        f"### 3. 影像说明\n"
        f"提供的影像采用固定色标（±0.1m），绿色代表稳定，红色代表沉降，蓝色代表抬升。\n\n"
        f"### 4. 诊断任务（请按以下步骤思考）\n"
        f"1. **形态学分析**: 观察影像中是否存在具有空间连续性的色斑或相位条纹？形变区边缘是否清晰？\n"
        f"2. **时空一致性**: 影像中的形变信号是否与已知灾害点位置吻合？如果吻合，评估其当前的活动强度（活跃/趋于稳定）。\n"
        f"3. **风险演化预测**: 在已知点之外，是否发现了新的疑似隐患点？结合地形判断其潜在威胁。\n"
        f"4. **综合风险评级**: 给出“低”、“中”或“高”风险评级，并简述理由。\n\n"
        f"### 5. 输出要求\n"
        f"- 使用 Markdown 格式，语言严谨、专业，严禁幻觉。\n"
        f"- 报告末尾必须包含以下加粗文字：\n"
        f"**--- 免责声明 ---**\n"
        f"**本报告由 AI 自动生成（模型：{resolved_model_name}），仅供科研参考，不具备法律效力。**"
    )

    return await analyze_map_with_vlm(images_base64, prompt, progress_callback=progress_callback, model_name=resolved_model_name)

async def warm_up_vlm(model_name: Optional[str] = None) -> bool:
    """
    预热 VLM 模型，将其加载至显存。
    发送一个轻量级请求以触发模型冷启动。
    返回 True 表示成功，False 表示失败。
    """
    # 预热时直接使用指定模型或探测到的模型
    resolved_model_name = await _get_available_vlm_model(model_name)
    
    payload = {
        "model": resolved_model_name,
        "prompt": "hi",
        "stream": False,
        "keep_alive": "30m"
    }
    
    try:
        # 预热请求给予 60s 超时，通常模型加载需要 10-30s
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(OLLAMA_API_URL, json=payload)
            response.raise_for_status()
            return True
    except Exception as e:
        print(f"模型预热失败: {str(e)}")
        return False
