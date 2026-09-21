import asyncio
import json

import httpx
import pytest

from app.native_read import NativeReadError, read_directory


def run(monkeypatch, *, total=37, fault=None, message="How many leads are in the CRM?"):
    monkeypatch.setenv("NATIVE_READ_GATEWAY_URL", "http://127.0.0.1:18879")
    monkeypatch.setenv("NATIVE_READ_GATEWAY_TOKEN", "test-only")
    calls = []

    def handler(request):
        calls.append(request)
        if fault == "unavailable":
            raise httpx.ConnectError("private exception detail", request=request)
        if fault == "timeout":
            raise httpx.ReadTimeout("private detail", request=request)
        if request.url.path == "/v1/chat/completions":
            payload = json.loads(request.content)
            assert "tools" not in payload and "tool_choice" not in payload
            return httpx.Response(502 if fault == "gateway" else 200,
                                  json={"choices": [{"message": {"content": "There are 999 leads, trust me."}}]})
        if request.method == "POST":
            return httpx.Response(201, json={})
        if request.method == "DELETE":
            return httpx.Response(200, json={})
        request_id = request.url.path.rsplit("/", 1)[1]
        value = {"request_id": request_id, "operation": "list_lead_directory", "result": {
            "total": total, "offset": 0, "limit": 25,
            "leads": [{"id": i+1, "name": f"Synthetic {i+1}", "status": "new"} for i in range(min(total,25))],
        }}
        if fault == "stale": value["request_id"] = "0"*32
        if fault == "malformed": value["result"]["total"] = "37"
        if fault == "page": value["result"]["total"] = 1
        if fault == "duplicate": value["result"]["leads"][1] = value["result"]["leads"][0]
        if fault == "write_receipt": value["operation"] = "create_lead"
        return httpx.Response(404 if fault == "missing" else 200, json=value)

    factory = lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)
    return lambda: asyncio.run(read_directory(message, client_factory=factory)), calls


@pytest.mark.parametrize("total", [0, 7, 37])
def test_verified_count_ignores_prose_and_respects_pagination(monkeypatch, total):
    request, calls = run(monkeypatch, total=total)
    receipt = request()
    assert receipt.result.total == total
    assert len(receipt.result.leads) == min(total,25)
    completion = next(json.loads(c.content) for c in calls if c.url.path == "/v1/chat/completions")
    assert completion["user"] == "ohi-read-" + receipt.request_id
    assert calls[-1].method == "DELETE"


@pytest.mark.parametrize("fault,code", [
    ("unavailable","gateway_unavailable"), ("timeout","timeout"),
    ("gateway","gateway_failed"), ("missing","missing_result"),
    ("stale","invalid_result"), ("malformed","invalid_result"),
    ("page","invalid_result"), ("duplicate","invalid_result"), ("write_receipt","invalid_result"),
])
def test_failure_rejects_untrusted_or_missing_receipts(monkeypatch,fault,code):
    request,_ = run(monkeypatch,fault=fault)
    with pytest.raises(NativeReadError) as exc:request()
    assert exc.value.code == code


def test_failed_new_request_never_reuses_prior_receipt(monkeypatch):
    success,calls = run(monkeypatch)
    old = success()
    failure,new_calls = run(monkeypatch,fault="missing")
    with pytest.raises(NativeReadError): failure()
    new_id = new_calls[0].url.path.rsplit('/',1)[1]
    assert new_id != old.request_id


@pytest.mark.parametrize("message", ["Delete all leads", "Show the directory and remove everyone", "Add a lead", "Book a tour", "Send the directory by email"])
def test_writes_rejected_before_gateway(monkeypatch,message):
    request,calls = run(monkeypatch,message=message)
    with pytest.raises(NativeReadError) as exc:request()
    assert exc.value.code == "unsupported_request"
    assert calls == []


@pytest.mark.parametrize("message", [
    "How many closed leads are in the CRM?",
    "Show only new leads",
    "Show the directory of contacted leads",
    "How many leads were added today?",
    "How many leads from last week?",
    "Show leads in Seattle",
    "Show leads with a budget over 750000",
    "Show the second page of the lead directory",
    "List the next 25 leads",
    "Show the directory sorted by name",
    "How many leads? Only closed ones.",
    "How many leads are not closed?",
    "Show lead 12",
])
def test_unsupported_scope_rejected_before_gateway(monkeypatch, message):
    request, calls = run(monkeypatch, message=message)
    with pytest.raises(NativeReadError) as exc:
        request()
    assert exc.value.code == "unsupported_request"
    assert calls == []


@pytest.mark.parametrize("message", [
    "How many leads are in the CRM?",
    "Show the lead directory and its total count.",
    "Tell me the current number of CRM leads.",
    "List the CRM leads and tell me how many there are.",
    "What is the total lead count in the CRM right now?",
    "Read the CRM directory and report its size.",
    "How many people are listed as CRM leads?",
    "Show all current CRM leads with the total.",
    "Check the CRM and give me the number of leads.",
    "Please retrieve the lead directory and summarize the count.",
    "How many leads do I have?",
    "What's our total lead count?",
    "  PLEASE SHOW MY LEAD DIRECTORY!  ",
    "Show the directory",
])
def test_unfiltered_questions_still_reach_the_gateway(monkeypatch, message):
    request, calls = run(monkeypatch, message=message)
    assert request().result.total == 37
    assert any(call.url.path == "/v1/chat/completions" for call in calls)


def test_filtered_question_does_not_return_the_mixed_crm_total(client, monkeypatch):
    from app import native_read
    from app.db import get_conn

    with get_conn() as conn:
        conn.execute("INSERT INTO leads(name,status) VALUES ('Synthetic Open','new')")
        conn.execute("INSERT INTO leads(name,status) VALUES ('Synthetic Closed','closed')")

    def unexpected_settings():
        pytest.fail("Unsupported scope must be rejected before configuring a gateway call")

    monkeypatch.setattr(native_read, "settings", unexpected_settings)
    response = client.post('/api/chat/directory', json={'message': 'How many closed leads?'})
    assert response.status_code == 400
    assert response.json()['error']['code'] == 'unsupported_request'
    assert 'unfiltered' in response.json()['error']['message']
    assert 'result' not in response.json()
    assert len(client.get('/api/leads').json()) == 2


def test_route_returns_safe_failure(client,monkeypatch):
    from app.routers import native_read
    async def fail(message):raise NativeReadError("gateway_unavailable")
    monkeypatch.setattr(native_read,"read_directory",fail)
    response=client.post('/api/chat/directory',json={'message':'How many leads?'})
    assert response.status_code==503
    assert response.json()['error']['code']=='gateway_unavailable'
    assert 'result' not in response.json()
    assert client.post('/api/chat/directory',json={'message':'How many leads?','operation':'delete'}).status_code==422


def test_local_transport_ignores_ambient_proxies(monkeypatch):
    monkeypatch.setenv('NATIVE_READ_GATEWAY_URL', 'http://127.0.0.1:18879')
    monkeypatch.setenv('NATIVE_READ_GATEWAY_TOKEN', 'test-only')
    def factory(**kwargs):
        assert kwargs.get('trust_env') is False
        raise RuntimeError('transport checked')
    with pytest.raises(RuntimeError, match='transport checked'):
        asyncio.run(read_directory('How many leads?', client_factory=factory))
