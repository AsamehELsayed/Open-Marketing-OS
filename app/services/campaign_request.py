"""Chat-to-campaign: what a business owner actually asked for.

Turns one natural-language request into structured campaign facts, a list of
what is still missing, and a plain proposal the owner can review before any
content is generated.

Rules this module enforces:

* Only facts the owner actually wrote are extracted. Nothing is guessed from
  context, defaulted, or filled with a plausible value. An absent fact stays
  absent and, when it matters, becomes a short question.
* Every fact carries its source (``owner_request`` or ``business_profile``) so
  the UI never has to guess where a statement came from.
* The proposal is assembled from those facts only. Anything it adds on its own
  (format ideas per channel) is labelled as an OMOS recommendation, never as a
  fact about the business or its market.

Storage reuses the existing ``campaigns.workflow_json["campaign_intake"]``
record; no schema change.
"""
from __future__ import annotations

import re

SOURCE_REQUEST = "owner_request"
SOURCE_PROFILE = "business_profile"

# Canonical channel names (stored lower-case, shown in this form).
CHANNEL_LABELS = {
    "instagram": "Instagram", "facebook": "Facebook", "tiktok": "TikTok",
    "linkedin": "LinkedIn", "twitter": "X (Twitter)", "youtube": "YouTube",
    "pinterest": "Pinterest", "reddit": "Reddit", "email": "Email",
    "newsletter": "Email newsletter", "google ads": "Google Ads",
    "meta ads": "Meta Ads", "paid social": "Paid social",
    "organic social": "Organic social", "search ads": "Search ads",
    "blog": "Blog", "sms": "SMS", "whatsapp": "WhatsApp",
}

# Phrases that name a channel, longest first so "meta ads" wins over "ads".
_CHANNEL_PATTERNS: tuple[tuple[str, str], ...] = (
    ("google ads", r"google\s+ads"), ("meta ads", r"meta\s+ads"),
    ("paid social", r"paid\s+social"), ("organic social", r"organic\s+social"),
    ("search ads", r"search\s+ads"),
    ("instagram", r"instagram|\binsta\b"), ("facebook", r"facebook"),
    ("tiktok", r"tik\s?tok"), ("linkedin", r"linked\s?in"),
    ("twitter", r"twitter|\bx\.com\b"), ("youtube", r"youtube"),
    ("pinterest", r"pinterest"), ("reddit", r"reddit"),
    ("newsletter", r"newsletters?"), ("email", r"e-?mails?"),
    ("blog", r"blogs?\b"), ("sms", r"\bsms\b|text\s+messages?"),
    ("whatsapp", r"whats\s?app"),
)

_NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "fourteen": 14, "thirty": 30, "sixty": 60, "ninety": 90,
}
_UNIT = {"day": "day", "days": "day", "week": "week", "weeks": "week",
         "month": "month", "months": "month"}

_AUDIENCE_NOUNS = (
    "families", "family", "parents", "moms", "mums", "dads", "kids",
    "children", "teens", "students", "seniors", "retirees", "couples",
    "homeowners", "renters", "owners", "customers", "clients", "patients",
    "members", "subscribers", "shoppers", "diners", "locals", "residents",
    "neighbors", "neighbours", "professionals", "freelancers", "founders",
    "entrepreneurs", "teachers", "runners", "cyclists", "gamers", "pet owners",
    "commuters", "travelers", "travellers", "newcomers", "tourists",
    "audiences", "audience", "women", "men", "adults", "millennials",
    "beginners", "enthusiasts", "fans", "followers", "visitors", "guests",
)
_AUDIENCE_END = re.compile(
    r"\s+(?:with|to\s+(?:promote|get|drive|increase|boost|grow|sell|bring|"
    r"announce|launch|raise|generate|encourage|build)|that|who|which|"
    r"offering|using|via|on|in|around|near|across|throughout|within|"
    r"for\s+(?:a|an|the|\d)|including|featuring|about|and\s+(?:an?|the|\d|"
    r"include|use|make|write|create)|so\b|by\b|starting|beginning|from|"
    r"during|over)\b", re.IGNORECASE)

