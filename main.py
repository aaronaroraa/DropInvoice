"""FastAPI entry point for the DropInvoice WhatsApp invoicing service."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Awaitable, Callable
from urllib.parse import parse_qsl

from dotenv import load_dotenv
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from twilio.request_validator import RequestValidator

from webhook.handler import router as webhook_router

load_dotenv()

logger = logging.getLogger("dropinvoice")
logging.basicConfig(level=logging.INFO)


@dataclass(frozen=True)
class Settings:
    """Runtime configuration loaded from environment variables."""

    app_name: str
    app_env: str
    public_base_url: str
    allowed_origins: list[str]
    twilio_auth_token: str | None


def load_settings() -> Settings:
    """Load and normalize application settings from environment variables."""

    origins = os.getenv("ALLOWED_ORIGINS", "*")
    allowed_origins = [origin.strip() for origin in origins.split(",") if origin.strip()]

    return Settings(
        app_name=os.getenv("APP_NAME", "DropInvoice"),
        app_env=os.getenv("APP_ENV", "local"),
        public_base_url=os.getenv("PUBLIC_BASE_URL", "").rstrip("/"),
        allowed_origins=allowed_origins or ["*"],
        twilio_auth_token=os.getenv("TWILIO_AUTH_TOKEN"),
    )


class TwilioSignatureValidationMiddleware:
    """Validate Twilio signatures for WhatsApp webhook requests."""

    def __init__(self, app: Callable, settings: Settings) -> None:
        """Store the downstream ASGI app and immutable settings."""

        self.app = app
        self.settings = settings

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        """Validate incoming webhook requests before they reach route handlers."""

        if scope["type"] != "http" or scope.get("path") != "/webhook":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        body = await request.body()

        if not self._is_valid_request(request, body):
            response = JSONResponse(
                {"detail": "Invalid Twilio signature"},
                status_code=status.HTTP_403_FORBIDDEN,
            )
            await response(scope, self._build_replay_receive(body), send)
            return

        await self.app(scope, self._build_replay_receive(body), send)

    def _is_valid_request(self, request: Request, body: bytes) -> bool:
        """Return True when the webhook signature matches Twilio's validator."""

        signature = request.headers.get("x-twilio-signature")
        if not signature:
            logger.warning("Rejected webhook without x-twilio-signature header")
            return False

        if not self.settings.twilio_auth_token:
            logger.error("TWILIO_AUTH_TOKEN is required for webhook validation")
            return False

        validator = RequestValidator(self.settings.twilio_auth_token)
        url = self._external_url(request)
        params = dict(parse_qsl(body.decode("utf-8"), keep_blank_values=True))

        is_valid = validator.validate(url, params, signature)
        if not is_valid:
            logger.warning("Rejected webhook with invalid Twilio signature for %s", url)

        return is_valid

    def _external_url(self, request: Request) -> str:
        """Build the public URL Twilio used to call this service."""

        if self.settings.public_base_url:
            return f"{self.settings.public_base_url}{request.url.path}"

        return str(request.url)

    @staticmethod
    def _build_replay_receive(body: bytes) -> Callable[[], Awaitable[dict]]:
        """Build a receive callable that replays the consumed request body."""

        async def receive() -> dict:
            """Return the previously consumed ASGI request payload."""

            return {"type": "http.request", "body": body, "more_body": False}

        return receive


settings = load_settings()
app = FastAPI(title=settings.app_name)

app.add_middleware(TwilioSignatureValidationMiddleware, settings=settings)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(webhook_router)


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Return a lightweight status payload for uptime checks."""

    return {
        "status": "ok",
        "service": settings.app_name,
        "environment": settings.app_env,
    }


