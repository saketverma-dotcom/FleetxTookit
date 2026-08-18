"""Updater rollback-safety (v3.11).

Regression tests for the real incident: antivirus quarantined the downloaded
.exe.new mid-swap, the old exe had already been deleted, and the user was left
with a broken install ("Failed to load Python DLL"). The swap must now refuse
to start unless the downloaded file is present and non-empty.
"""
import os
import tempfile

from fleetx_toolkit import updater as U


class TestSwapRefusesBadDownload:
    def test_missing_file_refused(self):
        ok, msg = U.apply_update_and_restart("/tmp/definitely_missing_9f8a7b.new")
        assert ok is False
        assert "quarantined" in msg or "missing" in msg

    def test_empty_file_refused(self):
        p = tempfile.mktemp(suffix=".new")
        open(p, "wb").close()
        try:
            ok, msg = U.apply_update_and_restart(p)
            assert ok is False
        finally:
            os.remove(p)


class TestVersionCompare:
    def test_ver_tuple(self):
        assert U._ver_tuple("3.11") == (3, 11)
        assert U._ver_tuple("3.9.5") == (3, 9, 5)

    def test_newer_versions_sort_correctly(self):
        # 3.11 must be treated as newer than 3.9 (not string-compared)
        assert U._ver_tuple("3.11") > U._ver_tuple("3.9")
        assert U._ver_tuple("3.10") > U._ver_tuple("3.9.5")
        assert U._ver_tuple("4.0") > U._ver_tuple("3.11")
