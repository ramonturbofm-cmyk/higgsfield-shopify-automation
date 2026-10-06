"""Closed-loop export limiter: P1 measurement -> PV limit -> P1 measurement.

Keeps grid export at ``target_w`` (+/- tolerance) by adjusting the inverter's
power limit. Smoothing (EMA on the measured export), a dead band, a fast path
down (export too high) and a rate-limited path up (avoid oscillation), and
release of the limit once PV no longer has to be curtailed.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ZeroExportRegulator:
    tolerance_w: float = 100.0
    gain_down: float = 0.9
    max_step_up_w: float = 400.0
    alpha: float = 0.5
    limit_w: float | None = None
    _export_ema: float | None = None

    def reset(self) -> None:
        self.limit_w, self._export_ema = None, None

    def update(self, pv_w: float, grid_w: float, target_export_w: float, pv_max_w: float) -> float | None:
        export = -grid_w
        self._export_ema = export if self._export_ema is None else (
            self.alpha * export + (1 - self.alpha) * self._export_ema)
        error = self._export_ema - target_export_w          # > 0: exporting too much
        if self.limit_w is None:
            if error > self.tolerance_w:
                self.limit_w = max(0.0, pv_w - error * self.gain_down)
            return self.limit_w
        if error > self.tolerance_w:
            self.limit_w = max(0.0, self.limit_w - error * self.gain_down)
        elif error < -self.tolerance_w:
            self.limit_w = self.limit_w + min(-error, self.max_step_up_w)
            if self.limit_w >= pv_max_w:
                self.reset()                                 # no curtailment needed any more
        return self.limit_w
