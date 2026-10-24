import pytest

# `make test` exports .env. Tests never use the real services behind these: DATABASE_URL is the shared
# Neon log the deployment uses, and the rest would send live model, bank, identity and device calls
# (replay judges and syncs the bank whenever they're configured). Tests that want them set fakes.
SERVICES = [
    "DATABASE_URL", "ANTHROPIC_API_KEY", "TYPESAFE_API_KEY", "JEV_API_KEY", "AI_GATEWAY_API_KEY", "JEV_BASE_URL",
    "NESSIE_API_KEY", "NESSIE_ACCOUNT_ID", "NESSIE_BASE_URL", "CRS_USERNAME", "CRS_PASSWORD", "CRS_API_KEY",
    "CRS_BASE_URL", "DEVICE_URL", "APPROVAL_KEY_PUBKEY", "BLOB_READ_WRITE_TOKEN", "BLOB_STORE_ID",
    "UPLOAD_PASSCODE", "INBOUND_PASSWORD",
]


@pytest.fixture(autouse=True)
def _no_live_services(monkeypatch):
    for name in SERVICES:
        monkeypatch.delenv(name, raising=False)
