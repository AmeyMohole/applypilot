"""Reply classification: a person answering versus receipts, out-of-office mail, redirects, bounces."""
import base64

import pytest

import mail_sync
import replies

ME = "ameymohole@gmail.com"


def kind(sender, subject, snippet):
    return replies.classify(sender, subject, snippet, me=ME)


def test_plus_addressed_noreply_receipt():
    v = kind("careers+noreply@acme.com", "Re: Application for DevOps Engineer | Pune",
             "Thank you for applying. We have received your application and our team will review it.")
    assert v.kind == "receipt"


def test_canned_will_get_in_touch_from_a_generic_inbox():
    v = kind("jobs@acme.com", "Re: Application for SRE",
             "Thank you for your email. Our HR team will get in touch with you shortly.")
    assert v.kind == "receipt"


def test_polite_rejection_after_the_thanks():
    v = kind("hr@acme.com", "Re: Application for DevOps Engineer",
             "Dear Amey, Thank you for your interest in the DevOps Engineer position at Acme. We appreciate "
             "your time. However, we regret to inform you that we will not be moving forward with your "
             "application at this time.")
    assert (v.kind, v.outcome) == ("reply", "rejected")


def test_receipt_promising_an_interview_is_still_a_receipt():
    v = kind("future+noreply@acme.com", "Application received",
             "Thanks for applying. If your profile is shortlisted, we will contact you for an interview.")
    assert v.kind == "receipt"


def test_out_of_office():
    v = kind("asha.rao@acme.com", "Automatic reply: Application for DevOps Engineer",
             "I am currently out of the office with limited access to email and will be back on 26 Nov.")
    assert v.kind == "ooo"


def test_out_of_office_with_an_alternate_contact_keeps_the_address_alive():
    v = kind("asha.rao@gmail.com", "Out of office",
             "I am on leave. Please reach out to talent@acme.com for anything urgent.")
    assert v.kind == "ooo" and v.redirect_to == ["talent@acme.com"] and not v.dead


def test_left_company_redirect():
    v = kind("ravi.k@acme.com", "Automatic reply: Application for Cloud Engineer",
             "Hi, I've left Acme. Please write to meera.s@acme.com for hiring queries.")
    assert v.kind == "redirect" and v.dead and v.redirect_to == ["meera.s@acme.com"]


def test_no_longer_with_redirect():
    v = kind("sam@acme.com", "Re: Application for DevOps Engineer",
             "Sam is no longer with Acme. Please contact Priya Nair at priya.n@acme.com.")
    assert (v.kind, v.redirect_to) == ("redirect", ["priya.n@acme.com"])


def test_unmonitored_mailbox_is_dead():
    v = kind("hrteam@acme.com", "Automatic reply", "This mailbox is not monitored. Please use our careers portal.")
    assert v.kind == "redirect" and v.dead


def test_bounce_names_the_failed_address():
    v = kind("mailer-daemon@googlemail.com", "Delivery Status Notification (Failure)",
             "Address not found. Your message wasn't delivered to dead.end@acme.com")
    assert v.kind == "bounce" and v.redirect_to == ["dead.end@acme.com"]


def test_broadcast():
    v = kind("hr@acme.com", "Openings", "Dear Candidates, we have the following openings for freshers. "
                                       "WhatsApp your CV.")
    assert v.kind == "broadcast"


def test_noreply_ats_rejection_still_counts():
    v = kind("no-reply@acme.com", "Your application",
             "Thank you for applying. Unfortunately, we will not be moving forward with your application.")
    assert (v.kind, v.outcome) == ("reply", "rejected")


def test_human_details_request():
    v = kind("neha.p@acme.com", "Re: Application for DevOps Engineer | Pune",
             "Dear Candidate, Thank you for your interest. To proceed, kindly share the following details: "
             "current CTC, expected CTC")
    assert (v.kind, v.outcome) == ("reply", "info requested")


def test_human_interview():
    v = kind("tara.m@acme.com", "Invitation: DevOps Engineer | Technical Interview | Round 1",
             "Join with Google Meet - Hello Amey, Greetings from Acme")
    assert (v.kind, v.outcome) == ("reply", "interview")


def test_human_short_reply_and_named_recruiter_ack_are_replies():
    assert kind("chandan.s@acme.com", "Re: Application for SRE",
                "Hi Amey, currently reviewing your profile, will get back.").is_reply
    v = kind("chandana.r@acme.com", "Re: Application for SRE",
             "Thank you for your email. I have forwarded your profile to the technical team.")
    assert (v.kind, v.outcome) == ("reply", "acknowledged")


def test_caution_flag_on_fee_and_id_requests():
    v = kind("suma.g@acme.com", "Re: Application for DevOps Engineer",
             "Please share your PAN card copy and a selfie with your ID for verification.")
    assert v.is_reply and v.caution


@pytest.mark.parametrize("addr", ["noreply@x.com", "careers+noreply@x.com", "do-not-reply@x.com",
                                  "notifications@x.com", "MAILER-DAEMON@x.com"])
def test_automated_senders(addr):
    assert replies.is_automated(addr)


@pytest.mark.parametrize("addr", ["nora.reply@x.com", "careers@x.com", "rahul@x.com"])
def test_people_and_generic_inboxes_are_not_automated(addr):
    assert not replies.is_automated(addr)


def test_mentions_role():
    assert replies.mentions_role("Re: Application for Cloud Engineer | Pune", "Cloud Engineer")
    assert not replies.mentions_role("Re: Application for Cloud Engineer", "Security Engineer")
    assert not replies.mentions_role("", "Cloud Engineer")


def test_body_text_drops_the_quoted_application():
    raw = ("Hi Amey,\n\nThe position is currently filled.\n\nOn Thu, Nov 5, 2026 at 2:11 PM Amey Mohole "
           "<a@x.com> wrote:\n> I came across your post...")
    payload = {"mimeType": "multipart/alternative", "parts": [
        {"mimeType": "text/plain", "body": {"data": base64.urlsafe_b64encode(raw.encode()).decode()}}]}
    assert mail_sync.body_text(payload) == "Hi Amey, The position is currently filled."
