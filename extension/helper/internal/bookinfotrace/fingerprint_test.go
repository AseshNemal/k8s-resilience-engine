package bookinfotrace

import "testing"

func TestTheFingerprintIgnoresMemoryReadingsButNotWhatWouldChange(t *testing.T) {
	a := BuildPlan(benchmarkFacts(), okAddress(), nil, roomy(), "docker-desktop")
	moved := roomy()
	moved.VMUsedMiB = f(2111)
	moved.SwapUsedMiB = f(2700)
	b := BuildPlan(benchmarkFacts(), okAddress(), nil, moved, "docker-desktop")
	if Fingerprint(a) != Fingerprint(b) {
		t.Fatal("readings that move all the time must not change the fingerprint")
	}

	otherAddress := okAddress()
	otherAddress.IPv4 = "192.168.65.1"
	if Fingerprint(BuildPlan(benchmarkFacts(), otherAddress, nil, roomy(), "docker-desktop")) == Fingerprint(a) {
		t.Error("a different Jaeger address is a different plan")
	}
	facts := benchmarkFacts()
	facts.Pods[0].Name = "productpage-v1-replacement"
	if Fingerprint(BuildPlan(facts, okAddress(), nil, roomy(), "docker-desktop")) == Fingerprint(a) {
		t.Error("a different Pod to delete is a different plan")
	}
	if Fingerprint(BuildPlan(benchmarkFacts(), okAddress(), nil, roomy(), "kind-other")) == Fingerprint(a) {
		t.Error("a different cluster is a different plan")
	}
	blocked := benchmarkFacts()
	blocked.IstioNamespaceExists = true
	if Fingerprint(BuildPlan(blocked, okAddress(), nil, roomy(), "docker-desktop")) == Fingerprint(a) {
		t.Error("a plan that became blocked is a different plan")
	}
}
