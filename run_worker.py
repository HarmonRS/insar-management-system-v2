import os
import asyncio
from dotenv import load_dotenv

from backend.app.database import init_db


def main() -> None:
    load_dotenv()
    init_db()

    poll_interval = float(os.getenv("JOB_WORKER_POLL_INTERVAL", "1.0"))
    concurrency = int(os.getenv("JOB_WORKER_CONCURRENCY", "1"))

    # Import after init_db to ensure AsyncSessionLocal is initialized.
    from backend.app.services.job_worker import run_worker_loop

    print("[*] Starting job worker...")
    print(f"[*] Poll interval: {poll_interval}s")
    print(f"[*] Concurrency: {concurrency}")

    asyncio.run(run_worker_loop(poll_interval=poll_interval, concurrency=concurrency))


if __name__ == "__main__":
    main()
