"""Top-level daemon: capture scheduler + timeline aggregator + session cutter.

The v2 writer is driven by session boundaries. ``SessionManager.on_session_end``
(wired in ``session/tick.py``) spawns the S2 reducer on a daemon thread, and
the reducer's success callback kicks the classifier. No periodic writer loop
is needed — each session produces exactly one reducer + classifier pass.
"""

from __future__ import annotations

import asyncio
import os
import signal
from collections.abc import Callable
from contextlib import suppress

from . import paths
from .capture import scheduler as capture_scheduler
from .capture.privacy_diagnostics import PrivacyDiagnosticsServer
from .capture.privacy_diagnostics_guard import (
    DiagnosticsGuardSnapshot,
    DiagnosticsLeaseManager,
)
from .capture.privacy_overlay import PrivacyOverlayClient
from .capture.protection_monitor import PrivacyProtectionMonitor
from .config import Config
from .logger import get
from .session import tick as session_tick
from .timeline import tick as timeline_tick

logger = get("openchronicle.daemon")


def _build_protection_monitor(
    cfg: Config,
    *,
    diagnostics_guard_reader: Callable[[], DiagnosticsGuardSnapshot] | None = None,
    guard_only: bool = False,
) -> PrivacyProtectionMonitor | None:
    if cfg.capture.screenshot_privacy_mode == "off" and not guard_only:
        return None
    return PrivacyProtectionMonitor(
        cfg.capture,
        config_path=paths.config_file(),
        overlay=PrivacyOverlayClient(),
        diagnostics_guard_reader=diagnostics_guard_reader,
        diagnostics_guard_only=guard_only,
    )


async def _mcp_loop(cfg: Config) -> None:
    """Host the MCP server inside the daemon. On crash, back off and restart."""
    from .mcp import server as mcp_server

    delay = 2.0
    while True:
        try:
            logger.info("mcp server starting (%s)", cfg.mcp.transport)
            await mcp_server.run_async(cfg)
            logger.info("mcp server exited cleanly")
            return
        except asyncio.CancelledError:
            raise
        except OSError as exc:
            logger.error(
                "mcp server failed to bind %s:%d — %s",
                cfg.mcp.host, cfg.mcp.port, exc,
            )
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("mcp server crashed: %s (restarting in %.0fs)", exc, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60.0)


async def _run(cfg: Config, *, capture_only: bool = False) -> None:
    paths.ensure_dirs()
    paths.pid_file().write_text(str(os.getpid()))

    protection_monitor: PrivacyProtectionMonitor | None = None
    diagnostics_server: PrivacyDiagnosticsServer | None = None
    lease_manager: DiagnosticsLeaseManager | None = None
    session_manager = None
    tasks: list[asyncio.Task] = []
    done_task: asyncio.Task | None = None
    try:
        lease_manager = DiagnosticsLeaseManager(paths.privacy_diagnostics_guard())
        loaded_guard = lease_manager.load()
        normal_protection_enabled = cfg.capture.screenshot_privacy_mode != "off"
        persisted_guard_requires_protection = (
            loaded_guard.fail_closed_all or bool(loaded_guard.display_ids)
        )
        if normal_protection_enabled or persisted_guard_requires_protection:
            protection_monitor = _build_protection_monitor(
                cfg,
                diagnostics_guard_reader=lease_manager.snapshot,
                guard_only=not normal_protection_enabled,
            )
        if protection_monitor is not None:
            if lease_manager is None:
                raise RuntimeError("privacy diagnostics lease manager is unavailable")
            diagnostics_server = PrivacyDiagnosticsServer(
                paths.privacy_diagnostics_socket(),
                lease_manager,
                request_refresh=protection_monitor.request_refresh,
                wait_for_display_protection=(
                    protection_monitor.wait_for_display_protection
                ),
            )
            protection_monitor.add_decision_listener(diagnostics_server.publish)
            protection_monitor.start()
            diagnostics_server.start()

        # SessionManager observes every capture-worthy event and fires the
        # reducer via its on_session_end callback. Built even when
        # capture_only is true so session rows still land on disk.
        session_manager = session_tick.build_manager(cfg)

        tasks.append(
            asyncio.create_task(
                session_tick.run_check_cuts(cfg, session_manager), name="session",
            )
        )
        tasks.append(
            asyncio.create_task(
                session_tick.run_daily_safety_net(cfg, session_manager),
                name="daily-safety-net",
            )
        )
        if not capture_only:
            tasks.append(
                asyncio.create_task(timeline_tick.run_forever(cfg), name="timeline")
            )
            tasks.append(
                asyncio.create_task(
                    session_tick.run_flush_tick(cfg, session_manager), name="flush",
                )
            )
            tasks.append(
                asyncio.create_task(
                    session_tick.run_classifier_tick(cfg, session_manager),
                    name="classifier-tick",
                )
            )
        if cfg.mcp.auto_start and cfg.mcp.transport in ("sse", "streamable-http"):
            tasks.append(asyncio.create_task(_mcp_loop(cfg), name="mcp"))
        tasks.append(
            asyncio.create_task(
                capture_scheduler.run_forever(
                    cfg.capture,
                    pre_capture_hook=session_manager.on_event,
                    protection_monitor=protection_monitor,
                ),
                name="capture",
            )
        )

        stop = asyncio.Event()

        def _handle_stop() -> None:
            logger.info("shutdown signal received")
            stop.set()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            with suppress(NotImplementedError):
                loop.add_signal_handler(sig, _handle_stop)

        done_task = asyncio.create_task(stop.wait())
        await asyncio.wait(
            [done_task, *tasks], return_when=asyncio.FIRST_COMPLETED
        )
    finally:
        if diagnostics_server is not None:
            with suppress(Exception):
                diagnostics_server.stop()
        if done_task is not None:
            done_task.cancel()
        for task in tasks:
            task.cancel()
        cleanup_tasks = [*tasks, *([done_task] if done_task is not None else [])]
        if cleanup_tasks:
            with suppress(asyncio.CancelledError):
                await asyncio.gather(*cleanup_tasks, return_exceptions=True)

        # Flush the currently open session so its S2 reducer has a chance
        # to run. The daemon-thread reducer spawned by the callback will be
        # killed when the process exits, but a row with status='ended'
        # survives and the next boot's safety-net picks it up.
        if session_manager is not None:
            with suppress(Exception):
                session_manager.force_end(reason="daemon-shutdown")

        if protection_monitor is not None:
            with suppress(Exception):
                protection_monitor.stop()
        with suppress(FileNotFoundError):
            paths.pid_file().unlink()
        logger.info("daemon stopped")


def run(cfg: Config, *, capture_only: bool = False) -> None:
    asyncio.run(_run(cfg, capture_only=capture_only))
