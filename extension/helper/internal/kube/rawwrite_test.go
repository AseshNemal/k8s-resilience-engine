package kube

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

type recorded struct {
	method, path, contentType string
	body                      map[string]any
}

// rawServer answers every request with the given status and body and records what it was sent.
func rawServer(t *testing.T, status int, reply string) (RawWriter, *[]recorded) {
	t.Helper()
	var seen []recorded
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw, _ := io.ReadAll(r.Body)
		entry := recorded{method: r.Method, path: r.URL.Path, contentType: r.Header.Get("Content-Type")}
		if len(raw) > 0 {
			_ = json.Unmarshal(raw, &entry.body)
		}
		seen = append(seen, entry)
		w.WriteHeader(status)
		_, _ = w.Write([]byte(reply))
	}))
	t.Cleanup(srv.Close)
	c, err := NewClient(Config{Server: srv.URL, Timeout: 5 * time.Second})
	if err != nil {
		t.Fatal(err)
	}
	return c.(RawWriter), &seen
}

var serviceEntries = Resource{Group: "networking.istio.io", Version: "v1", Plural: "serviceentries", Kind: "ServiceEntry", Namespaced: true}

func TestCreateRawPostsTheWholeDocumentAndReturnsTheServersUID(t *testing.T) {
	w, seen := rawServer(t, 201, `{"metadata":{"uid":"uid-123"}}`)
	body := map[string]any{"apiVersion": "networking.istio.io/v1", "kind": "ServiceEntry", "metadata": map[string]any{"name": "x"}}
	uid, err := w.CreateRaw(bg, serviceEntries, "bookinfo", body)
	if err != nil || uid != "uid-123" {
		t.Fatalf("%q %v", uid, err)
	}
	got := (*seen)[0]
	if got.method != "POST" || got.path != "/apis/networking.istio.io/v1/namespaces/bookinfo/serviceentries" || got.body["kind"] != "ServiceEntry" {
		t.Fatalf("%+v", got)
	}
}

func TestCreateRawOfANamespacedKindNeedsANamespace(t *testing.T) {
	w, seen := rawServer(t, 201, `{}`)
	if _, err := w.CreateRaw(bg, serviceEntries, "", map[string]any{}); err == nil || len(*seen) != 0 {
		t.Fatalf("a request was made without a namespace: %v", err)
	}
}

func TestCreateRawReportsAlreadyExistsAsTheServerSaidIt(t *testing.T) {
	w, _ := rawServer(t, 409, `{"kind":"Status","reason":"AlreadyExists","message":"exists"}`)
	if _, err := w.CreateRaw(bg, serviceEntries, "bookinfo", map[string]any{}); !IsAlreadyExists(err) {
		t.Fatalf("%v", err)
	}
}

func TestDeleteRawSendsTheUIDAsAPrecondition(t *testing.T) {
	w, seen := rawServer(t, 200, `{}`)
	if err := w.DeleteRaw(bg, serviceEntries, "bookinfo", "x", "uid-123"); err != nil {
		t.Fatal(err)
	}
	got := (*seen)[0]
	pre, _ := got.body["preconditions"].(map[string]any)
	if got.method != "DELETE" || got.path != "/apis/networking.istio.io/v1/namespaces/bookinfo/serviceentries/x" || pre["uid"] != "uid-123" {
		t.Fatalf("%+v", got)
	}
}

func TestDeleteRawWithoutAUIDSendsNoBody(t *testing.T) {
	w, seen := rawServer(t, 200, `{}`)
	if err := w.DeleteRaw(bg, serviceEntries, "bookinfo", "x", ""); err != nil {
		t.Fatal(err)
	}
	if (*seen)[0].body != nil {
		t.Fatalf("%+v", (*seen)[0])
	}
}

func TestADeleteWhoseUIDDoesNotMatchIsAConflict(t *testing.T) {
	w, _ := rawServer(t, 409, `{"kind":"Status","reason":"Conflict","message":"Precondition failed: UID in precondition"}`)
	if err := w.DeleteRaw(bg, serviceEntries, "bookinfo", "x", "other"); !IsConflict(err) {
		t.Fatalf("%v", err)
	}
}

func TestPatchLabelsIsAMergePatchThatTouchesOnlyLabels(t *testing.T) {
	w, seen := rawServer(t, 200, `{}`)
	value := "enabled"
	err := w.PatchLabels(bg, Namespaces, "", "bookinfo", map[string]*string{"istio-injection": &value, "old": nil})
	if err != nil {
		t.Fatal(err)
	}
	got := (*seen)[0]
	labels := got.body["metadata"].(map[string]any)["labels"].(map[string]any)
	if got.method != "PATCH" || got.path != "/api/v1/namespaces/bookinfo" || got.contentType != "application/merge-patch+json" ||
		labels["istio-injection"] != "enabled" || labels["old"] != nil || len(got.body) != 1 {
		t.Fatalf("%+v", got)
	}
	if _, present := labels["old"]; !present {
		t.Fatal("a removed label must be sent as null, not omitted")
	}
}

func TestObjectsCarryTheirUIDAndRawDocument(t *testing.T) {
	o, err := ObjectFromRaw(map[string]any{"metadata": map[string]any{"name": "a", "uid": "u-1"}, "spec": map[string]any{"x": 1.0}})
	if err != nil || o.UID != "u-1" || o.Raw()["spec"] == nil {
		t.Fatalf("%+v %v", o, err)
	}
	if (&Object{}).Raw() != nil {
		t.Fatal("an object that was not read has no raw document")
	}
}
