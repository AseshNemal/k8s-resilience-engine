ARG UPSTREAM_COMMIT=39efed435e697bcdcd31c5e45f4d36dc35c98a63

FROM alpine/git:2.47.2 AS source
ARG UPSTREAM_COMMIT
RUN git clone https://github.com/GoogleCloudPlatform/microservices-demo.git /source \
    && cd /source \
    && git checkout --detach "${UPSTREAM_COMMIT}" \
    && test "$(git rev-parse HEAD)" = "${UPSTREAM_COMMIT}"
COPY context-propagation.patch /tmp/context-propagation.patch
RUN cd /source && git apply --check /tmp/context-propagation.patch \
    && git apply /tmp/context-propagation.patch

FROM golang:1.22.5-alpine@sha256:8c9183f715b0b4eca05b8b3dbf59766aaedb41ec07477b132ee2891ac0110a07 AS builder
WORKDIR /src
COPY --from=source /source/src/checkoutservice/ ./
RUN go mod download && go mod verify
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -o /go/bin/checkoutservice .

FROM scratch
WORKDIR /src
COPY --from=builder /go/bin/checkoutservice /src/checkoutservice
ENV GOTRACEBACK=single
EXPOSE 5050
ENTRYPOINT ["/src/checkoutservice"]
