"""CLI entry point: ``python manage.py report <task_id_or_trace_id>``.

Pretty-prints an ExecutionReport so reviewers can see at a glance which
agents ran, how many tokens each consumed, how much it cost, and how the
validators behaved — without scrolling through structured logs.
"""
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError

from apps.tasks.models import AgentRun, ExecutionReport


# Validators the pipeline registers, in display order. Names match what the
# orchestrator writes into validation_summary.
VALIDATOR_ORDER = (
    "file_allowlist",
    "syntax_check",
    "secret_scan",
    "forbidden_pattern",
    "diff_size",
    "test_runner",
    "ai_self_review",
)


def _format_tokens(n: int) -> str:
    return f"{n:>6,}".replace(",", " ")


def _format_cost(c: Decimal | float) -> str:
    val = float(c)
    if val == 0:
        return "    $0.00   "
    return f"${val:.6f}".rjust(11)


def _format_duration_ms(ms: int) -> str:
    if ms < 1000:
        return f"{ms:>6} ms"
    return f"{ms / 1000:>6.1f} s "


def _format_duration_short(ms: int) -> str:
    if ms < 1000:
        return f"{ms}ms"
    return f"{ms / 1000:.1f}s"


def _box_line(width: int = 71, char: str = "═") -> str:
    return char * width


def _icon(passed: bool | None) -> str:
    if passed is True:
        return "✓"
    if passed is False:
        return "✗"
    return "─"


