"""The Redis consumer — the pipeline's entry point as a long-running process.

BRPOPs the "object-categorization" queue, runs ``process_job`` on each message,
and publishes the result three ways: a Redis key with a TTL, a pub/sub channel,
and a JSON file under ``output/``.

``_running`` and ``_consumer_stop`` belong together in this module: the signal
handler flips the flag the loop reads, so separating them would silently break
graceful shutdown on SIGTERM.

Run:  uv run python -m app.pipeline.consumer
"""

from __future__ import annotations

from pathlib import Path

from app.core.logging_config import get_logger
from app.pipeline.orchestrator import process_job

log = get_logger("stage1")

_running = True


def _consumer_stop(*_a) -> None:
    global _running
    _running = False
    log.info("shutting down after current message ...")


def _publish(client, s, result: dict) -> None:
    import json

    payload = json.dumps(result, ensure_ascii=False)
    key = f"{s.redis_result_prefix}{result['job_id']}"
    client.set(key, payload, ex=s.redis_result_ttl)
    client.publish(s.redis_results_channel, payload)
    # Relative to the working directory, not to this module — the container's
    # WORKDIR is /app, so results land in /app/output. Setting `working_dir` in
    # compose would silently relocate them.
    out = Path("output") / f"{result['job_id']}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(payload, encoding="utf-8")
    # The result is the terminal state, so the stage the job was on is now
    # stale: drop it rather than let it linger under its TTL and have the poll
    # report a stage for a job that has already finished.
    client.delete(f"{s.redis_progress_prefix}{result['job_id']}")
    log.info("job %s: published result [%s] -> redis '%s', %s",
             result["job_id"], result["status"], key, out)


def _stage_reporter(client, s, job_id: str):
    """An ``on_stage`` callback that publishes the job's current stage.

    The key carries the same TTL as the result it precedes, so a job that dies
    mid-run expires on its own instead of pinning the upload screen to a stage
    forever. Raising here would only lose progress, never the run — the
    orchestrator already treats this callback as cosmetic and swallows failures.

    Returns ``None`` for a message with no job_id: nobody can poll for a job
    they cannot name, and ``process_job`` reads ``None`` as "report nothing" —
    which beats writing every anonymous job's stage to one shared key.
    """
    if not job_id:
        return None

    def report(name: str) -> None:
        client.set(f"{s.redis_progress_prefix}{job_id}", name, ex=s.redis_result_ttl)
        log.info("job %s: stage %s", job_id, name)

    return report


def run_consumer() -> None:
    """Consume messages from the ``object-categorization`` queue and run the pipeline."""
    import json
    import signal

    import redis

    from app.core.config import get_settings

    signal.signal(signal.SIGINT, _consumer_stop)
    signal.signal(signal.SIGTERM, _consumer_stop)

    s = get_settings()

    # Self-migrate: the crawler needs the po/sow tables and the po_index the
    # documents feed, and this process may come up before anything else has run.
    from app.ingest.documents import ensure_po_index

    try:
        ensure_po_index()
    except Exception as exc:  # a missing index degrades matching, not the pipeline
        log.warning("could not ensure the PO index (%s); exact-ref matching only", exc)

    client = redis.Redis.from_url(s.redis_url, decode_responses=True)
    client.ping()
    log.info("connected to redis %s", s.redis_url)
    log.info("waiting for messages on '%s' (Ctrl-C to stop)", s.redis_job_queue)

    while _running:
        item = client.brpop(s.redis_job_queue, timeout=2)  # (queue, value) or None
        if item is None:
            continue
        _, raw = item
        log.info("received message from '%s'", s.redis_job_queue)
        try:
            job = json.loads(raw)
        except json.JSONDecodeError as exc:
            log.warning("skipping malformed message: %s", exc)
            continue
        on_stage = _stage_reporter(client, s, job.get("job_id") or "")
        _publish(client, s, process_job(job, on_stage=on_stage))


if __name__ == "__main__":
    run_consumer()
