"""Interactive terminal UI for managing kernelvm runs."""

from __future__ import annotations

import curses
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from .errors import AppError, ValidationError
from .models import RunMetadata
from .qemu import attach_console
from .runs import list_runs, load_metadata, refresh_runtime_state


MAIN_LOOP_TIMEOUT_MS = 1000


@dataclass(slots=True)
class TuiState:
    work_root: Path
    static_configs_dir: Path = Path("static_configs")
    create_config_path: Path | None = None
    verbose: bool = False
    selected_run_id: str | None = None
    run_ids: list[str] = field(default_factory=list)
    detail_lines: list[str] = field(default_factory=list)
    action_log: list[str] = field(default_factory=list)
    error: str | None = None
    confirm_destroy_run_id: str | None = None
    live_console_run_id: str | None = None


def run_tui(work_root: Path, *, verbose: bool = False) -> int:
    """Start the curses TUI and return a process-style exit code."""
    state = TuiState(
        work_root=work_root.expanduser(),
        static_configs_dir=Path("static_configs"),
        verbose=verbose,
    )
    try:
        curses.wrapper(_run_loop, state)
    except curses.error as exc:
        print(f"Could not start terminal UI: {exc}", file=sys.stderr)
        return 1
    return 0


def select_initial_run(work_root: Path) -> RunMetadata | None:
    """Select the active run, or the most recently updated run when none is active."""
    runs = [refresh_runtime_state(metadata) for metadata in list_runs(work_root)]
    if not runs:
        return None
    running = [metadata for metadata in runs if metadata.state == "running"]
    if running:
        return max(running, key=_metadata_sort_key)
    return max(runs, key=_metadata_sort_key)


def refresh_selection(state: TuiState) -> RunMetadata | None:
    runs = [refresh_runtime_state(metadata) for metadata in list_runs(state.work_root)]
    state.run_ids = [metadata.run_id for metadata in sorted(runs, key=_metadata_sort_key, reverse=True)]
    if not runs:
        state.selected_run_id = None
        return None

    selected = None
    if state.selected_run_id:
        selected = next((metadata for metadata in runs if metadata.run_id == state.selected_run_id), None)
    if selected is None:
        selected = select_initial_run(state.work_root)
    state.selected_run_id = selected.run_id if selected else None
    return selected


def perform_action(
    state: TuiState,
    action: str,
    *,
    prompt: Callable[[str], str | None] | None = None,
) -> bool:
    """Perform a TUI action. Returns True when the TUI should exit."""
    try:
        state.error = None
        if action not in {"destroy-confirm", "console-exit"}:
            state.confirm_destroy_run_id = None

        if action == "console-exit":
            state.live_console_run_id = None
            metadata = refresh_selection(state)
            state.detail_lines = _status_lines(metadata)
            _log(state, "Returned from console log")
            return False

        if action == "refresh":
            metadata = refresh_selection(state)
            state.detail_lines = _status_lines(metadata)
            _log(state, "Refreshed status")
            return False

        if action == "create":
            _action_create(state, prompt=prompt)
            return False

        metadata = _require_selected_metadata(state)

        if action == "start":
            _import_cli().start_existing_run(metadata.run_id, state.work_root)
            state.selected_run_id = metadata.run_id
            refreshed = refresh_selection(state)
            state.detail_lines = _status_lines(refreshed)
            _log(state, f"Started run {metadata.run_id}")
            return False

        if action == "stop":
            _import_cli().stop_existing_run(metadata.run_id, state.work_root)
            state.selected_run_id = metadata.run_id
            refreshed = refresh_selection(state)
            state.detail_lines = _status_lines(refreshed)
            _log(state, f"Stopped run {metadata.run_id}")
            return False

        if action == "destroy-request":
            state.confirm_destroy_run_id = metadata.run_id
            _log(state, f"Confirm destroy for {metadata.run_id} with y, or cancel with n")
            return False

        if action == "destroy-confirm":
            _action_destroy(state, metadata)
            return False

        if action == "destroy-cancel":
            state.confirm_destroy_run_id = None
            _log(state, "Destroy cancelled")
            return False

        if action == "ssh-info":
            state.detail_lines = _import_cli().format_ssh_info(metadata).splitlines()
            _log(state, f"Displayed SSH info for {metadata.run_id}")
            return False

        if action == "console-info":
            state.live_console_run_id = metadata.run_id
            state.detail_lines = _console_log_lines(metadata, live=True)
            _log(state, f"Tailing console log for {metadata.run_id}")
            return False

        if action == "console-attach":
            state.detail_lines = ["Attaching to serial console...", "Return here after attach exits."]
            _log(state, f"Attaching console for {metadata.run_id}")
            raise ConsoleAttachRequested(metadata.run_id)

        if action == "next-run":
            _cycle_selection(state, step=1)
            return False

        if action == "previous-run":
            _cycle_selection(state, step=-1)
            return False

        if action == "quit":
            return True

        _log(state, f"Unknown action: {action}")
        return False
    except ValidationError as exc:
        state.error = "\n".join(exc.errors)
        state.detail_lines = exc.errors
        _log(state, "Validation failed")
    except AppError as exc:
        state.error = exc.message
        state.detail_lines = [exc.message]
        _log(state, f"Error: {exc.message}")
    return False


