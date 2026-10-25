# ScleraScope

Two-photo eye scan app. Takes a close-up of each eye, reads the sclera, and returns a
report split into two clearly-labelled layers:

1. **Objective** — what a camera can actually see (redness, pigment, yellowing, corneal
   haze, limbal ring, pinguecula). Some of these are real medical signs and the app says
   "worth showing a professional" instead of diagnosing.
2. **Traditional sclerology** — the folk zone-to-body-system reading. Every line from this
   layer is prefixed with "In traditional sclerology," so it can never be mistaken for a
   diagnosis.

Consent is enforced before the camera opens: 18+, waiver accepted, photo-processing
consent. The consent record (timestamp, waiver version, IP, UA) is stored so there is
evidence the user agreed.

## Stack

- Flask + SQLite, server-rendered templates, no build step
- Vision: `fal-ai/any-llm/vision` (primary, ~2-4s per eye) with Gemini as fallback
- Photos downscaled to 1024px before analysis; originals kept server-side, purged after 30 days
- Monetization: unlock codes (local table or Gumroad license verification)

## Run locally

```bash
cd ~/sclerascope
pip install -r requirements.txt
export FAL.AI_KEY=...          # or GOOGLE_AI_API_KEY
PORT=5055 python app.py        # http://127.0.0.1:5055
```

Admin panel: `/admin?key=<ADMIN_KEY>` — the key is generated into `.admin_key` on first run
(or set `ADMIN_KEY`). From there you generate unlock codes.

## Env

| var | purpose |
| --- | --- |
| `FAL.AI_KEY` | primary vision provider (no daily request cap) |
| `GOOGLE_AI_API_KEY` | fallback vision provider (free tier = 20 requests/day/model — not enough alone) |
| `SECRET_KEY` | session signing |
| `ADMIN_KEY` | admin panel access |
| `DATA_DIR` | where the SQLite db + photos live (Render disk mounts here) |
| `HTTPS_ONLY` | `1` in production so the session cookie is Secure |
| `PRICE_FULL` / `PRICE_MONTH` | displayed prices |
| `SUPPORT_EMAIL` | shown on reports and legal pages |
| `GUMROAD_PRODUCT_ID` | if set, `/redeem` also verifies Gumroad license keys |
| `ENABLE_LLM_SUMMARY` | `1` to let the model write the summary (extra API call per scan) |

## Routes

`/` landing · `/start` consent + waiver · `/capture` camera · `POST /api/scan` analysis ·
`/report/<id>` report (locked sections until unlocked) · `/redeem` code entry ·
`/terms` `/privacy` `/waiver-text` · `/admin` · `/healthz`

## Deploy

Render: `render.yaml` is a blueprint, or create a Python web service pointing at this repo
with `gunicorn -w 2 --threads 4 -t 180 -b 0.0.0.0:$PORT app:app`.

Free Render instances sleep after ~15 minutes idle, so the first request of the day takes
30-50s. A $7/mo instance removes that.

## Legal posture

ScleraScope is presented as wellness education, not a medical device: explicit
"not FDA-cleared / does not diagnose" language on the landing page, in the consent flow,
on every report, and on the waiver. Sclerology is labelled as a traditional practice with
no scientific validation. Red-flag symptoms route the user to a real professional. Don't
remove those disclaimers — they are the product's legal cover.
