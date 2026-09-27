FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .
RUN mkdir -p /app/runtime
EXPOSE 8100
ENV MACD_LISTEN_HOST=0.0.0.0
CMD ["python", "-m", "macd_trader"]
