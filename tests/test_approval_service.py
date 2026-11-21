import ipaddress
import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import pytest
import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from src.mm.approval import CampaignPayload
from src.mm.approval_service import ApprovalService, make_handler, serve, tls_context
from src.mm.credentials import CredentialStore, issue, revoke
from src.mm.publisher import HttpVerifier, publish
from src.mm.records import connect

B = "comp_pulsepeak_02"
SECRET = "service-secret-not-for-production"
NOW = 1_800_000_000
TTL = 3600


class Clock:
    def __init__(self, now: int) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _payload(**overrides) -> CampaignPayload:
    data = dict(
        company_id=B,
        campaign_id="camp_monsoon_01",
        campaign_name="Monsoon Restart",
        destination="mock_email_inbox",
        schedule="2026-12-01T09:00:00+05:30",
        email_subject="Monsoon Restart at PulsePeak",
        channel_copy={"sms": "Monsoon Restart: 20% off Core Monthly. Book a free trial.",
                      "email": "Train hard, recover smarter. Book a free trial session."},
    )
    data.update(overrides)
    return CampaignPayload(**data)


def _self_signed(directory) -> tuple[str, str]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path = directory / "cert.pem"
    key_path = directory / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                           serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    return str(cert_path), str(key_path)


@pytest.fixture(scope="module")
def tls_files(tmp_path_factory):
    return _self_signed(tmp_path_factory.mktemp("tls"))


@pytest.fixture()
def clock():
    return Clock(NOW)


@pytest.fixture()
def service(tmp_path, clock):
    return ApprovalService(SECRET, clock=clock, audit_path=tmp_path / "audit.jsonl", ttl=TTL)


@pytest.fixture()
def creds_path(tmp_path):
    return tmp_path / "credentials.json"


@pytest.fixture()
def tokens(creds_path):
    reviewer_token, reviewer = issue(creds_path, "reviewer", "usr_1", 90)
    publisher_token, publisher = issue(creds_path, "publisher", "publisher_main", 90)
    return {"reviewer": reviewer_token, "reviewer_id": reviewer.credential_id,
            "publisher": publisher_token, "publisher_id": publisher.credential_id}


