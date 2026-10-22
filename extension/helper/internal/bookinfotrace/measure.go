package bookinfotrace

import (
	"context"
	"encoding/json"
	"fmt"
	"regexp"
	"strconv"
	"strings"
)

// sizeUnits are the units `docker stats` prints, in MiB.
var sizeUnits = map[string]float64{
	"B": 1.0 / 1048576, "KiB": 1.0 / 1024, "MiB": 1, "GiB": 1024,
	"kB": 1000.0 / 1048576, "MB": 1e6 / 1048576, "GB": 1e9 / 1048576,
}

var sizeText = regexp.MustCompile(`^([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]+)$`)

// parseSizeMiB turns "910.2MiB" or "3.825GiB" into MiB, and refuses anything else.
func parseSizeMiB(text string) (float64, error) {
	m := sizeText.FindStringSubmatch(strings.TrimSpace(text))
	if m == nil {
		return 0, fmt.Errorf("unreadable size %q", text)
	}
	unit, ok := sizeUnits[m[2]]
	if !ok {
		return 0, fmt.Errorf("unknown unit %q", m[2])
	}
	n, _ := strconv.ParseFloat(m[1], 64)
	return n * unit, nil
}

// sumUsedMiB adds up the "used" half of every `docker stats` MemUsage line ("910.2MiB / 3.825GiB").
func sumUsedMiB(output string) (float64, error) {
	total, lines := 0.0, 0
	for _, line := range strings.Split(strings.TrimSpace(output), "\n") {
		if strings.TrimSpace(line) == "" {
			continue
		}
		used, _, found := strings.Cut(line, "/")
		if !found {
			return 0, fmt.Errorf("unreadable memory line %q", line)
		}
		mib, err := parseSizeMiB(used)
		if err != nil {
			return 0, err
		}
		total += mib
		lines++
	}
	if lines == 0 {
		return 0, fmt.Errorf("no containers reported")
	}
	return total, nil
}

var swapUsed = regexp.MustCompile(`used = ([0-9.]+)([MG])`)

// parseSwapDarwin reads `sysctl -n vm.swapusage`: "total = 4096.00M  used = 2688.88M  free = 1407.12M  (encrypted)".
func parseSwapDarwin(output string) (float64, error) {
	m := swapUsed.FindStringSubmatch(output)
	if m == nil {
		return 0, fmt.Errorf("unreadable swap usage")
	}
	n, _ := strconv.ParseFloat(m[1], 64)
	if m[2] == "G" {
		n *= 1024
	}
	return n, nil
}

// parseSwapLinux reads /proc/meminfo: SwapTotal - SwapFree, in kB.
func parseSwapLinux(meminfo string) (float64, error) {
	var total, free float64
	var haveTotal, haveFree bool
	for _, line := range strings.Split(meminfo, "\n") {
		fields := strings.Fields(line)
		if len(fields) < 2 {
			continue
		}
		v, err := strconv.ParseFloat(fields[1], 64)
		if err != nil {
			continue
		}
		switch fields[0] {
		case "SwapTotal:":
			total, haveTotal = v, true
		case "SwapFree:":
			free, haveFree = v, true
		}
	}
	if !haveTotal || !haveFree {
		return 0, fmt.Errorf("no swap figures")
	}
	return (total - free) / 1024, nil
}

// HostSwapUsedMiB reads this computer's swap in use. Other systems return nil: unknown, never zero.
func HostSwapUsedMiB(ctx context.Context, d Deps) *float64 {
	var value float64
	var err error
	switch d.GOOS {
	case "darwin":
		if d.Runner == nil {
			return nil
		}
		res, runErr := d.Runner.Run(ctx, "/usr/sbin/sysctl", []string{"-n", "vm.swapusage"}, nil)
		if runErr != nil || res.ExitCode != 0 {
			return nil
		}
		value, err = parseSwapDarwin(string(res.Stdout))
	case "linux":
		if d.ReadFile == nil {
			return nil
		}
		raw, readErr := d.ReadFile("/proc/meminfo")
		if readErr != nil {
			return nil
		}
		value, err = parseSwapLinux(string(raw))
	default:
		return nil
	}
	if err != nil {
		return nil
	}
	return &value
}

// DockerMemory reads the Docker Desktop VM's limit and what its containers use. Either may be nil.
func DockerMemory(ctx context.Context, d Deps) (limit, used *float64) {
	docker, ok := dockerPath(d)
	if !ok {
		return nil, nil
	}
	if res, err := d.Runner.Run(ctx, docker, []string{"info", "--format", "{{.MemTotal}}"}, nil); err == nil && res.ExitCode == 0 {
		if bytes, perr := strconv.ParseFloat(strings.TrimSpace(string(res.Stdout)), 64); perr == nil && bytes > 0 {
			mib := bytes / 1048576
			limit = &mib
		}
	}
	if res, err := d.Runner.Run(ctx, docker, []string{"stats", "--no-stream", "--format", "{{.MemUsage}}"}, nil); err == nil && res.ExitCode == 0 {
		if mib, perr := sumUsedMiB(string(res.Stdout)); perr == nil {
			used = &mib
		}
	}
	return limit, used
}

// workingSetMiB returns the largest working set of any container with this name, as the Kubernetes nodes' container
// runtime reports it (`crictl stats`), and false when no node has such a container. The container name is a
// constant of this package; only the node's own name (checked by kindNodes) varies.
func workingSetMiB(ctx context.Context, d Deps, docker string, nodes []string, container string) (*float64, bool) {
	var largest *float64
	for _, node := range nodes {
		res, err := d.Runner.Run(ctx, docker, []string{"exec", node, "crictl", "stats", "-o", "json"}, nil)
		if err != nil || res.ExitCode != 0 {
			continue
		}
		var out struct {
			Stats []struct {
				Attributes struct {
					Metadata struct {
						Name string `json:"name"`
					} `json:"metadata"`
				} `json:"attributes"`
				Memory struct {
					WorkingSetBytes struct {
						Value string `json:"value"`
					} `json:"workingSetBytes"`
				} `json:"memory"`
			} `json:"stats"`
		}
		if json.Unmarshal(res.Stdout, &out) != nil {
			continue
		}
		for _, s := range out.Stats {
			if s.Attributes.Metadata.Name != container {
				continue
			}
			bytes, perr := strconv.ParseFloat(s.Memory.WorkingSetBytes.Value, 64)
			if perr != nil {
				continue
			}
			mib := bytes / 1048576
			if largest == nil || mib > *largest {
				largest = &mib
			}
		}
	}
	return largest, largest != nil
}

// Measure takes one reading of everything a gate may need. Unreadable values stay nil.
func Measure(ctx context.Context, d Deps) Measurements {
	var m Measurements
	m.VMLimitMiB, m.VMUsedMiB = DockerMemory(ctx, d)
	m.SwapUsedMiB = HostSwapUsedMiB(ctx, d)
	if docker, ok := dockerPath(d); ok {
		if nodes := kindNodes(ctx, d, docker); len(nodes) > 0 {
			m.IstiodMiB, _ = workingSetMiB(ctx, d, docker, nodes, "discovery") // istiod's container is named "discovery"
			m.SidecarMaxMiB, _ = workingSetMiB(ctx, d, docker, nodes, "istio-proxy")
		}
	}
	return m
}
