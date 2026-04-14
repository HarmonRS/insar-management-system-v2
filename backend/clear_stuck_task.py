"""清除卡住的 AI_DIAGNOSIS 任务"""
import asyncio
from app.database import AsyncSessionLocal
from app.models import SystemTaskORM, SystemJobORM
from sqlalchemy.future import select

async def clear_stuck_task():
    task_id = '82db228d-ba61-419b-920c-bbdad74f9f94'

    async with AsyncSessionLocal() as db:
        # 查找任务
        result = await db.execute(
            select(SystemTaskORM).where(SystemTaskORM.task_id == task_id)
        )
        task = result.scalar_one_or_none()

        if not task:
            print(f'❌ 任务 {task_id} 不存在')
            return

        print(f'📋 找到任务:')
        print(f'  - Task ID: {task.task_id}')
        print(f'  - Task Type: {task.task_type}')
        print(f'  - Status: {task.status}')
        print(f'  - Message: {task.message}')
        print(f'  - Created: {task.created_at}')
        print(f'  - Updated: {task.updated_at}')

        # 更新任务状态为 FAILED
        task.status = 'FAILED'
        task.message = '手动清除：测试任务失败'

        # 查找关联的 Job
        job_result = await db.execute(
            select(SystemJobORM).where(SystemJobORM.task_id == task_id)
        )
        jobs = job_result.scalars().all()

        if jobs:
            print(f'\n📦 找到 {len(jobs)} 个关联 Job:')
            for job in jobs:
                print(f'  - Job ID: {job.job_id}')
                print(f'  - Job Type: {job.job_type}')
                print(f'  - Status: {job.status}')

                # 更新 Job 状态
                if job.status not in ['COMPLETED', 'FAILED']:
                    job.status = 'FAILED'
                    print(f'    ✅ 已更新为 FAILED')

        await db.commit()
        print(f'\n✅ 任务已清除，状态更新为 FAILED')

if __name__ == '__main__':
    asyncio.run(clear_stuck_task())