@pytest.fixture()
def live(service, creds_path, tokens, tls_files):
    cert, key = tls_files
    server = serve(service, CredentialStore(creds_path), tls_context(cert, key), host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"https://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _post(base: str, path: str, body, headers: dict | None = None, verify=None) -> requests.Response:
    data = body if isinstance(body, (bytes, str)) else json.dumps(body)
    merged = {"Content-Type": "application/json", **(headers or {})}
    return requests.post(f"{base}{path}", data=data, headers=merged, timeout=5, verify=verify)


def _approve(base, payload, token, ca) -> requests.Response:
    return _post(base, "/approve", {"payload": payload.model_dump(mode="json")},
                 headers={"Authorization": f"Bearer {token}"}, verify=ca)


def _verify_http(base, payload, approval_token, claimed_hash, auth, ca) -> requests.Response:
    return _post(base, "/verify",
                 {"payload": payload.model_dump(mode="json"), "approval_token": approval_token,
                  "payload_hash": claimed_hash},
                 headers={"Authorization": f"Bearer {auth}"}, verify=ca)


# service logic

def test_service_refuses_to_start_without_secret(tmp_path):
    with pytest.raises(ValueError):
        ApprovalService("", audit_path=tmp_path / "a.jsonl")


def test_approve_requires_a_reviewer(service):
    with pytest.raises(ValueError):
        service.approve(_payload(), reviewer_id="  ", credential_id="x")


def test_approval_is_audited_with_credential_and_without_token_or_secret(service, tmp_path):
    grant = service.approve(_payload(), reviewer_id="usr_1", credential_id="reviewer:usr_1:abcd")
    line = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()[-1]
    record = json.loads(line)
    assert record["credential_id"] == "reviewer:usr_1:abcd"
    assert record["payload_hash"] == grant.payload_hash
    assert grant.approval_token not in line
    assert SECRET not in line


def test_service_judges_token_expiry_with_its_own_clock(service, clock):
    grant = service.approve(_payload(), reviewer_id="usr_1", credential_id="c")
    assert service.verify(_payload(), grant.approval_token, grant.payload_hash) is None
    clock.now = NOW + TTL + 1
    assert service.verify(_payload(), grant.approval_token, grant.payload_hash).error_code == "APPROVAL_EXPIRED"


def test_service_requires_tls_files_to_build_a_context(tls_files):
    cert, key = tls_files
    assert tls_context(cert, key).minimum_version.name == "TLSv1_2"


# HTTPS surface

def test_https_approve_then_verify_round_trip(live, tokens, tls_files):
    ca = tls_files[0]
    payload = _payload()
    grant = _approve(live, payload, tokens["reviewer"], ca).json()
    verified = _verify_http(live, payload, grant["approval_token"], grant["payload_hash"], tokens["publisher"], ca)
    assert verified.json() == {"valid": True, "error": None}


def test_https_verify_rejects_edited_payload(live, tokens, tls_files):
    ca = tls_files[0]
    grant = _approve(live, _payload(), tokens["reviewer"], ca).json()
    edited = _payload(schedule="2026-12-02T09:00:00+05:30")
    body = _verify_http(live, edited, grant["approval_token"], grant["payload_hash"], tokens["publisher"], ca).json()
    assert body["valid"] is False
    assert body["error"]["error_code"] == "APPROVAL_HASH_MISMATCH"


def test_plain_http_to_the_tls_port_is_refused(live):
    with pytest.raises(requests.RequestException):
        requests.post(live.replace("https://", "http://") + "/approve", json={}, timeout=5)


def test_approve_without_a_token_is_a_401(live, tls_files):
    response = _post(live, "/approve", {"payload": _payload().model_dump(mode="json")}, verify=tls_files[0])
    assert response.status_code == 401


def test_approve_with_a_wrong_token_is_a_401(live, tls_files):
    assert _approve(live, _payload(), "guessed", tls_files[0]).status_code == 401


def test_publisher_credential_cannot_approve(live, tokens, tls_files):
    assert _approve(live, _payload(), tokens["publisher"], tls_files[0]).status_code == 401


def test_reviewer_credential_cannot_verify(live, tokens, tls_files):
    ca = tls_files[0]
    payload = _payload()
    grant = _approve(live, payload, tokens["reviewer"], ca).json()
    assert _verify_http(live, payload, grant["approval_token"], grant["payload_hash"],
                        tokens["reviewer"], ca).status_code == 401


def test_verify_without_a_token_is_a_401(live, tls_files):
    response = _post(live, "/verify", {"payload": _payload().model_dump(mode="json"),
                                       "approval_token": "x", "payload_hash": "0" * 64},
                     verify=tls_files[0])
    assert response.status_code == 401


def test_approval_records_the_credential_owner_not_the_body(live, tokens, tls_files, tmp_path):
    body = {"payload": _payload().model_dump(mode="json"), "reviewer_id": "usr_someone_else"}
    response = _post(live, "/approve", body, headers={"Authorization": f"Bearer {tokens['reviewer']}"},
                     verify=tls_files[0])
    assert response.status_code == 200
    record = json.loads((tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert record["reviewer_id"] == "usr_1"
    assert record["credential_id"] == tokens["reviewer_id"]


def test_invalid_json_is_a_400(live, tokens, tls_files):
    response = _post(live, "/verify", "{not json", headers={"Authorization": f"Bearer {tokens['publisher']}"},
                     verify=tls_files[0])
    assert response.status_code == 400


def test_unknown_path_is_a_404(live, tokens, tls_files):
    response = _post(live, "/issue", {}, headers={"Authorization": f"Bearer {tokens['publisher']}"},
                     verify=tls_files[0])
    assert response.status_code == 404


# rotation and revocation, without restarting the service

def test_rotated_reviewer_credential_works_and_the_old_one_is_revoked_live(live, creds_path, tokens, tls_files):
    ca = tls_files[0]
    new_token, _ = issue(creds_path, "reviewer", "usr_1", 90)
    assert _approve(live, _payload(), tokens["reviewer"], ca).status_code == 200
    assert _approve(live, _payload(), new_token, ca).status_code == 200
    assert revoke(creds_path, tokens["reviewer_id"]) is True
    assert _approve(live, _payload(), tokens["reviewer"], ca).status_code == 401
    assert _approve(live, _payload(), new_token, ca).status_code == 200


def test_revoked_publisher_credential_fails_closed_live(live, creds_path, tokens, tls_files):
    ca = tls_files[0]
    verifier = HttpVerifier(live, tokens["publisher"], ca_bundle=ca)
    payload = _payload()
    grant = _approve(live, payload, tokens["reviewer"], ca).json()
    assert verifier(payload, grant["approval_token"], grant["payload_hash"]) is None
    revoke(creds_path, tokens["publisher_id"])
    error = verifier(payload, grant["approval_token"], grant["payload_hash"])
    assert error.error_code == "APPROVAL_SERVICE_UNAVAILABLE"


def test_expired_reviewer_credential_is_refused(live, creds_path, tls_files):
    ca = tls_files[0]
    old = datetime(2020, 1, 1, tzinfo=timezone.utc)
    expired_token, _ = issue(creds_path, "reviewer", "usr_old", 1, now=old)
    assert _approve(live, _payload(), expired_token, ca).status_code == 401


# publisher side

def test_publish_dispatches_through_the_live_service(live, tokens, tls_files, tmp_path):
    ca = tls_files[0]
    payload = _payload()
    grant = _approve(live, payload, tokens["reviewer"], ca).json()
    conn = connect(tmp_path / "records.db")
    out = publish(conn, B, payload, grant["approval_token"], grant["payload_hash"], "idem-live",
                  verify=HttpVerifier(live, tokens["publisher"], ca_bundle=ca))
    assert out.error is None
    assert conn.execute("SELECT COUNT(*) FROM campaign_dispatches").fetchone()[0] == 2
    conn.close()


def test_publish_fails_closed_with_a_wrong_publisher_token(live, tokens, tls_files, tmp_path):
    ca = tls_files[0]
    payload = _payload()
    grant = _approve(live, payload, tokens["reviewer"], ca).json()
    conn = connect(tmp_path / "records.db")
    out = publish(conn, B, payload, grant["approval_token"], grant["payload_hash"], "idem-badauth",
                  verify=HttpVerifier(live, "not-the-publisher-token", ca_bundle=ca))
    assert out.error.error_code == "APPROVAL_SERVICE_UNAVAILABLE"
    assert conn.execute("SELECT COUNT(*) FROM campaign_dispatches").fetchone()[0] == 0
    conn.close()


def test_publish_fails_closed_when_the_service_is_unreachable(tmp_path):
    payload = _payload()
    conn = connect(tmp_path / "records.db")
    out = publish(conn, B, payload, "1800003600.deadbeef", "0" * 64, "idem-down",
                  verify=HttpVerifier("https://127.0.0.1:9", "token", timeout=1))
    assert out.error.error_code == "APPROVAL_SERVICE_UNAVAILABLE"
    assert conn.execute("SELECT COUNT(*) FROM campaign_dispatches").fetchone()[0] == 0
    conn.close()


def test_publisher_refuses_an_untrusted_certificate(live, tokens):
    verifier = HttpVerifier(live, tokens["publisher"])
    error = verifier(_payload(), "1800003600.deadbeef", "0" * 64)
    assert error.error_code == "APPROVAL_SERVICE_UNAVAILABLE"


def test_http_verifier_requires_https_and_a_token():
    with pytest.raises(ValueError):
        HttpVerifier("http://127.0.0.1:8801", "token")
    with pytest.raises(ValueError):
        HttpVerifier("https://127.0.0.1:8801", "")
