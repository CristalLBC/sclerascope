"""
ScleraScope vision layer — talks to Google Gemini vision models.

Returns strictly-structured observations about ONE eye photo.
No diagnosis, no disease naming: that gating happens in sclerology.py.
"""
import base64
import io
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request

try:  # Pillow is used only to shrink photos before upload; optional
    from PIL import Image
except Exception:  # noqa: BLE001
    Image = None

GEMINI_KEY = os.environ.get("GOOGLE_AI_API_KEY", "").strip()
BASE = "https://generativelanguage.googleapis.com/v1beta/models"

# fal.ai is the primary provider: no daily request cap, ~2-4s per eye, and her key
# already has credits. Gemini stays as fallback (free tier = 20 requests/DAY/model,
# which is not enough to run a product on).
FAL_KEY = os.environ.get("FAL.AI_KEY", "").strip()
FAL_URL = "https://fal.run/fal-ai/any-llm/vision"
FAL_MODELS = ["google/gemini-flash-1.5", "anthropic/claude-3.5-haiku", "openai/gpt-4o-mini"]

# first working model wins; 3.8-flash has been flaky (503), so keep a chain
MODELS = [
    "gemini-3.5-flash",
    "gemini-3.8-flash",
    "gemini-3.1-flash-lite",
    "gemini-flash-latest",
    "gemini-2.5-flash",
]

OBSERVE_PROMPT = """You are an objective eye-image observer for a wellness education app.
Look at this photo of ONE human eye (close-up) and report ONLY what is visibly present.

Rules:
- Describe appearance, never diagnose. Do NOT name diseases or conditions.
- If something is not clearly visible, set present=false and confidence="low". Never guess.
- Be conservative: a marking counts only if you can actually see it.
- Ignore glare, camera reflections, specular highlights and bright light patches. A limbal
  ring is a continuous greyish-white band at the edge of the cornea, not a bright patch from
  a light source — if what you see is a reflection, set limbal_ring.present=false.
- brown_spots are on the WHITE of the eye (the sclera) only. Freckles or pigment on the
  iris (the coloured ring around the pupil) do NOT go in brown_spots — mention them in
  iris.notes instead.

Return STRICT JSON, exactly this shape:
{
 "usable": true,
 "usable_reasons": ["too dark" | "blurry" | "eye not visible" | "too far away" | ""],
 "eye_side_guess": "left" | "right" | "unknown",
 "sclera_base_color": "white" | "cream" | "pinkish" | "yellowish" | "bluish" | "reddened",
 "redness": {"level": "none|mild|moderate|marked",
             "pattern": "diffuse|localized|vessels_in_corners|ring_around_iris|band_across|none",
             "zones": ["upper","lower","nasal","temporal","whole"],
             "description": "one plain sentence"},
 "vessels": {"visible": false, "prominent": false, "description": ""},
 "yellowing": {"present": false, "confidence": "low|medium|high", "description": ""},
 "brown_spots": [{"zone": "upper|lower|nasal|temporal|near_iris", "size": "small|medium|large", "description": ""}],
 "bluish_tint": {"present": false, "zones": [], "description": ""},
 "limbal_ring": {"present": false, "confidence": "low|medium|high", "description": ""},
 "bump_near_corner": {"present": false, "which": "nasal|temporal", "description": ""},
 "corneal_clarity": "clear" | "slight_haze" | "cloudy" | "unknown",
 "pupil": {"shape": "round|irregular|unknown", "notes": ""},
 "iris": {"color": "", "notes": ""},
 "eyelid_margin": {"notes": ""},
 "quality_notes": ["..."],
 "observer_notes": ["plain factual sentences about the photo"]
}
JSON only, no markdown fence."""


ATTEMPTS = []  # debug trace: (model, outcome, seconds)

# Free-tier keys are rate limited (429). Keep concurrency low and remember which
# model actually works so we stop burning quota probing the top of the list.
_GATE = threading.Semaphore(2)
_STICKY = {"model": None}
_STICKY_LOCK = threading.Lock()


