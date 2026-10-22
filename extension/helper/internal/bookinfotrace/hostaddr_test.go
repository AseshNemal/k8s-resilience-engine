package bookinfotrace

import (
	"context"
	"errors"
	"strings"
	"testing"
)

func TestTheAddressIsRediscoveredFromEveryNodeAndProvenByConnecting(t *testing.T) {
	r := healthy()
	a, err := RediscoverJaegerOTLP(context.Background(), r.deps())
	if err != nil || a.IPv4 != "192.168.65.254" || a.Port != "4317" || len(a.Nodes) != 3 {
		t.Fatalf("%+v %v", a, err)
	}
	connections := 0
	for _, call := range r.calls {
		if strings.Contains(joined(call), "/dev/tcp/") {
			connections++
		}
	}
	if connections != 3 {
		t.Fatalf("every node must prove the connection, got %d", connections)
	}
}

func TestNothingIsSentWhenProvingTheAddress(t *testing.T) {
	r := healthy()
	RediscoverJaegerOTLP(context.Background(), r.deps())
	for _, call := range r.calls {
		text := joined(call)
		if strings.Contains(text, "/dev/tcp/") && !strings.HasSuffix(text, "exec 3<>/dev/tcp/192.168.65.254/4317") {
			t.Errorf("the connection test is not the fixed one: %s", text)
		}
	}
}

func TestADisagreementBetweenNodesIsNotGuessedThrough(t *testing.T) {
	r := healthy()
	r.addresses["desktop-worker2"] = getent("192.168.65.1")
	if _, err := RediscoverJaegerOTLP(context.Background(), r.deps()); !errors.Is(err, ErrNodesDisagree) {
		t.Fatalf("%v", err)
	}
}

func TestOnlyAPrivateIPv4AddressIsEverUsed(t *testing.T) {
	for name, out := range map[string]string{
		"public":      getent("8.8.8.8"),
		"loopback":    getent("127.0.0.1"),
		"link-local":  getent("169.254.1.1"),
		"unspecified": getent("0.0.0.0"),
		"multicast":   getent("224.0.0.1"),
	} {
		r := healthy()
		for node := range r.addresses {
			r.addresses[node] = out
		}
		if _, err := RediscoverJaegerOTLP(context.Background(), r.deps()); !errors.Is(err, ErrNotPrivate) {
			t.Errorf("%s: %v", name, err)
		}
	}
}

func TestAnAddressThatIsNotIPv4IsNotAnAddress(t *testing.T) {
	for _, out := range []string{"", "fdc4:f303:9324::254  STREAM host.docker.internal\n", "not-an-ip STREAM x\n", "192.168.65.254;rm -rf / STREAM x\n"} {
		r := healthy()
		for node := range r.addresses {
			r.addresses[node] = out
		}
		a, err := RediscoverJaegerOTLP(context.Background(), r.deps())
		if err == nil && strings.ContainsAny(a.IPv4, ";& ") {
			t.Errorf("an unsafe address got through: %q", a.IPv4)
		}
		if out != "192.168.65.254;rm -rf / STREAM x\n" && err == nil {
			t.Errorf("%q must not give an address", out)
		}
	}
}

func TestANodeThatCannotConnectStopsTheDiscovery(t *testing.T) {
	r := healthy()
	r.connect["desktop-worker"] = false
	if _, err := RediscoverJaegerOTLP(context.Background(), r.deps()); !errors.Is(err, ErrUnreachable) {
		t.Fatalf("%v", err)
	}
}

func TestANodeThatCannotResolveStopsTheDiscovery(t *testing.T) {
	r := healthy()
	delete(r.addresses, "desktop-worker")
	if _, err := RediscoverJaegerOTLP(context.Background(), r.deps()); !errors.Is(err, ErrNoAddress) {
		t.Fatalf("%v", err)
	}
}

func TestNoDockerAndNoNodesAreNamedFailures(t *testing.T) {
	d := healthy().deps()
	d.FindDocker = func() (string, error) { return "", errors.New("no") }
	if _, err := RediscoverJaegerOTLP(context.Background(), d); !errors.Is(err, ErrDockerUnavailable) {
		t.Fatalf("%v", err)
	}
	r := healthy()
	r.nodes = ""
	if _, err := RediscoverJaegerOTLP(context.Background(), r.deps()); !errors.Is(err, ErrNoNodes) {
		t.Fatalf("%v", err)
	}
}

func TestOnlySafeNodeNamesAndAtMostFourNodesReachACommandLine(t *testing.T) {
	r := healthy()
	r.nodes = "good-1\nbad;rm\n$(x)\ngood-2\ngood-3\ngood-4\ngood-5\n"
	r.addresses = map[string]string{}
	r.connect = map[string]bool{}
	for _, n := range []string{"good-1", "good-2", "good-3", "good-4", "good-5"} {
		r.addresses[n], r.connect[n] = getent("192.168.65.254"), true
	}
	a, err := RediscoverJaegerOTLP(context.Background(), r.deps())
	if err != nil || len(a.Nodes) != 4 {
		t.Fatalf("%+v %v", a, err)
	}
	for _, call := range r.calls {
		if len(call) > 2 && call[1] == "exec" && !nodeName.MatchString(call[2]) {
			t.Errorf("an unsafe node name reached a command line: %q", call[2])
		}
	}
}

func TestAChangedAddressIsReportedAsDrift(t *testing.T) {
	if Drift("192.168.65.254", Address{IPv4: "192.168.65.254"}) {
		t.Error("the same address is not drift")
	}
	if !Drift("192.168.65.254", Address{IPv4: "192.168.65.1"}) || !Drift("", Address{IPv4: "192.168.65.254"}) {
		t.Error("a different or missing address is drift")
	}
}
