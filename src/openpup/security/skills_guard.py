"""Skills guard: validation + auditing for skill content, ported in spirit from hermes-agent.

hermes-agent's ``skills_ast_audit`` AST-scans the Python scripts bundled with
a skill for dynamic import / dynamic attribute access patterns -- "hints for
human review, not verdicts". That scan is ported here, and extended for
OpenPup's bigger attack surface: skill *bodies*. A SKILL.md body is a block
of instructions injected straight into the agent's context, so a hostile or
tampered skill is a prompt-injection vector. ``audit_skill`` therefore also
runs a small, deliberately conservative list of regex heuristics over the
body (false positives erode trust faster than they buy safety).

Severity model:

* ``info``  -- noted in findings, never surfaced to the agent on load;
* ``warn``  -- prepended to the loaded body as a caution banner;
* ``block`` -- the skill body is refused on load and rejected on write,
  unless the skill is user-created AND pinned (explicit owner trust --
  see ``is_exempt``).

AST findings top out at ``warn``: bundled scripts are never auto-executed by
OpenPup, so they stay diagnostic hints, exactly as hermes intended. Only the
body heuristics can ``block``. This module is dependency-free on purpose --
it duck-types ``Skill`` (needs ``.body`` and ``.path``) and never raises
from its public surface.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List

INFO = "info"
WARN = "warn"
BLOCK = "block"

# Matches skills.store.SKILL_FILE; not imported, to keep this module
# free-standing (and free of import cycles through openpup.skills).
_SKILL_FILE = "SKILL.md"
_SCRIPTS_DIR = "scripts"
_IGNORED_DIRS = {"__pycache__", ".venv", "venv", "node_modules"}


@dataclass(frozen=True)
class Finding:
    """One audit hit: what was found, where, and how seriously to take it."""

    construct: str  # stable pattern id, e.g. "dynamic_import", "prompt_override"
    line: int  # 1-based line number within ``source``
    severity: str  # "info" | "warn" | "block"
    detail: str  # human-readable description
    source: str = _SKILL_FILE  # file inside the skill dir (e.g. "scripts/x.py")


def format_findings(findings: List[Finding]) -> str:
    """One compact clause per finding, suitable for tool error messages."""
    return "; ".join(
        f"{f.source}:L{f.line} {f.construct} [{f.severity}] -- {f.detail}" for f in findings
    )


# --------------------------------------------------------------------------
# Script audit (AST) -- ported from hermes-agent tools/skills_ast_audit.py
# --------------------------------------------------------------------------
_AST_SEVERITY = {
    "dynamic_import": WARN,
    "dynamic_import_computed": WARN,
    "dynamic_getattr": INFO,
    "dict_access": INFO,
    "importlib_import": INFO,
    "string_exec": WARN,  # OpenPup addition: eval/exec of runtime strings
}


def audit_script_source(content: str, rel_path: str) -> List[Finding]:
    """AST-audit one Python source for dynamic import/access/exec patterns.

    Every pattern flagged here has legitimate uses; findings are hints for
    review, not verdicts. Unparseable or hostile input yields what was
    collected so far (possibly nothing) -- never an exception.
    """
    try:
        tree = ast.parse(content)
    except (SyntaxError, ValueError, RecursionError):
        return []

    findings: List[Finding] = []

    def hit(construct: str, line: int, detail: str) -> None:
        findings.append(Finding(construct, line, _AST_SEVERITY[construct], detail, rel_path))

    class Visitor(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "import_module":
                hit(
                    "dynamic_import",
                    node.lineno,
                    "importlib.import_module() -- loads arbitrary modules at runtime",
                )
            elif isinstance(func, ast.Name) and func.id == "__import__":
                if node.args and not isinstance(node.args[0], ast.Constant):
                    hit(
                        "dynamic_import_computed",
                        node.lineno,
                        "__import__ with non-literal module name",
                    )
            elif isinstance(func, ast.Name) and func.id == "getattr":
                if len(node.args) >= 2 and not isinstance(node.args[1], ast.Constant):
                    hit(
                        "dynamic_getattr",
                        node.lineno,
                        "getattr with non-literal attribute name",
                    )
            elif isinstance(func, ast.Name) and func.id in ("eval", "exec"):
                if node.args and not isinstance(node.args[0], ast.Constant):
                    hit(
                        "string_exec",
                        node.lineno,
                        f"{func.id}() of a non-literal string -- runs computed code",
                    )
            self.generic_visit(node)

        def visit_Subscript(self, node: ast.Subscript) -> None:
            if (
                isinstance(node.value, ast.Attribute)
                and node.value.attr == "__dict__"
                and not isinstance(node.slice, ast.Constant)
            ):
                hit(
                    "dict_access",
                    node.lineno,
                    "__dict__[<computed>] -- dynamic attribute access",
                )
            self.generic_visit(node)

        def visit_Import(self, node: ast.Import) -> None:
            for alias in node.names:
                if alias.name == "importlib" or alias.name.startswith("importlib."):
                    hit(
                        "importlib_import",
                        node.lineno,
                        f"import {alias.name} -- enables dynamic module loading",
                    )
            self.generic_visit(node)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            module = node.module or ""
            if module == "importlib" or module.startswith("importlib."):
                hit(
                    "importlib_import",
                    node.lineno,
                    f"from {module} import ... -- enables dynamic module loading",
                )
            self.generic_visit(node)

    try:
        Visitor().visit(tree)
    except (RecursionError, ValueError, RuntimeError):
        pass  # hostile/pathological input: keep what we collected so far
    return findings


def audit_scripts(skill_dir: Path) -> List[Finding]:
    """AST-audit every ``.py`` under the skill's ``scripts/`` directory."""
    scripts_dir = Path(skill_dir) / _SCRIPTS_DIR
    if not scripts_dir.is_dir():
        return []
    findings: List[Finding] = []
    for py_file in sorted(scripts_dir.rglob("*.py")):
        if set(py_file.parent.parts) & _IGNORED_DIRS:
            continue
        try:
            content = py_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        try:
            rel = f"{_SCRIPTS_DIR}/{py_file.relative_to(scripts_dir).as_posix()}"
        except ValueError:
            rel = py_file.name
        findings.extend(audit_script_source(content, rel))
    return findings