class ConsoleAttachRequested(Exception):
    def __init__(self, run_id: str) -> None:
        super().__init__(run_id)
        self.run_id = run_id


def _run_loop(screen, state: TuiState) -> None:
    curses.curs_set(0)
    screen.keypad(True)
    screen.timeout(MAIN_LOOP_TIMEOUT_MS)
    refresh_selection(state)
    state.detail_lines = _status_lines(_selected_metadata(state))

    while True:
        if state.live_console_run_id:
            _refresh_live_console(state)
        _draw(screen, state)
        key = screen.getch()
        if key == -1:
            continue
        action = _key_to_action(key, state)
        if action is None:
            continue
        try:
            should_exit = perform_action(state, action, prompt=lambda prompt_text: _prompt_after_draw(screen, state, prompt_text))
        except ConsoleAttachRequested as exc:
            curses.def_prog_mode()
            curses.endwin()
            try:
                metadata = load_metadata(state.work_root, exc.run_id)
                state.detail_lines = attach_console(metadata, attach=True).splitlines()
                _log(state, f"Console attach finished for {exc.run_id}")
            finally:
                curses.reset_prog_mode()
                screen.keypad(True)
                curses.curs_set(0)
            continue
        if should_exit:
            return


def _prompt_after_draw(screen, state: TuiState, prompt: str) -> str | None:
    _draw(screen, state)
    return _prompt(screen, prompt)


def _action_create(state: TuiState, *, prompt: Callable[[str], str | None] | None) -> None:
    config_path = _choose_static_config(state, prompt=prompt)
    if config_path is None:
        raise AppError("Create requires choosing a static config file")

    run_id = _import_cli().create_run(config_path, state.work_root, verbose=state.verbose)
    state.create_config_path = config_path
    state.selected_run_id = run_id
    metadata = refresh_selection(state)
    state.detail_lines = _status_lines(metadata)
    _log(state, f"Created run {run_id}")


def _choose_static_config(state: TuiState, *, prompt: Callable[[str], str | None] | None) -> Path | None:
    configs = _list_static_configs(state.static_configs_dir)
    if not configs:
        raise AppError(f"No *.yaml files found in {state.static_configs_dir}")
    state.detail_lines = [
        f"Choose a config from {state.static_configs_dir}:",
        *(f"{index}. {path.name}" for index, path in enumerate(configs, start=1)),
    ]
    if prompt is None:
        if len(configs) == 1:
            return configs[0]
        raise AppError("Create requires an interactive config selection")

    selection = prompt("Config number/name: ")
    if selection is None:
        _log(state, "Create cancelled: no config selected")
        return None
    return _match_static_config(selection, configs)


def _list_static_configs(config_dir: Path) -> list[Path]:
    config_dir = config_dir.expanduser()
    try:
        return sorted(path for path in config_dir.glob("*.yaml") if path.is_file())
    except OSError as exc:
        raise AppError(f"Could not read static config directory {config_dir}: {exc}") from exc


