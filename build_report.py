"""Cross-module build/version report for the MAST ecosystem.

Collects git identity (branch, HEAD SHA, describe, dirty flag, submodule
recorded-vs-checked-out SHAs) for every MAST repo in a workspace, plus the
loaded MAST_common package version/path. Designed to be exposed via a tiny
FastAPI router in each MAST service so partial / inconsistent deployments
become visible.
"""
from __future__ import annotations

import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

MAST_REPO_PREFIX = "MAST"
COMMON_PACKAGE_NAME = "MAST_common"
COMMON_IMPORT_NAME = "common"


class SubmoduleReport(BaseModel):
    path: str
    recorded_sha: str
    checked_out_sha: str
    matches: bool


class RepoReport(BaseModel):
    name: str
    path: str
    branch: Optional[str] = None
    head_sha: Optional[str] = None
    head_describe: Optional[str] = None
    dirty: bool = False
    dirty_summary: Optional[str] = None
    submodules: list[SubmoduleReport] = Field(default_factory=list)
    error: Optional[str] = None


class CommonPackageReport(BaseModel):
    installed_version: Optional[str] = None
    installed_path: Optional[str] = None
    note: Optional[str] = None


class BuildReport(BaseModel):
    host: str
    generated_at: datetime
    workspace_root: str
    repos: list[RepoReport]
    common_package: CommonPackageReport
    common_sha_consistency: list[str] = Field(
        default_factory=list,
        description="Distinct MAST_common SHAs referenced across submodule pins. >1 entry = drift.",
    )


def _run_git(args: list[str], cwd: Path) -> tuple[str, str, int]:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip(), result.stderr.strip(), result.returncode


def _summarize_porcelain(porcelain: str) -> tuple[bool, Optional[str]]:
    lines = [line for line in porcelain.splitlines() if line]
    if not lines:
        return False, None
    counts: dict[str, int] = {}
    for line in lines:
        code = line[:2].strip() or "?"
        counts[code] = counts.get(code, 0) + 1
    parts = [f"{k}:{v}" for k, v in sorted(counts.items())]
    return True, f"{len(lines)} files ({', '.join(parts)})"


def _collect_submodules(repo: Path) -> list[SubmoduleReport]:
    # NOTE: do not use _run_git here — its .strip() would eat the leading marker
    # character on the first line ('+', '-', 'U' or ' '), which we rely on below.
    result = subprocess.run(
        ["git", "submodule", "status", "--recursive"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout:
        return []
    out: list[SubmoduleReport] = []
    for line in result.stdout.splitlines():
        # `<marker><sha> <path> (<describe>)` — marker: ' ' clean, '+' diverged,
        # '-' uninitialised, 'U' conflicts. The <sha> shown is the *checked-out*
        # commit, not the recorded one; we read the recorded SHA from the parent
        # tree separately so a drift case shows both values.
        line = line.rstrip("\n")
        if not line:
            continue
        marker = line[0] if line[0] in {" ", "+", "-", "U"} else " "
        rest = line[1:].strip() if line[0] in {" ", "+", "-", "U"} else line.strip()
        tokens = rest.split(None, 2)
        if len(tokens) < 2:
            continue
        status_sha = tokens[0]
        sub_path = tokens[1]
        recorded = _recorded_submodule_sha(repo, sub_path) or status_sha
        checked_out = status_sha if marker != "-" else ""
        out.append(
            SubmoduleReport(
                path=sub_path,
                recorded_sha=recorded,
                checked_out_sha=checked_out,
                matches=(marker == " "),
            )
        )
    return out


def _recorded_submodule_sha(repo: Path, sub_path: str) -> Optional[str]:
    """Return the submodule SHA *recorded* in the parent's HEAD tree."""
    stdout, _, code = _run_git(["ls-tree", "HEAD", sub_path], repo)
    if code != 0 or not stdout:
        return None
    # Format: `<mode> commit <sha>\t<path>`
    parts = stdout.split()
    if len(parts) >= 3 and parts[1] == "commit":
        return parts[2]
    return None


def _collect_repo(repo: Path) -> RepoReport:
    if not (repo / ".git").exists():
        return RepoReport(name=repo.name, path=str(repo), error="not a git repository")
    try:
        branch_out, _, _ = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo)
        sha_out, _, _ = _run_git(["rev-parse", "HEAD"], repo)
        desc_out, _, _ = _run_git(["describe", "--tags", "--always", "--dirty"], repo)
        porcelain_out, _, _ = _run_git(["status", "--porcelain"], repo)
        dirty, dirty_summary = _summarize_porcelain(porcelain_out)
        return RepoReport(
            name=repo.name,
            path=str(repo),
            branch=branch_out or None,
            head_sha=sha_out or None,
            head_describe=desc_out or None,
            dirty=dirty,
            dirty_summary=dirty_summary,
            submodules=_collect_submodules(repo),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return RepoReport(name=repo.name, path=str(repo), error=str(exc))


def _collect_common_package() -> CommonPackageReport:
    from importlib import metadata

    try:
        version = metadata.version(COMMON_PACKAGE_NAME)
    except metadata.PackageNotFoundError:
        version = None
        note: Optional[str] = "package not installed via importlib metadata (editable / source layout)"
    else:
        note = None

    path: Optional[str] = None
    try:
        common_pkg = __import__(COMMON_IMPORT_NAME)
        path = getattr(common_pkg, "__file__", None)
    except ImportError:
        path = None
        if note is None:
            note = "common package not importable in this process"

    return CommonPackageReport(installed_version=version, installed_path=path, note=note)


def _derive_common_sha_consistency(repos: list[RepoReport]) -> list[str]:
    shas: set[str] = set()
    for repo in repos:
        for sub in repo.submodules:
            if "common" in sub.path.lower():
                if sub.checked_out_sha:
                    shas.add(sub.checked_out_sha)
                if sub.recorded_sha:
                    shas.add(sub.recorded_sha)
    return sorted(shas)


def collect_build_report(workspace_root: Path) -> BuildReport:
    """Walk `workspace_root` for sibling MAST_* git repos and assemble a report.

    `workspace_root` is the directory that contains all `MAST_*` repos as siblings
    (i.e. the `/Users/.../MAST/` dev-side dir, or `/opt/MAST/` on a deployed host).
    """
    workspace_root = workspace_root.resolve()
    repos: list[RepoReport] = []
    if workspace_root.is_dir():
        for child in sorted(workspace_root.iterdir()):
            if not child.is_dir():
                continue
            if not child.name.startswith(MAST_REPO_PREFIX):
                continue
            if not (child / ".git").exists():
                continue
            repos.append(_collect_repo(child))

    return BuildReport(
        host=socket.gethostname(),
        generated_at=datetime.now(timezone.utc),
        workspace_root=str(workspace_root),
        repos=repos,
        common_package=_collect_common_package(),
        common_sha_consistency=_derive_common_sha_consistency(repos),
    )
