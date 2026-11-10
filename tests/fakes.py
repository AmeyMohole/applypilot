"""An in-memory stand-in for the Gmail API surface the pipeline uses.

Only what mail_sync / followup / send / draft_direct call: messages.list/get, threads.get,
drafts.list/get/create/send, getProfile. Queries understand the operators the code builds:
from:, -from:, to:, in:sent, in:inbox, after:; newer_than: is ignored.
"""
import base64
import email
import itertools
import re
from email.utils import formatdate

ME = "ameymohole@gmail.com"


class _Call:
    def __init__(self, value):
        self._value = value

    def execute(self):
        return self._value() if callable(self._value) else self._value


class FakeGmail:
    def __init__(self):
        self.messages = {}     # id -> dict(sender, to, subject, snippet, thread, date(epoch), body, sent)
        self.drafts = {}       # id -> dict(to, subject, thread)
        self.created = []      # draft ids created during the test
        self.sent = []         # draft ids "sent"
        self._ids = itertools.count(1)

    # ---- fixtures
    def add(self, sender, subject, snippet="", to=ME, thread=None, date=1_760_000_000, body="", sent=False):
        mid = f"m{next(self._ids)}"
        self.messages[mid] = dict(sender=sender.lower(), to=to.lower(), subject=subject, snippet=snippet,
                                  thread=thread or f"t{mid}", date=date, body=body or snippet, sent=sent)
        return mid

    def add_draft(self, to, subject, thread=None):
        did = f"d{next(self._ids)}"
        self.drafts[did] = dict(to=to, subject=subject, thread=thread)
        return did

    # ---- query matching
    @staticmethod
    def _base(addr):
        local, _, dom = addr.partition("@")
        return f"{local.split('+')[0]}@{dom}"

    def _match(self, m, q):
        for neg, op, val in re.findall(r"(-?)(\w+):(\S+)", q):
            val = val.lower()
            if op == "from":
                if "@" in val:
                    hit = m["sender"] == val or self._base(m["sender"]) == val
                elif neg:
                    hit = val in m["sender"]
                else:
                    hit = m["sender"].endswith("@" + val) or m["sender"].endswith("." + val)
            elif op == "to":
                hit = m["to"] == val
            elif op == "in":
                hit = m["sent"] if val == "sent" else not m["sent"]
            elif op == "after":
                hit = m["date"] > int(val)
            else:
                continue
            if hit == bool(neg):
                return False
        return True

    def users(self):
        return _Users(self)


def _headers(m):
    return [{"name": "From", "value": m["sender"]}, {"name": "To", "value": m["to"]},
            {"name": "Subject", "value": m["subject"]}, {"name": "Message-ID", "value": f"<{m['thread']}@x>"},
            {"name": "Date", "value": formatdate(m["date"])}]


class _Messages:
    def __init__(self, g):
        self.g = g

    def list(self, userId, q="", maxResults=100, pageToken=None):
        found = [mid for mid, m in sorted(self.g.messages.items(), key=lambda kv: -kv[1]["date"])
                 if self.g._match(m, q)]
        return _Call({"messages": [{"id": mid} for mid in found[:maxResults]]} if found else {})

    def get(self, userId, id, format="metadata", metadataHeaders=None):
        m = self.g.messages[id]
        return _Call({"id": id, "threadId": m["thread"], "snippet": m["snippet"],
                      "internalDate": str(m["date"] * 1000), "payload": {"headers": _headers(m)}})


class _Threads:
    def __init__(self, g):
        self.g = g

    def get(self, userId, id, format="metadata", metadataHeaders=None):
        msgs = sorted((m for m in self.g.messages.values() if m["thread"] == id), key=lambda m: m["date"])
        return _Call({"id": id, "messages": [
            {"snippet": m["snippet"], "internalDate": str(m["date"] * 1000), "payload": {"headers": _headers(m)}}
            for m in msgs]})


class _Drafts:
    def __init__(self, g):
        self.g = g

    def list(self, userId, maxResults=100, pageToken=None):
        ids = sorted(self.g.drafts)
        start = int(pageToken or 0)
        body = {"drafts": [{"id": d} for d in ids[start:start + maxResults]]}
        if start + maxResults < len(ids):
            body["nextPageToken"] = str(start + maxResults)
        return _Call(body)

    def get(self, userId, id, format="metadata"):
        d = self.g.drafts[id]
        return _Call({"id": id, "message": {"payload": {"headers": [
            {"name": "To", "value": d["to"]}, {"name": "Subject", "value": d["subject"]}]}}})

    def create(self, userId, body):
        raw = base64.urlsafe_b64decode(body["message"]["raw"])
        parsed = email.message_from_bytes(raw)
        did = f"d{next(self.g._ids)}"
        self.g.drafts[did] = {"to": parsed["To"], "subject": parsed["Subject"],
                              "thread": body["message"].get("threadId"), "raw": parsed}
        self.g.created.append(did)
        return _Call({"id": did})

    def send(self, userId, body):
        self.g.sent.append(body["id"])
        return _Call({"id": f"sent-{body['id']}", "threadId": self.g.drafts[body["id"]].get("thread") or f"t-{body['id']}"})

    def delete(self, userId, id):
        self.g.drafts.pop(id, None)
        return _Call({})


class _Users:
    def __init__(self, g):
        self.g = g

    def getProfile(self, userId):
        return _Call({"emailAddress": ME})

    def messages(self):
        return _Messages(self.g)

    def threads(self):
        return _Threads(self.g)

    def drafts(self):
        return _Drafts(self.g)
