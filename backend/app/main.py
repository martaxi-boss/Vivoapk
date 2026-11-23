"""Vivoapk: Mega OTT login control plane, never an IPTV restreamer."""
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.auth_rate_limit import charge_auth_attempt
from app.clients.catalog import CatalogUnavailable, XtreamCatalogClient
from app.clients.mega import MegaOTTClient
from app.clients.xtream import XtreamClient
from app.config import Settings, get_settings
from app.db import build_session_factory, get_db
from app.logging_config import configure_logging
from app.schemas import (
    CatalogListRequest,
    CatalogListResponse,
    EpgRequest,
    EpgResponse,
    SeriesDetailRequest,
    SeriesDetailResponse,
    ResolveRequest,
    ResolveResponse,
)
from app.services.catalog import CatalogDenied, CatalogService
from app.services.session import SessionResolver


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime_settings = settings or get_settings()
    configure_logging()
    app = FastAPI(title="Vivoapk Mega OTT Resolver", version="0.1.0")
    app.state.settings = runtime_settings
    app.state.session_factory = build_session_factory(runtime_settings.database_url)
    app.state.xtream_client_factory = XtreamClient
    app.state.catalog_client_factory = XtreamCatalogClient
    app.state.mega_client_factory = lambda: MegaOTTClient(
        base_url=runtime_settings.mega_ott_api_base,
        token=runtime_settings.require_mega_token(),
    )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, _error: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422, content={"detail": "Invalid request"},
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/v1/session/resolve", response_model=ResolveResponse)
    def resolve_session(
        payload: ResolveRequest, response: Response, request: Request,
        session: Session = Depends(get_db),
    ) -> ResolveResponse:
        response.headers["Cache-Control"] = "no-store"
        charge_auth_attempt(session, runtime_settings, payload.username)
        xtream_client: XtreamClient = request.app.state.xtream_client_factory()
        try:
            return SessionResolver(
                session, xtream_client, runtime_settings,
                mega_client_factory=request.app.state.mega_client_factory,
            ).resolve(
                username=payload.username,
                password=payload.password.get_secret_value(),
                platform=payload.platform,
            )
        finally:
            close = getattr(xtream_client, "close", None)
            if callable(close):
                close()

    @app.post("/v1/catalog/list", response_model=CatalogListResponse)
    def list_catalogue(
        payload: CatalogListRequest, response: Response, request: Request,
        authorization: str | None = Header(default=None),
        session: Session = Depends(get_db),
    ) -> CatalogListResponse:
        response.headers["Cache-Control"] = "no-store"
        if (authorization is None or not authorization.startswith("Bearer ")
                or len(authorization) > 4120):
            raise HTTPException(
                status_code=401, detail="Unauthorized",
                headers={"Cache-Control": "no-store"},
            )
        token = authorization[7:]
        try:
            return CatalogService(
                session, runtime_settings,
                mega_client_factory=request.app.state.mega_client_factory,
                xtream_client_factory=request.app.state.xtream_client_factory,
                catalog_client_factory=request.app.state.catalog_client_factory,
            ).list(token=token, kind=payload.kind, platform=payload.platform)
        except CatalogDenied:
            raise HTTPException(
                status_code=401, detail="Unauthorized",
                headers={"Cache-Control": "no-store"},
            ) from None
        except CatalogUnavailable:
            raise HTTPException(
                status_code=503, detail="Catalogue temporarily unavailable",
                headers={"Cache-Control": "no-store"},
            ) from None


    @app.post("/v1/catalog/series", response_model=SeriesDetailResponse)
    def read_series_detail(
        payload: SeriesDetailRequest, response: Response, request: Request,
        authorization: str | None = Header(default=None),
        session: Session = Depends(get_db),
    ) -> SeriesDetailResponse:
        response.headers["Cache-Control"] = "no-store"
        if (authorization is None or not authorization.startswith("Bearer ")
                or len(authorization) > 4120):
            raise HTTPException(
                status_code=401, detail="Unauthorized",
                headers={"Cache-Control": "no-store"},
            )
        try:
            return CatalogService(
                session, runtime_settings,
                mega_client_factory=request.app.state.mega_client_factory,
                xtream_client_factory=request.app.state.xtream_client_factory,
                catalog_client_factory=request.app.state.catalog_client_factory,
            ).series_detail(
                token=authorization[7:], platform=payload.platform, series_id=payload.series_id
            )
        except CatalogDenied:
            raise HTTPException(
                status_code=401, detail="Unauthorized",
                headers={"Cache-Control": "no-store"},
            ) from None
        except CatalogUnavailable:
            raise HTTPException(
                status_code=503, detail="Catalogue temporarily unavailable",
                headers={"Cache-Control": "no-store"},
            ) from None

    @app.post("/v1/catalog/epg", response_model=EpgResponse)
    def read_epg(
        payload: EpgRequest, response: Response, request: Request,
        authorization: str | None = Header(default=None),
        session: Session = Depends(get_db),
    ) -> EpgResponse:
        response.headers["Cache-Control"] = "no-store"
        if (authorization is None or not authorization.startswith("Bearer ")
                or len(authorization) > 4120):
            raise HTTPException(
                status_code=401, detail="Unauthorized",
                headers={"Cache-Control": "no-store"},
            )
        try:
            return CatalogService(
                session, runtime_settings,
                mega_client_factory=request.app.state.mega_client_factory,
                xtream_client_factory=request.app.state.xtream_client_factory,
                catalog_client_factory=request.app.state.catalog_client_factory,
            ).short_epg(
                token=authorization[7:], platform=payload.platform,
                stream_id=payload.stream_id, limit=payload.limit
            )
        except CatalogDenied:
            raise HTTPException(
                status_code=401, detail="Unauthorized",
                headers={"Cache-Control": "no-store"},
            ) from None
        except CatalogUnavailable:
            raise HTTPException(
                status_code=503, detail="Catalogue temporarily unavailable",
                headers={"Cache-Control": "no-store"},
            ) from None

    return app


app = create_app()
