"""Who may be written to: do-not-contact, mail servers, company dedupe, and the pre-draft screen."""
import contacts
import leadqueue
import parser as jp
import runlock
import scan
import store as st


def rec(email, **kw):
    return {"email": email, "role": "DevOps Engineer", "company": "Acme", "status": "sent",
            "sent_at": st.now(), **kw}


def levels(issues):
    return [level for level, _ in issues]


def test_not_an_address_is_blocked():
    assert contacts.blockers({}, "not-an-email")[0][0] == "block"


def test_do_not_contact_list_blocks_address_and_whole_domain():
    contacts.add_dnc("bad@acme.com", "asked us to stop")
    contacts.add_dnc("@spam.io", "scam template")
    contacts.add_dnc("bad@acme.com", "duplicate")           # not added twice
    assert len(contacts.load_dnc()) == 2
    assert "asked us to stop" in contacts.dnc_reason("BAD@acme.com")
    assert "scam" in contacts.dnc_reason("anyone@spam.io")
    assert contacts.dnc_reason("good@acme.com") == ""
    assert levels(contacts.blockers({}, "bad@acme.com")) == ["block"]


def test_a_bounced_or_do_not_mail_record_blocks_that_address_only():
    store = {"a": rec("gone@acme.com", bounced=True, bounce_note="2026-11-02: undeliverable"),
             "b": rec("left@acme.com", do_not_mail=True, do_not_mail_reason="auto-reply: no longer read")}
    assert "bounced" in contacts.dnc_reason("gone@acme.com", store)
    assert "do_not_mail" in contacts.dnc_reason("left@acme.com", store)
    assert contacts.dnc_reason("fine@acme.com", store) == ""


def test_same_company_by_domain_and_by_name_but_never_by_free_mail():
    store = {"a": rec("one@acme.com"), "b": rec("hr@gmail.com", company="Globex Pvt Ltd")}
    assert [k for k, _ in contacts.same_company(store, "two@acme.com")] == ["a"]
    assert [k for k, _ in contacts.same_company(store, "x@globex.in", "GLOBEX Limited")] == ["b"]
    assert contacts.same_company(store, "someone@gmail.com") == []        # gmail is not "a company"


def test_open_conversation_is_called_out_in_the_warning():
    store = {"a": rec("one@acme.com", replied=True, last_reply_outcome="interview")}
    issues = contacts.blockers(store, "two@acme.com", "Acme")
    assert levels(issues) == ["warn"] and "conversation is open" in issues[0][1]
    store["a"]["last_reply_outcome"] = "rejected"
    assert "conversation is open" not in contacts.blockers(store, "two@acme.com", "Acme")[0][1]


def test_mx_status_blocks_dead_domains_and_caches_lookups(monkeypatch):
    calls = []
    monkeypatch.setattr(contacts, "_lookup_mx", lambda d: calls.append(d) or "nxdomain")
    assert levels(contacts.blockers({}, "x@no-such-domain.example")) == ["block"]
    contacts.mx_status("y@no-such-domain.example")
    assert calls == ["no-such-domain.example"]                            # second lookup came from the cache
    monkeypatch.setattr(contacts, "_lookup_mx", lambda d: "unknown")
    assert levels(contacts.blockers({}, "x@other.example")) == ["warn"]   # can't check: warn, don't block
    assert contacts.mx_status("someone@gmail.com") == "ok"                # free mail is never looked up


def test_parse_nslookup():
    assert contacts._parse_nslookup("*** can't find x: Non-existent domain") == "nxdomain"
    assert contacts._parse_nslookup("x.com MX preference = 10, mail exchanger = mx.x.com") == "ok"
    assert contacts._parse_nslookup("x.com MX preference = 0, mail exchanger = .") == "no-mx"
    assert contacts._parse_nslookup("x.com\n primary name server = ns.x.com\n responsible mail addr = a") == "no-mx"
    assert contacts._parse_nslookup("timed out") == "unknown"


def test_inbox_kind():
    assert contacts.inbox_kind("priya.sharma@acme.com", "Priya Sharma") == "person"
    assert contacts.inbox_kind("careers@acme.com", "Priya Sharma") == "generic"


# ---- the screen scan.py runs before it drafts

def lead(email, company="Acme", **kw):
    return jp.Lead(**{**dict(role="DevOps Engineer", email=email, emails=[email], track="devops",
                             company=company, company_confident=True), **kw})


def test_screen_skips_blocked_leads_and_companies_that_are_already_talking():
    contacts.add_dnc("stop@dnc.io", "never again")
    store = {"a": rec("one@talking.com", company="Talking", replied=True, last_reply_outcome="interview"),
             "b": rec("one@samewhere.com", company="Samewhere")}
    leads = [lead("stop@dnc.io", "Dnc"), lead("two@talking.com", "Talking"),
             lead("two@samewhere.com", "Samewhere"), lead("hr@fresh.com", "Fresh")]
    ok, blocked, warned = scan.screen(leads, store)
    assert [x.email for x in ok] == ["two@samewhere.com", "hr@fresh.com"]
    assert {x.email: why.split(":")[0] for x, why in blocked} == {
        "stop@dnc.io": "do-not-contact list", "two@talking.com": "a conversation is already open at this company"}
    assert [x.email for x, _ in warned] == ["two@samewhere.com"]          # another application there: noted only


# ---- lead queue and send lock

def test_lead_queue_dedupes_and_drops_drafted():
    assert leadqueue.add([{"email": "New@Acme.com", "role": "SRE"}, {"email": "new@acme.com", "role": "sre"}]) == 1
    assert leadqueue.add([{"email": "new@acme.com", "role": "SRE"}]) == 0
    assert leadqueue.load()[0]["queued_at"]
    assert leadqueue.drop_drafted({"k": {"email": "new@acme.com"}}) == 1 and leadqueue.load() == []


def test_run_lock_is_exclusive_and_stale_locks_are_removed():
    assert runlock.held() is None
    with runlock.sending("test"):
        assert runlock.held()["what"] == "test"
        try:
            with runlock.sending("second"):
                raise AssertionError("a second sender got the lock")
        except RuntimeError as exc:
            assert "another send is running" in str(exc)
        assert runlock.refuse_if_sending("followup") is True
    assert runlock.held() is None and runlock.refuse_if_sending("followup") is False
    runlock._lock_path().write_text('{"pid": 99999999, "what": "crashed", "since": "01:00:00"}')
    assert runlock.held() is None and not runlock._lock_path().exists()      # dead process: lock cleared
