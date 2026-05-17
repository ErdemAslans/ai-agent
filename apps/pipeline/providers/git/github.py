"""GitHubProvider — GitPython for local ops, PyGithub for API."""
import re
from pathlib import Path

import structlog
from git import Repo as GitRepo
from github import Github

from .base import PullRequestInfo

log = structlog.get_logger(__name__)


class GitHubProvider:
    name = "github"

    def __init__(self, token: str):
        if not token:
            raise ValueError("GITHUB_TOKEN is empty — set it in .env")
        self.token = token
        self.api = Github(token)

    # ---- Local git operations ----

    def clone(self, repo_url: str, branch: str, dest: Path) -> Path:
        auth_url = self._with_token(repo_url)
        log.info("git.clone.started", repo_url=repo_url, branch=branch, dest=str(dest))
        try:
            GitRepo.clone_from(auth_url, str(dest), branch=branch, depth=1)
        except Exception as exc:
            log.error("git.clone.failed", repo_url=repo_url, branch=branch, error=str(exc))
            raise
        log.info("git.clone.completed", dest=str(dest))
        return dest

    def checkout_new_branch(self, repo_path: Path, branch_name: str) -> None:
        repo = GitRepo(repo_path)
        new_branch = repo.create_head(branch_name)
        new_branch.checkout()
        log.info("git.branch.created", branch=branch_name, repo=str(repo_path))

    def commit_all(
        self, repo_path: Path, message: str, author_name: str, author_email: str
    ) -> str:
        repo = GitRepo(repo_path)
        with repo.config_writer() as cw:
            cw.set_value("user", "name", author_name)
            cw.set_value("user", "email", author_email)
        repo.git.add(A=True)

        if not repo.is_dirty(untracked_files=True) and not repo.index.diff("HEAD"):
            log.warning("git.commit.empty_diff", repo=str(repo_path))
            raise RuntimeError("No changes to commit — agent produced empty diff")

        commit = repo.index.commit(message)
        log.info(
            "git.commit.created",
            sha=commit.hexsha[:8],
            message=message,
            repo=str(repo_path),
        )
        return commit.hexsha

    def push_branch(self, repo_path: Path, branch_name: str) -> None:
        """Push the branch to origin. Force on REJECTED — AI-agent branches
        are only written by us, so overwriting a stale remote copy is safe.
        """
        repo = GitRepo(repo_path)
        origin = repo.remote("origin")
        current_url = next(iter(origin.urls), None)
        if current_url:
            origin.set_url(self._with_token(current_url))

        push_results = origin.push(refspec=f"{branch_name}:{branch_name}")
        needs_force = False
        for info in push_results:
            if info.flags & (info.REJECTED | info.REMOTE_REJECTED):
                needs_force = True
                continue
            if info.flags & info.ERROR:
                raise RuntimeError(f"git push failed: {info.summary}")

        if needs_force:
            log.warning(
                "git.push.using_force",
                branch=branch_name,
                reason="remote diverged; AI-agent branch is force-overwritten",
            )
            push_results = origin.push(refspec=f"+{branch_name}:{branch_name}")
            for info in push_results:
                if info.flags & info.ERROR:
                    raise RuntimeError(f"git push --force failed: {info.summary}")

        log.info("git.push.completed", branch=branch_name, forced=needs_force)

    def diff(self, repo_path: Path) -> str:
        """Return the diff of the working tree against HEAD.

        Works pre-commit (uncommitted edits vs HEAD) and tolerates shallow
        clones (depth=1) where HEAD~1 does not exist.
        """
        repo = GitRepo(repo_path)
        return repo.git.diff("HEAD")

    def changed_files(self, repo_path: Path) -> list[str]:
        repo = GitRepo(repo_path)
        try:
            return repo.git.diff("HEAD", "--name-only").splitlines()
        except Exception:
            return [item.a_path for item in repo.index.diff(None)] + repo.untracked_files

    # ---- GitHub API ----

    def open_pull_request(
        self,
        repo_url: str,
        head_branch: str,
        base_branch: str,
        title: str,
        body: str,
    ) -> PullRequestInfo:
        """Open a PR. If one already exists for this branch, update it."""
        from github import GithubException

        owner_repo = self._extract_owner_repo(repo_url)
        repo = self.api.get_repo(owner_repo)
        try:
            pr = repo.create_pull(title=title, body=body, head=head_branch, base=base_branch)
            log.info("git.pr.opened", url=pr.html_url, number=pr.number, repo=owner_repo)
            return PullRequestInfo(url=pr.html_url, number=pr.number)
        except GithubException as exc:
            # 422 typically means "PR already exists for this head"
            if exc.status != 422:
                raise
            owner = owner_repo.split("/")[0]
            head_qualified = f"{owner}:{head_branch}"
            existing = next(
                iter(repo.get_pulls(state="open", head=head_qualified, base=base_branch)),
                None,
            )
            if existing is None:
                raise
            existing.edit(title=title, body=body)
            log.info(
                "git.pr.updated",
                url=existing.html_url,
                number=existing.number,
                repo=owner_repo,
            )
            return PullRequestInfo(url=existing.html_url, number=existing.number)

    # ---- Helpers ----

    def _with_token(self, repo_url: str) -> str:
        if "x-access-token:" in repo_url:
            return repo_url
        if repo_url.startswith("https://"):
            return repo_url.replace(
                "https://", f"https://x-access-token:{self.token}@", 1
            )
        return repo_url

    @staticmethod
    def _extract_owner_repo(repo_url: str) -> str:
        match = re.search(
            r"github\.com[:/]([^/]+)/([^/\s.]+?)(?:\.git)?/?$", repo_url
        )
        if not match:
            raise ValueError(f"Cannot parse GitHub URL: {repo_url}")
        return f"{match.group(1)}/{match.group(2)}"
