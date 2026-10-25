"""
ScleraScope knowledge base.

Two clearly separated layers, and the separation is the whole point:

1. OBJECTIVE — things a camera can genuinely show (redness, yellowing, pigment
   spots, corneal haze, a limbal ring, a pinguecula). Some of these are real
   medical signs; the app says "worth having a professional look" and does not
   diagnose.

2. TRADITIONAL SCLEROLOGY — the folk/complementary system that maps sclera
   zones to body systems. It is NOT validated science. Every line produced from
   this layer is prefixed so the user can never mistake it for a diagnosis.
"""

TRAD_PREFIX = "In traditional sclerology,"

ZONES = {
    "upper": {
        "name": "Upper sclera",
        "trad_organs": "head, sinuses, nervous system",
        "wellness": [
            "Most upper-sclera redness is simple dryness or screen strain — try the 20-20-20 rule for a week.",
            "If you get sinus pressure with it, steam, hydration and sleep usually move the needle more than anything else.",
        ],
    },
    "lower": {
        "name": "Lower sclera",
        "trad_organs": "digestive tract, abdomen, liver-gallbladder region",
        "wellness": [
            "Traditional readers link this zone to digestion. A boring week of water, fibre and fewer late meals is the cheapest experiment you can run.",
            "If you also notice bloating or irregular meals, that is the thing to mention to your doctor — not the eye itself.",
        ],
    },
    "nasal": {
        "name": "Nasal sclera (inner, toward the nose)",
        "trad_organs": "throat, neck, lymphatics, lungs",
        "wellness": [
            "Fine vessels right by the inner corner are extremely common and often just how that eye is built.",
            "Allergies and dry indoor air show up here first. A humidifier and an air filter are low-risk things to try.",
        ],
    },
    "temporal": {
        "name": "Temporal sclera (outer, toward the temple)",
        "trad_organs": "elimination, kidneys, general vitality",
        "wellness": [
            "Sun and wind exposure (beach, driving, windsurfing) is the usual culprit for markings out here — sunglasses are the fix.",
            "Keep an eye on whether it changes shape or colour over a month; that is the only reason to worry.",
        ],
    },
    "near_iris": {
        "name": "Near the iris edge",
        "trad_organs": "circulatory and nervous system tone",
        "wellness": [
            "A pale ring at the iris edge is common with age and worth one professional look, nothing more.",
            "Traditional readers call this a stress marker. Sleep and daylight beat any supplement.",
        ],
    },
    "whole": {
        "name": "Whole sclera",
        "trad_organs": "overall system balance",
        "wellness": [
            "Generalised redness is usually dryness, sleep debt, allergies, or screen time — in that order.",
            "If it is one-sided, painful, or comes with light sensitivity, skip the app and see a professional.",
        ],
    },
}

TYPE_LABELS = {
    "vessel": "Visible vessels / fine red lines",
    "redness": "Redness",
    "yellow_tint": "Yellow tint",
    "brown_spot": "Brown pigment spot",
    "bluish_tint": "Bluish tint",
    "limbal_ring": "Pale ring near the iris",
    "bump_near_corner": "Small raised bump near the corner",
    "corneal_haze": "Slight haze over the clear front surface",
}

# objective, real-world note + how much a professional should be involved
TYPE_OBJECTIVE = {
    "vessel": "Visible vessels are normal anatomy. They become more obvious with dryness, "
              "screen time, allergies, wind, smoke, or a poor night's sleep.",
    "redness": "Redness this pattern usually tracks dryness, irritation, allergy, or fatigue. "
               "One-sided pain or light sensitivity is a different story and needs a professional.",
    "yellow_tint": "A yellow tint in the white of the eye is the one finding on this list that "
                   "genuinely deserves a doctor's opinion — it can reflect how the liver is working. "
                   "It is most often harmless, but it should not be self-diagnosed.",
    "brown_spot": "Brown patches on the white of the eye are common pigment deposits. They are "
                  "usually harmless and stable. They should be looked at if they are new, growing, "
                  "raised, or changing colour.",
    "bluish_tint": "A slight blue-grey cast can simply be thin sclera over the underlying tissue. "
                   "Occasionally it is worth a routine blood check.",
    "limbal_ring": "A pale or greyish ring at the edge of the cornea is common with age and can be "
                   "associated with cholesterol levels. Worth mentioning at your next check-up.",
    "bump_near_corner": "A small yellowish bump near the inner corner is usually a pinguecula — "
                        "a sun- and dust-related change of the conjunctiva. An eye doctor can confirm "
                        "it in about two minutes.",
    "corneal_haze": "Any loss of clarity in the clear front surface of the eye should be checked by "
                    "an eye professional rather than self-assessed.",
}

