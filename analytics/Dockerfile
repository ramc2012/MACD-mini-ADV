FROM rust:1.88-alpine AS builder
RUN apk add --no-cache musl-dev
WORKDIR /app
COPY Cargo.toml Cargo.lock ./
COPY src ./src
RUN cargo test --locked --release && cargo build --locked --release

FROM alpine:3.21
COPY --from=builder /app/target/release/macd-analytics /usr/local/bin/macd-analytics
ENV PORT=8081
EXPOSE 8081
USER 10001:10001
CMD ["macd-analytics"]
