"""
测试 AI 诊断 API 的简单脚本
"""
import asyncio
import sys
sys.path.insert(0, '.')

async def test_create_diagnosis():
    from app.routers.ai import create_diagnosis
    from app.models import AiDiagnosisCreate

    # 创建测试请求
    request = AiDiagnosisCreate(
        result_id=1,  # 假设存在 ID 为 1 的结果
        model_name="llama3.2-vision",
        prompt_template="standard",
        custom_prompt=None
    )

    try:
        result = await create_diagnosis(request)
        print("Success:", result)
    except Exception as e:
        print(f"Error: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_create_diagnosis())
