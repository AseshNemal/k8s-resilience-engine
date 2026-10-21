ARG UPSTREAM_COMMIT=39efed435e697bcdcd31c5e45f4d36dc35c98a63

FROM alpine/git:2.47.2 AS source
ARG UPSTREAM_COMMIT
RUN git clone https://github.com/GoogleCloudPlatform/microservices-demo.git /source \
    && cd /source \
    && git checkout --detach "${UPSTREAM_COMMIT}" \
    && test "$(git rev-parse HEAD)" = "${UPSTREAM_COMMIT}"

FROM golang:1.22.5-alpine@sha256:8c9183f715b0b4eca05b8b3dbf59766aaedb41ec07477b132ee2891ac0110a07 AS builder
WORKDIR /src
COPY --from=source /source/src/shippingservice/ ./
COPY main.go ./main.go
RUN go get go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracegrpc@v1.24.0 \
    go.opentelemetry.io/otel/sdk@v1.24.0 \
    && go mod tidy \
    && go mod verify
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -o /go/bin/shippingservice .

FROM scratch
WORKDIR /src
COPY --from=builder /go/bin/shippingservice /src/shippingservice
ENV APP_PORT=50051 \
    GOTRACEBACK=single
EXPOSE 50051
ENTRYPOINT ["/src/shippingservice"]
