# THROWAWAY proof-of-concept extension. UI tab + the Go helper only: no backend, no cluster
# credentials. Installed under its own name; remove with `docker extension rm resilience-helper-poc`.
FROM alpine:latest
LABEL org.opencontainers.image.title="Resilience Helper PoC" \
      org.opencontainers.image.description="Temporary test: can the extension UI launch the Go host helper? Creates nothing." \
      org.opencontainers.image.vendor="K8s Resilience Engine research project (SLIIT J26-SE-311)" \
      com.docker.desktop.extension.api.version=">= 0.2.3" \
      com.docker.extension.categories="utility-tools"
COPY metadata.json icon.svg /
COPY ui /ui
COPY --chmod=0755 resilience-helper /darwin/resilience-helper
