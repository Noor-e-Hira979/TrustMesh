"""Application configuration.

All secrets and environment-specific values are read from the process environment
(``.env`` is loaded when present). Nothing sensitive is ever returned to the frontend:
the only blockchain values exposed through the API are the network name, chain id,
contract address and connection status.

Referenced by: every module that needs env-driven settings.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = BACKEND_ROOT / "trustmesh.db"

#: Values that are obviously not real credentials. Treating them as configured would make
#: every AI call attempt a doomed network round-trip and slow the demo down, so they are
#: recognised and the deterministic fallback is used instead.
PLACEHOLDER_SECRETS: frozenset[str] = frozenset(
    {
        "",
        "PLACEHOLDER_KEY",
        "PLACEHOLDER",
        "CHANGEME",
        "CHANGE_ME",
        "TODO",
        "NONE",
        "NULL",
        "XXX",
        "YOUR_API_KEY",
        "YOUR_API_KEY_HERE",
        "REPLACE_ME",
    }
)


def _is_placeholder(value: str | None) -> bool:
    if value is None:
        return True
    return value.strip().upper() in PLACEHOLDER_SECRETS


class Settings(BaseSettings):
    """Runtime settings for the TrustMesh control plane.

    Environment variable aliases
    ---------------------------
    Two naming conventions are accepted for the blockchain connection, because both are in
    common use and silently ignoring one of them would leave the service in a degraded
    state that is very hard to diagnose:

        BLOCKCHAIN_RPC_URL        (documented in .env.example)
        BLOCKCHAIN_NODE_URL       (generic node terminology)

        BLOCKCHAIN_CONTRACT_ADDRESS   (documented in .env.example)
        REGISTRY_CONTRACT_ADDRESS     (contract-specific terminology)
    """

    model_config = SettingsConfigDict(
        env_file=(BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    # ------------------------------------------------------------------ app
    app_name: str = "TrustMesh"
    app_env: Literal["development", "demo", "production"] = "demo"
    debug: bool = True
    api_prefix: str = "/api"
    #: Port the API is expected to be served on. Surfaced in /api/health and used by the
    #: documented start command; uvicorn is still invoked with an explicit --port.
    port: int = Field(default=8000, ge=1, le=65535)

    # -------------------------------------------------------------- database
    database_url: str = Field(default=f"sqlite:///{DEFAULT_DB_PATH.as_posix()}")
    sql_echo: bool = False

    @field_validator("database_url")
    @classmethod
    def _resolve_relative_sqlite_path(cls, value: str) -> str:
        """Anchor a relative SQLite path to the backend directory.

        ``sqlite:///./trustmesh.db`` is relative to the *current working directory*, so
        launching the server from anywhere other than ``backend/`` would silently create a
        second, empty database. Resolving against BACKEND_ROOT makes the behaviour
        identical regardless of where the process was started from.
        """
        prefix = "sqlite:///"
        if not value.startswith(prefix):
            return value
        raw = value[len(prefix) :]
        if not raw or raw.startswith(":") or raw.startswith("/"):
            return value  # :memory:, or already absolute
        if Path(raw).is_absolute():
            return value
        resolved = (BACKEND_ROOT / raw).resolve()
        return f"{prefix}{resolved.as_posix()}"

    # ------------------------------------------------------------------- ai
    gemini_api_key: str | None = Field(default=None, repr=False)
    gemini_model: str = "gemini-2.0-flash"
    gemini_timeout_seconds: float = 20.0
    gemini_max_attempts: int = 2
    # When False the AI layer is bypassed entirely and Demo Mode is used.
    ai_enabled: bool = True

    # ----------------------------------------------------------- blockchain
    blockchain_network: str = "sepolia"
    blockchain_chain_id: int = 11155111
    # Accepts either BLOCKCHAIN_RPC_URL or BLOCKCHAIN_NODE_URL.
    blockchain_rpc_url: str | None = Field(
        default=None,
        repr=False,
        validation_alias=AliasChoices("BLOCKCHAIN_RPC_URL", "BLOCKCHAIN_NODE_URL"),
    )
    # Accepts either BLOCKCHAIN_CONTRACT_ADDRESS or REGISTRY_CONTRACT_ADDRESS.
    blockchain_contract_address: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "BLOCKCHAIN_CONTRACT_ADDRESS", "REGISTRY_CONTRACT_ADDRESS"
        ),
    )
    # A local dev chain (Hardhat) uses chain id 31337; infer it so a plain
    # BLOCKCHAIN_NODE_URL pointing at 127.0.0.1:8545 does not report a chain mismatch.
    blockchain_infer_chain_id: bool = True
    # Never logged, never serialised into an API response.
    blockchain_private_key: str | None = Field(
        default=None,
        repr=False,
        validation_alias=AliasChoices("BLOCKCHAIN_PRIVATE_KEY", "DEPLOYER_PRIVATE_KEY"),
    )
    blockchain_confirmations_required: int = 1
    blockchain_timeout_seconds: float = 30.0
    #: Ceiling used only when gas estimation fails. 220k - the usual "simple contract
    #: call" figure - is NOT enough here: TrustMeshRegistry writes a 7-field struct into
    #: both a mapping and a dynamic array, which is ~10 cold SSTOREs at 22,100 gas each
    #: before the event log, so a 220k cap reliably runs out of gas.
    blockchain_gas_limit: int = 450_000
    # "auto"      -> use RPC when configured, otherwise SIMULATED (clearly labelled)
    # "live"      -> require RPC; failure produces FAILED commitments
    # "simulated" -> never touch the network; produce deterministic SIMULATED receipts
    blockchain_mode: Literal["auto", "live", "simulated"] = "auto"
    blockchain_simulated_latency_seconds: float = 1.2

    @field_validator("blockchain_contract_address")
    @classmethod
    def _validate_address(cls, value: str | None) -> str | None:
        """Reject anything that is not a 20-byte hex address, rather than failing later
        deep inside web3 with an opaque error."""
        if value is None or not value.strip():
            return None
        text = value.strip()
        if _is_placeholder(text):
            return None
        if not text.startswith("0x") or len(text) != 42:
            raise ValueError(
                f"Contract address must be a 42-character 0x-prefixed hex string, got {text!r}"
            )
        try:
            int(text, 16)
        except ValueError as exc:
            raise ValueError(f"Contract address is not valid hex: {text!r}") from exc
        return text

    @field_validator("blockchain_rpc_url")
    @classmethod
    def _validate_rpc_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        text = value.strip()
        return None if _is_placeholder(text) else text

    # ------------------------------------------------------------- security
    # HMAC secret for credential digests. Auto-generated per-process if unset,
    # which invalidates seeded credentials on restart (by design for a demo).
    credential_hmac_secret: str | None = Field(default=None, repr=False)
    # NoDecode is required: pydantic-settings JSON-decodes complex fields from the dotenv
    # source *before* validators run, so a plain comma-separated value would raise a
    # SettingsError instead of reaching _split_origins. Without this the service fails to
    # start whenever .env contains a readable CORS_ORIGINS list.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:4173",
            "http://127.0.0.1:4173",
        ]
    )

    # ---------------------------------------------------------------- events
    event_buffer_size: int = 500
    event_heartbeat_seconds: float = 15.0
    event_persist_limit: int = 5_000

    # ----------------------------------------------------------------- demo
    demo_step_delay_seconds: float = 0.55
    reset_on_boot: bool = False

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept a comma-separated string, a JSON array, or an already-parsed list."""
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return []
            if raw.startswith("["):
                import json

                try:
                    parsed = json.loads(raw)
                except ValueError as exc:
                    raise ValueError(
                        f"CORS_ORIGINS looks like JSON but did not parse: {raw!r}"
                    ) from exc
                return [str(item).strip() for item in parsed if str(item).strip()]
            return [item.strip() for item in raw.split(",") if item.strip()]
        return value

    # ------------------------------------------------------------- helpers
    @property
    def gemini_configured(self) -> bool:
        """True only when a *real* key is present.

        A placeholder such as ``PLACEHOLDER_KEY`` must not count as configured: it would
        make every reasoning call attempt a doomed network round-trip, adding seconds of
        latency to the demo before falling back. Treating it as absent keeps Demo Mode
        fast and honest.
        """
        return bool(self.ai_enabled) and not _is_placeholder(self.gemini_api_key)

    @property
    def gemini_placeholder(self) -> bool:
        """True when a key was supplied but it is an obvious placeholder."""
        return self.ai_enabled and bool(self.gemini_api_key) and _is_placeholder(
            self.gemini_api_key
        )

    @property
    def reasoning_available(self) -> bool:
        """Whether *some* reasoning provider is usable.

        The deterministic constraint solver is a fully functional provider (spec §10), so
        the reasoning subsystem is available even without Gemini. Health reporting uses
        this so a working Demo Mode is not mislabelled as a fault.
        """
        return True

    @property
    def reasoning_mode(self) -> str:
        return "gemini" if self.gemini_configured else "demo"

    @property
    def rpc_configured(self) -> bool:
        return bool(self.blockchain_rpc_url)

    @property
    def is_local_chain(self) -> bool:
        """True when the RPC points at a loopback development node."""
        url = (self.blockchain_rpc_url or "").lower()
        return any(host in url for host in ("127.0.0.1", "localhost", "0.0.0.0", "[::1]"))

    @property
    def effective_blockchain_mode(self) -> Literal["live", "simulated"]:
        """Resolve ``auto`` into a concrete mode without touching the network."""
        if self.blockchain_mode == "simulated":
            return "simulated"
        if self.blockchain_mode == "live":
            return "live"
        return "live" if self.rpc_configured else "simulated"

    @property
    def effective_chain_id(self) -> int:
        """Chain id to report, inferring the Hardhat default for a loopback node.

        Without this, pointing ``BLOCKCHAIN_NODE_URL`` at a local node while leaving
        ``BLOCKCHAIN_CHAIN_ID`` at its public-testnet default produces a permanent
        "RPC chain id does not match configured" degradation.
        """
        if self.blockchain_infer_chain_id and self.is_local_chain:
            return 31337
        return self.blockchain_chain_id

    @property
    def credential_secret(self) -> bytes:
        secret = self.credential_hmac_secret
        if not secret:
            secret = "trustmesh-dev-credential-secret"
        return secret.encode("utf-8")

    def safe_blockchain_summary(self) -> dict[str, object]:
        """Blockchain config that is explicitly safe to expose over HTTP."""
        return {
            "network": self.blockchain_network,
            "chain_id": self.effective_chain_id,
            "configured_chain_id": self.blockchain_chain_id,
            "chain_id_inferred": self.effective_chain_id != self.blockchain_chain_id,
            "contract_address": self.blockchain_contract_address,
            "mode": self.effective_blockchain_mode,
            "configured_mode": self.blockchain_mode,
            "rpc_configured": self.rpc_configured,
            "local_chain": self.is_local_chain,
            "private_key_configured": bool(self.blockchain_private_key),
            "confirmations_required": self.blockchain_confirmations_required,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()


settings = get_settings()


def reload_settings() -> Settings:
    """Clear the cache and re-read the environment (used by tests)."""
    get_settings.cache_clear()
    os.environ.setdefault("PYTHONHASHSEED", "0")
    return get_settings()
