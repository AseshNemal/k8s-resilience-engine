// Package bookinfotrace plans, and later verifies and undoes, the Istio tracing setup for the Bookinfo benchmark
// (see k8s-bookinfo/tracing and docs/BOOKINFO_TRACING_SETUP.md).
//
// What exists here is read-only by construction:
//
//   - Plan reads the cluster through a kube.Reader, an interface with no Create, Update, Delete or token request,
//     so a write cannot be written against it, and a test scans this package for the names of the write calls;
//   - the Docker and host facts it uses are read with fixed arguments (nothing from the page reaches a command);
//   - it contains no executor. Applying the setup is a separate, explicitly authorized mechanism that does not
//     exist yet; what is here is the exact preview it would have to show, the allow-list it would have to stay
//     inside, the gates that stop it, and the snapshot that proves a rollback.
//
// Unknown stays unknown: a measurement that could not be taken blocks the plan, it is never read as "fine".
package bookinfotrace
