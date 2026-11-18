"""The question pipeline against the sample documents. No database, no model."""
import json

import httpx
import pytest

from app import qa
from app.answering import Draft, Quote, get_answerer
from app.answering.llm import LLMAnswerer
from app.retrieval import get_retriever
from app.support import locate_quote


def ask(index, question, answerer=None):
    return qa.ask(question, index=index, retriever=get_retriever("bm25"),
                  answerer=answerer or get_answerer("extractive"))


@pytest.mark.parametrize("question, filename, expected", [
    ("How long must a password be?", "password-and-mfa-policy.pdf", "at least 14 characters"),
    ("Do I need MFA to access the finance system?", "password-and-mfa-policy.pdf", "finance system"),
    ("How many days a week can I work from home?", "remote-work-and-vpn-policy.pdf", "3 days per week"),
    ("What do I do if my laptop is stolen?", "remote-work-and-vpn-policy.pdf", "within 2 hours"),
    ("How long are backups kept?", "data-retention-policy.pdf", "35 days"),
    ("What is our policy on using personal USB drives?", "device-and-laptop-policy.md", "USB storage is blocked"),
    ("How quickly must a P1 incident get a first response?", "incident-response-procedure.md", "15 minutes"),
])
def test_sample_questions_are_answered_from_the_right_passage(sample_index, question, filename, expected):
    result = ask(sample_index, question)
    assert result["supported"] is True
    source = result["sources"][0]
    assert source["filename"] == filename
    assert expected in source["passage"]
    # The answer is copied from the passage, and the highlight points at it.
    start, end = source["highlight"]
    assert source["passage"][start:end] == source["quote"] == result["answer"]
    assert source["location"]["label"]


@pytest.mark.parametrize("question", [
    "What is the company's policy on parental leave?",
    "What is the dress code?",
    "Who won the football last night?",
    "???",
])
def test_unsupported_questions_are_declined(sample_index, question):
    result = ask(sample_index, question)
    assert result["supported"] is False
    assert result["sources"] == []
    assert result["answer"] == qa.NOT_SUPPORTED


def test_answers_are_fast(sample_index):
    assert ask(sample_index, "How long are backups kept?")["took_ms"] < 2000


class InventingAnswerer:
    """Stands in for a model that makes up a quote."""
    name = "inventing"

    def answer(self, question, hits):
        return Draft(text="Passwords must be 8 characters.",
                     quotes=[Quote(hits[0].chunk.id, "Passwords must be at least 8 characters long.")])


class OffTopicAnswerer:
    """Stands in for a model that cites a passage it was never given."""
    name = "off-topic"

    def answer(self, question, hits):
        return Draft(text="Email is retained for 7 years.", quotes=[Quote(-1, "Email is retained for 7 years.")])


def test_an_invented_quote_is_rejected_by_code(sample_index):
    result = ask(sample_index, "How long must a password be?", answerer=InventingAnswerer())
    assert result["supported"] is False
    assert result["answer"] == qa.NOT_SUPPORTED


def test_a_quote_from_a_passage_that_was_not_retrieved_is_rejected(sample_index):
    result = ask(sample_index, "How long must a password be?", answerer=OffTopicAnswerer())
    assert result["supported"] is False


def test_quote_matching_ignores_whitespace_only():
    passage = "Backups are  retained\nfor 35 days."
    assert locate_quote("Backups are retained for 35 days.", passage) == (0, len(passage))
    assert locate_quote("Backups are retained for 36 days.", passage) is None


def llm_with_reply(content: str) -> LLMAnswerer:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    return LLMAnswerer(base_url="http://model.test/v1", model="test-model",
                       transport=httpx.MockTransport(handler))


def test_llm_answer_is_kept_when_its_quote_is_real(sample_index):
    reply = json.dumps({"answer": "At least 14 characters.",
                        "quotes": [{"passage": 1, "text": "Passwords must be at least 14 characters long."}]})
    result = ask(sample_index, "How long must a password be?", answerer=llm_with_reply(reply))
    assert result["supported"] is True
    assert result["answer"] == "At least 14 characters."
    assert result["sources"][0]["quote"] == "Passwords must be at least 14 characters long."


