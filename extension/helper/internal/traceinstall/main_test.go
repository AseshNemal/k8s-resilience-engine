package traceinstall

import (
	"os"
	"testing"
)

// The simulated runs write the record hundreds of times each; waiting for the disk every time would make the suite
// take minutes. TestRecordWritesAreSyncedByDefault covers the real setting.
func TestMain(m *testing.M) {
	syncWrites = false
	os.Exit(m.Run())
}
