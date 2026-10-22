package kube

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/json"
	"encoding/pem"
	"io"
	"log"
	"math/big"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"
	"time"
)

var bg = context.Background()

const bearer = "tok-secret-bearer-value"

// ---------------------------------------------------------------- certificates for a local test server

type pki struct {
	caPEM     []byte
	serverTLS tls.Certificate
	ca        *x509.Certificate
	caKey     *ecdsa.PrivateKey
}

func newKey(t *testing.T) *ecdsa.PrivateKey {
	t.Helper()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	return key
}

func sign(t *testing.T, tpl, parent *x509.Certificate, pub *ecdsa.PublicKey, parentKey *ecdsa.PrivateKey) (*x509.Certificate, []byte) {
	t.Helper()
	der, err := x509.CreateCertificate(rand.Reader, tpl, parent, pub, parentKey)
	if err != nil {
		t.Fatal(err)
	}
	cert, err := x509.ParseCertificate(der)
	if err != nil {
		t.Fatal(err)
	}
	return cert, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})
}

// newPKI makes a CA and a server certificate valid for the name "localhost" and for no IP address,
// like kind's: connecting by 127.0.0.1 only verifies when the client is told to check "localhost".
func newPKI(t *testing.T) pki {
	t.Helper()
	caKey := newKey(t)
	caTpl := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "test-ca"},
		NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour),
		IsCA: true, BasicConstraintsValid: true, KeyUsage: x509.KeyUsageCertSign}
	ca, caPEM := sign(t, caTpl, caTpl, &caKey.PublicKey, caKey)

	srvKey := newKey(t)
	srvTpl := &x509.Certificate{SerialNumber: big.NewInt(2), Subject: pkix.Name{CommonName: "localhost"},
		NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour),
		DNSNames: []string{"localhost"}, KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}}
	_, srvPEM := sign(t, srvTpl, ca, &srvKey.PublicKey, caKey)
	keyDER, err := x509.MarshalECPrivateKey(srvKey)
	if err != nil {
		t.Fatal(err)
	}
	pair, err := tls.X509KeyPair(srvPEM, pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: keyDER}))
	if err != nil {
		t.Fatal(err)
	}
	return pki{caPEM: caPEM, serverTLS: pair, ca: ca, caKey: caKey}
}

// clientCert issues a client certificate and key (PEM) signed by the test CA.
func (p pki) clientCert(t *testing.T) (certPEM, keyPEM []byte) {
	t.Helper()
	key := newKey(t)
	tpl := &x509.Certificate{SerialNumber: big.NewInt(3), Subject: pkix.Name{CommonName: "kind-admin"},
		NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour),
		KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth}}
	_, certPEM = sign(t, tpl, p.ca, &key.PublicKey, p.caKey)
	der, err := x509.MarshalECPrivateKey(key)
	if err != nil {
		t.Fatal(err)
	}
	return certPEM, pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: der})
}

// serve starts a TLS server using the test certificate. It is reached at https://127.0.0.1:port.
func serve(t *testing.T, p pki, h http.HandlerFunc, mutate ...func(*httptest.Server)) *httptest.Server {
	t.Helper()
	srv := httptest.NewUnstartedServer(h)
	srv.Config.ErrorLog = log.New(io.Discard, "", 0) // the tests make handshakes fail on purpose
	srv.TLS = &tls.Config{Certificates: []tls.Certificate{p.serverTLS}}
	for _, m := range mutate {
		m(srv)
	}
	srv.StartTLS()
	t.Cleanup(srv.Close)
	return srv
}

func newClient(t *testing.T, srv *httptest.Server, p pki, tweak ...func(*Config)) Client {
	t.Helper()
	cfg := Config{Server: srv.URL, CAData: p.caPEM, TLSServerName: "localhost", BearerToken: bearer, Timeout: 5 * time.Second}
	for _, f := range tweak {
		f(&cfg)
	}
	c, err := NewClient(cfg)
	if err != nil {
		t.Fatal(err)
	}
	return c
}

func status(w http.ResponseWriter, code int, reason, message string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(map[string]any{"kind": "Status", "status": "Failure", "reason": reason, "message": message, "code": code})
}

