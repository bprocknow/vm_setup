from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kernelvm.errors import AppError
from kernelvm.models import RunMetadata, RuntimeInfo
from kernelvm.runs import save_metadata
from kernelvm.tui import (
    TuiState,
    _key_to_action,
    _list_static_configs,
    _match_static_config,
    _refresh_live_console,
    perform_action,
    refresh_selection,
    select_initial_run,
)


class TuiTests(unittest.TestCase):
    def test_select_initial_run_prefers_active_running_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            work_root = Path(tmpdir) / "work"
            self._save_metadata(work_root, "old-stopped", state="stopped", updated_at="2026-01-02T00:00:00+00:00")
            self._save_metadata(
                work_root,
                "active-run",
                state="running",
                updated_at="2026-01-01T00:00:00+00:00",
                pid=os.getpid(),
            )

            selected = select_initial_run(work_root)

            self.assertIsNotNone(selected)
            self.assertEqual(selected.run_id, "active-run")

    def test_select_initial_run_uses_most_recent_when_none_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            work_root = Path(tmpdir) / "work"
            self._save_metadata(work_root, "older", state="stopped", updated_at="2026-01-01T00:00:00+00:00")
            self._save_metadata(work_root, "newer", state="failed", updated_at="2026-01-03T00:00:00+00:00")

            selected = select_initial_run(work_root)

            self.assertIsNotNone(selected)
            self.assertEqual(selected.run_id, "newer")

    def test_create_action_uses_existing_create_run_and_selects_new_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            work_root = root / "work"
            static_configs = root / "static_configs"
            static_configs.mkdir()
            config_path = static_configs / "config.yaml"
            config_path.write_text("vm_name: test\n", encoding="utf-8")
            state = TuiState(work_root=work_root, static_configs_dir=static_configs)

            def fake_create_run(path: Path, work: Path, *, verbose: bool = False) -> str:
                self.assertEqual(path, config_path)
                self.assertEqual(work, work_root)
                self._save_metadata(work_root, "run-new", state="running", pid=os.getpid())
                return "run-new"

            with mock.patch("kernelvm.cli.create_run", side_effect=fake_create_run):
                should_exit = perform_action(state, "create", prompt=lambda _prompt: "1")

            self.assertFalse(should_exit)
            self.assertEqual(state.selected_run_id, "run-new")
            self.assertEqual(state.create_config_path, config_path)
            self.assertIn("Created run run-new", state.action_log)

    def test_create_action_rejects_paths_outside_static_configs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            static_configs = root / "static_configs"
            static_configs.mkdir()
            (static_configs / "allowed.yaml").write_text("vm_name: test\n", encoding="utf-8")
            state = TuiState(work_root=root / "work", static_configs_dir=static_configs)

            with mock.patch("kernelvm.cli.create_run") as create_run:
                perform_action(state, "create", prompt=lambda _prompt: str(root / "other.yaml"))

            create_run.assert_not_called()
            self.assertIn("Unknown static config selection", state.error or "")

    def test_static_config_listing_only_includes_yaml_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            static_configs = Path(tmpdir) / "static_configs"
            static_configs.mkdir()
            yaml_config = static_configs / "a.yaml"
            yaml_config.write_text("vm_name: test\n", encoding="utf-8")
            (static_configs / "b.yml").write_text("vm_name: test\n", encoding="utf-8")
            (static_configs / "example.yaml.template").write_text("vm_name: test\n", encoding="utf-8")

            self.assertEqual(_list_static_configs(static_configs), [yaml_config])

    def test_static_config_selection_accepts_number_filename_or_stem(self) -> None:
        configs = [Path("one.yaml"), Path("two.yaml")]

        self.assertEqual(_match_static_config("2", configs), Path("two.yaml"))
        self.assertEqual(_match_static_config("one.yaml", configs), Path("one.yaml"))
        self.assertEqual(_match_static_config("one", configs), Path("one.yaml"))

    def test_start_stop_refresh_actions_dispatch_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            work_root = Path(tmpdir) / "work"
            self._save_metadata(work_root, "run-1", state="stopped")
            state = TuiState(work_root=work_root, selected_run_id="run-1")

            with (
                mock.patch("kernelvm.cli.start_existing_run") as start_existing_run,
                mock.patch("kernelvm.cli.stop_existing_run") as stop_existing_run,
            ):
                perform_action(state, "start")
                perform_action(state, "stop")
                perform_action(state, "refresh")

            start_existing_run.assert_called_once_with("run-1", work_root)
            stop_existing_run.assert_called_once_with("run-1", work_root)
            self.assertIn("Refreshed status", state.action_log)

    def test_destroy_requires_confirmation_before_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            work_root = Path(tmpdir) / "work"
            self._save_metadata(work_root, "run-1", state="stopped")
            state = TuiState(work_root=work_root, selected_run_id="run-1")

            with mock.patch("kernelvm.cli.destroy_run") as destroy_run:
                perform_action(state, "destroy-request")
                destroy_run.assert_not_called()
                self.assertEqual(state.confirm_destroy_run_id, "run-1")

                perform_action(state, "destroy-confirm")

            destroy_run.assert_called_once_with("run-1", work_root)
            self.assertIsNone(state.confirm_destroy_run_id)
            self.assertIn("Destroyed run run-1", state.action_log)

    def test_action_errors_are_reported_in_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            work_root = Path(tmpdir) / "work"
            self._save_metadata(work_root, "run-1", state="stopped")
            state = TuiState(work_root=work_root, selected_run_id="run-1")

            with mock.patch("kernelvm.cli.start_existing_run", side_effect=AppError("boom")):
                should_exit = perform_action(state, "start")

            self.assertFalse(should_exit)
            self.assertEqual(state.error, "boom")
            self.assertEqual(state.detail_lines, ["boom"])

    def test_ssh_info_action_uses_existing_formatter(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            work_root = Path(tmpdir) / "work"
            self._save_metadata(work_root, "run-1", state="stopped")
            state = TuiState(work_root=work_root, selected_run_id="run-1")

            with mock.patch("kernelvm.cli.format_ssh_info", return_value="ssh details"):
                perform_action(state, "ssh-info")

            self.assertEqual(state.detail_lines, ["ssh details"])

    def test_console_info_action_shows_recent_console_log_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            work_root = Path(tmpdir) / "work"
            run_root = self._save_metadata(work_root, "run-1", state="stopped")
            (run_root / "serial" / "console.log").write_text("boot line 1\nboot line 2\n", encoding="utf-8")
            state = TuiState(work_root=work_root, selected_run_id="run-1")

            perform_action(state, "console-info")

            self.assertIn(f"Serial log: {run_root / 'serial' / 'console.log'}", state.detail_lines)
            self.assertIn("Live console log for run run-1", state.detail_lines)
            self.assertIn("Press q, b, or Esc to return to the main TUI.", state.detail_lines)
            self.assertIn("Last 2 line(s) from serial log:", state.detail_lines)
            self.assertIn("boot line 1", state.detail_lines)
            self.assertIn("boot line 2", state.detail_lines)
            self.assertEqual(state.live_console_run_id, "run-1")

    def test_live_console_log_refreshes_and_exits_to_main_tui(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            work_root = Path(tmpdir) / "work"
            run_root = self._save_metadata(work_root, "run-1", state="stopped")
            console_log = run_root / "serial" / "console.log"
            console_log.write_text("first line\n", encoding="utf-8")
            state = TuiState(work_root=work_root, selected_run_id="run-1")

            perform_action(state, "console-info")
            console_log.write_text("first line\nsecond line\n", encoding="utf-8")
            _refresh_live_console(state)

            self.assertIn("second line", state.detail_lines)
            self.assertEqual(_key_to_action(ord("q"), state), "console-exit")
            self.assertEqual(_key_to_action(ord("b"), state), "console-exit")
            self.assertEqual(_key_to_action(27, state), "console-exit")

            perform_action(state, "console-exit")

            self.assertIsNone(state.live_console_run_id)
            self.assertIn("run_id: run-1", state.detail_lines)

    def test_console_info_action_reports_missing_console_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            work_root = Path(tmpdir) / "work"
            run_root = self._save_metadata(work_root, "run-1", state="stopped")
            state = TuiState(work_root=work_root, selected_run_id="run-1")

            perform_action(state, "console-info")

            self.assertIn(f"Serial log does not exist yet: {run_root / 'serial' / 'console.log'}", state.detail_lines)

    def test_refresh_selection_tracks_available_run_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            work_root = Path(tmpdir) / "work"
            self._save_metadata(work_root, "older", state="stopped", updated_at="2026-01-01T00:00:00+00:00")
            self._save_metadata(work_root, "newer", state="stopped", updated_at="2026-01-02T00:00:00+00:00")
            state = TuiState(work_root=work_root)

            selected = refresh_selection(state)

            self.assertIsNotNone(selected)
            self.assertEqual(state.selected_run_id, "newer")
            self.assertEqual(state.run_ids, ["newer", "older"])

    def _save_metadata(
        self,
        work_root: Path,
        run_id: str,
        *,
        state: str,
        updated_at: str = "2026-01-01T00:00:00+00:00",
        pid: int | None = None,
    ) -> Path:
        run_root = work_root / run_id
        for subdir in ("config", "logs", "serial", "cloud-init", "overlay", "artifacts"):
            (run_root / subdir).mkdir(parents=True, exist_ok=True)
        metadata = RunMetadata(
            run_id=run_id,
            vm_name=f"vm-{run_id}",
            hostname=f"host-{run_id}",
            state=state,
            created_at="2026-01-01T00:00:00+00:00",
            updated_at=updated_at,
            config_path=str(run_root / "config" / "input-config.yaml"),
            normalized_config_path=str(run_root / "config" / "normalized-config.yaml"),
            base_image_path=str(run_root / "base.qcow2"),
            overlay_path=str(run_root / "overlay" / "overlay.qcow2"),
            bridge_name="br0",
            mac_address="52:54:00:12:34:56",
            detected_ip=None,
            disk_bus="virtio",
            net_model="virtio",
            vcpus=2,
            memory_mb=2048,
            disk_size_gb=20,
            paths={
                "root": str(run_root),
                "config_dir": str(run_root / "config"),
                "logs_dir": str(run_root / "logs"),
                "serial_dir": str(run_root / "serial"),
                "cloud_init_dir": str(run_root / "cloud-init"),
                "overlay_dir": str(run_root / "overlay"),
                "artifacts_dir": str(run_root / "artifacts"),
            },
            kernel_artifacts={},
            runtime=RuntimeInfo(
                pid=pid,
                serial_socket=str(run_root / "serial" / "console.sock"),
                serial_log=str(run_root / "serial" / "console.log"),
            ),
            errors=[],
        )
        save_metadata(metadata)
        return run_root
