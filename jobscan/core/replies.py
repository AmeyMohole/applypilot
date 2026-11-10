"""Tell a recruiter's answer apart from everything else that lands in the thread.

Treating any mail from the address (or its domain) as "they replied" stops every later follow-up.
Auto-receipts ("we have received your application"), out-of-office mail, "I've left, write to X",
mass broadcasts and bounces all come from the same addresses a real answer does.

classify() looks at one message (sender, subject, text) and says which of these it is. Only
kind == "reply" is a person answering the application.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")

# Machine senders, including plus-addressed ones (careers+noreply@...), which a plain "noreply"
# test on the whole address misses.
AUTOMATED_LOCAL_RE = re.compile(
    r"(?:^|[+._-])(?:no-?reply|do-?not-?reply|donotreply|notifications?|notify|mailer-daemon|"
    r"postmaster|auto-?reply|autoresponder|bounces?|alerts?|mailer)(?:$|[+._-])", re.I)

BOUNCE_SUBJECT_RE = re.compile(
    r"^(?:undeliverable|undelivered mail|delivery status notification|mail delivery (?:failed|failure|subsystem)|"
    r"returned mail|failure notice|delivery has failed|message not delivered|address not found)", re.I)

# The address is dead: the person left, or the mailbox is not read.
LEFT_RE = re.compile(
    r"(no longer (?:with|at|part of|working (?:with|at|for))|(?:i'?ve|i have|has|have) (?:now )?left\b|"
    r"left the (?:company|organi[sz]ation|firm|team)|moved on from|my last (?:working )?day (?:at|with|was)|"
    r"(?:mailbox|inbox|e-?mail(?: address| id)?) is (?:no longer|not) (?:monitored|in use|active|being monitored)|"
    r"unmonitored (?:mailbox|inbox|e-?mail))", re.I)

OOO_RE = re.compile(
    r"(out of (?:the )?office|\bOOO\b|on (?:annual |sick |maternity |paternity |casual |personal )?leave\b|"
    r"away from (?:the )?(?:office|my desk|work)|limited access to (?:my )?e-?mail|"
    r"currently (?:away|travell?ing|unavailable|out)|(?:will be|i'?ll be) back (?:on|by)|"
    r"will return (?:on|by)|returning on|automatic reply|auto(?:matic)?[- ]?reply)", re.I)

RECEIPT_RE = re.compile(
    r"(thank(?:s| you) for (?:your )?(?:application|applying|interest|e-?mail|mail|writing|reaching out|"
    r"contacting|sharing your (?:resume|cv|profile))|(?:we|i) (?:have )?received your (?:application|e-?mail|mail|"
    r"resume|cv|profile)|(?:has|have) been (?:successfully )?received|this is an? (?:automated|auto-?generated|"
    r"system[- ]generated)|(?:please )?do not reply to this|will (?:get|be) (?:in touch|back to you)|"
    r"(?:our|the) (?:hr |recruitment |talent |hiring )?team will (?:review|get back|contact|reach out)|"
    r"we will (?:reach out|contact you|get back)|forwarded (?:it |your (?:resume|cv|profile) )?to (?:the |our )?"
    r"(?:hiring|recruitment|technical) team)", re.I)

# Mass mail from an address you applied to: not an answer to your application.
BROADCAST_RE = re.compile(
    r"(dear (?:all|candidates|job ?seekers|applicants|aspirants)\b|(?:we have|there are) (?:multiple|several|"
    r"the following|urgent|many) (?:openings|vacancies|requirements|positions)|whatsapp (?:your|ur) (?:cv|resume)|"
    r"forward (?:this|it) to your (?:friends|network)|refer your friends)", re.I)

REJECT_RE = re.compile(r"(moving forward with other candidates|not (?:be )?moving forward|decided not to proceed|"
                       r"not be proceeding|regret to inform|unfortunately,? (?:we|your)|position (?:has been|is) "
                       r"(?:filled|closed|on hold)|role is on hold|vacancy .{0,40}is closed|do not have any openings|"
                       r"no (?:current )?openings|not shortlisted|opening is for \d|we are not interested|"
                       r"not (?:be )?(?:able to )?proceed(?:ing)? (?:further )?with your)", re.I)
ASSESS_RE = re.compile(r"(assessment|test link|online test|coding test|screening test|complete the test|hackerrank|"
                       r"assignment|take-?home|problem statement)", re.I)
INTERVIEW_RE = re.compile(r"(interview|schedule a call|available for a call|meet(?:ing)? link|walk-?in|"
                          r"meet\.google\.com|teams\.microsoft\.com|zoom\.us/j)", re.I)
INFO_RE = re.compile(r"(share (?:the|your) (?:following )?details|current ctc|expected ctc|notice period:|"
                     r"updated resume|pan card|fill (?:out|in) .{0,20}form|please share|kindly share)", re.I)

# A receipt often says "if shortlisted, we will call you for an interview"; that sentence is a
# promise, not an invitation, so it is ignored when deciding whether a receipt-looking mail also
# carries a real request.
CONDITIONAL_RE = re.compile(r"[^.!?]*\b(?:if|in case|once|should) (?:you are|you're|your (?:profile|application|cv|"
                            r"resume)|shortlisted|selected|suitable|there is a match)[^.!?]*[.!?]?", re.I)

# Worth a second look before answering: fees, ID documents, chat apps.
CAUTION_RE = re.compile(r"(registration fee|training fee|security deposit|refundable|pay(?:ment)? of rs|selfie|"
                        r"pan card|aadhaa?r|complete (?:your )?registration|whatsapp me|text me (?:on|in) whatsapp)",
                        re.I)


@dataclass
class Verdict:
    kind: str                       # reply | receipt | ooo | redirect | bounce | broadcast
    outcome: str = ""               # for replies: rejected / assessment / interview / info requested / replied
    redirect_to: list = field(default_factory=list)   # other addresses the mail points to
    dead: bool = False              # the address we wrote to will not be read again
    caution: bool = False

    @property
    def is_reply(self) -> bool:
        return self.kind == "reply"


def is_automated(address: str) -> bool:
    local = (address or "").lower().split("@")[0]
    return bool(AUTOMATED_LOCAL_RE.search(local))


def is_generic_mailbox(address: str) -> bool:
    """careers@, jobs@, hr@..., i.e. no person's name to greet."""
    import compose  # keep this module importable without pulling compose in
    return not compose.greeting_name("", address)


