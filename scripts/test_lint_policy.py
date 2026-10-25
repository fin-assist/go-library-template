"""The forbidigo rules in .golangci.yml reject every way to reach process-wide state.

The pinned golangci-lint (version from the Makefile) runs with the repository's
.golangci.yml on a generated probe module: one line per forbidden identifier, which must
be reported, and the allowed forms (constructors, methods on injected values, chains
through allowed calls), which must not. The probe runs for linux and windows, so the
platform packages are checked too. A static check pins analyze-types and the exclusions.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

try:
    import yaml
except ImportError as error:  # a missing parser must fail the checks, never skip them
    raise ImportError("PyYAML is required by scripts/test_lint_policy.py: "
                      "install python3-yaml or `pip install pyyaml`") from error


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / ".golangci.yml"
LINT_VERSION = re.search(r"^GOLANGCI_LINT_VERSION := (\S+)$", (ROOT / "Makefile").read_text(), re.M)[1]

PROMETHEUS = "github.com/prometheus/client_golang/prometheus"
# import path -> identifiers that must be reported; every one is referenced as a value.
FORBIDDEN = {
    "fmt": ["Print", "Printf", "Println"],
    "log/slog": ["Default", "SetDefault", "SetLogLoggerLevel", "Debug", "DebugContext", "Info", "InfoContext",
                 "Warn", "WarnContext", "Error", "ErrorContext", "Log", "LogAttrs"],
    "log": ["Print", "Printf", "Println", "Fatal", "Fatalf", "Fatalln", "Panic", "Panicf", "Panicln",
            "Default", "Writer", "SetOutput", "SetFlags", "SetPrefix", "Flags", "Prefix", "Output"],
    "google.golang.org/grpc/grpclog": ["Info", "Infof", "Infoln", "Warning", "Warningf", "Warningln",
                                       "Error", "Errorf", "Errorln", "Fatal", "Fatalf", "Fatalln", "V",
                                       "Component", "SetLoggerV2"],
    PROMETHEUS: ["MustRegister", "Register", "Unregister", "DefaultRegisterer", "DefaultGatherer"],
    PROMETHEUS + "/promauto": ["NewCounter", "NewCounterVec", "NewCounterFunc", "NewGauge", "NewGaugeVec",
                               "NewGaugeFunc", "NewHistogram", "NewHistogramVec", "NewSummary",
                               "NewSummaryVec", "NewUntypedFunc"],
    PROMETHEUS + "/promhttp": ["Handler"],
    "expvar": ["Publish", "Get", "Do", "Handler", "NewInt", "NewFloat", "NewMap", "NewString"],
    "net/http": ["DefaultClient", "DefaultTransport", "DefaultServeMux", "Handle", "HandleFunc", "Get", "Head",
                 "Post", "PostForm", "ListenAndServe", "ListenAndServeTLS"],
    "go.opentelemetry.io/otel": ["GetTracerProvider", "SetTracerProvider", "GetMeterProvider",
                                 "SetMeterProvider", "GetTextMapPropagator", "SetTextMapPropagator",
                                 "SetErrorHandler", "SetLogger"],
    "os/signal": ["Notify", "NotifyContext", "Ignore", "Reset", "Stop"],
    "os": ["Getenv", "LookupEnv", "Environ", "ExpandEnv", "Setenv", "Unsetenv", "Clearenv", "Args",
           "UserHomeDir", "UserConfigDir", "UserCacheDir"],
    "syscall": ["Getenv", "Environ", "Setenv", "Unsetenv", "Clearenv"],
}
PLATFORM = {
    "unix": ("golang.org/x/sys/unix", ["Getenv", "Environ", "Setenv", "Unsetenv", "Clearenv"]),
    "windows": ("golang.org/x/sys/windows", ["Getenv", "Environ", "Setenv", "Unsetenv", "Clearenv"]),
}

ALLOWED = '''package probe

import (
	"context"
	"fmt"
	"io"
	"log"
	"log/slog"
	"net/http"
	"os"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

// Allowed uses injected values only; none of these lines may be reported.
func Allowed(ctx context.Context, l *slog.Logger, reg prometheus.Registerer, c prometheus.Collector,
	client *http.Client, w io.Writer) {
	l.InfoContext(ctx, "x")
	l.Info("x")
	slog.New(slog.NewTextHandler(w, nil)).ErrorContext(ctx, "x", slog.String("k", "v"))
	std := log.New(w, "", 0)
	std.SetOutput(w)
	std.Printf("x")
	reg.MustRegister(c)
	r := prometheus.NewRegistry()
	r.MustRegister(c)
	_ = prometheus.NewCounter(prometheus.CounterOpts{Name: "x"})
	promauto.With(reg).NewCounter(prometheus.CounterOpts{Name: "y"})
	promauto.With(r).NewGaugeVec(prometheus.GaugeOpts{Name: "z"}, nil)
	h := promhttp.HandlerFor(r, promhttp.HandlerOpts{})
	_ = promhttp.InstrumentMetricHandler(reg, h)
	mux := http.NewServeMux()
	mux.Handle("/", h)
	_, _ = client.Get("http://example.invalid")
	_, _ = os.ReadFile("x")
	_ = os.Expand("$A", func(string) string { return "" })
	_ = fmt.Sprintf("x")
	_, _ = fmt.Fprintf(w, "x")
}
'''


def module_path(import_path):
    return import_path.rsplit("/", 1)[-1]


def forbidden_file(imports, build="", name="Forbidden"):
    lines = ([build, ""] if build else []) + ["package probe", "", "import ("]
    lines += [f'\t"{path}"' for path in imports]
    lines += [")", "", f"// {name} references every forbidden identifier on its own line.", f"func {name}() {{"]
    expected = set()
    for path, names in imports.items():
        for name in names:
            lines.append(f"\t_ = {module_path(path)}.{name}")
            expected.add(len(lines))
    if not build:
        for builtin in ["print", "println"]:
            lines.append(f'\t{builtin}("x")')
            expected.add(len(lines))
    lines.append("}")
    return "\n".join(lines) + "\n", expected


def lint(root, tool, goos):
    command = [str(tool), "run",
               "--allow-parallel-runners", "--enable-only", "forbidigo", "--max-issues-per-linter", "0",
               "--max-same-issues", "0", "--output.json.path", "stdout", "--show-stats=false", "./..."]
    result = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=600,
                            env=dict(os.environ, GOTOOLCHAIN="auto", GOOS=goos, GOFLAGS="-mod=mod"))
    report = json.loads(result.stdout.strip().splitlines()[-1]) if result.stdout.strip() else None
    if report is None:
        raise AssertionError(f"golangci-lint produced no report:\n{result.stderr}")
    return report.get("Issues") or [], result


class ConfigTests(unittest.TestCase):
    def test_forbidigo_matches_types_and_excludes_only_tests_and_pgtest(self):
        config = yaml.safe_load(CONFIG.read_text())
        self.assertIs(config["linters"]["settings"]["forbidigo"]["analyze-types"], True)
        excluded = sorted(rule["path"] for rule in config["linters"]["exclusions"]["rules"]
                          if "forbidigo" in rule.get("linters", []))
        self.assertEqual(excluded, sorted([r"_test\.go", "internal/pgtest/"]))


class ProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        root = cls.root = Path(cls.directory.name)
        shutil.copyfile(CONFIG, root / ".golangci.yml")
        (root / "go.mod").write_text("module example.com/probe\n\ngo 1.26.0\n")
        common, cls.expected = forbidden_file(FORBIDDEN)
        (root / "forbidden.go").write_text(common)
        cls.platform = {}
        for goos, (path, names) in PLATFORM.items():
            text, lines = forbidden_file({path: names}, f"//go:build {goos}", "Forbidden" + goos.title())
            (root / f"forbidden_{goos}.go").write_text(text)
            cls.platform[goos] = lines
        (root / "allowed.go").write_text(ALLOWED)
        # Exclusions: tests and exactly internal/pgtest are exempt; a similar path is not.
        reference = 'package {pkg}\n\nimport "os"\n\nfunc Env() string {{ return os.Getenv("X") }}\n'
        (root / "probe_test.go").write_text(reference.format(pkg="probe").replace("Env()", "testEnv()"))
        for folder in ["internal/pgtest", "internal/pgtestx"]:
            (root / folder).mkdir(parents=True)
            (root / folder / "env.go").write_text(reference.format(pkg=folder.rsplit("/", 1)[-1]))
        # Build the pinned linter once for the host; GOOS then selects only the analysed platform.
        install = subprocess.run(["go", "install", f"github.com/golangci/golangci-lint/v2/cmd/golangci-lint@{LINT_VERSION}"],
                                 capture_output=True, text=True, timeout=900,
                                 env=dict(os.environ, GOTOOLCHAIN="auto", GOBIN=str(root / "bin")))
        if install.returncode != 0:
            raise AssertionError(install.stderr)
        cls.tool = root / "bin" / "golangci-lint"
        tidy = subprocess.run(["go", "mod", "tidy"], cwd=root, capture_output=True, text=True, timeout=600,
                              env=dict(os.environ, GOTOOLCHAIN="auto"))
        if tidy.returncode != 0:
            raise AssertionError(tidy.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def check(self, goos):
        issues, result = lint(self.root, self.tool, goos)
        others = [issue for issue in issues if issue["FromLinter"] != "forbidigo"]
        self.assertEqual(others, [], result.stderr)
        reported = {(issue["Pos"]["Filename"], issue["Pos"]["Line"]) for issue in issues}
        expected = {("forbidden.go", line) for line in self.expected}
        expected |= {(f"forbidden_{goos_}.go", line)
                     for goos_, lines in self.platform.items()
                     if goos_ == ("windows" if goos == "windows" else "unix") for line in lines}
        expected.add(("internal/pgtestx/env.go", 5))
        self.assertEqual(sorted(reported - expected), [], "reported outside the forbidden lines")
        self.assertEqual(sorted(expected - reported), [], "forbidden lines not reported")

    def test_linux(self):
        self.check("linux")

    def test_windows(self):
        self.check("windows")


if __name__ == "__main__":
    unittest.main()