TYPES_NEEDING_PRO = {"yellow_tint", "corneal_haze", "limbal_ring", "bump_near_corner"}

RED_FLAGS = [
    "Sudden or severe eye pain",
    "Sudden change or loss of vision, or a curtain / shadow across your sight",
    "New flashes of light or a jump in floaters",
    "One eye that is very red with pain and light sensitivity",
    "Yellowing of the eyes together with yellowing skin, dark urine, or pale stools",
    "Eye injury, chemical exposure, or something stuck in the eye",
    "A pigment spot that is growing, raised, or bleeding",
]

DISCLAIMER_SHORT = (
    "ScleraScope is a wellness education tool. It is not a medical device and it is not "
    "reviewed or approved by the FDA. It does not diagnose, treat, cure, or prevent any "
    "disease. Sclerology is a traditional, complementary practice that has not been "
    "validated by scientific research. For anything that worries you, see a licensed "
    "eye care professional or physician."
)


def _trad_line(construct: str) -> str:
    return f"{TRAD_PREFIX} {construct}"


def build_findings(obs: dict, side_label: str) -> list[dict]:
    """Turn one eye's observation dict into a flat list of findings."""
    f = []
    if not obs.get("usable"):
        return f

    red = obs.get("redness") or {}
    level = (red.get("level") or "none").lower()
    zones = [z for z in (red.get("zones") or []) if z in ZONES] or ["whole"]
    if level in ("mild", "moderate", "marked"):
        f.append({
            "type": "redness",
            "zone": zones[0],
            "zones": zones,
            "severity": level,
            "label": TYPE_LABELS["redness"],
            "what_we_see": (red.get("description") or "").strip()
                           or f"{level.capitalize()} redness across the {zones[0]} sclera.",
            "trad": _trad_line(
                f"redness in the {ZONES[zones[0]]['name'].lower()} is read as irritation or heat "
                f"in the {ZONES[zones[0]]['trad_organs']}."
            ),
            "objective": TYPE_OBJECTIVE["redness"],
            "needs_pro": False,
        })

    v = obs.get("vessels") or {}
    if v.get("visible"):
        f.append({
            "type": "vessel",
            "zone": zones[0] if zones else "whole",
            "zones": zones,
            "severity": "prominent" if v.get("prominent") else "mild",
            "label": TYPE_LABELS["vessel"],
            "what_we_see": (v.get("description") or "Fine branching vessels visible in the sclera.").strip(),
            "trad": _trad_line("prominent vessels are read as congestion in the corresponding body system."),
            "objective": TYPE_OBJECTIVE["vessel"],
            "needs_pro": False,
        })

    y = obs.get("yellowing") or {}
    if y.get("present"):
        f.append({
            "type": "yellow_tint",
            "zone": "whole",
            "zones": ["whole"],
            "severity": y.get("confidence", "low"),
            "label": TYPE_LABELS["yellow_tint"],
            "what_we_see": (y.get("description") or "A yellowish cast to the white of the eye.").strip(),
            "trad": _trad_line("a yellow cast over the sclera is read as a liver or gallbladder signal."),
            "objective": TYPE_OBJECTIVE["yellow_tint"],
            "needs_pro": True,
        })

    for spot in (obs.get("brown_spots") or [])[:4]:
        z = spot.get("zone") if spot.get("zone") in ZONES else "temporal"
        f.append({
            "type": "brown_spot",
            "zone": z,
            "zones": [z],
            "severity": spot.get("size", "small"),
            "label": TYPE_LABELS["brown_spot"],
            "what_we_see": (spot.get("description") or f"A small brown pigment spot in the {z} sclera.").strip(),
            "trad": _trad_line(
                f"pigment in the {ZONES[z]['name'].lower()} zone is read as stagnation in the "
                f"{ZONES[z]['trad_organs']}."
            ),
            "objective": TYPE_OBJECTIVE["brown_spot"],
            "needs_pro": False,
        })

    b = obs.get("bluish_tint") or {}
    if b.get("present"):
        f.append({
            "type": "bluish_tint",
            "zone": (b.get("zones") or ["whole"])[0] if (b.get("zones") or []) else "whole",
            "zones": b.get("zones") or ["whole"],
            "severity": "mild",
            "label": TYPE_LABELS["bluish_tint"],
            "what_we_see": (b.get("description") or "A faint bluish-grey cast in the sclera.").strip(),
            "trad": _trad_line("a bluish cast is read as sluggish lymph or poor circulation."),
            "objective": TYPE_OBJECTIVE["bluish_tint"],
            "needs_pro": False,
        })

    lim = obs.get("limbal_ring") or {}
    if lim.get("present"):
        f.append({
            "type": "limbal_ring",
            "zone": "near_iris",
            "zones": ["near_iris"],
            "severity": "mild",
            "label": TYPE_LABELS["limbal_ring"],
            "what_we_see": (lim.get("description") or "A pale ring visible where the sclera meets the iris.").strip(),
            "trad": _trad_line("a pale ring at the iris edge is read as a circulatory tone marker."),
            "objective": TYPE_OBJECTIVE["limbal_ring"],
            "needs_pro": True,
        })

    bump = obs.get("bump_near_corner") or {}
    if bump.get("present"):
        f.append({
            "type": "bump_near_corner",
            "zone": bump.get("which") if bump.get("which") in ZONES else "nasal",
            "zones": [bump.get("which") if bump.get("which") in ZONES else "nasal"],
            "severity": "mild",
            "label": TYPE_LABELS["bump_near_corner"],
            "what_we_see": (bump.get("description") or "A small raised yellowish area near the corner of the eye.").strip(),
            "trad": _trad_line("this is read as a lymphatic or respiratory marker."),
            "objective": TYPE_OBJECTIVE["bump_near_corner"],
            "needs_pro": True,
        })

    clarity = (obs.get("corneal_clarity") or "clear").lower()
    if clarity in ("slight_haze", "cloudy"):
        f.append({
            "type": "corneal_haze",
            "zone": "near_iris",
            "zones": ["near_iris"],
            "severity": clarity,
            "label": TYPE_LABELS["corneal_haze"],
            "what_we_see": f"The front surface of the eye looks {clarity.replace('_', ' ')} in this photo.",
            "trad": _trad_line("this is read as a systemic congestion marker."),
            "objective": TYPE_OBJECTIVE["corneal_haze"],
            "needs_pro": True,
        })

    for finding in f:
        finding["eye"] = side_label

    # Redness and visible vessels usually describe the same thing twice — keep the
    # stronger, more useful card and fold the other description into it.
    types = {x["type"] for x in f}
    if "redness" in types and "vessel" in types:
        red = next(x for x in f if x["type"] == "redness")
        ves = next(x for x in f if x["type"] == "vessel")
        if ves.get("what_we_see") and ves["what_we_see"] not in red["what_we_see"]:
            red["what_we_see"] = red["what_we_see"].rstrip(".") + ". " + ves["what_we_see"]
        red["severity"] = ves["severity"] if ves.get("severity") == "prominent" else red["severity"]
        f = [x for x in f if x["type"] != "vessel"]
    return f