func reply(w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(v)
}

// ---------------------------------------------------------------- TLS and credentials

func TestTheBearerTokenIsSentAndTheVersionIsRead(t *testing.T) {
	p := newPKI(t)
	var auth, path string
	srv := serve(t, p, func(w http.ResponseWriter, r *http.Request) {
		auth, path = r.Header.Get("Authorization"), r.URL.Path
		reply(w, map[string]string{"gitVersion": "v1.37.0"})
	})
	v, err := newClient(t, srv, p).ServerVersion(bg)
	if err != nil || v != "v1.37.0" || path != "/version" || auth != "Bearer "+bearer {
		t.Fatalf("version %q err %v path %q auth %q", v, err, path, auth)
	}
}

func TestTLSServerNameLetsAnIPAddressVerifyAgainstTheNameTheCertificateHas(t *testing.T) {
	p := newPKI(t)
	srv := serve(t, p, func(w http.ResponseWriter, r *http.Request) { reply(w, map[string]string{"gitVersion": "v1"}) })

	if _, err := newClient(t, srv, p).ServerVersion(bg); err != nil {
		t.Fatalf("with tls-server-name localhost: %v", err)
	}
	_, err := newClient(t, srv, p, func(c *Config) { c.TLSServerName = "" }).ServerVersion(bg)
	if err == nil {
		t.Fatal("the certificate has no IP address, so verifying against 127.0.0.1 must fail")
	}
	_, err = newClient(t, srv, p, func(c *Config) { c.TLSServerName = "someone-else.example" }).ServerVersion(bg)
	if err == nil {
		t.Fatal("a different server name must fail verification")
	}
}

func TestAServerNotSignedByTheGivenCAIsRefused(t *testing.T) {
	p, other := newPKI(t), newPKI(t)
	srv := serve(t, p, func(w http.ResponseWriter, r *http.Request) { reply(w, map[string]string{}) })
	_, err := newClient(t, srv, p, func(c *Config) { c.CAData = other.caPEM }).ServerVersion(bg)
	if err == nil {
		t.Fatal("a certificate from another CA must never be trusted")
	}
}

func TestAClientCertificateIsPresentedWhenTheServerRequiresOne(t *testing.T) {
	p := newPKI(t)
	pool := x509.NewCertPool()
	pool.AddCert(p.ca)
	var subject string
	srv := serve(t, p, func(w http.ResponseWriter, r *http.Request) {
		subject = r.TLS.PeerCertificates[0].Subject.CommonName
		reply(w, map[string]string{"gitVersion": "v1"})
	}, func(s *httptest.Server) {
		s.TLS.ClientAuth = tls.RequireAndVerifyClientCert
		s.TLS.ClientCAs = pool
	})
	certPEM, keyPEM := p.clientCert(t)
	c := newClient(t, srv, p, func(c *Config) { c.BearerToken, c.CertData, c.KeyData = "", certPEM, keyPEM })
	if _, err := c.ServerVersion(bg); err != nil || subject != "kind-admin" {
		t.Fatalf("subject %q err %v", subject, err)
	}
	if _, err := newClient(t, srv, p, func(c *Config) { c.BearerToken = "" }).ServerVersion(bg); err == nil {
		t.Fatal("without a client certificate the handshake must fail")
	}
}

func TestInvalidCredentialMaterialIsRefusedWhenTheClientIsBuilt(t *testing.T) {
	p := newPKI(t)
	certPEM, keyPEM := p.clientCert(t)
	for name, cfg := range map[string]Config{
		"CA that is not PEM":         {Server: "https://127.0.0.1:1", CAData: []byte("not pem")},
		"certificate without a key":  {Server: "https://127.0.0.1:1", CertData: certPEM},
		"key without a certificate":  {Server: "https://127.0.0.1:1", KeyData: keyPEM},
		"mismatched certificate/key": {Server: "https://127.0.0.1:1", CertData: certPEM, KeyData: p.caPEM},
		"no scheme":                  {Server: "127.0.0.1:1"},
		"unsupported scheme":         {Server: "ftp://127.0.0.1:1"},
		"empty":                      {},
	} {
		if _, err := NewClient(cfg); err == nil {
			t.Errorf("%s: want an error", name)
		}
	}
}

