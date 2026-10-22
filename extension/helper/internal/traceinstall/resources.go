package traceinstall

import (
	"fmt"
	"sort"

	"resilience-engine/helper/internal/bookinfotrace"
	"resilience-engine/helper/internal/kube"
)

// The kinds this setup creates, and nothing else. A rendered object of any other kind is refused before any request.
var (
	resNamespace    = kube.Namespaces
	resCRD          = kube.CustomResourceDefinitions
	resServiceAcct  = kube.ServiceAccounts
	resConfigMap    = kube.ConfigMaps
	resClusterRole  = kube.ClusterRoles
	resClusterBind  = kube.ClusterRoleBindings
	resRole         = kube.Roles
	resRoleBinding  = kube.RoleBindings
	resService      = kube.Services
	resDeployment   = kube.Deployments
	resPod          = kube.Pods
	resValidatingWH = kube.Resource{Group: "admissionregistration.k8s.io", Version: "v1", Plural: "validatingwebhookconfigurations", Kind: "ValidatingWebhookConfiguration"}
	resMutatingWH   = kube.Resource{Group: "admissionregistration.k8s.io", Version: "v1", Plural: "mutatingwebhookconfigurations", Kind: "MutatingWebhookConfiguration"}
	resServiceEntry = kube.Resource{Group: "networking.istio.io", Version: "v1", Plural: "serviceentries", Kind: "ServiceEntry", Namespaced: true}
	resDestRule     = kube.Resource{Group: "networking.istio.io", Version: "v1", Plural: "destinationrules", Kind: "DestinationRule", Namespaced: true}
	resVirtualSvc   = kube.Resource{Group: "networking.istio.io", Version: "v1", Plural: "virtualservices", Kind: "VirtualService", Namespaced: true}
	resTelemetry    = kube.Resource{Group: "telemetry.istio.io", Version: "v1", Plural: "telemetries", Kind: "Telemetry", Namespaced: true}
)

var resourceTable = []kube.Resource{
	resNamespace, resCRD, resServiceAcct, resConfigMap, resClusterRole, resClusterBind, resRole, resRoleBinding, resService,
	resDeployment, resPod, resValidatingWH, resMutatingWH, resServiceEntry, resDestRule, resVirtualSvc, resTelemetry,
}

// ResourceFor maps an object's apiVersion and kind to its REST collection.
func ResourceFor(apiVersion, kind string) (kube.Resource, error) {
	for _, r := range resourceTable {
		if r.Kind == kind && r.APIVersion() == apiVersion {
			return r, nil
		}
	}
	return kube.Resource{}, fmt.Errorf("the kind %s (%s) is not one this setup creates", kind, apiVersion)
}

// Labels every created object carries. The first says who manages it; the second binds it to one installation, so
// an object with the same name made by anyone else, or by an earlier installation, is never treated as ours.
const (
	LabelManagedBy    = "app.kubernetes.io/managed-by"
	LabelInstallation = "resilience.sliit/installation-id"
)

// Inventory stages, in the order they run.
const (
	StageIstio    = "istio"
	StageConfig   = "bookinfo-config"
	StageWorkload = "workload"
	StageRoute    = "route"
	StageVerify   = "verify"
)

var istioNamespace = ObjectRef{APIVersion: "v1", Kind: "Namespace", Name: bookinfotrace.Contract.IstioNamespace}

func bookinfoRef(apiVersion, kind, name string) ObjectRef {
	return ObjectRef{APIVersion: apiVersion, Kind: kind, Namespace: bookinfotrace.Contract.Namespace, Name: name}
}

// Refs of the objects in the bookinfo namespace that this setup creates.
func serviceEntryRef() ObjectRef {
	return bookinfoRef("networking.istio.io/v1", "ServiceEntry", "trace-demo-jaeger-otlp")
}
func telemetryRef() ObjectRef { return bookinfoRef("telemetry.istio.io/v1", "Telemetry", "trace-demo") }
func destRuleRef() ObjectRef {
	return bookinfoRef("networking.istio.io/v1", "DestinationRule", "trace-demo-reviews")
}
func virtualSvcRef() ObjectRef {
	return bookinfoRef("networking.istio.io/v1", "VirtualService", "trace-demo-reviews")
}
func reviewsV2Ref() ObjectRef { return bookinfoRef("apps/v1", "Deployment", "reviews-v2") }
func clientPodRef() ObjectRef { return bookinfoRef("v1", "Pod", "trace-demo-client") }

// PlannedInventory is the complete list of objects the run may own, in creation order. reviews-v2 is always in it: Plan
// refuses to proceed when one already exists, so this setup always creates it itself (with a sidecar).
func PlannedInventory() []Entry {
	var inv []Entry
	add := func(stage string, ref ObjectRef) {
		inv = append(inv, Entry{Ref: ref, Stage: stage, State: EntryPlanned})
	}
	add(StageIstio, istioNamespace)
	istio := ExpectedIstioObjects()
	sort.SliceStable(istio, func(i, j int) bool { return creationBefore(istio[i], istio[j]) })
	for _, o := range istio {
		add(StageIstio, o)
	}
	add(StageConfig, serviceEntryRef())
	add(StageConfig, telemetryRef())
	add(StageWorkload, reviewsV2Ref())
	add(StageRoute, destRuleRef())
	add(StageRoute, virtualSvcRef())
	add(StageVerify, clientPodRef())
	return inv
}