_MONTHS = ("january february march april may june july august september "
           "october november december jan feb mar apr jun jul aug sep sept "
           "oct nov dec monday tuesday wednesday thursday friday saturday "
           "sunday summer winter spring fall autumn christmas").split()

_PLACE_RE = re.compile(
    r"\b(?:in|around|near|across|throughout|within|serving)\s+"
    r"(?:the\s+)?((?:[A-Z][\w'’-]*)"
    r"(?:(?:\s*,\s*|\s+)(?:(?:of|de|la|le)\s+)?[A-Z][\w'’-]*)*)")

# A negator shortly before a match (same clause) means the owner ruled it out.
_NEGATOR_RE = re.compile(
    r"(?<![\w'])(?:don't|do\s+not|doesn't|no|not|never|skip|without|avoid|"
    r"except|instead\s+of|rather\s+than|isn't|aren't|won't)(?![\w'])",
    re.IGNORECASE)
_CLAUSE_SPLIT_RE = re.compile(r"[;\n]|[.,!?]+(?=\s|$)")
_DURATION_NOISE_RE = re.compile(
    r"(?<![\w'])(?:guarantee[ds]?|refunds?|trials?|returns?|warranty|"
    r"notice|delivery|shipping|ago|old)(?![\w'])", re.IGNORECASE)


def _negated(text: str, start: int) -> bool:
    """True when a negator sits within ~4 words before ``start`` in its clause."""
    before = str(text or "")[:start].replace("’", "'")
    clause = _CLAUSE_SPLIT_RE.split(before)[-1]
    tail = " ".join(clause.split()[-4:])
    return bool(_NEGATOR_RE.search(tail))

_BUSINESS_NOUNS = (
    "bakery", "cafe", "café", "coffee shop", "coffee roastery", "roastery",
    "restaurant", "salon", "barbershop", "barber shop", "spa", "studio",
    "gym", "clinic", "dental practice", "dental clinic", "store", "shop",
    "boutique", "florist", "nursery", "school", "daycare", "agency",
    "bookstore", "brewery", "bar", "pub", "hotel", "gallery", "farm",
    "landscaping business", "cleaning business", "plumbing business",
    "pet store", "pet shop", "pizzeria", "food truck", "market", "business",
)

# What the deliverable plan produces for each channel. Email has no stored
# deliverable kind of its own, so email copy is stored as ``ad_copy`` with the
# platform "Email" and shown to the owner as "Email".
_EMAIL_CHANNELS = {"email", "newsletter"}
_SOCIAL_CHANNELS = {"instagram", "facebook", "tiktok", "linkedin", "twitter",
                    "youtube", "pinterest", "reddit", "organic social"}
_AD_CHANNELS = {"google ads", "meta ads", "paid social", "search ads"}

_CHANNEL_RECOMMENDATIONS = {
    "instagram": "Short visual posts with a clear call to action in the caption.",
    "facebook": "Community-style posts that invite replies and shares.",
    "tiktok": "Short video ideas with a hook in the first seconds.",
    "linkedin": "Plain, professional posts that explain the value.",
    "email": "A short email with one clear subject line and one action.",
    "newsletter": "A short newsletter issue with one featured item.",
    "google ads": "Search ad headlines and descriptions tied to what people search for.",
    "meta ads": "Ad copy variants to compare on the same audience.",
    "blog": "One helpful article that answers a question customers ask.",
}


# ---------------------------------------------------------------------------
# extraction
# ---------------------------------------------------------------------------

def _clean(value: str, limit: int = 240) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip(" \t\r\n.,;:!?-—")[:limit]