func TestARedirectIsNeverFollowedSoTheCredentialCannotBeSentElsewhere(t *testing.T) {
	p := newPKI(t)
	leaked := false
	elsewhere := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		leaked = r.Header.Get("Authorization") != ""
		reply(w, map[string]string{"gitVersion": "v1"})
	}))
	t.Cleanup(elsewhere.Close)
	srv := serve(t, p, func(w http.ResponseWriter, r *http.Request) { http.Redirect(w, r, elsewhere.URL, http.StatusFound) })

	_, err := newClient(t, srv, p).ServerVersion(bg)
	if err == nil || leaked {
		t.Fatalf("err %v leaked %v", err, leaked)
	}
}

func TestAConnectionFailureNeverMentionsTheToken(t *testing.T) {
	p := newPKI(t)
	srv := serve(t, p, func(w http.ResponseWriter, r *http.Request) {})
	c := newClient(t, srv, p)
	srv.Close()
	_, err := c.ServerVersion(bg)
	if err == nil || strings.Contains(err.Error(), bearer) {
		t.Fatalf("err = %v", err)
	}
}

func TestTheRequestTimeoutAndContextAreHonoured(t *testing.T) {
	p := newPKI(t)
	release := make(chan struct{})
	srv := serve(t, p, func(w http.ResponseWriter, r *http.Request) { <-release })
	t.Cleanup(func() { close(release) })
	c := newClient(t, srv, p, func(c *Config) { c.Timeout = 150 * time.Millisecond })
	start := time.Now()
	if _, err := c.ServerVersion(bg); err == nil || time.Since(start) > 3*time.Second {
		t.Fatalf("err %v after %s", err, time.Since(start))
	}
	ctx, cancel := context.WithCancel(bg)
	cancel()
	if _, err := newClient(t, srv, p).ServerVersion(ctx); err == nil {
		t.Fatal("a cancelled context must fail the request")
	}
}

func TestAnOversizedResponseIsRefused(t *testing.T) {
	p := newPKI(t)
	srv := serve(t, p, func(w http.ResponseWriter, r *http.Request) {
		chunk := []byte(strings.Repeat("a", 1<<20))
		for i := 0; i < 34; i++ {
			if _, err := w.Write(chunk); err != nil {
				return
			}
		}
	})
	if _, err := newClient(t, srv, p).ServerVersion(bg); err == nil || !strings.Contains(err.Error(), "too large") {
		t.Fatalf("err = %v", err)
	}
}

// ---------------------------------------------------------------- reading, creating, updating, deleting

const clusterRoleJSON = `{
  "apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRole",
  "metadata": {"name": "resilience-engine-reader", "resourceVersion": "42", "uid": "abc",
               "labels": {"app.kubernetes.io/managed-by": "resilience-engine-extension", "team": "x"},
               "annotations": {"note": "added by an administrator"}, "finalizers": ["example.com/keep"]},
  "rules": [{"apiGroups": [""], "resources": ["nodes"], "verbs": ["get", "list"]}],
  "aggregationRule": {"clusterRoleSelectors": [{"matchLabels": {"a": "b"}}]}
}`

func TestGetReadsTheFieldsTheHelperManages(t *testing.T) {
	p := newPKI(t)
	var path string
	srv := serve(t, p, func(w http.ResponseWriter, r *http.Request) {
		path = r.URL.Path
		io.WriteString(w, clusterRoleJSON)
	})
	o, err := newClient(t, srv, p).Get(bg, ClusterRoles, "ignored-for-cluster-scope", "resilience-engine-reader")
	if err != nil {
		t.Fatal(err)
	}
	if path != "/apis/rbac.authorization.k8s.io/v1/clusterroles/resilience-engine-reader" {
		t.Fatalf("path = %s", path)
	}
	if o.Name != "resilience-engine-reader" || o.Labels["team"] != "x" || !o.AggregationRule ||
		len(o.Rules) != 1 || o.Rules[0].Resources[0] != "nodes" || o.Rules[0].Verbs[1] != "list" {
		t.Fatalf("object = %+v", o)
	}
}

