"""Real solve-progress reporting.

Every stage here is an actual boundary in the work, and every duration is
measured rather than estimated. There are deliberately no percentages: CP-SAT
cannot say how much search remains, so a progress bar would be a lie. What can
be reported honestly is *which step is running now* and *how long each finished
step took*, which is what this module carries.

`ProgressLog` works with or without a listener, so the solver keeps one code
path whether or not anyone is watching. Most stages wrap a block of code; a few
end at a moment only the solver knows -- "first conflict-free timetable found"
is reported from inside a CP-SAT callback -- and use `begin`/`end` instead.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field


@dataclass(frozen=True, slots=True)
class Stage:
    key: str
    label: str
    seconds: float
    detail: str = ""


@dataclass(slots=True)
class StageHandle:
    """Lets a stage body attach a factual note once it knows one."""

    detail: str = ""

    def note(self, text: str) -> None:
        self.detail = text


@dataclass(slots=True)
class ProgressLog:
    """Times stage boundaries, and reports them live when given a listener."""

    listener: Callable[[dict], None] | None = None
    stages: list[Stage] = field(default_factory=list)
    _started: float = field(default_factory=time.perf_counter)
    _open: dict[str, tuple[str, float]] = field(default_factory=dict)

    @contextmanager
    def stage(self, key: str, label: str) -> Iterator[StageHandle]:
        handle = StageHandle()
        self._emit({"event": "stage_started", "key": key, "label": label})
        begun = time.perf_counter()
        try:
            yield handle
        finally:
            # Emitted even when the body raises, so a failed stage still
            # reports the time it burned before failing.
            record = Stage(
                key=key,
                label=label,
                seconds=round(time.perf_counter() - begun, 3),
                detail=handle.detail,
            )
            self.stages.append(record)
            self._emit({"event": "stage_finished", **asdict(record)})

    def begin(self, key: str, label: str) -> None:
        """Open a stage whose end is an event rather than the end of a block."""
        self._open[key] = (label, time.perf_counter())
        self._emit({"event": "stage_started", "key": key, "label": label})

    def end(self, key: str, detail: str = "") -> None:
        label, begun = self._open.pop(key)
        record = Stage(
            key=key,
            label=label,
            seconds=round(time.perf_counter() - begun, 3),
            detail=detail,
        )
        self.stages.append(record)
        self._emit({"event": "stage_finished", **asdict(record)})

    def is_open(self, key: str) -> bool:
        return key in self._open

    def child(self, key_prefix: str, label_prefix: str) -> ProgressLog:
        """A log whose stages appear in this one, renamed, as they happen.

        For work that runs another operation several times -- ranking repair
        alternatives runs a full repair per alternative -- so each run's
        stages stay distinguishable instead of repeating the same keys.
        """
        parent = self

        def forward(event: dict) -> None:
            renamed = {
                **event,
                "key": f"{key_prefix}:{event['key']}",
                "label": f"{label_prefix}{event['label']}",
            }
            if renamed["event"] == "stage_finished":
                parent.stages.append(
                    Stage(
                        key=renamed["key"],
                        label=renamed["label"],
                        seconds=renamed["seconds"],
                        detail=renamed.get("detail", ""),
                    )
                )
            parent._emit(renamed)

        return ProgressLog(listener=forward)

    @property
    def total_seconds(self) -> float:
        return round(time.perf_counter() - self._started, 3)

    def as_list(self) -> list[dict]:
        return [asdict(s) for s in self.stages]

    def _emit(self, payload: dict) -> None:
        if self.listener is not None:
            self.listener(payload)
