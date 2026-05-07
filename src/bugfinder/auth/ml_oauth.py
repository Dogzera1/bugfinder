"""
OAuth2 para Mercado Livre — suporta authorization_code + refresh_token.

A API pública ML restringiu /search a tokens de USUÁRIO (authorization_code).
Tokens de app (client_credentials) só funcionam em endpoints limitados.

Fluxo authorization_code:
  1. usuário visita auth_url no browser, autoriza app
  2. ML redireciona pra redirect_uri com ?code=...
  3. trocamos code por access_token + refresh_token
  4. quando access_token expira (6h), usamos refresh_token pra renovar

client_credentials continua suportado como fallback pra endpoints que
aceitam app token (ex: /sites/MLB/categories), mas NÃO funciona pra search.
"""
from __future__ import annotations

import json
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

ML_AUTH_BASE = "https://auth.mercadolivre.com.br/authorization"
ML_TOKEN_URL = "https://api.mercadolibre.com/oauth/token"
REFRESH_MARGIN_S = 60


class NoMLCredentialsError(RuntimeError):
    """ML_CLIENT_ID/SECRET ausentes — o sistema deve degradar graciosamente."""


class NoUserTokenError(RuntimeError):
    """Não há user token cacheado — usuário precisa rodar `bugfinder ml-auth`."""


class MLAuthError(RuntimeError):
    """Falha de autenticação no ML."""


@dataclass
class _TokenState:
    access_token: str
    expires_at: float
    refresh_token: str | None = None
    auth_method: str = "client_credentials"   # ou "authorization_code"
    user_id: int | None = None
    scope: str | None = None

    def is_valid(self) -> bool:
        return time.time() < self.expires_at - REFRESH_MARGIN_S