class Command(BaseCommand):
    help = (
        "Pretty-print an execution report. Accepts either the task_id "
        "(e.g. TASK-DEMO-003) or the full trace_id (UUID)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "identifier",
            type=str,
            help="task_id (e.g. TASK-123) or trace_id (UUID)",
        )
        parser.add_argument(
            "--no-timeline",
            action="store_true",
            help="Skip the timeline section (shorter output).",
        )

    def handle(self, *args, **options):
        ident = options["identifier"]
        report = self._lookup_report(ident)

        out = self.stdout.write
        write = lambda line="": out(line)  # noqa: E731

        # ---- Header --------------------------------------------------------
        write(_box_line())
        write(f"  EXECUTION REPORT — {report.task.task_id}")
        write(_box_line())

        completed_at = report.completed_at or datetime.now(dt_timezone.utc)
        duration_total_ms = (
            int((completed_at - report.started_at).total_seconds() * 1000)
            if report.started_at
            else 0
        )

        status_icon = "✓" if report.status == "completed" else (
            "✗" if report.status == "failed" else "•"
        )
        write(f"  Status      : {report.status} {status_icon}")
        write(f"  Title       : {report.task.title}")
        write(f"  Trace ID    : {report.trace_id}")
        write(f"  Duration    : {_format_duration_short(duration_total_ms)}")
        if report.pr_url:
            write(f"  PR          : {report.pr_url}")
        if report.branch_name:
            write(f"  Branch      : {report.branch_name}")
        if report.error:
            write(f"  Error       : {report.error[:200]}")
        write()

        # ---- Agent pipeline -----------------------------------------------
        agent_runs = list(
            AgentRun.objects.filter(report=report).order_by("started_at")
        )

        write("  AGENT PIPELINE                          tokens       cost   duration")
        write("  " + "─" * 69)

        circled = ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩"]
        total_tokens = 0
        total_cost = Decimal("0")
        for i, run in enumerate(agent_runs):
            num = circled[i] if i < len(circled) else f"({i + 1})"
            tokens = run.prompt_tokens + run.completion_tokens
            cost = run.estimated_cost_usd
            total_tokens += tokens
            total_cost += cost
            model_label = run.model or "-"
            status_mark = " " if run.status == "success" else "✗"
            write(
                f"  {num} {run.agent_name:<16}({model_label:<18})"
                f" {_format_tokens(tokens)}  {_format_cost(cost)}"
                f"  {_format_duration_ms(run.duration_ms)} {status_mark}"
            )

        write("  " + "─" * 69)
        write(
            f"  {'TOTAL':<37}    {_format_tokens(total_tokens)}  "
            f"{_format_cost(total_cost)}  {_format_duration_ms(duration_total_ms)}"
        )
        write()

        # ---- Validators ---------------------------------------------------
        vs = report.validation_summary or {}
        if vs:
            write("  VALIDATORS")
            write("  " + "─" * 69)
            for name in VALIDATOR_ORDER:
                if name not in vs:
                    continue
                entry = vs[name]
                passed = entry.get("passed")
                issues = entry.get("issues", []) or []
                detail = self._validator_detail(name, entry)
                write(f"  {_icon(passed)} {name:<22}{detail}")
                # Show first 2 issue messages if present
                for issue in issues[:2]:
                    msg = issue.get("message", "") if isinstance(issue, dict) else str(issue)
                    write(f"      ↳ {msg[:80]}")
            # Show any validators present but not in VALIDATOR_ORDER
            for name, entry in vs.items():
                if name in VALIDATOR_ORDER:
                    continue
                passed = entry.get("passed") if isinstance(entry, dict) else None
                write(f"  {_icon(passed)} {name:<22}")
            write()

        # ---- LLM usage rollup ---------------------------------------------
        lu = report.llm_usage or {}
        if lu:
            write("  LLM USAGE")
            write("  " + "─" * 69)
            for k, v in lu.items():
                write(f"  {k:<22} {v}")
            write()

        # ---- Timeline -----------------------------------------------------
        if not options["no_timeline"] and report.timeline:
            write("  TIMELINE")
            write("  " + "─" * 69)
            base = report.started_at
            for entry in report.timeline:
                at = entry.get("at", "")
                step = entry.get("step", "")
                dms = entry.get("duration_ms", 0)
                status_word = entry.get("status", "")
                offset = self._offset(base, at)
                mark = "✓" if status_word in ("ok", "") else "✗"
                write(
                    f"  [{offset:>7}] {step:<26} "
                    f"{_format_duration_short(dms):<8} {mark} {status_word}"
                )
            write()

        write(_box_line())

    # -------------------------------------------------------------------
    def _lookup_report(self, ident: str) -> ExecutionReport:
        """Find a report by trace_id first, then by task_id (newest report)."""
        try:
            return ExecutionReport.objects.select_related("task").get(trace_id=ident)
        except ExecutionReport.DoesNotExist:
            pass
        report = (
            ExecutionReport.objects.select_related("task")
            .filter(task__task_id=ident)
            .order_by("-started_at")
            .first()
        )
        if report is None:
            raise CommandError(
                f"No execution report found for '{ident}'. "
                "Pass a task_id (e.g. TASK-123) or a trace_id (UUID)."
            )
        return report

    def _validator_detail(self, name: str, entry: dict) -> str:
        extra = entry.get("extra", {}) if isinstance(entry, dict) else {}
        if name == "test_runner":
            status_word = extra.get("test_status")
            if status_word:
                return f"tests {status_word}"
        if name == "diff_size":
            stats = extra.get("stats") or {}
            added = stats.get("lines_added")
            removed = stats.get("lines_removed")
            if added is not None or removed is not None:
                return f"+{added or 0} / -{removed or 0}"
        if name == "ai_self_review":
            met = extra.get("criteria_met")
            total = extra.get("criteria_total")
            if met is not None and total is not None:
                return f"{met}/{total} criteria met"
        n_issues = len(entry.get("issues", []) or []) if isinstance(entry, dict) else 0
        if n_issues:
            return f"{n_issues} issue(s)"
        return "0 issues"

    def _offset(self, base, at: str) -> str:
        if not base or not at:
            return ""
        try:
            ts = datetime.fromisoformat(at.replace("Z", "+00:00"))
        except ValueError:
            return ""
        if base.tzinfo is None:
            seconds = (ts.replace(tzinfo=None) - base).total_seconds()
        else:
            seconds = (ts - base).total_seconds()
        return f"+{seconds:.2f}s"
