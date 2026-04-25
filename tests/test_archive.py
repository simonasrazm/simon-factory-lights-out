"""Tests for src.archive — move-instead-of-delete."""

from src.archive import archive_to_logs


class TestArchiveToLogs:
    def test_moves_file_to_logs(self, tmp_path):
        sflo_dir = tmp_path / ".sflo"
        sflo_dir.mkdir()
        f = sflo_dir / "BUILD-STATUS.md"
        f.write_text("test content")

        archived = archive_to_logs(str(sflo_dir), [str(f)])

        assert archived == ["BUILD-STATUS.md"]
        assert not f.exists()
        assert (sflo_dir / "logs" / "BUILD-STATUS.md").read_text() == "test content"

    def test_moves_directory_to_logs(self, tmp_path):
        sflo_dir = tmp_path / ".sflo"
        sflo_dir.mkdir()
        d = sflo_dir / "subdir"
        d.mkdir()
        (d / "round-01.md").write_text("round 1")

        archived = archive_to_logs(str(sflo_dir), [str(d)])

        assert archived == ["subdir"]
        assert not d.exists()
        assert (sflo_dir / "logs" / "subdir" / "round-01.md").read_text() == "round 1"

    def test_skips_missing_paths(self, tmp_path):
        sflo_dir = tmp_path / ".sflo"
        sflo_dir.mkdir()
        archived = archive_to_logs(str(sflo_dir), [str(sflo_dir / "nonexistent.md")])
        assert archived == []

    def test_overwrites_existing_in_logs(self, tmp_path):
        sflo_dir = tmp_path / ".sflo"
        sflo_dir.mkdir()
        logs = sflo_dir / "logs"
        logs.mkdir()
        (logs / "old.md").write_text("old")

        f = sflo_dir / "old.md"
        f.write_text("new")

        archive_to_logs(str(sflo_dir), [str(f)])
        assert (logs / "old.md").read_text() == "new"

    def test_never_archives_logs_dir_itself(self, tmp_path):
        sflo_dir = tmp_path / ".sflo"
        sflo_dir.mkdir()
        logs = sflo_dir / "logs"
        logs.mkdir()

        archived = archive_to_logs(str(sflo_dir), [str(logs)])
        assert archived == []
        assert logs.exists()