def extract_channels(text: str) -> list[str]:
    """Channels named in the text, in the order written, canonical lower-case."""
    lowered = str(text or "").lower()
    found: list[tuple[int, str]] = []
    for name, pattern in _CHANNEL_PATTERNS:
        for match in re.finditer(r"(?<![\w-])(?:" + pattern + r")(?![\w])", lowered):
            if _negated(lowered, match.start()):
                continue
            found.append((match.start(), name))
            break
    return list(dict.fromkeys(name for _, name in sorted(found)))


def extract_duration(text: str) -> str:
    """"30-day", "two weeks", "for a month" -> "30 days", "2 weeks", "1 month"."""
    lowered = str(text or "").lower()
    word_pattern = "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True))
    for match in re.finditer(
            r"(?<![\w.])(\d{1,3}|" + word_pattern + r")\s*(?:-|\s)\s*"
            r"(days?|weeks?|months?)(?!\w)", lowered):
        raw = match.group(1)
        count = int(raw) if raw.isdigit() else _NUMBER_WORDS.get(raw, 0)
        if count < 1:
            continue
        # Only the campaign's own length counts: not a guarantee, trial, age...
        before = _CLAUSE_SPLIT_RE.split(lowered[:match.start()])[-1].split()[-3:]
        after = _CLAUSE_SPLIT_RE.split(lowered[match.end():], maxsplit=1)[0].split()[:3]
        if _DURATION_NOISE_RE.search(" ".join(before + after)) \
                or re.search(r"\bmoney[\s-]back\b", " ".join(after)):
            continue
        unit = _UNIT[match.group(2)]
        return f"{count} {unit}" + ("" if count == 1 else "s")
    return ""


def extract_audience(text: str) -> str:
    """Audience only when the owner named one.

    Accepts "targeting X", "audience: X", "aimed at X", and "for X" only when
    X ends in a recognisable group noun (families, parents, students...), so a
    stray "for our launch" is never mistaken for an audience.
    """
    value = str(text or "")
    candidate = ""
    for explicit in re.finditer(
            r"(?i)(?:\b(?:target\s+audience|audience)\s*(?:is|are|of|:|-)\s*"
            r"|\b(?:target(?:ing|ed)?|aimed\s+(?:at|towards?))\s+(?!\d))"
            r"(?:our\s+|the\s+|my\s+)?([^,.;!?\r\n]{2,160})", value):
        if not _negated(value, explicit.start()):
            candidate = explicit.group(1)
            break
    if not candidate:
        nouns = "|".join(sorted((re.escape(n) for n in _AUDIENCE_NOUNS),
                                key=len, reverse=True))
        for implicit in re.finditer(
                r"(?i)\bfor\s+((?:[\w'’-]+\s+){0,3}(?:" + nouns + r"))\b",
                value):
            if not _negated(value, implicit.start()):
                candidate = implicit.group(1)
                break
    if not candidate:
        return ""
    candidate = _AUDIENCE_END.split(candidate, maxsplit=1)[0]
    candidate = re.sub(r"(?i)^(?:our|the|my)\s+", "", candidate)
    cleaned = _clean(candidate, 160)
    # A single stop word or a bare pronoun is not an audience.
    if not cleaned or cleaned.lower() in {"it", "them", "this", "that", "us",
                                          "everyone", "anyone", "people"}:
        return cleaned if cleaned.lower() in {"everyone", "people"} else ""
    return cleaned


def extract_geography(text: str) -> str:
    """A named place ("in Austin") only. "Local" alone is not a place."""
    for match in _PLACE_RE.finditer(str(text or "")):
        place = _clean(match.group(1), 80)
        words = place.lower().replace(",", " ").split()
        if not words or words[0] in _MONTHS:
            continue
        if re.match(r"(?i)q[1-4]\b", place) or _negated(str(text or ""), match.start()):
            continue
        if words[0] in {"the", "our", "my", "a", "an", "i", "we"}:
            continue
        # Trim trailing sentence-start words glued by a comma (", Create ...").
        place = re.split(r"\s*,\s*(?=[A-Z][a-z]+\s+(?:a|an|the|my|our)\b)", place)[0]
        return place
    return ""


