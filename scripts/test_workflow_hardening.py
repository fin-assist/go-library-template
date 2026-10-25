"""Workflow hardening that every library copies from the template (FIN-301).

CI runs these checks with the standard library only, so they read the workflow text
without a YAML parser. They fail closed:
- a step is any YAML list item, whatever its first key, and its checkout must set
  `persist-credentials: false`;
- a `run:` value (inline, continued or any block scalar) must contain no `${{` at all;
  values reach the script through `env:`.
"""
from pathlib import Path
import re
import unittest


WORKFLOWS = Path(__file__).resolve().parents[1] / ".github/workflows"
# Release fetches main and publishes with the token; Claude's action manages its own git auth.
CREDENTIALED = {"release.yml", "claude.yml"}

ITEM = re.compile(r"^( *)- ")
CHECKOUT = re.compile(r"""^( *)(?:- )?uses:[ \t]*["']?actions/checkout@""")
PERSIST_OFF = re.compile(r"""^ *persist-credentials:[ \t]*["']?false["']?[ \t]*(#.*)?$""")
RUN = re.compile(r"^( *)(- )?run:(.*)$")
BLOCK_HEADER = re.compile(r"^[ \t]*[|>][-+0-9]*[ \t]*(#.*)?$")


def indent(line):
    return len(line) - len(line.lstrip(" "))


def nested(lines, start, parent):
    """Lines after start that belong to a node at column parent: blank or deeper."""
    end = start + 1
    while end < len(lines) and (not lines[end].strip() or indent(lines[end]) > parent):
        end += 1
    return lines[start + 1:end]


def checkout_steps(text):
    """The text of every list item that holds an actions/checkout `uses:` key."""
    lines = text.splitlines()
    steps = []
    for number, line in enumerate(lines):
        match = CHECKOUT.match(line)
        if not match:
            continue
        start = number
        if not ITEM.match(line):
            # The list item whose content starts at the key's column, searched upwards.
            key = indent(line)
            start = number - 1
            while start >= 0:
                above = lines[start]
                item = ITEM.match(above)
                if item and len(item.group(1)) + 2 == key:
                    break
                if above.strip() and indent(above) < key and not item:
                    start = -1  # left the mapping without meeting its list item
                    break
                start -= 1
        if start < 0:
            steps.append(line)  # a checkout outside any step is reported as unsafe
            continue
        steps.append("\n".join([lines[start]] + nested(lines, start, indent(lines[start]))))
    return steps


def run_scripts(text):
    """The value of every `run:` key, including block scalars and continuation lines."""
    lines = text.splitlines()
    scripts = []
    for number, line in enumerate(lines):
        match = RUN.match(line)
        if not match:
            continue
        key = len(match.group(1)) + (2 if match.group(2) else 0)
        body = [] if BLOCK_HEADER.match(match.group(3)) else [match.group(3)]
        body += nested(lines, number, key)
        scripts.append("\n".join(body))
    return scripts


def persists_token(step):
    return not any(PERSIST_OFF.match(line) for line in step.splitlines())


def interpolates(script):
    return "${{" in script


class WorkflowHardeningTests(unittest.TestCase):
    def test_checkouts_do_not_keep_the_token(self):
        for path in sorted(WORKFLOWS.glob("*.yml")):
            if path.name in CREDENTIALED:
                continue
            for number, step in enumerate(checkout_steps(path.read_text()), start=1):
                with self.subTest(workflow=path.name, checkout=number):
                    self.assertFalse(persists_token(step), step)

    def test_scripts_contain_no_expressions(self):
        for path in sorted(WORKFLOWS.glob("*.yml")):
            for number, script in enumerate(run_scripts(path.read_text()), start=1):
                with self.subTest(workflow=path.name, script=number):
                    self.assertFalse(interpolates(script), script)

    def test_every_workflow_run_is_found(self):
        # A detector that finds nothing would pass every workflow.
        for path in sorted(WORKFLOWS.glob("*.yml")):
            text = path.read_text()
            with self.subTest(workflow=path.name):
                self.assertEqual(len(run_scripts(text)), len(re.findall(r"(?m)^ *(?:- )?run:", text)))
                self.assertEqual(len(checkout_steps(text)), text.count("actions/checkout@"))


