"""CLI wrapper: precompute the hero-combo discovery table for a model version.
Logic lives in dotaml_live.queries.combos_precompute (so the retrain cycle can call it
on promotion). Usage: python scripts/precompute_combos.py [--model-dir <dir>]"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dotaml_live.common import paths  # noqa: E402
from dotaml_live.queries.combos_precompute import build_table  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", default=None, help="default: live model dir")
    ap.add_argument("--pair-samples", type=int, default=6)
    ap.add_argument("--trio-samples", type=int, default=4)
    args = ap.parse_args()
    model_dir = Path(args.model_dir) if args.model_dir else paths.live_model_dir()
    t = time.time()
    dest = build_table(model_dir, args.pair_samples, args.trio_samples)
    print(f"[combos] wrote {dest} ({dest.stat().st_size / 1e6:.1f} MB) in {time.time()-t:.0f}s")


if __name__ == "__main__":
    main()
