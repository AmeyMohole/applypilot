"""Gmail API: auth, draft creation, thread lookup, reply check, send.

The google libraries are imported lazily so the parser and tests run without them.
"""
from __future__ import annotations

import base64
import mimetypes
import os
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path

import config
import replies

SCOPES = [
    "https://www.googleapis.com/auth/gmail.compose",   # create drafts, send drafts we created
    "https://www.googleapis.com/auth/gmail.readonly",  # reply check + original thread lookup
]


API_RETRIES = 5     # googleapiclient backs off exponentially between tries


def _retrying_request():
    """A request class whose execute() retries 429 / 5xx / connection errors instead of failing the run
    at the first blip (an unattended run has nobody to rerun it)."""
    from googleapiclient.http import HttpRequest

    class RetryingRequest(HttpRequest):
        def execute(self, http=None, num_retries=0):
            return super().execute(http=http, num_retries=num_retries or API_RETRIES)
    return RetryingRequest


def get_service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if config.TOKEN.exists():
        creds = Credentials.from_authorized_user_file(str(config.TOKEN), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if os.environ.get("JOBSCAN_NONINTERACTIVE"):  # unattended runs must never pop a browser
                raise SystemExit("Gmail sign-in has expired. Run once by hand: python 2-apply/send.py")
            if not config.CREDENTIALS.exists():
                raise SystemExit(f"Missing {config.CREDENTIALS}. See SETUP.md, step 2.")
            flow = InstalledAppFlow.from_client_secrets_file(str(config.CREDENTIALS), SCOPES)
            creds = flow.run_local_server(port=0, login_hint=config.GMAIL_ACCOUNT)
        config.TOKEN.write_text(creds.to_json())
    svc = build("gmail", "v1", credentials=creds, requestBuilder=_retrying_request(), cache_discovery=False)
    who = svc.users().getProfile(userId="me").execute()["emailAddress"].lower()
    if who != config.GMAIL_ACCOUNT.lower():
        raise SystemExit(f"Signed in as {who}, but drafts must come from {config.GMAIL_ACCOUNT}. "
                         f"Delete {config.TOKEN} and sign in with the right account.")
    return svc


def build_raw(to: str, subject: str, text: str, html: str, attachments: list[Path] | None = None,
              in_reply_to: str = "") -> str:
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to  # clients thread on References, some servers on
        msg["References"] = in_reply_to   # In-Reply-To: set both
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    for path in attachments or []:
        path = Path(path)
        if not path.exists():
            raise SystemExit(f"Attachment not found: {path}")
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        main, sub = ctype.split("/", 1)
        msg.add_attachment(path.read_bytes(), maintype=main, subtype=sub, filename=path.name)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


def create_draft(svc, to: str, subject: str, text: str, html: str,
                 attachments: list[Path] | None = None, thread_id: str = "",
                 in_reply_to: str = "") -> str:
    message = {"raw": build_raw(to, subject, text, html, attachments, in_reply_to)}
    if thread_id:
        message["threadId"] = thread_id
    return svc.users().drafts().create(userId="me", body={"message": message}).execute()["id"]


def find_thread(svc, address: str) -> dict | None:
    """The oldest sent message to this address is the original application."""
    ids, token = [], None
    while True:
        resp = svc.users().messages().list(userId="me", q=f"in:sent to:{address}",
                                           pageToken=token).execute()
        ids += [m["id"] for m in resp.get("messages", [])]
        token = resp.get("nextPageToken")
        if not token:
            break
    if not ids:
        return None
    msg = svc.users().messages().get(userId="me", id=ids[-1], format="metadata",
                                     metadataHeaders=["Message-ID", "Subject"]).execute()
    hdr = {h["name"].lower(): h["value"] for h in msg["payload"]["headers"]}
    return {"thread_id": msg["threadId"], "message_id": hdr.get("message-id", ""),
            "subject": hdr.get("subject", "")}


def recent_messages(svc, query: str, limit: int = 5) -> list[dict]:
    """The newest messages matching a Gmail query: {sender, subject, snippet, date, thread_id, ms}."""
    resp = svc.users().messages().list(userId="me", q=query, maxResults=limit).execute()
    out = []
    for item in resp.get("messages", []):
        m = svc.users().messages().get(userId="me", id=item["id"], format="metadata",
                                       metadataHeaders=["From", "Subject", "Date"]).execute()
        hdr = {h["name"].lower(): h["value"] for h in m["payload"]["headers"]}
        out.append({"sender": parseaddr(hdr.get("from", ""))[1].lower(), "subject": hdr.get("subject", ""),
                    "date": hdr.get("date", ""), "snippet": " ".join(m.get("snippet", "").split())[:300],
                    "thread_id": m.get("threadId"), "ms": int(m.get("internalDate", "0"))})
    return out


def latest_reply(svc, address: str, after: float | None = None) -> dict | None:
    """The newest message from this address in the last 60 days that is a person answering, as
    {subject, date, snippet, outcome}. Receipts, out-of-office mail, broadcasts, bounces and
    "I have left" notices are not answers. `after` (epoch seconds) ignores older mail."""
    if not address:
        raise ValueError("empty address would search the whole inbox")
    query = f"from:{address} newer_than:60d" + (f" after:{int(after)}" if after else "")
    for m in recent_messages(svc, query):
        verdict = replies.classify(m["sender"], m["subject"], m["snippet"], config.GMAIL_ACCOUNT)
        if verdict.is_reply:
            return {"subject": m["subject"], "date": m["date"], "snippet": m["snippet"],
                    "outcome": verdict.outcome}
    return None


def has_replied(svc, address: str) -> bool:
    return latest_reply(svc, address) is not None


def list_drafts(svc) -> list[dict]:
    out, token = [], None
    while True:
        resp = svc.users().drafts().list(userId="me", pageToken=token).execute()
        for d in resp.get("drafts", []):
            m = svc.users().drafts().get(userId="me", id=d["id"], format="metadata").execute()
            hdr = {h["name"].lower(): h["value"] for h in m["message"]["payload"]["headers"]}
            out.append({"id": d["id"], "to": parseaddr(hdr.get("to", ""))[1].lower(),
                        "subject": hdr.get("subject", "")})
        token = resp.get("nextPageToken")
        if not token:
            return out


def send_draft(svc, draft_id: str) -> dict:
    """Send a draft. Returns Gmail's message resource ({id, threadId, ...})."""
    return svc.users().drafts().send(userId="me", body={"id": draft_id}).execute()


def delete_draft(svc, draft_id: str) -> None:
    svc.users().drafts().delete(userId="me", id=draft_id).execute()


def has_bounced(svc, address: str) -> bool:
    """Did the mail to this address come back undeliverable (last 14 days)?"""
    q = f'from:(mailer-daemon OR postmaster) "{address}" newer_than:14d'
    return bool(svc.users().messages().list(userId="me", q=q, maxResults=1).execute().get("messages"))