def extract_offer(text: str) -> str:
    """A discount, freebie or explicitly labelled offer the owner stated."""
    value = str(text or "")
    patterns = (
        r"(?i)\b((?:\$|£|€)\s?\d+(?:\.\d+)?\s*off(?:\s+(?:your|any|every|the)\s+"
        r"[\w' -]{2,40})?)",
        r"(?i)\b(\d{1,3}\s?%\s*(?:off|discount)(?:\s+(?:your|any|every|the|on|all|first)\s+"
        r"[\w' -]{2,40})?)",
        r"(?i)\b(buy\s+one,?\s+get\s+one(?:\s+(?:free|50\s?%\s*off))?)",
        r"(?i)\b(free\s+(?!shipping\s+on\b)[\w' -]{2,40}?(?=\s+(?:with|for|to|on|when|and|"
        r"at)\b|[,.;!?]|$))",
        r"(?i)\b(?:offer|offering|promo(?:tion)?|special|deal)\s*(?:is|of|:|-)\s*"
        r"([^,.;!?\r\n]{3,120})",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, value):
            if _negated(value, match.start(1)):
                continue
            return _clean(match.group(1), 160)
    return ""


def extract_goal(text: str, objective: str = "") -> str:
    """The stated purpose: "to get more bookings", "to promote our new menu"."""
    value = str(text or "")
    match = re.search(
        r"(?i)\b(?:to|so\s+(?:we|i)\s+can|in\s+order\s+to|goal\s+(?:is\s+)?(?:to|of)|"
        r"aim(?:ing)?\s+(?:to|for))\s+"
        r"((?:get|gain|win|attract|increase|boost|drive|grow|promote|launch|"
        r"generate|raise|sell|bring|announce|fill|build|introduce|encourage|"
        r"reach|improve|reduce|retain|re-?engage|recruit|collect|book)\b"
        r"[^,.;!?\r\n]{2,160})", value)
    if match:
        goal = _AUDIENCE_END.split(match.group(1), maxsplit=1)[0] \
            if re.match(r"(?i)\s*(?:target|audience)", match.group(1)) else match.group(1)
        # Cut the goal at the next owner clause (targeting / with / for N days).
        goal = re.split(
            r"(?i)\s+(?:targeting|aimed\s+at|with\s+(?:an?|the|\d)|for\s+\d|"
            r"on\s+(?:instagram|facebook|tiktok|linkedin|email)|over\s+\d)\b",
            goal, maxsplit=1)[0]
        return _clean(goal, 200)
    return _clean(objective, 200)


def extract_business(text: str) -> tuple[str, str]:
    """(business type/name, product) as the owner phrased them."""
    value = str(text or "")
    business = ""
    nouns = "|".join(sorted((re.escape(n) for n in _BUSINESS_NOUNS),
                            key=len, reverse=True))
    match = re.search(
        r"(?i)\b(?:for|of|at)\s+(?:my|our|the)\s+((?:[\w'’&-]+\s+){0,3}?(?:"
        + nouns + r"))\b", value)
    if match:
        business = _clean(match.group(1), 80)
    product = ""
    for prod in re.finditer(
            r"(?i)\b(?:promot(?:e|ing)|advertis(?:e|ing)|launch(?:ing)?|"
            r"market(?:ing)?|sell(?:ing)?|introduc(?:e|ing))\s+"
            r"(?:(?:my|our|the|a|an)\s+)?([^,.;!?\r\n]{3,100})", value):
        if _negated(value, prod.start()):
            continue
        candidate = re.split(
            r"(?i)\s+(?:to\s+|for\s+|on\s+|via\s+|with\s+|targeting\b|in\s+|"
            r"and\s+(?:an?|the|\d)\b)", prod.group(1), maxsplit=1)[0]
        product = _clean(candidate, 100)
        if product.lower() in {"it", "this", "that", "campaign", "a campaign",
                               "our campaign"}:
            product = ""
        # A preposition or verb-ish start means the capture is not a product.
        if re.match(r"(?i)(?:on|in|for|to|with|targeting|via|at|by|from|aimed)\b",
                    product) or "approval" in product.lower():
            product = ""
        if product:
            break
    return business, product


