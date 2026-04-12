"""SFLO artifact archive — move-instead-of-delete for debuggability.

Instead of permanently deleting state files and gate artifacts, the runner
and scaffold move them to <sflo_dir>/logs/. This preserves the most recent
removed copies for inspection without affecting the active pipeline (the
gate loop only scans top-level <sflo_dir>/, never logs/).

Last-write wins: each call overwrites whatever is in logs/ with the same
basename. logs/ always reflects the most recently removed artifact of each
name. No timestamped subdirectories — single flat folder. We only care
about the most recent removal.
"""

import os
import shutil


def archive_to_logs(sflo_dir, paths):
    """Move files / directories from <sflo_dir>/* to <sflo_dir>/logs/.

    Args:
        sflo_dir: Pipeline state dir (e.g. ".sflo").
        paths: Iterable of absolute paths inside sflo_dir to archive.

    Returns:
        list of basenames that were actually archived (skipped paths
        omitted). Useful for logging.

    Notes:
        - Missing source paths are silently skipped (idempotent).
        - Existing logs/<basename> entries are overwritten so logs/ always
          holds the most recent removed copy.
        - logs/ itself is never archived to avoid recursion.
    """
    logs_dir = os.path.join(sflo_dir, "logs")
    os.makedirs(logs_dir, exist_ok=True)

    archived = []
    for path in paths:
        if not path:
            continue
        if not os.path.exists(path):
            continue

        basename = os.path.basename(path.rstrip("/"))
        # Never archive the logs dir into itself
        if basename == "logs":
            continue

        dest = os.path.join(logs_dir, basename)

        # Last-write wins: clear any existing entry with the same name
        if os.path.isdir(dest) and not os.path.islink(dest):
            shutil.rmtree(dest)
        elif os.path.exists(dest):
            os.remove(dest)

        shutil.move(path, dest)
        archived.append(basename)

    return archived
