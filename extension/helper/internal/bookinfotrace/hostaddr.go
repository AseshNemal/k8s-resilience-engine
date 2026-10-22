package bookinfotrace

import (
	"context"
	"errors"
	"net"
	"regexp"
	"strings"

	"resilience-engine/helper/internal/execx"
)

// Deps are the host operations, behind interfaces so no test touches Docker or the cluster.
type Deps struct {
	Runner     execx.Runner
	FindDocker func() (string, error)
	GOOS       string
	ReadFile   func(string) ([]byte, error)
	Getenv     func(string) string // only SystemRoot is read, to find Windows PowerShell
}

const (
	// otlpGRPCPort is the receiver the tracing provider talks to.
	otlpGRPCPort = "4317"
	// HostName is what a node resolves to reach this computer's published ports.
	HostName = "host.docker.internal"
	maxNodes = 4
)

var nodeName = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$`)

func dockerPath(d Deps) (string, bool) {
	if d.FindDocker == nil || d.Runner == nil {
		return "", false
	}
	path, err := d.FindDocker()
	return path, err == nil && path != ""
}

// kindNodes lists the Kubernetes node containers (label io.x-k8s.kind.role), at most maxNodes, with safe names only.
func kindNodes(ctx context.Context, d Deps, docker string) []string {
	res, err := d.Runner.Run(ctx, docker, []string{"ps", "--filter", "label=io.x-k8s.kind.role", "--format", "{{.Names}}"}, nil)
	if err != nil || res.ExitCode != 0 {
		return nil
	}
	var nodes []string
	for _, line := range strings.Fields(string(res.Stdout)) {
		if nodeName.MatchString(line) && len(nodes) < maxNodes {
			nodes = append(nodes, line)
		}
	}
	return nodes
}

// Address is the rediscovered address of the Jaeger OTLP receiver as the cluster's nodes see it.
type Address struct {
	IPv4  string   `json:"ipv4"`
	Port  string   `json:"port"`
	Nodes []string `json:"nodes"` // the nodes that agreed on the address and reached it
}

// Discovery failures. Each is a fixed reason; Docker's own text is never returned.
var (
	ErrDockerUnavailable = errors.New("Docker could not be reached")
	ErrNoNodes           = errors.New("no Kubernetes node containers were found")
	ErrNoAddress         = errors.New("a node could not resolve " + HostName + " to an IPv4 address")
	ErrNodesDisagree     = errors.New("the nodes do not agree on the address of " + HostName)
	ErrNotPrivate        = errors.New(HostName + " did not resolve to a private IPv4 address")
	ErrUnreachable       = errors.New("a node could not connect to the Jaeger OTLP receiver at the discovered address")
)

// parseIPv4 takes the first IPv4 address in `getent ahostsv4` output and accepts it only if it is a private address:
// a loopback, link-local, multicast, unspecified or public address is never used as a tracing target.
func parseIPv4(output string) (string, error) {
	for _, field := range strings.Fields(output) {
		ip := net.ParseIP(field)
		if ip == nil || ip.To4() == nil {
			continue
		}
		if !ip.IsPrivate() || ip.IsLoopback() || ip.IsLinkLocalUnicast() || ip.IsUnspecified() || ip.IsMulticast() {
			return "", ErrNotPrivate
		}
		return ip.To4().String(), nil
	}
	return "", ErrNoAddress
}

// RediscoverJaegerOTLP finds the address, from inside the cluster's nodes, and proves it. Nothing is assumed:
//
//   - every node (up to four) resolves HostName and they must all agree on one private IPv4 address;
//   - every one of them must then open a TCP connection to that address on the OTLP gRPC port (a connection only,
//     no bytes are sent, so no trace can be created).
//
// It is run by Plan, again just before an Apply, and by Verify, which compares the result with the address in the
// ServiceEntry. A changed address is reported there; it is never silently kept.
func RediscoverJaegerOTLP(ctx context.Context, d Deps) (Address, error) {
	docker, ok := dockerPath(d)
	if !ok {
		return Address{}, ErrDockerUnavailable
	}
	nodes := kindNodes(ctx, d, docker)
	if len(nodes) == 0 {
		return Address{}, ErrNoNodes
	}
	address := ""
	for _, node := range nodes {
		res, err := d.Runner.Run(ctx, docker, []string{"exec", node, "getent", "ahostsv4", HostName}, nil)
		if err != nil || res.ExitCode != 0 {
			return Address{}, ErrNoAddress
		}
		found, perr := parseIPv4(string(res.Stdout))
		if perr != nil {
			return Address{}, perr
		}
		if address != "" && found != address {
			return Address{}, ErrNodesDisagree
		}
		address = found
	}
	for _, node := range nodes {
		// address is a parsed net.IP rendered by the standard library, so it holds only digits and dots.
		res, err := d.Runner.Run(ctx, docker, []string{"exec", node, "timeout", "3", "bash", "-c", "exec 3<>/dev/tcp/" + address + "/" + otlpGRPCPort}, nil)
		if err != nil || res.ExitCode != 0 {
			return Address{}, ErrUnreachable
		}
	}
	return Address{IPv4: address, Port: otlpGRPCPort, Nodes: nodes}, nil
}

// Drift reports whether the address now discovered differs from the one a ServiceEntry holds.
func Drift(applied string, current Address) bool { return applied != current.IPv4 }