def wellness_for(finding: dict) -> list[str]:
    return ZONES.get(finding["zone"], ZONES["whole"])["wellness"]


def build_summary(obs_bundle: dict, findings: list[dict], needs_pro: list[dict], unreadable: list[str]) -> str:
    """Deterministic plain-language summary. Fast, free, and cannot overstate."""
    parts = []

    if unreadable:
        parts.append(
            f"We could only read part of this scan — the {' and '.join(unreadable)} eye photo was not clear "
            "enough to analyse. Retake it in brighter light, closer to the eye, and you will get a full read."
        )

    if not findings:
        parts.append(
            "Both eyes came back clean in this photo: the white of the eye looks even in colour with no "
            "distinct markings in any zone. There is nothing here that asks for a second look."
        )
    else:
        zones = {z for f in findings for z in f["zones"]}
        sev_order = ["low", "small", "mild", "moderate", "prominent", "marked", "high"]
        known = [f["severity"] for f in findings if f.get("severity") in sev_order]
        worst = max(known, key=sev_order.index) if known else None
        tone = ""
        if worst in ("low", "small", "mild"):
            tone = " — all on the milder side"
        elif worst:
            friendly = {"prominent": "moderate to strong", "moderate": "moderate",
                        "marked": "strong"}.get(worst, worst)
            tone = f" — the strongest of them reads as {friendly}"
        parts.append(
            f"We picked up {len(findings)} marking{'s' if len(findings) != 1 else ''} across "
            f"{len(zones)} zone{'s' if len(zones) != 1 else ''}{tone}. "
            "None of this is a diagnosis — it is what a camera can see on the surface of your eye."
        )
        lead = findings[0]
        parts.append(
            f"The most obvious one: {lead['what_we_see'].rstrip('.')}. "
            f"{TYPE_OBJECTIVE[lead['type']]}"
        )
        if len(findings) > 1:
            parts.append(
                "In traditional sclerology these markings are read as signals in the "
                + ", ".join(sorted({ZONES.get(z, ZONES['whole'])['trad_organs'] for z in zones}))
                + " — a tradition worth knowing about, not established science."
            )

    if needs_pro:
        parts.append(
            "One thing is worth a professional look rather than an app: "
            + "; ".join(f"{f['eye']} eye, {f['label'].lower()}" for f in needs_pro)
            + ". An eye doctor can examine that properly in a few minutes."
        )

    parts.append(
        "Markings in the sclera change slowly. If you scan again in a week or two, in the same light, "
        "the trend will tell you more than this single photo ever could."
    )
    return "\n".join(parts)


