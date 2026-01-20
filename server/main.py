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
REPLICATE_MODEL = os.getenv("REPLICATE_MODEL", "black-forest-labs/flux-schnell")
REPLICATE_FAST_MODEL = os.getenv("REPLICATE_FAST_MODEL", "black-forest-labs/flux-schnell")
REPLICATE_QUALITY_MODEL = os.getenv("REPLICATE_QUALITY_MODEL", "black-forest-labs/flux-dev")

def resolve_model(mode: str) -> str:
    return REPLICATE_FAST_MODEL if mode == "fast" else REPLICATE_QUALITY_MODEL

CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "86400"))
RATE_LIMIT_SECONDS = float(os.getenv("RATE_LIMIT_SECONDS", "2.5"))

DME_IMAGE_RES = os.getenv("DME_IMAGE_RES", "2K").strip().upper()  # "2K" default (unchanged)

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
    raw = (
        f"{REPLICATE_MODEL}|{DME_IMAGE_RES}|"
        f"{payload.mood}|{payload.layout}|{payload.room or ''}|"
        f"{payload.venue_image_url or ''}"
    )
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
def build_prompt(mood: str, layout: str, room: Optional[str]) -> str:
    venue_lock = (
        "HIGHEST PRIORITY: Keep the exact architecture and the exact camera/view.\n"
        "Do not change walls, ceiling height, columns, doors, windows, floor edges.\n"
        "Do not change viewpoint, framing, horizon line, vanishing points, or lens/FOV.\n"
    )

    allowed_changes = (
        "ALLOWED AND ENCOURAGED:\n"
        "Apply strong event lighting, decor, furniture, florals, linens, and props.\n"
        "Make the lighting and styling the dominant visual transformation.\n"
        "The architecture and camera must remain unchanged.\n"
    )

    composition = (
        "Photorealistic event styling visualisation. High-end professional event photography. "
        "Same camera position as the reference image. Realistic materials, realistic lighting, "
        "no text, no logos, no watermark."
    )

    mood_map = {
        "Editorial": (
            "EDITORIAL MOOD\n\n"
            "Maintain the existing room architecture, layout, lighting, walls, flooring, ceiling, staging, and AV exactly as shown. Do not alter any fixed or structural elements.\n\n"
            "Only update table linens, chair covers, and table centrepieces.\n\n"
            "Style the event with a design-led, editorial aesthetic focused on intentional moments and strong visual composition. Every styling choice is deliberate, modern, and crafted to photograph beautifully.\n\n"
            "Linens: Layered, high-quality fabrics with refined texture and structure — tailored tablecloths or runners in contemporary tones (soft neutrals, stone, charcoal, or muted colour accents). Crisp edges and controlled drape that enhance line and form.\n\n"
            "Seat covers: Clean, architectural silhouettes in premium fabrics. Minimal, tailored, and sculptural — no bows, ties, or decorative excess. Colour and texture should support the overall composition, not compete with it.\n\n"
            "Table centrepieces: Statement, sculptural floral installations designed as focal points. Florals feel modern and artistic rather than traditional, using intentional form, negative space, and controlled scale. Arrangements are visually striking but refined, never cluttered or oversized.\n\n"
            "Table settings: Elevated and precise — refined tableware, layered glassware, and considered spacing that reinforces balance and symmetry.\n\n"
            "Styling emphasises layered textures, contrast, and proportion, with a contemporary, gallery-like sensibility. The overall mood is confident, modern, and editorial, created for high-impact event photography.\n\n"
            "Ultra-high resolution, photorealistic materials"
        ),
        "Luxe": (
            "LUXE MOOD\n\n"
            "Maintain the existing room architecture, lighting, layout, walls, flooring, ceiling, staging, and AV exactly as shown. Do not alter any structural or spatial elements.\n\n"
            "Only update table linens, chair covers, and table centrepieces.\n\n"
            "Style the event with a luxury aesthetic that embodies refined glamour, opulence, and comfort. The atmosphere feels lavish yet liveable — sophisticated, inviting, and timeless rather than showy.\n\n"
            "Linens: Tailored, high-quality fabrics with beautiful drape — silk-blend or premium textured linens in champagne, light metallic tones with warm undertones. Conveys elegance, subtle luxury, and a refined glow without overpowering the space.\n\n"
            "Seat covers: Elegant and minimal, using soft upholstery, velvet, or refined fabric wraps in neutral or charcoal tones. Clean lines, subtle structure, no bows or decorative ties.\n\n"
            "Table centrepieces: Sculptural. Elegant high floral arrangements with controlled form, subtle height variation, and restrained colour. Incorporate premium materials such as brushed brass vessels, smoked glass, or stone accents. No oversized, busy, or overly organic compositions.\n\n"
            "Styling balances classic elegance with modern simplicity, with subtle Art Deco–inspired geometry (soft curves, symmetry, clean lines) expressed through proportions and finishes, not overt motifs.\n\n"
            "Ultra-high resolution, photorealistic materials and lighting. No people, no branding, no text. The overall mood is confident, polished, and quietly indulgent, suitable for premium event marketing visuals."
        ),
        "Minimal": (
            "MINIMAL MOOD\n\n"
            "Maintain the existing room architecture, layout, lighting, walls, flooring, ceiling, staging, and AV exactly as shown. Do not modify any fixed or structural elements.\n\n"
            "Only update table linens, chair covers, and table centrepieces.\n\n"
            "Style the event with a clean, contemporary aesthetic that prioritises simplicity, restraint, and calm. The overall mood is modern, architectural, and grounded.\n\n"
            "Use a limited, cool-toned colour palette inspired by natural stone — muted greys, soft concrete, pale ash, and subtle charcoal accents only. Avoid warm tones or colour contrast.\n\n"
            "Ultra-high resolution, photorealistic materials and lighting."
        ),
        "Mediterranean": (
            "MEDITERRANEAN MOOD\n\n"
            "Maintain the existing room architecture, layout, lighting, walls, flooring, ceiling, staging, and AV exactly as shown. Do not alter any fixed or structural elements.\n\n"
            "Only update table linens, chair covers, and table centrepieces.\n\n"
            "Style the event with a warm, relaxed, sun-washed aesthetic inspired by coastal Southern Europe. The mood feels grounded, social, and timeless — effortless rather than styled.\n\n"
            "Use a warm, earthy colour palette inspired by natural clay, sunbaked landscapes, and subtle azure Mediterranean water tones. Colours should feel organic and softly weathered, never saturated or bold.\n\n"
            "Ultra-high resolution, photorealistic materials and lighting."
        ),
        "Manhattan": (
            "MANHATTAN MOOD\n\n"
            "Maintain the existing room architecture, layout, lighting, walls, flooring, ceiling, staging, and AV exactly as shown. Do not alter any fixed or structural elements.\n\n"
            "Only update table linens, chair covers, and table centrepieces.\n\n"
            "Style the event with a Manhattan-inspired luxury aesthetic — bold, sleek, and urban, drawing from New York luxury hotel and penthouse interiors. The mood is confident, high-energy, and polished.\n\n"
            "Use a dark, sophisticated colour palette: deep charcoal, black, rich espresso, and graphite, accented with controlled metallic highlights in brushed brass, champagne gold, or polished chrome.\n\n"
            "Ultra-high resolution, photorealistic materials."
        ),
    }

    layout_map = {
        "Cocktail": (
            "Cocktail-style event layout with curated lounge clusters, "
            "high-top tables, relaxed circulation paths, and layered decor moments."
        ),
        "Long Tables": (
            "Long banquet tables arranged in continuous runs. "
            "Layered table styling with runners, candles, florals, and refined place settings."
        ),
        "Banquet": (
            "Round banquet tables with balanced centrepieces, "
            "clear sightlines, and cohesive spacing."
        ),
        "Theatre": (
            "Theatre-style seating with refined aisle styling, "
            "intentional lighting focus toward the stage or focal area."
        )
    }

    lighting_plan = (
        "Lighting design is intentional and dominant. "
        "Use controlled event lighting rather than generic daylight. "
        "Define the mood using uplighting, warm accent lighting, "
        "and directional highlights while preserving realistic exposure."
    )

    room_line = (
        f"This design is specifically styled for the room named: {room}."
        if room else
        "This design is styled for a modern event venue interior."
    )

    negative_constraints = (
        "Do not alter architecture. No new walls, windows, doors, or ceiling features. "
        "No fisheye or wide-angle distortion. No empty or unfinished space. "
        "Avoid bland or flat lighting. No signage text."
    )

    return "\n".join([
        venue_lock,
        allowed_changes,
        composition,
        lighting_plan,
        mood_map.get(mood, mood),
        layout_map.get(layout, layout),
        room_line,
        negative_constraints
    ])

