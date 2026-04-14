-- 清除卡住的 AI_DIAGNOSIS 任务
-- 任务 ID: 82db228d-ba61-419b-920c-bbdad74f9f94

-- 1. 查看任务当前状态
SELECT task_id, task_type, status, message, created_at, updated_at
FROM system_tasks
WHERE task_id = '82db228d-ba61-419b-920c-bbdad74f9f94';

-- 2. 更新任务状态为 FAILED
UPDATE system_tasks
SET status = 'FAILED',
    message = '手动清除：测试任务失败',
    updated_at = CURRENT_TIMESTAMP
WHERE task_id = '82db228d-ba61-419b-920c-bbdad74f9f94';

-- 3. 更新关联的 Job 状态
UPDATE system_jobs
SET status = 'FAILED',
    updated_at = CURRENT_TIMESTAMP
WHERE task_id = '82db228d-ba61-419b-920c-bbdad74f9f94'
  AND status NOT IN ('COMPLETED', 'FAILED');

-- 4. 验证更新结果
SELECT task_id, task_type, status, message
FROM system_tasks
WHERE task_id = '82db228d-ba61-419b-920c-bbdad74f9f94';

SELECT job_id, job_type, status
FROM system_jobs
WHERE task_id = '82db228d-ba61-419b-920c-bbdad74f9f94';
