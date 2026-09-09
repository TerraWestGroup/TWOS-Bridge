"""
Shared Microsoft Graph client-credentials helper. Used by both the
connection test script and the real nightly ingestion script so the
auth logic lives in exactly one place.
"""

import json
import urllib.parse
import urllib.request
import urllib.error


def get_access_token(tenant_id, client_id, client_secret):
    url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    data = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": "https://graph.microsoft.com/.default",
        "grant_type": "client_credentials",
    }).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req) as resp:
        body = json.loads(resp.read())
        return body["access_token"]


def graph_get(token, url):
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def graph_get_all_pages(token, url, max_pages=10):
    """Follows @odata.nextLink up to max_pages, concatenating 'value' arrays."""
    results = []
    pages = 0
    next_url = url
    while next_url and pages < max_pages:
        page = graph_get(token, next_url)
        results.extend(page.get("value", []))
        next_url = page.get("@odata.nextLink")
        pages += 1
    return results
