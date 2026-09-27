import os

import uvicorn


if __name__ == "__main__":
    uvicorn.run("macd_trader.app:app", host=os.getenv("MACD_LISTEN_HOST", "127.0.0.1"), port=8100, reload=False)
