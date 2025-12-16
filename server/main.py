import os
import time
import base64
import hashlib
from typing import Optional, Dict, Tuple

from dotenv import load_dotenv
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# --------------------------------------------------
# Load environment variables from .env
# --------------------------------------------------
load_dotenv()

# --------------------------------------------------
# Config
# --------------------------------------------------
ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "*")

REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN", "")
REPLICATE_MODEL = os.getenv(
    "REPLICATE_MODEL",
    "black-forest-labs/flux-schnell"  # must be owner/name
)

CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "86400"))
RATE_LIMIT_SECONDS = float(os.getenv("RATE_LIMIT_SECONDS", "2.5"))

# --------------------------------------------------
# In-memory cache + throttle
# --------------------------------------------------
_cache: Dict[str, Tuple[float, dict]] = {}
_last_call_by_ip: Dict[str, float] = {}

# --------------------------------------------------
# App
# --------------------------------------------------
app = FastAPI(title="Design My Event API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOWED_ORIGIN] if ALLOWED_ORIGIN != "*" else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------------------------------------------------
# Models
# --------------------------------------------------
class GenerateRequest(BaseModel):
    mood: str = Field(..., min_length=2, max_length=40)
    palette: str = Field(..., min_length=2, max_length=40)
    layout: str = Field(..., min_length=2, max_length=40)
    room: Optional[str] = Field(None, max_length=80)
    venue_image_url: Optional[str] = None
    
class GenerateResponse(BaseModel):
    image_data_url: str
    prompt: str
    cache_hit: bool

# --------------------------------------------------
# Helpers
# --------------------------------------------------
def throttle(request: Request):
    ip = request.headers.get(
        "x-forwarded-for",
        request.client.host or "unknown"
    ).split(",")[0].strip()

    now = time.time()
    last = _last_call_by_ip.get(ip, 0)

    if now - last < RATE_LIMIT_SECONDS:
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please wait a moment."
        )

    _last_call_by_ip[ip] = now

def cache_key(payload: GenerateRequest) -> str:
    raw = f"{payload.mood}|{payload.palette}|{payload.layout}|{payload.room or ''}"
    return hashlib.sha256(raw.lower().encode("utf-8")).hexdigest()

def get_cached(key: str) -> Optional[dict]:
    item = _cache.get(key)
    if not item:
        return None

    created_ts, value = item
    if time.time() - created_ts > CACHE_TTL_SECONDS:
        _cache.pop(key, None)
        return None

    return value

def set_cached(key: str, value: dict):
    _cache[key] = (time.time(), value)

# --------------------------------------------------
# Prompt builder
# --------------------------------------------------
def build_prompt(mood: str, palette: str, layout: str, room: Optional[str]) -> str:
    base = (
        "Photoreal event styling moodboard. Bright, airy, daylight-balanced lighting. "
        "Premium event design, realistic venue materials, no text, no logos, no watermark."
    )

    mood_map = {
        "Editorial": "Editorial styling, high-end magazine look, crisp composition.",
        "Luxe": "Luxe styling, layered linens, refined textures, elegant tableware.",
        "Minimal": "Minimal styling, clean lines, negative space, calm sophistication.",
        "Mediterranean": "Mediterranean styling, sun-warmed textures, relaxed elegance.",
        "Manhattan": "Manhattan styling, modern architecture, polished details."
    }

    palette_map = {
        "Terracotta": "Terracotta, warm sand, clay accents, soft brass.",
        "Champagne": "Champagne, ivory, warm whites, soft gold.",
        "Slate": "Slate grey, cool stone, airy contrast.",
        "Coastal Neutral": "Driftwood, sand, linen white, warm greys."
    }

    layout_map = {
        "Cocktail": "Cocktail layout, lounge clusters, relaxed mingling.",
        "Long Tables": "Long tables, continuous runs, layered centre styling.",
        "Banquet": "Round banquet tables, balanced centrepieces.",
        "Theatre": "Theatre seating, refined aisle moments."
    }

    room_line = (
        f"Designed for the venue room: {room}."
        if room else
        "Designed for a modern event venue."
    )

    return "\n".join([
        base,
        mood_map.get(mood, mood),
        palette_map.get(palette, palette),
        layout_map.get(layout, layout),
        room_line,
    ])

# --------------------------------------------------
# Replicate integration (raw HTTP)
# --------------------------------------------------
def replicate_generate_image_url(prompt: str, venue_image_url: Optional[str] = None) -> str:
    if not REPLICATE_API_TOKEN:
        raise RuntimeError("REPLICATE_API_TOKEN not configured")

    if "/" not in REPLICATE_MODEL:
        raise RuntimeError("REPLICATE_MODEL must be 'owner/name'")

    owner, name = REPLICATE_MODEL.split("/", 1)

    create_url = f"https://api.replicate.com/v1/models/{owner}/{name}/predictions"

    headers = {
        "Authorization": f"Token {REPLICATE_API_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {"input": {"prompt": prompt}}

    if venue_image_url:
        payload["input"]["image"] = venue_image_url
        payload["input"]["prompt_strength"] = 0.8

    with httpx.Client(timeout=120.0) as client:
        r = client.post(create_url, headers=headers, json=payload)
        r.raise_for_status()
        pred = r.json()

        poll_url = pred.get("urls", {}).get("get")
        if not poll_url:
            raise RuntimeError("Replicate response missing poll URL")

        for _ in range(240):
            g = client.get(poll_url, headers=headers)
            g.raise_for_status()
            data = g.json()

            status = data.get("status")
            if status == "succeeded":
                output = data.get("output")
                if isinstance(output, list) and output:
                    return output[0]
                if isinstance(output, str):
                    return output
                raise RuntimeError("Unexpected Replicate output")

            if status in ("failed", "canceled"):
                raise RuntimeError(data.get("error", "Replicate failed"))

            time.sleep(0.75)

        raise RuntimeError("Replicate request timed out")




def download_image_as_data_url(url: str) -> str:
    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        r = client.get(url)
        r.raise_for_status()
        mime = r.headers.get("content-type", "image/png")
        b64 = base64.b64encode(r.content).decode("utf-8")
        return f"data:{mime};base64,{b64}"

# --------------------------------------------------
# Routes
# --------------------------------------------------
@app.get("/api/health")
def health():
    return {"ok": True}

@app.post("/api/generate", response_model=GenerateResponse)
def generate(req: GenerateRequest, request: Request):
    throttle(request)

    key = cache_key(req)
    cached = get_cached(key)
    if cached:
        return GenerateResponse(**cached, cache_hit=True)

    prompt = build_prompt(req.mood, req.palette, req.layout, req.room)

    try:
        image_url = replicate_generate_image_url(prompt, req.venue_image_url)
        data_url = download_image_as_data_url(image_url)

        resp = {
            "image_data_url": data_url,
            "prompt": prompt,
        }

        set_cached(key, resp)
        return GenerateResponse(**resp, cache_hit=False)

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Image generation failed: {e}"
        )
