





def test_create_call_record(client):
    response = client.post(
        "/call-records",
        json={
            "external_call_id": "test123",
            "direction": "inbound",
            "disposition": "answered_by_ai",
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["accepted"] is True
    assert data["external_call_id"] == "test123"


def test_duplicate_call_record_returns_409(client):
    payload = {
        "external_call_id": "test123",
        "direction": "inbound",
        "disposition": "answered_by_ai",
    }
    # First creation should succeed
    response1 = client.post("/call-records", json=payload)
    response2 = client.post("/call-records", json=payload)

    assert response1.status_code == 201
    assert response2.status_code == 409

def test_get_call_record_by_external_id(client):
    payload = {
        "external_call_id": "test123",
        "direction": "inbound",
        "disposition": "answered_by_ai",
    }
    client.post("/call-records", json=payload)

    response = client.get("/call-records/test123")
    assert response.status_code == 200
    data = response.json()
    assert data["external_call_id"] == "test123"
    assert data["direction"] == "inbound"
    assert data["disposition"] == "answered_by_ai"



def test_get_missing_call_record_returns_404(client):
    response = client.get("/call-records/nonexistent")
    assert response.status_code == 404


def test_list_call_records_can_filter_by_direction(client):
    payload1 = {
        "external_call_id": "test123",
        "direction": "inbound",
        "disposition": "answered_by_ai",
    }
    payload2 = {
        "external_call_id": "test456",
        "direction": "outbound",
        "disposition": "no_answer",
    }
    client.post("/call-records", json=payload1)
    client.post("/call-records", json=payload2)

    response1 = client.get("/call-records?direction=inbound")
    response2 = client.get("/call-records?direction=outbound")

    assert response1.status_code == 200
    assert response2.status_code == 200
    data1 = response1.json()
    data2 = response2.json()
    assert len(data1) == 1
    assert len(data2) == 1
    assert data1[0]["external_call_id"] == "test123"
    assert data2[0]["external_call_id"] == "test456"

def test_list_call_records_can_filter_by_disposition(client):
    payload1 = {
        "external_call_id": "test123",
        "direction": "inbound",
        "disposition": "answered_by_ai",
    }
    payload2 = {
        "external_call_id": "test456",
        "direction": "outbound",
        "disposition": "no_answer",
    }
    client.post("/call-records", json=payload1)
    client.post("/call-records", json=payload2)

    response1 = client.get("/call-records?disposition=answered_by_ai")
    response2 = client.get("/call-records?disposition=no_answer")

    assert response1.status_code == 200
    assert response2.status_code == 200
    data1 = response1.json()
    data2 = response2.json()
    assert len(data1) == 1
    assert len(data2) == 1
    assert data1[0]["external_call_id"] == "test123"
    assert data2[0]["external_call_id"] == "test456"

def test_list_call_records_rejects_invalid_direction(client):
    response = client.get("/call-records?direction=invalid_direction")
    assert response.status_code == 422


def test_list_call_records_rejects_invalid_disposition(client):
    response = client.get("/call-records?disposition=invalid_disposition")
    assert response.status_code == 422


def test_list_call_records_supports_limit(client):
    for index in range(10):
        client.post(
            "/call-records",
            json={
                "external_call_id": f"call_record_{index}",
                "direction": "inbound",
                "disposition": "answered_by_ai",
            },
        )

    response = client.get("/call-records?limit=10")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 10


def test_list_call_records_supports_offset(client):
    for index in range(10):
        client.post(
            "/call-records",
            json={
                "external_call_id": f"call_record_{index}",
                "direction": "inbound",
                "disposition": "answered_by_ai",
            },
        )
    response = client.get("/call-records?offset=7")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 3



def test_list_call_records_supports_limit_and_offset(client):
    for index in range(10):
        client.post(
            "/call-records",
            json={
                "external_call_id": f"call_record_{index}",
                "direction": "inbound",
                "disposition": "answered_by_ai",
            },
        )
    response = client.get("/call-records?limit=3&offset=5")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 3
    assert data[0]["external_call_id"] == "call_record_4"
    assert data[1]["external_call_id"] == "call_record_3"
    assert data[2]["external_call_id"] == "call_record_2"


def test_list_call_records_rejects_invalid_limit(client):
    response = client.get("/call-records?limit=0")
    assert response.status_code == 422

    response = client.get("/call-records?limit=101")
    assert response.status_code == 422           

def test_list_call_records_can_filter_by_tool_name_and_campaign_name(client):
    payload1 = {
        "external_call_id": "test123",
        "direction": "inbound",
        "disposition": "answered_by_ai",
        "tool_name": "FOT",
        "campaign_name": "Graduate Test"
    }
    payload2 = {
        "external_call_id": "test456",
        "direction": "inbound",
        "disposition": "answered_by_ai",
        "tool_name": "Scholastica",
        "campaign_name": "Scholarship Test"
    }
    client.post("/call-records", json=payload1)
    client.post("/call-records", json=payload2)

    response1 = client.get("/call-records?tool_name=FOT&campaign_name=Graduate Test")
    response2 = client.get("/call-records?tool_name=Scholastica&campaign_name=Scholarship Test")

    assert response1.status_code == 200
    assert response2.status_code == 200
    data1 = response1.json()
    data2 = response2.json()
    assert len(data1) == 1
    assert len(data2) == 1
    assert data1[0]["external_call_id"] == "test123"
    assert data2[0]["external_call_id"] == "test456"


def test_create_call_record_rejects_invalid_tool_name(client):
    response = client.post(
        "/call-records",
        json={
            "external_call_id": "test789",
            "direction": "inbound",
            "disposition": "answered_by_ai",
            "tool_name": "InvalidTool"
        },
    )
    assert response.status_code == 422