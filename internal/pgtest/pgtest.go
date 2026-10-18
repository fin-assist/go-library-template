// Package pgtest provides a PostgreSQL for the library's tests and the checks of its
// migration source.
//
// It mirrors migratetest and migrate.Validate of fin-assist/go-service-template, so a
// migration that passes here passes the service's runner: the same file rules, the same
// version table per source, the same "up → down → up" walk. Keep the two in step.
package pgtest

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"io/fs"
	"net/url"
	"os"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/golang-migrate/migrate/v4"
	pgxmigrate "github.com/golang-migrate/migrate/v4/database/pgx/v5"
	"github.com/golang-migrate/migrate/v4/source/iofs"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/stdlib"
	tcpostgres "github.com/testcontainers/testcontainers-go/modules/postgres"
)

// DefaultImage is the PostgreSQL image of the test container; override with
// TEST_POSTGRES_IMAGE. The same major version as production (PostgreSQL 18).
const DefaultImage = "postgres:18-alpine"

// IrreversibleMarker starts the down file of a step that cannot be rolled back; the rest
// of the file raises an error, and the up → down → up walk skips its down.
const IrreversibleMarker = "-- irreversible"

var (
	serverOnce sync.Once
	serverDSN  string
	serverErr  error
)

// DSN returns the DSN of a fresh, empty database dropped at the end of the test.
//
// The server is TEST_DATABASE_URL when set (a local PostgreSQL), otherwise a container
// started once per test binary. Without either the test is skipped locally and fails in
// CI (CI is set), so a check cannot pass by silently not running.
func DSN(t testing.TB) string {
	t.Helper()
	serverOnce.Do(func() { serverDSN, serverErr = server() })
	if serverErr != nil {
		if os.Getenv("CI") != "" {
			t.Fatalf("no PostgreSQL for tests: %v", serverErr)
		}
		t.Skipf("no PostgreSQL for tests (set TEST_DATABASE_URL or run Docker): %v", serverErr)
	}

	ctx := context.Background()
	admin, err := pgx.Connect(ctx, serverDSN)
	if err != nil {
		t.Fatalf("connect: %v", err)
	}
	defer func() { _ = admin.Close(ctx) }()

	name := "t_" + randomHex(8)
	if _, err := admin.Exec(ctx, "CREATE DATABASE "+name); err != nil {
		t.Fatalf("create database: %v", err)
	}
	t.Cleanup(func() {
		c, err := pgx.Connect(context.Background(), serverDSN)
		if err != nil {
			return
		}
		defer func() { _ = c.Close(context.Background()) }()
		_, _ = c.Exec(context.Background(), "DROP DATABASE IF EXISTS "+name+" WITH (FORCE)")
	})

	u, err := url.Parse(serverDSN)
	if err != nil {
		t.Fatalf("parse DSN: %v", err)
	}
	u.Path = "/" + name
	return u.String()
}