class MLOAuthClient:
    def __init__(self, client_id: str | None, client_secret: str | None,
                 cache_path: Path | str | None = None,
                 timeout: float = 15.0,
                 prefer_user_token: bool = True,
                 seed_refresh_token: str | None = None) -> None:
        """
        seed_refresh_token: usado na primeira inicialização em ambientes onde
        não há cache (ex: deploy fresh em Railway). Se cache não existe, faz um
        refresh imediato com esse token e popula o cache.
        """
        if not client_id or not client_secret:
            raise NoMLCredentialsError(
                "ML_CLIENT_ID e ML_CLIENT_SECRET não estão configurados no .env. "
                "Cadastre uma app em https://developers.mercadolivre.com.br."
            )
        self.client_id = client_id
        self.client_secret = client_secret
        self.timeout = timeout
        self.cache_path = Path(cache_path) if cache_path else None
        self.prefer_user_token = prefer_user_token
        self._state: _TokenState | None = self._load_cache()

        # bootstrap em ambiente fresh (ex: container Railway novo)
        if not self._state and seed_refresh_token:
            try:
                self._state = self._refresh(seed_refresh_token)
                self._save_cache(self._state)
            except MLAuthError:
                # seed inválido — segue sem token, vai cair no NoUserTokenError
                pass

    # ---- token lifecycle ----

    def get_access_token(self) -> str:
        """
        Devolve o token vigente. Renova via refresh_token quando expirado.
        Se não há user token e prefer_user_token=True, levanta NoUserTokenError
        sugerindo `bugfinder ml-auth`.
        """
        if self._state and self._state.is_valid():
            return self._state.access_token

        # tenta refresh
        if self._state and self._state.refresh_token:
            try:
                self._state = self._refresh(self._state.refresh_token)
                self._save_cache(self._state)
                return self._state.access_token
            except MLAuthError:
                # refresh falhou — limpa cache e cai no fluxo de fallback
                self._state = None

        # sem state válido: pergunta o que fazer baseado em prefer_user_token
        if self.prefer_user_token:
            raise NoUserTokenError(
                "Sem token de usuário válido. Rode "
                "`bugfinder ml-auth` pra autenticar via browser uma vez."
            )

        # client_credentials como fallback
        self._state = self._fetch_client_credentials()
        self._save_cache(self._state)
        return self._state.access_token

    def get_app_token(self) -> str:
        """Força token de app (client_credentials), independente de user token."""
        # cache separado não — pra simplicidade, faz sempre fresh
        st = self._fetch_client_credentials()
        return st.access_token

    # ---- authorization_code flow ----

    def build_auth_url(self, redirect_uri: str, scope: str = "read") -> str:
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "scope": scope,
        }
        return f"{ML_AUTH_BASE}?{urllib.parse.urlencode(params)}"

    def exchange_code(self, code: str, redirect_uri: str) -> _TokenState:
        """Troca o ?code= recebido no callback por access+refresh tokens."""
        try:
            r = httpx.post(
                ML_TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
                headers={"Accept": "application/json"},
                timeout=self.timeout,
            )
        except httpx.TransportError as e:
            raise MLAuthError(f"Falha de rede: {e}") from e
        if r.status_code != 200:
            raise MLAuthError(
                f"Troca de code falhou ({r.status_code}): {r.text[:300]}"
            )
        data = r.json()
        state = _TokenState(
            access_token=data["access_token"],
            expires_at=time.time() + int(data.get("expires_in") or 21600),
            refresh_token=data.get("refresh_token"),
            auth_method="authorization_code",
            user_id=data.get("user_id"),
            scope=data.get("scope"),
        )
        self._state = state
        self._save_cache(state)
        return state

    def _refresh(self, refresh_token: str) -> _TokenState:
        try:
            r = httpx.post(
                ML_TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": refresh_token,
                },
                headers={"Accept": "application/json"},
                timeout=self.timeout,
            )
        except httpx.TransportError as e:
            raise MLAuthError(f"Falha de rede no refresh: {e}") from e
        if r.status_code != 200:
            raise MLAuthError(
                f"Refresh falhou ({r.status_code}): {r.text[:300]}"
            )
        data = r.json()
        return _TokenState(
            access_token=data["access_token"],
            expires_at=time.time() + int(data.get("expires_in") or 21600),
            # ML pode rotacionar o refresh_token; usa o novo se vier
            refresh_token=data.get("refresh_token") or refresh_token,
            auth_method="authorization_code",
            user_id=data.get("user_id"),
            scope=data.get("scope"),
        )

    # ---- client_credentials flow ----

    def _fetch_client_credentials(self) -> _TokenState:
        try:
            r = httpx.post(
                ML_TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
                headers={"Accept": "application/json"},
                timeout=self.timeout,
            )
        except httpx.TransportError as e:
            raise MLAuthError(f"Falha de rede: {e}") from e
        if r.status_code != 200:
            raise MLAuthError(
                f"client_credentials falhou ({r.status_code}): {r.text[:300]}"
            )
        data = r.json()
        return _TokenState(
            access_token=data["access_token"],
            expires_at=time.time() + int(data.get("expires_in") or 21600),
            auth_method="client_credentials",
        )

    # ---- cache ----

    def _load_cache(self) -> _TokenState | None:
        if not self.cache_path or not self.cache_path.exists():
            return None
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            return _TokenState(
                access_token=data["access_token"],
                expires_at=float(data["expires_at"]),
                refresh_token=data.get("refresh_token"),
                auth_method=data.get("auth_method", "client_credentials"),
                user_id=data.get("user_id"),
                scope=data.get("scope"),
            )
        except Exception:
            return None

    def _save_cache(self, state: _TokenState) -> None:
        if not self.cache_path:
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(
                json.dumps({
                    "access_token": state.access_token,
                    "expires_at": state.expires_at,
                    "refresh_token": state.refresh_token,
                    "auth_method": state.auth_method,
                    "user_id": state.user_id,
                    "scope": state.scope,
                }),
                encoding="utf-8",
            )
        except Exception:
            pass
