package traceinstall

import (
	"errors"
	"testing"

	"resilience-engine/helper/internal/kube"
	"resilience-engine/helper/internal/kube/kubetest"
)

const install = "0123456789abcdef0123456789abcdef"

func guardOver(f *kubetest.Fake) *Guard {
	return NewGuard(f, install, PlannedInventory(), []string{"ratings-v1-abc"})
}

func doc(apiVersion, kind, ns, name string) map[string]any {
	meta := map[string]any{"name": name}
	if ns != "" {
		meta["namespace"] = ns
	}
	return map[string]any{"apiVersion": apiVersion, "kind": kind, "metadata": meta}
}

func TestAnInventoryObjectWithTheInstallationLabelsIsCreated(t *testing.T) {
	f := kubetest.New()
	body := Stamp(doc("networking.istio.io/v1", "ServiceEntry", "bookinfo", "trace-demo-jaeger-otlp"), install)
	ref, uid, err := guardOver(f).Create(bg, body)
	if err != nil || uid == "" || ref.Name != "trace-demo-jaeger-otlp" {
		t.Fatalf("%v %q %v", ref, uid, err)
	}
	got, ok := f.Lookup(resServiceEntry, "bookinfo", "trace-demo-jaeger-otlp")
	if !ok || got.Labels[LabelInstallation] != install || got.Labels[LabelManagedBy] != "resilience-tracing" {
		t.Fatalf("%+v", got)
	}
}

func TestEveryCreateOutsideTheInventoryIsRefusedBeforeARequestIsMade(t *testing.T) {
	cases := map[string]map[string]any{
		"another namespace":         Stamp(doc("networking.istio.io/v1", "ServiceEntry", "default", "trace-demo-jaeger-otlp"), install),
		"a Secret":                  Stamp(doc("v1", "Secret", "bookinfo", "trace-demo"), install),
		"an existing Deployment":    Stamp(doc("apps/v1", "Deployment", "bookinfo", "productpage-v1"), install),
		"a ClusterRole not in list": Stamp(doc("rbac.authorization.k8s.io/v1", "ClusterRole", "", "cluster-admin"), install),
		"a binding to admin":        Stamp(doc("rbac.authorization.k8s.io/v1", "ClusterRoleBinding", "", "resilience-admin"), install),
		"the kube-system namespace": Stamp(doc("v1", "Namespace", "", "kube-system"), install),
		"a wrong apiVersion":        Stamp(doc("networking.istio.io/v1beta1", "ServiceEntry", "bookinfo", "trace-demo-jaeger-otlp"), install),
		"no labels":                 doc("networking.istio.io/v1", "ServiceEntry", "bookinfo", "trace-demo-jaeger-otlp"),
		"another installation":      Stamp(doc("networking.istio.io/v1", "ServiceEntry", "bookinfo", "trace-demo-jaeger-otlp"), "ffffffffffffffffffffffffffffffff"),
	}
	for name, body := range cases {
		f := kubetest.New()
		if _, _, err := guardOver(f).Create(bg, body); !errors.Is(err, ErrNotAllowed) {
			t.Errorf("%s: %v", name, err)
		}
		if len(f.Actions()) != 0 {
			t.Errorf("%s: a request was made: %v", name, f.Actions())
		}
	}
}

func TestADeleteNeedsAnInventoryObjectAndItsUID(t *testing.T) {
	f := kubetest.New()
	g := guardOver(f)
	_, uid, err := g.Create(bg, Stamp(doc("telemetry.istio.io/v1", "Telemetry", "bookinfo", "trace-demo"), install))
	if err != nil || uid == "" {
		t.Fatalf("%q %v", uid, err)
	}
	if err := g.Delete(bg, telemetryRef(), ""); !errors.Is(err, ErrNotAllowed) {
		t.Fatalf("without a UID: %v", err)
	}
	if err := g.Delete(bg, bookinfoRef("apps/v1", "Deployment", "productpage-v1"), "uid-1"); !errors.Is(err, ErrNotAllowed) {
		t.Fatalf("not in the inventory: %v", err)
	}
	if err := g.Delete(bg, telemetryRef(), "uid-someone-else"); !kube.IsConflict(err) {
		t.Fatalf("a different UID must be refused by the server: %v", err)
	}
	if _, ok := f.Lookup(resTelemetry, "bookinfo", "trace-demo"); !ok {
		t.Fatal("the object was deleted despite the UID mismatch")
	}
	if err := g.Delete(bg, telemetryRef(), uid); err != nil {
		t.Fatal(err)
	}
}

