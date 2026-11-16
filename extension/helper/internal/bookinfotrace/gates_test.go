package bookinfotrace

import (
	"strings"
	"testing"
)

func proceeds(d Decision) bool { return d.Proceed && len(d.Reasons) == 0 }

func TestGateZeroPassesOnTheMeasuredBaseline(t *testing.T) {
	if d := Evaluate(Gate0, Measurements{}, roomy(), DefaultThresholds()); !proceeds(d) {
		t.Fatalf("the real machine has room: %+v", d)
	}
}

func TestGateZeroStopsWhenThereIsNotRoomForTheWorstCase(t *testing.T) {
	tight := Measurements{VMLimitMiB: f(3917), VMUsedMiB: f(3000), SwapUsedMiB: f(100)}
	d := Evaluate(Gate0, Measurements{}, tight, DefaultThresholds())
	if d.Proceed || !strings.Contains(strings.Join(d.Reasons, " "), "free") {
		t.Fatalf("%+v", d)
	}
}

func TestAMeasurementThatCouldNotBeTakenIsNeverTreatedAsFine(t *testing.T) {
	cases := map[string]Measurements{
		"no memory limit": {VMUsedMiB: f(2000), SwapUsedMiB: f(1)},
		"no memory use":   {VMLimitMiB: f(3917), SwapUsedMiB: f(1)},
		"no swap":         {VMLimitMiB: f(3917), VMUsedMiB: f(2000)},
		"nothing at all":  {},
	}
	for name, m := range cases {
		if d := Evaluate(Gate0, Measurements{}, m, DefaultThresholds()); d.Proceed || !strings.Contains(strings.Join(d.Reasons, " "), "could not be measured") {
			t.Errorf("%s: %+v", name, d)
		}
	}
}

func TestGateOneChecksIstiodDockerAndSwapGrowth(t *testing.T) {
	base := roomy()
	good := Measurements{IstiodMiB: f(150), VMUsedMiB: f(2300), SwapUsedMiB: f(2900)}
	if d := Evaluate(Gate1, base, good, DefaultThresholds()); !proceeds(d) {
		t.Fatalf("%+v", d)
	}
	bad := map[string]Measurements{
		"istiod too big":     {IstiodMiB: f(300), VMUsedMiB: f(2300), SwapUsedMiB: f(2900)},
		"docker too full":    {IstiodMiB: f(150), VMUsedMiB: f(2700), SwapUsedMiB: f(2900)},
		"swap grew by a GiB": {IstiodMiB: f(150), VMUsedMiB: f(2300), SwapUsedMiB: f(2689 + 1024)},
		"istiod unreadable":  {VMUsedMiB: f(2300), SwapUsedMiB: f(2900)},
		"swap unreadable":    {IstiodMiB: f(150), VMUsedMiB: f(2300)},
	}
	for name, m := range bad {
		if d := Evaluate(Gate1, base, m, DefaultThresholds()); d.Proceed {
			t.Errorf("%s must stop: %+v", name, d)
		}
	}
	if d := Evaluate(Gate1, Measurements{}, good, DefaultThresholds()); d.Proceed {
		t.Error("without a recorded baseline swap, growth cannot be judged")
	}
}

func TestBoundariesAreStrict(t *testing.T) {
	base := roomy()
	exactly := Measurements{IstiodMiB: f(300), VMUsedMiB: f(2300), SwapUsedMiB: f(2689)}
	if d := Evaluate(Gate1, base, exactly, DefaultThresholds()); d.Proceed {
		t.Error("istiod at exactly 300 MiB is not under 300 MiB")
	}
}

func TestGateTwoChecksDockerSidecarsAndReadiness(t *testing.T) {
	base := roomy()
	good := Measurements{VMUsedMiB: f(3000), SidecarMaxMiB: f(70), SwapUsedMiB: f(3100), PodsReady: b(true)}
	if d := Evaluate(Gate2, base, good, DefaultThresholds()); !proceeds(d) {
		t.Fatalf("%+v", d)
	}
	bad := map[string]Measurements{
		"docker over 3.2 GiB":  {VMUsedMiB: f(3300), SidecarMaxMiB: f(70), SwapUsedMiB: f(3100), PodsReady: b(true)},
		"a sidecar at its cap": {VMUsedMiB: f(3000), SidecarMaxMiB: f(128), SwapUsedMiB: f(3100), PodsReady: b(true)},
		"a Pod not Ready":      {VMUsedMiB: f(3000), SidecarMaxMiB: f(70), SwapUsedMiB: f(3100), PodsReady: b(false)},
		"readiness unknown":    {VMUsedMiB: f(3000), SidecarMaxMiB: f(70), SwapUsedMiB: f(3100)},
		"no sidecar reading":   {VMUsedMiB: f(3000), SwapUsedMiB: f(3100), PodsReady: b(true)},
		"swap grew a lot":      {VMUsedMiB: f(3000), SidecarMaxMiB: f(70), SwapUsedMiB: f(4000), PodsReady: b(true)},
	}
	for name, m := range bad {
		if d := Evaluate(Gate2, base, m, DefaultThresholds()); d.Proceed {
			t.Errorf("%s must stop: %+v", name, d)
		}
	}
}

func TestAnUnknownGateNeverProceeds(t *testing.T) {
	if d := Evaluate(Gate(99), roomy(), roomy(), DefaultThresholds()); d.Proceed {
		t.Fatal("an unknown gate must not pass")
	}
}

func TestEveryStopSaysWhy(t *testing.T) {
	d := Evaluate(Gate2, roomy(), Measurements{}, DefaultThresholds())
	if d.Proceed || len(d.Reasons) < 3 {
		t.Fatalf("%+v", d)
	}
}
