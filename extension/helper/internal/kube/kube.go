// Package kube is the small slice of the Kubernetes REST API the helper uses, over plain net/http.
//
// The helper needs a handful of calls: read the server version, get/create/update/delete the four
// objects that make up the extension's access (Namespace, ServiceAccount, ClusterRole,
// ClusterRoleBinding), list a few kinds to find extra grants or to prove a namespace is empty, and
// request a ServiceAccount token. Pulling in client-go for that meant 590 packages and three
// cross-compiles of them, so this package speaks JSON to the API server directly.
//
// Rules that keep it safe to use for writes:
//   - Update writes back onto the object exactly as it was read, changing only the fields the helper
//     manages, so anything else on the object (annotations, finalizers, resourceVersion) survives.
//   - Errors carry the server's own status, so callers can tell NotFound, Forbidden, AlreadyExists
//     and Conflict apart. They never contain the credential.
package kube

import (
	"context"
	"time"
)

// Resource names one REST collection.
type Resource struct {
	Group      string // "" for the core group
	Version    string
	Plural     string
	Kind       string
	Namespaced bool
}

// APIVersion is the apiVersion string of objects in the collection.
func (r Resource) APIVersion() string {
	if r.Group == "" {
		return r.Version
	}
	return r.Group + "/" + r.Version
}

const rbacGroup = "rbac.authorization.k8s.io"

// Nodes is only listed, to read the provider ID each node reports (see Object.ProviderID).
var Nodes = Resource{Version: "v1", Plural: "nodes", Kind: "Node"}

// The four kinds the helper creates, changes and deletes.
var (
	Namespaces          = Resource{Version: "v1", Plural: "namespaces", Kind: "Namespace"}
	ServiceAccounts     = Resource{Version: "v1", Plural: "serviceaccounts", Kind: "ServiceAccount", Namespaced: true}
	ClusterRoles        = Resource{Group: rbacGroup, Version: "v1", Plural: "clusterroles", Kind: "ClusterRole"}
	ClusterRoleBindings = Resource{Group: rbacGroup, Version: "v1", Plural: "clusterrolebindings", Kind: "ClusterRoleBinding"}
)

// CustomResourceDefinitions is only listed, to see whether a CRD group (for example Istio's) is already installed.
var CustomResourceDefinitions = Resource{Group: "apiextensions.k8s.io", Version: "v1", Plural: "customresourcedefinitions", Kind: "CustomResourceDefinition"}

// Kinds that are only listed: to find other bindings of the service account, and to check that the
// extension's namespace holds nothing else before it may be deleted.
var (
	RoleBindings           = Resource{Group: rbacGroup, Version: "v1", Plural: "rolebindings", Kind: "RoleBinding", Namespaced: true}
	Roles                  = Resource{Group: rbacGroup, Version: "v1", Plural: "roles", Kind: "Role", Namespaced: true}
	Pods                   = Resource{Version: "v1", Plural: "pods", Kind: "Pod", Namespaced: true}
	Services               = Resource{Version: "v1", Plural: "services", Kind: "Service", Namespaced: true}
	Secrets                = Resource{Version: "v1", Plural: "secrets", Kind: "Secret", Namespaced: true}
	ConfigMaps             = Resource{Version: "v1", Plural: "configmaps", Kind: "ConfigMap", Namespaced: true}
	PersistentVolumeClaims = Resource{Version: "v1", Plural: "persistentvolumeclaims", Kind: "PersistentVolumeClaim", Namespaced: true}
	Deployments            = Resource{Group: "apps", Version: "v1", Plural: "deployments", Kind: "Deployment", Namespaced: true}
	StatefulSets           = Resource{Group: "apps", Version: "v1", Plural: "statefulsets", Kind: "StatefulSet", Namespaced: true}
	DaemonSets             = Resource{Group: "apps", Version: "v1", Plural: "daemonsets", Kind: "DaemonSet", Namespaced: true}
	Jobs                   = Resource{Group: "batch", Version: "v1", Plural: "jobs", Kind: "Job", Namespaced: true}
	CronJobs               = Resource{Group: "batch", Version: "v1", Plural: "cronjobs", Kind: "CronJob", Namespaced: true}
	Ingresses              = Resource{Group: "networking.k8s.io", Version: "v1", Plural: "ingresses", Kind: "Ingress", Namespaced: true}
)

// PolicyRule is one RBAC rule.
type PolicyRule struct {
	APIGroups       []string `json:"apiGroups,omitempty"`
	Resources       []string `json:"resources,omitempty"`
	ResourceNames   []string `json:"resourceNames,omitempty"`
	NonResourceURLs []string `json:"nonResourceURLs,omitempty"`
	Verbs           []string `json:"verbs"`
}

// RoleRef points a binding at a role. It cannot be changed on an existing binding.
type RoleRef struct {
	APIGroup string `json:"apiGroup"`
	Kind     string `json:"kind"`
	Name     string `json:"name"`
}

// Subject is who a binding grants a role to.
type Subject struct {
	Kind      string `json:"kind"`
	APIGroup  string `json:"apiGroup,omitempty"`
	Name      string `json:"name"`
	Namespace string `json:"namespace,omitempty"`
}

