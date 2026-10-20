package kube

import (
	"encoding/base64"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"

	"go.yaml.in/yaml/v3"
)

// Kubeconfig is the user's kubeconfig as the standard tools see it: every file in the precedence
// list merged, the first file that defines a name winning, and the first file that sets
// current-context choosing the current one.
//
// Only the credential forms a local kind cluster uses are supported: an embedded or file CA, a client
// certificate and key, or a bearer token. Credential plugins (exec, auth-provider) are refused rather
// than run, because running one would execute a program named in a file the helper does not control.
type Kubeconfig struct {
	current  string
	clusters map[string]cluster
	users    map[string]user
	contexts map[string]kubeContext
}

type cluster struct {
	dir                   string // where the defining file lives, for relative paths
	Server                string `yaml:"server"`
	TLSServerName         string `yaml:"tls-server-name"`
	CertificateAuthority  string `yaml:"certificate-authority"`
	CertificateAuthorityD string `yaml:"certificate-authority-data"`
	InsecureSkipTLSVerify bool   `yaml:"insecure-skip-tls-verify"`
	ProxyURL              string `yaml:"proxy-url"`
}

type user struct {
	dir                   string
	ClientCertificate     string `yaml:"client-certificate"`
	ClientCertificateData string `yaml:"client-certificate-data"`
	ClientKey             string `yaml:"client-key"`
	ClientKeyData         string `yaml:"client-key-data"`
	Token                 string `yaml:"token"`
	TokenFile             string `yaml:"tokenFile"`
	Username              string `yaml:"username"`
	Exec                  any    `yaml:"exec"`
	AuthProvider          any    `yaml:"auth-provider"`
}

type kubeContext struct {
	Cluster string `yaml:"cluster"`
	User    string `yaml:"user"`
}

type kubeFile struct {
	CurrentContext string `yaml:"current-context"`
	Clusters       []struct {
		Name    string  `yaml:"name"`
		Cluster cluster `yaml:"cluster"`
	} `yaml:"clusters"`
	Users []struct {
		Name string `yaml:"name"`
		User user   `yaml:"user"`
	} `yaml:"users"`
	Contexts []struct {
		Name    string      `yaml:"name"`
		Context kubeContext `yaml:"context"`
	} `yaml:"contexts"`
}

// LoadKubeconfig reads the kubeconfig. When explicit is set it is the only file and must be readable.
// Otherwise every file in precedence that exists is merged in order; files that do not exist are
// skipped, as kubectl does, and a missing kubeconfig simply has no contexts. Errors never include the
// file's content.
func LoadKubeconfig(explicit string, precedence []string) (*Kubeconfig, error) {
	paths := precedence
	if explicit != "" {
		paths = []string{explicit}
	}
	k := &Kubeconfig{clusters: map[string]cluster{}, users: map[string]user{}, contexts: map[string]kubeContext{}}
	for _, path := range paths {
		data, err := os.ReadFile(path)
		if err != nil {
			if explicit == "" && errors.Is(err, os.ErrNotExist) {
				continue
			}
			return nil, errors.New("could not read the kubeconfig file")
		}
		var file kubeFile
		if err := yaml.Unmarshal(data, &file); err != nil {
			return nil, errors.New("the kubeconfig file is not valid")
		}
		dir := filepath.Dir(path)
		if k.current == "" {
			k.current = file.CurrentContext
		}
		for _, c := range file.Clusters {
			if _, taken := k.clusters[c.Name]; !taken {
				c.Cluster.dir = dir
				k.clusters[c.Name] = c.Cluster
			}
		}
		for _, u := range file.Users {
			if _, taken := k.users[u.Name]; !taken {
				u.User.dir = dir
				k.users[u.Name] = u.User
			}
		}
		for _, c := range file.Contexts {
			if _, taken := k.contexts[c.Name]; !taken {
				k.contexts[c.Name] = c.Context
			}
		}
	}
	return k, nil
}

// CurrentContext is the context kubectl would use by default, or "".
func (k *Kubeconfig) CurrentContext() string { return k.current }

// ContextNames lists the contexts, sorted.
func (k *Kubeconfig) ContextNames() []string {
	names := make([]string, 0, len(k.contexts))
	for name := range k.contexts {
		names = append(names, name)
	}
	sort.Strings(names)
	return names
}

// Server is the API server URL of a context's cluster, or "" when the context or cluster is unknown.
func (k *Kubeconfig) Server(contextName string) string {
	return k.clusters[k.contexts[contextName].Cluster].Server
}

// Config resolves one context into what is needed to connect. It reads referenced files but runs
// nothing. Anything that cannot be honoured safely is an error, never a silent downgrade.
func (k *Kubeconfig) Config(contextName string) (Config, error) {
	c, ok := k.contexts[contextName]
	if !ok {
		return Config{}, errors.New("that context is not in the kubeconfig")
	}
	cl, ok := k.clusters[c.Cluster]
	if !ok || cl.Server == "" {
		return Config{}, errors.New("the context has no cluster address")
	}
	us := k.users[c.User]

	switch {
	case cl.InsecureSkipTLSVerify:
		return Config{}, errors.New("insecure-skip-tls-verify is not supported: TLS is always verified")
	case cl.ProxyURL != "":
		return Config{}, errors.New("a proxy-url in the kubeconfig is not supported")
	case us.Exec != nil || us.AuthProvider != nil:
		return Config{}, errors.New("a credential plugin (exec or auth-provider) is not supported, and is never run")
	case us.Username != "":
		return Config{}, errors.New("basic authentication is not supported")
	}

	out := Config{Server: cl.Server, TLSServerName: cl.TLSServerName, BearerToken: us.Token}
	var err error
	if out.CAData, err = dataOrFile(cl.CertificateAuthorityD, cl.CertificateAuthority, cl.dir, "certificate authority"); err != nil {
		return Config{}, err
	}
	if out.CertData, err = dataOrFile(us.ClientCertificateData, us.ClientCertificate, us.dir, "client certificate"); err != nil {
		return Config{}, err
	}
	if out.KeyData, err = dataOrFile(us.ClientKeyData, us.ClientKey, us.dir, "client key"); err != nil {
		return Config{}, err
	}
	if out.BearerToken == "" && us.TokenFile != "" {
		raw, err := os.ReadFile(resolve(us.TokenFile, us.dir))
		if err != nil {
			return Config{}, errors.New("could not read the token file")
		}
		out.BearerToken = string(trimNewline(raw))
	}
	return out, nil
}

// dataOrFile prefers embedded base64 data, as kubectl does, and otherwise reads the file.
func dataOrFile(encoded, file, dir, what string) ([]byte, error) {
	switch {
	case encoded != "":
		raw, err := base64.StdEncoding.DecodeString(encoded)
		if err != nil {
			return nil, fmt.Errorf("the %s in the kubeconfig is not valid base64", what)
		}
		return raw, nil
	case file != "":
		raw, err := os.ReadFile(resolve(file, dir))
		if err != nil {
			return nil, fmt.Errorf("could not read the %s file", what)
		}
		return raw, nil
	}
	return nil, nil
}

// resolve makes a path from a kubeconfig relative to the folder of the file that named it.
func resolve(path, dir string) string {
	if filepath.IsAbs(path) {
		return path
	}
	return filepath.Join(dir, path)
}

func trimNewline(b []byte) []byte {
	for len(b) > 0 && (b[len(b)-1] == '\n' || b[len(b)-1] == '\r') {
		b = b[:len(b)-1]
	}
	return b
}
