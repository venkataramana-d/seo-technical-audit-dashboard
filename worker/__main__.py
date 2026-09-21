"""`python -m worker` - starts the worker loop.

Imports `worker.tasks` for its side effect (registering job handlers via the
`@register` decorator) before starting. The loop itself interleaves two
things in one process (no separate scheduler process/cron daemon - see
`worker/scheduler.py`'s docstring for why): claiming/processing queued jobs
via `Worker.run_once()`, and periodically checking for due recurring crawls
via `enqueue_due_crawls()`. `worker/queue.py` stays fully generic - only this
driving loop knows both concerns exist.
"""

import logging
import time

from worker import tasks  # noqa: F401 - import registers job handlers
from worker.queue import Worker
from worker.scheduler import enqueue_due_crawls

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("worker")

SCHEDULE_CHECK_INTERVAL = 60.0  # seconds between checks for due recurring crawls


def main() -> None:
    worker = Worker(poll_interval=2.0)
    last_schedule_check = 0.0
    logger.info(
        "worker started, polling every %.1fs, checking schedules every %.0fs",
        worker.poll_interval,
        SCHEDULE_CHECK_INTERVAL,
    )
    while True:
        now = time.monotonic()
        if now - last_schedule_check >= SCHEDULE_CHECK_INTERVAL:
            due = enqueue_due_crawls()
            if due:
                logger.info("scheduler enqueued %d due crawl(s): %s", len(due), due)
            last_schedule_check = now

        if not worker.run_once():
            time.sleep(worker.poll_interval)


if __name__ == "__main__":
    main()
