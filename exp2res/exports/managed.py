"""§13.14 manifest, containment, reconciliation, and publication support."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import secrets
import stat
from typing import Literal

from pydantic import ConfigDict, field_validator, model_validator

from exp2res.domain.canonical import canonical_hash, canonical_json_bytes
from exp2res.domain.enums import AssessmentScope
from exp2res.domain.models import (
    BoundaryDatetime,
    StrictModel,
    validate_free_text,
    validate_structural,
)
from exp2res.errors import IntegrityFailureError, ManagedOutputIncompleteError
from exp2res.services.privacy import (
    locked_database_anchor,
    locked_tree_identities_established,
    locked_tree_identity,
    managed_root_paths,
    record_locked_tree_identity,
    report_unproven_residual,
    workspace_database_is_live,
)

from .branch import (
    BranchExportGraph,
    branch_render_input_bundle,
)
from .bullet_pack import render_bullet_pack
from .companions import (
    build_bullet_pack_evidence_map,
    build_evidence_map_document,
    build_self_claims_document,
    build_verification_report,
    companion_bytes,
)
from .graph import AssessmentExportGraph, fs_id_key, render_input_bundle
from .html import render_report_html
from .report import render_report


ENTITY_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,127}$")
_CANDIDATE = re.compile(
    r"^\.exp2res-candidate-(?P<entity>[a-z0-9][a-z0-9_-]{0,127})-"
    r"(?P<nonce>[0-9a-f]{32})$"
)
_ROLLBACK = re.compile(
    r"^\.exp2res-rollback-(?P<entity>[a-z0-9][a-z0-9_-]{0,127})-"
    r"(?P<nonce>[0-9a-f]{32})$"
)
_MEMBER_NAMES = (
    "evidence_map.json",
    "report.html",
    "report.md",
    "self_claims.json",
)
_ALL_NAMES = (*_MEMBER_NAMES, "manifest.json")
_RESUME_MEMBER_NAMES = (
    "bullet_pack.md",
    "evidence_map.json",
    "verification_report.json",
)
_RESUME_ALL_NAMES = (*_RESUME_MEMBER_NAMES, "manifest.json")
# §13.14 rule 1: the two reserved parents, and the managed kind each publishes.
# `branch` holds the `resume` kind because §14.10 keeps the persisted entity
# names while the product-facing artifact is the verified bullet pack.
_PARENT_KIND = {"assessment": "assessment", "branch": "resume"}


class _ManifestModel(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class AssessmentIdentity(_ManifestModel):
    snapshot_title: str
    scope: AssessmentScope

    @field_validator("snapshot_title")
    @classmethod
    def valid_title(cls, value: str) -> str:
        return validate_free_text(value, nonempty=True)


class AssessmentSourceIds(_ManifestModel):
    self_claim_ids: list[str]
    experience_fact_ids: list[str]
    evidence_item_ids: list[str]
    raw_log_ids: list[str]
    gap_question_ids: list[str]
    contradiction_ids: list[str]

    @field_validator("*")
    @classmethod
    def duplicate_free_sorted(cls, value: list[str]) -> list[str]:
        for item in value:
            validate_structural(item)
        if len(value) != len(set(value)):
            raise ValueError("duplicate source ID")
        if value != sorted(value, key=fs_id_key):
            raise ValueError("source IDs are not byte ordered")
        return value


class ManifestMember(_ManifestModel):
    name: Literal["report.md", "report.html", "self_claims.json", "evidence_map.json"]
    sha256: str

    @field_validator("sha256")
    @classmethod
    def lowercase_sha256(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("invalid SHA-256")
        return value


class AssessmentManifest(_ManifestModel):
    manifest_version: Literal[6]
    output_kind: Literal["assessment"]
    entity_id: str
    generation_id: str
    produced_by_run_id: str
    created_at: BoundaryDatetime
    identity: AssessmentIdentity
    source_ids: AssessmentSourceIds
    render_input_sha256: str
    members: list[ManifestMember]

    @field_validator("entity_id")
    @classmethod
    def valid_entity_id(cls, value: str) -> str:
        if not ENTITY_ID.fullmatch(value):
            raise ValueError("invalid managed-output entity ID")
        return value

    @field_validator("generation_id", "produced_by_run_id")
    @classmethod
    def valid_production_ids(cls, value: str) -> str:
        return validate_structural(value)

    @field_validator("render_input_sha256")
    @classmethod
    def valid_render_hash(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("invalid render-input SHA-256")
        return value

    @model_validator(mode="after")
    def exact_member_set(self) -> "AssessmentManifest":
        names = [item.name for item in self.members]
        if names != sorted(_MEMBER_NAMES, key=fs_id_key):
            raise ValueError("manifest member set or order is invalid")
        return self


class ResumeIdentity(_ManifestModel):
    branch_name: str
    job_description_id: str
    assessment_snapshot_id: str

    @field_validator("branch_name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        return validate_free_text(value, nonempty=True)

    @field_validator("job_description_id", "assessment_snapshot_id")
    @classmethod
    def valid_ids(cls, value: str) -> str:
        return validate_structural(value)


class ResumeSourceIds(_ManifestModel):
    resume_bullet_ids: list[str]
    assessment_snapshot_ids: list[str]
    job_description_ids: list[str]
    self_claim_ids: list[str]
    experience_fact_ids: list[str]
    evidence_item_ids: list[str]
    raw_log_ids: list[str]
    jd_requirement_ids: list[str]

    @field_validator("*")
    @classmethod
    def duplicate_free_sorted(cls, value: list[str]) -> list[str]:
        for item in value:
            validate_structural(item)
        if len(value) != len(set(value)):
            raise ValueError("duplicate source ID")
        if value != sorted(value, key=fs_id_key):
            raise ValueError("source IDs are not byte ordered")
        return value


class ResumeManifestMember(_ManifestModel):
    name: Literal["bullet_pack.md", "evidence_map.json", "verification_report.json"]
    sha256: str

    @field_validator("sha256")
    @classmethod
    def lowercase_sha256(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("invalid SHA-256")
        return value


class ResumeManifest(_ManifestModel):
    manifest_version: Literal[6]
    output_kind: Literal["resume"]
    entity_id: str
    generation_id: str
    produced_by_run_id: str
    created_at: BoundaryDatetime
    identity: ResumeIdentity
    source_ids: ResumeSourceIds
    render_input_sha256: str
    members: list[ResumeManifestMember]

    @field_validator("entity_id")
    @classmethod
    def valid_entity_id(cls, value: str) -> str:
        if not ENTITY_ID.fullmatch(value):
            raise ValueError("invalid managed-output entity ID")
        return value

    @field_validator("generation_id", "produced_by_run_id")
    @classmethod
    def valid_production_ids(cls, value: str) -> str:
        return validate_structural(value)

    @field_validator("render_input_sha256")
    @classmethod
    def valid_render_hash(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("invalid render-input SHA-256")
        return value

    @model_validator(mode="after")
    def exact_member_set(self) -> "ResumeManifest":
        names = [item.name for item in self.members]
        if names != sorted(_RESUME_MEMBER_NAMES, key=fs_id_key):
            raise ValueError("manifest member set or order is invalid")
        return self

    @model_validator(mode="after")
    def identity_matches_source_ids(self) -> "ResumeManifest":
        # §13.14 rule 2: each of these lists holds exactly the one ID the
        # identity names, so a manifest can never claim two anchors.
        if self.source_ids.assessment_snapshot_ids != [
            self.identity.assessment_snapshot_id
        ]:
            raise ValueError("snapshot source list disagrees with identity")
        if self.source_ids.job_description_ids != [self.identity.job_description_id]:
            raise ValueError("job-description source list disagrees with identity")
        return self


def validate_entity_id(value: str) -> None:
    if not ENTITY_ID.fullmatch(value):
        raise IntegrityFailureError("managed_output_entity_id_invalid")


def _validate_snapshot_title(graph: AssessmentExportGraph) -> None:
    snapshot = graph.snapshot.value
    if snapshot.title != "Self-Assessment — Global":
        raise IntegrityFailureError("snapshot_title_invalid")


def assessment_member_bytes(graph: AssessmentExportGraph) -> dict[str, bytes]:
    _validate_snapshot_title(graph)
    return {
        "report.md": render_report(graph),
        "report.html": render_report_html(graph),
        "self_claims.json": companion_bytes(build_self_claims_document(graph)),
        "evidence_map.json": companion_bytes(build_evidence_map_document(graph)),
    }


def render_input_sha256(graph: AssessmentExportGraph) -> str:
    bundle = render_input_bundle(graph)
    return canonical_hash(bundle.model_dump(mode="python"))


def build_assessment_manifest(
    graph: AssessmentExportGraph,
    members: dict[str, bytes],
    *,
    created_at: datetime,
) -> AssessmentManifest:
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise IntegrityFailureError("manifest_created_at_naive")
    if set(members) != set(_MEMBER_NAMES):
        raise IntegrityFailureError("assessment_member_set_invalid")
    _validate_snapshot_title(graph)
    snapshot = graph.snapshot.value
    return AssessmentManifest(
        manifest_version=6,
        output_kind="assessment",
        entity_id=snapshot.id,
        generation_id=graph.snapshot.generation_id,
        produced_by_run_id=graph.snapshot.produced_by_run_id,
        created_at=created_at,
        identity=AssessmentIdentity(
            snapshot_title=snapshot.title, scope=snapshot.scope
        ),
        source_ids=AssessmentSourceIds(**graph.source_ids()),
        render_input_sha256=render_input_sha256(graph),
        members=[
            ManifestMember(
                name=name,
                sha256=hashlib.sha256(members[name]).hexdigest(),
            )
            for name in sorted(_MEMBER_NAMES, key=fs_id_key)
        ],
    )


def manifest_bytes(manifest: AssessmentManifest | ResumeManifest) -> bytes:
    return canonical_json_bytes(manifest.model_dump(mode="python")) + b"\n"


def branch_member_bytes(graph: BranchExportGraph) -> dict[str, bytes]:
    return {
        "bullet_pack.md": render_bullet_pack(graph),
        "evidence_map.json": companion_bytes(build_bullet_pack_evidence_map(graph)),
        "verification_report.json": companion_bytes(build_verification_report(graph)),
    }


def branch_render_input_sha256(graph: BranchExportGraph) -> str:
    bundle = branch_render_input_bundle(graph)
    return canonical_hash(bundle.model_dump(mode="python"))


def build_branch_manifest(
    graph: BranchExportGraph,
    members: dict[str, bytes],
    *,
    created_at: datetime,
) -> ResumeManifest:
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise IntegrityFailureError("manifest_created_at_naive")
    if set(members) != set(_RESUME_MEMBER_NAMES):
        raise IntegrityFailureError("resume_member_set_invalid")
    branch = graph.branch.value
    return ResumeManifest(
        manifest_version=6,
        output_kind="resume",
        entity_id=branch.id,
        generation_id=graph.branch.generation_id,
        produced_by_run_id=graph.branch.produced_by_run_id,
        created_at=created_at,
        identity=ResumeIdentity(
            branch_name=branch.name,
            job_description_id=branch.job_description_id,
            assessment_snapshot_id=branch.assessment_snapshot_id,
        ),
        source_ids=ResumeSourceIds(**graph.source_ids()),
        render_input_sha256=branch_render_input_sha256(graph),
        members=[
            ResumeManifestMember(
                name=name,
                sha256=hashlib.sha256(members[name]).hexdigest(),
            )
            for name in sorted(_RESUME_MEMBER_NAMES, key=fs_id_key)
        ],
    )


def _lstat(path: Path):
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def _is_real_dir(path: Path) -> bool:
    info = _lstat(path)
    return info is not None and stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)


def _is_real_file(path: Path) -> bool:
    info = _lstat(path)
    return info is not None and stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode)


def _canonical_roots(workspace: Path) -> tuple[Path, Path]:
    try:
        root = workspace.resolve(strict=True)
    except OSError as error:
        raise ManagedOutputIncompleteError((str(workspace.absolute()),)) from error
    out = root / "out"
    if not _is_real_dir(out):
        raise ManagedOutputIncompleteError((str(out.absolute()),))
    try:
        real_out = out.resolve(strict=True)
        real_out.relative_to(root)
    except (OSError, ValueError) as error:
        raise ManagedOutputIncompleteError((str(out.absolute()),)) from error
    return root, real_out


def _validate_existing_path(path: Path, out_root: Path, *, directory: bool) -> None:
    try:
        relative = path.relative_to(out_root)
    except ValueError as error:
        raise OSError("managed path escapes out root") from error
    current = out_root
    for part in relative.parts:
        current = current / part
        info = _lstat(current)
        if info is None or stat.S_ISLNK(info.st_mode):
            raise OSError("missing or symlinked managed path")
        if current != path and not stat.S_ISDIR(info.st_mode):
            raise OSError("non-directory managed ancestor")
    info = path.lstat()
    if directory and not stat.S_ISDIR(info.st_mode):
        raise OSError("managed path is not a directory")
    if not directory and not stat.S_ISREG(info.st_mode):
        raise OSError("managed path is not a file")
    try:
        path.resolve(strict=True).relative_to(out_root)
    except (OSError, ValueError) as error:
        raise OSError("managed path resolves outside out root") from error


def _mkdir_private(path: Path, out_root: Path) -> tuple[int, int] | None:
    """Make one managed directory private, naming the entry if it made it.

    §13.14 rule 9 binds a reserved parent absent at the lock to this command's
    own creation, and to that entry rather than to the name it was given: the
    identity comes off the open descriptor, because a pathname re-read after
    the close would name whatever answers by then, which is the substitution
    the binding exists to catch.
    """

    created = False
    identity: tuple[int, int] | None = None
    parent_descriptor = _open_directory_fd(path.parent, out_root)
    try:
        if _lstat(path) is None:
            os.mkdir(path.name, 0o700, dir_fd=parent_descriptor)
            created = True
        descriptor = os.open(
            path.name,
            _open_flags(os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)),
            dir_fd=parent_descriptor,
        )
        try:
            os.fchmod(descriptor, 0o700)
            opened = os.fstat(descriptor)
            if stat.S_IMODE(opened.st_mode) != 0o700:
                raise OSError("private directory mode unavailable")
            if created:
                identity = (opened.st_dev, opened.st_ino)
        finally:
            os.close(descriptor)
    finally:
        os.close(parent_descriptor)
    _validate_existing_path(path, out_root, directory=True)
    return identity


def _open_flags(base: int) -> int:
    return base | (os.O_NOFOLLOW if hasattr(os, "O_NOFOLLOW") else 0)


def _open_directory_fd(path: Path, out_root: Path) -> int:
    """Open every managed path component with directory/no-follow semantics."""

    try:
        relative = path.relative_to(out_root)
    except ValueError as error:
        raise OSError("managed directory escapes out root") from error
    flags = _open_flags(os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    descriptor = os.open(out_root, flags)
    try:
        for part in relative.parts:
            next_descriptor = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _write_private_file(path: Path, data: bytes, out_root: Path) -> None:
    _validate_existing_path(path.parent, out_root, directory=True)
    parent_descriptor = _open_directory_fd(path.parent, out_root)
    try:
        descriptor = os.open(
            path.name,
            _open_flags(os.O_WRONLY | os.O_CREAT | os.O_EXCL),
            0o600,
            dir_fd=parent_descriptor,
        )
    except BaseException:
        os.close(parent_descriptor)
        raise
    try:
        os.fchmod(descriptor, 0o600)
        remaining = memoryview(data)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("short managed-output write")
            remaining = remaining[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
        os.close(parent_descriptor)
    _validate_existing_path(path, out_root, directory=False)
    if stat.S_IMODE(path.lstat().st_mode) != 0o600:
        raise OSError("private file mode unavailable")


def _read_regular(path: Path, out_root: Path) -> bytes:
    _validate_existing_path(path, out_root, directory=False)
    parent_descriptor = _open_directory_fd(path.parent, out_root)
    try:
        descriptor = os.open(
            path.name,
            _open_flags(os.O_RDONLY),
            dir_fd=parent_descriptor,
        )
    except BaseException:
        os.close(parent_descriptor)
        raise
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError("managed member is not a regular file")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)
        os.close(parent_descriptor)


def _fsync_directory(path: Path, out_root: Path) -> None:
    _validate_existing_path(path, out_root, directory=True)
    descriptor = _open_directory_fd(path, out_root)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _rename(source: Path, destination: Path) -> None:
    if source.parent != destination.parent:
        raise OSError("managed rename must stay within one parent")
    out_root = source.parent.parent
    _validate_existing_path(source.parent, out_root, directory=True)
    _validate_existing_path(source, out_root, directory=True)
    if _lstat(destination) is not None:
        raise OSError("managed rename destination already exists")
    parent_descriptor = _open_directory_fd(source.parent, out_root)
    try:
        source_info = os.stat(
            source.name, dir_fd=parent_descriptor, follow_symlinks=False
        )
        if not stat.S_ISDIR(source_info.st_mode):
            raise OSError("managed rename source changed")
        os.rename(
            source.name,
            destination.name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
        )
    finally:
        os.close(parent_descriptor)


def _tree_is_safe(path: Path, out_root: Path) -> bool:
    try:
        _validate_existing_path(path, out_root, directory=True)
        with os.scandir(path) as entries:
            for entry in entries:
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode):
                    return False
                child = path / entry.name
                if stat.S_ISDIR(info.st_mode):
                    if not _tree_is_safe(child, out_root):
                        return False
                elif not stat.S_ISREG(info.st_mode):
                    return False
        return True
    except OSError:
        return False


def _remove_tree(
    path: Path, out_root: Path, still_live: Callable[[], bool] | None = None
) -> bool:
    """Remove one contained real tree, member by member.

    `still_live` is §13.14 rule 9's binding, re-asked before every unlink and
    the closing rmdir. One set is many pathname-resolved operations, so a
    workspace replaced after an early member is gone would otherwise have the
    rest of the tree removed from the replacement.
    """

    if still_live is not None and not still_live():
        return False
    if not _tree_is_safe(path, out_root):
        return False
    try:
        with os.scandir(path) as entries:
            names = sorted((entry.name for entry in entries), key=fs_id_key)
        for name in names:
            child = path / name
            info = child.lstat()
            if stat.S_ISDIR(info.st_mode):
                if not _remove_tree(child, out_root, still_live):
                    return False
            else:
                if still_live is not None and not still_live():
                    return False
                _validate_existing_path(child, out_root, directory=False)
                parent_descriptor = _open_directory_fd(path, out_root)
                try:
                    current = os.stat(
                        name, dir_fd=parent_descriptor, follow_symlinks=False
                    )
                    if not stat.S_ISREG(current.st_mode):
                        return False
                    os.unlink(name, dir_fd=parent_descriptor)
                finally:
                    os.close(parent_descriptor)
        if still_live is not None and not still_live():
            return False
        _validate_existing_path(path, out_root, directory=True)
        parent_descriptor = _open_directory_fd(path.parent, out_root)
        try:
            current = os.stat(
                path.name, dir_fd=parent_descriptor, follow_symlinks=False
            )
            if not stat.S_ISDIR(current.st_mode):
                return False
            os.rmdir(path.name, dir_fd=parent_descriptor)
        finally:
            os.close(parent_descriptor)
        return True
    except OSError:
        return False


def _remove_entry(
    path: Path, out_root: Path, still_live: Callable[[], bool] | None = None
) -> bool:
    """Remove one contained real file/tree without following any link."""

    info = _lstat(path)
    if info is None:
        return True
    if stat.S_ISLNK(info.st_mode):
        return False
    if stat.S_ISDIR(info.st_mode):
        return _remove_tree(path, out_root, still_live)
    if not stat.S_ISREG(info.st_mode):
        return False
    if still_live is not None and not still_live():
        return False
    try:
        _validate_existing_path(path, out_root, directory=False)
        parent_descriptor = _open_directory_fd(path.parent, out_root)
        try:
            current = os.stat(
                path.name, dir_fd=parent_descriptor, follow_symlinks=False
            )
            if not stat.S_ISREG(current.st_mode):
                return False
            os.unlink(path.name, dir_fd=parent_descriptor)
        finally:
            os.close(parent_descriptor)
        return True
    except OSError:
        return False


def _directory_names(path: Path, out_root: Path) -> list[str]:
    _validate_existing_path(path, out_root, directory=True)
    descriptor = _open_directory_fd(path, out_root)
    try:
        with os.scandir(descriptor) as entries:
            return sorted((entry.name for entry in entries), key=fs_id_key)
    finally:
        os.close(descriptor)


def _inspect_set(
    path: Path, parent: Path, out_root: Path
) -> AssessmentManifest | ResumeManifest | None:
    """Read one managed set and return its manifest only when it matches.

    §13.14 rule 3 binds a set to its parent: the managed kind is decided by
    which reserved parent the set sits under, never by the manifest's own
    `output_kind`, so a resume manifest planted under `out/assessment/` — or
    the reverse — is never matching and never current.
    """

    kind = _PARENT_KIND.get(parent.name)
    if kind is None:
        return None
    model = AssessmentManifest if kind == "assessment" else ResumeManifest
    all_names = _ALL_NAMES if kind == "assessment" else _RESUME_ALL_NAMES
    try:
        _validate_existing_path(parent, out_root, directory=True)
        _validate_existing_path(path, out_root, directory=True)
        if stat.S_IMODE(path.lstat().st_mode) != 0o700:
            return None
        names = _directory_names(path, out_root)
        if names != sorted(all_names, key=fs_id_key):
            return None
        manifest_path = path / "manifest.json"
        if stat.S_IMODE(manifest_path.lstat().st_mode) != 0o600:
            return None
        stored_manifest = _read_regular(manifest_path, out_root)
        manifest = model.model_validate_json(stored_manifest)
        if stored_manifest != manifest_bytes(manifest):
            return None
        path_entity = path.name
        reserved = _CANDIDATE.fullmatch(path.name) or _ROLLBACK.fullmatch(path.name)
        if reserved is not None:
            path_entity = reserved.group("entity")
        if manifest.entity_id != path_entity or manifest.output_kind != kind:
            return None
        for member in manifest.members:
            member_path = path / member.name
            if stat.S_IMODE(member_path.lstat().st_mode) != 0o600:
                return None
            if hashlib.sha256(_read_regular(member_path, out_root)).hexdigest() != member.sha256:
                return None
        return manifest
    except (OSError, ValueError, TypeError):
        return None


def _ensure_managed_parents(
    workspace: Path, *, still_live: Callable[[], bool] | None = None
) -> tuple[Path, Path, Path]:
    """Create both reserved managed parents under §13.14 rule 9's binding.

    Creating and chmod-ing a directory mutates the tree exactly as removing one
    does, so the binding reaches here as well. A caller's gate runs before
    `_canonical_roots` has resolved anything; a replacement landing in between
    would otherwise have both parents created and made private in a workspace
    whose writer lock this command never took.
    """

    _root, out_root = _canonical_roots(workspace)
    _out_key, assessment_key, branch_key = managed_root_paths(workspace)
    assessment = out_root / "assessment"
    branch = out_root / "branch"
    for parent, key in ((assessment, assessment_key), (branch, branch_key)):
        if still_live is not None and not still_live():
            raise ManagedOutputIncompleteError((str(out_root),))
        created = _mkdir_private(parent, out_root)
        if created is not None:
            record_locked_tree_identity(key, created)
    return out_root, assessment, branch


def reconcile_managed_outputs(workspace: Path) -> tuple[str, ...]:
    """Apply §13.14 rule 5's preamble while the caller holds the writer lock.

    Rule 9 binds this pass too: it removes abandoned candidates and rollbacks
    and promotes a surviving rollback into place, all by pathname, so a
    workspace replaced between the lock acquisition and this preamble would
    have another workspace's half-published sets reconciled while this one's
    stayed abandoned. The mismatch refuses the whole pass rather than its
    removals alone, because the promotion mutates a foreign tree exactly as a
    removal does, and it refuses before creating the managed parents so the
    refusal leaves no directories behind either.
    """

    still_live = locked_workspace_predicate(workspace)
    residuals: set[str] = set()

    def refuse_if_replaced(reported: tuple[str, ...]) -> tuple[str, ...]:
        """Route a report through the unwithdrawable channel after a mismatch.

        Every exit from this pass runs it, because a mismatch can arrive at any
        of them and §14.14 rule 4's existence re-check would drop a path that
        names a workspace this command never wrote to.
        """

        if still_live():
            return reported
        refused = tuple(
            sorted({*reported, str((workspace / "out").absolute())}, key=fs_id_key)
        )
        report_unproven_residual(refused)
        return refused

    if not still_live():
        return refuse_if_replaced(())
    try:
        out_root, assessment, branch = _ensure_managed_parents(
            workspace, still_live=still_live
        )
    except ManagedOutputIncompleteError as error:
        return refuse_if_replaced(error.residual_paths)
    except OSError:
        return refuse_if_replaced((str((workspace / "out").absolute()),))

    for parent in (assessment, branch):
        try:
            names = _directory_names(parent, out_root)
        except OSError:
            residuals.add(str(parent))
            continue

        rollbacks: dict[str, list[Path]] = {}
        for name in names:
            path = parent / name
            candidate_match = _CANDIDATE.fullmatch(name)
            if candidate_match is not None:
                if not _remove_tree(path, out_root, still_live):
                    residuals.add(str(path))
                continue
            rollback_match = _ROLLBACK.fullmatch(name)
            if rollback_match is not None:
                rollbacks.setdefault(rollback_match.group("entity"), []).append(path)

        for entity_id, siblings in rollbacks.items():
            final_path = parent / entity_id
            if _lstat(final_path) is None:
                if len(siblings) != 1:
                    residuals.update(str(path) for path in siblings)
                    continue
                rollback = siblings[0]
                rollback_manifest = _inspect_set(rollback, parent, out_root)
                if rollback_manifest is None or rollback_manifest.entity_id != entity_id:
                    residuals.add(str(rollback))
                    continue
                if not still_live():
                    residuals.add(str(rollback))
                    continue
                try:
                    _rename(rollback, final_path)
                    _fsync_directory(parent, out_root)
                except OSError:
                    residuals.add(str(rollback if _lstat(rollback) is not None else final_path))
                continue
            final_manifest = _inspect_set(final_path, parent, out_root)
            if final_manifest is None or final_manifest.entity_id != entity_id:
                residuals.update(str(path) for path in siblings)
                residuals.add(str(final_path))
                continue
            removed_any = False
            for rollback in siblings:
                rollback_manifest = _inspect_set(rollback, parent, out_root)
                if rollback_manifest is None or rollback_manifest.entity_id != entity_id:
                    residuals.add(str(rollback))
                elif not _remove_tree(rollback, out_root, still_live):
                    residuals.add(str(rollback))
                else:
                    removed_any = True
            if removed_any:
                try:
                    _fsync_directory(parent, out_root)
                except OSError:
                    residuals.add(str(parent))
    return refuse_if_replaced(tuple(sorted(residuals, key=fs_id_key)))


def locked_workspace_predicate(workspace: Path) -> Callable[[], bool]:
    """Build §13.14 rule 9's predicate for one workspace under the held lock.

    Every entry point that touches managed output builds it the same way, so
    the binding cannot drift between them. What each question re-reads is the
    record the lock established, never the filesystem's own account of what the
    pathname ought to be — that account is written by whoever holds the
    pathname, which is the substitution the rule exists to catch.
    """

    expected_database = locked_database_anchor()
    managed_roots = managed_root_paths(workspace)

    def still_live() -> bool:
        """Answer whether both the database and the managed tree are the ones.

        The database identity alone leaves one substitution uncovered: `out/`
        or either reserved parent renamed and replaced while the database stays
        put. Every helper below reopens those by pathname, so the pass would
        remove from and publish into the replacement while the sets it
        committed to stayed detached — and unlike a whole-workspace
        replacement, no later check would notice.

        The comparison is against what the lock established and nothing else,
        which makes both directions a mismatch: a root that changed hands, and
        equally a root standing where the lock found none and this command
        created none. Absence on both sides is the ordinary state before a
        first publication, and the creation step records what it made, so the
        only entry that answers here is one this command is entitled to.
        """

        if not workspace_database_is_live(workspace, expected_database):
            return False
        if not locked_tree_identities_established():
            return False
        for root in managed_roots:
            info = _lstat(root)
            identity = None if info is None else (info.st_dev, info.st_ino)
            if identity != locked_tree_identity(root):
                return False
        return True

    return still_live


def _managed_set_paths(
    workspace: Path,
    entity_ids: tuple[str, ...] | list[str],
    *,
    parent_name: str,
    existing_only: bool = True,
) -> tuple[str, ...]:
    """Report-only paths of existing ID-keyed sets an invalidation affects.

    A trigger site records these through the CLI residual sink *before* its
    interruptible post-commit cleanup, so an interrupt between the business
    commit and the removal still reports the retained stale set (§13
    stale-export invalidation rule). Envelope assembly drops any reported path
    that no longer exists, so a completed removal clears its own pending report.

    `existing_only=False` reports every selected set whether or not the
    pathname currently holds it. §13.14 rule 9's mismatch arm needs that: there
    the pathname reaches a *different* workspace, so what exists under it says
    nothing about the sets left stale in the one the mutation committed to, and
    filtering by it would report complete invalidation of a tree this process
    never touched. §13.14 rule 1's ID revalidation applies on both arms: this
    report stands in for a removal, and on the mismatch arm it is the only
    warning an owner gets about the set left stale.
    """

    selected = tuple(sorted(set(entity_ids), key=fs_id_key))
    try:
        _root, out_root = _canonical_roots(workspace)
    except ManagedOutputIncompleteError:
        out_root = (workspace / "out").absolute()
    parent = out_root / parent_name
    paths = []
    for entity_id in selected:
        if ENTITY_ID.fullmatch(entity_id) is None:
            raise IntegrityFailureError("managed_output_entity_id_invalid")
        path = parent / entity_id
        if not existing_only:
            paths.append(str(path))
            continue
        try:
            exists = _lstat(path) is not None
        except OSError:
            # Fail closed: an unreadable managed parent is not evidence that
            # the set is gone, and this report is the only warning an owner
            # gets about a stale export. Callers run it before the transaction
            # they are about to commit (§13 stale-export invalidation rule), so
            # raising here would refuse the swap over a filesystem condition
            # §13.13 rule 6 classifies as residual.
            exists = True
        if exists:
            paths.append(str(path))
    return tuple(paths)


def _remove_managed_sets(
    workspace: Path,
    entity_ids: tuple[str, ...] | list[str],
    *,
    parent_name: str,
    removed_ledger: list[str] | None = None,
    still_live: Callable[[], bool] | None = None,
) -> tuple[str, ...]:
    """Remove exactly the selected ID-keyed sets after commit.

    `removed_ledger` receives each set path this pass durably removed — that is,
    unlinked and then flushed. The return value is only produced once the pass
    finishes, so a caller that must report durable effects after a cancellation
    mid-pass has no other way to learn what this function already removed
    (§14.14 rule 6).

    `still_live` is §13.14 rule 9's binding, re-asked at every point this pass
    is about to commit to the pathname: once after the roots resolve, and again
    before each entry is unlinked. One check at the entry would authorize the
    whole pass, and the pass is long — every selected assessment ID, then every
    branch ID — so a workspace replaced anywhere inside it would have the
    remainder removed from the foreign tree. Whatever the pass has not reached
    when the answer turns false is reported residual instead.

    POSIX unlinks by name, so this narrows the window rather than closing it:
    no filesystem offers removal bound to the inode an open connection holds.
    §8.1's single business writer and §29's local boundary — anything able to
    rename the workspace root is already inside it — bound what remains.
    """

    selected = tuple(sorted(set(entity_ids), key=fs_id_key))
    for entity_id in selected:
        if ENTITY_ID.fullmatch(entity_id) is None:
            raise IntegrityFailureError("managed_output_entity_id_invalid")
    if not selected:
        return ()

    def live() -> bool:
        return still_live is None or still_live()

    try:
        _root, out_root = _canonical_roots(workspace)
    except ManagedOutputIncompleteError as error:
        return error.residual_paths
    parent = out_root / parent_name
    if not live():
        return tuple(str(parent / entity_id) for entity_id in selected)
    try:
        if _lstat(parent) is None:
            if not live():
                return tuple(str(parent / entity_id) for entity_id in selected)
            return ()
        _validate_existing_path(parent, out_root, directory=True)
    except OSError:
        # Fail closed for the same reason as the per-entry probe below: a
        # parent that turns unsearchable after the business commit is a
        # §13.13 rule 6 residual, never an exception that would cost the
        # caller the committed result it is holding for the envelope.
        return tuple(str(parent / entity_id) for entity_id in selected)

    residuals: set[str] = set()
    unlinked: list[str] = []
    for position, entity_id in enumerate(selected):
        path = parent / entity_id
        if not live():
            residuals.update(
                str(parent / remaining) for remaining in selected[position:]
            )
            break
        try:
            if _lstat(path) is None:
                if not live():
                    residuals.add(str(path))
                continue
            removed = _remove_entry(path, out_root, still_live)
        except OSError:
            # Fail closed: this pass runs after the business commit, so an
            # entry that turns unreadable mid-pass is a §13.13 rule 6 residual,
            # never an exception that would cost the caller its committed
            # result.
            residuals.add(str(path))
            continue
        if removed:
            unlinked.append(str(path))
        else:
            residuals.add(str(path))
    if unlinked:
        try:
            _fsync_directory(parent, out_root)
        except OSError:
            # The unlinks are visible but not durable. The returned residual is
            # lost whenever a later half of the same pass is cancelled, so the
            # ledger must not claim these removals either.
            residuals.update(unlinked)
            residuals.add(str(parent))
            unlinked = []
        else:
            if not live():
                residuals.update(unlinked)
                residuals.add(str(parent))
                unlinked = []
    # Only a flushed removal is banked: an interrupt inside the flush skips this
    # line for the same reason, and the caller keeps reporting those sets rather
    # than claiming a removal a crash could undo.
    if removed_ledger is not None:
        removed_ledger.extend(unlinked)
    return tuple(sorted(residuals, key=fs_id_key))


def assessment_set_paths(
    workspace: Path,
    snapshot_ids: tuple[str, ...] | list[str],
    *,
    existing_only: bool = True,
) -> tuple[str, ...]:
    return _managed_set_paths(
        workspace,
        snapshot_ids,
        parent_name="assessment",
        existing_only=existing_only,
    )


def remove_assessment_sets(
    workspace: Path,
    snapshot_ids: tuple[str, ...] | list[str],
    *,
    removed_ledger: list[str] | None = None,
    still_live: Callable[[], bool] | None = None,
) -> tuple[str, ...]:
    return _remove_managed_sets(
        workspace,
        snapshot_ids,
        parent_name="assessment",
        removed_ledger=removed_ledger,
        still_live=still_live,
    )


def branch_set_paths(
    workspace: Path,
    branch_ids: tuple[str, ...] | list[str],
    *,
    existing_only: bool = True,
) -> tuple[str, ...]:
    """§13.14 rule 1: a resume set lives only at `out/branch/<branch-id>/`."""

    return _managed_set_paths(
        workspace, branch_ids, parent_name="branch", existing_only=existing_only
    )


def remove_branch_sets(
    workspace: Path,
    branch_ids: tuple[str, ...] | list[str],
    *,
    removed_ledger: list[str] | None = None,
    still_live: Callable[[], bool] | None = None,
) -> tuple[str, ...]:
    return _remove_managed_sets(
        workspace,
        branch_ids,
        parent_name="branch",
        removed_ledger=removed_ledger,
        still_live=still_live,
    )


def remove_managed_sets_for_locked_database(
    workspace: Path,
    *,
    snapshot_ids: tuple[str, ...] | list[str] = (),
    branch_ids: tuple[str, ...] | list[str] = (),
    removed_ledger: list[str] | None = None,
) -> tuple[str, ...]:
    """Remove the selected ID-keyed sets, or report them all residual.

    §13.14 rule 9: every removal below re-resolves the workspace pathname,
    while the §8.1 writer lock pins the inode it was opened through rather
    than the name. A workspace renamed and replaced in between therefore
    leaves the caller committing to one tree and unlinking from another. The
    identity anchored when the lock was acquired is what separates the two,
    and a mismatch — including an anchor that could never be established —
    reports every in-scope set as an unsuccessful invalidation instead of
    removing anything.

    The anchor arrives from `writer_lock` rather than from a parameter, so a
    call site cannot supply one read too late to mean anything; the removal
    pass re-asks the same question at every point it is about to commit to the
    pathname.

    Both arms report every selected set, unfiltered by what the pathname holds:
    on a mismatch the pathname reaches another workspace entirely, so its
    contents are no evidence about the sets this mutation left stale. Assessment
    sets precede branch sets on both arms, in the order the call sites already
    report them.
    """

    still_live = locked_workspace_predicate(workspace)

    def selected() -> tuple[str, ...]:
        return (
            *assessment_set_paths(workspace, snapshot_ids, existing_only=False),
            *branch_set_paths(workspace, branch_ids, existing_only=False),
        )

    if not still_live():
        stranded = selected()
        report_unproven_residual(stranded)
        return stranded
    try:
        residuals = (
            *remove_assessment_sets(
                workspace,
                snapshot_ids,
                removed_ledger=removed_ledger,
                still_live=still_live,
            ),
            *remove_branch_sets(
                workspace,
                branch_ids,
                removed_ledger=removed_ledger,
                still_live=still_live,
            ),
        )
    except BaseException:
        if not still_live():
            report_unproven_residual(selected())
        raise
    if residuals and not still_live():
        report_unproven_residual(residuals)
    return residuals


def remove_all_managed_output_entries(workspace: Path) -> tuple[str, ...]:
    """Remove every contained entry below both reserved managed parents.

    §13.14 rule 9 binds this sweep like every other removal. It is the widest
    of them — it takes whatever it finds rather than a selected ID list — so on
    a replacement it would empty another workspace's managed output entirely
    while the tree its caller is deleting from kept all of it. A mismatch
    reports the managed root, because the names this pass would otherwise
    enumerate are the replacement's and say nothing about what was stranded.
    """

    still_live = locked_workspace_predicate(workspace)
    managed_root = str((workspace / "out").absolute())
    if not still_live():
        return (managed_root,)
    # §13.13 rule 6: this enumeration serves privacy deletions that commit
    # whether or not cleanup succeeds, so every filesystem error becomes a
    # residual path rather than an exception that could abort the caller.
    try:
        _root, out_root = _canonical_roots(workspace)
    except ManagedOutputIncompleteError as error:
        return error.residual_paths
    except OSError:
        return (managed_root,)

    residuals: set[str] = set()
    for parent_name in ("assessment", "branch"):
        parent = out_root / parent_name
        if not still_live():
            return (managed_root,)
        try:
            if _lstat(parent) is None:
                continue
            names = _directory_names(parent, out_root)
        except OSError:
            residuals.add(str(parent))
            continue
        removed = False
        for name in names:
            path = parent / name
            try:
                entry_removed = _remove_entry(path, out_root, still_live)
            except OSError:
                entry_removed = False
            if entry_removed:
                removed = True
            else:
                residuals.add(str(path))
        if removed:
            try:
                _fsync_directory(parent, out_root)
            except OSError:
                residuals.add(str(parent))
    if not still_live():
        return (managed_root,)
    return tuple(sorted(residuals, key=fs_id_key))


@dataclass(frozen=True)
class CurrentAssessmentRead:
    """One §13.14 rule 3 read verdict for a reader that publishes nothing.

    The verdict distinguishes the three §30 rule 7 managed-output outcomes:
    `not_current` for a set §13.14 rule 5 can still replace in place,
    `residual` for stable state no re-export can replace on its own, and
    `changed` for a final entry that moved between one validation and the
    next no-follow operation.
    """

    status: Literal["current", "not_current", "residual", "changed"]
    members: dict[str, bytes] | None = None


_CURRENT_NOT = CurrentAssessmentRead("not_current")
_CURRENT_RESIDUAL = CurrentAssessmentRead("residual")
_CURRENT_CHANGED = CurrentAssessmentRead("changed")


def _entry_identity(path: Path) -> tuple[int, int, int] | None:
    """Identify one no-follow entry, so a replacement is never mistaken for it."""

    info = _lstat(path)
    if info is None:
        return None
    return (info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode))


def read_current_assessment_members(
    workspace: Path, graph: AssessmentExportGraph
) -> CurrentAssessmentRead:
    """Revalidate the published assessment set and return its member bytes.

    §13.14 rule 3's complete current-output standard applied by a reader: the
    manifest must be structurally valid, matching, and agree with the graph
    read from the caller's coherent database snapshot, including a recomputed
    `render_input_sha256`. The returned bytes are the bytes whose digests this
    function verified against that manifest, so a §30 view serves exactly what
    it validated rather than re-reading afterwards.

    The reader creates, repairs, and publishes nothing: every failure is one
    of the three refusal verdicts above, never a different path.
    """

    snapshot = graph.snapshot.value
    if ENTITY_ID.fullmatch(snapshot.id) is None:
        # Rule 1's stored-ID invariant is the writer's fail-closed check; a
        # reader that reached one is looking at corrupted stored state.
        raise IntegrityFailureError("managed_output_entity_id_invalid")

    try:
        root = workspace.resolve(strict=True)
    except OSError:
        return _CURRENT_RESIDUAL
    out = root / "out"
    if _lstat(out) is None:
        # Nothing has been published under this workspace at all.
        return _CURRENT_NOT
    if not _is_real_dir(out):
        return _CURRENT_RESIDUAL
    try:
        out_root = out.resolve(strict=True)
        out_root.relative_to(root)
    except (OSError, ValueError):
        return _CURRENT_RESIDUAL

    parent = out_root / "assessment"
    if _lstat(parent) is None:
        return _CURRENT_NOT
    if not _is_real_dir(parent):
        return _CURRENT_RESIDUAL

    final_path = parent / snapshot.id
    identity = _entry_identity(final_path)
    if identity is None:
        return _CURRENT_NOT

    def _stable(verdict: CurrentAssessmentRead) -> CurrentAssessmentRead:
        # Rule 6's narrow §30 reporting exception: a failure whose validated
        # entry no longer occupies the final path is a concurrent publication,
        # not an owner-removable residual.
        if _entry_identity(final_path) != identity:
            return _CURRENT_CHANGED
        return verdict

    # Every observation from here on is guarded: once the entry has been
    # identified, a failure the reader sees because that entry was replaced
    # under it is the concurrent publication, never manual-repair state.
    if not _is_real_dir(final_path):
        return _stable(_CURRENT_RESIDUAL)

    manifest = _inspect_set(final_path, parent, out_root)
    if manifest is None:
        # Rule 5: an incomplete, invalid, or superseded-version set at the
        # final path aborts publication instead of being overwritten.
        return _stable(_CURRENT_RESIDUAL)
    if not _manifest_matches_prior(manifest, graph):
        return _stable(_CURRENT_RESIDUAL)
    if not _manifest_matches_current(manifest, graph):
        # A replaceable prior set whose sources or render input moved on: the
        # ordinary §14.9 export publishes over it.
        return _stable(_CURRENT_NOT)

    members: dict[str, bytes] = {}
    for member in manifest.members:
        try:
            data = _read_regular(final_path / member.name, out_root)
        except OSError:
            return _stable(_CURRENT_RESIDUAL)
        if hashlib.sha256(data).hexdigest() != member.sha256:
            return _stable(_CURRENT_RESIDUAL)
        members[member.name] = data
    if set(members) != set(_MEMBER_NAMES):
        return _stable(_CURRENT_RESIDUAL)
    if _entry_identity(final_path) != identity:
        return _CURRENT_CHANGED
    return CurrentAssessmentRead("current", members)


def _candidate_cleanup(
    path: Path, out_root: Path, still_live: Callable[[], bool] | None = None
) -> None:
    """Remove one candidate, or report it when the binding will not allow it.

    An absent entry is ordinarily a finished cleanup. Under a failed binding it
    is the opposite: the pathname reaches a tree that never held this
    candidate, while the one the pass built it in keeps it — so the fast path
    would let the original exception escape with nothing naming what was left.
    """

    if _lstat(path) is None:
        if still_live is not None and not still_live():
            raise ManagedOutputIncompleteError((str(path),))
        return
    if not _remove_tree(path, out_root, still_live):
        raise ManagedOutputIncompleteError((str(path),))


def _clean_or_report_candidate(
    path: Path,
    out_root: Path,
    still_live: Callable[[], bool] | None,
    error: BaseException,
) -> None:
    """Clean the candidate without letting the report displace what is louder.

    Two in-flight errors outrank this refusal. §14.14 rule 6: a cancelled
    command reports as cancelled, not as an integrity failure that hides the
    interrupt. And an incomplete-output error already carries its own residual
    set, usually a wider one than this single path — a refusal raised over it
    would narrow the report to the candidate alone. Either way the refusal is
    still heard: it travels the channel the envelope assembles however the
    command ended.
    """

    try:
        _candidate_cleanup(path, out_root, still_live)
    except ManagedOutputIncompleteError as refused:
        if not isinstance(error, (KeyboardInterrupt, ManagedOutputIncompleteError)):
            raise
        report_unproven_residual(refused.residual_paths)


def _build_candidate(
    parent: Path,
    out_root: Path,
    entity_id: str,
    members: dict[str, bytes],
    manifest: AssessmentManifest | ResumeManifest,
    member_names: tuple[str, ...],
    still_live: Callable[[], bool] | None = None,
) -> Path:
    """Write one complete candidate set, bound to the locked database.

    Every member write reopens the candidate through its absolute path, so a
    single check before the first one authorizes the rest of a pass that spans
    the whole set: the later writes would land in a replacement holding the
    same candidate name, and the cleanup below would then recursively remove a
    directory in that foreign tree. Its own removal is bound for the same
    reason, and refuses rather than reaching there.
    """

    def require_live(residual: Path) -> None:
        if still_live is not None and not still_live():
            raise ManagedOutputIncompleteError((str(residual),))

    candidate = parent / f".exp2res-candidate-{entity_id}-{secrets.token_hex(16)}"
    try:
        require_live(out_root)
        _mkdir_private(candidate, out_root)
    except BaseException as error:
        _clean_or_report_candidate(candidate, out_root, still_live, error)
        raise
    try:
        for name in sorted(member_names, key=fs_id_key):
            require_live(candidate)
            _write_private_file(candidate / name, members[name], out_root)
        require_live(candidate)
        _write_private_file(candidate / "manifest.json", manifest_bytes(manifest), out_root)
        if _inspect_set(candidate, parent, out_root) != manifest:
            raise IntegrityFailureError("candidate_manifest_validation_failed")
        _fsync_directory(candidate, out_root)
        _fsync_directory(parent, out_root)
        require_live(candidate)
        return candidate
    except BaseException as error:
        _clean_or_report_candidate(candidate, out_root, still_live, error)
        raise


def _manifest_matches_prior(
    manifest: AssessmentManifest, graph: AssessmentExportGraph
) -> bool:
    """Recognize a prior set whose lifecycle-sensitive parts may be stale.

    The render hash and the source closure both move with §14.7 lifecycle
    events (a gap answered after export adds its answer log), so a prior set
    for the same snapshot, generation, and identity stays replaceable; the
    reuse short-circuit in publish_assessment still requires full equality.
    """

    snapshot = graph.snapshot.value
    return not (
        manifest.entity_id != snapshot.id
        or manifest.generation_id != graph.snapshot.generation_id
        or manifest.produced_by_run_id != graph.snapshot.produced_by_run_id
        or manifest.identity
        != AssessmentIdentity(
            snapshot_title=snapshot.title, scope=snapshot.scope
        )
    )


def _manifest_matches_current(
    manifest: AssessmentManifest, graph: AssessmentExportGraph
) -> bool:
    return (
        _manifest_matches_prior(manifest, graph)
        and manifest.source_ids == AssessmentSourceIds(**graph.source_ids())
        and manifest.render_input_sha256 == render_input_sha256(graph)
    )


def _bound_publication(workspace: Path) -> Callable[[], bool]:
    """Refuse publication outright unless §13.14 rule 9's binding holds.

    Publication writes a candidate, renames it into place, and removes both the
    prior same-view sets and its own rollback — all by pathname. On a mismatch
    none of that may touch the tree the pathname now reaches, and the refusal
    comes before the managed parents are created so it leaves nothing behind
    there either. The predicate is returned for the removals further in, which
    re-ask it as the protocol proceeds.
    """

    still_live = locked_workspace_predicate(workspace)
    if not still_live():
        raise ManagedOutputIncompleteError((str((workspace / "out").absolute()),))
    return still_live


def _remove_stale_same_view(
    parent: Path,
    out_root: Path,
    candidate_manifest: AssessmentManifest,
    still_live: Callable[[], bool] | None = None,
) -> None:
    """Remove every prior assessment set §13.14 rule 5's identity replaces.

    Rule 5 compares the manifest's exact `scope` identity, which `Assessment
    Identity` types as the single-member `AssessmentScope`: a set whose stored
    scope is anything else cannot parse into a manifest at all, so it is
    already `None` below rather than a same-view candidate. Every prior set
    that does parse therefore names this view.
    """

    residuals: list[str] = []
    for name in _directory_names(parent, out_root):
        if name == candidate_manifest.entity_id or name.startswith(".exp2res-"):
            continue
        path = parent / name
        info = _lstat(path)
        if info is not None and (
            stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode)
        ):
            residuals.append(str(path))
            continue
        if not ENTITY_ID.fullmatch(name):
            continue
        if _inspect_set(path, parent, out_root) is None:
            continue
        if not _remove_tree(path, out_root, still_live):
            residuals.append(str(path))
    if residuals:
        raise ManagedOutputIncompleteError(residuals)
    try:
        _fsync_directory(parent, out_root)
    except OSError as error:
        raise ManagedOutputIncompleteError((str(parent),)) from error


def _member_bytes_equal(
    final_path: Path,
    out_root: Path,
    members: dict[str, bytes],
    member_names: tuple[str, ...] = _MEMBER_NAMES,
) -> bool:
    try:
        return all(
            _read_regular(final_path / name, out_root) == members[name]
            for name in member_names
        )
    except OSError:
        return False


def _publish_set(
    *,
    out_root: Path,
    parent: Path,
    entity_id: str,
    members: dict[str, bytes],
    candidate_manifest,
    member_names: tuple[str, ...],
    all_names: tuple[str, ...],
    matches_prior,
    matches_current,
    render_hash: str,
    still_live: Callable[[], bool] | None = None,
):
    """Run §13.14 rules 4–8 for one already rendered and validated set.

    Both managed kinds share this body verbatim. What differs between them —
    which members exist, what makes a prior manifest replaceable, and how the
    render hash is computed — arrives as parameters, so the candidate,
    rollback, restoration, and post-commit rules cannot drift apart between an
    assessment set and a bullet pack.
    """

    def require_live(residual: Path) -> None:
        """Refuse the next mutation unless §13.14 rule 9's binding still holds.

        The gate at the entry point cannot carry the whole protocol: a
        first-time export has no prior set and no rollback, so every removal
        that consults the predicate is skipped and the candidate would be
        written and made visible in a replacement without a second question.
        Each step that writes to the pathname therefore asks again.
        """

        if still_live is not None and not still_live():
            raise ManagedOutputIncompleteError((str(residual),))

    def strand_under_mismatch(paths: tuple[str, ...], error: BaseException) -> None:
        """Name what a move already made left behind in the tree it was made in.

        A rename that succeeded is a fact; probing the pathname afterwards is
        not, because under a failed binding the probe answers about a tree this
        pass never wrote to. The report therefore travels the unwithdrawable
        channel on its own, and only an ordinary failure is escalated into a
        raise — §14.14 rule 6 keeps a cancelled command reporting as cancelled.
        """

        reported = tuple(sorted(set(paths), key=fs_id_key))
        report_unproven_residual(reported)
        if not isinstance(error, KeyboardInterrupt):
            raise ManagedOutputIncompleteError(reported)

    def require_live_pair(residual: Path, rollback: Path | None) -> None:
        """Refuse, naming the rollback too when the prior set is already aside."""

        if still_live is not None and not still_live():
            reported = (str(residual),) if rollback is None else (
                str(residual),
                str(rollback),
            )
            raise ManagedOutputIncompleteError(
                tuple(sorted(set(reported), key=fs_id_key))
            )

    candidate = _build_candidate(
        parent,
        out_root,
        entity_id,
        members,
        candidate_manifest,
        member_names,
        still_live=still_live,
    )
    final_path = parent / entity_id
    rollback: Path | None = None
    published = False
    try:
        if _lstat(final_path) is not None:
            prior_manifest = _inspect_set(final_path, parent, out_root)
            if prior_manifest is None or not matches_prior(prior_manifest):
                raise ManagedOutputIncompleteError((str(final_path),))
            if (
                prior_manifest.source_ids == candidate_manifest.source_ids
                and prior_manifest.render_input_sha256
                == candidate_manifest.render_input_sha256
                and _member_bytes_equal(final_path, out_root, members, member_names)
            ):
                _candidate_cleanup(candidate, out_root, still_live)
                try:
                    _fsync_directory(parent, out_root)
                except OSError as error:
                    raise ManagedOutputIncompleteError((str(parent),)) from error
                require_live(candidate)
                paths = tuple(
                    str(final_path / name) for name in sorted(all_names, key=fs_id_key)
                )
                return prior_manifest, paths

            # §13.14 rule 5 portable fallback: native directory exchange is
            # deliberately unavailable in V1.
            rollback = parent / (
                f".exp2res-rollback-{entity_id}-{secrets.token_hex(16)}"
            )
            require_live(candidate)
            moved_aside = False
            try:
                _rename(final_path, rollback)
                moved_aside = True
                _fsync_directory(parent, out_root)
            except BaseException as error:
                if moved_aside and still_live is not None and not still_live():
                    strand_under_mismatch((str(candidate), str(rollback)), error)
                if _lstat(rollback) is not None and _lstat(final_path) is None:
                    try:
                        _rename(rollback, final_path)
                        _fsync_directory(parent, out_root)
                        rollback = None
                    except BaseException:
                        raise ManagedOutputIncompleteError((str(rollback),)) from None
                raise
        require_live_pair(candidate, rollback)
        try:
            _rename(candidate, final_path)
            published = True
        except BaseException:
            if rollback is not None and _lstat(final_path) is None:
                try:
                    # Rule 5 permits exactly one no-follow restoration attempt.
                    _rename(rollback, final_path)
                    _fsync_directory(parent, out_root)
                    rollback = None
                except BaseException:
                    residuals = [str(rollback)]
                    if _lstat(candidate) is not None and not _remove_tree(
                        candidate, out_root, still_live
                    ):
                        residuals.append(str(candidate))
                    raise ManagedOutputIncompleteError(residuals) from None
            raise

        try:
            _fsync_directory(parent, out_root)
            if rollback is not None:
                if not _remove_tree(rollback, out_root, still_live):
                    raise ManagedOutputIncompleteError((str(rollback),))
                rollback = None
            _fsync_directory(parent, out_root)
        except ManagedOutputIncompleteError:
            raise
        except OSError as error:
            residual = str(rollback) if rollback is not None else str(final_path)
            raise ManagedOutputIncompleteError((residual,)) from error

        require_live_pair(final_path, rollback)
        current = _inspect_set(final_path, parent, out_root)
        if (
            current is None
            or not matches_current(current)
            or current.render_input_sha256 != render_hash
        ):
            raise ManagedOutputIncompleteError((str(final_path),))
        paths = tuple(
            str(final_path / name) for name in sorted(all_names, key=fs_id_key)
        )
        return current, paths
    except BaseException as error:
        if not published:
            _clean_or_report_candidate(candidate, out_root, still_live, error)
        raise


def publish_assessment(
    workspace: Path,
    graph: AssessmentExportGraph,
    *,
    clock=None,
) -> tuple[AssessmentManifest, tuple[str, ...]]:
    """Publish and revalidate one complete assessment set under §13.14."""

    validate_entity_id(graph.snapshot.value.id)
    still_live = _bound_publication(workspace)
    out_root, parent, _branch = _ensure_managed_parents(workspace, still_live=still_live)
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    members = assessment_member_bytes(graph)
    candidate_manifest = build_assessment_manifest(graph, members, created_at=now)

    _remove_stale_same_view(parent, out_root, candidate_manifest, still_live)
    return _publish_set(
        out_root=out_root,
        parent=parent,
        entity_id=graph.snapshot.value.id,
        members=members,
        candidate_manifest=candidate_manifest,
        member_names=_MEMBER_NAMES,
        all_names=_ALL_NAMES,
        matches_prior=lambda manifest: isinstance(manifest, AssessmentManifest)
        and _manifest_matches_prior(manifest, graph),
        matches_current=lambda manifest: isinstance(manifest, AssessmentManifest)
        and _manifest_matches_current(manifest, graph),
        render_hash=render_input_sha256(graph),
        still_live=still_live,
    )


def _branch_manifest_matches_prior(
    manifest: ResumeManifest, graph: BranchExportGraph
) -> bool:
    """Recognize a prior set for this same branch generation.

    A branch is replaced by name, not in place: Stage 10 supersedes the old
    branch and allocates a new ID, and §13.13 removes that old ID-keyed set.
    So a prior set at *this* path is a re-export of this same branch, and only
    its render-sensitive parts — the source closure and the render hash — may
    legitimately have moved underneath it.
    """

    branch = graph.branch.value
    return not (
        manifest.entity_id != branch.id
        or manifest.generation_id != graph.branch.generation_id
        or manifest.produced_by_run_id != graph.branch.produced_by_run_id
        or manifest.identity
        != ResumeIdentity(
            branch_name=branch.name,
            job_description_id=branch.job_description_id,
            assessment_snapshot_id=branch.assessment_snapshot_id,
        )
    )


def publish_branch(
    workspace: Path,
    graph: BranchExportGraph,
    *,
    clock=None,
) -> tuple[ResumeManifest, tuple[str, ...]]:
    """Publish and revalidate one complete verified bullet pack under §13.14.

    There is no same-view sweep here: rule 5's enumeration is the assessment
    kind's replacement rule, while a branch's replacement identity is the
    folded name Stage 10 resolves, whose superseded set §13.13 already removed
    through `remove_branch_sets`.
    """

    validate_entity_id(graph.branch.value.id)
    still_live = _bound_publication(workspace)
    out_root, _assessment, parent = _ensure_managed_parents(workspace, still_live=still_live)
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    members = branch_member_bytes(graph)
    candidate_manifest = build_branch_manifest(graph, members, created_at=now)
    render_hash = branch_render_input_sha256(graph)

    def matches_prior(manifest) -> bool:
        return isinstance(manifest, ResumeManifest) and _branch_manifest_matches_prior(
            manifest, graph
        )

    def matches_current(manifest) -> bool:
        return (
            matches_prior(manifest)
            and manifest.source_ids == ResumeSourceIds(**graph.source_ids())
            and manifest.render_input_sha256 == render_hash
        )

    return _publish_set(
        out_root=out_root,
        parent=parent,
        entity_id=graph.branch.value.id,
        members=members,
        candidate_manifest=candidate_manifest,
        member_names=_RESUME_MEMBER_NAMES,
        all_names=_RESUME_ALL_NAMES,
        matches_prior=matches_prior,
        matches_current=matches_current,
        render_hash=render_hash,
        still_live=still_live,
    )
