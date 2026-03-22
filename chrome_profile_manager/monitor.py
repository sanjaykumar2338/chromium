from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Iterable

from .launcher import ChromeLauncher, ManagedInstance, ProfileAssignment


class ProcessMonitor:
    def __init__(
        self,
        launcher: ChromeLauncher,
        logger: logging.Logger,
        relaunch_delay_seconds: float,
        check_interval_seconds: float,
    ) -> None:
        self.launcher = launcher
        self.logger = logger
        self.relaunch_delay_seconds = relaunch_delay_seconds
        self.check_interval_seconds = check_interval_seconds
        self.profiles: list[Path] = []
        self.profile_index = -1
        self.instances: dict[int, ManagedInstance | None] = {}
        self.profile_targets: dict[int, Path] = {}
        self._next_relaunch_ts: dict[int, float] = {}
        self._should_stop = False

    def start(self, assignments: Iterable[ProfileAssignment]) -> None:
        assignments_list = list(assignments)
        self.profiles = self.launcher.get_managed_profiles() or [
            assignment.profile_dir for assignment in assignments_list
        ]
        if assignments_list:
            try:
                self.profile_index = self.profiles.index(assignments_list[-1].profile_dir)
            except ValueError:
                self.profile_index = -1

        for assignment in assignments_list:
            instance_id = assignment.instance_id
            self.profile_targets[instance_id] = assignment.profile_dir
            self.instances[instance_id] = None
            self._next_relaunch_ts[instance_id] = 0.0
            self._launch_instance(instance_id, relaunch=False)

    def run_forever(self) -> None:
        self.logger.info(
            "Watchdog started (check every %.2fs, relaunch delay %.2fs)",
            self.check_interval_seconds,
            self.relaunch_delay_seconds,
        )
        try:
            while not self._should_stop:
                self._check_instances()
                time.sleep(self.check_interval_seconds)
        except KeyboardInterrupt:
            self.logger.info("Shutdown signal received. Stopping managed instances.")
            self.stop_all()
        finally:
            self.logger.info("Watchdog stopped.")

    def stop_all(self) -> None:
        self._should_stop = True
        for managed in self.instances.values():
            if managed is None:
                continue
            if managed.process.poll() is None:
                managed.process.terminate()
        for managed in self.instances.values():
            if managed is None:
                continue
            if managed.process.poll() is None:
                try:
                    managed.process.wait(timeout=5)
                except Exception:  # noqa: BLE001
                    managed.process.kill()

    def _check_instances(self) -> None:
        now = time.time()
        for instance_id, managed in list(self.instances.items()):
            profile_dir = self.profile_targets[instance_id]
            if managed is None:
                if now >= self._next_relaunch_ts.get(instance_id, 0.0):
                    self._launch_instance(instance_id, relaunch=True)
                continue

            code = managed.process.poll()
            if code is None:
                continue

            next_profile = self._select_next_profile(
                instance_id=instance_id,
                previous_profile=profile_dir,
            )
            self.profile_targets[instance_id] = next_profile
            self.logger.warning(
                "Instance %s (profile=%s) exited with code %s. Instance exited -> selecting next profile.",
                instance_id,
                profile_dir,
                code,
            )
            self.logger.info(
                "Relaunching with %s after %.2fs.",
                next_profile.name,
                self.relaunch_delay_seconds,
            )
            self.instances[instance_id] = None
            self._next_relaunch_ts[instance_id] = now + self.relaunch_delay_seconds

    def _launch_instance(self, instance_id: int, relaunch: bool) -> None:
        profile_dir = self.profile_targets.get(instance_id)
        if profile_dir is None:
            self.logger.error("No profile mapping found for instance %s.", instance_id)
            return

        if relaunch:
            self.logger.info("Relaunching with %s", profile_dir.name)

        try:
            self.instances[instance_id] = self.launcher.launch(
                instance_id,
                profile_dir=profile_dir,
                relaunch=relaunch,
            )
            self._next_relaunch_ts[instance_id] = 0.0
        except Exception as exc:  # noqa: BLE001
            self.instances[instance_id] = None
            self._next_relaunch_ts[instance_id] = time.time() + self.relaunch_delay_seconds
            self.logger.error(
                "Failed to launch instance %s (profile=%s, error=%s). Next retry in %.2fs.",
                instance_id,
                profile_dir,
                exc,
                self.relaunch_delay_seconds,
            )

    def _select_next_profile(self, instance_id: int, previous_profile: Path) -> Path:
        if not self.profiles:
            return previous_profile

        active_profiles = {
            profile
            for other_instance_id, profile in self.profile_targets.items()
            if other_instance_id != instance_id and self.instances.get(other_instance_id) is not None
        }
        total_profiles = len(self.profiles)
        start_index = self.profile_index

        def _pick_candidate(allow_previous_profile: bool) -> Path | None:
            for step in range(1, total_profiles + 1):
                candidate_index = (start_index + step) % total_profiles
                candidate = self.profiles[candidate_index]
                if candidate in active_profiles:
                    continue
                if (
                    not allow_previous_profile
                    and candidate == previous_profile
                    and total_profiles > 1
                ):
                    continue

                self.profile_index = candidate_index
                return candidate
            return None

        next_profile = _pick_candidate(allow_previous_profile=False)
        if next_profile is not None:
            return next_profile

        next_profile = _pick_candidate(allow_previous_profile=True)
        if next_profile is not None:
            return next_profile

        try:
            self.profile_index = self.profiles.index(previous_profile)
        except ValueError:
            pass
        return previous_profile