func server() (string, error) {
	if dsn := os.Getenv("TEST_DATABASE_URL"); dsn != "" {
		return dsn, nil
	}
	img := os.Getenv("TEST_POSTGRES_IMAGE")
	if img == "" {
		img = DefaultImage
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()
	c, err := tcpostgres.Run(ctx, img,
		tcpostgres.WithDatabase("postgres"),
		tcpostgres.WithUsername("postgres"),
		tcpostgres.WithPassword("postgres"),
		tcpostgres.BasicWaitStrategies(),
	)
	if err != nil {
		return "", err
	}
	// The container lives until the test binary exits; Ryuk removes it.
	return c.ConnectionString(ctx, "sslmode=disable")
}

func randomHex(n int) string {
	b := make([]byte, n)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

var fileName = regexp.MustCompile(`^(\d+)_([a-z0-9_]+)\.(up|down)\.sql$`)

// Validate checks the files of a migration source by the service runner's rules: every SQL
// name matches NNN_name.(up|down).sql, every version has one up and one down file, and a
// version number belongs to one name. Non-SQL files (embed.go) are ignored.
func Validate(fsys fs.FS) error {
	entries, err := fs.ReadDir(fsys, ".")
	if err != nil {
		return err
	}
	type pair struct {
		name     string
		up, down bool
	}
	versions := map[uint64]*pair{}
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(e.Name(), ".sql") {
			continue
		}
		m := fileName.FindStringSubmatch(e.Name())
		if m == nil {
			return fmt.Errorf("%q does not match NNN_name.up.sql / NNN_name.down.sql", e.Name())
		}
		v, err := strconv.ParseUint(m[1], 10, 64)
		if err != nil {
			return fmt.Errorf("%q: version: %w", e.Name(), err)
		}
		p := versions[v]
		if p == nil {
			p = &pair{name: m[2]}
			versions[v] = p
		} else if p.name != m[2] {
			return fmt.Errorf("version %d is used by %q and %q", v, p.name, m[2])
		}
		if m[3] == "up" {
			p.up = true
		} else {
			p.down = true
		}
	}
	for v, p := range versions {
		if !p.up {
			return fmt.Errorf("version %d (%s) has no up file", v, p.name)
		}
		if !p.down {
			return fmt.Errorf("version %d (%s) has no down file", v, p.name)
		}
	}
	return nil
}

// CheckUpDownUp applies the migrations of fsys one by one on an empty database with the
// version table table, and for each version checks up → down → up: the down file really
// undoes the up file (the same up applies again) and the source ends at its last version,
// clean. A down file that starts with IrreversibleMarker is not run.
func CheckUpDownUp(t testing.TB, table string, fsys fs.FS) {
	t.Helper()
	if err := Validate(fsys); err != nil {
		t.Fatal(err)
	}
	m := open(t, DSN(t), table, fsys)

	for {
		before := version(t, m)
		err := m.Steps(1)
		if errors.Is(err, os.ErrNotExist) || errors.Is(err, migrate.ErrNoChange) {
			break // no next migration
		}
		if err != nil {
			t.Fatalf("up from %d: %v", before, err)
		}
		v := version(t, m)
		if irreversible(t, fsys, v) {
			continue // its down file only fails on purpose
		}
		if err := m.Steps(-1); err != nil {
			t.Fatalf("down from %d: %v", v, err)
		}
		if got := version(t, m); got != before {
			t.Fatalf("down from %d left version %d, want %d", v, got, before)
		}
		if err := m.Steps(1); err != nil {
			t.Fatalf("up again to %d: %v", v, err)
		}
	}
	if _, dirty, _ := m.Version(); dirty {
		t.Fatal("dirty at the end")
	}
}

func irreversible(t testing.TB, fsys fs.FS, v uint) bool {
	t.Helper()
	matches, err := downFiles(fsys, v)
	if err != nil || len(matches) != 1 {
		t.Fatalf("down file of %d: %v %v", v, matches, err)
	}
	b, err := fs.ReadFile(fsys, matches[0])
	if err != nil {
		t.Fatal(err)
	}
	return strings.HasPrefix(strings.TrimSpace(string(b)), IrreversibleMarker)
}

// downFiles finds the down file of version v whatever its leading zeros (000001_, 1_).
func downFiles(fsys fs.FS, v uint) ([]string, error) {
	all, err := fs.Glob(fsys, "*.down.sql")
	if err != nil {
		return nil, err
	}
	var out []string
	for _, name := range all {
		m := fileName.FindStringSubmatch(name)
		if m == nil {
			continue
		}
		if n, err := strconv.ParseUint(m[1], 10, 64); err == nil && n == uint64(v) {
			out = append(out, name)
		}
	}
	return out, nil
}

func version(t testing.TB, m *migrate.Migrate) uint {
	t.Helper()
	v, dirty, err := m.Version()
	if errors.Is(err, migrate.ErrNilVersion) {
		return 0
	}
	if err != nil {
		t.Fatal(err)
	}
	if dirty {
		t.Fatalf("dirty at version %d", v)
	}
	return v
}

func open(t testing.TB, dsn, table string, fsys fs.FS) *migrate.Migrate {
	t.Helper()
	pc, err := pgx.ParseConfig(dsn)
	if err != nil {
		t.Fatal(err)
	}
	db := stdlib.OpenDB(*pc)
	t.Cleanup(func() { _ = db.Close() })
	drv, err := pgxmigrate.WithInstance(db, &pgxmigrate.Config{MigrationsTable: table})
	if err != nil {
		t.Fatal(err)
	}
	files, err := iofs.New(fsys, ".")
	if err != nil {
		t.Fatal(err)
	}
	m, err := migrate.NewWithInstance("iofs", files, "pgx5", drv)
	if err != nil {
		t.Fatal(err)
	}
	return m
}
