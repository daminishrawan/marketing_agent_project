## Summary

Restores the security boundary between the approval service (holds HMAC_SECRET_KEY) and publisher (verifies via HTTP). Removes dotenv loading from library modules.

## Changes

### Security boundary restoration (`src/mm/publisher.py`)
- Production now uses only `HttpVerifier` — calls approval service over TLS
- Removed `LocalVerifier` class and `PUBLISHER_USE_LOCAL_VERIFIER` env var from production
- Production imports only `CampaignPayload`, `canonical_json`, `payload_hash` from approval — NOT `verify_approval` or `issue_approval`
- The approval service remains the only process holding `HMAC_SECRET_KEY` in production

### Remove dotenv from library modules
- `src/mm/embed.py`: removed `from dotenv import load_dotenv` and `load_dotenv()` call
- `src/mm/index.py`: removed `from dotenv import load_dotenv` and `load_dotenv()` call
- Entry points (CLI scripts) should handle env loading

### Test helper (`tests/publisher_helpers.py`)
- New file containing `LocalVerifier` and `verify_approval` import for local testing only
- Tests use this helper instead of production module for local verification

### Updated tests (`tests/test_publisher.py`)
- Uses test helper for local verification
- AST test `test_publisher_holds_no_secret_and_no_issuer` now passes

### .gitignore
- Added `.env` to prevent accidental commits of local secrets

## Testing

All 225 tests pass.