def _post(model, body, timeout=120):
    req = urllib.request.Request(
        f"{BASE}/{model}:generateContent?key={GEMINI_KEY}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _retry_delay(err, default=6.0):
    """Google returns a RetryInfo block on 429 — honour it, capped."""
    try:
        raw = err.read().decode("utf-8", "ignore")
    except Exception:  # noqa: BLE001
        return default
    m = re.search(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"', raw)
    if m:
        return min(float(m.group(1)) + 0.5, 25.0)
    return default


def _model_order():
    with _STICKY_LOCK:
        sticky = _STICKY["model"]
    order = list(MODELS)
    if sticky and sticky in order:
        order.remove(sticky)
        order.insert(0, sticky)
    return order


def _remember(model):
    with _STICKY_LOCK:
        _STICKY["model"] = model


def _generate(parts, temperature=0.2, timeout=120, thinking=0):
    gen = {"temperature": temperature}
    if thinking is not None:
        # thinkingBudget=0 cuts latency from ~70s to ~4s on flash models
        gen["thinkingConfig"] = {"thinkingBudget": thinking}
    body = {"contents": [{"parts": parts}], "generationConfig": gen}
    last = None
    with _GATE:
        for model in _model_order():
            t0 = time.time()
            try:
                out = _post(model, body, timeout)
                ATTEMPTS.append((model, "ok", round(time.time() - t0, 1)))
                _remember(model)
                return out, model
            except urllib.error.HTTPError as e:
                attempt_s = round(time.time() - t0, 1)
                last = f"{model}: HTTP {e.code}"
                if e.code == 400 and "thinkingConfig" in json.dumps(body):
                    body["generationConfig"].pop("thinkingConfig", None)
                    ATTEMPTS.append((model, "400-nothink", attempt_s))
                    continue
                if e.code == 429:
                    wait = _retry_delay(e)
                    ATTEMPTS.append((model, f"429 wait{wait:.0f}s", attempt_s))
                    # one patient retry on the same model, then move on
                    time.sleep(wait)
                    try:
                        out = _post(model, body, timeout)
                        ATTEMPTS.append((model, "ok-after-wait", round(time.time() - t0, 1)))
                        _remember(model)
                        return out, model
                    except Exception as e2:  # noqa: BLE001
                        last = f"{model}: {e2!r}"[:200]
                        ATTEMPTS.append((model, "still-limited", round(time.time() - t0, 1)))
                elif e.code in (500, 502, 503, 504):
                    ATTEMPTS.append((model, f"http{e.code}", attempt_s))
                    time.sleep(1.5)
                else:
                    ATTEMPTS.append((model, f"http{e.code}", attempt_s))
            except Exception as e:  # noqa: BLE001
                ATTEMPTS.append((model, f"err:{type(e).__name__}", round(time.time() - t0, 1)))
                last = f"{model}: {e!r}"[:200]
    raise RuntimeError(f"all vision models failed ({last})")


def _extract_json(text):
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```")[1] if "```" in t[3:] else t.strip("`")
        t = t.lstrip("json").strip()
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no json object in model output")
    return json.loads(t[start:end + 1])


EMPTY = {
    "usable": False,
    "usable_reasons": ["could not read image"],
    "eye_side_guess": "unknown",
    "sclera_base_color": "unknown",
    "redness": {"level": "none", "pattern": "none", "zones": [], "description": ""},
    "vessels": {"visible": False, "prominent": False, "description": ""},
    "yellowing": {"present": False, "confidence": "low", "description": ""},
    "brown_spots": [],
    "bluish_tint": {"present": False, "zones": [], "description": ""},
    "limbal_ring": {"present": False, "confidence": "low", "description": ""},
    "bump_near_corner": {"present": False, "which": "nasal", "description": ""},
    "corneal_clarity": "unknown",
    "pupil": {"shape": "unknown", "notes": ""},
    "iris": {"color": "unknown", "notes": ""},
    "eyelid_margin": {"notes": ""},
    "quality_notes": [],
    "observer_notes": [],
}


def _normalise(obs):
    """Fill missing keys so templates can trust the shape."""
    out = json.loads(json.dumps(EMPTY))
    for k, v in (obs or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    if not isinstance(out.get("brown_spots"), list):
        out["brown_spots"] = []
    return out


def shrink(image_bytes: bytes, max_px: int = 1024, quality: int = 88):
    """Downscale before sending: fewer image tokens, faster, fewer rate-limit hits."""
    if Image is None:
        return image_bytes, "image/jpeg"
    try:
        im = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        if max(im.size) > max_px:
            im.thumbnail((max_px, max_px), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=quality, optimize=True)
        return buf.getvalue(), "image/jpeg"
    except Exception:  # noqa: BLE001
        return image_bytes, "image/jpeg"


def _fal_generate(prompt: str, image_bytes: bytes, mime: str = "image/jpeg", timeout=120):
    """fal-ai/any-llm/vision. Accepts a data URL, so no separate upload step."""
    if not FAL_KEY:
        raise RuntimeError("FAL.AI_KEY is not set")
    data_url = f"data:{mime};base64," + base64.b64encode(image_bytes).decode()
    last = None
    for model in FAL_MODELS:
        t0 = time.time()
        try:
            req = urllib.request.Request(
                FAL_URL,
                data=json.dumps({"prompt": prompt, "image_url": data_url, "model": model}).encode(),
                headers={"Authorization": "Key " + FAL_KEY, "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.load(r)
            out = (d.get("output") or "").strip()
            if out:
                ATTEMPTS.append((f"fal:{model}", "ok", round(time.time() - t0, 1)))
                return out, f"fal:{model}"
            last = f"{model}: empty output"
            ATTEMPTS.append((f"fal:{model}", "empty", round(time.time() - t0, 1)))
        except urllib.error.HTTPError as e:
            body = e.read()[:200].decode("utf-8", "ignore")
            ATTEMPTS.append((f"fal:{model}", f"http{e.code}", round(time.time() - t0, 1)))
            last = f"{model}: HTTP {e.code} {body}"
            if e.code in (401, 403):
                break
            time.sleep(1)
        except Exception as e:  # noqa: BLE001
            ATTEMPTS.append((f"fal:{model}", f"err:{type(e).__name__}", round(time.time() - t0, 1)))
            last = f"{model}: {e!r}"[:200]
            time.sleep(1)
    raise RuntimeError(f"fal vision failed ({last})")


def observe_eye(image_bytes: bytes, side: str = "unknown", mime: str = "image/jpeg") -> dict:
    """Analyse one eye photo -> structured observation dict. fal first, Gemini fallback."""
    if not (FAL_KEY or GEMINI_KEY):
        raise RuntimeError("no vision provider configured (FAL.AI_KEY / GOOGLE_AI_API_KEY)")
    small, mime = shrink(image_bytes)
    prompt = OBSERVE_PROMPT + f"\n\nThe app's label for this photo is: {side} eye. Confirm or correct eye_side_guess."

    text, model = "", ""
    errors = []
    if FAL_KEY:
        try:
            text, model = _fal_generate(prompt, small, mime)
        except Exception as e:  # noqa: BLE001
            errors.append(str(e)[:150])
    if not text and GEMINI_KEY:
        b64 = base64.b64encode(small).decode()
        parts = [
            {"text": prompt},
            {"inline_data": {"mime_type": mime, "data": b64}},
        ]
        try:
            data, gm = _generate(parts)
            cand = (data.get("candidates") or [{}])[0]
            text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
            model = f"gemini:{gm}"
        except Exception as e:  # noqa: BLE001
            errors.append(str(e)[:150])
    if not text:
        raise RuntimeError("; ".join(errors) or "no vision output")

    try:
        obs = _normalise(_extract_json(text))
    except Exception:
        obs = _normalise({"usable": False, "usable_reasons": ["analysis unreadable"]})
    obs["_model"] = model
    return obs


SUMMARY_PROMPT = """You are writing the friendly plain-language summary for a wellness app called ScleraScope.

Here is the objective observation data for both eyes (JSON):
{data}

Traditional sclerology readings (NOT science — label them as such if you use them):
{trad}

Write 3-5 short, warm, plain-English paragraphs for the user. Hard rules:
- NEVER diagnose, never name a disease, never say "you have".
- Use "may", "can be associated with", "worth keeping an eye on".
- Any traditional-reading sentence MUST start with "In traditional sclerology," so the user knows it is not established science.
- Mention that these are observations from a photo, not a medical exam, once.
- No scare tactics, no cures, no supplements to buy, no promises.
- Do not use markdown headers. Plain paragraphs, max 220 words total.
"""


def write_summary(obs_bundle: dict, trad_lines: list[str]) -> str:
    """Best-effort friendly summary; caller falls back to a template on failure."""
    if not GEMINI_KEY:
        return ""
    prompt = SUMMARY_PROMPT.format(
        data=json.dumps(obs_bundle, ensure_ascii=False)[:6000],
        trad="\n".join("- " + t for t in trad_lines[:14]) or "(none)",
    )
    try:
        data, _ = _generate([{"text": prompt}], temperature=0.4, timeout=90)
        cand = (data.get("candidates") or [{}])[0]
        return "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", [])).strip()
    except Exception:
        return ""
