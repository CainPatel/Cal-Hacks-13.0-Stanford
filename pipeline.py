"""The one path every pay request takes to a decision. Owner: B.

Replay and the fuzzer both call `decide`, so the scorecard measures the real path.
Today: state -> kernel. When C's judgment lands, it slots in here (state -> Jev
proposal -> kernel) and both callers pick it up unchanged.

Kernel interface expected from A:
    kernel.runner.decide(state: dict[str, list], request_id: str) -> Decision
Until it exists, a stub that releases everything stands in, so any number it produces
is obviously wrong rather than plausibly right.
"""
from shared.schema import Action, Decision

try:
    from kernel.runner import decide
    KERNEL = "kernel.runner"
except ImportError:
    KERNEL = "STUB (releases everything; kernel/runner.py not built yet)"

    def decide(state, request_id):
        return Decision(request_id=request_id, action=Action.release, results=[])
