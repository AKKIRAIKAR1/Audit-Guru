import hashlib
import hmac
import secrets
import time

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete

from app.config import settings
from app.db import Base, Session
from sqlalchemy import Float, String
from sqlalchemy.orm import Mapped, mapped_column

router = APIRouter(prefix='/api/v1/auth', tags=['Authentication'])
COOKIE = 'ledgerlens_session'


class LoginSession(Base):
    __tablename__ = 'login_sessions'
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(160))
    expires_at: Mapped[float] = mapped_column(Float, index=True)
    credential_fingerprint: Mapped[str] = mapped_column(String(64))


def password_hash(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 600_000).hex()
    return f'pbkdf2_sha256$600000${salt}${digest}'


def verify_password(password, encoded):
    try:
        algorithm, rounds, salt, expected = encoded.split('$')
        if algorithm != 'pbkdf2_sha256':
            return False
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), int(rounds)).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def fingerprint():
    cfg = settings()
    return hashlib.sha256(f'{cfg.admin_username}:{cfg.admin_password_hash}:{cfg.admin_organization}'.encode()).hexdigest()


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def check_origin(request):
    # Same-origin custom header plus SameSite cookies protects state-changing requests.
    if request.headers.get('x-requested-with') != 'LedgerLens':
        raise HTTPException(403, 'Invalid request origin.')
    origin = request.headers.get('origin')
    if origin and origin != str(request.base_url).rstrip('/'):
        raise HTTPException(403, 'Invalid request origin.')


def session_tenant(request):
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        check_origin(request)
    with Session() as db:
        session = db.get(LoginSession, token_hash(token))
        if session and session.expires_at > time.time() and session.credential_fingerprint == fingerprint():
            return session.organization_id
    return None


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=256)


@router.post('/login')
def login(body: LoginRequest, request: Request, response: Response):
    check_origin(request)
    cfg = settings()
    valid_password = verify_password(body.password, cfg.admin_password_hash)
    valid_name = hmac.compare_digest(body.username.encode(), cfg.admin_username.encode())
    if not valid_password or not valid_name:
        raise HTTPException(401, 'Incorrect name or password.')
    token = secrets.token_urlsafe(32)
    with Session.begin() as db:
        db.execute(delete(LoginSession).where(LoginSession.expires_at <= time.time()))
        previous = request.cookies.get(COOKIE)
        if previous:
            db.execute(delete(LoginSession).where(LoginSession.token_hash == token_hash(previous)))
        db.add(LoginSession(token_hash=token_hash(token), organization_id=cfg.admin_organization,
                            expires_at=time.time() + cfg.session_hours * 3600, credential_fingerprint=fingerprint()))
    response.set_cookie(COOKIE, token, httponly=True, samesite='strict', secure=cfg.session_cookie_secure,
                        max_age=cfg.session_hours * 3600, path='/')
    return {'username': cfg.admin_username}


@router.get('/session')
def current_session(request: Request):
    if not session_tenant(request):
        raise HTTPException(401, 'Please sign in.')
    return {'username': settings().admin_username}


@router.post('/logout')
def logout(request: Request, response: Response):
    check_origin(request)
    token = request.cookies.get(COOKIE)
    if token:
        with Session.begin() as db:
            db.execute(delete(LoginSession).where(LoginSession.token_hash == token_hash(token)))
    response.delete_cookie(COOKIE, path='/', secure=settings().session_cookie_secure, httponly=True, samesite='strict')
    return {'status': 'signed_out'}
