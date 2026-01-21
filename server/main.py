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
    av_equipment: Optional[str] = Field(None, max_length=10)
    uplighting_colour: Optional[str] = Field(None, max_length=20)

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
        f"{payload.venue_image_url or ''}|"
        f"{payload.av_equipment or ''}"
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
# Designer negative prompt reference (NOT ACTIVE YET)
# --------------------------------------------------

DESIGNER_NEGATIVE_PROMPTS = {
    "global": """
Do not change the base image
Do not alter room dimensions, walls, ceiling, floor, finishes, or architectural details
Do not change camera position, angle, framing, zoom, or perspective
No missing attendees, empty seats, or sparse areas
No standing attendees, no people facing or looking at the camera
No casual, sloppy, or informal clothing
No bows, chair ties, sashes, or decorative excess
No traditional, cluttered, small scale, or floral-heavy centrepieces
No mismatched table styling or inconsistent centrepieces
No curtains, drapes, blinds, frosting, decals, props, or window obstructions
No changes to exterior view, skyline, water, buildings, weather, or time of day
No additional audio visual equipment
No alternative stage layout, size, colour, or position
No additional lecterns or presenters
No change to LED wall size, placement, colour, or displayed text
No dramatic lighting effects, no coloured spotlights, no haze or fog
No flat lighting or high contrast stylisation
No low resolution, illustrative, stylised, cartoon, or CGI appearance
In no circumstances is the bottom row of the LED wall ever to be lifted above stage level. The LED wall in every instance must start at stage level.
""".strip(),

    "layout": {
        "Theatre": """
No tables
No centrepieces
No floral arrangements
No vases
No candles
No tabletop styling elements
No decorative plants
No uplighting applied to plants
""".strip()
    }
}

# --------------------------------------------------
# Negative prompt builder (NOT WIRED YET)
# --------------------------------------------------

def _np_split_lines(text: str) -> list[str]:
    if not text:
        return []
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def _np_dedupe_keep_order(items: list[str]) -> list[str]:
    seen = set()
    out = []
    for it in items:
        if it not in seen:
            seen.add(it)
            out.append(it)
    return out


def build_designer_negative_prompt(*, layout: str | None = None) -> str:
    """
    Builds a single negative prompt string from DESIGNER_NEGATIVE_PROMPTS.
    Currently supports:
      - global
      - layout-specific (e.g. Theatre)
    """
    parts: list[str] = []
    parts += _np_split_lines(DESIGNER_NEGATIVE_PROMPTS.get("global", ""))

    if layout:
        parts += _np_split_lines(DESIGNER_NEGATIVE_PROMPTS.get("layout", {}).get(layout, ""))

    return "\n".join(_np_dedupe_keep_order(parts)).strip()

