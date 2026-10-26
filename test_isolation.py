"""
test_isolation.py - Security regression tests for tenant/role isolation.

Run:  pytest -q
"""

import pytest
from fastapi.testclient import TestClient

import rag
from api import NO_CONTEXT_ANSWER, app
from auth import Role, User

ALICE = {"Authorization": "Bearer alice-hr-token"}
BOB = {"Authorization": "Bearer bob-eng-token"}

HR_DOC = {
    "document": "The 2026 Q3 Bonus pool is $100,000.",
    "metadata": {"tenant_id": "AcmeCorp", "required_role": "HR", "source": "hr/comp"},
}
ENG_DOC = {
    "document": "The new API architecture uses GraphQL.",
    "metadata": {"tenant_id": "AcmeCorp", "required_role": "Engineering", "source": "eng/rfc"},
}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:  # runs the lifespan, which loads the model
        assert c.post("/v1/ingest", json=HR_DOC, headers=ALICE).status_code == 201
        assert c.post("/v1/ingest", json=ENG_DOC, headers=BOB).status_code == 201
        # A different tenant's HR document, written directly to the store.
        rag.add_document("Globex bonus pool is $9,000,000.", tenant_id="Globex", required_role="HR")
        yield c


def ask(client, headers, question):
    resp = client.post("/v1/query", json={"question": question}, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_alice_hr_gets_bonus_answer(client):
    body = ask(client, ALICE, "What is the bonus pool?")
    assert body["context_found"] is True
    assert "$500,000" in body["answer"]
    assert body["scope"] == {"user_id": "alice", "tenant_id": "AcmeCorp", "role": "HR"}


def test_bob_engineering_cannot_see_bonus(client):
    body = ask(client, BOB, "What is the bonus pool?")
    assert body["context_found"] is False
    assert body["answer"] == NO_CONTEXT_ANSWER
    assert body["sources"] == []


def test_bob_gets_his_own_docs(client):
    body = ask(client, BOB, "What does the new API architecture use?")
    assert "GraphQL" in body["answer"]
    assert all("Bonus" not in s["text"] for s in body["sources"])


def test_no_cross_tenant_leak(client):
    body = ask(client, ALICE, "What is the Globex bonus pool?")
    assert all("Globex" not in s["text"] for s in body["sources"])


def test_filter_blocks_even_with_threshold_disabled():
    """With the relevance threshold switched off, Bob still only ever receives Engineering chunks."""
    bob = User("bob", "AcmeCorp", Role.ENGINEERING)
    chunks = rag.secure_query("What is the bonus pool?", bob, top_k=10, min_similarity=-1.0)
    assert chunks, "expected Bob's own (irrelevant) chunk to be a candidate"
    assert {(c.tenant_id, c.required_role) for c in chunks} == {("AcmeCorp", "Engineering")}


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer nope"}, {"Authorization": "Basic abc"}])
def test_rejects_bad_credentials(client, headers):
    assert client.post("/v1/query", json={"question": "hi"}, headers=headers).status_code == 401


def test_cannot_ingest_into_other_tenant(client):
    payload = {**HR_DOC, "metadata": {**HR_DOC["metadata"], "tenant_id": "Globex"}}
    assert client.post("/v1/ingest", json=payload, headers=ALICE).status_code == 403


def test_cannot_ingest_into_other_role(client):
    # Bob tries to plant a document in HR's scope.
    assert client.post("/v1/ingest", json=HR_DOC, headers=BOB).status_code == 403


def test_rejects_unknown_fields(client):
    payload = {**ENG_DOC, "metadata": {**ENG_DOC["metadata"], "required_role": ["HR", "Engineering"]}}
    assert client.post("/v1/ingest", json=payload, headers=BOB).status_code == 422
    payload = {**ENG_DOC, "user_id": "alice"}
    assert client.post("/v1/ingest", json=payload, headers=BOB).status_code == 422