def test_llm_answer_is_dropped_when_its_quote_is_made_up(sample_index):
    reply = json.dumps({"answer": "At least 8 characters.",
                        "quotes": [{"passage": 1, "text": "Passwords must be at least 8 characters."}]})
    result = ask(sample_index, "How long must a password be?", answerer=llm_with_reply(reply))
    assert result["supported"] is False


def test_llm_failure_falls_back_to_the_extractive_answer(sample_index):
    def handler(request):
        raise httpx.ConnectError("no network")

    answerer = LLMAnswerer(base_url="http://model.test/v1", model="test-model",
                           transport=httpx.MockTransport(handler))
    result = ask(sample_index, "How long must a password be?", answerer=answerer)
    assert result["supported"] is True
    assert result["answer"] == "Passwords must be at least 14 characters long."
    assert "llm unavailable" in result["answerer"]


def test_aliases_only_match_whole_words():
    from app.text import words
    assert words("Design in the open") == ["design", "in", "the", "open"]
    assert words("Use Wi-Fi to sign in") == ["use", "wifi", "to", "signin"]


# ---------------------------------------------------------------- stricter support rules

def index_of(documents: dict[str, str]):
    """An in-memory index built from Markdown text."""
    from app.extract import extract
    from app.index import Chunk, CorpusIndex

    chunks, cid = [], 0
    for doc_id, (name, text) in enumerate(documents.items()):
        ex = extract(text.encode(), name)
        for i, p in enumerate(ex.passages):
            cid += 1
            chunks.append(Chunk(cid, doc_id, name, ex.title, ex.doc_version, ex.doc_owner, 1,
                                "", i, p.kind, p.text, p.heading, p.page, p.clause, p.line))
    return CorpusIndex(chunks)


OFFICE = {
    "onboarding.md": "# Onboarding Checklist\n\n## First month\n- The hiring manager sets probation goals within 30 days.\n"
                     "- The hiring manager assigns an onboarding buddy.\n",
    "desk.md": "# Clean Desk Policy\n\n## Rooms\n- Whiteboards in meeting rooms must be wiped after use.\n",
    "devices.md": "# Device Policy\n\n**Owner:** IT Operations  |  **Version:** 2.0\n\n## Laptops\n- Laptops are refreshed every 4 years.\n",
    "travel.md": "# Travel Policy\n\n## Flights\n- Flights must be booked at least 14 days before departure.\n",
}


@pytest.mark.parametrize("question", [
    "Who approved the data retention policy?",       # names a document; no passage says who approved it
    "Who wrote the incident response procedure?",
])
def test_naming_a_document_is_not_enough_to_get_an_answer(sample_index, question):
    assert ask(sample_index, question)["supported"] is False


def test_a_how_to_question_needs_its_action_word_in_the_passage():
    result = ask(index_of(OFFICE), "How do I book a meeting room?")
    assert result["supported"] is False
    assert "book" in result["reason"]


def test_half_of_a_phrase_is_not_enough():
    # "probation goals within 30 days" is not about the probation period.
    assert ask(index_of(OFFICE), "How long is the probation period?")["supported"] is False


def test_an_upper_case_acronym_is_not_a_stop_word():
    from app.text import words
    assert "itxx" in words("Who is the IT manager?")
    assert "itxx" not in words("WHO IS THE IT MANAGER?")        # all capitals: not acronyms
    assert ask(index_of(OFFICE), "Who is the IT manager?")["supported"] is False


def test_common_wording_differences_are_bridged():
    result = ask(index_of(OFFICE), "How often are laptops replaced?")
    assert result["supported"] is True and "every 4 years" in result["answer"]


class FragmentAnswerer:
    """Stands in for a model that writes its own answer and cites a meaningless fragment."""
    name = "fragment"

    def answer(self, question, hits):
        return Draft(text="Backups are kept for 7 years and may be shared with anyone.",
                     quotes=[Quote(hits[0].chunk.id, "a")])


def test_a_fragment_is_not_accepted_as_a_quote(sample_index):
    assert ask(sample_index, "How long are backups kept?", answerer=FragmentAnswerer())["supported"] is False


def test_an_answer_that_contradicts_its_own_quote_is_replaced_by_the_quote(sample_index):
    reply = json.dumps({"answer": "Passwords must be at least 8 characters long.",
                        "quotes": [{"passage": 1, "text": "Passwords must be at least 14 characters long."}]})
    result = ask(sample_index, "How long must a password be?", answerer=llm_with_reply(reply))
    assert result["supported"] is True
    assert result["answer"] == "Passwords must be at least 14 characters long."
    assert "wording replaced" in result["answerer"]


