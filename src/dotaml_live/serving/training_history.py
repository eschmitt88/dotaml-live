"""Training-history aggregation for the dashboard 'Training' tab.

Surfaces model progression over the FULL history, not just the keep_last_n
versions still on disk — this is the payoff of committing registry metadata each
cycle (the on-disk registry only keeps the last ~10 runs):

  * prequential — the append-only daily health log (data/prequential.jsonl): the
    live model's AUC on the freshest unseen window, one point per cycle. Never
    pruned, so it is the long continuous series (>10 days).
  * runs — per-run validation metrics (val AUC + probe heads + train window) for
    every version ever registered. On-disk versions are read directly; pruned
    ones are recovered from git history, so the table spans well beyond disk.
  * promotions — each time the `live` pointer moved, derived from registry.json's
    git history: the model-progression timeline.

git reads are cached on the registry HEAD sha (+ prequential mtime), so once warm
a tab load costs a single `git rev-parse`.
"""

from __future__ import annotations

import json
import subprocess
from functools import lru_cache

from ..common import paths

# probe heads worth surfacing, in display order (orientation is mixed, so the SPA
# shows raw values rather than a good/bad bar)
PROBE_KEYS = ["pure_pregame", "duration_cond", "items_cond", "outcome_cond",
              "kills_pair_probe", "gpm_probe", "hd_probe"]


def _git(*args: str) -> str:
    out = subprocess.run(["git", "-C", str(paths.REPO_ROOT), *args],
                         capture_output=True, text=True, timeout=15)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or f"git {' '.join(args)} failed")
    return out.stdout


def _head_sha() -> str:
    try:
        return _git("rev-parse", "HEAD").strip()
    except Exception:        # noqa: BLE001 — not a git checkout / git missing
        return ""            # disk-only mode (still serves what's on disk)


def _version_date(v: str) -> str:
    """'ft-2026-06-20' -> '2026-06-20'; undated versions (e.g. 'v7-base') -> ''."""
    tail = v.split("-", 1)[1] if "-" in v else ""
    return tail if tail[:2].isdigit() else ""


def _kind(v: str) -> str:
    pre = v.split("-", 1)[0]
    return {"ft": "fine-tune", "fs": "from-scratch"}.get(pre, pre)


def _parse_metrics(version: str, raw: str, on_disk: bool) -> dict:
    m = json.loads(raw)
    probes = m.get("final_probe_results", {}) or {}
    return {
        "version": version,
        "date": _version_date(version),
        "kind": _kind(version),
        "val_auc": m.get("val_auc_pure_pregame"),
        "epochs": m.get("epochs"),
        "train_cutoff": m.get("train_cutoff"),
        "parent": m.get("warm_started_from"),
        "probes": {k: probes[k] for k in PROBE_KEYS if k in probes},
        "on_disk": on_disk,
    }


def _load_prequential() -> list[dict]:
    p = paths.DATA_DIR / "prequential.jsonl"
    if not p.exists():
        return []
    rows = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    rows.sort(key=lambda r: r.get("cycle", ""))
    return rows


def _disk_versions() -> set[str]:
    reg = paths.REGISTRY_DIR
    if not reg.exists():
        return set()
    # skip the `live` symlink (it resolves to a real version dir) — versions only
    return {p.name for p in reg.iterdir()
            if p.is_dir() and not p.is_symlink() and (p / "metrics.json").exists()}


def _git_metrics_versions() -> dict[str, str]:
    """version -> sha of the last commit that still had its metrics.json (so
    pruned/deleted versions resolve to the commit just before deletion)."""
    try:
        names = _git("log", "--pretty=format:", "--name-only",
                     "--", "registry/*/metrics.json")
    except Exception:        # noqa: BLE001
        return {}
    versions: dict[str, str] = {}
    for line in names.splitlines():
        line = line.strip()
        if line.endswith("/metrics.json") and line.startswith("registry/"):
            versions.setdefault(line.split("/")[1], "")
    for v in list(versions):
        try:
            # --diff-filter=AMR keeps only Added/Modified/Renamed commits, so this
            # is the last commit whose TREE still HAS the file (a pruned version's
            # pre-deletion snapshot) — what `git show <sha>:path` needs. The
            # deletion commit is skipped.
            sha = _git("log", "-1", "--diff-filter=AMR", "--format=%H",
                       "--", f"registry/{v}/metrics.json").strip()
            if sha:
                versions[v] = sha
            else:
                versions.pop(v, None)
        except Exception:    # noqa: BLE001
            versions.pop(v, None)
    return versions


def _promotions() -> list[dict]:
    """[{date, at, version}] each time registry.json's `live` changed, oldest
    first — the model-progression timeline, read from git history."""
    try:
        log = _git("log", "--reverse", "--format=%H%x09%cI",
                   "--", "registry/registry.json")
    except Exception:        # noqa: BLE001
        return []
    out, last = [], None
    for line in log.splitlines():
        if "\t" not in line:
            continue
        sha, iso = line.split("\t", 1)
        try:
            live = json.loads(_git("show", f"{sha}:registry/registry.json")).get("live")
        except Exception:    # noqa: BLE001
            continue
        if live and live != last:
            out.append({"date": iso[:10], "at": iso, "version": live})
            last = live
    return out


def _current_live() -> str | None:
    rp = paths.REGISTRY_DIR / "registry.json"
    if rp.exists():
        try:
            return json.loads(rp.read_text()).get("live")
        except Exception:    # noqa: BLE001
            pass
    return None


@lru_cache(maxsize=4)
def _history_cached(cache_key: str) -> dict:
    # cache_key (HEAD sha + prequential mtime) only invalidates the cache; the
    # reads below always reflect the current working tree.
    disk = _disk_versions()
    git_versions = _git_metrics_versions()
    runs = []
    for v in sorted(disk | set(git_versions)):
        try:
            if v in disk:
                raw = (paths.REGISTRY_DIR / v / "metrics.json").read_text()
                runs.append(_parse_metrics(v, raw, on_disk=True))
            else:
                raw = _git("show", f"{git_versions[v]}:registry/{v}/metrics.json")
                runs.append(_parse_metrics(v, raw, on_disk=False))
        except Exception:    # noqa: BLE001 — skip an unreadable/corrupt run
            continue
    runs.sort(key=lambda r: (r["date"], r["version"]))
    return {
        "prequential": _load_prequential(),
        "runs": runs,
        "promotions": _promotions(),
        "live": _current_live(),
        "kept_on_disk": len(disk),
    }


def load_history() -> dict:
    pq = paths.DATA_DIR / "prequential.jsonl"
    pq_key = str(pq.stat().st_mtime_ns) if pq.exists() else "0"
    return _history_cached(f"{_head_sha()}:{pq_key}")
