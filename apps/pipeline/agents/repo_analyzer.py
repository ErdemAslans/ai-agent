"""RepoAnalyzerAgent — heuristic detection of language/framework/test/files.

Day 2: pure heuristic. Day 3: add LLM fallback for ambiguous repos.
"""
import re
import time
from pathlib import Path

import structlog

from apps.pipeline.observability import langfuse_context, observe
from apps.tasks.models import ExecutionReport

from .base import AgentInput, AgentOutput, record_agent_run

log = structlog.get_logger(__name__)

STOPWORDS = {
    "the", "and", "for", "with", "should", "must", "this", "that", "from", "have",
    "will", "into", "user", "users", "currently", "format", "format.", "format,",
    "added", "adding", "addition", "above", "below", "value", "values", "endpoint",
}

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    "dist", "build", "target", "vendor", ".idea", ".vscode",
}


class RepoAnalyzerInput(AgentInput):
    workspace_path: str
    requirement: str


class RepoAnalyzerOutput(AgentOutput):
    language: str
    framework: str
    build_tool: str
    test_command: str
    relevant_files: list[str]


class RepoAnalyzerAgent:
    name = "repo_analyzer"

    def __init__(self, llm=None):
        self.llm = llm

    @observe(name="agent.repo_analyzer", capture_input=False, capture_output=False)
    def run(self, input: RepoAnalyzerInput, report: ExecutionReport) -> RepoAnalyzerOutput:
        langfuse_context.update_current_observation(
            input={"workspace": input.workspace_path, "requirement": input.requirement[:300]},
        )
        started = time.monotonic()
        workspace = Path(input.workspace_path)

        info = self._detect_stack(workspace)
        relevant = self._find_relevant_files(workspace, input.requirement, info["language"])

        output = RepoAnalyzerOutput(
            language=info["language"],
            framework=info["framework"],
            build_tool=info["build_tool"],
            test_command=info["test_command"],
            relevant_files=relevant,
        )

        record_agent_run(
            report=report,
            agent_name=self.name,
            model="heuristic",
            started_at=started,
            input_summary=f"workspace={workspace} req={input.requirement[:200]}",
            output_summary=output.model_dump_json()[:500],
        )
        log.info(
            "repo_analyzer.completed",
            language=output.language,
            framework=output.framework,
            relevant_count=len(output.relevant_files),
        )
        langfuse_context.update_current_observation(
            output={
                "language": output.language,
                "framework": output.framework,
                "test_command": output.test_command,
                "relevant_files": output.relevant_files,
            },
        )
        return output

    def _detect_stack(self, workspace: Path) -> dict:
        if (workspace / "package.json").exists():
            return {
                "language": "JavaScript",
                "framework": "Node.js",
                "build_tool": "npm",
                "test_command": "npm test",
            }
        if (workspace / "pyproject.toml").exists() or (workspace / "requirements.txt").exists():
            framework = "Django" if (workspace / "manage.py").exists() else "Python"
            test_cmd = self._detect_python_test_cmd(workspace)
            return {
                "language": "Python",
                "framework": framework,
                "build_tool": "pip",
                "test_command": test_cmd,
            }
        if (workspace / "pom.xml").exists():
            return {
                "language": "Java",
                "framework": "Spring",
                "build_tool": "Maven",
                "test_command": "mvn test",
            }
        if (workspace / "go.mod").exists():
            return {
                "language": "Go",
                "framework": "Go modules",
                "build_tool": "go",
                "test_command": "go test ./...",
            }
        return {
            "language": "unknown",
            "framework": "unknown",
            "build_tool": "unknown",
            "test_command": "make test",
        }

    @staticmethod
    def _detect_python_test_cmd(workspace: Path) -> str:
        for f in ("requirements.txt", "pyproject.toml", "requirements-dev.txt"):
            path = workspace / f
            if path.exists() and "pytest" in path.read_text(errors="ignore").lower():
                return "pytest"
        if (workspace / "pytest.ini").exists():
            return "pytest"
        return "python -m unittest discover"

    def _find_relevant_files(
        self, workspace: Path, requirement: str, language: str
    ) -> list[str]:
        keywords = self._extract_keywords(requirement)
        extensions = {
            "Python": [".py"],
            "JavaScript": [".js", ".ts", ".jsx", ".tsx"],
            "Java": [".java"],
            "Go": [".go"],
        }.get(language, [".py", ".js", ".ts", ".java", ".go"])

        candidates: list[tuple[int, str]] = []
        for path in workspace.rglob("*"):
            if not path.is_file():
                continue
            if any(skip in path.parts for skip in SKIP_DIRS):
                continue
            if path.suffix not in extensions:
                continue

            rel_path = path.relative_to(workspace).as_posix()
            score = 0
            lowered_path = rel_path.lower()

            for kw in keywords:
                if kw.lower() in lowered_path:
                    score += 10

            try:
                content = path.read_text(errors="ignore")[:5000].lower()
                for kw in keywords:
                    if kw.lower() in content:
                        score += 1
            except OSError:
                pass

            if score > 0:
                candidates.append((score, rel_path))

        candidates.sort(key=lambda x: -x[0])
        return [path for _, path in candidates[:5]]

    @staticmethod
    def _extract_keywords(text: str) -> list[str]:
        words = re.findall(r"[A-Za-z_]+", text)
        return [w for w in words if len(w) > 3 and w.lower() not in STOPWORDS]