def _match_static_config(selection: str, configs: list[Path]) -> Path:
    selection = selection.strip()
    if selection.isdigit():
        index = int(selection)
        if 1 <= index <= len(configs):
            return configs[index - 1]

    for path in configs:
        if selection in {path.name, path.stem}:
            return path

    valid = ", ".join(path.name for path in configs)
    raise AppError(f"Unknown static config selection: {selection}. Choose one of: {valid}")


def _action_destroy(state: TuiState, metadata: RunMetadata) -> None:
    if state.confirm_destroy_run_id != metadata.run_id:
        state.confirm_destroy_run_id = metadata.run_id
        _log(state, f"Confirm destroy for {metadata.run_id} with y")
        return
    _import_cli().destroy_run(metadata.run_id, state.work_root)
    _log(state, f"Destroyed run {metadata.run_id}")
    state.confirm_destroy_run_id = None
    state.selected_run_id = None
    refreshed = refresh_selection(state)
    state.detail_lines = _status_lines(refreshed)


def _require_selected_metadata(state: TuiState) -> RunMetadata:
    metadata = _selected_metadata(state)
    if metadata is None:
        raise AppError("No run is selected")
    return metadata


def _selected_metadata(state: TuiState) -> RunMetadata | None:
    if not state.selected_run_id:
        return refresh_selection(state)
    try:
        return refresh_runtime_state(load_metadata(state.work_root, state.selected_run_id))
    except AppError:
        return refresh_selection(state)


def _status_lines(metadata: RunMetadata | None) -> list[str]:
    if metadata is None:
        return [
            "No VM run is currently available.",
            "Use c to create a run when a config file is available.",
        ]
    return _import_cli().format_status(metadata).splitlines()


def _console_log_lines(metadata: RunMetadata, *, max_lines: int = 80, live: bool = False) -> list[str]:
    console_info = attach_console(metadata, attach=False).splitlines()
    if live:
        console_info = [
            f"Live console log for run {metadata.run_id}",
            "Press q, b, or Esc to return to the main TUI.",
            "",
            *console_info,
        ]
    serial_log = metadata.runtime.serial_log
    if not serial_log:
        return [*console_info, "", "Serial log is not configured for this run."]

    log_path = Path(serial_log)
    if not log_path.exists():
        return [*console_info, "", f"Serial log does not exist yet: {log_path}"]
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return [*console_info, "", f"Could not read serial log: {exc}"]

    if not lines:
        return [*console_info, "", "Serial log is empty."]

    visible_lines = lines[-max_lines:]
    omitted = len(lines) - len(visible_lines)
    header = f"Last {len(visible_lines)} line(s) from serial log:"
    if omitted:
        header = f"Last {len(visible_lines)} line(s) from serial log ({omitted} earlier line(s) omitted):"
    return [*console_info, "", header, *visible_lines]


def _refresh_live_console(state: TuiState) -> None:
    if not state.live_console_run_id:
        return
    try:
        metadata = refresh_runtime_state(load_metadata(state.work_root, state.live_console_run_id))
    except AppError as exc:
        state.live_console_run_id = None
        state.error = exc.message
        state.detail_lines = [exc.message]
        _log(state, f"Console log stopped: {exc.message}")
        return
    state.detail_lines = _console_log_lines(metadata, live=True)


def _cycle_selection(state: TuiState, *, step: int) -> None:
    if not state.run_ids:
        refresh_selection(state)
    if not state.run_ids:
        _log(state, "No runs available")
        return
    if state.selected_run_id not in state.run_ids:
        state.selected_run_id = state.run_ids[0]
    else:
        index = state.run_ids.index(state.selected_run_id)
        state.selected_run_id = state.run_ids[(index + step) % len(state.run_ids)]
    state.detail_lines = _status_lines(_selected_metadata(state))
    _log(state, f"Selected run {state.selected_run_id}")


