ARG UPSTREAM_COMMIT=39efed435e697bcdcd31c5e45f4d36dc35c98a63

FROM alpine/git:2.47.2 AS source
ARG UPSTREAM_COMMIT
RUN git clone https://github.com/GoogleCloudPlatform/microservices-demo.git /source \
    && cd /source \
    && git checkout --detach "${UPSTREAM_COMMIT}" \
    && test "$(git rev-parse HEAD)" = "${UPSTREAM_COMMIT}"

FROM mcr.microsoft.com/dotnet/sdk:8.0.303-noble@sha256:487fcee12549e1f56567d238ed4198f2e9ef7de076d399980eae8dcdf84efe82 AS builder
WORKDIR /app
COPY --from=source /source/src/cartservice/src/ ./
COPY cartservice.csproj Startup.cs ./
RUN dotnet restore cartservice.csproj -r linux-x64
RUN dotnet publish cartservice.csproj \
    -p:PublishSingleFile=true \
    -r linux-x64 \
    --self-contained true \
    -p:PublishTrimmed=false \
    -c release \
    -o /cartservice

FROM mcr.microsoft.com/dotnet/runtime-deps:8.0.7-noble-chiseled@sha256:a5aaf2f3dcbd52c5b5e3623e08c8bae86f2db784994bce6872c4d7c954c6762a
WORKDIR /app
COPY --from=builder /cartservice .
EXPOSE 7070
ENV DOTNET_EnableDiagnostics=0 \
    ASPNETCORE_HTTP_PORTS=7070
USER 1000
ENTRYPOINT ["/app/cartservice"]