func TestANullAggregationRuleCountsAsAbsent(t *testing.T) {
	o, err := objectFromRaw(map[string]any{"metadata": map[string]any{"name": "x"}, "aggregationRule": nil})
	if err != nil || o.AggregationRule {
		t.Fatalf("object %+v err %v", o, err)
	}
}

func TestUpdateChangesOnlyWhatTheHelperManagesAndKeepsEverythingElse(t *testing.T) {
	p := newPKI(t)
	var put map[string]any
	var method, path string
	srv := serve(t, p, func(w http.ResponseWriter, r *http.Request) {
		method, path = r.Method, r.URL.Path
		if r.Method == http.MethodGet {
			io.WriteString(w, clusterRoleJSON)
			return
		}
		json.NewDecoder(r.Body).Decode(&put)
		reply(w, put)
	})
	c := newClient(t, srv, p)
	o, err := c.Get(bg, ClusterRoles, "", "resilience-engine-reader")
	if err != nil {
		t.Fatal(err)
	}
	o.Labels = map[string]string{"app.kubernetes.io/managed-by": "resilience-engine-extension", "team": "x"}
	o.Rules = []PolicyRule{{APIGroups: []string{""}, Resources: []string{"nodes", "pods"}, Verbs: []string{"get", "list"}}}
	o.AggregationRule = false
	if err := c.Update(bg, ClusterRoles, o); err != nil {
		t.Fatal(err)
	}

	if method != http.MethodPut || path != "/apis/rbac.authorization.k8s.io/v1/clusterroles/resilience-engine-reader" {
		t.Fatalf("%s %s", method, path)
	}
	meta := put["metadata"].(map[string]any)
	if meta["resourceVersion"] != "42" || meta["uid"] != "abc" {
		t.Fatalf("resourceVersion/uid must be sent back so a concurrent change is detected: %v", meta)
	}
	if meta["annotations"].(map[string]any)["note"] != "added by an administrator" || meta["finalizers"].([]any)[0] != "example.com/keep" {
		t.Fatalf("fields the helper does not model were dropped: %v", meta)
	}
	if _, present := put["aggregationRule"]; present {
		t.Fatal("the aggregation rule must be removed when the helper clears it")
	}
	rules := put["rules"].([]any)
	if len(rules) != 1 || len(rules[0].(map[string]any)["resources"].([]any)) != 2 {
		t.Fatalf("rules = %v", rules)
	}
}

func TestUpdateKeepsAnAggregationRuleItWasNotAskedToClear(t *testing.T) {
	raw := map[string]any{"metadata": map[string]any{"name": "x"}, "aggregationRule": map[string]any{"k": "v"}}
	o, _ := objectFromRaw(raw)
	body := o.updateBody(ClusterRoles)
	if _, present := body["aggregationRule"]; !present {
		t.Fatal("an aggregation rule the helper left set must stay")
	}
}

func TestUpdateDoesNotMutateTheObjectItWasGiven(t *testing.T) {
	raw := map[string]any{"metadata": map[string]any{"name": "x", "labels": map[string]any{"a": "b"}}}
	o, _ := objectFromRaw(raw)
	o.Labels = map[string]string{"c": "d"}
	o.updateBody(Namespaces)
	if !reflect.DeepEqual(raw["metadata"].(map[string]any)["labels"], map[string]any{"a": "b"}) {
		t.Fatalf("raw metadata was changed: %v", raw["metadata"])
	}
}

func TestEmptyLabelsRemoveTheLabelsKey(t *testing.T) {
	raw := map[string]any{"metadata": map[string]any{"name": "x", "labels": map[string]any{"a": "b"}}}
	o, _ := objectFromRaw(raw)
	o.Labels = nil
	if _, present := o.updateBody(ServiceAccounts)["metadata"].(map[string]any)["labels"]; present {
		t.Fatal("labels should be removed when none remain")
	}
}

