"""§13.14 manifest, containment, reconciliation, and publication support."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import secrets
import stat
from typing import Literal
import unicodedata

from pydantic import ConfigDict, field_validator, model_validator

from exp2res.domain.canonical import canonical_hash, canonical_json_bytes
from exp2res.domain.enums import AssessmentScope
from exp2res.domain.models import StrictModel, validate_free_text, validate_structural
from exp2res.errors import IntegrityFailureError, ManagedOutputIncompleteError

from .companions import (
    build_evidence_map_document,
    build_self_claims_document,
    companion_bytes,
)
from .graph import AssessmentExportGraph, id_key, render_input_bundle
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
_MEMBER_NAMES = ("evidence_map.json", "report.md", "self_claims.json")
_ALL_NAMES = (*_MEMBER_NAMES, "manifest.json")


class _ManifestModel(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class AssessmentIdentity(_ManifestModel):
    snapshot_title: str
    scope: AssessmentScope
    scope_target: str | None

    @field_validator("snapshot_title")
    @classmethod
    def valid_title(cls, value: str) -> str:
        return validate_free_text(value, nonempty=True)

    @field_validator("scope_target")
    @classmethod
    def valid_scope_target(cls, value: str | None) -> str | None:
        return None if value is None else validate_structural(value)


class AssessmentSourceIds(_ManifestModel):
    self_claim_ids: list[str]
    self_signal_ids: list[str]
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
        if value != sorted(value, key=id_key):
            raise ValueError("source IDs are not byte ordered")
        return value


class ManifestMember(_ManifestModel):
    name: Literal["report.md", "self_claims.json", "evidence_map.json"]
    sha256: str

    @field_validator("sha256")
    @classmethod
    def lowercase_sha256(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("invalid SHA-256")
        return value


class AssessmentManifest(_ManifestModel):
    manifest_version: Literal[1]
    output_kind: Literal["assessment"]
    entity_id: str
    generation_id: str
    produced_by_run_id: str
    created_at: datetime
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

    @field_validator("created_at")
    @classmethod
    def aware_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("manifest datetime must carry an offset")
        return value

    @field_validator("render_input_sha256")
    @classmethod
    def valid_render_hash(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("invalid render-input SHA-256")
        return value

    @model_validator(mode="after")
    def exact_member_set(self) -> "AssessmentManifest":
        names = [item.name for item in self.members]
        if names != sorted(_MEMBER_NAMES, key=id_key):
            raise ValueError("manifest member set or order is invalid")
        return self


def validate_entity_id(value: str) -> None:
    if not ENTITY_ID.fullmatch(value):
        raise IntegrityFailureError("managed_output_entity_id_invalid")


def assessment_member_bytes(graph: AssessmentExportGraph) -> dict[str, bytes]:
    return {
        "report.md": render_report(graph),
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
    snapshot = graph.snapshot.value
    return AssessmentManifest(
        manifest_version=1,
        output_kind="assessment",
        entity_id=snapshot.id,
        generation_id=graph.snapshot.generation_id,
        produced_by_run_id=graph.snapshot.produced_by_run_id,
        created_at=created_at,
        identity=AssessmentIdentity(
            snapshot_title=snapshot.title,
            scope=snapshot.scope,
            scope_target=snapshot.scope_target,
        ),
        source_ids=AssessmentSourceIds(**graph.source_ids()),
        render_input_sha256=render_input_sha256(graph),
        members=[
            ManifestMember(
                name=name,
                sha256=hashlib.sha256(members[name]).hexdigest(),
            )
            for name in sorted(_MEMBER_NAMES, key=id_key)
        ],
    )


def manifest_bytes(manifest: AssessmentManifest) -> bytes:
    return canonical_json_bytes(manifest.model_dump(mode="python")) + b"\n"


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


def _mkdir_private(path: Path, out_root: Path) -> None:
    parent_descriptor = _open_directory_fd(path.parent, out_root)
    try:
        if _lstat(path) is None:
            os.mkdir(path.name, 0o700, dir_fd=parent_descriptor)
        descriptor = os.open(
            path.name,
            _open_flags(os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)),
            dir_fd=parent_descriptor,
        )
        try:
            os.fchmod(descriptor, 0o700)
            if stat.S_IMODE(os.fstat(descriptor).st_mode) != 0o700:
                raise OSError("private directory mode unavailable")
        finally:
            os.close(descriptor)
    finally:
        os.close(parent_descriptor)
    _validate_existing_path(path, out_root, directory=True)


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


def _remove_tree(path: Path, out_root: Path) -> bool:
    if not _tree_is_safe(path, out_root):
        return False
    try:
        with os.scandir(path) as entries:
            names = sorted((entry.name for entry in entries), key=id_key)
        for name in names:
            child = path / name
            info = child.lstat()
            if stat.S_ISDIR(info.st_mode):
                if not _remove_tree(child, out_root):
                    return False
            else:
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


def _directory_names(path: Path, out_root: Path) -> list[str]:
    _validate_existing_path(path, out_root, directory=True)
    descriptor = _open_directory_fd(path, out_root)
    try:
        with os.scandir(descriptor) as entries:
            return sorted((entry.name for entry in entries), key=id_key)
    finally:
        os.close(descriptor)


def _inspect_set(path: Path, parent: Path, out_root: Path) -> AssessmentManifest | None:
    try:
        _validate_existing_path(parent, out_root, directory=True)
        _validate_existing_path(path, out_root, directory=True)
        if stat.S_IMODE(path.lstat().st_mode) != 0o700:
            return None
        names = _directory_names(path, out_root)
        if names != sorted(_ALL_NAMES, key=id_key):
            return None
        manifest_path = path / "manifest.json"
        if stat.S_IMODE(manifest_path.lstat().st_mode) != 0o600:
            return None
        manifest = AssessmentManifest.model_validate_json(
            _read_regular(manifest_path, out_root)
        )
        path_entity = path.name
        reserved = _CANDIDATE.fullmatch(path.name) or _ROLLBACK.fullmatch(path.name)
        if reserved is not None:
            path_entity = reserved.group("entity")
        if manifest.entity_id != path_entity or manifest.output_kind != "assessment":
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


def _same_view(left: AssessmentIdentity, right: AssessmentIdentity) -> bool:
    if left.scope != right.scope:
        return False
    if left.scope_target is None or right.scope_target is None:
        return left.scope_target is right.scope_target
    left_key = unicodedata.normalize("NFC", left.scope_target).strip().casefold()
    right_key = unicodedata.normalize("NFC", right.scope_target).strip().casefold()
    return left_key == right_key


def _ensure_managed_parents(workspace: Path) -> tuple[Path, Path, Path]:
    _root, out_root = _canonical_roots(workspace)
    assessment = out_root / "assessment"
    branch = out_root / "branch"
    _mkdir_private(assessment, out_root)
    _mkdir_private(branch, out_root)
    return out_root, assessment, branch


def reconcile_managed_outputs(workspace: Path) -> tuple[str, ...]:
    """Apply §13.14 rule 5's preamble while the caller holds the writer lock."""

    residuals: set[str] = set()
    try:
        out_root, assessment, branch = _ensure_managed_parents(workspace)
    except ManagedOutputIncompleteError as error:
        return error.residual_paths
    except OSError:
        return (str((workspace / "out").absolute()),)

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
                if not _remove_tree(path, out_root):
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
                elif not _remove_tree(rollback, out_root):
                    residuals.add(str(rollback))
                else:
                    removed_any = True
            if removed_any:
                try:
                    _fsync_directory(parent, out_root)
                except OSError:
                    residuals.add(str(parent))
    return tuple(sorted(residuals, key=lambda value: value.encode("utf-8")))


