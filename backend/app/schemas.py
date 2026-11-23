from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class ClientPlatform(StrEnum):
    ANDROID = "android"
    ANDROID_TV = "android_tv"
    IOS = "ios"
    WINDOWS = "windows"
    TIZEN = "tizen"
    WEBOS = "webos"


NATIVE_TV_PLATFORMS = frozenset({ClientPlatform.TIZEN, ClientPlatform.WEBOS})


class ResolveCode(StrEnum):
    SUCCESS = "SUCCESS"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    EXPIRED = "EXPIRED"
    DISABLED = "DISABLED"
    DNS_UNREACHABLE = "DNS_UNREACHABLE"
    UPSTREAM_ERROR = "UPSTREAM_ERROR"


class ResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str
    password: SecretStr
    platform: ClientPlatform = ClientPlatform.ANDROID


class ResolveResponse(BaseModel):
    code: ResolveCode
    session_token: str | None = None
    session_expires_at: datetime | None = None
    xtream_base_url: str | None = None
    account_expires_at: datetime | None = None


class EntitlementState(StrEnum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    DISABLED = "DISABLED"
    UNAVAILABLE = "UNAVAILABLE"


class EntitlementCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=128)


class EntitlementCheckResponse(BaseModel):
    state: EntitlementState


class CatalogKind(StrEnum):
    LIVE = "live"
    VOD = "vod"
    SERIES = "series"


class CatalogListRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: CatalogKind
    platform: ClientPlatform = ClientPlatform.ANDROID


class CatalogCategoryItemsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: CatalogKind
    category_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    platform: ClientPlatform = ClientPlatform.ANDROID


class CatalogCategory(BaseModel):
    id: str
    name: str


class CatalogItem(BaseModel):
    id: str
    title: str
    category_id: str | None = None
    container_extension: str | None = None


class CatalogListResponse(BaseModel):
    kind: CatalogKind
    categories: list[CatalogCategory]
    items: list[CatalogItem]


class SeriesDetailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    series_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    platform: ClientPlatform = ClientPlatform.ANDROID


class EpisodeInfo(BaseModel):
    id: str
    season_id: str
    title: str
    episode_number: str | None = None
    container_extension: str | None = None


class SeasonInfo(BaseModel):
    id: str
    title: str


class SeriesDetailResponse(BaseModel):
    series_id: str
    title: str | None = None
    seasons: list[SeasonInfo]
    episodes: list[EpisodeInfo]


class EpgRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stream_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    limit: int = Field(default=10, ge=1, le=20)
    platform: ClientPlatform = ClientPlatform.ANDROID


class EpgProgramme(BaseModel):
    title: str
    start_timestamp: int | None = None
    end_timestamp: int | None = None


class EpgResponse(BaseModel):
    stream_id: str
    programmes: list[EpgProgramme]
