package rbac

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"sync"
	"testing"

	"resilience-engine/helper/internal/kube"
)

// apiServer is just enough of a Kubernetes API server to check the helper's wire behaviour end to end:
// paths, request bodies, optimistic concurrency on update, and what is left untouched. Objects are kept
// as the JSON they were sent in, so anything the helper does not model must survive a round trip.
type apiServer struct {
	mu      sync.Mutex
	objects map[string]map[string]any // collection path + "/" + name
	log     []string
}

func newAPIServer(t *testing.T) (*apiServer, kube.Client) {
	t.Helper()
	api := &apiServer{objects: map[string]map[string]any{}}
	srv := httptest.NewServer(api)
	t.Cleanup(srv.Close)
	client, err := kube.NewClient(kube.Config{Server: srv.URL})
	if err != nil {
		t.Fatal(err)
	}
	return api, client
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(v)
}

func status(w http.ResponseWriter, code int, reason string) {
	writeJSON(w, code, map[string]any{"kind": "Status", "reason": reason, "message": reason, "code": code})
}

func (a *apiServer) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.log = append(a.log, r.Method+" "+r.URL.Path)
	path := strings.TrimSuffix(r.URL.Path, "/")

	if strings.HasSuffix(path, "/token") && r.Method == http.MethodPost {
		writeJSON(w, http.StatusCreated, map[string]any{"status": map[string]any{"token": "t", "expirationTimestamp": "2099-01-01T00:00:00Z"}})
		return
	}
	parts := strings.Split(strings.TrimPrefix(path, "/"), "/")
	// An item path ends in a name; a collection path ends in a plural. Decide by whether the second to
	// last segment is a known plural.
	plurals := map[string]bool{"namespaces": true, "serviceaccounts": true, "clusterroles": true, "clusterrolebindings": true,
		"rolebindings": true, "roles": true, "pods": true, "services": true, "secrets": true, "configmaps": true,
		"persistentvolumeclaims": true, "deployments": true, "statefulsets": true, "daemonsets": true,
		"jobs": true, "cronjobs": true, "ingresses": true}
	last := parts[len(parts)-1]
	isCollection := plurals[last] && !(len(parts) >= 2 && parts[len(parts)-2] == "namespaces" && last != "namespaces")

	switch {
	case r.Method == http.MethodGet && isCollection:
		items := []any{}
		for key, obj := range a.objects {
			if strings.HasSuffix(parentOf(key), "/"+last) || parentOf(key) == path {
				items = append(items, obj)
			}
		}
		writeJSON(w, 200, map[string]any{"items": items})
	case r.Method == http.MethodPost && isCollection:
		var body map[string]any
		json.NewDecoder(r.Body).Decode(&body)
		name := body["metadata"].(map[string]any)["name"].(string)
		key := path + "/" + name
		if _, taken := a.objects[key]; taken {
			status(w, 409, "AlreadyExists")
			return
		}
		body["metadata"].(map[string]any)["resourceVersion"] = "1"
		a.objects[key] = body
		writeJSON(w, 201, body)
	case r.Method == http.MethodGet:
		if obj, ok := a.objects[path]; ok {
			writeJSON(w, 200, obj)
			return
		}
		status(w, 404, "NotFound")
	case r.Method == http.MethodPut:
		current, ok := a.objects[path]
		if !ok {
			status(w, 404, "NotFound")
			return
		}
		var body map[string]any
		json.NewDecoder(r.Body).Decode(&body)
		sent := body["metadata"].(map[string]any)["resourceVersion"]
		have := current["metadata"].(map[string]any)["resourceVersion"]
		if sent != have {
			status(w, 409, "Conflict") // optimistic concurrency: the helper must send back what it read
			return
		}
		n, _ := strconv.Atoi(have.(string))
		body["metadata"].(map[string]any)["resourceVersion"] = strconv.Itoa(n + 1)
		a.objects[path] = body
		writeJSON(w, 200, body)
	case r.Method == http.MethodDelete:
		if _, ok := a.objects[path]; !ok {
			status(w, 404, "NotFound")
			return
		}
		delete(a.objects, path)
		writeJSON(w, 200, map[string]any{"kind": "Status", "status": "Success"})
	default:
		status(w, 405, "MethodNotAllowed")
	}
}

func parentOf(key string) string { return key[:strings.LastIndex(key, "/")] }

func (a *apiServer) object(path string) map[string]any {
	a.mu.Lock()
	defer a.mu.Unlock()
	return a.objects[path]
}

const (
	clusterRolePath = "/apis/rbac.authorization.k8s.io/v1/clusterroles/" + Name
	bindingPath     = "/apis/rbac.authorization.k8s.io/v1/clusterrolebindings/" + Name
	namespacePath   = "/api/v1/namespaces/" + Namespace
)

