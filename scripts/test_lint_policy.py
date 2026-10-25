"""The forbidigo rules in .golangci.yml reject every way to reach process-wide state.

forbidigo with analyze-types matches `pattern` against "<package name>.<identifier>" and
`pkg` against the full import path. The test applies the same matching to the configured
rules, so a rule that is deleted or narrowed fails here before a library depends on it.
"""
from pathlib import Path
import re
import unittest


CONFIG = Path(__file__).resolve().parents[1] / ".golangci.yml"


def rules():
    text = CONFIG.read_text()
    section = text.split("    forbidigo:\n", 1)[1].split("    depguard:\n", 1)[0]
    found = []
    for block in re.split(r"\n\s+- pattern: ", section)[1:]:
        lines = block.splitlines()
        pkg = re.search(r"^\s+pkg: (\S+)$", block, re.M)
        found.append((re.compile(lines[0].strip()), re.compile(pkg[1]) if pkg else None))
    return found


def forbidden(import_path, expression):
    return any(pattern.search(expression) and (pkg is None or pkg.search(import_path))
               for pattern, pkg in rules())


PROMETHEUS = "github.com/prometheus/client_golang/prometheus"
PROMAUTO = PROMETHEUS + "/promauto"


class ProcessStateRulesTests(unittest.TestCase):
    def test_process_wide_state_is_forbidden(self):
        cases = [
            ("log/slog", "slog.Default"), ("log/slog", "slog.SetDefault"),
            ("log/slog", "slog.InfoContext"), ("log/slog", "slog.SetLogLoggerLevel"),
            ("log", "log.Printf"), ("log", "log.Fatal"), ("log", "log.Default"),
            ("log", "log.SetOutput"), ("log", "log.Writer"), ("log", "log.Output"),
            (PROMETHEUS, "prometheus.MustRegister"), (PROMETHEUS, "prometheus.Register"),
            (PROMETHEUS, "prometheus.Unregister"), (PROMETHEUS, "prometheus.DefaultRegisterer"),
            (PROMETHEUS, "prometheus.DefaultGatherer"), (PROMAUTO, "promauto.NewCounter"),
            ("os", "os.Getenv"), ("os", "os.LookupEnv"), ("os", "os.Environ"),
            ("os", "os.ExpandEnv"), ("os", "os.Setenv"), ("os", "os.Unsetenv"),
            ("os", "os.Clearenv"), ("syscall", "syscall.Getenv"), ("syscall", "syscall.Environ"),
            ("syscall", "syscall.Setenv"),
        ]
        for import_path, expression in cases:
            with self.subTest(expression=expression):
                self.assertTrue(forbidden(import_path, expression))

    def test_injected_state_stays_allowed(self):
        cases = [
            ("log/slog", "slog.New"), ("log/slog", "slog.String"), ("log", "log.New"),
            (PROMETHEUS, "prometheus.NewRegistry"), (PROMETHEUS, "prometheus.NewCounter"),
            (PROMAUTO, "promauto.With"), ("os", "os.ReadFile"), ("os", "os.Expand"),
            # The same identifier from another package is not process-wide state.
            ("example.com/settings", "settings.Getenv"),
            ("example.com/metrics/prometheus", "prometheus.DefaultRegisterer"),
        ]
        for import_path, expression in cases:
            with self.subTest(expression=expression):
                self.assertFalse(forbidden(import_path, expression))


if __name__ == "__main__":
    unittest.main()
