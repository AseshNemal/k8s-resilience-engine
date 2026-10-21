// Package kubetest is an in-memory cluster for tests: it implements kube.Client, remembers every
// action it was asked to perform, and can be told to fail a given action.
//
// It is only imported by tests. It stands in for the API server's storage rules that matter to the
// helper (not found, already exists, objects are copied in and out), not for its validation.
package kubetest

import (
	"context"
	"errors"
	"net/http"
	"sort"
	"sync"
	"time"

	"resilience-engine/helper/internal/kube"
)

// Action is one request the helper made. Resource is the plural name ("clusterroles"), or "version"
// for the server-version request. Verb is get, list, create, update, delete, or token for a token request.
type Action struct {
	Verb      string
	Resource  string
	Namespace string
	Name      string
}

// TokenFunc answers a token request.
type TokenFunc func(namespace, serviceAccount string, expirationSeconds int64) (kube.Token, error)

// Fake is an in-memory cluster.
type Fake struct {
	mu      sync.Mutex
	objects map[string]kube.Object // key: plural/namespace/name
	actions []Action
	fails   []failure
	token   TokenFunc
	Version string
}

type failure struct {
	verb, resource string
	err            error
}

// New returns an empty cluster that reports Kubernetes v1.37.0.
func New() *Fake {
	return &Fake{objects: map[string]kube.Object{}, Version: "v1.37.0"}
}

func key(plural, namespace, name string) string { return plural + "/" + namespace + "/" + name }

func copyObject(o kube.Object) kube.Object {
	c := o
	if o.Labels != nil {
		c.Labels = make(map[string]string, len(o.Labels))
		for k, v := range o.Labels {
			c.Labels[k] = v
		}
	}
	c.Rules = append([]kube.PolicyRule(nil), o.Rules...)
	c.Subjects = append([]kube.Subject(nil), o.Subjects...)
	return c
}

// ---- arranging the cluster ------------------------------------------------------------------------

// Put stores an object directly, without recording an action.
func (f *Fake) Put(r kube.Resource, o kube.Object) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.objects[key(r.Plural, o.Namespace, o.Name)] = copyObject(o)
}

// Fail makes every later request with this verb and resource fail with err, newest rule first.
// A request that fails is still recorded as an action.
func (f *Fake) Fail(verb, resource string, err error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.fails = append([]failure{{verb, resource, err}}, f.fails...)
}

// FailVersion makes the server-version request fail, as an unreachable cluster does.
func (f *Fake) FailVersion(err error) { f.Fail("get", "version", err) }

// IssueTokens sets how token requests are answered.
func (f *Fake) IssueTokens(fn TokenFunc) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.token = fn
}

// GrantTokens answers every token request with this token, expiring grant after now.
func (f *Fake) GrantTokens(value string, now time.Time, grant time.Duration) {
	f.IssueTokens(func(string, string, int64) (kube.Token, error) {
		return kube.Token{Value: value, ExpiresAt: now.Add(grant)}, nil
	})
}

// ---- inspecting it --------------------------------------------------------------------------------

// Actions returns what was requested so far, oldest first.
func (f *Fake) Actions() []Action {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]Action(nil), f.actions...)
}

// ClearActions forgets the recorded actions, to assert on what a later step did on its own.
func (f *Fake) ClearActions() {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.actions = nil
}

// Writes lists every change made to the cluster as "verb resource", and counts token requests
// separately because they issue a credential without changing any object.
func (f *Fake) Writes() (writes []string, tokenRequests int) {
	for _, a := range f.Actions() {
		switch a.Verb {
		case "token":
			tokenRequests++
		case "create", "update", "delete":
			writes = append(writes, a.Verb+" "+a.Resource)
		}
	}
	return writes, tokenRequests
}

// Lookup returns a copy of one stored object.
func (f *Fake) Lookup(r kube.Resource, namespace, name string) (kube.Object, bool) {
	f.mu.Lock()
	defer f.mu.Unlock()
	o, ok := f.objects[key(r.Plural, namespace, name)]
	return copyObject(o), ok
}

// Count is how many objects of a kind are stored.
func (f *Fake) Count(r kube.Resource) int {
	f.mu.Lock()
	defer f.mu.Unlock()
	n := 0
	for k := range f.objects {
		if len(k) > len(r.Plural) && k[:len(r.Plural)+1] == r.Plural+"/" {
			n++
		}
	}
	return n
}

