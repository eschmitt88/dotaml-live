"""Phase 4 — live Azure blob consumer.

Pulls NEW Turbo match parquets from the upstream landing zone
(`dota2datalake/matches/turbo/year=/month=/day=/matches_*.parquet`, populated by the
DotaDB collector) into the canonical lake's `turbo/live/` partition. We consume the
blob only — we never re-hit the Steam Web API.

Idempotent and tail-only: it lists blobs for dates AFTER the latest date already
present locally (history + snapshot + live) and downloads just those, skipping files
that already exist with the same size. So it never re-mirrors the ~175 GB history.

Auth uses DefaultAzureCredential (respects `az login` / managed identity). The azure
SDKs are an optional dependency (`pip install -e '.[azure]'`); they're imported lazily
so the rest of the package works without them.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
from pathlib import Path

from ..common import config

# turbo/year=YYYY/month=MM/day=DD/matches_*.parquet
_DATE_RE = re.compile(r"year=(\d{4})/month=(\d{2})/day=(\d{2})/")


def _azure_cfg() -> dict:
    return config.pipeline_config()["azure"]


def _lake_roots() -> list[Path]:
    raw = config.pipeline_config()["raw"]
    return [Path(r).expanduser() for r in (list(raw["historical_roots"]) + [raw["live_dir"]])]


def blob_date(name: str) -> dt.date | None:
    m = _DATE_RE.search(name)
    if not m:
        return None
    try:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def latest_local_date() -> dt.date | None:
    """Newest date partition already present anywhere in the lake."""
    best: dt.date | None = None
    for root in _lake_roots():
        if not root.exists():
            continue
        for p in root.rglob("matches_*.parquet"):
            d = blob_date(str(p))
            if d and (best is None or d > best):
                best = d
    return best


def _container_client():
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import ContainerClient
    cfg = _azure_cfg()
    return ContainerClient(account_url=cfg["account_url"], container_name=cfg["container"],
                           credential=DefaultAzureCredential())


def list_new_blobs(client, prefix: str, since: dt.date | None):
    """Yield (blob_name, size) for blobs strictly after `since`."""
    for b in client.list_blobs(name_starts_with=prefix):
        if not b.name.endswith(".parquet"):
            continue
        d = blob_date(b.name)
        if d is None:
            continue
        if since is None or d > since:
            yield b.name, (b.size or 0)


def _dest_for(blob_name: str, live_dir: Path) -> Path:
    """Map blob path under the prefix to live_dir/year=.../matches_*.parquet."""
    m = _DATE_RE.search(blob_name)
    fname = blob_name.rsplit("/", 1)[-1]
    if not m:
        return live_dir / fname
    y, mo, da = m.groups()
    return live_dir / f"year={y}" / f"month={mo}" / f"day={da}" / fname


def pull(since: str | None = None, dry_run: bool = False, max_files: int | None = None) -> dict:
    cfg = config.pipeline_config()
    live_dir = Path(cfg["raw"]["live_dir"]).expanduser()
    since_date = dt.date.fromisoformat(since) if since else latest_local_date()
    print(f"[blob] pulling Turbo blobs after {since_date} -> {live_dir}"
          f"{' (dry-run)' if dry_run else ''}")

    if dry_run and _azure_unavailable():
        print("[blob] azure SDK not installed; dry-run can only show the plan.")
    client = None if dry_run and _azure_unavailable() else _container_client()
    if client is None:
        return {"since": str(since_date), "planned": "azure SDK missing", "downloaded": 0}

    prefix = _azure_cfg()["prefix"]
    n_dl = n_skip = n_bytes = 0
    for name, size in list_new_blobs(client, prefix, since_date):
        dest = _dest_for(name, live_dir)
        if dest.exists() and dest.stat().st_size == size:
            n_skip += 1
            continue
        if dry_run:
            print(f"  would download {name} ({size:,}B) -> {dest}")
            n_dl += 1
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as f:
                client.download_blob(name).readinto(f)
            n_dl += 1
            n_bytes += size
        if max_files and n_dl >= max_files:
            break
    return {"since": str(since_date), "downloaded": n_dl, "skipped": n_skip,
            "bytes": n_bytes, "dry_run": dry_run}


def _azure_unavailable() -> bool:
    try:
        import azure.identity  # noqa: F401
        import azure.storage.blob  # noqa: F401
        return False
    except Exception:
        return True


def main() -> None:
    ap = argparse.ArgumentParser(description="Pull new Turbo match blobs into the lake (no Steam API)")
    ap.add_argument("--since", default=None, help="pull dates after this YYYY-MM-DD (default: latest local)")
    ap.add_argument("--dry-run", action="store_true", help="list what would be downloaded")
    ap.add_argument("--max-files", type=int, default=None)
    args = ap.parse_args()
    print(f"[blob] {pull(since=args.since, dry_run=args.dry_run, max_files=args.max_files)}")


if __name__ == "__main__":
    main()