func TestAnObjectThatWasNotReadCannotBeUpdated(t *testing.T) {
	p := newPKI(t)
	srv := serve(t, p, func(w http.ResponseWriter, r *http.Request) { t.Error("no request expected") })
	if err := newClient(t, srv, p).Update(bg, Namespaces, &Object{Name: "x"}); err == nil {
		t.Fatal("want an error")
	}
}

func TestCreateSendsTheKindSpecificBody(t *testing.T) {
	p := newPKI(t)
	type sent struct {
		method, path string
		body         map[string]any
	}
	var got []sent
	srv := serve(t, p, func(w http.ResponseWriter, r *http.Request) {
		var body map[string]any
		json.NewDecoder(r.Body).Decode(&body)
		got = append(got, sent{r.Method, r.URL.Path, body})
		w.WriteHeader(http.StatusCreated)
		reply(w, body)
	})
	c := newClient(t, srv, p)
	labels := map[string]string{"app.kubernetes.io/managed-by": "resilience-engine-extension"}

	must := func(err error) {
		t.Helper()
		if err != nil {
			t.Fatal(err)
		}
	}
	must(c.Create(bg, Namespaces, &Object{Name: "resilience-engine", Labels: labels}))
	must(c.Create(bg, ServiceAccounts, &Object{Name: "reader", Namespace: "resilience-engine", Labels: labels}))
	must(c.Create(bg, ClusterRoles, &Object{Name: "reader", Labels: labels, Rules: []PolicyRule{{APIGroups: []string{""}, Resources: []string{"pods"}, Verbs: []string{"list"}}}}))
	must(c.Create(bg, ClusterRoleBindings, &Object{Name: "reader", Labels: labels,
		RoleRef:  RoleRef{APIGroup: RBACGroup, Kind: "ClusterRole", Name: "reader"},
		Subjects: []Subject{{Kind: ServiceAccountKind, Name: "reader", Namespace: "resilience-engine"}}}))

	wantPaths := []string{"/api/v1/namespaces", "/api/v1/namespaces/resilience-engine/serviceaccounts",
		"/apis/rbac.authorization.k8s.io/v1/clusterroles", "/apis/rbac.authorization.k8s.io/v1/clusterrolebindings"}
	for i, want := range wantPaths {
		if got[i].method != http.MethodPost || got[i].path != want {
			t.Errorf("request %d: %s %s, want POST %s", i, got[i].method, got[i].path, want)
		}
	}
	if got[0].body["kind"] != "Namespace" || got[0].body["apiVersion"] != "v1" {
		t.Errorf("namespace body = %v", got[0].body)
	}
	if got[1].body["metadata"].(map[string]any)["namespace"] != "resilience-engine" {
		t.Errorf("service account body = %v", got[1].body)
	}
	if got[2].body["apiVersion"] != "rbac.authorization.k8s.io/v1" || len(got[2].body["rules"].([]any)) != 1 {
		t.Errorf("cluster role body = %v", got[2].body)
	}
	binding := got[3].body
	if binding["roleRef"].(map[string]any)["name"] != "reader" || binding["subjects"].([]any)[0].(map[string]any)["namespace"] != "resilience-engine" {
		t.Errorf("binding body = %v", binding)
	}
}

func TestDeleteAndListUseTheRightPaths(t *testing.T) {
	p := newPKI(t)
	var calls []string
	srv := serve(t, p, func(w http.ResponseWriter, r *http.Request) {
		calls = append(calls, r.Method+" "+r.URL.Path)
		if r.Method == http.MethodGet {
			reply(w, map[string]any{"items": []any{
				map[string]any{"metadata": map[string]any{"name": "a", "namespace": "ns1"}, "subjects": []any{map[string]any{"kind": "ServiceAccount", "name": "s", "namespace": "n"}}},
				map[string]any{"metadata": map[string]any{"name": "b", "namespace": "ns2"}},
			}})
			return
		}
		reply(w, map[string]string{"status": "Success"})
	})
	c := newClient(t, srv, p)

	all, err := c.List(bg, RoleBindings, "")
	if err != nil || len(all) != 2 || all[0].Namespace != "ns1" || all[0].Subjects[0].Name != "s" {
		t.Fatalf("items %+v err %v", all, err)
	}
	if _, err := c.List(bg, Pods, "resilience-engine"); err != nil {
		t.Fatal(err)
	}
	if err := c.Delete(bg, ServiceAccounts, "resilience-engine", "reader"); err != nil {
		t.Fatal(err)
	}
	if err := c.Delete(bg, Namespaces, "", "resilience-engine"); err != nil {
		t.Fatal(err)
	}
	want := []string{
		"GET /apis/rbac.authorization.k8s.io/v1/rolebindings",
		"GET /api/v1/namespaces/resilience-engine/pods",
		"DELETE /api/v1/namespaces/resilience-engine/serviceaccounts/reader",
		"DELETE /api/v1/namespaces/resilience-engine",
	}
	if !reflect.DeepEqual(calls, want) {
		t.Fatalf("calls = %v", calls)
	}
}

