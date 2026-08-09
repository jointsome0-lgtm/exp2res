"""§19.3 GitHub commit importer; one local payload, never a network call."""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Literal, Mapping, Optional

from pydantic import field_validator, model_validator

from exp2res.domain.enums import OwnerAttribution
from exp2res.domain.models import (
    OccurredAt,
    StrictModel,
    validate_free_text,
    validate_structural,
)
from exp2res.errors import InvalidInputError
from exp2res.integrations.records import (
    EvidencePlan,
    ImportPlan,
    PlanContext,
    RecordRejected,
    SourceContract,
    SourceRecord,
)
from exp2res.services.source_files import validate_remote_locator

COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
# §19.3 calls `repo` the `owner/name` repository identity, and the derived
# `<repo>@<commit_sha>` is the record's whole idempotency key. Only that stated
# two-segment shape is enforced: what characters a host allows inside a segment
# is the adapter's business, and §19.3 normalizes nothing.
REPO_IDENTITY = re.compile(r"^[^/]+/[^/]+$")
MAX_LIST_ITEMS = 1_000
EVIDENCE_SUMMARY = "Imported repository commit."


class CommitIdentity(StrictModel):
    """Inert upstream provenance; never a locally verified identity."""

    name: Optional[str] = None
    email: Optional[str] = None
    login: Optional[str] = None

    @field_validator("name", "email", "login")
    @classmethod
    def structural_fields(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else validate_structural(value)


class GithubRecord(SourceRecord):
    """§19.3's complete accepted commit record."""

    source: Literal["github"]
    repo: str
    commit_sha: str
    message: str
    files: list[str]
    url: str
    author: CommitIdentity
    committer: CommitIdentity
    authored_at: datetime
    committed_at: datetime
    # Omission materializes the conservative value before §19.4 canonical
    # serialization, so omission and an explicit `unknown` hash alike.
    owner_attribution: OwnerAttribution = "unknown"

    @field_validator("url")
    @classmethod
    def structural_fields(cls, value: str) -> str:
        return validate_structural(value)

    @field_validator("repo")
    @classmethod
    def owner_and_name(cls, value: str) -> str:
        validate_structural(value)
        if REPO_IDENTITY.fullmatch(value) is None:
            raise ValueError("repo must be an owner/name repository identity")
        return value

    @field_validator("commit_sha")
    @classmethod
    def full_lowercase_sha(cls, value: str) -> str:
        if COMMIT_SHA.fullmatch(value) is None:
            raise ValueError("commit_sha must be 40 lowercase hexadecimal characters")
        return value

    @field_validator("message")
    @classmethod
    def source_voice(cls, value: str) -> str:
        # §13.1 rule 1 requires non-empty raw text, and this is the record's
        # only source voice.
        return validate_free_text(value, raw=True, nonempty=True)

    @field_validator("files")
    @classmethod
    def source_reported_files(cls, value: list[str]) -> list[str]:
        if len(value) > MAX_LIST_ITEMS:
            raise ValueError("files exceeds the §11 item limit")
        for member in value:
            validate_structural(member)
        return value

    @field_validator("authored_at", "committed_at")
    @classmethod
    def upstream_times_are_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("upstream times must carry an offset")
        return value

    @model_validator(mode="after")
    def derived_identity_is_structural(self) -> "GithubRecord":
        # §19.3 derives the identity from two accepted fields, so the §11 rule
        # 30 bound on the identity itself is only checkable once both hold: a
        # `repo` just under the structural limit still concatenates past it,
        # and that value is what the record persists and reports.
        validate_structural(self.source_identity)
        return self

    @property
    def source_identity(self) -> str:
        """§19.3's derived identity: no adapter value supplies or overrides it."""

        return f"{self.repo}@{self.commit_sha}"


def raw_identity(raw: Mapping[str, Any]) -> Optional[str]:
    repo = raw.get("repo")
    commit_sha = raw.get("commit_sha")
    if not isinstance(repo, str) or not isinstance(commit_sha, str):
        return None
    if COMMIT_SHA.fullmatch(commit_sha) is None:
        return None
    if REPO_IDENTITY.fullmatch(repo) is None:
        return None
    identity = f"{repo}@{commit_sha}"
    # The derived string is what a rejected record reports, so it is the one
    # held to §11's structural bound: `repo` alone can pass it and still
    # concatenate past it.
    try:
        validate_structural(identity)
    except (UnicodeError, ValueError, TypeError):
        return None
    return identity


def check(record: GithubRecord, raw: Mapping[str, Any]) -> None:
    """Hold `url` to §29.4's remote-locator form before persisting it.

    §19.3 makes `url` inert provenance under §29.4, and §13.1 rule 4 keeps an
    imported artifact's source URI. A persisted locator is re-validated
    before every §15 serialization, so a value that cannot pass that check
    fails here rather than failing a later stage closed.
    """

    try:
        validate_remote_locator(record.url)
    except InvalidInputError as error:
        raise RecordRejected("github_url_not_remote") from error


def plan(record: GithubRecord, context: PlanContext) -> ImportPlan:
    # Only `owner` reaches commit_or_pr; the mapping establishes evidential
    # scope alone and never supplies an OwnershipLevel (§16.4).
    strength = (
        "commit_or_pr"
        if record.owner_attribution == "owner"
        else "artifact_reference"
    )
    return ImportPlan(
        entry_type="github_commit",
        source_type="imported_artifact",
        occurred=OccurredAt(
            start=record.committed_at,
            end=None,
            precision="exact_datetime",
            confidence="high",
        ),
        raw_text=record.message,
        external_ref=record.url,
        evidence=(
            EvidencePlan(
                summary=EVIDENCE_SUMMARY,
                strength=strength,
                uri=record.url,
            ),
        ),
    )


CONTRACT = SourceContract(
    source_system="github",
    record_model=GithubRecord,
    multi_record=False,
    raw_identity=raw_identity,
    plan=plan,
    check=check,
)
