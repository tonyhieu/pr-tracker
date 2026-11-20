"""Entry point: `uv run python -m prt [--seed]`."""

import os
import sys

import uvicorn

from prt import db
from prt.app import create_app


def main() -> None:
    if "--seed" in sys.argv[1:]:
        conn = db.connect()
        db.seed_demo(conn)
        conn.close()
    port = int(os.environ.get("PRT_PORT", "4747"))
    uvicorn.run(create_app(), host="127.0.0.1", port=port, workers=1)


if __name__ == "__main__":
    main()