func TestANamedNamespacedObjectNeedsANamespace(t *testing.T) {
	p := newPKI(t)
	srv := serve(t, p, func(w http.ResponseWriter, r *http.Request) { t.Error("no request expected") })
	c := newClient(t, srv, p)
	if _, err := c.Get(bg, ServiceAccounts, "", "reader"); err == nil {
		t.Fatal("want an error")
	}
	if err := c.Delete(bg, ServiceAccounts, "", "reader"); err == nil {
		t.Fatal("want an error")
	}
	if err := c.Create(bg, ServiceAccounts, &Object{Name: "reader"}); err == nil {
		t.Fatal("want an error")
	}
}

func TestNamesAreEscapedInPaths(t *testing.T) {
	p := newPKI(t)
	var raw string
	srv := serve(t, p, func(w http.ResponseWriter, r *http.Request) {
		raw = r.URL.EscapedPath()
		reply(w, map[string]any{"metadata": map[string]any{"name": "x"}})
	})
	if _, err := newClient(t, srv, p).Get(bg, ClusterRoles, "", "a/b c"); err != nil {
		t.Fatal(err)
	}
	if !strings.HasSuffix(raw, "/clusterroles/a%2Fb%20c") {
		t.Fatalf("path = %s", raw)
	}
}

// ---------------------------------------------------------------- errors

func TestEachKindOfServerErrorIsToldApart(t *testing.T) {
	p := newPKI(t)
	cases := []struct {
		name         string
		code         int
		reason       string
		body         string
		isNotFound   bool
		isForbidden  bool
		isExists     bool
		isConflict   bool
		isUnauthPred bool
	}{
		{name: "not found", code: 404, reason: "NotFound", isNotFound: true},
		{name: "forbidden", code: 403, reason: "Forbidden", isForbidden: true},
		{name: "already exists", code: 409, reason: "AlreadyExists", isExists: true},
		{name: "conflict", code: 409, reason: "Conflict", isConflict: true},
		{name: "unauthorized", code: 401, reason: "Unauthorized", isUnauthPred: true},
		{name: "not found without a Status body", code: 404, body: "<html>nope</html>", isNotFound: true},
		{name: "forbidden without a Status body", code: 403, body: "denied", isForbidden: true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv := serve(t, p, func(w http.ResponseWriter, r *http.Request) {
				if tc.body != "" {
					w.WriteHeader(tc.code)
					io.WriteString(w, tc.body)
					return
				}
				status(w, tc.code, tc.reason, "the server's own message")
			})
			_, err := newClient(t, srv, p).Get(bg, Namespaces, "", "x")
			got := []bool{IsNotFound(err), IsForbidden(err), IsAlreadyExists(err), IsConflict(err), IsUnauthorized(err)}
			want := []bool{tc.isNotFound, tc.isForbidden, tc.isExists, tc.isConflict, tc.isUnauthPred}
			if !reflect.DeepEqual(got, want) {
				t.Fatalf("[notFound forbidden exists conflict unauthorized] = %v, want %v (err %v)", got, want, err)
			}
			if tc.body == "" && ReasonForError(err) != tc.reason {
				t.Fatalf("reason = %q", ReasonForError(err))
			}
		})
	}
}