// ---- kube.Client -----------------------------------------------------------------------------------

// begin records the action and returns the error it was told to fail with, if any.
func (f *Fake) begin(verb, resource, namespace, name string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.actions = append(f.actions, Action{verb, resource, namespace, name})
	for _, fail := range f.fails {
		if fail.verb == verb && fail.resource == resource {
			return fail.err
		}
	}
	return nil
}

func notFound(r kube.Resource, name string) error {
	return kube.NewAPIError(http.StatusNotFound, "NotFound", r.Plural+` "`+name+`" not found`)
}

// ServerVersion implements kube.Client.
func (f *Fake) ServerVersion(context.Context) (string, error) {
	if err := f.begin("get", "version", "", ""); err != nil {
		return "", err
	}
	return f.Version, nil
}

// Get implements kube.Client.
func (f *Fake) Get(_ context.Context, r kube.Resource, namespace, name string) (*kube.Object, error) {
	if !r.Namespaced {
		namespace = ""
	}
	if err := f.begin("get", r.Plural, namespace, name); err != nil {
		return nil, err
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	o, ok := f.objects[key(r.Plural, namespace, name)]
	if !ok {
		return nil, notFound(r, name)
	}
	c := copyObject(o)
	return &c, nil
}

// List implements kube.Client.
func (f *Fake) List(_ context.Context, r kube.Resource, namespace string) ([]kube.Object, error) {
	if err := f.begin("list", r.Plural, namespace, ""); err != nil {
		return nil, err
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	var out []kube.Object
	for k, o := range f.objects {
		if len(k) > len(r.Plural) && k[:len(r.Plural)+1] == r.Plural+"/" && (namespace == "" || o.Namespace == namespace) {
			out = append(out, copyObject(o))
		}
	}
	sort.Slice(out, func(i, j int) bool {
		return out[i].Namespace+"/"+out[i].Name < out[j].Namespace+"/"+out[j].Name
	})
	return out, nil
}

// Create implements kube.Client.
func (f *Fake) Create(_ context.Context, r kube.Resource, o *kube.Object) error {
	namespace := o.Namespace
	if !r.Namespaced {
		namespace = ""
	}
	if err := f.begin("create", r.Plural, namespace, o.Name); err != nil {
		return err
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	k := key(r.Plural, namespace, o.Name)
	if _, taken := f.objects[k]; taken {
		return kube.NewAPIError(http.StatusConflict, "AlreadyExists", r.Plural+` "`+o.Name+`" already exists`)
	}
	stored := copyObject(*o)
	stored.Namespace = namespace
	f.objects[k] = stored
	return nil
}

// Update implements kube.Client.
func (f *Fake) Update(_ context.Context, r kube.Resource, o *kube.Object) error {
	namespace := o.Namespace
	if !r.Namespaced {
		namespace = ""
	}
	if err := f.begin("update", r.Plural, namespace, o.Name); err != nil {
		return err
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	k := key(r.Plural, namespace, o.Name)
	if _, ok := f.objects[k]; !ok {
		return notFound(r, o.Name)
	}
	stored := copyObject(*o)
	stored.Namespace = namespace
	f.objects[k] = stored
	return nil
}

// Delete implements kube.Client.
func (f *Fake) Delete(_ context.Context, r kube.Resource, namespace, name string) error {
	if !r.Namespaced {
		namespace = ""
	}
	if err := f.begin("delete", r.Plural, namespace, name); err != nil {
		return err
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	k := key(r.Plural, namespace, name)
	if _, ok := f.objects[k]; !ok {
		return notFound(r, name)
	}
	delete(f.objects, k)
	return nil
}

// CreateToken implements kube.Client.
func (f *Fake) CreateToken(_ context.Context, namespace, serviceAccount string, seconds int64) (kube.Token, error) {
	if err := f.begin("token", kube.ServiceAccounts.Plural, namespace, serviceAccount); err != nil {
		return kube.Token{}, err
	}
	f.mu.Lock()
	issue := f.token
	f.mu.Unlock()
	if issue == nil {
		return kube.Token{}, errors.New("kubetest: no token behaviour was set (use GrantTokens or IssueTokens)")
	}
	return issue(namespace, serviceAccount, seconds)
}

var _ kube.Client = (*Fake)(nil)