_CONSTRAINT_PATTERNS = (
    r"(?i)\b((?:no|avoid|without|never\s+use|don['’]t\s+(?:use|mention|include|"
    r"say)|do\s+not\s+(?:use|mention|include|say))\s+[^,.;!?\r\n]{2,100})",
    r"(?i)\b(budget\s*(?:is|of|:|around|under|up\s+to)?\s*(?:\$|£|€)?\s?\d[\d,]*"
    r"(?:\.\d+)?\s*(?:k|usd|dollars)?)",
    r"(?i)\b((?:under|max(?:imum)?|up\s+to|no\s+more\s+than)\s+(?:\$|£|€)\s?\d[\d,]*"
    r"(?:\.\d+)?)",
    r"(?i)\b(must\s+(?:include|mention|use|say|be)\s+[^,.;!?\r\n]{2,100})",
    r"(?i)\b(keep\s+it\s+[^,.;!?\r\n]{2,60})",
    r"(?i)\b(tone\s*(?:is|of|:)\s*[^,.;!?\r\n]{2,60})",
    r"(?i)\b((?:friendly|formal|playful|professional|casual|warm)\s+tone)\b",
)


def extract_constraints(text: str) -> list[str]:
    """Explicit owner restrictions ("no emojis", "don't mention prices")."""
    found: list[str] = []
    for pattern in _CONSTRAINT_PATTERNS:
        for match in re.finditer(pattern, str(text or "")):
            item = _clean(match.group(1), 140)
            # Not a restriction: "no later than", "no problem".
            if item and not re.match(r"(?i)no\s+(?:later|problem|worries)", item) \
                    and item.lower() not in {c.lower() for c in found}:
                found.append(item)
    return found[:6]


def extract_content_requirements(text: str) -> dict:
    """Deliverable kinds and counts the owner asked for ("5 posts")."""
    from app.services import campaign_production as production

    types = production.requested_types(text)
    counts: dict[str, int] = {}
    for number, noun in re.findall(
            r"(?i)\b(\d{1,2})(?!\s*-?\s*(?:days?|weeks?|months?)\b)\s+"
            r"(?:\w+\s+){0,2}?(posts?|captions?|emails?|"
            r"newsletters?|ads?|ad\s+variations?|headlines?)\b", str(text or "")):
        key = ("social_post" if noun.lower().startswith(("post", "caption"))
               else "email" if noun.lower().startswith(("email", "newsletter"))
               else "ad_copy")
        counts[key] = max(counts.get(key, 0), min(int(number), 30))
    return {"types": types, "counts": counts}


def extract(text: str, *, objective: str = "") -> dict:
    """Every campaign fact the owner stated. Absent facts are absent keys."""
    business, product = extract_business(text)
    facts = {
        "objective": extract_goal(text, objective),
        "target_audience": extract_audience(text),
        "channels": extract_channels(text),
        "duration": extract_duration(text),
        "geography": extract_geography(text),
        "offer": extract_offer(text),
        "business": business,
        "product": product,
        "constraints": extract_constraints(text),
    }
    content = extract_content_requirements(text)
    if content["types"] or content["counts"]:
        facts["content_requirements"] = content
    return {k: v for k, v in facts.items() if v not in ("", [], None, {})}


# ---------------------------------------------------------------------------
# the owner-facing picture: facts + sources + what is missing
# ---------------------------------------------------------------------------

