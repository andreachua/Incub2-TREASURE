"""Enqueue a receipt job onto the Redis queue (simulates the frontend).

Examples:
    uv run scripts/enqueue.py --system oxn --receipt samples/receipt1.png
    uv run scripts/enqueue.py --system oxn --receipt samples/po2.pdf --embed-b64 --wait
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import redis  # noqa: E402

from src.config import DEFAULT_SYSTEM, VALID_SYSTEMS, get_settings  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="Enqueue a receipt job for the worker.")
    ap.add_argument("--system", default=DEFAULT_SYSTEM, choices=VALID_SYSTEMS)
    ap.add_argument("--receipt", required=True, help="Path to a receipt image/PDF.")
    ap.add_argument("--job-id", default=None)
    ap.add_argument(
        "--embed-b64",
        action="store_true",
        help="Send the file contents inline as base64 instead of a path.",
    )
    ap.add_argument("--wait", action="store_true", help="Block for the result and print it.")
    args = ap.parse_args()

    s = get_settings()
    client = redis.Redis.from_url(s.redis_url, decode_responses=True)

    job_id = args.job_id or uuid.uuid4().hex
    job = {"job_id": job_id, "system": args.system}
    if args.embed_b64:
        data = Path(args.receipt).read_bytes()
        job["receipt_b64"] = base64.b64encode(data).decode("ascii")
        job["filename"] = Path(args.receipt).name
    else:
        job["receipt_path"] = args.receipt

    client.lpush(s.redis_job_queue, json.dumps(job))
    print(f"enqueued job {job_id} on '{s.redis_job_queue}'")

    if args.wait:
        key = f"{s.redis_result_prefix}{job_id}"
        print(f"waiting for result at '{key}' ...")
        import time

        for _ in range(600):  # up to ~5 min
            payload = client.get(key)
            if payload:
                print(json.dumps(json.loads(payload), indent=2))
                return
            time.sleep(0.5)
        print("timed out waiting for result", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
