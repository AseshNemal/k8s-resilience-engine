package traceinstall

import (
	"context"
	"errors"
	"fmt"
	"net"
	"strings"
	"time"

	"resilience-engine/helper/internal/bookinfotrace"
	"resilience-engine/helper/internal/kube"
)

// stepError is a failure with wording that is safe to show: it names what the setup was doing, never text a cluster or
// a program returned.
type stepError struct{ reason string }

func (e *stepError) Error() string { return e.reason }

func failed(format string, args ...any) error {
	return &stepError{reason: fmt.Sprintf(format, args...)}
}

// describe turns an error into safe wording for "what was being done".
func describe(doing string, err error) error {
	var step *stepError
	switch {
	case errors.As(err, &step):
		return step
	case errors.Is(err, context.Canceled):
		return failed("%s was interrupted", doing)
	case errors.Is(err, context.DeadlineExceeded):
		return failed("%s ran out of time", doing)
	case errors.Is(err, ErrNotAllowed):
		return failed("%s was refused by the setup's own allow-list", doing)
	case kube.IsForbidden(err), kube.IsUnauthorized(err):
		return failed("%s was refused: your kubeconfig is not allowed to do it", doing)
	case kube.IsAlreadyExists(err):
		return failed("%s found that the name is already taken by something this setup did not create", doing)
	case kube.IsConflict(err):
		return failed("%s found the object changed by someone else", doing)
	case kube.IsNotFound(err):
		return failed("%s could not find what it expected in the cluster", doing)
	}
	return failed("%s failed", doing)
}

// run is the state of one Apply or Undo: the record being kept, the writer, and the memory baseline.
type run struct {
	d            Deps
	rec          *Record
	guard        *Guard
	baseline     bookinfotrace.Measurements
	lastMeasured time.Time
	unreadable   int
	currentStage string
}

func newRun(d Deps, rec *Record) *run {
	return &run{d: d, rec: rec, guard: NewGuard(d.Cluster, rec.InstallationID, rec.Inventory, rec.PlannedPods), baseline: rec.Baseline}
}

// save writes the record. A record that cannot be saved stops the setup: nothing more may be created that could not
// be found again.
func (r *run) save() error {
	if err := r.d.Store.Save(r.rec, r.d.clock().Now()); err != nil {
		return failed("the setup record could not be saved, so the setup stops")
	}
	return nil
}

func (r *run) log(step, status, detail string) {
	r.rec.Log(step, status, detail, r.d.clock().Now())
	_ = r.save() // a journal line is progress for the page; a record that cannot be saved is caught by the next real save
}

// ---- creating ----------------------------------------------------------------------------------------

var customResourceGroups = map[string]bool{"networking.istio.io": true, "telemetry.istio.io": true}

// create adds one object of the inventory. The intent is written before the request and the UID after it, so a crash
// between the two leaves an "intended" entry that Undo resolves by looking at the cluster.
func (r *run) create(ctx context.Context, body map[string]any) error {
	ref := refOf(body)
	e := r.rec.Entry(ref)
	if e == nil {
		return failed("%s is not in the setup's inventory", ref)
	}
	e.State = EntryIntent
	if err := r.save(); err != nil {
		return err
	}
	stamped := Stamp(body, r.rec.InstallationID)
	attempts := 1
	if group := strings.SplitN(ref.APIVersion, "/", 2)[0]; customResourceGroups[group] {
		attempts += r.d.Timeouts.CRRetries // the CRD may not be served yet
	}
	var uid string
	var err error
	for i := 0; i < attempts; i++ {
		_, uid, err = r.guard.Create(ctx, stamped)
		if err == nil || !kube.IsNotFound(err) {
			break
		}
		if sleep := r.d.clock().Sleep(ctx, r.d.Timeouts.CRRetryWait); sleep != nil {
			return describe("creating "+ref.String(), sleep)
		}
	}
	switch {
	case err == nil:
		e.UID, e.State = uid, EntryCreated
		return r.save()
	case kube.IsAlreadyExists(err):
		e.State = EntryForeign // never ours: it is left alone by Undo
		_ = r.save()
	}
	return describe("creating "+ref.String(), err)
}

func hasSidecar(o kube.Object) bool {
	for _, c := range o.Containers {
		if c == "istio-proxy" {
			return true
		}
	}
	return false
}

func isReady(o kube.Object) bool { return o.Ready != nil && *o.Ready }

// ---- waiting and watching memory ------------------------------------------------------------------

// monitor reads memory at most every MonitorEvery while the setup waits. A reading over the limit stops the setup; a
// reading that cannot be taken several times in a row also stops it (never "fine").
func (r *run) monitor(ctx context.Context) error {
	now := r.d.clock().Now()
	if !r.lastMeasured.IsZero() && now.Sub(r.lastMeasured) < r.d.Timeouts.MonitorEvery {
		return nil
	}
	r.lastMeasured = now
	m := r.d.measure(ctx)
	t := r.d.Thresholds
	if m.VMUsedMiB != nil && *m.VMUsedMiB >= t.VMAfterSidecars {
		return failed("Docker memory use reached %.0f MiB while waiting, which is not under %.0f MiB", *m.VMUsedMiB, t.VMAfterSidecars)
	}
	if m.SwapUsedMiB != nil && r.baseline.SwapUsedMiB != nil && *m.SwapUsedMiB-*r.baseline.SwapUsedMiB >= t.SwapGrowthMiB {
		return failed("this computer swapped %.0f MiB more while waiting, which is not under %.0f MiB", *m.SwapUsedMiB-*r.baseline.SwapUsedMiB, t.SwapGrowthMiB)
	}
	if m.VMUsedMiB == nil && m.SwapUsedMiB == nil {
		r.unreadable++
		if r.unreadable >= 3 {
			return failed("memory could not be read three times in a row while waiting, so the setup stops")
		}
		return nil
	}
	r.unreadable = 0
	return nil
}

