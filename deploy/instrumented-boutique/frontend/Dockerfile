ARG UPSTREAM_COMMIT=39efed435e697bcdcd31c5e45f4d36dc35c98a63

FROM alpine/git:2.47.2 AS source
ARG UPSTREAM_COMMIT
RUN git clone https://github.com/GoogleCloudPlatform/microservices-demo.git /source
WORKDIR /source
RUN git checkout --detach "${UPSTREAM_COMMIT}" \
    && test "$(git rev-parse HEAD)" = "${UPSTREAM_COMMIT}"
COPY exporter-connection.patch /tmp/exporter-connection.patch
RUN git apply --check /tmp/exporter-connection.patch \
    && git apply /tmp/exporter-connection.patch

FROM golang:1.22.5-alpine@sha256:8c9183f715b0b4eca05b8b3dbf59766aaedb41ec07477b132ee2891ac0110a07 AS builder
WORKDIR /src
COPY --from=source /source/src/frontend/ ./
RUN go mod download && go mod verify
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -o /go/bin/server .

# Keep the pinned upstream templates, static assets and runtime configuration.
# frontend v0.10.1, pinned by digest
FROM gcr.io/google-samples/microservices-demo/frontend@sha256:25a61a274264640e3449f8799b2d3be87a91e3df2367e4edbed608c6e01821da
COPY --from=builder /go/bin/server /src/server