# --------------------------------------------------------------------------
# Body audit (regex heuristics over SKILL.md instructions)
# --------------------------------------------------------------------------
# Kept deliberately short and conservative: each pattern targets phrasing
# with essentially no honest use in a procedure document. (construct,
# severity, line-local regex, detail).
_BODY_PATTERNS = [
    # Prompt injection: telling the model to discard its standing instructions.
    (
        "prompt_override",
        BLOCK,
        re.compile(
            r"\b(ignore|disregard|forget|override)\b[^.\n]{0,40}"
            r"\b(previous|prior|earlier|above|system)\s+(instructions?|prompts?|rules|guidance)",
            re.I,
        ),
        "instructs the agent to discard its standing instructions",
    ),
    # Exfiltration: a transmit verb within reach of a secret-shaped noun.
    (
        "secret_exfiltration",
        BLOCK,
        re.compile(
            # NB: no \b before ".env" -- space->dot is not a word boundary.
            r"\b(send|post|upload|forward|e-?mail|transmit|exfiltrate|paste)\b[^.\n]{0,60}"
            r"(\b(secrets?|credentials?|api[ _-]?keys?|tokens?|passwords?"
            r"|environment\s+variables?|ssh\s+keys?)\b|\.env\b)",
            re.I,
        ),
        "instructs the agent to transmit secrets or environment data",
    ),
    # Covert action: contacting/acting while hiding it from the owner.
    (
        "covert_messaging",
        BLOCK,
        re.compile(
            r"\bwithout\s+(telling|asking|notifying|informing|alerting)\s+(the\s+)?(owner|user)\b"
            r"|\bdo\s+not\s+(tell|inform|alert|mention\s+this\s+to)\s+(the\s+)?(owner|user)\b",
            re.I,
        ),
        "instructs the agent to act behind the owner's back",
    ),
    # Obfuscated payloads: base64 near an execute verb has no honest use
    # in a procedure document.
    (
        "decode_and_execute",
        BLOCK,
        re.compile(
            r"\b(base64|b64decode)\b[^.\n]{0,80}\b(run|execute|eval|exec|sh|bash|python3?)\b",
            re.I,
        ),
        "instructs the agent to decode and execute an obfuscated payload",
    ),
    # Pipe-to-shell installs: common in legit install docs, so only warn --
    # but the agent should see a caution before following one.
    (
        "pipe_to_shell",
        WARN,
        re.compile(r"\b(curl|wget)\b[^\n|]{0,120}\|\s*(sudo\s+)?(sh|bash|zsh|python3?)\b", re.I),
        "pipes downloaded content straight into a shell",
    ),
    # Fetch-then-execute phrased in prose rather than as a pipeline.
    (
        "fetch_and_execute",
        WARN,
        re.compile(r"\b(download|fetch)\b[^.\n]{0,60}\b(and|then)\s+(run|execute)\b", re.I),
        "tells the agent to fetch remote content and execute it",
    ),
    # Reading credential material: rare-but-legit in ops flows, hence warn.
    (
        "sensitive_file_read",
        WARN,
        re.compile(
            r"\b(cat|type|print|echo|dump|read)\b[^.\n]{0,40}"
            r"(~?/\.?(ssh|aws|gnupg)\b|\bid_rsa\b|\.env\b|\bcredentials\b)",
            re.I,
        ),
        "reads credential/secret files",
    ),
]

