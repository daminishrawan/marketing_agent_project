import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

from src.mm.credentials import CredentialStore, expiry_report, issue, revoke
from src.mm.ingest import ROOT

NOW = datetime(2026, 11, 21, tzinfo=timezone.utc)


class Clock:
    def __init__(self, when: datetime) -> None:
        self.when = when

    def __call__(self) -> float:
        return self.when.timestamp()


def test_token_is_stored_only_as_a_hash(tmp_path):
    path = tmp_path / "creds.json"
    token, record = issue(path, "reviewer", "usr_1", 90, now=NOW)
    text = path.read_text(encoding="utf-8")
    assert token not in text
    assert record.sha256 in text


def test_authenticate_matches_role_and_token(tmp_path):
    path = tmp_path / "creds.json"
    token, record = issue(path, "reviewer", "usr_1", 90, now=NOW)
    store = CredentialStore(path, clock=Clock(NOW))
    assert store.authenticate("reviewer", f"Bearer {token}").credential_id == record.credential_id
    assert store.authenticate("publisher", f"Bearer {token}") is None
    assert store.authenticate("reviewer", "Bearer nope") is None
    assert store.authenticate("reviewer", token) is None


def test_revocation_takes_effect_on_the_next_check(tmp_path):
    path = tmp_path / "creds.json"
    token, record = issue(path, "publisher", "publisher_main", 90, now=NOW)
    store = CredentialStore(path, clock=Clock(NOW))
    assert store.authenticate("publisher", f"Bearer {token}") is not None
    assert revoke(path, record.credential_id) is True
    assert store.authenticate("publisher", f"Bearer {token}") is None


def test_expired_credential_is_refused(tmp_path):
    path = tmp_path / "creds.json"
    token, _ = issue(path, "reviewer", "usr_1", 30, now=NOW)
    later = NOW + timedelta(days=31)
    assert CredentialStore(path, clock=Clock(later)).authenticate("reviewer", f"Bearer {token}") is None


def test_credential_is_valid_right_up_to_expiry_boundary(tmp_path):
    path = tmp_path / "creds.json"
    token, _ = issue(path, "reviewer", "usr_1", 1, now=NOW)
    just_before = NOW + timedelta(days=1) - timedelta(seconds=1)
    assert CredentialStore(path, clock=Clock(just_before)).authenticate("reviewer", f"Bearer {token}") is not None


def test_rotation_keeps_the_new_token_working_while_old_is_revoked(tmp_path):
    path = tmp_path / "creds.json"
    old_token, old = issue(path, "reviewer", "usr_1", 90, now=NOW)
    new_token, _ = issue(path, "reviewer", "usr_1", 90, now=NOW)
    store = CredentialStore(path, clock=Clock(NOW))
    revoke(path, old.credential_id)
    assert store.authenticate("reviewer", f"Bearer {old_token}") is None
    assert store.authenticate("reviewer", f"Bearer {new_token}") is not None


def test_revoking_an_unknown_id_reports_not_found(tmp_path):
    path = tmp_path / "creds.json"
    issue(path, "reviewer", "usr_1", 90, now=NOW)
    assert revoke(path, "reviewer:nobody:0000") is False


def test_missing_file_authenticates_nobody(tmp_path):
    assert CredentialStore(tmp_path / "absent.json").authenticate("reviewer", "Bearer x") is None


@pytest.mark.parametrize("kwargs", [
    {"role": "admin", "subject": "x", "ttl_days": 1},
    {"role": "reviewer", "subject": "  ", "ttl_days": 1},
    {"role": "reviewer", "subject": "x", "ttl_days": 0},
])
def test_invalid_issue_arguments_are_refused(tmp_path, kwargs):
    with pytest.raises(ValueError):
        issue(tmp_path / "creds.json", now=NOW, **kwargs)


def test_expiry_report_flags_expiring_and_expired_but_not_revoked_or_distant(tmp_path):
    path = tmp_path / "creds.json"
    _, soon = issue(path, "reviewer", "usr_soon", 5, now=NOW)
    _, far = issue(path, "reviewer", "usr_far", 90, now=NOW)
    _, gone = issue(path, "publisher", "pub_old", 1, now=NOW - timedelta(days=10))
    _, revoked = issue(path, "reviewer", "usr_revoked", 5, now=NOW)
    revoke(path, revoked.credential_id)
    report = {record.credential_id: state for record, state in expiry_report(path, 14, now=NOW)}
    assert report == {soon.credential_id: "EXPIRING", gone.credential_id: "EXPIRED"}
    assert far.credential_id not in report
    assert revoked.credential_id not in report


def test_expiry_report_is_empty_when_nothing_is_near(tmp_path):
    path = tmp_path / "creds.json"
    issue(path, "reviewer", "usr_far", 90, now=NOW)
    assert expiry_report(path, 14, now=NOW) == []


def test_check_expiry_cli_exit_codes(tmp_path):
    path = tmp_path / "creds.json"
    issue(path, "reviewer", "usr_soon", 5)
    result = subprocess.run(
        [sys.executable, "-m", "src.mm.credentials", "check-expiry", "--file", str(path), "--within-days", "30"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert "EXPIRING" in result.stdout

    quiet = tmp_path / "quiet.json"
    issue(quiet, "reviewer", "usr_far", 365)
    ok = subprocess.run(
        [sys.executable, "-m", "src.mm.credentials", "check-expiry", "--file", str(quiet), "--within-days", "30"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert ok.returncode == 0


def test_cli_issues_lists_and_revokes(tmp_path):
    path = tmp_path / "creds.json"
    run = lambda *args: subprocess.run(
        [sys.executable, "-m", "src.mm.credentials", *args],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout
    issued = run("issue", "--file", str(path), "--role", "publisher", "--subject", "publisher_main")
    assert "token (shown once):" in issued
    credential_id = next(line.split(":", 1)[1].strip() for line in issued.splitlines()
                         if line.startswith("credential_id"))
    assert "active" in run("list", "--file", str(path))
    assert "revoked" in run("revoke", "--file", str(path), "--id", credential_id)
    assert "revoked" in run("list", "--file", str(path))
