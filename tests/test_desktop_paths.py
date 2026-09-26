import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from desktop import paths
from paper_trading import storage


class DesktopPathTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _write_ledger(self, folder, marker="original"):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / paths.DB_FILENAME).write_text(marker, encoding="utf-8")
        (folder / "notes.txt").write_text("keep me", encoding="utf-8")

    def test_migrates_legacy_ledger_with_backup_and_leaves_source(self):
        legacy_root = self.root / "repo"
        legacy = legacy_root / "paper_trading_data"
        self._write_ledger(legacy, "legacy-bytes")
        user_root = self.root / "user"
        with patch.dict(
            os.environ,
            {"STOCK_COMP_USER_DATA_ROOT": str(user_root)},
            clear=False,
        ):
            destination, report = paths.prepare_paper_data_dir(
                extra_legacy_roots=[legacy_root],
                include_default_roots=False,
            )
        self.assertTrue((destination / paths.DB_FILENAME).is_file())
        self.assertEqual(
            (destination / paths.DB_FILENAME).read_text(encoding="utf-8"),
            "legacy-bytes",
        )
        self.assertEqual(
            legacy.joinpath(paths.DB_FILENAME).read_text(encoding="utf-8"),
            "legacy-bytes",
        )
        self.assertIsNotNone(report["backup"])
        self.assertTrue(Path(report["backup"]).joinpath(paths.DB_FILENAME).is_file())
        self.assertEqual(Path(report["migrated_from"]), legacy.resolve())

    def test_does_not_overwrite_existing_user_ledger(self):
        legacy_root = self.root / "repo"
        self._write_ledger(legacy_root / "paper_trading_data", "legacy")
        user_root = self.root / "user"
        current = user_root / "paper_trading_data"
        self._write_ledger(current, "already-here")
        with patch.dict(
            os.environ,
            {"STOCK_COMP_USER_DATA_ROOT": str(user_root)},
            clear=False,
        ):
            destination, report = paths.prepare_paper_data_dir(
                extra_legacy_roots=[legacy_root],
                include_default_roots=False,
            )
        self.assertEqual(
            (destination / paths.DB_FILENAME).read_text(encoding="utf-8"),
            "already-here",
        )
        self.assertIsNone(report["migrated_from"])
        self.assertTrue(report["skipped_overwrite"])
        self.assertEqual(
            (legacy_root / "paper_trading_data" / paths.DB_FILENAME).read_text(
                encoding="utf-8"
            ),
            "legacy",
        )

    def test_apply_runtime_env_points_storage_at_user_dir(self):
        paper_dir = self.root / "paper"
        cache_dir = self.root / "cache"
        with patch.dict(os.environ, {}, clear=False):
            paths.apply_runtime_data_env(paper_dir, cache_dir)
            resolved = storage._resolve_db_path()
            self.assertEqual(resolved, (paper_dir / paths.DB_FILENAME).resolve())
