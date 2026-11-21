"""Approval service: the only process that holds HMAC_SECRET_KEY.

POST /approve  a reviewer issues a token for one exact payload (reviewer credential, audited)
POST /verify   publisher asks whether a token is valid for a payload (publisher credential)

Served over TLS only. Credentials are role-scoped, expiring and revocable (see src/mm/credentials.py).
Expiry of approval tokens is judged by this service's clock, not the caller's.
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, ValidationError

from src.mm.approval import ApprovalGrant, CampaignPayload, issue_approval, verify_approval
from src.mm.credentials import CredentialStore
from src.mm.ingest import ROOT
from src.mm.models import ErrorPayload

DEFAULT_AUDIT_PATH = ROOT / "storage" / "approval_audit.jsonl"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8801


class VerifyRequest(BaseModel):
    payload: CampaignPayload
    approval_token: str
    payload_hash: str


class ApproveRequest(BaseModel):
    payload: CampaignPayload


class ApprovalService:
    def __init__(self, secret: str, clock: Callable[[], float] = time.time,
                 audit_path: Path | str = DEFAULT_AUDIT_PATH, ttl: int | None = None) -> None:
        if not secret:
            raise ValueError("HMAC secret is required (set HMAC_SECRET_KEY)")
        self._secret = secret
        self._clock = clock
        self._audit_path = Path(audit_path)
        self._ttl = ttl
        self._lock = threading.Lock()

    def approve(self, payload: CampaignPayload, reviewer_id: str, credential_id: str) -> ApprovalGrant:
        if not reviewer_id.strip():
            raise ValueError("reviewer_id is required")
        grant = issue_approval(payload, self._secret, int(self._clock()), self._ttl)
        self._audit({
            "event": "approved",
            "reviewer_id": reviewer_id,
            "credential_id": credential_id,
            "company_id": payload.company_id,
            "campaign_id": payload.campaign_id,
            "payload_hash": grant.payload_hash,
            "expires_at": grant.expires_at,
            "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
        return grant

    def verify(self, payload: CampaignPayload, approval_token: str, claimed_hash: str) -> ErrorPayload | None:
        return verify_approval(payload, approval_token, claimed_hash, self._secret, int(self._clock()))

    def _audit(self, event: dict) -> None:
        self._audit_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event, sort_keys=True)
        with self._lock, self._audit_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def make_handler(service: ApprovalService, credentials: CredentialStore) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", 0))
            try:
                data = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self._send(400, {"error": "invalid JSON"})

            auth = self.headers.get("Authorization", "")

            if self.path == "/approve":
                reviewer = credentials.authenticate("reviewer", auth)
                if reviewer is None:
                    return self._send(401, {"error": "reviewer authentication required"})
                try:
                    req = ApproveRequest.model_validate(data)
                    grant = service.approve(req.payload, reviewer.subject, reviewer.credential_id)
                except (ValidationError, ValueError) as exc:
                    return self._send(400, {"error": str(exc)})
                return self._send(200, grant.model_dump())

            if self.path == "/verify":
                if credentials.authenticate("publisher", auth) is None:
                    return self._send(401, {"error": "publisher authentication required"})
                try:
                    req = VerifyRequest.model_validate(data)
                except ValidationError as exc:
                    return self._send(400, {"error": str(exc)})
                error = service.verify(req.payload, req.approval_token, req.payload_hash)
                return self._send(200, {"valid": error is None,
                                        "error": error.model_dump() if error else None})

            return self._send(404, {"error": "unknown endpoint"})

        def log_message(self, format, *args) -> None:
            return

        def _send(self, status: int, body: dict) -> None:
            encoded = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    return Handler


def tls_context(cert_file: str | Path, key_file: str | Path) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile=str(cert_file), keyfile=str(key_file))
    return context


def serve(service: ApprovalService, credentials: CredentialStore, tls: ssl.SSLContext,
          host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(service, credentials))
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    return server


def main() -> None:
    secret = os.environ.get("HMAC_SECRET_KEY", "")
    if not secret:
        sys.exit("HMAC_SECRET_KEY is not set; refusing to start the approval service")
    credentials_file = os.environ.get("CREDENTIALS_FILE", "")
    if not credentials_file:
        sys.exit("CREDENTIALS_FILE is not set")
    cert = os.environ.get("APPROVAL_TLS_CERT", "")
    key = os.environ.get("APPROVAL_TLS_KEY", "")
    if not cert or not key:
        sys.exit("APPROVAL_TLS_CERT and APPROVAL_TLS_KEY are required; the service runs over TLS only")
    port = int(os.environ.get("APPROVAL_PORT", DEFAULT_PORT))
    serve(ApprovalService(secret), CredentialStore(credentials_file),
          tls_context(cert, key), port=port).serve_forever()


if __name__ == "__main__":
    main()