// ServiceAccountKind is the Subject kind of a service account.
const ServiceAccountKind = "ServiceAccount"

// RBACGroup is the API group of RBAC objects, as used in RoleRef.APIGroup.
const RBACGroup = rbacGroup

// Object is the part of a Kubernetes object the helper reads and manages. Only the fields that apply
// to the object's kind are filled in: Rules and AggregationRule for a ClusterRole, RoleRef and
// Subjects for a binding.
type Object struct {
	Name      string
	Namespace string
	Labels    map[string]string
	// UID is metadata.uid as the server reported it. It identifies this exact object: a deleted and recreated
	// object with the same name has another one. Empty for an object that was not read from the server.
	UID string

	Rules           []PolicyRule
	AggregationRule bool // the object has an aggregationRule, so it can gain permissions from other roles

	RoleRef  RoleRef
	Subjects []Subject

	// ProviderID is spec.providerID of a Node ("kind://docker/<cluster>/<node>" for kind). Empty when the
	// node reports none; it is never guessed.
	ProviderID string

	// Read-only facts about workloads, filled in only when the server reported them. Unknown stays nil or empty:
	// nothing here is ever defaulted.
	Containers    []string // spec.containers[].name of a Pod or of a Deployment's Pod template
	Replicas      *int     // spec.replicas of a Deployment
	ReadyReplicas *int     // status.readyReplicas of a Deployment
	Phase         string   // status.phase of a Pod
	Ready         *bool    // the Ready condition of a Pod
	// SpecDigest is a SHA-256 of the object's spec as the server sent it (keys sorted, so it is stable). It lets a
	// caller prove a spec is unchanged without keeping a copy of it. Empty when the object has no spec.
	SpecDigest string

	// raw is the object exactly as the server sent it. Update writes onto it, so fields the helper does
	// not model are sent back unchanged. It is nil for an object that was not read from the server.
	raw map[string]any
}

// Raw returns the object exactly as the server sent it, or nil for an object that was not read from the server. The
// map is the caller's to read and must not be modified; it is how a caller looks at fields this package does not model
// (a custom resource's spec, a CRD's versions).
func (o *Object) Raw() map[string]any { return o.raw }

// Token is a ServiceAccount token as the TokenRequest API returned it. ExpiresAt is zero when the
// server did not report one.
type Token struct {
	Value     string
	ExpiresAt time.Time
}

// Reader is the read-only part of Client. Code that must never change a cluster takes a Reader, so a Create,
// Update, Delete or token request in it does not compile.
type Reader interface {
	// ServerVersion returns the server's git version, for example v1.37.0.
	ServerVersion(ctx context.Context) (string, error)
	// Get reads one object. namespace is ignored for cluster-scoped kinds.
	Get(ctx context.Context, r Resource, namespace, name string) (*Object, error)
	// List reads a collection. For a namespaced kind, namespace "" lists every namespace.
	List(ctx context.Context, r Resource, namespace string) ([]Object, error)
}

// RawWriter creates, deletes and relabels objects given as JSON documents. It is separate from Client on purpose: only
// the installation mechanism for the Bookinfo tracing setup (package traceinstall) uses it, and only through a writer
// that checks an allow-list before every request. Nothing that plans or reads takes one.
type RawWriter interface {
	// CreateRaw posts body (a complete object) to the collection and returns the UID the server assigned.
	// The server rejects a taken name with AlreadyExists.
	CreateRaw(ctx context.Context, r Resource, namespace string, body map[string]any) (uid string, err error)
	// DeleteRaw deletes one object. A non-empty uid is sent as a precondition, so the server refuses (Conflict) to
	// delete an object that is not the one the caller created.
	DeleteRaw(ctx context.Context, r Resource, namespace, name, uid string) error
	// PatchLabels changes labels with a JSON merge patch: a nil value removes the label, nothing else is touched.
	PatchLabels(ctx context.Context, r Resource, namespace, name string, set map[string]*string) error
}

// Client is what the helper needs from a cluster.
type Client interface {
	// ServerVersion returns the server's git version, for example v1.37.0.
	ServerVersion(ctx context.Context) (string, error)
	// Get reads one object. namespace is ignored for cluster-scoped kinds.
	Get(ctx context.Context, r Resource, namespace, name string) (*Object, error)
	// List reads a collection. For a namespaced kind, namespace "" lists every namespace.
	List(ctx context.Context, r Resource, namespace string) ([]Object, error)
	// Create adds an object. The server rejects it with AlreadyExists if the name is taken.
	Create(ctx context.Context, r Resource, o *Object) error
	// Update writes back an object returned by Get with the helper-managed fields changed.
	Update(ctx context.Context, r Resource, o *Object) error
	// Delete removes one object.
	Delete(ctx context.Context, r Resource, namespace, name string) error
	// CreateToken asks the TokenRequest API for a token for a ServiceAccount.
	CreateToken(ctx context.Context, namespace, serviceAccount string, expirationSeconds int64) (Token, error)
}
