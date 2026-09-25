# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/optimize_content.py

"""Generic peephole cleanup over a flat content-stream instruction list.

Pure list-in/list-out over (operands, op_str) tuples -- no PDF/font/
graphics-state knowledge required. Intended as a finishing pass any
content-stream rewriter (excise today) can run over its own output
before serialization, to avoid leaving behind operators that were only
rendered pointless by upstream deletion (a Tf/Td/Tm whose only show op
got excised) or that trivially collapse (adjacent Td's, a Tz that
doesn't change anything).

Six sub-passes, composed in optimize_positioning_ops:

1. _drop_dead_tf -- reverse dead-store elimination for Tf. A Tf is
   dropped if a later Tf supersedes it with no intervening show op AND
   no intervening q/Q/BT/ET. Those four are barriers rather than drop
   triggers -- Tf is a graphics-state parameter that persists across
   BT/ET (a later text object with no Tf of its own inherits whatever
   font was last set) and is saved/restored by q/Q, so this pass cannot
   safely decide a Tf is dead across one without a real state stack.
   Conservative: keeps a Tf it can't prove dead, never drops one it
   can't prove dead.

   BT/ET's barrier status is caller-selectable via the `aggressive_tf`
   flag on optimize_positioning_ops (default False -- the conservative
   behavior above, unchanged). Tf persists across a text object per
   spec, so a trailing Tf right before ET is in principle just as
   provably dead as one immediately superseded by a later Tf, PROVIDED
   nothing between here and the next real Tf ever shows -- which can
   legitimately include a LATER BT in the same instructions list.

   The catch: that's only sound if `instructions` represents one
   complete, already-concatenated logical content stream. Per spec, a
   page's /Contents may be an array of multiple stream objects
   concatenated into a single logical stream at render time, with
   operator state (including the current font) carrying across that
   array boundary. A caller running this pass separately per array
   element rather than on the merged whole would have "end of this
   instructions list" NOT mean "end of scope" -- a later array element
   could still inherit a Tf this pass would otherwise drop, which is a
   real rendering bug, not a false positive. `aggressive_tf=True`
   should only be passed by a caller that has confirmed it operates on
   a single, fully-merged content stream.

All three passes compare operator identity via str(op), NEVER via a
bare `op == "Tf"`-style comparison against the raw operator value --
instructions produced by pikepdf.parse_content_stream carry `operator`
as a pikepdf.Operator object, not a plain str, and Operator's equality
against a str is not guaranteed (in practice it silently never
matches, which previously made this whole module a silent no-op on
any real parsed stream containing pass-through operators). The
ORIGINAL op object -- whichever type it came in as -- is always what
gets appended to the output; only comparisons go through str().

2. _collapse_text_positioning -- forward pass, scoped to each BT..ET
   span (Td/TD/Tm are reset by BT and never touched by q/Q, so unlike
   Tf there's no cross-span persistence concern). Non-positional ops
   pass through immediately, unreordered relative to each other. Td/
   TD/Tm are buffered rather than emitted immediately: a Tm clears
   anything already buffered (its absolute value makes earlier
   relative moves provably unused, since nothing has been shown yet or
   they'd already have been flushed); a Td directly following a
   buffered Td sums into it; TD is buffered but never arithmetically
   merged (it also sets /TL -- folding that in correctly isn't worth
   the risk here). The buffer flushes (in original relative order)
   immediately before the next show op, or is silently dropped at ET
   if no show op ever consumed it -- dead position state, since Tm/Tlm
   reset at the next BT regardless.

   The buffer-then-flush shape is what makes this pass also subsume
   plain adjacent-Td merging AND let a Td hop over an intervening Tf/
   Tc/Tw/Tz/TL/Ts: those ops are emitted where they stood, unmoved, and
   the buffered Td simply lands just before the eventual show, wherever
   that ends up in the output.

3. _eliminate_redundant_tz -- forward pass, global (Tz persists across
   BT/ET so this is not scoped to a text object): drops a Tz whose
   value doesn't change the running current_tz (starts at spec default
   100.0).

4. _eliminate_redundant_color -- forward pass, global (color state
   persists across BT/ET like Tz, and is saved/restored by q/Q, but a
   REPEATED identical value is redundant regardless of what q/Q did in
   between, since if the value coming out of a q/Q block matches what
   was already current before it, the net color op is provably a
   no-op). Tracks one current color per slot, nonstroking and stroking,
   as an (operator, values) pair: g, rg, k, cs, sc and scn all set the
   same nonstroking color, so a `g` after an `rg` is redundant only if
   the slot still holds that same `g` value. cs/sc/scn (and CS/SC/SCN)
   reset their slot to unknown. No spec default is assumed (unlike Tz's 100.0) --
   the first occurrence of any color op is always kept. Unlike Tz,
   color state genuinely IS saved/restored by q/Q (it's real graphics
   state per spec), so this pass tracks it with an explicit q/Q stack,
   the same shape as _drop_dead_tf's -- a color value set inside a
   q...Q block must not "leak" into what's compared after the matching
   Q, since Q reverts exactly to whatever was current at q.

5. _drop_dead_q_blocks -- forward pass using an explicit q/Q stack. A
   matched q...Q span (balanced, arbitrary nesting) is dropped in its
   ENTIRETY -- the q, everything inside, and the Q -- if no paint-ish
   operator (fill/stroke/clip-paint/Do/sh/inline-image/text-show)
   appears anywhere inside it, at any nesting depth. Safe because Q
   unconditionally reverts every bit of state q saved, so a block that
   never painted anything had zero observable effect regardless of
   what graphics-state ops it contains. Malformed input (unbalanced
   q/Q) is left untouched rather than guessed at.

7. _drop_dead_state_stores -- generalizes _drop_dead_tf's reverse
   dead-store elimination to several independent graphics-state
   "families" at once: fill and stroke color, each split into space
   (g/rg/k/cs) and value (those plus sc/scn), line width (w), line cap
   (J), line join (j), dash pattern (d), and miter limit (M). A setter
   is dead once every family it wrote is superseded with no paint op
   in between (sc after k supersedes only the value, so k stays), and
   unlike _eliminate_redundant_color, this drops even
   when the two values DIFFER, since a fully-overwritten-before-use
   setter is dead regardless of what its old value was (the common
   "1 G" immediately followed by "0 G" shape). Paint (_PAINT_OPS)
   confirms every pending family alive at once, mirroring
   _drop_dead_tf's show-op barrier. q/Q push/pop the WHOLE set of
   pending families as one frame (same shape as
   _eliminate_redundant_color's q_stack) -- a store made INSIDE a
   q...Q block that's never painted under is dead once Q reverts it,
   symmetric with _drop_dead_tf's q..Q handling for Tf. A paint inside
   a block also confirms the enclosing frames' stores, which the block
   inherited.

   Implemented via tombstoning (mark dead entries None, filter at the
   end) rather than del-by-index -- unlike _drop_dead_tf's single
   pending slot, several families' indices are live simultaneously, so
   deleting one by index would require re-shifting every other
   tracked index; tombstone-then-filter sidesteps that entirely.

6. _drop_empty_bt_et -- forward pass dropping a BT...ET pair with
   nothing between them. Must run AFTER _drop_dead_tf and
   _collapse_text_positioning, since either of those can strip a
   text object's contents down to nothing (a dead Tf, a dead Td/Tm
   chain never consumed by a show) -- an empty BT...ET is exactly as
   observably inert as an empty q...Q, just without the state-stack
   nesting concern (BT/ET don't nest). Only an EXACTLY-adjacent
   BT,ET pair is dropped; anything at all in between (even something
   this module doesn't itself understand) is left alone.

9. _drop_paintless_stream -- whole-stream short-circuit, checked BEFORE
   any other pass runs. If NO paint-ish operator (_PAINT_OPS) appears
   anywhere in `instructions` at all, the entire stream is replaced with
   an empty list. This needs no q/Q or barrier reasoning whatsoever,
   unlike every other pass here -- if nothing is ever painted under ANY
   graphics state this stream sets up, in any combination, the whole
   stream is provably inert. This is deliberately narrower than trying
   to detect a dead RUN of ops (e.g. a trailing chain of unconsumed cm
   concatenations) mid-stream -- proving a whole stream paintless is
   trivial and total; proving a sub-run dead while other paint exists
   elsewhere in the same stream is a separate, harder problem this
   pass does not attempt.
"""

