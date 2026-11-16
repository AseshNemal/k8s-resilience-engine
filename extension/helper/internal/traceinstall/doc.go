// Package traceinstall is the only code that changes a cluster for the Bookinfo tracing setup. It applies, verifies
// and undoes the Istio sidecar tracing described in docs/BOOKINFO_TRACING_SETUP.md.
//
// It is deliberately separate from package bookinfotrace, which plans and can never write. The rules that make it
// safe to run:
//
//   - nothing is created, restarted or removed before a native approval dialog. Apply lists every change, the Pods that
//     restart (and what is unavailable meanwhile) and that a failed step rolls back what this run created; Undo and Run
//     test traffic have their own dialogs. Verify is read-only and asks nothing;
//   - the person's own kubeconfig credential is used inside this process for this one operation. It is never stored,
//     printed or passed to another process; the extension's own access is neither used nor changed;
//   - every write goes through Guard, which checks an allow-list in code before any request is made;
//   - Istio is not installed by `istioctl install`. A pinned, checksum-verified istioctl only renders the objects,
//     offline, and this package creates exactly those objects, so the inventory of what the run owns is exact;
//   - what the run created is written to a durable, permission-restricted record (bound to the cluster's identity)
//     before it is created, so an interrupted run can be finished or undone, and Undo deletes only that inventory,
//     object by object, by UID.
package traceinstall
