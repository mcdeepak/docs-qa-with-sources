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
