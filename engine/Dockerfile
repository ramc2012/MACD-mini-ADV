FROM python:3.12-slim
# sqlite3 CLI and flock for read-only integrity checks after an unclean shutdown.
RUN apt-get update && apt-get install -y --no-install-recommends sqlite3 util-linux && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .
RUN mkdir -p /app/runtime
COPY db_doctor.sh /app/db_doctor.sh
EXPOSE 8100
ENV MACD_LISTEN_HOST=0.0.0.0
CMD ["sh", "-c", "sh /app/db_doctor.sh && exec python -m macd_trader"]
