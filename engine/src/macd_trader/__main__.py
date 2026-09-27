import os

import uvicorn


# One image, three layouts. "desk" serves the Market Profile / order-flow lane
# from the tick bus; "all" and "strategy" serve the engine API.
APPS = {"desk": "macd_trader.desk_app:app"}


if __name__ == "__main__":
    role = os.getenv("MACD_ENGINE_ROLE", "all").strip().lower()
    uvicorn.run(APPS.get(role, "macd_trader.app:app"),
                host=os.getenv("MACD_LISTEN_HOST", "127.0.0.1"), port=8100, reload=False)
