package bookinfotrace

import (
	"context"
	"math"
	"testing"
)

func near(a, b float64) bool { return math.Abs(a-b) < 0.01 }

func TestSizesAreReadInMiBAndNothingElseIsAccepted(t *testing.T) {
	good := map[string]float64{"910.2MiB": 910.2, "3.825GiB": 3916.8, "256MiB": 256, "1KiB": 1.0 / 1024, "0B": 0, "1MB": 1e6 / 1048576}
	for text, want := range good {
		got, err := parseSizeMiB(text)
		if err != nil || !near(got, want) {
			t.Errorf("%q = %v %v, want %v", text, got, err, want)
		}
	}
	for _, text := range []string{"", "MiB", "12", "12 parsecs", "-5MiB", "1e3MiB", "abc"} {
		if _, err := parseSizeMiB(text); err == nil {
			t.Errorf("%q must be refused", text)
		}
	}
}

func TestDockerStatsAreSummedFromTheUsedHalfOnly(t *testing.T) {
	got, err := sumUsedMiB("910.2MiB / 3.825GiB\n384.1MiB / 3.825GiB\n41.43MiB / 256MiB\n")
	if err != nil || !near(got, 910.2+384.1+41.43) {
		t.Fatalf("%v %v", got, err)
	}
	for _, text := range []string{"", "\n", "garbage", "12MiB", "x / 3GiB"} {
		if _, err := sumUsedMiB(text); err == nil {
			t.Errorf("%q must be an error, not zero", text)
		}
	}
}

func TestSwapIsReadOnDarwinAndLinuxAndUnknownElsewhere(t *testing.T) {
	if v, err := parseSwapDarwin("total = 4096.00M  used = 2688.88M  free = 1407.12M  (encrypted)"); err != nil || !near(v, 2688.88) {
		t.Fatalf("%v %v", v, err)
	}
	if v, _ := parseSwapDarwin("total = 4.00G  used = 1.50G  free = 2.50G"); !near(v, 1536) {
		t.Fatalf("gigabytes: %v", v)
	}
	if _, err := parseSwapDarwin("nothing useful"); err == nil {
		t.Fatal("unreadable swap must be an error")
	}
	if v, err := parseSwapLinux("MemTotal: 1 kB\nSwapTotal:  4194304 kB\nSwapFree:   3145728 kB\n"); err != nil || !near(v, 1024) {
		t.Fatalf("%v %v", v, err)
	}
	if _, err := parseSwapLinux("MemTotal: 1 kB\n"); err == nil {
		t.Fatal("no swap lines must be an error")
	}

	r := healthy()
	if v := HostSwapUsedMiB(context.Background(), r.deps()); v == nil || !near(*v, 2688.88) {
		t.Fatalf("darwin: %v", v)
	}
	d := r.deps()
	d.GOOS = "windows"
	if HostSwapUsedMiB(context.Background(), d) != nil {
		t.Fatal("an unsupported system is unknown, not zero")
	}
	d.GOOS = "linux"
	d.ReadFile = func(string) ([]byte, error) { return []byte("SwapTotal: 2048 kB\nSwapFree: 1024 kB\n"), nil }
	if v := HostSwapUsedMiB(context.Background(), d); v == nil || !near(*v, 1.0) {
		t.Fatalf("linux: %v", v)
	}
	d.ReadFile = nil
	if HostSwapUsedMiB(context.Background(), d) != nil {
		t.Fatal("linux without a way to read the file is unknown")
	}
	broken := healthy()
	broken.sysctl = ""
	if HostSwapUsedMiB(context.Background(), broken.deps()) != nil {
		t.Fatal("a failed sysctl is unknown, not zero")
	}
}

func TestDockerMemoryReadsTheLimitAndTheUse(t *testing.T) {
	limit, used := DockerMemory(context.Background(), healthy().deps())
	if limit == nil || used == nil || !near(*limit, 4107005952.0/1048576) || !near(*used, 910.2+384.1+569.4+41.43) {
		t.Fatalf("%v %v", limit, used)
	}
	none := healthy()
	none.info, none.stats = "", ""
	if l, u := DockerMemory(context.Background(), none.deps()); l != nil || u != nil {
		t.Fatalf("unreadable output is unknown: %v %v", l, u)
	}
	d := healthy().deps()
	d.FindDocker = func() (string, error) { return "", context.Canceled }
	if l, u := DockerMemory(context.Background(), d); l != nil || u != nil {
		t.Fatal("no Docker is unknown")
	}
}

const crictlSample = `{"stats":[
 {"attributes":{"metadata":{"name":"discovery"}},"memory":{"workingSetBytes":{"value":"157286400"}}},
 {"attributes":{"metadata":{"name":"istio-proxy"}},"memory":{"workingSetBytes":{"value":"52428800"}}},
 {"attributes":{"metadata":{"name":"istio-proxy"}},"memory":{"workingSetBytes":{"value":"73400320"}}},
 {"attributes":{"metadata":{"name":"reviews"}},"memory":{"workingSetBytes":{"value":"120324096"}}}]}`

func TestContainerMemoryIsTheLargestWorkingSetWithThatName(t *testing.T) {
	r := healthy()
	r.crictl = crictlSample
	m := Measure(context.Background(), r.deps())
	if m.IstiodMiB == nil || !near(*m.IstiodMiB, 150) {
		t.Fatalf("istiod: %v", m.IstiodMiB)
	}
	if m.SidecarMaxMiB == nil || !near(*m.SidecarMaxMiB, 70) {
		t.Fatalf("largest sidecar: %v", m.SidecarMaxMiB)
	}
	if m.VMLimitMiB == nil || m.VMUsedMiB == nil || m.SwapUsedMiB == nil {
		t.Fatalf("the rest must be measured too: %+v", m)
	}
}

func TestNoIstiodYetIsUnknownNotZero(t *testing.T) {
	r := healthy()
	r.crictl = `{"stats":[{"attributes":{"metadata":{"name":"reviews"}},"memory":{"workingSetBytes":{"value":"1"}}}]}`
	m := Measure(context.Background(), r.deps())
	if m.IstiodMiB != nil || m.SidecarMaxMiB != nil {
		t.Fatalf("a container that does not exist has no memory figure: %+v", m)
	}
	r.crictl = "not json"
	if m := Measure(context.Background(), r.deps()); m.IstiodMiB != nil {
		t.Fatal("unreadable output is unknown")
	}
}