def _key_to_action(key: int, state: TuiState) -> str | None:
    if state.live_console_run_id:
        if key in (ord("q"), ord("Q"), ord("b"), ord("B"), 27):
            return "console-exit"
        return None

    if state.confirm_destroy_run_id:
        if key in (ord("y"), ord("Y")):
            return "destroy-confirm"
        if key in (ord("n"), ord("N"), 27):
            return "destroy-cancel"
    mapping = {
        ord("q"): "quit",
        ord("Q"): "quit",
        ord("r"): "refresh",
        ord("R"): "refresh",
        ord("c"): "create",
        ord("C"): "create",
        ord("s"): "start",
        ord("S"): "start",
        ord("t"): "stop",
        ord("T"): "stop",
        ord("d"): "destroy-request",
        ord("D"): "destroy-request",
        ord("i"): "ssh-info",
        ord("I"): "ssh-info",
        ord("x"): "console-info",
        ord("X"): "console-info",
        ord("a"): "console-attach",
        ord("A"): "console-attach",
        ord("j"): "next-run",
        curses.KEY_DOWN: "next-run",
        ord("k"): "previous-run",
        curses.KEY_UP: "previous-run",
    }
    return mapping.get(key)


def _draw(screen, state: TuiState) -> None:
    screen.erase()
    height, width = screen.getmaxyx()
    _addstr(screen, 0, 0, "kernelvm TUI", width, curses.A_BOLD)
    config = str(state.create_config_path) if state.create_config_path else "none"
    selected = state.selected_run_id or "none"
    _addstr(screen, 1, 0, f"work_root: {state.work_root}", width)
    _addstr(screen, 2, 0, f"create_config: {config}", width)
    _addstr(screen, 3, 0, f"selected_run: {selected}", width)

    if state.live_console_run_id:
        actions = "live console log | q/b/Esc back to TUI"
    else:
        actions = "q quit | r refresh | c create | s start | t stop | d destroy | i ssh | x live log | a attach | j/k select"
    _addstr(screen, 5, 0, actions, width, curses.A_REVERSE)

    if state.confirm_destroy_run_id:
        _addstr(screen, 7, 0, f"Confirm destroy {state.confirm_destroy_run_id}: y/n", width, curses.A_BOLD)
        detail_start = 9
    else:
        detail_start = 7

    max_detail = max(0, height - detail_start - 7)
    for offset, line in enumerate(state.detail_lines[:max_detail]):
        _addstr(screen, detail_start + offset, 0, line, width)

    log_start = max(detail_start + max_detail + 1, height - 6)
    _addstr(screen, log_start, 0, "Recent actions", width, curses.A_BOLD)
    for offset, line in enumerate(state.action_log[-4:]):
        _addstr(screen, log_start + offset + 1, 0, line, width)
    if state.error:
        _addstr(screen, height - 1, 0, state.error.replace("\n", " | "), width, curses.A_BOLD)
    screen.refresh()


def _prompt(screen, prompt: str) -> str | None:
    height, width = screen.getmaxyx()
    screen.timeout(-1)
    curses.echo()
    curses.curs_set(1)
    try:
        screen.move(height - 1, 0)
        screen.clrtoeol()
        _addstr(screen, height - 1, 0, prompt, width)
        value = screen.getstr(height - 1, min(len(prompt), width - 1), max(1, width - len(prompt) - 1))
        return value.decode("utf-8").strip() or None
    finally:
        curses.noecho()
        curses.curs_set(0)
        screen.timeout(MAIN_LOOP_TIMEOUT_MS)


def _addstr(screen, y: int, x: int, text: str, width: int, attr: int = 0) -> None:
    if y < 0 or x >= width:
        return
    max_width = max(0, width - x - 1)
    if max_width == 0:
        return
    try:
        screen.addstr(y, x, text[:max_width], attr)
    except curses.error:
        pass


def _metadata_sort_key(metadata: RunMetadata) -> tuple[str, str]:
    try:
        updated = datetime.fromisoformat(metadata.updated_at).isoformat()
    except ValueError:
        updated = metadata.updated_at
    return (updated, metadata.run_id)


def _log(state: TuiState, message: str) -> None:
    state.action_log.append(message)
    del state.action_log[:-20]


def _import_cli():
    from . import cli

    return cli