def _candidate_cleanup(path: Path, out_root: Path) -> None:
    if _lstat(path) is not None and not _remove_tree(path, out_root):
        raise ManagedOutputIncompleteError((str(path),))


def _build_candidate(
    parent: Path,
    out_root: Path,
    graph: AssessmentExportGraph,
    members: dict[str, bytes],
    manifest: AssessmentManifest,
) -> Path:
    candidate = parent / (
        f".exp2res-candidate-{graph.snapshot.value.id}-{secrets.token_hex(16)}"
    )
    try:
        _mkdir_private(candidate, out_root)
    except BaseException:
        if _lstat(candidate) is not None and not _remove_tree(candidate, out_root):
            raise ManagedOutputIncompleteError((str(candidate),)) from None
        raise
    try:
        for name in sorted(_MEMBER_NAMES, key=id_key):
            _write_private_file(candidate / name, members[name], out_root)
        _write_private_file(candidate / "manifest.json", manifest_bytes(manifest), out_root)
        if _inspect_set(candidate, parent, out_root) != manifest:
            raise IntegrityFailureError("candidate_manifest_validation_failed")
        _fsync_directory(candidate, out_root)
        _fsync_directory(parent, out_root)
        return candidate
    except BaseException:
        _candidate_cleanup(candidate, out_root)
        raise


def _matching_current_manifest(
    final_path: Path,
    parent: Path,
    out_root: Path,
    graph: AssessmentExportGraph,
) -> AssessmentManifest | None:
    manifest = _inspect_set(final_path, parent, out_root)
    if manifest is None:
        return None
    snapshot = graph.snapshot.value
    if (
        manifest.entity_id != snapshot.id
        or manifest.generation_id != graph.snapshot.generation_id
        or manifest.produced_by_run_id != graph.snapshot.produced_by_run_id
        or manifest.identity
        != AssessmentIdentity(
            snapshot_title=snapshot.title,
            scope=snapshot.scope,
            scope_target=snapshot.scope_target,
        )
        or manifest.source_ids != AssessmentSourceIds(**graph.source_ids())
        or manifest.render_input_sha256 != render_input_sha256(graph)
    ):
        return None
    return manifest