from __future__ import annotations

from typing import Any

_TEXT_SHOW_OPS = frozenset({"Tj", "TJ", "'", '"'})
# Always-barrier ops for Tf, regardless of aggressive_tf: a show op
# confirms liveness, and so does Do, since a Form XObject inherits the
# font. q/Q is handled separately in _drop_dead_tf via an explicit
# push/pop stack rather than being an unconditional barrier -- see that
# function's docstring.
_BARRIER_OPS = frozenset({"Tj", "TJ", "'", '"', "Do"})
# BT/ET are barriers ONLY in conservative (default) mode -- see
# optimize_positioning_ops' aggressive_tf docstring.
_TEXT_OBJECT_BOUNDARY_OPS = frozenset({"BT", "ET"})
_POSITION_OPS = frozenset({"Td", "TD", "Tm"})

_COLOR_OP_ARITY = {"g": 1, "G": 1, "rg": 3, "RG": 3, "k": 4, "K": 4}
_COLOR_SLOT = {
    **{op: "nonstroke" for op in ("g", "rg", "k", "cs", "sc", "scn")},
    **{op: "stroke" for op in ("G", "RG", "K", "CS", "SC", "SCN")},
}
# pikepdf reports a whole BI...ID...EI inline image as one "INLINE IMAGE" op.
_PAINT_OPS = frozenset(
    {"f", "F", "f*", "S", "s", "B", "B*", "b", "b*", "n", "Do", "sh", "BI", "INLINE IMAGE"}
    | {"Tj", "TJ", "'", '"'}
)

