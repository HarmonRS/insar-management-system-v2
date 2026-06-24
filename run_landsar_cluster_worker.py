import asyncio
import os

from dotenv import load_dotenv

from backend.app.database import init_db


def main() -> None:
    load_dotenv()
    os.environ.setdefault("JOB_WORKER_ALLOWED_TYPES", "LANDSAR_CLUSTER_ITEM")
    os.environ.setdefault("JOB_WORKER_CONCURRENCY", "1")

    init_db()

    poll_interval = float(os.getenv("JOB_WORKER_POLL_INTERVAL", "1.0"))
    concurrency = int(os.getenv("JOB_WORKER_CONCURRENCY", "1"))
    worker_id = os.getenv("LANDSAR_CLUSTER_WORKER_ID", "").strip()

    from backend.app.services.job_worker import run_worker_loop

    print("[*] Starting LandSAR cluster worker...")
    print("[*] Allowed job types: LANDSAR_CLUSTER_ITEM")
    print(f"[*] Poll interval: {poll_interval}s")
    print(f"[*] Concurrency: {concurrency}")

    asyncio.run(
        run_worker_loop(
            worker_id=worker_id,
            poll_interval=poll_interval,
            concurrency=concurrency,
        )
    )


if __name__ == "__main__":
    main()
