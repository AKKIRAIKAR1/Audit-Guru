import time

from sqlalchemy import update

from app.auth import COOKIE, LoginSession, password_hash
from app.config import settings
from app.db import Session

HEADERS = {'X-Requested-With': 'LedgerLens'}


def configure(monkeypatch):
    monkeypatch.setattr(settings(), 'admin_username', 'Admin')
    monkeypatch.setattr(settings(), 'admin_password_hash', password_hash('OmegaXL9'))
    monkeypatch.setattr(settings(), 'admin_organization', 'org-a')
    monkeypatch.setattr(settings(), 'api_keys', {})


def login(client, password='OmegaXL9'):
    return client.post('/api/v1/auth/login', headers=HEADERS,
                       json={'username': 'Admin', 'password': password})


def test_login_session_logout_and_revocation(client, monkeypatch):
    configure(monkeypatch)
    assert client.get('/api/v1/documents').status_code == 401
    assert client.get('/api/v1/documents', headers={'X-API-Key':'change-this-development-key'}).status_code == 401
    assert login(client, 'wrong').status_code == 401
    response = login(client)
    assert response.status_code == 200
    assert 'HttpOnly' in response.headers['set-cookie']
    assert 'SameSite=strict' in response.headers['set-cookie']
    token = client.cookies.get(COOKIE)
    assert client.get('/api/v1/auth/session').json() == {'username':'Admin'}
    assert client.get('/api/v1/documents').status_code == 200
    assert client.post('/api/v1/auth/logout', headers=HEADERS).status_code == 200
    assert client.get('/api/v1/documents').status_code == 401
    client.cookies.set(COOKIE, token)
    assert client.get('/api/v1/documents').status_code == 401


def test_expired_and_changed_password_sessions(client, monkeypatch):
    configure(monkeypatch)
    login(client)
    with Session.begin() as db:
        db.execute(update(LoginSession).values(expires_at=time.time()-1))
    assert client.get('/api/v1/documents').status_code == 401
    login(client)
    monkeypatch.setattr(settings(), 'admin_password_hash', password_hash('different-password'))
    assert client.get('/api/v1/documents').status_code == 401


def test_csrf_and_password_not_exposed(client, monkeypatch):
    configure(monkeypatch)
    body = {'username':'Admin', 'password':'OmegaXL9'}
    assert client.post('/api/v1/auth/login', json=body).status_code == 403
    assert client.post('/api/v1/auth/login', json=body, headers={**HEADERS,'Origin':'https://foreign.example'}).status_code == 403
    login(client)
    assert client.post('/api/v1/ocr/process', json={'document_id':'missing'}).status_code == 403
    for path in ['/', '/static/app.js', '/openapi.json']:
        response = client.get(path)
        assert 'OmegaXL9' not in response.text
    assert 'Organization API key' not in client.get('/').text