# State components each setter writes, for _drop_dead_state_stores.
# g/rg/k/cs set a color space and a value; sc/scn set only the value, so
# they never supersede the space an earlier g/rg/k/cs chose.
_STATE_STORE_FAMILIES = {
    **{op: ("fill_space", "fill_value") for op in ("g", "rg", "k", "cs")},
    **{op: ("fill_value",) for op in ("sc", "scn")},
    **{op: ("stroke_space", "stroke_value") for op in ("G", "RG", "K", "CS")},
    **{op: ("stroke_value",) for op in ("SC", "SCN")},
    "w": ("line_width",),
    "J": ("line_cap",),
    "j": ("line_join",),
    "d": ("dash",),
    "M": ("miter_limit",),
}

_TZ_TOL = 0.001
_TZ_DEFAULT = 100.0

Instruction = tuple[list[Any], str]


def _s(op: Any) -> str:
    """Operator identity for comparison purposes -- see module docstring."""
    return op if isinstance(op, str) else str(op)


def optimize_positioning_ops(
    instructions: list[Instruction], *, aggressive_tf: bool = False
) -> list[Instruction]:
    """Runs all eight sub-passes, in this order: dead-Tf must run before
    text-positioning collapse so the positioning pass sees the smaller,
    already-cleaned instruction list (order doesn't change correctness
    either way -- the two passes touch disjoint operator sets -- but
    Tf-first is cheaper). Tz and color elimination are fully independent
    of the first two and could run anywhere in the sequence. Dead-q-block
    elimination runs last since it can only see paint ops that survived
    the earlier passes (none of which drop paint ops themselves, so this
    ordering doesn't change correctness -- it's just the natural place
    for a "did anything actually paint" pass to sit).

    _drop_dead_state_stores runs before _eliminate_redundant_color so
    the latter sees a smaller instruction list -- a store dead-store-
    eliminated (different value, never used) never needs a same-value
    check at all. Running them in the other order would still be
    correct, just marginally more work.

    _drop_empty_bt_et runs LAST, after everything else, since it's only
    ever able to see a text object as empty once dead-Tf and
    text-positioning collapse have already stripped it down that far.

    aggressive_tf: if True, BT/ET no longer act as barriers for
    dead-Tf elimination -- see module docstring for when this is (and
    is not) safe to pass. Defaults to False, the original conservative
    behavior, so existing callers are unaffected."""
    instructions = _drop_paintless_stream(instructions)
    if not instructions:
        return instructions
    instructions = _drop_dead_tf(instructions, aggressive_tf=aggressive_tf)
    instructions = _collapse_text_positioning(instructions)
    instructions = _drop_dead_state_stores(instructions)
    instructions = _eliminate_redundant_tz(instructions)
    instructions = _eliminate_redundant_color(instructions)
    instructions = _drop_dead_q_blocks(instructions)
    instructions = _drop_empty_bt_et(instructions)
    return instructions


