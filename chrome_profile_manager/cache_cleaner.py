from __future__ import annotations

import logging
import shutil
from pathlib import Path


_CACHE_RELATIVE_PATHS = [
    Path("Cache"),
    Path("Code Cache"),
    Path("GPUCache"),
    Path("Default") / "Cache",
    Path("Default") / "Code Cache",
    Path("Default") / "GPUCache",
    Path("Default") / "Service Worker" / "CacheStorage",
    Path("Default") / "ShaderCache",
    Path("Default") / "GrShaderCache",
    Path("Default") / "DawnCache",
]


def clean_profile_cache(profile_dir: Path, logger: logging.Logger) -> None:
    logger.info("Starting cache cleanup for profile: %s", profile_dir)
    removed_count = 0
    for rel in _CACHE_RELATIVE_PATHS:
        target = profile_dir / rel
        if not target.exists():
            continue

        try:
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=False)
            else:
                target.unlink(missing_ok=True)
            removed_count += 1
            logger.info("Cache cleaned: %s", target)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to clean cache path %s: %s", target, exc)

    if removed_count == 0:
        logger.info("No cache folders found for profile: %s", profile_dir)
    else:
        logger.info("Cache cleanup completed for %s (removed paths=%s).", profile_dir, removed_count)
