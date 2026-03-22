from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from chrome_profile_manager.cache_cleaner import clean_profile_cache
from chrome_profile_manager.config import ConfigError, load_config
from chrome_profile_manager.launcher import ChromeLauncher
from chrome_profile_manager.logger_setup import setup_logger
from chrome_profile_manager.monitor import ProcessMonitor


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Launch and monitor multiple Chromium-compatible browser instances with "
            "separate profiles."
        )
    )
    parser.add_argument(
        "--config",
        default="config.json",
        help="Path to config JSON file (default: config.json).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if os.name != "nt":
        print("This tool is Windows-only. Current platform is not supported.", file=sys.stderr)
        return 1

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    logger = setup_logger(config.log_file)
    logger.info("Starting Chrome Profile Manager")
    logger.info("Config file loaded: %s", Path(args.config).resolve())
    logger.info(
        "Browser type: %s (%s)",
        config.browser_type,
        config.browser_display_name,
    )
    logger.info("Browser executable path: %s", config.chrome_path)
    if config.extension_auto_load_expected:
        logger.info(
            "Extension auto-loading expectation: unpacked extensions are expected to "
            "auto-load with %s.",
            config.browser_display_name,
        )
    else:
        logger.warning(
            "Extension auto-loading expectation: unpacked extensions may not "
            "auto-load in current official Google Chrome builds. Chromium or "
            "Chrome for Testing is recommended."
        )
    logger.info("Instances target: %s", config.instances)

    launcher = ChromeLauncher(config, logger)
    profile_assignments = launcher.prepare_profile_assignments()
    if not profile_assignments:
        logger.error("No profiles are available to launch. Exiting.")
        return 3

    logger.info("Managed profile count: %s", len(profile_assignments))
    if config.cleanup_cache_on_start:
        logger.info("Cache cleanup on start is enabled.")
        for assignment in profile_assignments:
            clean_profile_cache(assignment.profile_dir, logger)

    monitor = ProcessMonitor(
        launcher=launcher,
        logger=logger,
        relaunch_delay_seconds=config.relaunch_delay_seconds,
        check_interval_seconds=config.check_interval_seconds,
    )
    monitor.start(profile_assignments)
    monitor.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
