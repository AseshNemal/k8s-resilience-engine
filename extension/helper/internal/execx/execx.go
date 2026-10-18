// Package execx runs external programs behind an interface so tests never launch a real dialog,
// Docker or anything else. Arguments are always an argv slice: no shell is ever involved, and
// secrets are passed on stdin, never as arguments (arguments are visible in the process list).
package execx

import (
	"bytes"
	"context"
	"errors"
	"os/exec"
)

// Result is a finished process. A non-zero exit is reported in ExitCode, not as an error: the error
// is reserved for failing to run the program at all (not found, killed by the context).
type Result struct {
	Stdout   []byte
	Stderr   []byte
	ExitCode int
}

type Runner interface {
	Run(ctx context.Context, name string, args []string, stdin []byte) (Result, error)
}

// Real runs programs on the host.
type Real struct{}

func (Real) Run(ctx context.Context, name string, args []string, stdin []byte) (Result, error) {
	cmd := exec.CommandContext(ctx, name, args...)
	if stdin != nil {
		cmd.Stdin = bytes.NewReader(stdin)
	}
	var out, errb bytes.Buffer
	cmd.Stdout, cmd.Stderr = &out, &errb
	err := cmd.Run()
	res := Result{Stdout: out.Bytes(), Stderr: errb.Bytes()}
	var exit *exec.ExitError
	switch {
	case err == nil:
		return res, nil
	case errors.As(err, &exit):
		res.ExitCode = exit.ExitCode()
		if ctx.Err() != nil {
			return res, ctx.Err()
		}
		return res, nil
	default:
		return res, err
	}
}
