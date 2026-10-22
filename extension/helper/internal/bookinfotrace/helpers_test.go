package bookinfotrace

import (
	"context"
	"errors"
	"strings"

	"resilience-engine/helper/internal/execx"
	"resilience-engine/helper/internal/kube"
)

func f(v float64) *float64 { return &v }
func b(v bool) *bool       { return &v }
func i(v int) *int         { return &v }

// runner answers by the docker sub-command, and records every call.
type runner struct {
	nodes     string // what `docker ps` prints
	addresses map[string]string
	connect   map[string]bool // node -> can it open the OTLP port
	calls     [][]string
	info      string
	stats     string
	crictl    string
	sysctl    string
}

func (r *runner) Run(_ context.Context, name string, args []string, _ []byte) (execx.Result, error) {
	r.calls = append(r.calls, append([]string{name}, args...))
	if name == "/usr/sbin/sysctl" {
		if r.sysctl == "" {
			return execx.Result{ExitCode: 1}, nil
		}
		return execx.Result{Stdout: []byte(r.sysctl)}, nil
	}
	switch args[0] {
	case "ps":
		return execx.Result{Stdout: []byte(r.nodes)}, nil
	case "info":
		return execx.Result{Stdout: []byte(r.info)}, nil
	case "stats":
		return execx.Result{Stdout: []byte(r.stats)}, nil
	case "exec":
		node, rest := args[1], args[2:]
		switch rest[0] {
		case "getent":
			out, ok := r.addresses[node]
			if !ok {
				return execx.Result{ExitCode: 2}, nil
			}
			return execx.Result{Stdout: []byte(out)}, nil
		case "timeout":
			if r.connect[node] {
				return execx.Result{}, nil
			}
			return execx.Result{ExitCode: 1}, nil
		case "crictl":
			return execx.Result{Stdout: []byte(r.crictl)}, nil
		}
	}
	return execx.Result{ExitCode: 1}, nil
}

func (r *runner) deps() Deps {
	return Deps{Runner: r, FindDocker: func() (string, error) { return "/usr/local/bin/docker", nil }, GOOS: "darwin"}
}

func getent(ip string) string {
	return ip + "  STREAM host.docker.internal\n" + ip + "  DGRAM\n" + ip + "  RAW\n"
}

// healthy is the cluster as it was measured on the real machine: three nodes that agree and reach the receiver.
func healthy() *runner {
	return &runner{
		nodes:     "desktop-control-plane\ndesktop-worker\ndesktop-worker2\n",
		addresses: map[string]string{"desktop-control-plane": getent("192.168.65.254"), "desktop-worker": getent("192.168.65.254"), "desktop-worker2": getent("192.168.65.254")},
		connect:   map[string]bool{"desktop-control-plane": true, "desktop-worker": true, "desktop-worker2": true},
		info:      "4107005952\n",
		stats:     "910.2MiB / 3.825GiB\n384.1MiB / 3.825GiB\n569.4MiB / 3.825GiB\n41.43MiB / 256MiB\n",
		sysctl:    "total = 4096.00M  used = 2688.88M  free = 1407.12M  (encrypted)\n",
	}
}

// fakeReader is a read-only cluster. It cannot write: kube.Reader has no write methods.
type fakeReader struct {
	version  string
	versionE error
	objects  map[string]*kube.Object  // "Resource/namespace/name"
	lists    map[string][]kube.Object // "Resource/namespace"
	listE    map[string]error
}

func key(r kube.Resource, ns, name string) string { return r.Plural + "/" + ns + "/" + name }

func (c *fakeReader) ServerVersion(context.Context) (string, error) { return c.version, c.versionE }
func (c *fakeReader) Get(_ context.Context, r kube.Resource, ns, name string) (*kube.Object, error) {
	if o, ok := c.objects[key(r, ns, name)]; ok {
		return o, nil
	}
	return nil, &kube.APIError{Code: 404, Reason: "NotFound"}
}
func (c *fakeReader) List(_ context.Context, r kube.Resource, ns string) ([]kube.Object, error) {
	if err := c.listE[r.Plural+"/"+ns]; err != nil {
		return nil, err
	}
	return c.lists[r.Plural+"/"+ns], nil
}

var errBoom = errors.New("boom")

func joined(call []string) string { return strings.Join(call, " ") }

// the facts of the real benchmark: four Deployments one replica each and Ready, one container per Pod, no Istio.
func benchmarkFacts() Facts {
	deployment := func(digest string) DeploymentFact {
		return DeploymentFact{Replicas: i(1), ReadyReplicas: i(1), Containers: []string{"app"}, SpecDigest: digest}
	}
	pod := func(name, app string) PodFact {
		return PodFact{Name: name, App: app, Version: "v1", Containers: []string{app}, Ready: b(true), Phase: "Running"}
	}
	return Facts{
		ServerVersion: "v1.36.1", KubernetesMinor: 36, NamespaceExists: true,
		NamespaceLabels: map[string]string{"kubernetes.io/metadata.name": "bookinfo"},
		Deployments: map[string]DeploymentFact{
			"productpage-v1": deployment("aaaa1111aaaa1111"), "details-v1": deployment("bbbb2222bbbb2222"),
			"ratings-v1": deployment("cccc3333cccc3333"), "reviews-v1": deployment("dddd4444dddd4444"),
		},
		Pods: []PodFact{pod("productpage-v1-7498b5bd44-jbskx", "productpage"), pod("details-v1-65bd58d55f-b7zln", "details"),
			pod("ratings-v1-9bff6cb79-dc6k5", "ratings"), pod("reviews-v1-54465994f9-jlmrk", "reviews")},
	}
}

func okAddress() *Address {
	return &Address{IPv4: "192.168.65.254", Port: "4317", Nodes: []string{"desktop-control-plane", "desktop-worker", "desktop-worker2"}}
}

// roomy is a baseline with plenty of Docker memory free and swap readable.
func roomy() Measurements {
	return Measurements{VMLimitMiB: f(3917), VMUsedMiB: f(2025), SwapUsedMiB: f(2689)}
}
