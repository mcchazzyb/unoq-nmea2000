"""Ask the HOST to power the board off cleanly (Setup tab "Shut down" button).

The app runs in a container and cannot power the board off itself. It writes a small request file into
the bind-mounted ``.cache`` directory; a host systemd path unit (``deploy/unoq-shutdown.path``) sees the
file and runs ``deploy/unoq_shutdown.sh``, which calls ``systemctl poweroff``.

The file holds the kernel BOOT ID of the boot it was written in. The host acts only if that matches the
current boot. Without that guard, a request left behind by a power cut right after the button was pressed
would shut the board down again at every following boot. The board has no RTC, so a timestamp cannot be
used for this; the boot id is shared between container and host and always correct.

Nothing here touches the CAN bus.
"""
from __future__ import annotations

import os
from pathlib import Path

BOOT_ID_PATH = "/proc/sys/kernel/random/boot_id"
REQUEST_NAME = "shutdown-request"


class ShutdownUnavailable(RuntimeError):
    """The boot id could not be read, so a request the host would accept cannot be made."""


def request_file(cache_dir: Path) -> Path:
    return Path(cache_dir) / REQUEST_NAME


def _boot_id(path: str) -> str:
    try:
        raw = Path(path).read_text().strip()
    except OSError as exc:
        raise ShutdownUnavailable(f"cannot read the boot id: {exc}") from exc
    if not raw:
        raise ShutdownUnavailable("the boot id is empty")
    return raw


def request_shutdown(path: Path, boot_id_path: str = BOOT_ID_PATH) -> None:
    """Write the request atomically (temp file, fsync, rename, fsync directory).

    The host unit watches only the final name, so it can never see a half-written file.
    Raises ShutdownUnavailable (no boot id) or OSError (cannot write)."""
    boot = _boot_id(boot_id_path)
    path = Path(path)
    clear_markers(path)  # an old outcome must not be mistaken for this request's result
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        os.write(fd, (boot + "\n").encode())
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    dir_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def failed_file(path: Path) -> Path:
    return Path(path).with_name(Path(path).name + ".failed")


def queued_file(path: Path) -> Path:
    return Path(path).with_name(Path(path).name + ".queued")


def has_failed(path: Path) -> bool:
    """True when the host took the request but could not queue the power-off (it wrote <file>.failed)."""
    return failed_file(path).exists()


def has_queued(path: Path) -> bool:
    """True once systemctl accepted the power-off (the host wrote <file>.queued). The only proof of success."""
    return queued_file(path).exists()


def clear_markers(path: Path) -> None:
    """Forget the outcome of any earlier request (called for each new request and at app start)."""
    failed_file(path).unlink(missing_ok=True)
    queued_file(path).unlink(missing_ok=True)


def is_pending(path: Path) -> bool:
    """True while the host has not yet picked the request up (it deletes the file when it does)."""
    return Path(path).exists()