STEPS = "jobs:\n  x:\n    steps:\n"


class DetectorTests(unittest.TestCase):
    """Every form reported in review must be caught; safe forms must pass."""

    def unsafe_checkouts(self, body):
        return [step for step in checkout_steps(STEPS + body) if persists_token(step)]

    def test_checkout_without_the_flag(self):
        self.assertEqual(len(self.unsafe_checkouts(
            "      - uses: actions/checkout@v4\n        with:\n          fetch-depth: 0\n")), 1)

    def test_checkout_with_the_flag(self):
        self.assertEqual(self.unsafe_checkouts(
            "      - uses: actions/checkout@v4\n        with:\n          persist-credentials: false\n"), [])

    def test_step_starting_with_another_key(self):
        for first in ["env:\n          A: b", "with:\n          fetch-depth: 0", "name: Checkout",
                      "id: co", "if: always()", "continue-on-error: true", "timeout-minutes: 1"]:
            with self.subTest(first=first):
                self.assertEqual(len(self.unsafe_checkouts(
                    f"      - {first}\n        uses: actions/checkout@v4\n")), 1)

    def test_quoted_uses(self):
        for uses in ['"actions/checkout@v4"', "'actions/checkout@v4'"]:
            with self.subTest(uses=uses):
                self.assertEqual(len(self.unsafe_checkouts(f"      - uses: {uses}\n")), 1)

    def test_blank_line_does_not_merge_steps(self):
        body = ("      - run: echo hi\n\n"
                "      - uses: actions/checkout@v4\n\n"
                "      - uses: actions/checkout@v4\n        with:\n          persist-credentials: false\n")
        self.assertEqual(len(checkout_steps(STEPS + body)), 2)
        self.assertEqual(len(self.unsafe_checkouts(body)), 1)

    def test_flag_in_the_next_step_does_not_count(self):
        body = ("      - uses: actions/checkout@v4\n"
                "      - uses: actions/setup-go@v5\n        with:\n          persist-credentials: false\n")
        self.assertEqual(len(self.unsafe_checkouts(body)), 1)

    def unsafe_scripts(self, body):
        return [script for script in run_scripts(STEPS + body) if interpolates(script)]

    def test_every_expression_in_run_is_rejected(self):
        for expression in ["github.base_ref", "github.head_ref", "github.event.pull_request.title",
                           "github.event.commits[0].message", "github.event.workflow_run.head_branch",
                           "env.BASE_REF", "format('{0}', github.event.issue.title)",
                           "toJSON(github.event)", "inputs.tag", "steps.x.outputs.y"]:
            with self.subTest(expression=expression):
                self.assertEqual(len(self.unsafe_scripts(f"      - run: echo ${{{{ {expression} }}}}\n")), 1)

    def test_block_scalar_headers(self):
        for header in ["|", ">", "|-", ">-", "|+", ">+", "|2", "|-2", "| # comment", ">-  # x"]:
            with self.subTest(header=header):
                body = f"      - name: x\n        run: {header}\n          echo ok\n          echo ${{{{ env.A }}}}\n"
                self.assertEqual(len(self.unsafe_scripts(body)), 1)

    def test_block_scalar_with_blank_lines_and_next_step(self):
        body = ("      - run: |-\n          echo ok\n\n          echo ${{ github.head_ref }}\n"
                "      - run: echo safe\n")
        self.assertEqual(len(run_scripts(STEPS + body)), 2)
        self.assertEqual(len(self.unsafe_scripts(body)), 1)

    def test_inline_continuation_and_quotes(self):
        for body in ["      - run: echo a\n          ${{ github.event.issue.title }}\n",
                     "      - run: \"echo ${{ github.base_ref }}\"\n",
                     "      - name: x\n        run: echo ${{ github.event.workflow_run.head_branch }}\n"]:
            with self.subTest(body=body):
                self.assertEqual(len(self.unsafe_scripts(body)), 1)

    def test_expressions_outside_run_are_allowed(self):
        body = ("      - name: x\n        env:\n          BASE_REF: ${{ github.base_ref }}\n"
                "        run: git diff \"origin/$BASE_REF...HEAD\"\n")
        self.assertEqual(self.unsafe_scripts(body), [])


if __name__ == "__main__":
    unittest.main()
