from __future__ import annotations

from hmac import compare_digest

from fastapi import HTTPException, Request
from sqlmodel import Session

from scan64.api.models import PlayerCredential, player_token_hash


def require_player_token(
    request: Request,
    player_id: str,
    session: Session,
    expected_token_hash: str | None = None,
) -> str:
    authorization = request.headers.get("Authorization")
    scheme, separator, token = authorization.partition(" ") if authorization else ("", "", "")
    if scheme != "Bearer" or not separator or not token:
        raise HTTPException(
            status_code=401,
            detail="A player bearer token is required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token_hash = player_token_hash(token)
    if expected_token_hash is None:
        credential = session.get(PlayerCredential, player_id)
        expected_token_hash = credential.token_hash if credential else None

    if expected_token_hash is None or not compare_digest(token_hash, expected_token_hash):
        raise HTTPException(status_code=403, detail="Player bearer token does not match")

    return token_hash