func TestTheWholeFlowWorksThroughTheRealClientAgainstAnAPIServer(t *testing.T) {
	api, client := newAPIServer(t)

	// Nothing exists: four creates.
	r := inspect(t, client)
	if r.Overall() != OverallAbsent || len(Plan(r)) != 4 {
		t.Fatalf("overall %s plan %v", r.Overall(), Plan(r))
	}
	if err := Apply(ctx, client, r); err != nil {
		t.Fatal(err)
	}
	if r := inspect(t, client); r.Overall() != OverallApproved {
		t.Fatalf("after apply: %s", r.Overall())
	}
	role := api.object(clusterRolePath)
	if role["kind"] != "ClusterRole" || role["apiVersion"] != "rbac.authorization.k8s.io/v1" {
		t.Fatalf("role as stored = %v", role)
	}
	if api.object(bindingPath)["roleRef"].(map[string]any)["name"] != Name {
		t.Fatalf("binding as stored = %v", api.object(bindingPath))
	}

	// Someone widens the role and annotates it. A repair must fix the rules and keep their annotation.
	api.mu.Lock()
	stored := api.objects[clusterRolePath]
	stored["rules"] = append(stored["rules"].([]any), map[string]any{"apiGroups": []any{""}, "resources": []any{"secrets"}, "verbs": []any{"get"}})
	stored["metadata"].(map[string]any)["annotations"] = map[string]any{"owner": "platform-team"}
	stored["metadata"].(map[string]any)["finalizers"] = []any{"example.com/keep"}
	api.mu.Unlock()

	drifted := inspect(t, client)
	if drifted.Overall() != OverallDrifted {
		t.Fatalf("overall = %s", drifted.Overall())
	}
	if err := Apply(ctx, client, drifted); err != nil {
		t.Fatalf("a repair is an update that must carry the resourceVersion it read: %v", err)
	}
	repaired := api.object(clusterRolePath)
	meta := repaired["metadata"].(map[string]any)
	if meta["annotations"].(map[string]any)["owner"] != "platform-team" || meta["finalizers"].([]any)[0] != "example.com/keep" {
		t.Fatalf("fields the helper does not manage were lost: %v", meta)
	}
	if meta["resourceVersion"] != "2" {
		t.Fatalf("resourceVersion = %v", meta["resourceVersion"])
	}
	if len(repaired["rules"].([]any)) != 2 {
		t.Fatalf("rules = %v", repaired["rules"])
	}
	if r := inspect(t, client); r.Overall() != OverallApproved {
		t.Fatalf("after repair: %s", r.Overall())
	}

	// Revoke deletes exactly the plan, and a second revoke is harmless.
	plan := PlanRevoke(ctx, client, inspect(t, client))
	if got := strings.Join(deleted(plan), ","); got != "ClusterRoleBinding,ClusterRole,ServiceAccount,Namespace" {
		t.Fatalf("plan = %s keep %v", got, plan.Keep)
	}
	if err := Revoke(ctx, client, plan); err != nil {
		t.Fatal(err)
	}
	if err := Revoke(ctx, client, plan); err != nil {
		t.Fatalf("repeat: %v", err)
	}
	if r := inspect(t, client); r.Overall() != OverallAbsent {
		t.Fatalf("after revoke: %s", r.Overall())
	}
}

func TestAnExtraBindingFoundOverTheWireStopsApplyBeforeAnyWrite(t *testing.T) {
	api, client := newAPIServer(t)
	if err := Apply(ctx, client, inspect(t, client)); err != nil {
		t.Fatal(err)
	}
	api.mu.Lock()
	api.objects["/apis/rbac.authorization.k8s.io/v1/namespaces/default/rolebindings/edit"] = map[string]any{
		"metadata": map[string]any{"name": "edit", "namespace": "default"},
		"roleRef":  map[string]any{"apiGroup": kube.RBACGroup, "kind": "ClusterRole", "name": "edit"},
		"subjects": []any{map[string]any{"kind": "ServiceAccount", "name": Name, "namespace": Namespace}},
	}
	api.log = nil
	api.mu.Unlock()

	r := inspect(t, client)
	if r.Overall() != OverallOverGranted || len(r.ExtraGrants) != 1 || r.ExtraGrants[0].Namespace != "default" {
		t.Fatalf("overall %s grants %+v", r.Overall(), r.ExtraGrants)
	}
	if err := Apply(ctx, client, r); err != ErrOverGranted {
		t.Fatalf("Apply = %v", err)
	}
	for _, line := range api.log {
		if !strings.HasPrefix(line, "GET ") {
			t.Fatalf("a refused Apply must not write, but sent %q", line)
		}
	}
}

func TestNonLabelledLegacyObjectsAreLabelledWithoutChangingTheirContent(t *testing.T) {
	api, client := newAPIServer(t)
	if err := Apply(ctx, client, inspect(t, client)); err != nil {
		t.Fatal(err)
	}
	api.mu.Lock()
	for _, key := range []string{namespacePath, "/api/v1/namespaces/" + Namespace + "/serviceaccounts/" + Name, clusterRolePath, bindingPath} {
		delete(api.objects[key]["metadata"].(map[string]any), "labels")
	}
	api.mu.Unlock()

	r := inspect(t, client)
	if r.Overall() != OverallApprovedOld {
		t.Fatalf("overall = %s", r.Overall())
	}
	if err := Apply(ctx, client, r); err != nil {
		t.Fatal(err)
	}
	if r := inspect(t, client); r.Overall() != OverallApproved {
		t.Fatalf("after labelling: %s", r.Overall())
	}
}
