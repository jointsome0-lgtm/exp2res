"""Generic bubblewrap assembly and provider-free read-confinement canary."""

from __future__ import annotations

from dataclasses import dataclass
import glob
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import tempfile
from typing import Sequence


@dataclass(frozen=True)
class SandboxLayout:
    """Host paths intentionally exposed inside one isolated child."""

    workspace: Path
    bwrap_binary: Path
    ro_binds: tuple[tuple[Path, str], ...] = ()
    tmpfs_mounts: tuple[str, ...] = ()
    top_dirs: tuple[str, ...] = ()
    extra_env: tuple[tuple[str, str], ...] = ()
    chdir: str | None = None


@dataclass(frozen=True)
class CanaryResult:
    available: bool
    effective: bool
    reason: str


def discover_bwrap(explicit: Path | None = None) -> Path | None:
    candidate = str(explicit) if explicit is not None else shutil.which("bwrap")
    if candidate is None:
        return None
    path = Path(candidate)
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return None
    return resolved if resolved.is_file() and os.access(resolved, os.X_OK) else None


def _runtime_binds() -> tuple[Path, ...]:
    verify_paths = ssl.get_default_verify_paths()
    candidates = [
        Path("/usr"),
        Path("/bin"),
        Path("/etc/ssl"),
        Path("/etc/pki"),
        Path("/etc/ca-certificates"),
    ]
    candidates.extend(Path(value) for value in sorted(glob.glob("/lib*")))
    candidates.extend(
        Path(value)
        for value in ("/etc/resolv.conf", "/etc/hosts", "/etc/nsswitch.conf")
    )
    result: list[Path] = []
    for candidate in candidates:
        if candidate.exists() and candidate not in result:
            result.append(candidate)
    for value in (verify_paths.cafile, verify_paths.capath):
        if not value:
            continue
        for candidate in (Path(value), Path(value).resolve()):
            if not candidate.exists() or candidate in result:
                continue
            if any(root.is_dir() and candidate.is_relative_to(root) for root in result):
                continue
            result.append(candidate)
    return tuple(result)


def build_bwrap_command(
    layout: SandboxLayout,
    command: Sequence[str],
) -> list[str]:
    """Build the closed namespace command; the canary proves effectiveness."""

    if not command:
        raise ValueError("sandbox command must not be empty")
    workspace = layout.workspace.resolve(strict=True)
    if not workspace.is_dir():
        raise ValueError("sandbox workspace must be a directory")
    result = [
        str(layout.bwrap_binary),
        "--unshare-all",
        "--share-net",
        "--die-with-parent",
        "--new-session",
        "--clearenv",
        "--dir",
        "/work",
        "--dir",
        "/tmp",
        "--dir",
        "/etc",
    ]
    for sandbox_path in layout.top_dirs:
        result.extend(("--dir", sandbox_path))
    # A transient directory must exist before a file can be mounted inside it.
    for sandbox_path in layout.tmpfs_mounts:
        result.extend(("--tmpfs", sandbox_path))
    for host_path in _runtime_binds():
        result.extend(("--ro-bind", str(host_path), str(host_path)))
    for host_path, sandbox_path in layout.ro_binds:
        result.extend(("--ro-bind", str(host_path), sandbox_path))
    result.extend(
        (
            "--setenv",
            "PATH",
            "/runner:/usr/local/bin:/usr/bin:/bin",
            "--setenv",
            "HOME",
            "/work",
        )
    )
    for name, value in layout.extra_env:
        result.extend(("--setenv", name, value))
    if layout.chdir is not None:
        if not layout.chdir.startswith("/"):
            raise ValueError("sandbox working directory must be absolute")
    result.extend(
        (
            "--bind",
            str(workspace),
            "/work",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
        )
    )
    if layout.chdir is not None:
        # Select cwd only after its host workspace bind is installed.
        result.extend(("--chdir", layout.chdir))
    result.extend(("--", *command))
    return result


def _repository_probe_files(repository_root: Path) -> tuple[Path, ...]:
    candidates = [
        repository_root / "SDD.md",
        repository_root / ".env",
        repository_root / ".exp2res" / "exp2res.sqlite",
        repository_root / "exp2res" / "__init__.py",
        repository_root / "pyproject.toml",
    ]
    result = [candidate for candidate in candidates if candidate.is_file()]
    if not result:
        result.extend(candidate for candidate in repository_root.iterdir() if candidate.is_file())
    if not result:
        result.append(repository_root / "__exp2res_missing_repository_probe__")
    return tuple(result)


def probe_isolation(
    *,
    repository_root: Path,
    bwrap_binary: Path | None = None,
    ambient_paths: Sequence[Path] = (),
) -> CanaryResult:
    """Prove `/work` is readable while planted and ambient files are not."""

    bwrap = discover_bwrap(bwrap_binary)
    if bwrap is None:
        return CanaryResult(False, False, "bubblewrap is not installed or executable")
    shell = Path("/usr/bin/sh")
    cat = Path("/usr/bin/cat")
    if not shell.is_file() or not cat.is_file():
        return CanaryResult(False, False, "the canary requires /usr/bin/sh and cat")

    parent = Path(tempfile.mkdtemp(prefix="exp2res-sandbox-probe-"))
    workspace = parent / "work"
    outside = parent / "outside-canary"
    try:
        workspace.mkdir(mode=0o700)
        (workspace / "inside-canary").write_bytes(b"inside\n")
        outside.write_bytes(b"outside\n")
        outside.chmod(0o600)
        denied = [outside, *_repository_probe_files(repository_root.resolve(strict=True))]
        denied.extend(path.resolve(strict=True) for path in ambient_paths)
        script = (
            "/usr/bin/cat /work/inside-canary >/dev/null || exit 41; "
            "for target do /usr/bin/cat \"$target\" >/dev/null 2>&1 && exit 42; done; "
            "exit 0"
        )
        command = build_bwrap_command(
            SandboxLayout(workspace=workspace, bwrap_binary=bwrap),
            [str(shell), "-c", script, "exp2res-canary", *(str(item) for item in denied)],
        )
        try:
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=10,
                check=False,
                start_new_session=True,
            )
        except (OSError, subprocess.TimeoutExpired):
            return CanaryResult(False, False, "bubblewrap or user namespaces are unavailable")
        if completed.returncode == 0:
            return CanaryResult(True, True, "isolation canary passed")
        if completed.returncode == 41:
            return CanaryResult(True, False, "sandbox child could not read /work")
        if completed.returncode == 42:
            return CanaryResult(True, False, "sandbox child read an undeclared host path")
        error = completed.stderr.lower()
        if any(
            marker in error
            for marker in (b"operation not permitted", b"user namespace", b"no permissions")
        ):
            return CanaryResult(
                False, False, "bubblewrap user namespaces are unavailable"
            )
        return CanaryResult(True, False, "bubblewrap canary command failed")
    finally:
        shutil.rmtree(parent, ignore_errors=True)
