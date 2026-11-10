import os
import sys
import threading

import uvicorn


# One image, three layouts. "desk" serves the Market Profile / order-flow lane
# from the tick bus; "all" and "strategy" serve the engine API.
APPS = {"desk": "macd_trader.desk_app:app"}


def serve() -> int:
    role = os.getenv("MACD_ENGINE_ROLE", "all").strip().lower()
    try:
        uvicorn.run(APPS.get(role, "macd_trader.app:app"),
                    host=os.getenv("MACD_LISTEN_HOST", "127.0.0.1"), port=8100, reload=False)
    except SystemExit as exc:                     # uvicorn's own exit (a failed startup is 3)
        return exc.code if isinstance(exc.code, int) else 1
    return 0


if __name__ == "__main__":
    code = serve()
    # The server has shut down: the stores are closed and the clean-shutdown marker is written. Python would now
    # wait for every non-daemon thread, and the Fyers SDK's message and ping threads and the SDK worker pool never
    # end, so the process sat there until Docker killed it (exit 137 on every stop, found 10 Nov 2026). Nothing is
    # left to save: leave now, with the server's own exit code.
    waiting = sorted(thread.name for thread in threading.enumerate()
                     if thread is not threading.main_thread() and not thread.daemon)
    if waiting:
        print(f"exit: leaving {len(waiting)} worker thread(s) behind ({', '.join(waiting[:6])}"
              f"{', ...' if len(waiting) > 6 else ''})", flush=True)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
