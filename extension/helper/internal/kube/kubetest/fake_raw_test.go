package kubetest

import (
	"context"
	"testing"

	"resilience-engine/helper/internal/kube"
)

func TestTheFakeCreatesWithUIDsDeletesOnlyTheMatchingUIDAndPatchesLabels(t *testing.T) {
	f := New()
	ctx := context.Background()
	body := func(n string) map[string]any {
		return map[string]any{"apiVersion": "v1", "kind": "ConfigMap", "metadata": map[string]any{"name": n, "labels": map[string]any{"a": "1"}}}
	}
	uid, err := f.CreateRaw(ctx, kube.ConfigMaps, "ns", body("x"))
	if err != nil || uid == "" {
		t.Fatal(uid, err)
	}
	if _, err := f.CreateRaw(ctx, kube.ConfigMaps, "ns", body("x")); !kube.IsAlreadyExists(err) {
		t.Fatalf("%v", err)
	}
	got, _ := f.Lookup(kube.ConfigMaps, "ns", "x")
	if got.UID != uid || got.Labels["a"] != "1" {
		t.Fatalf("%+v", got)
	}
	v := "2"
	if err := f.PatchLabels(ctx, kube.ConfigMaps, "ns", "x", map[string]*string{"b": &v, "a": nil}); err != nil {
		t.Fatal(err)
	}
	got, _ = f.Lookup(kube.ConfigMaps, "ns", "x")
	if got.Labels["b"] != "2" || got.Labels["a"] != "" {
		t.Fatalf("%+v", got.Labels)
	}
	if err := f.DeleteRaw(ctx, kube.ConfigMaps, "ns", "x", "uid-other"); !kube.IsConflict(err) {
		t.Fatalf("%v", err)
	}
	if err := f.DeleteRaw(ctx, kube.ConfigMaps, "ns", "x", uid); err != nil {
		t.Fatal(err)
	}
	writes, _ := f.Writes()
	if len(writes) != 5 {
		t.Fatalf("%v", writes)
	}
}
