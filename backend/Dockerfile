FROM golang:1.24-alpine AS build

WORKDIR /src
COPY go.mod ./
COPY *.go ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /gateway .

FROM alpine:3.21
COPY --from=build /gateway /gateway
USER 65532:65532
EXPOSE 8100
ENTRYPOINT ["/gateway"]