AV_EQUIPMENT_PROMPTS = {
"IN": """
STAGE AND PRESENTER

At the far end of the room, centred in the image, is a clean and minimal black stage.
The stage is four point eight metres wide and two point four metres deep.
The stage brand is Megadeck and the stage is three hundred millimetres high.
The stage has a black stage skirt on all visible sides.
There is a single black tread providing access to the stage.

A single black Lectrum lectern is placed stage right from the perspective of the camera.
A single female presenter is standing at the lectern speaking.
She is dressed in smart casual attire.
She does not look at the camera.


LED WALL

Behind the stage is a single LED wall.
The LED wall is five metres wide and three metres high.
The LED wall starts at stage level with no gap.
The LED wall displays a solid white background with hex colour #ffffff.
The LED wall displays black text reading “AIME 2026”.
        

AUDIO VISUAL CONSTRAINTS

No additional audio visual equipment is present beyond what is specified.


HOUSE AND STAGE LIGHTING AND COLOUR TEMPERATURE

Lighting is a primary driver of mood and depth.
Stage lighting is soft white with a colour temperature between 4000 and 4200 kelvin, providing clarity without harshness.
House lighting is dimmed and warmer, with a colour temperature between 3200 and 3500 kelvin, adding warmth and comfort to the audience areas.
"""
}




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
        'SEATING_MODE = "COCKTAIL"\n'
        "\n"
        "The room is already fully set and correctly designed. There are cocktail tables installed throughout the space. No additional furniture or seating of any kind is present. The layout feels full, balanced, and intentionally designed, with no empty central space and no visual voids between tables.\n"
        "\n"
        "Every cocktail table is occupied. Each table has a minimum of two attendees standing around it, with some tables accommodating three or four attendees. No cocktail table is unoccupied. Attendees are evenly distributed across the room so that crowd density reads as complete, natural, and professionally planned. There are no gaps between table groupings and no areas of unused floor space.\n"
        "\n"
        "Attendees are standing and engaged in conversation with others at their table. Body language is relaxed, social, and varied. Attendees have natural, happy expressions and realistic body proportions. Attendees are dressed smart casual with refined styling appropriate to a premium corporate environment. Attendees never look at the camera.\n"
        "\n"
        "Two wait staff are walking through scene holding a drinks tray with half full red and white wine glasses.\n"
        "\n"
        "Cocktail tables are a strong visual element within the scene. All tables feature luxe, premium styling, including high end linens, elegant centrepieces, and sophisticated corporate event finishes. Centrepieces repeat consistently across the room to create visual rhythm and cohesion. Materials feel tactile, weighted, and realistic, with finishes consistent across all tables.\n"
    ),

    "Long Tables": (
        'SEATING_MODE = "LONG_TABLE"\n'
        "\n"
        "The room is already fully set and correctly designed. There are long banquet tables installed in the room. No additional tables of any kind are present. The layout feels full, balanced, and intentionally designed.\n"
        "\n"
        "Attendees are seated at the long tables. Each long table seats exactly ten attendees per side. All seats are occupied. Attendees are evenly distributed along the length of each table so the space reads as complete and professionally planned. There are no gaps, empty seats, or irregularities in the seating arrangement.\n"
        "\n"
        "Attendees are dressed smart casual with refined styling appropriate to a premium corporate environment. Facial expressions are natural and happy, with realistic body proportions and subtle variation. Attendees are engaged with others seated near them. Attendees never look at the camera.\n"
        "\n"
        "At two tables wait staff are leaning over and pouring champagne into flutes.\n"
        "\n"
        "Long tables are a strong visual element within the scene. All tables feature centrepieces. Centrepieces repeat consistently along the length of the tables to create visual rhythm and cohesion. Materials feel realistic, with finishes consistent across all tables.\n"
    ),

    "Banquet": (
        'SEATING_MODE = "BANQUET"\n'
        "\n"
        "The room is already fully set and correctly designed. There are round banquet tables installed in the room. There are EXACTLY six round banquet tables. No additional tables of any kind are present. Fill the space with tables and people at every seat. The room feels full, balanced, and intentionally designed. Each round banquet table seats exactly ten attendees. All seats are occupied. Attendees are seated evenly around each table and engaged in conversation with others at their table. Body language is natural, social, and varied. Attendees are dressed smart casual with refined styling appropriate to a premium corporate environment. Attendees never look at the camera.\n"
        "\n"
        "At two tables wait staff are leaning over and pouring champagne into flutes.\n"
        "\n"
        "Tables are a strong visual element within the scene. They feature high end linens with subtle fabric texture, refined centrepieces, and understated sculptural accents. Centrepieces repeat consistently across the room to create visual rhythm. Materials feel tactile, weighted, and realistic.\n"
    ),

    "Theatre": (
        'SEATING_MODE = "THEATRE"\n'
        "\n"
        "The room is already fully set and correctly designed. There is theatre style seating installed in the room. There are EXACTLY sixty theatre seats. No additional seating of any kind is present. The space feels full, balanced, and intentionally designed.\n"
        "\n"
        "The seating layout is fixed and symmetrical. There is a single central aisle positioned exactly in the middle of the image. The aisle runs in a straight line from the stage toward the camera. On each side of the central aisle are rows of seats. Each row consists of exactly five seats on the left side of the aisle and five seats on the right side of the aisle. Seating rows are evenly spaced, aligned precisely toward the stage, and professionally planned. No seats are missing. No seats are moved. The layout must be preserved exactly as specified.\n"
        "\n"
        "All sixty seats are occupied. No seats are empty. Attendees are seated facing the stage and evenly distributed across the room so the audience reads as complete, dense, and intentionally arranged. There are no gaps, irregularities, or visual voids in the seating layout.\n"
        "\n"
        "Attendees are dressed smart casual with refined styling appropriate to a premium corporate environment. Body posture is natural, attentive, and relaxed, with realistic proportions and subtle variation. Attendees remain focused on the stage and never look at the camera.\n"
    ),
}
   
 
    return "\n".join([
        venue_lock,
        allowed_changes,
        composition,
        mood_map.get(mood, mood),
        layout_map.get(layout, layout)
    ])

# --------------------------------------------------
# Replicate integration (raw HTTP)
# --------------------------------------------------
def replicate_generate_image_url(
    prompt: str,
    venue_image_url: Optional[str] = None,
    layout: Optional[str] = None,
) -> str:
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

    payload = {
        "input": {
            "prompt": prompt
        }
    }

    # Only nano-banana models accept negative_prompt
    if model in ("google/nano-banana", "google/nano-banana-pro"):
        negative_prompt = build_designer_negative_prompt(layout=layout)
        if negative_prompt:
            payload["input"]["negative_prompt"] = negative_prompt

        # Resolution toggle (only supported by google/nano-banana-pro on Replicate)
        if model == "google/nano-banana-pro":
            payload["input"]["resolution"] = "1K" if DME_IMAGE_RES == "1K" else "2K"

    # Reference image handling
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
    
    if (req.av_equipment or "").strip().upper() == "IN":
        prompt = prompt + "\n\n" + AV_EQUIPMENT_PROMPTS["IN"].strip()

    try:
        image_url = replicate_generate_image_url(
            prompt,
            req.venue_image_url,
            req.layout
)
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
