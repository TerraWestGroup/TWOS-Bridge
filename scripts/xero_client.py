"""
Xero API client using a Custom Connection (client-credentials OAuth2,
no user login required -- same shape as the Microsoft Graph app-only
flow in graph_client.py).

Custom Connections are still bound to a single Xero organisation, but
API calls still require an Xero-tenant-id header. That tenant id is
discovered once via GET /connections after obtaining a token.
"""

import json
import base64
import urllib.parse
import urllib.request
import urllib.error

TOKEN_URL = "https://identity.xero.com/connect/token"
API_BASE = "https://api.xero.com"

SCOPES = "accounting.reports.read accounting.transactions.read accounting.settings.read"


def get_access_token(client_id, client_secret):
    credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    data = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "scope": SCOPES,
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=data, method="POST")
    req.add_header("Authorization", f"Basic {credentials}")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req) as resp:
        body = json.loads(resp.read())
        return body["access_token"]


def get_tenant_id(access_token):
    req = urllib.request.Request(f"{API_BASE}/connections", method="GET")
    req.add_header("Authorization", f"Bearer {access_token}")
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req) as resp:
        connections = json.loads(resp.read())
    if not connections:
        raise RuntimeError("No Xero connections found for this Custom Connection -- has it been authorised?")
    return connections[0]["tenantId"]


def xero_get(access_token, tenant_id, path, params=None):
    url = f"{API_BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {access_token}")
    req.add_header("Xero-tenant-id", tenant_id)
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())
