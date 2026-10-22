package traceinstall

import (
	"context"
	"time"

	"resilience-engine/helper/internal/approve"
	"resilience-engine/helper/internal/bookinfotrace"
	"resilience-engine/helper/internal/kube"
)

// Cluster is the person's own cluster connection: reads, and the raw writes Guard wraps. It exists in memory for one
// command and is dropped when the command ends. Nothing here stores or prints the credential behind it.
type Cluster interface {
	kube.Reader
	kube.RawWriter
}

// Clock lets tests run minutes of waiting in no time.
type Clock interface {
	Now() time.Time
	Sleep(ctx context.Context, d time.Duration) error
}

// RealClock is the wall clock.
type RealClock struct{}

func (RealClock) Now() time.Time { return time.Now() }
func (RealClock) Sleep(ctx context.Context, d time.Duration) error {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-t.C:
		return nil
	}
}

// Timeouts bound every wait. They are configurable; the defaults suit a first run that has to pull images on a slow
// machine.
type Timeouts struct {
	Istiod       time.Duration // istiod becoming Ready after its Deployment is created
	Pod          time.Duration // one Pod being replaced, or reviews-v2 starting
	Rollback     time.Duration // the whole of an automatic rollback
	Poll         time.Duration
	MonitorEvery time.Duration // how often memory is read while waiting
	CRRetries    int           // retries of a custom resource whose CRD is not served yet
	CRRetryWait  time.Duration
	Client       time.Duration // the temporary client Pod finishing its requests
}

// DefaultTimeouts are used unless a test or a future setting overrides them.
func DefaultTimeouts() Timeouts {
	return Timeouts{Istiod: 6 * time.Minute, Pod: 5 * time.Minute, Rollback: 15 * time.Minute, Poll: 3 * time.Second, MonitorEvery: 15 * time.Second,
		CRRetries: 10, CRRetryWait: 2 * time.Second, Client: 3 * time.Minute}
}

// Deps is everything the commands need from the outside world.
type Deps struct {
	Cluster    Cluster
	Prompter   approve.Prompter
	Store      Store
	Tools      ToolEnv
	Host       bookinfotrace.Deps
	Clock      Clock
	Timeouts   Timeouts
	Thresholds bookinfotrace.Thresholds
	// Measure and Discover default to the real host readings; tests replace them.
	Measure  func(context.Context) bookinfotrace.Measurements
	Discover func(context.Context) (bookinfotrace.Address, error)
	NewID    func() (string, error)
}

func (d Deps) clock() Clock {
	if d.Clock != nil {
		return d.Clock
	}
	return RealClock{}
}

func (d Deps) measure(ctx context.Context) bookinfotrace.Measurements {
	if d.Measure != nil {
		return d.Measure(ctx)
	}
	return bookinfotrace.Measure(ctx, d.Host)
}

func (d Deps) discover(ctx context.Context) (bookinfotrace.Address, error) {
	if d.Discover != nil {
		return d.Discover(ctx)
	}
	return bookinfotrace.RediscoverJaegerOTLP(ctx, d.Host)
}

func (d Deps) newID() (string, error) {
	if d.NewID != nil {
		return d.NewID()
	}
	return NewInstallationID()
}

// Params identify the cluster a command was asked to act on.
type Params struct {
	ContextName string
	Identity    ClusterIdentity
}

// States a command can end in. They are stable: the page turns each into fixed wording.
const (
	StateApplied           = "bookinfo-applied"
	StateAlreadyApplied    = "bookinfo-already-applied"
	StatePlanBlocked       = "bookinfo-plan-blocked"
	StatePlanChanged       = "bookinfo-plan-changed"
	StateSetupIncomplete   = "bookinfo-setup-incomplete"
	StateApplyFailed       = "bookinfo-apply-failed"
	StateRollbackIncomplet = "bookinfo-rollback-incomplete"
	StateUndone            = "bookinfo-undone"
	StateNothingToUndo     = "bookinfo-nothing-to-undo"
	StateNoRecord          = "bookinfo-no-record"
	StateIdentityChanged   = "bookinfo-identity-changed"
	StateRecordUnusable    = "bookinfo-record-unusable"
	StatePlatform          = "bookinfo-platform-unsupported"
	StateToolUnavailable   = "bookinfo-tool-unavailable"
	StateDeclined          = "declined"
	StateTimedOut          = "timed-out"
	StateApprovalNA        = "approval-unavailable"
	StateError             = "error"
	StateChecksPassed      = "bookinfo-checks-passed"
	StateTrafficSent       = "bookinfo-traffic-sent"
	StateVerifyFailed      = "bookinfo-verify-failed"
	StateNotApplied        = "bookinfo-not-applied"
	StateStatus            = "bookinfo-status"
)

// Summary is the part of a record the page may see: no credentials, no cluster text, bounded.
type Summary struct {
	InstallationID string       `json:"installationId"`
	Phase          Phase        `json:"phase"`
	StartedAt      string       `json:"startedAt"`
	UpdatedAt      string       `json:"updatedAt"`
	Steps          []StepLog    `json:"steps"`
	Created        int          `json:"created"`
	Deleted        int          `json:"deleted"`
	Kept           int          `json:"kept"`
	Foreign        int          `json:"foreign"`
	Planned        int          `json:"planned"`
	Failure        *FailureInfo `json:"failure,omitempty"`
	Remaining      []string     `json:"remaining,omitempty"`
}

// SummaryOf condenses a record.
func SummaryOf(r *Record) *Summary {
	if r == nil {
		return nil
	}
	s := &Summary{InstallationID: r.InstallationID, Phase: r.Phase, StartedAt: r.StartedAt, UpdatedAt: r.UpdatedAt, Failure: r.Failure,
		Remaining: append([]string(nil), r.Remaining...)}
	steps := r.Steps
	if len(steps) > 60 {
		steps = steps[len(steps)-60:]
	}
	s.Steps = append([]StepLog{}, steps...)
	for _, e := range r.Inventory {
		switch e.State {
		case EntryCreated, EntryIntent:
			s.Created++
		case EntryDeleted:
			s.Deleted++
		case EntryKept:
			s.Kept++
		case EntryForeign:
			s.Foreign++
		default:
			s.Planned++
		}
	}
	return s
}

// Outcome is what a command returns. Every string in it is fixed wording or a name the setup itself chose.
type Outcome struct {
	OK      bool
	State   string
	Message string
	Plan    *bookinfotrace.PlanResult
	Setup   *Summary
	Diffs   []bookinfotrace.Diff
	Verify  *VerifyResult
}

func fail(state, message string) Outcome { return Outcome{State: state, Message: message} }
