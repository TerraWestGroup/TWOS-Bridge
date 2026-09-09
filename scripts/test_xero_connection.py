"""
Test script: authenticate to Xero via the Custom Connection client-
credentials flow, discover the tenant id, then pull a small sample of
data (organisation name, bank accounts, and a Balance Sheet summary)
to confirm the connection works and to see the real shape of the data
before building the real DN03 ingestion logic on top of it.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from xero_client import get_access_token, get_tenant_id, xero_get


def get_env(name):
    value = os.environ.get(name)
    if not value:
        print(f"ERROR: missing required environment variable {name}", file=sys.stderr)
        sys.exit(1)
    return value


def main():
    client_id = get_env("XERO_CLIENT_ID")
    client_secret = get_env("XERO_CLIENT_SECRET")

    print("Requesting access token...")
    token = get_access_token(client_id, client_secret)
    print("Token acquired successfully.")

    tenant_id = get_tenant_id(token)
    print(f"Tenant id: {tenant_id}")

    org = xero_get(token, tenant_id, "/api.xro/2.0/Organisation")
    org_name = org["Organisations"][0]["Name"]
    print(f"Organisation: {org_name}")

    accounts = xero_get(token, tenant_id, "/api.xro/2.0/Accounts", params={"where": 'Type=="BANK"'})
    print(f"\nBank accounts ({len(accounts.get('Accounts', []))}):")
    for acc in accounts.get("Accounts", []):
        print(f"  - {acc.get('Name')} | code={acc.get('Code')} | status={acc.get('Status')} | "
              f"bankAccountNumber={acc.get('BankAccountNumber')}")

    print("\nCONNECTION TEST: SUCCESS")


if __name__ == "__main__":
    main()