func TestOnlyPodsTheApprovedPlanNamedCanBeDeleted(t *testing.T) {
	f := kubetest.New()
	f.Put(kube.Pods, kube.Object{Name: "ratings-v1-abc", Namespace: "bookinfo", UID: "u1"})
	f.Put(kube.Pods, kube.Object{Name: "reviews-v1-xyz", Namespace: "bookinfo", UID: "u2"})
	g := guardOver(f)
	if err := g.DeletePod(bg, "reviews-v1-xyz", "u2"); !errors.Is(err, ErrNotAllowed) {
		t.Fatalf("%v", err)
	}
	if err := g.DeletePod(bg, "ratings-v1-abc", ""); !errors.Is(err, ErrNotAllowed) {
		t.Fatalf("%v", err)
	}
	if err := g.DeletePod(bg, "ratings-v1-abc", "u1"); err != nil {
		t.Fatal(err)
	}
	if _, ok := f.Lookup(kube.Pods, "bookinfo", "reviews-v1-xyz"); !ok {
		t.Fatal("an unplanned Pod was deleted")
	}
}

func TestTheOnlyLabelChangeIsInjectionOnTheBookinfoNamespace(t *testing.T) {
	f := kubetest.New()
	f.Put(kube.Namespaces, kube.Object{Name: "bookinfo", Labels: map[string]string{"keep": "me"}})
	g := guardOver(f)
	if err := g.SetInjection(bg, true); err != nil {
		t.Fatal(err)
	}
	got, _ := f.Lookup(kube.Namespaces, "", "bookinfo")
	if got.Labels["istio-injection"] != "enabled" || got.Labels["keep"] != "me" {
		t.Fatalf("%v", got.Labels)
	}
	if err := g.SetInjection(bg, false); err != nil {
		t.Fatal(err)
	}
	got, _ = f.Lookup(kube.Namespaces, "", "bookinfo")
	if _, there := got.Labels["istio-injection"]; there || got.Labels["keep"] != "me" {
		t.Fatalf("%v", got.Labels)
	}
	for _, a := range f.Actions() {
		if a.Resource != "namespaces" || a.Name != "bookinfo" {
			t.Errorf("unexpected action %+v", a)
		}
	}
}

func TestStampChangesOnlyTheLabelsAndLeavesTheOriginalAlone(t *testing.T) {
	original := doc("apps/v1", "Deployment", "bookinfo", "reviews-v2")
	original["spec"] = map[string]any{"replicas": 1}
	stamped := Stamp(original, install)
	if _, there := asMap(original["metadata"])["labels"]; there {
		t.Fatal("the input was modified")
	}
	if stamped["spec"].(map[string]any)["replicas"] != 1 || asMap(asMap(stamped["metadata"])["labels"])[LabelInstallation] != install {
		t.Fatalf("%v", stamped)
	}
}

func TestEveryRenderedAndConfiguredKindHasAResource(t *testing.T) {
	for _, e := range PlannedInventory() {
		if _, err := ResourceFor(e.Ref.APIVersion, e.Ref.Kind); err != nil {
			t.Errorf("%s: %v", e.Ref, err)
		}
	}
}

func TestThePlannedInventoryIsTheReviewedListPlusTheBookinfoObjects(t *testing.T) {
	inv := PlannedInventory()
	if len(inv) != 1+32+2+1+2+1 { // istio-system, Istio's 32, ServiceEntry+Telemetry, reviews-v2, DR+VS, client Pod
		t.Fatalf("%d entries", len(inv))
	}
	for _, e := range inv {
		if e.State != EntryPlanned {
			t.Errorf("%s starts as %s", e.Ref, e.State)
		}
	}
}

func TestTheInventoryIsInCreationOrderSoUndoCanWalkItBackwards(t *testing.T) {
	var kinds []string
	for _, e := range PlannedInventory() {
		if e.Stage == StageIstio {
			kinds = append(kinds, e.Ref.Kind)
		}
	}
	if kinds[0] != "Namespace" || kinds[1] != "CustomResourceDefinition" || kinds[len(kinds)-1] != "MutatingWebhookConfiguration" {
		t.Fatalf("%v", kinds)
	}
	last := -1
	for _, k := range kinds {
		if kindOrder[k] < last {
			t.Fatalf("not in creation order at %s: %v", k, kinds)
		}
		last = kindOrder[k]
	}
}
