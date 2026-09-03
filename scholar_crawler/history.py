"""Learning the pacing from previous runs' takeovers.

The challenge log is the only record of how this profile has actually been treated by
Scholar. A run that starts at the same rhythm as the run that just got blocked will
usually get blocked again, so the log is read at start-up and turned into a starting
rhythm — the one place where the tool carries experience across runs.

Advice widens delays, never narrows them: history can prove that a rhythm was too fast,
but no history proves that a faster rhythm is safe.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from .crawler import delay_span
from .storage import ChallengeRecord

CROWDED = 30
"""Blocks arriving within this many requests mean the rhythm, not the volume, is the problem."""


@dataclass(slots=True, frozen=True)
class History:
    """What previous runs recorded about being blocked.

    :param blocks: real takeovers, excluding rehearsals.
    :param back_to_back: blocks that arrived with no successful page since the previous one.
    :param typical_request: median request index a block arrived at, or None without blocks.
    :param last_at: timestamp of the most recent block, or None without blocks.
    :param kinds: block count per challenge kind.
    """

    blocks: int
    back_to_back: int
    typical_request: int | None
    last_at: str | None
    kinds: dict[str, int]

    def describe(self) -> str:
        """Summarize the history in one line.

        :returns: block count, kinds, and when and where blocks tend to arrive.
        """
        kinds = ", ".join(f"{kind} x{count}" for kind, count in sorted(self.kinds.items()))
        parts = [f"{self.blocks} previous block{'' if self.blocks == 1 else 's'} ({kinds})"]
        if self.typical_request is not None:
            parts.append(f"typically at request {self.typical_request}")
        if self.back_to_back:
            parts.append(f"{self.back_to_back} arrived back to back")
        if self.last_at:
            parts.append(f"last {self.last_at}")
        return "; ".join(parts)


def read_history(entries: list[ChallengeRecord]) -> History:
    """Summarize a challenge log.

    Drills are excluded: they prove the takeover path works, not that Scholar blocked us. They
    are identified by :attr:`ChallengeRecord.drill` rather than by outcome, because a drill
    nobody attends records ``unattended`` and one ended with Ctrl+C records ``interrupted`` —
    either would otherwise read as evidence that this profile gets blocked at request 0.

    :param entries: records read from the challenge log, oldest first.
    :returns: the history; ``blocks`` is 0 when nothing but rehearsals was recorded.
    """
    blocks = [entry for entry in entries if not entry.drill]
    if not blocks:
        return History(blocks=0, back_to_back=0, typical_request=None, last_at=None, kinds={})
    kinds: dict[str, int] = {}
    for entry in blocks:
        kinds[entry.kind] = kinds.get(entry.kind, 0) + 1
    return History(
        blocks=len(blocks),
        back_to_back=sum(1 for entry in blocks if entry.consecutive > 1),
        typical_request=int(median(entry.request_index for entry in blocks)),
        last_at=blocks[-1].at,
        kinds=kinds,
    )


MAX_FACTOR = 2.0
"""Widest starting rhythm history can ask for: 1.6 for repetition, +0.2 back-to-back, +0.2 early."""


def suggest_factor(history: History) -> float:
    """Choose how much to widen the starting delays given this history.

    One block is not a pattern, so it only earns a warning. Repeated blocks earn a wider
    rhythm, and blocks that arrived back to back — or early in a run — earn more, because
    those mean the rhythm itself was refused rather than the run being long.

    :param history: summary of previous blocks.
    :returns: a factor between 1.0 and :data:`MAX_FACTOR`, which the terms reach exactly when
        every one of them applies.
    """
    if history.blocks < 2:
        return 1.0
    factor = 1.3 if history.blocks < 5 else 1.6
    if history.back_to_back:
        factor += 0.2
    if history.typical_request is not None and history.typical_request <= CROWDED:
        factor += 0.2
    return factor


@dataclass(slots=True, frozen=True)
class Advice:
    """A starting rhythm derived from previous runs.

    :param min_delay: recommended minimum delay in seconds.
    :param max_delay: recommended maximum delay in seconds.
    :param factor: how much the recommendation widens the defaults.
    :param history: the history it was derived from.
    """

    min_delay: float
    max_delay: float
    factor: float
    history: History

    @property
    def changes_pacing(self) -> bool:
        """Whether following this advice would change the rhythm.

        :returns: True when the factor widens the delays.
        """
        return self.factor > 1.0

    def describe(self) -> str:
        """Explain the recommendation in one line.

        :returns: the history and the resulting delays.
        """
        if not self.changes_pacing:
            return f"{self.history.describe()}; keeping the current rhythm"
        return (
            f"{self.history.describe()}; starting at "
            f"{delay_span(self.min_delay, self.max_delay)} (x{self.factor:.1f})"
        )


def advise(entries: list[ChallengeRecord], min_delay: float, max_delay: float) -> Advice | None:
    """Derive a starting rhythm from a challenge log.

    :param entries: records read from the challenge log, oldest first.
    :param min_delay: the minimum delay the run would otherwise use.
    :param max_delay: the maximum delay the run would otherwise use.
    :returns: the advice, or None when the log records no blocks worth learning from.
    """
    history = read_history(entries)
    if not history.blocks:
        return None
    factor = suggest_factor(history)
    return Advice(
        min_delay=round(min_delay * factor, 1),
        max_delay=round(max_delay * factor, 1),
        factor=factor,
        history=history,
    )