def other_addresses(text: str, *exclude: str) -> list:
    """Addresses named in the mail, other than the sender's and your own."""
    skip = {e.lower() for e in exclude if e}
    found = []
    for raw in EMAIL_RE.findall(text or ""):
        addr = raw.lower().rstrip(".")
        if addr not in skip and addr not in found and not is_automated(addr):
            found.append(addr)
    return found


def outcome_of(text: str) -> str:
    """Best guess at what a human reply says. Order matters: a rejection that mentions
    "interview" is still a rejection."""
    return ("rejected" if REJECT_RE.search(text) else "assessment" if ASSESS_RE.search(text)
            else "interview" if INTERVIEW_RE.search(text) else "info requested" if INFO_RE.search(text)
            else "replied")


def has_request(text: str) -> bool:
    """True when the mail asks for something or decides something, once conditional promises
    ("if shortlisted we will call you") are removed."""
    firm = CONDITIONAL_RE.sub(" ", text)
    return bool(REJECT_RE.search(firm) or ASSESS_RE.search(firm) or INFO_RE.search(firm)
                or INTERVIEW_RE.search(firm))


def classify(sender: str, subject: str, snippet: str, me: str = "") -> Verdict:
    """Sort one inbound message. sender is the bare address."""
    sender = (sender or "").lower()
    subject = subject or ""
    text = f"{subject} {snippet or ''}"
    local = sender.split("@")[0]
    caution = bool(CAUTION_RE.search(text))

    if local in ("mailer-daemon", "postmaster") or BOUNCE_SUBJECT_RE.search(subject.strip()):
        return Verdict("bounce", redirect_to=other_addresses(snippet, sender, me))

    # A departure notice usually names who to write to instead.
    if LEFT_RE.search(text):
        return Verdict("redirect", redirect_to=other_addresses(text, sender, me), dead=True)

    automated = is_automated(sender)
    if OOO_RE.search(text) and not (has_request(text) and not RECEIPT_RE.search(text) and not automated):
        # "Please reach out to x@y.com while I am away": a pointer worth keeping, but the address
        # itself is still read.
        return Verdict("ooo", redirect_to=other_addresses(text, sender, me))

    if BROADCAST_RE.search(text):
        return Verdict("broadcast", caution=caution)

    if RECEIPT_RE.search(text) or automated:
        firm = CONDITIONAL_RE.sub(" ", text)
        # An ATS rejection from no-reply@ is still the answer.
        if REJECT_RE.search(firm):
            return Verdict("reply", "rejected", caution=caution)
        if ASSESS_RE.search(firm) and re.search(r"https?://|link", firm, re.I):
            return Verdict("reply", "assessment", caution=caution)
        if not has_request(text):
            # The same "thanks, we will get back to you" is canned from careers@ but a real
            # acknowledgement from a named recruiter: only the first stops follow-ups.
            if automated or is_generic_mailbox(sender):
                return Verdict("receipt", caution=caution)
            return Verdict("reply", "acknowledged", caution=caution)

    return Verdict("reply", outcome_of(text), caution=caution)


def mentions_role(subject: str, role: str) -> bool:
    """True when a reply's subject is about this role ("Re: Application for X").

    Decides which of several applications at one company a colleague's reply belongs to."""
    def norm(s: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()

    role_n, subject_n = norm(role), norm(subject)
    if not role_n or not subject_n:
        return False
    if f" {role_n} " in f" {subject_n} ":
        return True
    # Every word counts, short ones too: "DevOps Engineer" must not match a subject about the
    # "Security Engineer" role just because both say "engineer".
    words = [w for w in role_n.split() if w not in ("and", "the", "of", "for", "with")]
    return bool(words) and sum(w in subject_n.split() for w in words) / len(words) >= 0.75
