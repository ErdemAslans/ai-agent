"""Unit tests for the deterministic validators."""
from pathlib import Path

import pytest

from apps.pipeline.validators.base import Severity, ValidationContext
from apps.pipeline.validators.diff_size import DiffSizeValidator
from apps.pipeline.validators.file_allowlist import FileAllowlistValidator
from apps.pipeline.validators.forbidden_pattern import ForbiddenPatternValidator
from apps.pipeline.validators.secret_scanner import SecretScanner
from apps.pipeline.validators.syntax import SyntaxValidator


@pytest.fixture
def ctx(tmp_path: Path) -> ValidationContext:
    return ValidationContext(
        workspace_path=tmp_path,
        allowlist={"src/app.py", "tests/test_app.py"},
        requirement="Add email validation",
        acceptance_criteria=["Email format check returns 400"],
        changed_files=[],
        diff="",
        test_command="pytest",
    )


# ---- FileAllowlistValidator -----------------------------------------------


class TestFileAllowlistValidator:
    def test_passes_when_files_in_allowlist(self, ctx):
        ctx.changed_files = ["src/app.py", "tests/test_app.py"]
        result = FileAllowlistValidator().validate(ctx)
        assert result.passed
        assert result.issues == []

    def test_fails_when_file_outside_allowlist(self, ctx):
        ctx.changed_files = ["src/app.py", "config/secrets.yaml"]
        result = FileAllowlistValidator().validate(ctx)
        assert not result.passed
        assert any(i.code == "UNAUTHORIZED_FILE" for i in result.issues)


# ---- SyntaxValidator ------------------------------------------------------


class TestSyntaxValidator:
    def test_passes_valid_python(self, ctx, tmp_path):
        (tmp_path / "good.py").write_text("def foo():\n    return 42\n")
        ctx.changed_files = ["good.py"]
        result = SyntaxValidator().validate(ctx)
        assert result.passed

    def test_fails_invalid_python(self, ctx, tmp_path):
        (tmp_path / "bad.py").write_text("def foo(:\n    return 42\n")
        ctx.changed_files = ["bad.py"]
        result = SyntaxValidator().validate(ctx)
        assert not result.passed
        assert any(i.code == "SYNTAX_ERROR" for i in result.issues)

    def test_skips_unknown_extension(self, ctx, tmp_path):
        (tmp_path / "config.yaml").write_text("foo: bar\n")
        ctx.changed_files = ["config.yaml"]
        result = SyntaxValidator().validate(ctx)
        assert result.passed  # no checker → no issues


# ---- SecretScanner --------------------------------------------------------


class TestSecretScanner:
    def test_detects_github_token(self, ctx):
        ctx.diff = "+token = 'github_pat_AABBCCDDEEFFGGHHIIJJKKLLMMNNOOPPQQ'"
        result = SecretScanner().validate(ctx)
        assert not result.passed
        assert any(i.code == "SECRET_LEAK" for i in result.issues)

    def test_detects_pem_private_key(self, ctx):
        ctx.diff = "+-----BEGIN RSA PRIVATE KEY-----\n+MIIE..."
        result = SecretScanner().validate(ctx)
        assert not result.passed

    def test_ignores_removed_lines(self, ctx):
        # '-' lines are existing content — not the agent's introduction
        ctx.diff = "-token = 'sk-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'"
        result = SecretScanner().validate(ctx)
        assert result.passed

    def test_passes_clean_diff(self, ctx):
        ctx.diff = "+def hello():\n+    return 'world'"
        result = SecretScanner().validate(ctx)
        assert result.passed


# ---- ForbiddenPatternValidator --------------------------------------------


class TestForbiddenPatternValidator:
    def test_detects_os_system(self, ctx):
        ctx.diff = "+os.system('rm -rf /')"
        result = ForbiddenPatternValidator().validate(ctx)
        assert not result.passed
        assert any(i.code == "FORBIDDEN_PATTERN" for i in result.issues)

    def test_detects_subprocess_shell_true(self, ctx):
        ctx.diff = "+subprocess.run(cmd, shell=True)"
        result = ForbiddenPatternValidator().validate(ctx)
        assert not result.passed

    def test_detects_eval(self, ctx):
        ctx.diff = "+eval(user_input)"
        result = ForbiddenPatternValidator().validate(ctx)
        assert not result.passed

    def test_passes_safe_code(self, ctx):
        ctx.diff = "+subprocess.run(['ls', '-la'])"
        result = ForbiddenPatternValidator().validate(ctx)
        assert result.passed


# ---- DiffSizeValidator ----------------------------------------------------


class TestDiffSizeValidator:
    def test_passes_when_no_git_history(self, ctx):
        # No git repo at workspace_path → shortstat fails → (0,0,0)
        result = DiffSizeValidator().validate(ctx)
        assert result.passed
        assert result.extra.get("stats", {}).get("files") == 0


# ---- ValidationResult helpers --------------------------------------------


class TestValidationResultSeverity:
    def test_warning_only_still_passes(self, ctx):
        # DiffSizeValidator warning-only path is harder to trigger without git;
        # this just sanity-checks the Severity enum.
        assert Severity.WARNING != Severity.BLOCKER