# "Never send credentials anywhere" is advice, not an attack: suppress a
# match when a negation token sits just before it on the same line.
_NEGATION_RE = re.compile(r"\b(never|not|no|don'?t|doesn'?t|avoid|without)\b", re.I)
_NEGATION_WINDOW = 28


def _negated(line: str, start: int) -> bool:
    return bool(_NEGATION_RE.search(line[max(0, start - _NEGATION_WINDOW) : start]))


def audit_body(body: str, source: str = _SKILL_FILE) -> List[Finding]:
    """Scan a SKILL.md body for suspicious instruction patterns."""
    findings: List[Finding] = []
    for lineno, line in enumerate((body or "").splitlines(), 1):
        for construct, severity, pattern, detail in _BODY_PATTERNS:
            match = pattern.search(line)
            if match and not _negated(line, match.start()):
                findings.append(Finding(construct, lineno, severity, detail, source))
    return findings


# --------------------------------------------------------------------------
# Whole-skill audit + enforcement helpers
# --------------------------------------------------------------------------
def audit_skill(skill: Any) -> List[Finding]:
    """Audit one skill: body heuristics + AST audit of bundled scripts.

    ``skill`` is duck-typed -- anything with ``.body`` (str) and ``.path``
    (the skill directory) works.
    """
    return audit_body(skill.body) + audit_scripts(Path(skill.path))


def is_exempt(skill: Any) -> bool:
    """Explicit owner trust: user-created AND pinned skills bypass blocking."""
    return getattr(skill, "created_by", "") == "user" and getattr(skill, "state", "") == "pinned"


def is_quarantined(skill: Any) -> bool:
    """True when a skill has block-level findings and no owner-trust exemption.

    Never raises -- a broken audit must not take down read paths (e.g. the
    prompt index), so failures err on the side of visibility.
    """
    if is_exempt(skill):
        return False
    try:
        return any(f.severity == BLOCK for f in audit_skill(skill))
    except Exception:
        return False


def warn_banner(findings: List[Finding]) -> str:
    """Caution banner to prepend to a loaded body; "" when nothing to warn."""
    warns = [f for f in findings if f.severity == WARN]
    if not warns:
        return ""
    lines = ["> [!] SKILLS-GUARD CAUTION -- review before following these instructions:"]
    lines.extend(f"> - {f.source}:L{f.line} {f.construct}: {f.detail}" for f in warns)
    return "\n".join(lines) + "\n\n"
