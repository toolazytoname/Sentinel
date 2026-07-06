"""Drift guard for the StrategyBase double-copy (RC.1).

`strategies/base.py` is the tested single-source-of-truth. The freqtrade
Docker image does NOT mount the `strategies/` package, so
`deploy/user_data/strategies/base.py` is a hand-maintained, self-contained
copy that INLINES the veto HTTP logic (equivalent to
`strategies/veto_gate.py::check_veto`).

These two copies have ALREADY drifted on purpose in docstrings/imports.
This guard therefore asserts the *behavioral contract* is identical while
TOLERATING the intentional documentation/import differences — it must FAIL
only when the runtime logic diverges (signature, fail-open semantics, or the
shared constants), never on prose.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

# --- Locate the three source files (repo-root relative to this test). ---
_REPO_ROOT = Path(__file__).resolve().parents[2]
ROOT_BASE = _REPO_ROOT / "strategies" / "base.py"
DEPLOY_BASE = _REPO_ROOT / "deploy" / "user_data" / "strategies" / "base.py"
VETO_GATE = _REPO_ROOT / "strategies" / "veto_gate.py"


def _parse(path: Path) -> ast.Module:
    assert path.is_file(), f"expected source file missing: {path}"
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _find_function(module: ast.Module, name: str) -> ast.FunctionDef:
    """Return the first FunctionDef with `name`, searched module + class level."""
    for node in ast.walk(module):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"function {name!r} not found")


def _module_constant(module: ast.Module, name: str):
    """Return the literal value of a module-level `name = <literal>` assignment."""
    for node in module.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
        if isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name:
                return ast.literal_eval(node.value)
    raise AssertionError(f"module constant {name!r} not found")


def _param_names(func: ast.FunctionDef) -> list[str]:
    args = func.args
    names = [a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)]
    if args.vararg:
        names.append("*" + args.vararg.arg)
    if args.kwarg:
        names.append("**" + args.kwarg.arg)
    return names


def _normalized_body(func: ast.FunctionDef) -> str:
    """Unparse the function body with the docstring stripped.

    Using ast.unparse discards comments and all whitespace/formatting, so the
    result reflects only executable structure — immune to the intentional
    docstring/import prose differences between the two copies.
    """
    body = list(func.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]  # drop docstring
    return "\n".join(ast.unparse(stmt) for stmt in body)


# --- Parse once at import time. ---
_ROOT = _parse(ROOT_BASE)
_DEPLOY = _parse(DEPLOY_BASE)
_VETO = _parse(VETO_GATE)


# ---------------------------------------------------------------------------
# 1. confirm_trade_entry signature must match across the two base.py copies.
# ---------------------------------------------------------------------------
def test_confirm_trade_entry_signature_matches():
    root_sig = _param_names(_find_function(_ROOT, "confirm_trade_entry"))
    deploy_sig = _param_names(_find_function(_DEPLOY, "confirm_trade_entry"))
    assert root_sig == deploy_sig, (
        "confirm_trade_entry signature drifted between root and deploy copies"
    )


# ---------------------------------------------------------------------------
# 2. Shared constants must be byte-for-value identical on both sides.
#    (root base.py imports them from veto_gate, so compare veto_gate ↔ deploy.)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("const", ["DEFAULT_AI_SERVICE_URL", "DEFAULT_TIMEOUT_S"])
def test_shared_constants_match(const):
    veto_val = _module_constant(_VETO, const)
    deploy_val = _module_constant(_DEPLOY, const)
    assert veto_val == deploy_val, (
        f"{const} drifted: veto_gate={veto_val!r} deploy={deploy_val!r}"
    )


# ---------------------------------------------------------------------------
# 3. The veto logic the deploy copy INLINES must equal veto_gate.check_veto
#    (the logic the root copy delegates to) after normalization.
# ---------------------------------------------------------------------------
def test_inlined_check_veto_body_matches_veto_gate():
    canonical = _normalized_body(_find_function(_VETO, "check_veto"))
    inlined = _normalized_body(_find_function(_DEPLOY, "check_veto"))
    assert inlined == canonical, (
        "deploy inlined check_veto diverged from strategies/veto_gate.check_veto"
    )


# ---------------------------------------------------------------------------
# 4. Fail-open control flow must be present on BOTH sides, independent of the
#    equality check above (guards against an identical-but-wrong refactor).
# ---------------------------------------------------------------------------
def _has_veto_block_branch(func: ast.FunctionDef) -> bool:
    """True if the function has an `if decision == "VETO": ... return False`."""
    for node in ast.walk(func):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not (isinstance(test, ast.Compare) and len(test.comparators) == 1):
            continue
        rhs = test.comparators[0]
        if isinstance(rhs, ast.Constant) and rhs.value == "VETO":
            if any(
                isinstance(s, ast.Return)
                and isinstance(s.value, ast.Constant)
                and s.value.value is False
                for s in ast.walk(node)
            ):
                return True
    return False


def _has_failopen_except(func: ast.FunctionDef) -> bool:
    """True if an except handler returns True (fail-open on error)."""
    for node in ast.walk(func):
        if isinstance(node, ast.ExceptHandler):
            if any(
                isinstance(s, ast.Return)
                and isinstance(s.value, ast.Constant)
                and s.value.value is True
                for s in ast.walk(node)
            ):
                return True
    return False


@pytest.mark.parametrize(
    "module,name",
    [(_VETO, "check_veto"), (_DEPLOY, "check_veto")],
    ids=["veto_gate", "deploy_inline"],
)
def test_veto_blocks_only_on_veto_decision(module, name):
    func = _find_function(module, name)
    assert _has_veto_block_branch(func), (
        f"{name}: missing `decision == 'VETO'` → return False branch"
    )


@pytest.mark.parametrize(
    "module,name",
    [(_VETO, "check_veto"), (_DEPLOY, "check_veto")],
    ids=["veto_gate", "deploy_inline"],
)
def test_veto_fails_open_on_error(module, name):
    func = _find_function(module, name)
    assert _has_failopen_except(func), (
        f"{name}: missing except → return True fail-open fallback"
    )
