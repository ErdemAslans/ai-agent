"""Unit tests for WorkspaceManager."""
import pytest

from apps.pipeline.workspace import WorkspaceManager


@pytest.fixture
def mgr(tmp_path):
    return WorkspaceManager(root=tmp_path)


class TestWorkspaceManager:
    def test_create_returns_isolated_directory(self, mgr):
        path = mgr.create("TASK-1", "abcdef1234567890")
        assert path.exists()
        assert path.is_dir()
        assert path.name.startswith("TASK-1_")
        assert "abcdef12" in path.name  # first 8 trace chars

    def test_create_sanitizes_task_id(self, mgr):
        path = mgr.create("PROJ/123<>", "tid")
        assert path.exists()
        # Path-unsafe chars replaced
        for bad in "/<>":
            assert bad not in path.name

    def test_destroy_removes_directory(self, mgr):
        path = mgr.create("TASK-X", "trace01")
        (path / "file.txt").write_text("hi")
        mgr.destroy(path)
        assert not path.exists()

    def test_destroy_refuses_path_outside_root(self, mgr, tmp_path):
        outside = tmp_path.parent / "elsewhere"
        outside.mkdir(exist_ok=True)
        try:
            with pytest.raises(ValueError):
                mgr.destroy(outside)
        finally:
            try:
                outside.rmdir()
            except OSError:
                pass

    def test_destroy_silently_ignores_missing_path(self, mgr, tmp_path):
        ghost = tmp_path / "never_existed"
        mgr.destroy(ghost)  # no exception
        assert not ghost.exists()
