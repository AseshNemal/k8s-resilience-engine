package traceinstall

import (
	"context"
	"fmt"
	"net/http"
	"sort"
	"strings"
	"time"

	"resilience-engine/helper/internal/kube"
)

// ---- a clock that makes minutes of waiting instant, and lets the cluster change as time passes ----

type pending struct {
	at time.Time
	fn func()
}

type fakeClock struct {
	now     time.Time
	queue   []pending
	sleeps  int
	onSleep func(n int) // called before each sleep; a test can cancel a context here
}

func (c *fakeClock) Now() time.Time { return c.now }
func (c *fakeClock) after(d time.Duration, fn func()) {
	c.queue = append(c.queue, pending{at: c.now.Add(d), fn: fn})
}
func (c *fakeClock) Sleep(ctx context.Context, d time.Duration) error {
	c.sleeps++
	if c.onSleep != nil {
		c.onSleep(c.sleeps)
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	c.now = c.now.Add(d)
	c.runDue()
	return ctx.Err()
}
func (c *fakeClock) runDue() {
	for {
		ran := false
		for i, p := range c.queue {
			if !p.at.After(c.now) {
				c.queue = append(c.queue[:i:i], c.queue[i+1:]...)
				p.fn()
				ran = true
				break
			}
		}
		if !ran {
			return
		}
	}
}

// ---- the cluster ----

type simObject struct{ obj kube.Object }

// sim is an in-memory cluster that behaves like the parts of Kubernetes and Istio this setup depends on:
// Deployments recreate deleted Pods, the injection webhook adds a sidecar only while the namespace is labelled, and it
// fails closed (no Pod can be created) when the namespace is labelled and Istio is gone.
type sim struct {
	clock *fakeClock
	objs  map[string]*simObject
	uidN  int
	podN  int

	version      string
	istiodDelay  time.Duration
	podDelay     time.Duration
	crdLag       int  // custom resource creates answer NotFound this many times first
	injectBroken bool // the webhook silently injects nothing

	writes     []string
	writeCount int
	failWrite  map[int]error    // the nth write (1-based) fails once with this error, before taking effect
	failAlways map[string]error // "delete:Telemetry/trace-demo" ... fail every time
	killBefore int              // panic at the nth write before it takes effect
	killAfter  int              // panic at the nth write after it took effect
	failList   map[string]error // "list:configmaps" ...
	stuckPods  int              // Pods the Deployment could not create (webhook down)
	baseKeys   map[string]bool
	clientDone time.Duration // how long the temporary client Pod runs
}

func key(plural, ns, name string) string { return plural + "/" + ns + "/" + name }

func newSim(clock *fakeClock) *sim {
	s := &sim{clock: clock, objs: map[string]*simObject{}, version: "v1.36.1", istiodDelay: 20 * time.Second, podDelay: 12 * time.Second,
		failWrite: map[int]error{}, failAlways: map[string]error{}, failList: map[string]error{}, clientDone: 70 * time.Second}
	s.put(kube.Namespaces, kube.Object{Name: "kube-system", UID: "uid-kube-system"})
	s.put(kube.Namespaces, kube.Object{Name: "bookinfo", Labels: map[string]string{"kubernetes.io/metadata.name": "bookinfo"}})
	one := 1
	for _, app := range []string{"productpage", "details", "ratings", "reviews"} {
		s.put(kube.Deployments, kube.Object{Name: app + "-v1", Namespace: "bookinfo", Replicas: &one, ReadyReplicas: &one,
			Containers: []string{app}, SpecDigest: "digest-" + app})
		s.newPod(app, "v1")
	}
	s.baseKeys = map[string]bool{}
	for k := range s.objs {
		s.baseKeys[k] = true
	}
	return s
}

func (s *sim) put(r kube.Resource, o kube.Object) {
	if !r.Namespaced {
		o.Namespace = ""
	}
	if o.UID == "" {
		s.uidN++
		o.UID = fmt.Sprintf("uid-%d", s.uidN)
	}
	s.objs[key(r.Plural, o.Namespace, o.Name)] = &simObject{obj: o}
}

func (s *sim) get(r kube.Resource, ns, name string) (*simObject, bool) {
	if !r.Namespaced {
		ns = ""
	}
	o, ok := s.objs[key(r.Plural, ns, name)]
	return o, ok
}

func (s *sim) injected() bool {
	ns, _ := s.get(kube.Namespaces, "", "bookinfo")
	return ns != nil && ns.obj.Labels["istio-injection"] == "enabled"
}

func (s *sim) istioUp() bool { _, ok := s.get(kube.Deployments, "istio-system", "istiod"); return ok }

// newPod creates a Pod the way a Deployment's controller would, subject to the injection webhook.
func (s *sim) newPod(app, version string) bool {
	containers := []string{app}
	if s.injected() {
		if !s.istioUp() {
			s.stuckPods++ // failurePolicy Fail: the Pod cannot be created
			return false
		}
		if !s.injectBroken {
			containers = append(containers, "istio-proxy")
		}
	}
	s.podN++
	ready := true
	s.put(kube.Pods, kube.Object{Name: fmt.Sprintf("%s-%s-p%d", app, version, s.podN), Namespace: "bookinfo",
		Labels: map[string]string{"app": app, "version": version}, Containers: containers, Ready: &ready, Phase: "Running"})
	return true
}

func (s *sim) podsOwnedBy(app, version string) []string {
	var out []string
	for k, o := range s.objs {
		if strings.HasPrefix(k, "pods/bookinfo/") && o.obj.Labels["app"] == app && o.obj.Labels["version"] == version {
			out = append(out, k)
		}
	}
	return out
}

// ---- kube.Reader ----

func (s *sim) ServerVersion(context.Context) (string, error) { return s.version, nil }

func (s *sim) Get(_ context.Context, r kube.Resource, ns, name string) (*kube.Object, error) {
	o, ok := s.get(r, ns, name)
	if !ok {
		return nil, kube.NewAPIError(http.StatusNotFound, "NotFound", "not found")
	}
	c := o.obj
	return &c, nil
}

func (s *sim) List(_ context.Context, r kube.Resource, ns string) ([]kube.Object, error) {
	if err := s.failList["list:"+r.Plural]; err != nil {
		return nil, err
	}
	var out []kube.Object
	for k, o := range s.objs {
		if strings.HasPrefix(k, r.Plural+"/") && (ns == "" || o.obj.Namespace == ns) {
			out = append(out, o.obj)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Namespace+out[i].Name < out[j].Namespace+out[j].Name })
	return out, nil
}

// ---- kube.RawWriter ----

// begin counts a write, applies the injected failures and the simulated process deaths.
func (s *sim) begin(verb, what string) (error, func()) {
	s.writeCount++
	s.writes = append(s.writes, verb+" "+what)
	if err := s.failAlways[verb+":"+what]; err != nil {
		return err, nil
	}
	if err, ok := s.failWrite[s.writeCount]; ok {
		delete(s.failWrite, s.writeCount)
		return err, nil
	}
	if s.killBefore == s.writeCount {
		panic("process killed before write " + fmt.Sprint(s.writeCount))
	}
	var after func()
	if s.killAfter == s.writeCount {
		after = func() { panic("process killed after write " + fmt.Sprint(s.writeCount)) }
	}
	return nil, after
}

func metaOf(body map[string]any) (name string, labels map[string]string) {
	meta, _ := body["metadata"].(map[string]any)
	name, _ = meta["name"].(string)
	labels = map[string]string{}
	if l, ok := meta["labels"].(map[string]any); ok {
		for k, v := range l {
			labels[k], _ = v.(string)
		}
	}
	return
}

func (s *sim) CreateRaw(_ context.Context, r kube.Resource, ns string, body map[string]any) (string, error) {
	name, labels := metaOf(body)
	err, after := s.begin("create", r.Kind+" "+nsName(r, ns, name))
	if err != nil {
		return "", err
	}
	if _, taken := s.get(r, ns, name); taken {
		return "", kube.NewAPIError(http.StatusConflict, "AlreadyExists", "already exists")
	}
	if r.Group == "networking.istio.io" || r.Group == "telemetry.istio.io" {
		if s.crdLag > 0 {
			s.crdLag--
			return "", kube.NewAPIError(http.StatusNotFound, "NotFound", "no matches for kind")
		}
	}
	if !r.Namespaced {
		ns = ""
	}
	obj := kube.Object{Name: name, Namespace: ns, Labels: labels}
	if raw, e := kube.ObjectFromRaw(withUID(body, s)); e == nil {
		obj = *raw
		obj.Namespace = ns
		obj.Labels = labels
	}
	s.objs[key(r.Plural, ns, name)] = &simObject{obj: obj}
	s.onCreate(r, ns, name)
	if after != nil {
		after()
	}
	return obj.UID, nil
}

func withUID(body map[string]any, s *sim) map[string]any {
	s.uidN++
	out := map[string]any{}
	for k, v := range body {
		out[k] = v
	}
	meta := map[string]any{}
	for k, v := range body["metadata"].(map[string]any) {
		meta[k] = v
	}
	meta["uid"] = fmt.Sprintf("uid-%d", s.uidN)
	out["metadata"] = meta
	return out
}

func nsName(r kube.Resource, ns, name string) string {
	if r.Namespaced {
		return ns + "/" + name
	}
	return name
}

func (s *sim) onCreate(r kube.Resource, ns, name string) {
	switch {
	case r.Plural == "deployments" && ns == "istio-system" && name == "istiod":
		one, zero := 1, 0
		s.objs[key(r.Plural, ns, name)].obj.Replicas, s.objs[key(r.Plural, ns, name)].obj.ReadyReplicas = &one, &zero
		s.clock.after(s.istiodDelay, func() {
			if o, ok := s.get(r, ns, name); ok {
				ready := 1
				o.obj.ReadyReplicas = &ready
			}
		})
	case r.Plural == "deployments" && ns == "bookinfo" && name == "reviews-v2":
		one, zero := 1, 0
		o := s.objs[key(r.Plural, ns, name)]
		o.obj.Replicas, o.obj.ReadyReplicas = &one, &zero
		s.clock.after(s.podDelay, func() {
			if _, still := s.get(r, ns, name); still && s.newPod("reviews", "v2") {
				ready := 1
				if dep, ok := s.get(r, ns, name); ok {
					dep.obj.ReadyReplicas = &ready
				}
			}
		})
	case r.Plural == "pods" && name == "trace-demo-client":
		ready := false
		o := s.objs[key(r.Plural, ns, name)]
		o.obj.Phase, o.obj.Ready, o.obj.Containers = "Running", &ready, []string{"curl", "istio-proxy"}
		s.clock.after(s.clientDone, func() {
			if p, ok := s.get(kube.Pods, ns, name); ok {
				p.obj.Phase = "Succeeded"
			}
		})
	}
}

func (s *sim) DeleteRaw(_ context.Context, r kube.Resource, ns, name, uid string) error {
	err, after := s.begin("delete", r.Kind+" "+nsName(r, ns, name))
	if err != nil {
		return err
	}
	o, ok := s.get(r, ns, name)
	if !ok {
		return kube.NewAPIError(http.StatusNotFound, "NotFound", "not found")
	}
	if uid != "" && o.obj.UID != uid {
		return kube.NewAPIError(http.StatusConflict, "Conflict", "Precondition failed")
	}
	if !r.Namespaced {
		ns = ""
	}
	delete(s.objs, key(r.Plural, ns, name))
	switch {
	case r.Plural == "pods" && ns == "bookinfo" && name != "trace-demo-client":
		app, version := o.obj.Labels["app"], o.obj.Labels["version"]
		if _, owner := s.get(kube.Deployments, ns, app+"-"+version); owner {
			s.clock.after(s.podDelay, func() { s.newPod(app, version) })
		}
	case r.Plural == "deployments":
		for _, k := range s.podsOwnedBy(o.obj.Labels["app"], "v2") {
			if name == "reviews-v2" {
				delete(s.objs, k)
			}
		}
		if ns == "istio-system" && name == "istiod" {
			for k, p := range s.objs {
				if strings.HasPrefix(k, "pods/istio-system/") && p.obj.Labels["app"] == "istiod" {
					delete(s.objs, k)
				}
			}
		}
	case r.Plural == "namespaces":
		for k, x := range s.objs {
			if x.obj.Namespace == name {
				delete(s.objs, k)
			}
		}
	}
	if after != nil {
		after()
	}
	return nil
}

func (s *sim) PatchLabels(_ context.Context, r kube.Resource, ns, name string, set map[string]*string) error {
	err, after := s.begin("patch", r.Plural+" "+name)
	if err != nil {
		return err
	}
	o, ok := s.get(r, ns, name)
	if !ok {
		return kube.NewAPIError(http.StatusNotFound, "NotFound", "not found")
	}
	labels := map[string]string{}
	for k, v := range o.obj.Labels {
		labels[k] = v
	}
	for k, v := range set {
		if v == nil {
			delete(labels, k)
		} else {
			labels[k] = *v
		}
	}
	o.obj.Labels = labels
	if after != nil {
		after()
	}
	return nil
}

// leftovers lists everything in the cluster that was not there at the start.
func (s *sim) leftovers() []string {
	var out []string
	for k := range s.objs {
		if !s.baseKeys[k] && !strings.HasPrefix(k, "pods/bookinfo/") { // Pods are compared by their containers, not their names
			out = append(out, k)
		}
	}
	sort.Strings(out)
	return out
}

func (s *sim) writesMatching(prefix string) []int {
	var at []int
	for i, w := range s.writes {
		if strings.HasPrefix(w, prefix) {
			at = append(at, i)
		}
	}
	return at
}
