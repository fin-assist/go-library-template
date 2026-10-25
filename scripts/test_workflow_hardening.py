"""Workflow hardening that every library copies from the template (FIN-301).

Standard library only: the checks read the workflow text, as CI runs them without pip.
"""
from pathlib import Path
import re
import unittest


WORKFLOWS = Path(__file__).resolve().parents[1] / ".github/workflows"
# Release fetches main and publishes with the token; Claude's action manages its own git auth.
CREDENTIALED = {"release.yml", "claude.yml"}
# Values a pull request author or a branch creator controls.
UNTRUSTED = re.compile(r"\$\{\{\s*github\.(base_ref|head_ref|event\.(pull_request|issue|comment|review|head_commit))")


def steps(text):
    """Each step as its text, from its "- " line up to the next step at the same indent."""
    pattern = r"^(\s+)- (?:uses|name|run|id|if):.*?(?=^\1- |^\S|^  \S|\Z)"
    return [m.group(0) for m in re.finditer(pattern, text, re.M | re.S)]


def run_blocks(text):
    """The script text of every run step: an inline value or an indented block."""
    blocks = []
    for match in re.finditer(r"^(\s+)(?:- )?run: (.*)$", text, re.M):
        indent, value = match.group(1), match.group(2)
        if value.strip() not in ("|", ">"):
            blocks.append(value)
            continue
        body = []
        for line in text[match.end() + 1:].splitlines():
            if line.strip() and not line.startswith(indent + "  "):
                break
            body.append(line)
        blocks.append("\n".join(body))
    return blocks


class WorkflowHardeningTests(unittest.TestCase):
    def test_checkouts_do_not_keep_the_token(self):
        for path in sorted(WORKFLOWS.glob("*.yml")):
            if path.name in CREDENTIALED:
                continue
            checkouts = [s for s in steps(path.read_text()) if "uses: actions/checkout@" in s]
            for number, step in enumerate(checkouts, start=1):
                with self.subTest(workflow=path.name, checkout=number):
                    self.assertRegex(step, r"(?m)^\s+persist-credentials: false\s*$", step)

    def test_scripts_take_untrusted_values_from_the_environment(self):
        for path in sorted(WORKFLOWS.glob("*.yml")):
            for block in run_blocks(path.read_text()):
                with self.subTest(workflow=path.name, script=block.strip().splitlines()[0]):
                    self.assertIsNone(UNTRUSTED.search(block), block)

    def test_detectors_catch_the_unsafe_forms(self):
        unsafe = (
            "jobs:\n  x:\n    steps:\n"
            "      - uses: actions/checkout@v4\n"
            "        with:\n          fetch-depth: 0\n"
            "      - name: Diff\n"
            "        run: |\n"
            "          git diff \"origin/${{ github.base_ref }}...HEAD\"\n"
            "      - run: echo ${{ github.event.pull_request.title }}\n"
        )
        checkout = [s for s in steps(unsafe) if "actions/checkout@" in s]
        self.assertEqual(len(checkout), 1)
        self.assertNotIn("persist-credentials: false", checkout[0])
        self.assertEqual(len([b for b in run_blocks(unsafe) if UNTRUSTED.search(b)]), 2)
        safe = unsafe.replace("fetch-depth: 0", "fetch-depth: 0\n          persist-credentials: false")
        self.assertIn("persist-credentials: false", [s for s in steps(safe) if "checkout@" in s][0])


if __name__ == "__main__":
    unittest.main()
