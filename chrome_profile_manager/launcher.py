from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import AppConfig


_BASE_PERFORMANCE_FLAGS = [
    "--disable-background-networking",
    "--disable-default-apps",
    "--disable-sync",
    "--disable-component-update",
    "--disable-features=Translate,OptimizationHints",
    "--metrics-recording-only",
    "--no-first-run",
    "--no-default-browser-check",
    "--disk-cache-size=104857600",
    "--media-cache-size=20971520",
]


@dataclass(slots=True)
class ManagedInstance:
    instance_id: int
    profile_dir: Path
    process: subprocess.Popen[str]


@dataclass(frozen=True, slots=True)
class ProfileAssignment:
    instance_id: int
    profile_dir: Path


class ChromeLauncher:
    def __init__(self, config: AppConfig, logger: logging.Logger) -> None:
        self.config = config
        self.logger = logger
        self.managed_profiles: list[Path] = []
        self._validated_extension_folders = self._validate_extension_folders(
            self.config.extension_folders
        )
        self._extension_arg = self._build_extension_argument(self._validated_extension_folders)
        self.logger.info(
            "Launcher browser type: %s (%s)",
            self.config.browser_type,
            self.config.browser_display_name,
        )
        if self._validated_extension_folders:
            self.logger.info(
                "Accepted extension paths (%s): %s",
                len(self._validated_extension_folders),
                ", ".join(
                    self._normalize_windows_path(path)
                    for path in self._validated_extension_folders
                ),
            )
            self.logger.info("Final extension argument string: %s", self._extension_arg)
        else:
            self.logger.info("Final extension argument string: not set (no valid extensions).")

        if self.config.use_proxy and self.config.proxy_server:
            self.logger.info("Proxy enabled: %s", self.config.proxy_server)
        else:
            self.logger.info("Proxy disabled.")

    def prepare_profile_assignments(self) -> list[ProfileAssignment]:
        existing_profiles = self._discover_profile_dirs()
        created_profiles = self._create_missing_profiles(existing_profiles)
        all_profiles = sorted(
            [*existing_profiles, *created_profiles],
            key=lambda path: path.name.lower(),
        )

        if self.config.cycle_existing_profiles:
            self.managed_profiles = all_profiles
            self.logger.info(
                "Profile selection mode: cycle existing profiles (rotation pool=%s, startup instances=%s).",
                len(self.managed_profiles),
                min(self.config.instances, len(self.managed_profiles)),
            )
        else:
            self.managed_profiles = all_profiles[: self.config.instances]
            self.logger.info(
                "Profile selection mode: first N profiles (N=%s, rotation pool=%s).",
                self.config.instances,
                len(self.managed_profiles),
            )

        selected_profiles = self.managed_profiles[: self.config.instances]
        assignments = [
            ProfileAssignment(instance_id=index + 1, profile_dir=profile_dir)
            for index, profile_dir in enumerate(selected_profiles)
        ]
        for assignment in assignments:
            self.logger.info(
                "Profile assignment: instance %s -> %s",
                assignment.instance_id,
                assignment.profile_dir,
            )
        return assignments

    def get_managed_profiles(self) -> list[Path]:
        return list(self.managed_profiles)

    def launch(self, instance_id: int, profile_dir: Path, relaunch: bool = False) -> ManagedInstance:
        command = self._build_command(profile_dir)
        self.logger.info("Full browser command: %s", subprocess.list2cmdline(command))
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )

        action = "Relaunched" if relaunch else "Launched"
        self.logger.info(
            "%s instance %s (PID=%s, profile=%s)",
            action,
            instance_id,
            process.pid,
            profile_dir,
        )
        return ManagedInstance(instance_id=instance_id, profile_dir=profile_dir, process=process)

    def _build_command(self, profile_dir: Path) -> list[str]:
        args = [
            self._normalize_windows_path(self.config.chrome_path),
            f"--user-data-dir={self._normalize_windows_path(profile_dir)}",
            f"--window-size={self.config.window_width},{self.config.window_height}",
            *_BASE_PERFORMANCE_FLAGS,
        ]

        if self._extension_arg:
            args.append(f"--load-extension={self._extension_arg}")
            args.append(f"--disable-extensions-except={self._extension_arg}")

        if self.config.use_proxy and self.config.proxy_server:
            args.append(f"--proxy-server={self.config.proxy_server}")

        args.extend(self.config.extra_chrome_flags)
        args.append("about:blank")
        return args

    def _discover_profile_dirs(self) -> list[Path]:
        profile_root = self.config.profiles_root
        profile_root.mkdir(parents=True, exist_ok=True)
        discovered = sorted(
            [child.resolve() for child in profile_root.iterdir() if child.is_dir()],
            key=lambda path: path.name.lower(),
        )
        if discovered:
            self.logger.info(
                "Discovered %s existing profile folder(s): %s",
                len(discovered),
                ", ".join(path.name for path in discovered),
            )
        else:
            self.logger.info("No existing profile folders found in %s", profile_root)
        return discovered

    def _create_missing_profiles(self, existing_profiles: list[Path]) -> list[Path]:
        missing_count = max(0, self.config.instances - len(existing_profiles))
        if missing_count == 0:
            self.logger.info("No additional profiles needed (target=%s).", self.config.instances)
            return []

        created: list[Path] = []
        used_names = {path.name.lower() for path in existing_profiles}
        index = 1
        while len(created) < missing_count:
            folder_name = f"profile_{index:02d}"
            index += 1
            if folder_name.lower() in used_names:
                continue

            profile_dir = (self.config.profiles_root / folder_name).resolve()
            if profile_dir.exists():
                used_names.add(folder_name.lower())
                continue

            profile_dir.mkdir(parents=True, exist_ok=True)
            created.append(profile_dir)
            used_names.add(folder_name.lower())
            self.logger.info("Created profile folder: %s", profile_dir)

        return created

    def _validate_extension_folders(self, folders: list[Path]) -> list[Path]:
        if not folders:
            self.logger.info("No extension folders configured.")
            return []

        valid_folders: list[Path] = []
        for folder in folders:
            path = folder.resolve()
            if not path.is_dir():
                self.logger.error(
                    "Extension folder rejected (not found): %s",
                    self._normalize_windows_path(path),
                )
                continue

            manifest = path / "manifest.json"
            if not manifest.is_file():
                self.logger.error(
                    "Extension folder rejected (manifest.json missing): %s",
                    self._normalize_windows_path(path),
                )
                continue

            valid_folders.append(path)
            self.logger.info(
                "Extension folder accepted: %s",
                self._normalize_windows_path(path),
            )

        if not valid_folders:
            self.logger.warning("No valid extension folders will be loaded.")
        return valid_folders

    def _build_extension_argument(self, folders: list[Path]) -> str:
        return ",".join(self._normalize_windows_path(path) for path in folders)

    def _normalize_windows_path(self, path: Path) -> str:
        # Normalized absolute path is required for Chromium-compatible browser arguments on Windows.
        return os.path.normpath(os.path.abspath(str(path)))
