"""
Test script: authenticate to Microsoft Graph using the app-only client
credentials flow, then list recent messages in the mailbox's
'Mechanic Desk Reports' folder (under Inbox) to confirm the
GRAPH_TENANT_ID / GRAPH_CLIENT_ID / GRAPH_CLIENT_SECRET / MAILBOX_ADDRESS
secrets are correctly wired and the Mail.Read application permission
is actually working.

This script does not write to twos-state.json or download attachments;
it only proves the connection and prints what it finds, for verification
before building the real ingestion pipeline on top of it.
"""

import os
import sys
import json
import urllib.request
import urllib.parse
import urllib.error


def get_env(name):
    value = os.environ.get(name)
    if not value:
        print(f"ERROR: missing required environment variable {name}", file=sys.stderr)
        sys.exit(1)
    return value


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
    try:
        with urllib.request.urlopen(req) as resp:
            body = json.loads(resp.read())
            return body["access_token"]
    except urllib.error.HTTPError as e:
        print("Token request failed:", e.read().decode(), file=sys.stderr)
        sys.exit(1)


def graph_get(token, url):
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"Graph GET failed for {url}:", e.read().decode(), file=sys.stderr)
        sys.exit(1)


def main():
    tenant_id = get_env("GRAPH_TENANT_ID")
    client_id = get_env("GRAPH_CLIENT_ID")
    client_secret = get_env("GRAPH_CLIENT_SECRET")
    mailbox = get_env("MAILBOX_ADDRESS")

    print(f"Authenticating for mailbox: {mailbox}")
    token = get_access_token(tenant_id, client_id, client_secret)
    print("Token acquired successfully.")

    # Find the "Mechanic Desk Reports" child folder under Inbox
    base = f"https://graph.microsoft.com/v1.0/users/{urllib.parse.quote(mailbox)}"
    inbox_children = graph_get(token, f"{base}/mailFolders/Inbox/childFolders?$top=50")
    folder = next(
        (f for f in inbox_children.get("value", []) if f["displayName"] == "Mechanic Desk Reports"),
        None,
    )
    if not folder:
        print("Inbox child folders found:", [f["displayName"] for f in inbox_children.get("value", [])])
        print("ERROR: 'Mechanic Desk Reports' folder not found under Inbox.", file=sys.stderr)
        sys.exit(1)

    print(f"Found folder: {folder['displayName']} (id={folder['id']}, totalItemCount={folder.get('totalItemCount')})")

    messages = graph_get(
        token,
        f"{base}/mailFolders/{folder['id']}/messages"
        f"?$top=10&$orderby=receivedDateTime desc&$select=subject,receivedDateTime,hasAttachments",
    )

    print(f"\nMost recent {len(messages.get('value', []))} messages in Mechanic Desk Reports:")
    for m in messages.get("value", []):
        print(f"  - {m['receivedDateTime']} | attachments={m['hasAttachments']} | {m['subject']}")

    print("\nCONNECTION TEST: SUCCESS")


if __name__ == "__main__":
    main()