class _TfDeadStoreTracker:
    """Holds _drop_dead_tf's running state -- see that function's
    docstring for the exact semantics each method implements. Isolating
    each branch into its own method keeps the dispatch loop itself
    trivial to follow."""

    def __init__(self) -> None:
        self.out: list[Instruction] = []
        self.pending_tf_index: int | None = None
        self.q_stack: list[int | None] = []

    def handle_tf(self, operands: list[Any], op: str) -> None:
        if self.pending_tf_index is not None:
            del self.out[self.pending_tf_index]  # dead: never reached a show
        self.out.append((operands, op))
        self.pending_tf_index = len(self.out) - 1

    def handle_q(self, operands: list[Any], op: str) -> None:
        self.q_stack.append(self.pending_tf_index)
        self.pending_tf_index = None  # fresh, independent chain inside the block
        self.out.append((operands, op))

    def handle_q_close(self, operands: list[Any], op: str) -> None:
        if self.pending_tf_index is not None:
            del self.out[self.pending_tf_index]  # dead: block closed, Q reverts it unused
        self.pending_tf_index = self.q_stack.pop() if self.q_stack else None
        self.out.append((operands, op))

    def handle_barrier(self, operands: list[Any], op: str) -> None:
        # Confirmed alive, including a Tf from an enclosing q frame, which this inherits.
        self.pending_tf_index = None
        self.q_stack = [None] * len(self.q_stack)
        self.out.append((operands, op))

    def handle_passthrough(self, operands: list[Any], op: str) -> None:
        self.out.append((operands, op))

    def finish(self) -> list[Instruction]:
        if self.pending_tf_index is not None:
            del self.out[self.pending_tf_index]  # trailing dead Tf, nothing follows it
        return self.out


def _drop_dead_tf(
    instructions: list[Instruction], *, aggressive_tf: bool = False
) -> list[Instruction]:
    """Forward pass tracking one pending (unconfirmed) Tf at a time. A
    pending Tf is dropped -- not kept -- if it's superseded by a later
    Tf with nothing in between, OR if the instruction list simply ends
    while it's still pending (a trailing Tf nothing ever shows under is
    just as dead as one immediately overwritten -- there's no barrier
    at end-of-stream to hide behind). A show op or scope boundary
    (BT/ET, in conservative mode) confirms the pending Tf alive and
    clears the marker. When aggressive_tf is True, BT/ET are excluded
    from that barrier set.

    q/Q are NOT treated as unconditional barriers -- unlike BT/ET, they
    ARE a real state stack (this pass just needed to actually use one).
    `q` pushes whatever pending-Tf state existed before it and starts a
    fresh, independent pending-Tf chain for the block; `Q` pops back to
    that saved state, since a real PDF renderer's Q restores the font
    (and everything else) to exactly what it was at the matching q --
    an unconfirmed Tf from before `q` is therefore still just as
    unconfirmed-but-possibly-dead after `Q`, not newly "proven alive".
    Symmetrically, any Tf set INSIDE a q..Q block that never gets shown
    before the matching Q is dead too, for the same reason (Q reverts
    it before it's ever used) -- so it's dropped right at `Q`, the same
    way a trailing pending Tf is dropped at end-of-stream. A stray `Q`
    with no matching `q` (malformed input) degrades to restoring "no
    pending Tf" rather than raising.

    See _TfDeadStoreTracker for the per-branch state; this function is
    just the dispatch loop over it.
    """
    tracker = _TfDeadStoreTracker()
    for operands, op in instructions:
        op_str = _s(op)
        if op_str == "Tf":
            tracker.handle_tf(operands, op)
            continue
        if op_str == "q":
            tracker.handle_q(operands, op)
            continue
        if op_str == "Q":
            tracker.handle_q_close(operands, op)
            continue
        is_barrier = op_str in _BARRIER_OPS or (
            not aggressive_tf and op_str in _TEXT_OBJECT_BOUNDARY_OPS
        )
        if is_barrier:
            tracker.handle_barrier(operands, op)
        else:
            tracker.handle_passthrough(operands, op)
    return tracker.finish()