#: field -> (label shown to the owner, question when missing, essential?)
FIELD_SPECS = {
    "objective": ("Goal", "What do you want this campaign to achieve?", False),
    "target_audience": ("Audience", "Who is this campaign for?", True),
    "channels": ("Channels", "Where will you post or advertise "
                 "(for example Instagram, email)?", True),
    "duration": ("Duration", "How long should the campaign run?", False),
    "offer": ("Offer", "Is there a specific offer or message to promote?", False),
    "geography": ("Area", "Which town or area are you focusing on?", False),
}
EDITABLE_FIELDS = ("objective", "target_audience", "channels", "duration",
                   "offer", "geography", "business", "product")


def _profile_fact(profile: dict, field: str) -> str:
    profile = profile or {}
    keys = {"target_audience": ("audience",), "offer": ("offer",),
            "geography": ("location",), "business": ("business_name", "name"),
            "objective": (), "product": ()}.get(field, ())
    for key in keys:
        value = str(profile.get(key) or "").strip()
        if value:
            return value[:300]
    return ""


def understanding(intake: dict | None, profile: dict | None = None) -> dict:
    """What OMOS understood, where each fact came from, and what is missing.

    Owner-request facts always win; a Business Profile fact fills a gap only for
    audience, offer, area and business name, and is labelled as such.
    """
    intake = intake if isinstance(intake, dict) else {}
    profile = profile if isinstance(profile, dict) else {}
    facts: list[dict] = []
    sources = intake.get("field_sources") if isinstance(
        intake.get("field_sources"), dict) else {}

    def add(field: str, value, source: str) -> None:
        label = FIELD_SPECS.get(field, (field.replace("_", " ").title(),))[0]
        if field == "business":
            label = "Business"
        elif field == "product":
            label = "Product"
        shown = ", ".join(CHANNEL_LABELS.get(v, v.title()) for v in value) \
            if field == "channels" else value
        facts.append({"field": field, "label": label, "value": shown,
                      "source": source,
                      "source_label": ("Told to us by you" if source == SOURCE_REQUEST
                                       else "From your business profile")})

    for field in ("business", "product", "objective", "target_audience",
                  "channels", "duration", "geography", "offer"):
        value = intake.get(field)
        if value:
            add(field, value, sources.get(field) or SOURCE_REQUEST)
            continue
        if field in ("target_audience", "offer", "geography", "business"):
            fallback = _profile_fact(profile, field)
            if fallback:
                add(field, fallback, SOURCE_PROFILE)
    constraints = [c for c in (intake.get("constraints") or []) if isinstance(c, str)]
    if constraints:
        facts.append({"field": "constraints", "label": "Constraints",
                      "value": "; ".join(constraints), "source": SOURCE_REQUEST,
                      "source_label": "Told to us by you"})
    content = intake.get("content_requirements")
    if isinstance(content, dict) and (content.get("types") or content.get("counts")):
        parts = [TYPE_NAMES.get(t, t) for t in content.get("types") or []]
        parts += [f"{n} × {TYPE_NAMES.get(k, k)}" for k, n in
                  (content.get("counts") or {}).items()]
        facts.append({"field": "content_requirements", "label": "Content wanted",
                      "value": ", ".join(parts), "source": SOURCE_REQUEST,
                      "source_label": "Told to us by you"})

    have = {f["field"] for f in facts}
    missing = []
    for field, (label, question, essential) in FIELD_SPECS.items():
        if field in have:
            continue
        # "Area" matters only when the owner said the campaign is local.
        if field == "geography":
            audience = str(intake.get("target_audience") or "")
            if not re.search(r"(?i)\blocal(?:s)?\b|\bnearby\b|\bneighbou?r", audience):
                continue
        missing.append({"field": field, "label": label, "question": question,
                        "essential": essential})
    return {"facts": facts, "missing": missing,
            "ready_to_generate": not any(m["essential"] for m in missing)}