// waitUntil polls cond until it holds, the timeout passes, or something goes wrong. Memory is watched between polls.
func (r *run) waitUntil(ctx context.Context, what string, timeout time.Duration, cond func(context.Context) (bool, error)) error {
	deadline := r.d.clock().Now().Add(timeout)
	for {
		ok, err := cond(ctx)
		if err != nil {
			return describe("waiting for "+what, err)
		}
		if ok {
			return nil
		}
		if !r.d.clock().Now().Before(deadline) {
			return failed("%s did not become ready in time", what)
		}
		if err := r.d.clock().Sleep(ctx, r.d.Timeouts.Poll); err != nil {
			return describe("waiting for "+what, err)
		}
		if err := r.monitor(ctx); err != nil {
			return err
		}
	}
}

// gate reads memory and every Pod's readiness and applies the staged rule. A failed gate stops the setup.
func (r *run) gate(ctx context.Context, g bookinfotrace.Gate, name string) error {
	m := r.d.measure(ctx)
	if g == bookinfotrace.Gate2 {
		ready, err := r.allPodsReady(ctx)
		if err != nil {
			return describe("checking the Pods", err)
		}
		m.PodsReady = &ready
	}
	decision := bookinfotrace.Evaluate(g, r.baseline, m, r.d.Thresholds)
	r.log(name, map[bool]string{true: "done", false: "failed"}[decision.Proceed], readingText(m))
	if !decision.Proceed {
		return failed("%s: %s", name, strings.Join(decision.Reasons, "; "))
	}
	return nil
}

func readingText(m bookinfotrace.Measurements) string {
	one := func(label string, v *float64) string {
		if v == nil {
			return label + " unknown"
		}
		return fmt.Sprintf("%s %.0f MiB", label, *v)
	}
	return strings.Join([]string{one("docker", m.VMUsedMiB), one("swap", m.SwapUsedMiB), one("istiod", m.IstiodMiB), one("largest sidecar", m.SidecarMaxMiB)}, ", ")
}

func (r *run) allPodsReady(ctx context.Context) (bool, error) {
	pods, err := r.d.Cluster.List(ctx, resPod, bookinfotrace.Contract.Namespace)
	if err != nil {
		return false, err
	}
	for _, p := range pods {
		if !isReady(p) {
			return false, nil
		}
	}
	return len(pods) > 0, nil
}

// ---- reading what the cluster has -------------------------------------------------------------------

func (r *run) podsOf(ctx context.Context, app, version string) ([]kube.Object, error) {
	pods, err := r.d.Cluster.List(ctx, resPod, bookinfotrace.Contract.Namespace)
	if err != nil {
		return nil, err
	}
	var out []kube.Object
	for _, p := range pods {
		if p.Labels["app"] == app && p.Labels["version"] == version {
			out = append(out, p)
		}
	}
	return out, nil
}

func (r *run) deploymentReady(ctx context.Context, namespace, name string) (bool, error) {
	d, err := r.d.Cluster.Get(ctx, resDeployment, namespace, name)
	if err != nil {
		return false, err
	}
	return d.Replicas != nil && d.ReadyReplicas != nil && *d.Replicas > 0 && *d.ReadyReplicas >= *d.Replicas, nil
}

// ---- documents ----------------------------------------------------------------------------------------

func documentOf(file, kind string) (map[string]any, error) {
	docs, err := decodeAll(readAsset(file))
	if err != nil {
		return nil, failed("an embedded manifest could not be read")
	}
	for _, d := range docs {
		if k, _ := d["kind"].(string); k == kind {
			return d, nil
		}
	}
	return nil, failed("an embedded manifest has no %s", kind)
}

// serviceEntryDocument fills the template's address. Only a parsed IPv4 address is substituted.
func serviceEntryDocument(ipv4 string) (map[string]any, error) {
	ip := net.ParseIP(ipv4)
	if ip == nil || ip.To4() == nil {
		return nil, failed("the Jaeger address is not an IPv4 address")
	}
	doc, err := documentOf(fileServiceEntry, "ServiceEntry")
	if err != nil {
		return nil, err
	}
	spec, _ := doc["spec"].(map[string]any)
	endpoints, _ := spec["endpoints"].([]any)
	if len(endpoints) != 1 {
		return nil, failed("the ServiceEntry template is not what was reviewed")
	}
	endpoint, _ := endpoints[0].(map[string]any)
	if endpoint["address"] != addressPlaceholder {
		return nil, failed("the ServiceEntry template is not what was reviewed")
	}
	endpoint["address"] = ip.To4().String()
	return doc, nil
}

func namespaceDocument(name string) map[string]any {
	return map[string]any{"apiVersion": "v1", "kind": "Namespace", "metadata": map[string]any{"name": name}}
}
