"""GitProvider abstraction — swap providers (GitHub, GitLab, Bitbucket)."""
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass
class PullRequestInfo:
    url: str
    number: int


class GitProvider(Protocol):
    name: str

    def clone(self, repo_url: str, branch: str, dest: Path) -> Path: ...

    def checkout_new_branch(self, repo_path: Path, branch_name: str) -> None: ...

    def commit_all(
        self, repo_path: Path, message: str, author_name: str, author_email: str
    ) -> str: ...

    def push_branch(self, repo_path: Path, branch_name: str) -> None: ...

    def open_pull_request(
        self,
        repo_url: str,
        head_branch: str,
        base_branch: str,
        title: str,
        body: str,
    ) -> PullRequestInfo: ...

    def diff(self, repo_path: Path) -> str: ...

    def changed_files(self, repo_path: Path) -> list[str]: ...