func TestPlainErrorsAreNotAPIErrors(t *testing.T) {
	err := context.DeadlineExceeded
	if IsNotFound(err) || IsForbidden(err) || IsAlreadyExists(err) || IsConflict(err) || IsUnauthorized(err) || ReasonForError(err) != "" {
		t.Fatal("only server responses are API errors")
	}
	if got := (&APIError{Code: 500}).Error(); !strings.Contains(got, "500") {
		t.Fatalf("message = %q", got)
	}
}

// ---------------------------------------------------------------- tokens

func TestCreateTokenAsksForTheLifetimeAndReadsTheRealExpiry(t *testing.T) {
	p := newPKI(t)
	var path string
	var body map[string]any
	expires := time.Date(2026, 10, 19, 20, 0, 0, 0, time.UTC)
	srv := serve(t, p, func(w http.ResponseWriter, r *http.Request) {
		path = r.URL.Path
		json.NewDecoder(r.Body).Decode(&body)
		w.WriteHeader(http.StatusCreated)
		reply(w, map[string]any{"status": map[string]any{"token": "issued", "expirationTimestamp": expires.Format(time.RFC3339)}})
	})
	tok, err := newClient(t, srv, p).CreateToken(bg, "resilience-engine", "reader", 28800)
	if err != nil || tok.Value != "issued" || !tok.ExpiresAt.Equal(expires) {
		t.Fatalf("token %+v err %v", tok, err)
	}
	if path != "/api/v1/namespaces/resilience-engine/serviceaccounts/reader/token" {
		t.Fatalf("path = %s", path)
	}
	if body["kind"] != "TokenRequest" || body["apiVersion"] != "authentication.k8s.io/v1" ||
		body["spec"].(map[string]any)["expirationSeconds"] != float64(28800) {
		t.Fatalf("body = %v", body)
	}
}

func TestAMissingExpiryStaysZeroAndAnUnreadableOneIsAnError(t *testing.T) {
	p := newPKI(t)
	for name, tc := range map[string]struct {
		status  map[string]any
		wantErr bool
	}{
		"missing":    {status: map[string]any{"token": "t"}},
		"unreadable": {status: map[string]any{"token": "t", "expirationTimestamp": "tomorrow"}, wantErr: true},
	} {
		t.Run(name, func(t *testing.T) {
			srv := serve(t, p, func(w http.ResponseWriter, r *http.Request) { reply(w, map[string]any{"status": tc.status}) })
			tok, err := newClient(t, srv, p).CreateToken(bg, "n", "s", 60)
			if (err != nil) != tc.wantErr || (err == nil && !tok.ExpiresAt.IsZero()) {
				t.Fatalf("token %+v err %v", tok, err)
			}
		})
	}
}

func TestTheTokenRequestFailsWithTheServersReason(t *testing.T) {
	p := newPKI(t)
	srv := serve(t, p, func(w http.ResponseWriter, r *http.Request) {
		status(w, 403, "Forbidden", "cannot create serviceaccounts/token")
	})
	_, err := newClient(t, srv, p).CreateToken(bg, "n", "s", 60)
	if !IsForbidden(err) {
		t.Fatalf("err = %v", err)
	}
}

func TestNodesAreListedWithTheProviderIDTheyReport(t *testing.T) {
	p := newPKI(t)
	var path string
	srv := serve(t, p, func(w http.ResponseWriter, r *http.Request) {
		path = r.URL.Path
		reply(w, map[string]any{"items": []any{
			map[string]any{"metadata": map[string]any{"name": "desktop-worker"}, "spec": map[string]any{"providerID": "kind://docker/desktop/desktop-worker"}},
			map[string]any{"metadata": map[string]any{"name": "bare"}, "spec": map[string]any{}},
		}})
	})

	nodes, err := newClient(t, srv, p).List(bg, Nodes, "")

	if err != nil || path != "/api/v1/nodes" || len(nodes) != 2 {
		t.Fatalf("path %s nodes %+v err %v", path, nodes, err)
	}
	if nodes[0].ProviderID != "kind://docker/desktop/desktop-worker" || nodes[1].ProviderID != "" {
		t.Fatalf("provider ids = %q, %q (an absent one must stay empty, never guessed)", nodes[0].ProviderID, nodes[1].ProviderID)
	}
}