def _matching_prior_manifest(
    final_path: Path,
    parent: Path,
    out_root: Path,
    graph: AssessmentExportGraph,
) -> AssessmentManifest | None:
    """Validate a prior set while allowing its lifecycle-sensitive hash to be stale."""

    manifest = _inspect_set(final_path, parent, out_root)
    if manifest is None:
        return None
    snapshot = graph.snapshot.value
    if (
        manifest.entity_id != snapshot.id
        or manifest.generation_id != graph.snapshot.generation_id
        or manifest.produced_by_run_id != graph.snapshot.produced_by_run_id
        or manifest.identity
        != AssessmentIdentity(
            snapshot_title=snapshot.title,
            scope=snapshot.scope,
            scope_target=snapshot.scope_target,
        )
        or manifest.source_ids != AssessmentSourceIds(**graph.source_ids())
    ):
        return None
    return manifest


def _remove_stale_same_view(
    parent: Path,
    out_root: Path,
    candidate_manifest: AssessmentManifest,
) -> None:
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
        prior = _inspect_set(path, parent, out_root)
        if prior is None or not _same_view(prior.identity, candidate_manifest.identity):
            continue
        if not _remove_tree(path, out_root):
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
) -> bool:
    try:
        return all(
            _read_regular(final_path / name, out_root) == members[name]
            for name in _MEMBER_NAMES
        )
    except OSError:
        return False


def publish_assessment(
    workspace: Path,
    graph: AssessmentExportGraph,
    *,
    clock=None,
) -> tuple[AssessmentManifest, tuple[str, ...]]:
    """Publish and revalidate one complete assessment set under §13.14."""

    validate_entity_id(graph.snapshot.value.id)
    out_root, parent, _branch = _ensure_managed_parents(workspace)
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    members = assessment_member_bytes(graph)
    candidate_manifest = build_assessment_manifest(graph, members, created_at=now)

    _remove_stale_same_view(parent, out_root, candidate_manifest)
    candidate = _build_candidate(parent, out_root, graph, members, candidate_manifest)
    final_path = parent / graph.snapshot.value.id
    rollback: Path | None = None
    published = False
    try:
        prior_manifest: AssessmentManifest | None = None
        if _lstat(final_path) is not None:
            prior_manifest = _matching_prior_manifest(
                final_path, parent, out_root, graph
            )
            if prior_manifest is None:
                raise ManagedOutputIncompleteError((str(final_path),))
            if (
                prior_manifest.render_input_sha256
                == candidate_manifest.render_input_sha256
                and _member_bytes_equal(final_path, out_root, members)
            ):
                _candidate_cleanup(candidate, out_root)
                try:
                    _fsync_directory(parent, out_root)
                except OSError as error:
                    raise ManagedOutputIncompleteError((str(parent),)) from error
                manifest = prior_manifest
                paths = tuple(
                    str(final_path / name)
                    for name in sorted(_ALL_NAMES, key=id_key)
                )
                return manifest, paths

            # §13.14 rule 5 portable fallback: native directory exchange is
            # deliberately unavailable in V1.
            rollback = parent / (
                f".exp2res-rollback-{graph.snapshot.value.id}-{secrets.token_hex(16)}"
            )
            try:
                _rename(final_path, rollback)
                _fsync_directory(parent, out_root)
            except BaseException:
                if _lstat(rollback) is not None and _lstat(final_path) is None:
                    try:
                        _rename(rollback, final_path)
                        _fsync_directory(parent, out_root)
                        rollback = None
                    except BaseException:
                        raise ManagedOutputIncompleteError((str(rollback),)) from None
                raise
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
                    if _lstat(candidate) is not None and not _remove_tree(candidate, out_root):
                        residuals.append(str(candidate))
                    raise ManagedOutputIncompleteError(residuals) from None
            raise

        try:
            _fsync_directory(parent, out_root)
            if rollback is not None:
                if not _remove_tree(rollback, out_root):
                    raise ManagedOutputIncompleteError((str(rollback),))
                rollback = None
            _fsync_directory(parent, out_root)
        except ManagedOutputIncompleteError:
            raise
        except OSError as error:
            residual = str(rollback) if rollback is not None else str(final_path)
            raise ManagedOutputIncompleteError((residual,)) from error

        current = _matching_current_manifest(final_path, parent, out_root, graph)
        if current is None or current.render_input_sha256 != render_input_sha256(graph):
            raise ManagedOutputIncompleteError((str(final_path),))
        paths = tuple(
            str(final_path / name) for name in sorted(_ALL_NAMES, key=id_key)
        )
        return current, paths
    except BaseException:
        if not published and _lstat(candidate) is not None:
            _candidate_cleanup(candidate, out_root)
        raise