def _merge_into_pending(pending: list[Instruction], operands: list[Any], op: str) -> None:
    """Adds one Td/TD/Tm to `pending` per _collapse_text_positioning's
    buffering rules. Mutates `pending` in place."""
    op_str = _s(op)
    if op_str == "Tm":
        pending.clear()
        pending.append((list(operands), op))
        return

    # op is Td or TD. Only a Td landing directly on top of a buffered
    # Td (nothing else buffered since) gets arithmetically summed.
    if op_str == "Td" and pending and _s(pending[-1][1]) == "Td":
        prev_operands, _ = pending[-1]
        try:
            merged = [
                float(prev_operands[0]) + float(operands[0]),
                float(prev_operands[1]) + float(operands[1]),
            ]
            pending[-1] = (merged, "Td")
            return
        except (TypeError, ValueError, IndexError):
            pass  # malformed operands -- fall through, buffer separately
    pending.append((list(operands), op))


def _collapse_text_positioning(instructions: list[Instruction]) -> list[Instruction]:
    out: list[Instruction] = []
    pending: list[Instruction] = []
    in_text_object = False

    for operands, op in instructions:
        op_str = _s(op)
        if op_str == "BT":
            pending.clear()  # nothing carries in -- Tm/Tlm reset here anyway
            in_text_object = True
            out.append((operands, op))
        elif op_str == "ET":
            pending.clear()  # never reached a show -> dead, drop silently
            in_text_object = False
            out.append((operands, op))
        elif in_text_object and op_str in _POSITION_OPS:
            _merge_into_pending(pending, operands, op)
        elif op_str in _TEXT_SHOW_OPS:
            out.extend(pending)
            pending.clear()
            out.append((operands, op))
        else:
            out.append((operands, op))

    # Defensive: an unterminated text object (malformed input stream)
    # shouldn't silently eat operators our BT/ET bookkeeping never saw
    # closed -- surface whatever was still buffered rather than drop it.
    out.extend(pending)
    return out


