"""Container healthcheck: GET /api/health over the Unix socket (the app has no TCP port)."""

import socket
import sys

SOCKET = "/run/guest-services/k8s-resilience-engine.sock"

try:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(3)
        connection.connect(SOCKET)
        connection.sendall(b"GET /api/health HTTP/1.0\r\nHost: localhost\r\n\r\n")
        reply = connection.recv(64)
except OSError:
    sys.exit(1)

sys.exit(0 if b" 200 " in reply.split(b"\r\n", 1)[0] else 1)