def test_questions_about_a_document_owner_and_version_use_its_header_line(sample_index):
    owner = ask(sample_index, "Who owns the device and laptop policy?")
    assert owner["supported"] is True and "IT Operations" in owner["answer"]
    assert owner["sources"][0]["filename"] == "device-and-laptop-policy.md"
    version = ask(sample_index, "What version is the remote work and VPN policy?")
    assert version["supported"] is True and "3.0" in version["answer"]
    assert version["sources"][0]["filename"] == "remote-work-and-vpn-policy.pdf"


# ---------------------------------------------------------------- regressions from the external review

def one_document(text: str, name: str = "a.md"):
    """An index holding a single small Markdown document."""
    from app.extract import extract
    from app.index import Chunk, CorpusIndex

    ex = extract(text.encode(), name)
    return CorpusIndex([Chunk(i + 1, 1, name, ex.title, ex.doc_version, ex.doc_owner, 1, "", i,
                              p.kind, p.text, p.heading, p.page, p.clause, p.line)
                        for i, p in enumerate(ex.passages)])


def test_a_negative_question_is_not_answered_by_the_positive_rule():
    index = one_document("MFA is required for payroll access.\n")
    assert ask(index, "Is MFA required for payroll access?")["supported"] is True
    declined = ask(index, "When is MFA not required?")
    assert declined["supported"] is False
    assert "exception or negative" in declined["reason"]


def test_a_negative_question_is_answered_by_a_passage_that_states_the_negative():
    index = one_document("MFA is not required for the guest wifi.\n")
    assert ask(index, "When is MFA not required?")["supported"] is True


def test_an_un_word_is_not_answered_by_its_opposite():
    index = one_document("Approved vendors are eligible for reimbursement.\n")
    assert ask(index, "Are unapproved vendors eligible for reimbursement?")["supported"] is False
    # ...but "not collected" does answer "uncollected".
    index = one_document("Print jobs that are not collected are kept for 12 hours.\n")
    assert ask(index, "How long are uncollected print jobs kept?")["supported"] is True


def test_how_long_needs_a_number_with_a_unit_not_just_any_number():
    assert ask(one_document("Backups are kept in vault 7.\n"), "How long are backups kept?")["supported"] is False
    assert ask(one_document("Backups are kept for 35 days.\n"), "How long are backups kept?")["supported"] is True
    # "How many" is still answered by a bare number.
    assert ask(one_document("The maximum number of guests is 5.\n"), "How many guests are allowed?")["supported"] is True


def test_the_sentence_shown_for_a_quantity_question_states_the_quantity():
    text = ("This section explains how long backups are kept. The retention period is 35 days."
            + " This is background." * 16 + "\n")
    result = ask(one_document(text), "How long are backups kept?")
    assert result["supported"] is True
    assert "35 days" in result["answer"] and "35 days" in result["sources"][0]["quote"]


def test_a_negative_question_needs_the_negated_word_in_the_passage(sample_index):
    """Regression: with the sample documents, "When is MFA not required?" was answered
    with "SMS codes are not an approved MFA method", a negative passage about MFA that
    says nothing about when it is not required."""
    assert ask(sample_index, "When is MFA not required?")["supported"] is False
    # A negative question the documents do answer is still answered.
    index = one_document("MFA is not required for the guest wifi.\n")
    assert ask(index, "When isn't MFA required?")["supported"] is True


def test_a_question_with_one_ordinary_word_is_declined_as_too_general(sample_index):
    """Regression: "How was your day?" has one topic word, "day", and was answered with
    "Backups are retained for 35 days"."""
    for small_talk in ["How was your day?", "help", "what's up", "How is your week going?",
                       "Tell me about backups"]:
        result = ask(sample_index, small_talk)
        assert result["supported"] is False, small_talk
        assert "too general" in result["reason"]
    # One word is enough when it is an acronym or a code...
    assert ask(sample_index, "What is a P1?")["supported"] is True
    assert ask(sample_index, "What is MFA?")["supported"] is True
    # ...or when the question also asks for a quantity.
    assert ask(sample_index, "How long must a password be?")["supported"] is True
