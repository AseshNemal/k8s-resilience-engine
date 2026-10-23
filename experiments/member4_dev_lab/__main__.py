"""Start the Member 4 Development Lab on localhost (development only, offline)."""

import os

import uvicorn

from experiments.member4_dev_lab.app import create_app

PORT = int(os.environ.get("DEV_LAB_PORT", "8741"))


def main() -> None:
    print("Member 4 Development Lab (offline, development only)")
    print(f"Serving on http://127.0.0.1:{PORT} — no cluster is read or written.")
    uvicorn.run(create_app(), host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
