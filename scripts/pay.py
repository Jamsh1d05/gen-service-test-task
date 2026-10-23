"""Send a signed payment webhook to a running service.

python scripts/pay.py <user_id> <tokens> [payment_id] [--url http://localhost:8000]
"""

import argparse
import hashlib
import hmac
import json
import os
import urllib.request
import uuid


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("user_id", type=int)
    parser.add_argument("tokens", type=int)
    parser.add_argument("payment_id", nargs="?", default=None)
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--status", default="succeeded")
    args = parser.parse_args()

    body = json.dumps(
        {
            "payment_id": args.payment_id or f"manual-{uuid.uuid4()}",
            "user_id": args.user_id,
            "tokens": args.tokens,
            "status": args.status,
        }
    ).encode()
    secret = os.getenv("WEBHOOK_SECRET", "dev-secret").encode()
    request = urllib.request.Request(
        f"{args.url}/webhooks/payments",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Signature": hmac.new(secret, body, hashlib.sha256).hexdigest(),
        },
    )
    with urllib.request.urlopen(request) as response:
        print(response.status, response.read().decode())


if __name__ == "__main__":
    main()
