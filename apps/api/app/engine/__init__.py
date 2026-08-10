"""The Reality Engine.

Pure and deterministic: given the same observations it produces the same
reality state, always. No I/O, no wall-clock reads (``as_of`` is an argument),
no randomness, and no AI — confidence is arithmetic over declared inputs, not a
judgement.

* ``types``      — the vocabulary the engine speaks
* ``selection``  — which value wins, deterministically
* ``spec``       — what is confirmed, and what is missing
* ``confidence`` — the confirmed scoring structure
* ``conflicts``  — recorded disagreement, never a competing belief
* ``engine``     — the single entry point, ``calculate()``

Persistence lives outside, in ``app/services/reality.py``.
"""

from app.engine.engine import CalculationBlocked, calculate

__all__ = ["CalculationBlocked", "calculate"]
