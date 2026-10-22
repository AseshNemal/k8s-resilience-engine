package bookinfotrace

import "fmt"

// Gate is a checkpoint in the staged setup. Each one measures before the next stage is allowed.
type Gate int

const (
	Gate0 Gate = iota // before anything: is there room, and is the baseline known
	Gate1             // after istiod only
	Gate2             // after the sidecars and the route
)

// Measurements are real readings. A nil value means "could not be measured", which is never treated as fine.
type Measurements struct {
	VMLimitMiB    *float64 `json:"vmLimitMiB"`    // the Docker Desktop VM's memory
	VMUsedMiB     *float64 `json:"vmUsedMiB"`     // the sum of every container's memory, as `docker stats` reports it
	SwapUsedMiB   *float64 `json:"swapUsedMiB"`   // this computer's swap in use (the Mac is already swapping)
	IstiodMiB     *float64 `json:"istiodMiB"`     // istiod's working set
	SidecarMaxMiB *float64 `json:"sidecarMaxMiB"` // the largest sidecar's working set
	PodsReady     *bool    `json:"podsReady"`
}

// Thresholds are configurable defaults: the limits at which the setup stops. They are the ones in
// k8s-bookinfo/tracing/README.md.
type Thresholds struct {
	IstiodMaxMiB    float64 // Gate 1
	VMAfterIstiod   float64 // Gate 1: 2.6 GiB
	VMAfterSidecars float64 // Gate 2: 3.2 GiB
	SwapGrowthMiB   float64 // Gates 1 and 2: how much more the computer may swap than at the baseline
	SidecarCapMiB   float64 // Gate 2: every sidecar must be under its cap
}

func DefaultThresholds() Thresholds {
	return Thresholds{IstiodMaxMiB: 300, VMAfterIstiod: 2.6 * 1024, VMAfterSidecars: 3.2 * 1024, SwapGrowthMiB: 1024, SidecarCapMiB: float64(Contract.MemoryMiB.SidecarLimit)}
}

// Decision says whether to go on. Reasons are always filled when Proceed is false, and say which reading failed.
type Decision struct {
	Proceed bool     `json:"proceed"`
	Reasons []string `json:"reasons,omitempty"`
}

func (d *Decision) fail(format string, args ...any) {
	d.Reasons = append(d.Reasons, fmt.Sprintf(format, args...))
}

// need records a reason when a reading is missing. It reports whether the value is usable.
func need(d *Decision, name string, v *float64) bool {
	if v == nil {
		d.fail("%s could not be measured, so the setup does not continue", name)
		return false
	}
	return true
}

// Evaluate decides one gate from the baseline (taken at Gate 0) and the readings now.
func Evaluate(g Gate, baseline, now Measurements, t Thresholds) Decision {
	var d Decision
	switch g {
	case Gate0:
		if need(&d, "the Docker memory limit", now.VMLimitMiB) && need(&d, "the Docker memory in use", now.VMUsedMiB) {
			headroom := *now.VMLimitMiB - *now.VMUsedMiB
			if worst := float64(Contract.WorstCaseMiB()); headroom < worst {
				d.fail("only %.0f MiB of Docker memory is free, but the setup can use up to %.0f MiB at its caps", headroom, worst)
			}
		}
		need(&d, "this computer's swap in use", now.SwapUsedMiB)
	case Gate1:
		if need(&d, "istiod's memory", now.IstiodMiB) && *now.IstiodMiB >= t.IstiodMaxMiB {
			d.fail("istiod uses %.0f MiB, which is not under %.0f MiB", *now.IstiodMiB, t.IstiodMaxMiB)
		}
		if need(&d, "the Docker memory in use", now.VMUsedMiB) && *now.VMUsedMiB >= t.VMAfterIstiod {
			d.fail("Docker uses %.0f MiB, which is not under %.0f MiB", *now.VMUsedMiB, t.VMAfterIstiod)
		}
		d.swapGrowth(baseline, now, t)
	case Gate2:
		if need(&d, "the Docker memory in use", now.VMUsedMiB) && *now.VMUsedMiB >= t.VMAfterSidecars {
			d.fail("Docker uses %.0f MiB, which is not under %.0f MiB", *now.VMUsedMiB, t.VMAfterSidecars)
		}
		if need(&d, "the sidecars' memory", now.SidecarMaxMiB) && *now.SidecarMaxMiB >= t.SidecarCapMiB {
			d.fail("a sidecar uses %.0f MiB, which is not under its %.0f MiB cap", *now.SidecarMaxMiB, t.SidecarCapMiB)
		}
		if now.PodsReady == nil {
			d.fail("whether every Pod is Ready could not be read, so the setup does not continue")
		} else if !*now.PodsReady {
			d.fail("not every Pod is Ready")
		}
		d.swapGrowth(baseline, now, t)
	default:
		d.fail("unknown gate")
	}
	d.Proceed = len(d.Reasons) == 0
	return d
}

func (d *Decision) swapGrowth(baseline, now Measurements, t Thresholds) {
	if baseline.SwapUsedMiB == nil {
		d.fail("the baseline swap was not recorded, so growth cannot be judged")
		return
	}
	if !need(d, "this computer's swap in use", now.SwapUsedMiB) {
		return
	}
	if growth := *now.SwapUsedMiB - *baseline.SwapUsedMiB; growth >= t.SwapGrowthMiB {
		d.fail("this computer has swapped %.0f MiB more since the baseline, which is not under %.0f MiB", growth, t.SwapGrowthMiB)
	}
}