TYPE_NAMES = {
    "strategy_brief": "strategy brief", "social_post": "social post",
    "ad_copy": "ad copy", "creative_brief": "creative brief",
    "content_calendar": "content calendar", "email": "email",
}


# ---------------------------------------------------------------------------
# the proposal
# ---------------------------------------------------------------------------

def planned_deliverables(intake: dict | None) -> list[dict]:
    """The deliverables OMOS proposes to write, derived from stated facts only.

    One entry per deliverable kind (the generator writes one block per kind and
    names every channel it covers). The owner's own list wins. Otherwise: social
    posts for the social channels chosen, ad copy for email and ad channels, and
    a strategy brief tying them together.
    """
    intake = intake if isinstance(intake, dict) else {}
    channels = [c for c in (intake.get("channels") or []) if isinstance(c, str)]
    content = intake.get("content_requirements") or {}
    asked = [t for t in (content.get("types") or []) if isinstance(t, str)]
    counts = content.get("counts") or {}
    platforms: dict[str, list[str]] = {}
    reasons: dict[str, str] = {}

    def add(kind: str, platform: str | None, why: str) -> None:
        bucket = platforms.setdefault(kind, [])
        reasons.setdefault(kind, why)
        if platform and platform not in bucket:
            bucket.append(platform)

    for kind in asked:
        add(kind, None, "You asked for this.")
    for c in channels:
        label = CHANNEL_LABELS.get(c, c.title())
        if c in _SOCIAL_CHANNELS and (not asked or "social_post" in asked):
            add("social_post", label, "You asked for this." if asked
                else f"You chose {label}.")
        elif c in _EMAIL_CHANNELS and (not asked or "ad_copy" in asked
                                       or counts.get("email")):
            add("ad_copy", "Email", "You chose email.")
        elif c in _AD_CHANNELS and (not asked or "ad_copy" in asked):
            add("ad_copy", label, "You asked for this." if asked
                else f"You chose {label}.")
    if counts.get("email") and "Email" not in platforms.get("ad_copy", []):
        add("ad_copy", "Email", "You asked for email.")
    if channels and not asked and platforms:
        add("strategy_brief", None, "Ties the channels to your goal.")

    plan = []
    for kind in DELIVERABLE_ORDER:
        if kind not in platforms:
            continue
        shown = platforms[kind]
        email_only = kind == "ad_copy" and shown == ["Email"]
        plan.append({
            "type": kind,
            "platform": ", ".join(shown) or None,
            "platforms": shown,
            "reason": reasons[kind],
            "label": "Email" if email_only
            else TYPE_NAMES.get(kind, kind).capitalize(),
        })
    return plan


DELIVERABLE_ORDER = ("strategy_brief", "social_post", "ad_copy",
                     "creative_brief", "content_calendar")


def plan_request_text(intake: dict | None, profile: dict | None = None) -> str:
    """The owner's request plus their confirmed details, as one source text.

    Generation validates facts against this text, so it contains only what the
    owner said (the original message and any details they filled in), plus the
    plan OMOS is about to write.
    """
    intake = intake if isinstance(intake, dict) else {}
    original = str((intake.get("request_facts") or {}).get("user_request") or "")
    view = understanding(intake, profile)
    lines = [original] if original else []
    for fact in view["facts"]:
        if fact["source"] == SOURCE_REQUEST and fact["field"] != "content_requirements":
            lines.append(f"{fact['label']}: {fact['value']}")
    plan = planned_deliverables(intake)
    counts = (intake.get("content_requirements") or {}).get("counts") or {}
    if plan:
        parts = []
        for item in plan:
            part = TYPE_NAMES.get(item["type"], item["type"])
            if item["platform"]:
                part += f" for {item['platform']}"
            if item["type"] == "social_post" and counts.get("social_post"):
                part = f"{counts['social_post']} {part}s"
            parts.append(part)
        lines.append("Write one deliverable block each for: " + "; ".join(parts) + ".")
    return chr(10).join(lines)[:4000]