# --------------------------------------------------
# Replicate integration (raw HTTP)
# --------------------------------------------------
def replicate_generate_image_url(prompt: str, venue_image_url: Optional[str] = None) -> str:
    if not REPLICATE_API_TOKEN:
        raise RuntimeError("REPLICATE_API_TOKEN not configured")

    mode = os.getenv("REPLICATE_MODE", "fast").strip().lower()
    model = resolve_model(mode)

    if "/" not in model:
        raise RuntimeError("Resolved model must be 'owner/name'")

    owner, name = model.split("/", 1)

    create_url = f"https://api.replicate.com/v1/models/{owner}/{name}/predictions"

    headers = {
        "Authorization": f"Token {REPLICATE_API_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {"input": {"prompt": prompt}}

    # Resolution toggle (only supported by google/nano-banana-pro on Replicate)
    if model == "google/nano-banana-pro":
        payload["input"]["resolution"] = "1K" if DME_IMAGE_RES == "1K" else "2K"

    if venue_image_url:
        if model in ("google/nano-banana", "google/nano-banana-pro"):
            payload["input"]["image_input"] = [venue_image_url]
        else:
            payload["input"]["image"] = venue_image_url
            payload["input"]["prompt_strength"] = 0.6

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

    prompt = build_prompt(req.mood, req.layout, req.room)

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
