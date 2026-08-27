"""Capture scheduler: event-driven + heartbeat. Writes one JSON per tick to capture-buffer/."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import math
import queue
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .. import paths
from ..capture_pause import capture_is_paused
from ..config import CaptureConfig
from ..logger import get
from ..store import fts as fts_store
from . import ax_capture, privacy, s1_parser, screenshot, window_meta
from .event_dispatcher import EventDispatcher
from .protection import ProtectionState
from .protection_monitor import PrivacyProtectionMonitor, ProtectionDecision
from .watcher import AXWatcherProcess

logger = get("openchronicle.capture")
_WINDOW_FILTERED_PRIVACY_MODES = frozenset({"mask-window", "exclude-window"})


def _decision_is_terminal(_cfg: CaptureConfig, decision: ProtectionDecision) -> bool:
    snapshot = decision.snapshot
    return (
        snapshot.state is ProtectionState.PAUSED
        or (
            snapshot.state is ProtectionState.FAILED
            and decision.failure_capture_blocked
        )
    )


def _decision_blocks_ax(cfg: CaptureConfig, decision: ProtectionDecision) -> bool:
    snapshot = decision.snapshot
    if _decision_is_terminal(cfg, decision):
        return True
    if snapshot.state is ProtectionState.FAILED:
        return decision.failure_capture_blocked
    return snapshot.ax_blocked


def _valid_window_ids(window_ids: object, *, require_nonempty: bool) -> bool:
    if not isinstance(window_ids, tuple):
        return False
    if require_nonempty and not window_ids:
        return False
    return len(set(window_ids)) == len(window_ids) and all(
        isinstance(window_id, int)
        and not isinstance(window_id, bool)
        and 0 < window_id <= 0xFFFFFFFF
        for window_id in window_ids
    )


def _filtered_capture_is_eligible(
    cfg: CaptureConfig,
    decision: ProtectionDecision,
) -> bool:
    snapshot = decision.snapshot
    indicator_ids = decision.indicator_window_ids
    overlay_ids_eligible = (
        not indicator_ids
        if snapshot.indicator_style == "off"
        else _valid_window_ids(indicator_ids, require_nonempty=True)
    )
    return (
        cfg.screenshot_privacy_mode in _WINDOW_FILTERED_PRIVACY_MODES
        and snapshot.capture_mode == cfg.screenshot_monitor
        and snapshot.state is ProtectionState.PROTECTED
        and snapshot.window_filterable
        and not snapshot.diagnostics_guard_active
        and not snapshot.diagnostics_guard_invalid
        and decision.capture_confirmation_satisfied
        and overlay_ids_eligible
        and _valid_window_ids(
            tuple(snapshot.protected_window_ids),
            require_nonempty=True,
        )
        and len(snapshot.protected_window_ids) == len(snapshot.protected_window_regions)
    )


def _region_key(region: privacy.ScreenRegion) -> tuple[float, float, float, float]:
    return (region.left, region.top, region.width, region.height)


def _filtered_authorization_key(decision: ProtectionDecision) -> tuple[object, ...]:
    snapshot = decision.snapshot
    displays = tuple(
        sorted(
            (
                display.id,
                *_region_key(display.region),
                display.is_primary,
            )
            for display in snapshot.displays
        )
    )
    return (
        snapshot.state,
        snapshot.capture_mode,
        snapshot.indicator_style,
        snapshot.indicator_placement,
        displays,
        tuple(sorted(snapshot.protected_display_ids)),
        tuple(sorted(snapshot.protected_window_ids)),
        tuple(sorted(_region_key(region) for region in snapshot.protected_window_regions)),
        snapshot.window_filterable,
        snapshot.diagnostics_guard_active,
        snapshot.diagnostics_guard_invalid,
        decision.indicator_confirmed,
        tuple(sorted(decision.indicator_window_ids)),
    )


def _fallback_regions_are_valid(decision: ProtectionDecision) -> bool:
    snapshot = decision.snapshot
    if snapshot.state is ProtectionState.PROTECTED:
        if not snapshot.protected_display_ids:
            return False
        displays_by_id = {display.id: display for display in snapshot.displays}
        if len(displays_by_id) != len(snapshot.displays):
            return False
        if not snapshot.protected_display_ids <= displays_by_id.keys():
            return False
        regions = snapshot.protected_regions
        return len(regions) == len(snapshot.protected_display_ids) and all(
            all(
                math.isfinite(value)
                for value in (region.left, region.top, region.width, region.height)
            )
            and region.width > 0
            and region.height > 0
            for region in regions
        )
    return not snapshot.protected_display_ids and not snapshot.protected_regions


def _grab_current_monitor_screenshots(
    cfg: CaptureConfig,
    decision: ProtectionDecision,
) -> list[screenshot.Screenshot]:
    snapshot = decision.snapshot
    if snapshot.state is ProtectionState.FAILED:
        blocked_regions: list[privacy.ScreenRegion] = []
    elif not decision.capture_confirmation_satisfied:
        logger.warning("screenshot skipped: privacy indicator not confirmed")
        return []
    else:
        blocked_regions = snapshot.protected_regions
    return screenshot.grab_many(
        monitor_mode=cfg.screenshot_monitor,
        max_width=cfg.screenshot_max_width,
        jpeg_quality=cfg.screenshot_jpeg_quality,
        blocked_regions=blocked_regions,
    )


def _grab_fresh_skip_monitor_fallback(
    cfg: CaptureConfig,
    protection_monitor: PrivacyProtectionMonitor,
    *,
    category: str,
    current_decision: ProtectionDecision | None = None,
) -> tuple[list[screenshot.Screenshot], ProtectionDecision] | None:
    latest = current_decision or protection_monitor.decision_for_capture(force=True)
    if _decision_is_terminal(cfg, latest):
        return None
    if not latest.capture_confirmation_satisfied:
        logger.warning("screenshot fallback skipped: privacy indicator not confirmed")
        return [], latest
    if not _fallback_regions_are_valid(latest):
        logger.warning("screenshot fallback skipped: category=invalid_protected_regions")
        return [], latest

    authorization = _filtered_authorization_key(latest)
    logger.info("screenshot fallback: category=%s", category)
    shots = screenshot.grab_many(
        monitor_mode=cfg.screenshot_monitor,
        max_width=cfg.screenshot_max_width,
        jpeg_quality=cfg.screenshot_jpeg_quality,
        blocked_regions=latest.snapshot.protected_regions,
    )
    after_capture = protection_monitor.decision_for_capture(force=True)
    if _decision_is_terminal(cfg, after_capture):
        return None
    if (
        not after_capture.capture_confirmation_satisfied
        or not _fallback_regions_are_valid(after_capture)
        or _filtered_authorization_key(after_capture) != authorization
    ):
        logger.warning("screenshot fallback discarded: category=authorization_changed")
        return [], after_capture
    return shots, after_capture


def _grab_inactive_filtered_screenshots(
    cfg: CaptureConfig,
    protection_monitor: PrivacyProtectionMonitor,
    decision: ProtectionDecision,
) -> tuple[list[screenshot.Screenshot], ProtectionDecision] | None:
    if not decision.capture_confirmation_satisfied:
        logger.warning("screenshot skipped: privacy indicator clear not confirmed")
        return [], decision
    if not _fallback_regions_are_valid(decision):
        return [], decision

    authorization = _filtered_authorization_key(decision)
    shots = screenshot.grab_many(
        monitor_mode=cfg.screenshot_monitor,
        max_width=cfg.screenshot_max_width,
        jpeg_quality=cfg.screenshot_jpeg_quality,
        blocked_regions=[],
    )
    after_capture = protection_monitor.decision_for_capture(force=True)
    if _decision_is_terminal(cfg, after_capture):
        return None
    if (
        after_capture.snapshot.state is not ProtectionState.INACTIVE
        or not after_capture.capture_confirmation_satisfied
        or not _fallback_regions_are_valid(after_capture)
        or _filtered_authorization_key(after_capture) != authorization
    ):
        logger.warning("screenshot discarded: inactive authorization changed")
        return [], after_capture
    return shots, after_capture


def _now_iso() -> str:
    return datetime.now(UTC).astimezone().replace(microsecond=0).isoformat()


def _safe_filename(ts: str) -> str:
    return ts.replace(":", "-").replace("+", "p")


def _build_capture(
    cfg: CaptureConfig,
    provider: ax_capture.AXProvider,
    trigger: dict[str, Any] | None,
    *,
    protection_monitor: PrivacyProtectionMonitor | None = None,
) -> dict[str, Any] | None:
    """Build an enriched capture dict in memory. Returns None if capturing is paused."""
    paths.ensure_dirs()

    decision: ProtectionDecision | None = None
    if protection_monitor is None:
        if capture_is_paused():
            logger.info("capture skipped (paused)")
            return None
    else:
        decision = protection_monitor.decision_for_capture(force=True)
        if _decision_is_terminal(cfg, decision):
            return None

    ts = _now_iso()
    out: dict[str, Any] = {
        "timestamp": ts,
        "schema_version": 2,
        "trigger": trigger or {"event_type": "heartbeat"},
    }

    meta = window_meta.active_window()
    out["window_meta"] = {
        "app_name": meta.app_name,
        "title": meta.title,
        "bundle_id": meta.bundle_id,
    }

    reason = privacy.capture_denylist_reason(cfg, out)
    if reason is not None:
        logger.info("capture skipped (denylist: %s)", reason)
        return None

    if decision is not None and _decision_blocks_ax(cfg, decision):
        out["ax_skipped"] = "protected_display"
    else:
        if provider.available:
            result = provider.capture_frontmost(focused_window_only=True)
            if result is not None:
                out["ax_tree"] = result.raw_json
                out["ax_metadata"] = result.metadata
        else:
            out["ax_unavailable"] = True

        s1_parser.enrich(out)

    if decision is not None:
        latest = protection_monitor.decision_for_capture(force=False)
        if _decision_is_terminal(cfg, latest):
            return None
        if (
            latest.snapshot.generation != decision.snapshot.generation
            and _decision_blocks_ax(cfg, latest)
        ):
            logger.warning("capture skipped: latest privacy protection invalidated capture")
            return None
        decision = latest

    reason = privacy.capture_denylist_reason(cfg, out)
    if reason is not None:
        logger.info("capture skipped (denylist: %s)", reason)
        return None

    if cfg.include_screenshot:
        if decision is None:
            if cfg.screenshot_privacy_mode == "off":
                blocked_regions = []
            else:
                blocked_regions = privacy.sensitive_window_regions(cfg)
                if blocked_regions is None:
                    if (
                        cfg.screenshot_privacy_mode in _WINDOW_FILTERED_PRIVACY_MODES
                        or cfg.screenshot_privacy_fail_closed
                    ):
                        logger.warning(
                            "screenshot skipped: category=visible_window_inventory_unavailable"
                        )
                        return out
                    blocked_regions = []
            shots = screenshot.grab_many(
                monitor_mode=cfg.screenshot_monitor,
                max_width=cfg.screenshot_max_width,
                jpeg_quality=cfg.screenshot_jpeg_quality,
                blocked_regions=blocked_regions,
            )
        elif (
            cfg.screenshot_privacy_mode in _WINDOW_FILTERED_PRIVACY_MODES
            and _filtered_capture_is_eligible(cfg, decision)
        ):
            authorization = _filtered_authorization_key(decision)
            shots = screenshot.grab_filtered_many(
                monitor_mode=cfg.screenshot_monitor,
                privacy_mode=cfg.screenshot_privacy_mode,
                displays=decision.snapshot.displays,
                protected_window_ids=decision.snapshot.protected_window_ids,
                protected_window_regions=decision.snapshot.protected_window_regions,
                overlay_window_ids=decision.indicator_window_ids,
                max_width=cfg.screenshot_max_width,
                jpeg_quality=cfg.screenshot_jpeg_quality,
            )
            if shots is None:
                fallback = _grab_fresh_skip_monitor_fallback(
                    cfg,
                    protection_monitor,
                    category="filtered_helper_unavailable",
                )
                if fallback is None:
                    return None
                shots, decision = fallback
            else:
                latest = protection_monitor.decision_for_capture(force=True)
                if _decision_is_terminal(cfg, latest):
                    return None
                if (
                    not _filtered_capture_is_eligible(cfg, latest)
                    or _filtered_authorization_key(latest) != authorization
                ):
                    fallback = _grab_fresh_skip_monitor_fallback(
                        cfg,
                        protection_monitor,
                        category="filtered_authorization_changed",
                        current_decision=latest,
                    )
                    if fallback is None:
                        return None
                    shots, latest = fallback
                decision = latest
        elif cfg.screenshot_privacy_mode in _WINDOW_FILTERED_PRIVACY_MODES:
            if decision.snapshot.state is ProtectionState.INACTIVE:
                fallback = _grab_inactive_filtered_screenshots(
                    cfg,
                    protection_monitor,
                    decision,
                )
            else:
                fallback = _grab_fresh_skip_monitor_fallback(
                    cfg,
                    protection_monitor,
                    category="filtered_ineligible",
                )
            if fallback is None:
                return None
            shots, decision = fallback
        else:
            shots = _grab_current_monitor_screenshots(cfg, decision)
        if shots:
            shot_dicts = [screenshot.to_dict(shot) for shot in shots]
            if cfg.screenshot_monitor == "separate":
                out["screenshots"] = shot_dicts
            out["screenshot"] = shot_dicts[0]

    return out


def _write_capture(out: dict[str, Any]) -> Path:
    """Persist a built capture dict to the buffer, index it for search, and log."""
    ts = out["timestamp"]
    path = paths.capture_buffer_dir() / f"{_safe_filename(ts)}.json"
    path.write_text(json.dumps(out, ensure_ascii=False))
    _index_capture(path.stem, out)
    meta = out.get("window_meta") or {}
    logger.info(
        "capture ok: %s trigger=%s app=%r title=%r ax=%s screenshot=%s",
        path.name,
        (out.get("trigger") or {}).get("event_type"),
        meta.get("app_name"),
        (meta.get("title") or "")[:60],
        "ax_tree" in out,
        "screenshot" in out,
    )
    return path


def _index_capture(file_stem: str, out: dict[str, Any]) -> None:
    """Insert/upsert the capture's S1 fields into the FTS5 index.

    Failures here are non-fatal — a missed FTS row is recoverable via
    ``openchronicle rebuild-captures-index``; killing the capture worker
    over an indexing hiccup would lose the JSON too.
    """
    meta = out.get("window_meta") or {}
    focused = out.get("focused_element") or {}
    try:
        with fts_store.cursor() as conn:
            fts_store.insert_capture(
                conn,
                id=file_stem,
                timestamp=out.get("timestamp", ""),
                app_name=meta.get("app_name") or "",
                bundle_id=meta.get("bundle_id") or "",
                window_title=meta.get("title") or "",
                focused_role=focused.get("role") or "",
                focused_value=focused.get("value") or "",
                visible_text=out.get("visible_text") or "",
                url=out.get("url") or "",
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("captures FTS insert failed for %s: %s", file_stem, exc)


def _content_fingerprint(out: dict[str, Any]) -> str:
    """Hash the content-bearing fields of a capture for consecutive-duplicate detection.

    Excludes timestamp, trigger metadata, screenshots, and the raw ax_tree (which
    contains coordinate noise). Focuses on what actually drives downstream stages:
    the window identity + what the user can see + what they've typed.
    """
    meta = out.get("window_meta") or {}
    focused = out.get("focused_element") or {}
    payload = "\x1f".join(
        [
            meta.get("bundle_id") or "",
            meta.get("title") or "",
            focused.get("role") or "",
            focused.get("value") or "",
            out.get("visible_text") or "",
            out.get("url") or "",
        ]
    )
    return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()


def capture_once(
    cfg: CaptureConfig,
    provider: ax_capture.AXProvider,
    *,
    trigger: dict[str, Any] | None = None,
) -> Path | None:
    """Perform one capture and write it to the buffer. Returns the file path on success.

    ``trigger`` (optional) carries the watcher event metadata that caused this
    capture. When absent the capture is treated as a heartbeat / manual tick.

    This helper always writes — content-dedup lives in ``_CaptureRunner`` so the
    CLI ``capture-once`` smoke test still produces a fresh file on demand.
    """
    out = _build_capture(cfg, provider, trigger)
    if out is None:
        return None
    return _write_capture(out)


class _CaptureRunner:
    """Serializes capture_once calls from the watcher thread + heartbeat task.

    Captures execute on a single dedicated worker thread fed by a bounded
    queue, so the watcher reader thread never blocks on AX / screenshot I/O
    and a runaway burst of events can never spawn unbounded threads.

    Also enforces *consecutive-duplicate dedup*: if the content fingerprint
    (bundle+title+focused value+visible_text+url) matches the previously
    written capture, the new one is dropped. Time-based dedup in the
    dispatcher handles rapid-fire bursts; this handles a static screen
    (e.g. the lock screen overnight) that keeps generating identical
    captures. When deduped, the ``pre_capture_hook`` is NOT fired, so the
    session manager's idle timer isn't reset by meaningless repetition.
    """

    # Bounded queue for backpressure. Captures are de-duplicated by the
    # dispatcher upstream and again by content-fingerprint here, so a
    # backlog past this size is a sign the worker is stuck or LLM/AX
    # calls are slow — drop with a warning rather than build an
    # unbounded thread/memory backlog.
    _MAX_PENDING = 16
    _SENTINEL: Any = object()

    def __init__(
        self,
        cfg: CaptureConfig,
        provider: ax_capture.AXProvider,
        *,
        pre_capture_hook: Callable[[dict[str, Any]], None] | None = None,
        protection_monitor: PrivacyProtectionMonitor | None = None,
    ) -> None:
        self._cfg = cfg
        self._provider = provider
        self._pre_capture_hook = pre_capture_hook
        self._protection_monitor = protection_monitor
        self._lock = threading.Lock()
        self._last_fingerprint: str | None = None
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=self._MAX_PENDING)
        self._worker: threading.Thread | None = None

    def start_worker(self) -> None:
        """Spawn the dedicated worker thread. Idempotent."""
        if self._worker is not None and self._worker.is_alive():
            return
        self._worker = threading.Thread(
            target=self._worker_loop, name="capture-worker", daemon=True,
        )
        self._worker.start()

    def stop_worker(self, *, timeout: float = 5.0) -> None:
        """Drain the queue and join the worker thread."""
        if self._worker is None:
            return
        with contextlib.suppress(queue.Full):
            self._queue.put(self._SENTINEL, timeout=1.0)
        self._worker.join(timeout=timeout)
        if self._worker.is_alive():
            logger.warning("capture worker did not exit within %.1fs", timeout)
        self._worker = None

    def _worker_loop(self) -> None:
        while True:
            item = self._queue.get()
            if item is self._SENTINEL:
                return
            self.run(item)

    def run(self, trigger: dict[str, Any] | None) -> None:
        # Serialize so two near-simultaneous triggers don't double-capture.
        with self._lock:
            try:
                out = _build_capture(
                    self._cfg,
                    self._provider,
                    trigger,
                    protection_monitor=self._protection_monitor,
                )
                if out is None:
                    return
                fingerprint = _content_fingerprint(out)
                if fingerprint == self._last_fingerprint:
                    meta = out.get("window_meta") or {}
                    logger.debug(
                        "capture skipped (content dedup): trigger=%s app=%r title=%r",
                        (trigger or {}).get("event_type"),
                        meta.get("app_name"),
                        (meta.get("title") or "")[:60],
                    )
                    return
                self._last_fingerprint = fingerprint
                _write_capture(out)
                if self._pre_capture_hook is not None and trigger is not None:
                    try:
                        self._pre_capture_hook(trigger)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("pre_capture_hook failed: %s", exc)
            except Exception as exc:  # noqa: BLE001
                logger.error("capture failed: %s", exc, exc_info=True)

    def run_threaded(self, trigger: dict[str, Any] | None) -> None:
        """Enqueue a capture for the worker thread; drop with a warning if full."""
        try:
            self._queue.put_nowait(trigger)
        except queue.Full:
            logger.warning(
                "capture queue full (%d pending); dropping trigger=%s",
                self._queue.qsize(),
                (trigger or {}).get("event_type") if trigger else "heartbeat",
            )


async def run_forever(
    cfg: CaptureConfig,
    *,
    pre_capture_hook: Callable[[dict[str, Any]], None] | None = None,
    protection_monitor: PrivacyProtectionMonitor | None = None,
) -> None:
    """Run the capture pipeline until cancelled.

    If ``cfg.event_driven`` is true, starts the watcher subprocess and routes
    events through the dispatcher. A heartbeat timer also runs so long idle
    periods (no window changes, no typing) still get periodic snapshots.

    ``pre_capture_hook`` (optional) fires with the trigger dict for every
    capture that actually wrote new content to the buffer — duplicates
    collapsed by content-dedup do NOT fire it, so the session manager's idle
    timer isn't refreshed by a screen that isn't changing (e.g. the lock
    screen overnight).
    """
    provider = ax_capture.create_provider(depth=cfg.ax_depth, timeout=cfg.ax_timeout_seconds)
    if not provider.available:
        logger.warning(
            "AX capture unavailable: %s", getattr(provider, "reason", "unknown reason")
        )

    runner = _CaptureRunner(
        cfg,
        provider,
        pre_capture_hook=pre_capture_hook,
        protection_monitor=protection_monitor,
    )
    runner.start_worker()
    watcher: AXWatcherProcess | None = None
    dispatcher: EventDispatcher | None = None

    def _on_capture(trigger: dict[str, Any] | None) -> None:
        # Hook firing is deferred into the runner so content-deduped captures
        # (e.g. overnight lock-screen repeats) don't refresh the session timer.
        if protection_monitor is not None:
            protection_monitor.request_refresh()
        runner.run_threaded(trigger)

    if cfg.event_driven:
        watcher = AXWatcherProcess()
        if watcher.available:
            dispatcher = EventDispatcher(
                _on_capture,
                debounce_seconds=cfg.debounce_seconds,
                min_capture_gap_seconds=cfg.min_capture_gap_seconds,
                dedup_interval_seconds=cfg.dedup_interval_seconds,
                same_window_dedup_seconds=cfg.same_window_dedup_seconds,
            )
            watcher.on_event(dispatcher.on_event)
            watcher.start()
            logger.info("event-driven capture started")
        else:
            logger.warning(
                "AX watcher unavailable — falling back to heartbeat-only captures"
            )

    # One capture immediately so the user sees something in the buffer right away.
    runner.run_threaded(None)

    try:
        if cfg.heartbeat_minutes > 0:
            heartbeat_interval = max(60.0, cfg.heartbeat_minutes * 60.0)
            logger.info(
                "heartbeat capture every %.0fs (event_driven=%s)",
                heartbeat_interval, cfg.event_driven,
            )
            while True:
                await asyncio.sleep(heartbeat_interval)
                try:
                    await asyncio.to_thread(runner.run, None)
                except Exception as exc:  # noqa: BLE001
                    logger.error("heartbeat capture failed: %s", exc, exc_info=True)
        else:
            logger.info(
                "heartbeat disabled (heartbeat_minutes=%d); event-driven only",
                cfg.heartbeat_minutes,
            )
            # Park until the task is cancelled so the watcher keeps streaming.
            await asyncio.Event().wait()
    finally:
        # Stop in producer→consumer order so no new work piles up after we've
        # told the worker to drain: watcher (no new events) → dispatcher
        # (cancel debounce) → runner worker (drain + join).
        if watcher is not None:
            watcher.stop()
        if dispatcher is not None:
            dispatcher.shutdown()
        runner.stop_worker()


def cleanup_buffer(
    retention_hours: int,
    processed_before_ts: str | None = None,
    *,
    screenshot_retention_hours: int | None = None,
    max_mb: int = 0,
) -> dict[str, int]:
    """Tiered buffer hygiene. Returns {deleted, stripped, evicted}.

    Three passes, all gated on ``processed_before_ts`` so an unprocessed
    trailing capture is never evicted:

    1. **Delete whole file** when mtime is older than ``retention_hours``.
    2. **Strip screenshot payloads** when mtime is older than
       ``screenshot_retention_hours`` (if provided and smaller than
       ``retention_hours``). Screenshot payloads are most of the file size
       and nothing downstream consumes them, so stripping keeps AX+text
       queryable for much longer at ~20% of the original size.
    3. **Evict by size** once total buffer size exceeds ``max_mb`` MB.
       Oldest already-absorbed files go first. ``max_mb=0`` disables this.
    """
    buf = paths.capture_buffer_dir()
    if not buf.exists():
        return {"deleted": 0, "stripped": 0, "evicted": 0}

    now = time.time()
    delete_cutoff = now - retention_hours * 3600
    strip_cutoff = (
        now - screenshot_retention_hours * 3600
        if screenshot_retention_hours and screenshot_retention_hours > 0
        else None
    )
    absorbed_before = (
        _safe_filename(processed_before_ts) if processed_before_ts is not None else None
    )

    deleted = stripped = evicted = 0
    surviving: list[tuple[float, Path, int]] = []  # (mtime, path, size_after_pass)
    removed_stems: list[str] = []  # for FTS delete-through

    for p in sorted(buf.iterdir()):
        if not p.is_file() or p.suffix != ".json":
            continue
        is_absorbed = absorbed_before is None or p.stem < absorbed_before
        try:
            st = p.stat()
        except OSError:
            continue

        if is_absorbed and st.st_mtime <= delete_cutoff:
            try:
                p.unlink()
                deleted += 1
                removed_stems.append(p.stem)
            except OSError:
                pass
            continue

        if (
            is_absorbed
            and strip_cutoff is not None
            and st.st_mtime <= strip_cutoff
            and _strip_screenshot_inplace(p)
        ):
            stripped += 1
            with contextlib.suppress(OSError):
                st = p.stat()

        surviving.append((st.st_mtime, p, st.st_size))

    if max_mb > 0:
        limit = max_mb * 1024 * 1024
        total = sum(sz for _, _, sz in surviving)
        if total > limit:
            surviving.sort()  # oldest first by mtime
            for _mtime, path, size in surviving:
                if total <= limit:
                    break
                if absorbed_before is not None and path.stem >= absorbed_before:
                    continue  # don't evict un-absorbed captures
                try:
                    path.unlink()
                    total -= size
                    evicted += 1
                    removed_stems.append(path.stem)
                except OSError:
                    pass

    if removed_stems:
        _delete_captures_from_fts(removed_stems)

    return {"deleted": deleted, "stripped": stripped, "evicted": evicted}


def _delete_captures_from_fts(stems: list[str]) -> None:
    """Drop matching rows from the captures index. Non-fatal on failure."""
    try:
        with fts_store.cursor() as conn:
            for stem in stems:
                fts_store.delete_capture(conn, stem)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "captures FTS delete failed for %d stems: %s", len(stems), exc
        )


def _strip_screenshot_inplace(path: Path) -> bool:
    """Rewrite a capture JSON without screenshot payloads. Returns True if stripped."""
    try:
        raw = path.read_text()
    except OSError:
        return False
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return False
    if "screenshot" not in data and "screenshots" not in data:
        return False
    data.pop("screenshot", None)
    data.pop("screenshots", None)
    data["screenshot_stripped"] = True
    try:
        path.write_text(json.dumps(data, ensure_ascii=False))
        return True
    except OSError:
        return False
