package pgtest

import (
	"context"
	"fmt"
	"runtime"
	"strings"
	"testing"
	"testing/fstest"

	"github.com/jackc/pgx/v5"
)

func file(s string) *fstest.MapFile { return &fstest.MapFile{Data: []byte(s)} }

// Validate delegates to migrate.Validate; these cases only show the delegation keeps the
// rules the library relies on. The rules themselves are tested in go-migrate.
func TestUnitValidate(t *testing.T) {
	cases := []struct {
		name  string
		files fstest.MapFS
		want  string // substring of the error; empty for valid
	}{
		{"empty", fstest.MapFS{}, ""},
		{"pairs and non-SQL files", fstest.MapFS{
			"embed.go":            file("package migrations"),
			"000001_a.up.sql":     file("SELECT 1"),
			"000001_a.down.sql":   file("SELECT 1"),
			"README.md":           file("notes"),
			"sub/000009_x.up.sql": file("ignored: not at the root"),
		}, ""},
		{"bad name", fstest.MapFS{"1-add.up.sql": file("")}, "does not match"},
		{"no down", fstest.MapFS{"000001_a.up.sql": file("")}, "has no down file"},
		{"no up", fstest.MapFS{"000001_a.down.sql": file("")}, "has no up file"},
		{"one version, two names", fstest.MapFS{
			"000001_a.up.sql":   file(""),
			"000001_b.down.sql": file(""),
		}, "is used by"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			err := Validate(c.files)
			switch {
			case c.want == "" && err != nil:
				t.Fatalf("unexpected error: %v", err)
			case c.want != "" && (err == nil || !strings.Contains(err.Error(), c.want)):
				t.Fatalf("error %v, want one containing %q", err, c.want)
			}
		})
	}
}

func TestUnitExplicitServerDoesNotStartContainer(t *testing.T) {
	t.Setenv("TEST_DATABASE_URL", "explicit-test-server")
	t.Setenv("TEST_POSTGRES_IMAGE", "must-not-be-pulled")
	dsn, err := server()
	if err != nil || dsn != "explicit-test-server" {
		t.Fatalf("explicit server selection: %q %v", dsn, err)
	}
}

func TestUnitCheckUpDownUpRejectsInvalidFilesBeforeConnecting(t *testing.T) {
	r := &recorder{TB: t}
	done := make(chan struct{})
	go func() {
		defer close(done)
		CheckUpDownUp(r, "lib_schema_migrations", fstest.MapFS{"000001_a.up.sql": file("")})
	}()
	<-done
	if !r.failed || !strings.Contains(r.msg, "has no down file") {
		t.Fatalf("failure %v %q, want the validation error", r.failed, r.msg)
	}
}

func TestIntegrationDSNGivesAnEmptyDatabaseOfItsOwn(t *testing.T) {
	a, b := DSN(t), DSN(t)
	if a == b {
		t.Fatal("two calls returned the same database")
	}
	ctx := context.Background()
	conn, err := pgx.Connect(ctx, a)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = conn.Close(ctx) }()
	var tables int
	if err := conn.QueryRow(ctx, "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'").Scan(&tables); err != nil {
		t.Fatal(err)
	}
	if tables != 0 {
		t.Fatalf("%d tables in a fresh database", tables)
	}
}

func TestIntegrationCheckUpDownUpPasses(t *testing.T) {
	files := fstest.MapFS{
		"000001_create_a.up.sql":   file("CREATE TABLE a (id bigint PRIMARY KEY)"),
		"000001_create_a.down.sql": file("DROP TABLE a"),
		"000002_drop_a.up.sql":     file("DROP TABLE a"),
		"000002_drop_a.down.sql": file(IrreversibleMarker + "\n" +
			"DO $$ BEGIN RAISE EXCEPTION 'irreversible'; END $$;"),
	}
	CheckUpDownUp(t, "lib_schema_migrations", files)
}

func TestIntegrationCheckUpDownUpCatchesBadMigrations(t *testing.T) {
	cases := []struct {
		name  string
		files fstest.MapFS
		want  string
	}{
		{"down does not undo up", fstest.MapFS{
			"000001_a.up.sql":   file("CREATE TABLE a (id bigint)"),
			"000001_a.down.sql": file("SELECT 1"),
		}, "up again to 1"},
		{"down fails", fstest.MapFS{
			"000001_a.up.sql":   file("CREATE TABLE a (id bigint)"),
			"000001_a.down.sql": file("DROP TABLE missing"),
		}, "down from 1"},
		{"up fails", fstest.MapFS{
			"000001_a.up.sql":   file("CREATE TABLE"),
			"000001_a.down.sql": file("SELECT 1"),
		}, "up from 0"},
	}
	// Without PostgreSQL the test skips here, not inside the recorder.
	_ = DSN(t)
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			r := &recorder{TB: t}
			done := make(chan struct{})
			go func() {
				defer close(done)
				CheckUpDownUp(r, "lib_schema_migrations", c.files)
			}()
			<-done
			if !r.failed {
				t.Fatal("the check passed")
			}
			if !strings.Contains(r.msg, c.want) {
				t.Fatalf("failure %q, want one containing %q", r.msg, c.want)
			}
		})
	}
}

// recorder records the first fatal failure of the code under test instead of failing the
// test itself; like testing.T it stops the goroutine on Fatal.
type recorder struct {
	testing.TB
	failed bool
	msg    string
}

func (r *recorder) Helper() {}

func (r *recorder) Fatal(args ...any) { r.fail(fmt.Sprint(args...)) }

func (r *recorder) Fatalf(format string, args ...any) { r.fail(fmt.Sprintf(format, args...)) }

func (r *recorder) fail(msg string) {
	r.failed, r.msg = true, msg
	runtime.Goexit()
}
