from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    BRIDGE_BASE: str = os.getenv("BRIDGE_BASE", "https://intervals.stas.run/gw")
    # Render injects PORT; uvicorn uses $PORT. No secrets here.


settings = Settings()