BANNED_SUMMARY_WORDS = [
    "you have", "diagnos", "jaundice", "cancer", "diabetes", "anemia", "anaemia", "liver disease",
    "high cholesterol", "cure", "treat your", "prescrib",
]


def summary_is_safe(text: str) -> bool:
    if not text or len(text) < 120:
        return False
    low = text.lower()
    return not any(w in low for w in BANNED_SUMMARY_WORDS)


def build_report(obs_bundle: dict, summary: str) -> dict:
    """obs_bundle = {"right": obs, "left": obs}; returns render-ready report dict."""
    per_eye = {}
    all_findings = []
    for side in ("right", "left"):
        obs = obs_bundle.get(side) or {}
        findings = build_findings(obs, side)
        per_eye[side] = {
            "usable": bool(obs.get("usable")),
            "reasons": obs.get("usable_reasons") or [],
            "iris": obs.get("iris") or {},
            "pupil": obs.get("pupil") or {},
            "sclera_base_color": obs.get("sclera_base_color", "unknown"),
            "findings": findings,
            "observer_notes": obs.get("observer_notes") or [],
            "quality_notes": obs.get("quality_notes") or [],
            "model": obs.get("_model", ""),
        }
        all_findings.extend(findings)

    zones_hit = {}
    for f in all_findings:
        for z in f["zones"]:
            zones_hit.setdefault(z, {"zone": z, "name": ZONES.get(z, ZONES["whole"])["name"],
                                     "trad_organs": ZONES.get(z, ZONES["whole"])["trad_organs"],
                                     "count": 0, "types": set()})
            zones_hit[z]["count"] += 1
            zones_hit[z]["types"].add(f["type"])
    zone_list = sorted(zones_hit.values(), key=lambda z: -z["count"])
    for z in zone_list:
        z["types"] = sorted(z["types"])

    focus = []
    for z in zone_list[:4]:
        focus.append({
            "zone": z["name"],
            "trad": z["trad_organs"],
            "tips": ZONES.get(z["zone"], ZONES["whole"])["wellness"],
        })

    needs_pro = [f for f in all_findings if f["needs_pro"]]
    unreadable = [s for s in ("right", "left") if not per_eye[s]["usable"]]

    return {
        "summary": summary,
        "per_eye": per_eye,
        "findings": all_findings,
        "zones": zone_list,
        "focus": focus,
        "needs_pro": needs_pro,
        "unreadable": unreadable,
        "red_flags": RED_FLAGS,
        "disclaimer": DISCLAIMER_SHORT,
        "trad_lines": [f["trad"] for f in all_findings if f.get("trad")],
    }
