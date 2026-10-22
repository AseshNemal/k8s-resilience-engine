// Package bookinfotrace plans the Istio tracing setup for the Bookinfo benchmark (see k8s-bookinfo/tracing and
// docs/BOOKINFO_TRACING_SETUP.md) and holds what the setup is measured and approved against.
//
// This package never writes to a cluster, by construction:
//
//   - Plan reads the cluster through a kube.Reader, an interface with no Create, Update, Delete or token request, so a
//     write cannot be written against it, and a test scans this package for the names of the write calls;
//   - the Docker and host facts it uses are read with fixed arguments (nothing from the page reaches a command);
//   - it contains no executor. Applying, verifying with traffic and undoing live in package traceinstall, which has its
//     own allow-listed writer and is reached only after a native approval. What stays here is the exact preview and the
//     fingerprint an approval is bound to, the approval dialogs' text, the memory gates, and the snapshot that proves a
//     rollback.
//
// Unknown stays unknown: a measurement that could not be taken blocks the plan, it is never read as "fine".
package bookinfotrace
