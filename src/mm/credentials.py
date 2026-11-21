"""Bearer credentials with roles, expiry and revocation.

The file stores SHA-256 hashes only. Tokens are shown once, at issue time.
The file is reread on every authentication, so revocation takes effect without a restart.

CLI:
    python -m src.mm.credentials issue --file F --role reviewer --subject usr_1 --days 90
    python -m src.mm.credentials revoke --file F --id reviewer:usr_1:ab12cd34
    python -m src.mm.credentials list --file F
    python -m src.mm.credentials check-expiry --file F --within-days 14   (exit 2 if action needed)
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, Field

ROLES = ("reviewer", "publisher")


class CredentialRecord(BaseModel):
    credential_id: str
    role: str
    subject: str
    sha256: str
    expires_at: str
    revoked: bool = False


class CredentialFile(BaseModel):
    credentials: list[CredentialRecord] = Field(default_factory=list)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _load(path: Path) -> CredentialFile:
    if not path.exists():
        return CredentialFile()
    return CredentialFile.model_validate_json(path.read_text(encoding="utf-8"))


def _save(path: Path, data: CredentialFile) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data.model_dump(), indent=2), encoding="utf-8")
    os.replace(temp, path)


class CredentialStore:
    def __init__(self, path: Path | str, clock: Callable[[], float] = time.time) -> None:
        self._path = Path(path)
        self._clock = clock

    def authenticate(self, role: str, authorization_header: str) -> CredentialRecord | None:
        if not authorization_header.startswith("Bearer "):
            return None
        presented = hash_token(authorization_header[len("Bearer "):].strip())
        match = None
        for record in _load(self._path).credentials:
            if record.role == role and hmac.compare_digest(presented, record.sha256):
                match = record
        if match is None or match.revoked:
            return None
        expires = datetime.fromisoformat(match.expires_at).timestamp()
        if self._clock() >= expires:
            return None
        return match


def issue(path: Path | str, role: str, subject: str, ttl_days: int, now: datetime | None = None) -> tuple[str, CredentialRecord]:
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    if not subject.strip():
        raise ValueError("subject is required")
    if ttl_days <= 0:
        raise ValueError("ttl_days must be positive")
    path = Path(path)
    issued_at = now or datetime.now(timezone.utc)
    token = secrets.token_urlsafe(32)
    record = CredentialRecord(
        credential_id=f"{role}:{subject}:{secrets.token_hex(4)}",
        role=role,
        subject=subject,
        sha256=hash_token(token),
        expires_at=(issued_at + timedelta(days=ttl_days)).isoformat(timespec="seconds"),
    )
    data = _load(path)
    data.credentials.append(record)
    _save(path, data)
    return token, record


def revoke(path: Path | str, credential_id: str) -> bool:
    path = Path(path)
    data = _load(path)
    found = False
    for record in data.credentials:
        if record.credential_id == credential_id:
            record.revoked = True
            found = True
    if found:
        _save(path, data)
    return found


def expiry_report(path: Path | str, within_days: int, now: datetime | None = None) -> list[tuple[CredentialRecord, str]]:
    current = now or datetime.now(timezone.utc)
    horizon = current + timedelta(days=within_days)
    findings: list[tuple[CredentialRecord, str]] = []
    for record in _load(Path(path)).credentials:
        if record.revoked:
            continue
        expires = datetime.fromisoformat(record.expires_at)
        if expires <= current:
            findings.append((record, "EXPIRED"))
        elif expires <= horizon:
            findings.append((record, "EXPIRING"))
    return sorted(findings, key=lambda item: item[0].expires_at)


def _cli() -> None:
    parser = argparse.ArgumentParser(description="Manage approval-service credentials")
    sub = parser.add_subparsers(dest="command", required=True)
    issue_p = sub.add_parser("issue")
    issue_p.add_argument("--file", required=True)
    issue_p.add_argument("--role", required=True, choices=ROLES)
    issue_p.add_argument("--subject", required=True)
    issue_p.add_argument("--days", type=int, default=90)
    revoke_p = sub.add_parser("revoke")
    revoke_p.add_argument("--file", required=True)
    revoke_p.add_argument("--id", required=True)
    list_p = sub.add_parser("list")
    list_p.add_argument("--file", required=True)
    check_p = sub.add_parser("check-expiry", help="exit 2 if any active credential is expired or expiring")
    check_p.add_argument("--file", required=True)
    check_p.add_argument("--within-days", type=int, default=14)
    args = parser.parse_args()

    if args.command == "issue":
        token, record = issue(args.file, args.role, args.subject, args.days)
        print(f"credential_id: {record.credential_id}")
        print(f"expires_at:    {record.expires_at}")
        print(f"token (shown once): {token}")
    elif args.command == "revoke":
        print("revoked" if revoke(args.file, args.id) else "not found")
    elif args.command == "check-expiry":
        findings = expiry_report(args.file, args.within_days)
        for record, state in findings:
            print(f"{state}\t{record.credential_id}\t{record.role}\t{record.expires_at}")
        if not findings:
            print(f"ok: no active credential expires within {args.within_days} days")
        sys.exit(2 if findings else 0)
    else:
        for record in _load(Path(args.file)).credentials:
            state = "revoked" if record.revoked else "active"
            print(f"{record.credential_id}\t{record.role}\t{record.expires_at}\t{state}")


if __name__ == "__main__":
    _cli()