class _StateStoreTracker:
    """Holds _drop_dead_state_stores' running state -- see that
    function's docstring for the tombstoning rationale and the exact
    q/Q/paint semantics each method implements."""

    def __init__(self) -> None:
        self.out: list[Instruction | None] = []
        self.pending: dict[str, int] = {}  # family -> index into out
        self.q_stack: list[dict[str, int]] = []
        # index -> families it wrote that are not yet superseded
        self.unsuperseded: dict[int, set[str]] = {}
        self.live: set[int] = set()

    def _supersede(self, idx: int, family: str) -> None:
        remaining = self.unsuperseded[idx]
        remaining.discard(family)
        if not remaining and idx not in self.live:
            self.out[idx] = None  # dead: every component it wrote went unused

    def handle_q(self, operands: list[Any], op: str) -> None:
        self.q_stack.append(dict(self.pending))
        self.pending = {}
        self.out.append((operands, op))

    def handle_q_close(self, operands: list[Any], op: str) -> None:
        for family, idx in self.pending.items():
            self._supersede(idx, family)  # Q reverts it unused
        self.pending = self.q_stack.pop() if self.q_stack else {}
        self.out.append((operands, op))

    def handle_family(self, operands: list[Any], op: str, families: tuple[str, ...]) -> None:
        for family in families:
            if family in self.pending:
                self._supersede(self.pending[family], family)
        self.out.append((operands, op))
        idx = len(self.out) - 1
        self.unsuperseded[idx] = set(families)
        for family in families:
            self.pending[family] = idx

    def handle_paint(self, operands: list[Any], op: str) -> None:
        # Confirmed alive, including enclosing q frames' stores, which this inherits.
        self.live.update(self.pending.values())
        for frame in self.q_stack:
            self.live.update(frame.values())
            frame.clear()
        self.pending = {}
        self.out.append((operands, op))

    def handle_passthrough(self, operands: list[Any], op: str) -> None:
        self.out.append((operands, op))

    def finish(self) -> list[Instruction]:
        for family, idx in self.pending.items():
            self._supersede(idx, family)  # trailing stores, nothing follows them
        return [instr for instr in self.out if instr is not None]


def _drop_dead_state_stores(instructions: list[Instruction]) -> list[Instruction]:
    """Generic reverse dead-store elimination across several independent
    graphics-state families -- see module docstring (item 7) for the
    family list and the q/Q/paint semantics."""
    tracker = _StateStoreTracker()
    for operands, op in instructions:
        op_str = _s(op)
        if op_str == "q":
            tracker.handle_q(operands, op)
            continue
        if op_str == "Q":
            tracker.handle_q_close(operands, op)
            continue
        families = _STATE_STORE_FAMILIES.get(op_str)
        if families is not None:
            tracker.handle_family(operands, op, families)
            continue
        if op_str in _PAINT_OPS:
            tracker.handle_paint(operands, op)
        else:
            tracker.handle_passthrough(operands, op)
    return tracker.finish()


def _drop_paintless_stream(instructions: list[Instruction]) -> list[Instruction]:
    """See module docstring (item 9). A single membership scan; no
    state tracking needed at all."""
    if any(_s(op) in _PAINT_OPS for _, op in instructions):
        return instructions
    return []


def _eliminate_redundant_tz(instructions: list[Instruction]) -> list[Instruction]:
    out: list[Instruction] = []
    current_tz = _TZ_DEFAULT
    for operands, op in instructions:
        op_str = _s(op)
        if op_str == "Tz":
            try:
                new_tz = float(operands[0])
            except (IndexError, TypeError, ValueError):
                out.append((operands, op))
                continue
            if abs(new_tz - current_tz) < _TZ_TOL:
                continue  # redundant -- doesn't change anything
            current_tz = new_tz
        out.append((operands, op))
    return out


class _ColorTracker:
    """Holds _eliminate_redundant_color's running state -- see that
    function's docstring for the semantics. One method per branch,
    same shape as _TfDeadStoreTracker."""

    def __init__(self) -> None:
        self.out: list[Instruction] = []
        self.current: dict[str, tuple[str, tuple[float, ...]] | None] = {}
        self.q_stack: list[dict[str, tuple[str, tuple[float, ...]] | None]] = []

    def handle_q(self, operands: list[Any], op: str) -> None:
        self.q_stack.append(dict(self.current))
        self.out.append((operands, op))

    def handle_q_close(self, operands: list[Any], op: str) -> None:
        self.current = self.q_stack.pop() if self.q_stack else self.current
        self.out.append((operands, op))

    def handle_color(self, operands: list[Any], op: str, op_str: str, arity: int) -> None:
        slot = _COLOR_SLOT[op_str]
        try:
            values = tuple(float(x) for x in operands[:arity])
            if len(values) != arity:
                raise ValueError
        except (TypeError, ValueError, IndexError):
            self.current[slot] = None
            self.out.append((operands, op))
            return
        prev = self.current.get(slot)
        if (
            prev is not None
            and prev[0] == op_str
            and all(abs(a - b) < _TZ_TOL for a, b in zip(prev[1], values))
        ):
            return  # redundant -- doesn't change anything
        self.current[slot] = (op_str, values)
        self.out.append((operands, op))

    def handle_unknown_color(self, operands: list[Any], op: str, op_str: str) -> None:
        self.current[_COLOR_SLOT[op_str]] = None
        self.out.append((operands, op))

    def handle_passthrough(self, operands: list[Any], op: str) -> None:
        self.out.append((operands, op))


