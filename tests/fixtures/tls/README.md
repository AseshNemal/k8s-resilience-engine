# Test-only TLS material

Everything here is generated for automated tests and protects nothing. The CA is private to the tests,
its key was deleted after signing, and the certificates are valid for the name `localhost` only (like
kind's API server certificate, they list no IP address, which is what makes `tls-server-name` matter).

| File | Used for |
|---|---|
| `ca.pem` | the CA the test API server's certificate chains to |
| `server.pem` / `server.key` | a TLS test server for `localhost` |
| `client.pem` / `client.key` | a client certificate (kind's admin context authenticates this way) |
| `backend-kubeconfig.yaml` | golden output of the Go helper's `kubeconfig.Build`, which the Python backend must be able to read |

Regenerate `backend-kubeconfig.yaml` after an intended format change with
`(cd extension/helper && UPDATE_GOLDEN=1 go test ./internal/kubeconfig/ -run Golden)`.
