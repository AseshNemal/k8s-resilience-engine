package kube

import (
	"bytes"
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// maxResponseBytes bounds what is read from one response, so a misbehaving server cannot exhaust memory.
const maxResponseBytes = 32 << 20

// Config is everything needed to talk to one API server. It holds credentials, so it is never
// printed or logged; callers keep it inside the helper process.
type Config struct {
	Server        string // for example https://127.0.0.1:58227
	CAData        []byte // PEM; when empty the system roots are used
	CertData      []byte // PEM client certificate, with KeyData
	KeyData       []byte // PEM client key
	BearerToken   string
	TLSServerName string // the name the server certificate is verified against, when it differs from the host
	Timeout       time.Duration
}

type httpClient struct {
	base   string
	bearer string
	http   *http.Client
}

// NewClient builds a Client for cfg. TLS verification is always on.
func NewClient(cfg Config) (Client, error) {
	u, err := url.Parse(cfg.Server)
	if err != nil || u.Host == "" || (u.Scheme != "https" && u.Scheme != "http") {
		return nil, errors.New("the API server address is not valid")
	}
	tlsConfig := &tls.Config{MinVersion: tls.VersionTLS12, ServerName: cfg.TLSServerName}
	if len(cfg.CAData) > 0 {
		pool := x509.NewCertPool()
		if !pool.AppendCertsFromPEM(cfg.CAData) {
			return nil, errors.New("the certificate authority is not valid PEM")
		}
		tlsConfig.RootCAs = pool
	}
	if len(cfg.CertData) > 0 || len(cfg.KeyData) > 0 {
		pair, err := tls.X509KeyPair(cfg.CertData, cfg.KeyData)
		if err != nil {
			return nil, errors.New("the client certificate and key are not valid")
		}
		tlsConfig.Certificates = []tls.Certificate{pair}
	}
	transport := &http.Transport{
		Proxy:                 http.ProxyFromEnvironment, // loopback addresses are never proxied
		TLSClientConfig:       tlsConfig,
		TLSHandshakeTimeout:   cfg.Timeout,
		ResponseHeaderTimeout: cfg.Timeout,
		DisableKeepAlives:     true, // a short-lived command: nothing to reuse
	}
	return &httpClient{
		base:   strings.TrimRight(cfg.Server, "/"),
		bearer: cfg.BearerToken,
		http: &http.Client{
			Transport: transport,
			Timeout:   cfg.Timeout,
			// The API server does not redirect. Following one would send the credential elsewhere.
			CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
		},
	}, nil
}

func (r Resource) path(namespace, name string) (string, error) {
	var b strings.Builder
	if r.Group == "" {
		b.WriteString("/api/" + r.Version)
	} else {
		b.WriteString("/apis/" + r.Group + "/" + r.Version)
	}
	if r.Namespaced && namespace != "" {
		b.WriteString("/namespaces/" + url.PathEscape(namespace))
	}
	b.WriteString("/" + r.Plural)
	if name != "" {
		if r.Namespaced && namespace == "" {
			return "", fmt.Errorf("a %s needs a namespace", r.Kind)
		}
		b.WriteString("/" + url.PathEscape(name))
	}
	return b.String(), nil
}

func (c *httpClient) do(ctx context.Context, method, path string, in, out any) error {
	var body io.Reader
	if in != nil {
		encoded, err := json.Marshal(in)
		if err != nil {
			return err
		}
		body = bytes.NewReader(encoded)
	}
	req, err := http.NewRequestWithContext(ctx, method, c.base+path, body)
	if err != nil {
		return err
	}
	req.Header.Set("Accept", "application/json")
	if in != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	if c.bearer != "" {
		req.Header.Set("Authorization", "Bearer "+c.bearer)
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(io.LimitReader(resp.Body, maxResponseBytes+1))
	if err != nil {
		return err
	}
	if len(data) > maxResponseBytes {
		return errors.New("the API server's response is too large")
	}
	if resp.StatusCode < 200 || resp.StatusCode > 299 {
		return parseStatus(resp.StatusCode, data)
	}
	if out == nil {
		return nil
	}
	return json.Unmarshal(data, out)
}

func (c *httpClient) ServerVersion(ctx context.Context) (string, error) {
	var v struct {
		GitVersion string `json:"gitVersion"`
	}
	if err := c.do(ctx, http.MethodGet, "/version", nil, &v); err != nil {
		return "", err
	}
	return v.GitVersion, nil
}

func (c *httpClient) Get(ctx context.Context, r Resource, namespace, name string) (*Object, error) {
	path, err := r.path(namespace, name)
	if err != nil {
		return nil, err
	}
	var raw map[string]any
	if err := c.do(ctx, http.MethodGet, path, nil, &raw); err != nil {
		return nil, err
	}
	return objectFromRaw(raw)
}

func (c *httpClient) List(ctx context.Context, r Resource, namespace string) ([]Object, error) {
	path, err := r.path(namespace, "")
	if err != nil {
		return nil, err
	}
	var list struct {
		Items []map[string]any `json:"items"`
	}
	if err := c.do(ctx, http.MethodGet, path, nil, &list); err != nil {
		return nil, err
	}
	out := make([]Object, 0, len(list.Items))
	for _, raw := range list.Items {
		o, err := objectFromRaw(raw)
		if err != nil {
			return nil, err
		}
		out = append(out, *o)
	}
	return out, nil
}

func (c *httpClient) Create(ctx context.Context, r Resource, o *Object) error {
	if r.Namespaced && o.Namespace == "" {
		return fmt.Errorf("a %s needs a namespace", r.Kind)
	}
	path, err := r.path(o.Namespace, "")
	if err != nil {
		return err
	}
	return c.do(ctx, http.MethodPost, path, o.createBody(r), nil)
}

func (c *httpClient) Update(ctx context.Context, r Resource, o *Object) error {
	if o.raw == nil {
		return errors.New("only an object returned by Get can be updated")
	}
	path, err := r.path(o.Namespace, o.Name)
	if err != nil {
		return err
	}
	return c.do(ctx, http.MethodPut, path, o.updateBody(r), nil)
}

func (c *httpClient) Delete(ctx context.Context, r Resource, namespace, name string) error {
	path, err := r.path(namespace, name)
	if err != nil {
		return err
	}
	return c.do(ctx, http.MethodDelete, path, nil, nil)
}

func (c *httpClient) CreateToken(ctx context.Context, namespace, serviceAccount string, expirationSeconds int64) (Token, error) {
	path, err := ServiceAccounts.path(namespace, serviceAccount)
	if err != nil {
		return Token{}, err
	}
	request := map[string]any{
		"apiVersion": "authentication.k8s.io/v1",
		"kind":       "TokenRequest",
		"spec":       map[string]any{"expirationSeconds": expirationSeconds},
	}
	var reply struct {
		Status struct {
			Token               string `json:"token"`
			ExpirationTimestamp string `json:"expirationTimestamp"`
		} `json:"status"`
	}
	if err := c.do(ctx, http.MethodPost, path+"/token", request, &reply); err != nil {
		return Token{}, err
	}
	token := Token{Value: reply.Status.Token}
	if reply.Status.ExpirationTimestamp != "" {
		expires, err := time.Parse(time.RFC3339, reply.Status.ExpirationTimestamp)
		if err != nil {
			return Token{}, errors.New("the cluster reported an expiry the helper cannot read")
		}
		token.ExpiresAt = expires
	}
	return token, nil
}

// ---- translating between Object and the server's JSON --------------------------------------------

func objectFromRaw(raw map[string]any) (*Object, error) {
	encoded, err := json.Marshal(raw)
	if err != nil {
		return nil, err
	}
	var wire struct {
		Metadata struct {
			Name      string            `json:"name"`
			Namespace string            `json:"namespace"`
			Labels    map[string]string `json:"labels"`
		} `json:"metadata"`
		Spec struct {
			ProviderID string `json:"providerID"`
		} `json:"spec"`
		Rules           []PolicyRule    `json:"rules"`
		AggregationRule json.RawMessage `json:"aggregationRule"`
		RoleRef         RoleRef         `json:"roleRef"`
		Subjects        []Subject       `json:"subjects"`
	}
	if err := json.Unmarshal(encoded, &wire); err != nil {
		return nil, err
	}
	aggregation := len(wire.AggregationRule) > 0 && string(wire.AggregationRule) != "null"
	return &Object{
		Name:            wire.Metadata.Name,
		Namespace:       wire.Metadata.Namespace,
		Labels:          wire.Metadata.Labels,
		Rules:           wire.Rules,
		AggregationRule: aggregation,
		RoleRef:         wire.RoleRef,
		Subjects:        wire.Subjects,
		ProviderID:      wire.Spec.ProviderID,
		raw:             raw,
	}, nil
}

// createBody is the object a Create sends.
func (o *Object) createBody(r Resource) map[string]any {
	metadata := map[string]any{"name": o.Name}
	if r.Namespaced && o.Namespace != "" {
		metadata["namespace"] = o.Namespace
	}
	if len(o.Labels) > 0 {
		metadata["labels"] = o.Labels
	}
	body := map[string]any{"apiVersion": r.APIVersion(), "kind": r.Kind, "metadata": metadata}
	o.setManaged(r, body)
	return body
}

// updateBody is the object as read, with only the fields the helper manages replaced. The pointer to
// raw is not shared with the caller: the maps are copied one level so nothing read earlier changes.
func (o *Object) updateBody(r Resource) map[string]any {
	body := make(map[string]any, len(o.raw)+1)
	for k, v := range o.raw {
		body[k] = v
	}
	metadata := map[string]any{}
	if existing, ok := o.raw["metadata"].(map[string]any); ok {
		for k, v := range existing {
			metadata[k] = v
		}
	}
	if len(o.Labels) > 0 {
		metadata["labels"] = o.Labels
	} else {
		delete(metadata, "labels")
	}
	body["metadata"] = metadata
	o.setManaged(r, body)
	return body
}

// setManaged writes the kind-specific fields the helper owns.
func (o *Object) setManaged(r Resource, body map[string]any) {
	switch r.Plural {
	case ClusterRoles.Plural:
		rules := o.Rules
		if rules == nil {
			rules = []PolicyRule{}
		}
		body["rules"] = rules
		if !o.AggregationRule {
			delete(body, "aggregationRule")
		}
	case ClusterRoleBindings.Plural:
		body["roleRef"] = o.RoleRef
		body["subjects"] = o.Subjects
	}
}