def _eliminate_redundant_color(instructions: list[Instruction]) -> list[Instruction]:
    """Drops a g/G/rg/RG/k/K operator that re-sets the color its slot
    (nonstroking or stroking) already holds -- see module docstring
    (item 4). q/Q push/pop a
    snapshot of the whole tracked-color dict, mirroring _drop_dead_tf's
    q_stack -- color really is graphics state restored by Q, so tracking
    it flatly (ignoring q/Q) would wrongly treat a value set INSIDE a
    block as still current after Q reverts it."""
    tracker = _ColorTracker()
    for operands, op in instructions:
        op_str = _s(op)
        if op_str == "q":
            tracker.handle_q(operands, op)
            continue
        if op_str == "Q":
            tracker.handle_q_close(operands, op)
            continue
        arity = _COLOR_OP_ARITY.get(op_str)
        if arity is not None:
            tracker.handle_color(operands, op, op_str, arity)
            continue
        if op_str in _COLOR_SLOT:
            tracker.handle_unknown_color(operands, op, op_str)
            continue
        tracker.handle_passthrough(operands, op)
    return tracker.out


def _drop_dead_q_blocks(instructions: list[Instruction]) -> list[Instruction]:
    """Drops each balanced q...Q span containing no paint-ish operator
    at any nesting depth -- see module docstring (item 5). Implemented
    as a single forward scan: each open `q` pushes the output-length
    marker it would need to roll back to, plus a "saw paint" flag for
    its own span; a paint op sets every open span's flag (an inner
    span's paint keeps its OUTER span alive too, since the outer span
    genuinely did paint something via its nested block); `Q` pops and
    either keeps the whole span (paint seen) or truncates `out` back to
    the marker (rolling back the `q`, everything inside, and now the
    `Q` itself is simply never appended). Unbalanced input (a `Q` with
    no open `q`, or `q`'s left open at the end) is left exactly as
    encountered for the unmatched operators -- no guessing.
    """
    out: list[Instruction] = []
    # Each entry: [output_index_at_open, saw_paint_inside]
    stack: list[list[int | bool]] = []

    for operands, op in instructions:
        op_str = _s(op)
        if op_str == "q":
            stack.append([len(out), False])
            out.append((operands, op))
            continue
        if op_str == "Q":
            if not stack:
                out.append((operands, op))  # unmatched Q -- pass through
                continue
            open_index, saw_paint = stack.pop()
            if saw_paint:
                out.append((operands, op))
            else:
                del out[open_index:]  # drop q, its whole contents, and this Q
            continue
        if op_str in _PAINT_OPS:
            for frame in stack:
                frame[1] = True
        out.append((operands, op))
    return out


def _drop_empty_bt_et(instructions: list[Instruction]) -> list[Instruction]:
    """Drops a BT immediately followed by ET (nothing at all between
    them) -- see module docstring (item 6). A single one-instruction
    lookahead is enough since BT/ET never nest."""
    out: list[Instruction] = []
    i = 0
    n = len(instructions)
    while i < n:
        operands, op = instructions[i]
        if _s(op) == "BT" and i + 1 < n and _s(instructions[i + 1][1]) == "ET":
            i += 2  # drop both -- nothing between them
            continue
        out.append((operands, op))
        i += 1
    return out
