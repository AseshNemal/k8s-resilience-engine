package flow

import (
	"crypto/sha256"
	"encoding/hex"
	encpem "encoding/pem"

	"resilience-engine/helper/internal/clusters"
	"resilience-engine/helper/internal/kubeconfig"
	"resilience-engine/helper/internal/transport"
)

// Why a credential is installed. See transport.InstallRequest.Origin.
const (
	OriginAuthorize = "authorize"
	OriginRenew     = "renew"
	OriginReconnect = "reconnect"
)

// identityOf is what a cluster is, for deciding whether it is the one that was approved: its API endpoint and
// the fingerprint of its certificate authority. A recreated cluster gets a new authority, and a context
// pointed somewhere else gets another address, so either one changes the identity.
func identityOf(c clusters.Context, cluster kubeconfig.Cluster) transport.Identity {
	return transport.Identity{Server: clusters.HostPort(c.Server), CASha256: caFingerprint(cluster.CAData)}
}

// caFingerprint hashes the certificates themselves (their DER bytes), so blank lines or a re-encoded PEM in
// the kubeconfig do not change it. Data that is not a certificate is hashed as given, never skipped.
func caFingerprint(ca []byte) string {
	var der []byte
	rest := ca
	for {
		var block *encpem.Block
		block, rest = encpem.Decode(rest)
		if block == nil {
			break
		}
		if block.Type == "CERTIFICATE" {
			der = append(der, block.Bytes...)
		}
	}
	if len(der) == 0 {
		der = ca
	}
	sum := sha256.Sum256(der)
	return hex.EncodeToString(sum[:])
}

func sameIdentity(a *transport.Approval, id transport.Identity) bool {
	return a != nil && a.Server == id.Server && a.CASha256 == id.CASha256
}
