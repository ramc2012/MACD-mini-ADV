FROM golang:1.25-alpine AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN go test ./...
RUN CGO_ENABLED=0 go build -trimpath -o /us-paper .

FROM alpine:3.22
RUN apk add --no-cache ca-certificates tzdata && addgroup -S paper && adduser -S -G paper paper && mkdir /data && chown paper:paper /data
COPY --from=build /us-paper /usr/local/bin/us-paper
USER paper
EXPOSE 8080
CMD ["/usr/local/bin/us-paper"]