def planned_types(intake: dict | None) -> list[str]:
    return list(dict.fromkeys(p["type"] for p in planned_deliverables(intake)))


def build_proposal(intake: dict | None, profile: dict | None = None,
                   research: dict | None = None) -> dict:
    """A reviewable campaign plan. Facts are the owner's; ideas are OMOS's."""
    intake = intake if isinstance(intake, dict) else {}
    view = understanding(intake, profile)
    fact = {f["field"]: f for f in view["facts"]}
    channels = [c for c in (intake.get("channels") or []) if isinstance(c, str)]

    focus_parts = [fact[k]["value"] for k in ("offer", "objective") if k in fact]
    message_focus = " — ".join(focus_parts) if focus_parts else ""
    if "target_audience" in fact and message_focus:
        message_focus += f" (for {fact['target_audience']['value']})"
    ideas = [{"channel": CHANNEL_LABELS.get(c, c.title()),
              "idea": _CHANNEL_RECOMMENDATIONS[c]}
             for c in channels if c in _CHANNEL_RECOMMENDATIONS]

    research = research or {}
    plan = planned_deliverables(intake)
    return {
        "audience": fact.get("target_audience", {}).get("value", ""),
        "audience_source": fact.get("target_audience", {}).get("source", ""),
        "channels": [CHANNEL_LABELS.get(c, c.title()) for c in channels],
        "duration": fact.get("duration", {}).get("value", ""),
        "message_focus": message_focus,
        "message_focus_note": (
            "Built only from what you told us. Wording is written after you "
            "approve this plan." if message_focus else
            "No offer or goal was given yet, so no message is proposed."),
        "recommendations": ideas,
        "recommendations_note": "Ideas from OMOS, not facts about your market.",
        "deliverables": plan,
        "missing": view["missing"],
        "ready_to_generate": bool(plan) and view["ready_to_generate"],
        "research": {
            "inspected": int(research.get("inspected", 0)),
            "leads": int(research.get("leads", 0)),
            "note": research.get("note", ""),
        },
    }


def summary_markdown(intake: dict | None, profile: dict | None = None) -> str:
    """Plain chat text: what was understood, what is missing, what is next."""
    view = understanding(intake, profile)
    lines = ["**What I understood**"]
    if view["facts"]:
        lines += [f"- **{f['label']}:** {f['value']}" for f in view["facts"]
                  if f["field"] != "content_requirements"]
    else:
        lines.append("- I did not catch specific details yet.")
    plan = planned_deliverables(intake)
    if plan:
        lines += ["", "**What I propose to write once you say go**"]
        lines += [f"- {p['label']}" + (f" ({p['platform']})" if p["platform"]
                                       and p["label"] != "Email" else "")
                  for p in plan]
    if view["missing"]:
        essential = [m for m in view["missing"] if m["essential"]]
        asks = essential[:2] or view["missing"][:1]
        lines += ["", "**What I still need from you**"]
        lines += [f"- {m['question']}" for m in asks]
    return "\n".join(lines)


def title_from_intake(intake: dict | None) -> str:
    """A short campaign name built only from stated duration/channels/audience."""
    intake = intake if isinstance(intake, dict) else {}
    parts = []
    duration = str(intake.get("duration") or "")
    match = re.match(r"(\d+)\s+(day|week|month)s?$", duration)
    if match:
        parts.append(f"{match.group(1)}-{match.group(2)}")
    names = [CHANNEL_LABELS.get(c, c.title()) for c in (intake.get("channels") or [])
             if isinstance(c, str)]
    if names:
        parts.append(" + ".join(names))
    parts.append("campaign")
    audience = str(intake.get("target_audience") or "")
    if audience:
        parts.append(f"for {audience}")
    return " ".join(parts)[:140]
