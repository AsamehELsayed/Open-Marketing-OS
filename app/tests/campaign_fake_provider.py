"""Deterministic local provider for campaign acceptance tests.

This is an authorized *test double*, not a model. It is plugged in as the
``local_provider`` of the real ``ModelRouter`` so the real production path runs
end to end: prompt building, routing, ``model_calls`` telemetry, output
validation, and deliverable persistence. It makes no network call and spends
nothing.

What it proves: the product plumbing (request -> plan -> generation -> review ->
revision -> approval -> export) and its honesty rules. What it does NOT prove:
the quality, accuracy or tone of any real AI model. Its text is templated from
the facts in the request and is deliberately plain.
"""
from __future__ import annotations

import json
import re
from types import SimpleNamespace

_FACT_LINE = re.compile(
    r"^(Goal|Audience|Channels|Duration|Offer|Area|Business|Product|"
    r"Constraints): (.+)$", re.MULTILINE)
_PLATFORM_FOR = {
    "strategy_brief": None,
}
_TITLES = {
    "strategy_brief": "Campaign strategy brief",
    "social_post": "Social post",
    "ad_copy": "Promotional copy",
    "creative_brief": "Creative brief",
    "content_calendar": "Content calendar",
}


def _platforms(user_request: str) -> dict[str, str]:
    """type -> platform, read from the plan line OMOS appends to the request."""
    found: dict[str, str] = {}
    line = re.search(r"Write one deliverable block each for: (.+)\.", user_request)
    if not line:
        return found
    for part in line.group(1).split(";"):
        match = re.match(r"\s*(?:\d+\s+)?(strategy brief|social post|ad copy|"
                         r"creative brief|content calendar)s?(?: for (.+))?\s*$",
                         part.strip())
        if match:
            kind = match.group(1).replace(" ", "_")
            if match.group(2):
                found[kind] = match.group(2).strip()
    return found


_FINAL_COPY = {
    "social_post": (
        "Fresh mornings start with wholesome baking. Handcrafted daily with honest ingredients, "
        "our breads and pastries are made to bring local families together around the table.\n\n"
        "Stop by your neighborhood bakery this week and taste the difference that love and craft make."
    ),
    "ad_copy_email": (
        "Subject: Fresh morning favorites for the family\n\n"
        "Hi there,\n\n"
        "Nothing brings a family together like the aroma of freshly baked bread in the morning. "
        "At your neighborhood bakery, we wake up before sunrise to craft every loaf, pastry, and "
        "treat using only wholesome, honest ingredients.\n\n"
        "Whether you are packing school lunches or sharing breakfast together, we are here to make "
        "your mornings a little brighter.\n\n"
        "Warmly,\n"
        "Your Neighborhood Bakery Team"
    ),
    "ad_copy": (
        "Headline: Wholesome Baking for Your Family Table\n\n"
        "Body: Crafted fresh every morning with simple, honest ingredients. "
        "Give your family the taste of real neighborhood baking.\n\n"
        "Call to action: Learn More"
    ),
    "strategy_brief": (
        "Core Strategy: Connect with local families by emphasizing handcrafted morning freshness "
        "and wholesome neighborhood tradition.\n\n"
        "Key Message: Honest ingredients, warm hospitality, and daily scratch baking for family moments."
    ),
    "creative_brief": (
        "Visual Theme: Natural morning daylight highlighting golden-crust artisan loaves and "
        "warm family kitchen tables.\n\n"
        "Tone & Voice: Warm, authentic, approachable, and craft-centered."
    ),
    "content_calendar": (
        "Weekly Rhythm:\n"
        "- Opening Feature: Behind the scenes dough preparation and craft insights\n"
        "- Midweek Spotlight: Wholesome family breakfast pairings\n"
        "- Weekend Showcase: Artisan specialty loaf highlights"
    ),
}


def _block(kind: str, title: str, platform: str | None, facts: list[str],
           suggestion: str, unknown: str) -> str:
    attrs = f'type="{kind}" title="{title}"'
    attrs += f' platform="{platform}"' if platform else ' platform=""'
    fact_lines = "\n".join(f"- {line}" for line in facts) or \
        "No persisted facts supplied."
    if kind == "ad_copy" and platform and platform.lower() == "email":
        copy_text = _FINAL_COPY["ad_copy_email"]
    else:
        copy_text = _FINAL_COPY.get(kind, "")
    copy_section = f"## Final copy\n{copy_text}\n\n" if copy_text else ""
    return (f"## DELIVERABLE {attrs}\n\n"
            f"{copy_section}"
            f"## Persisted user facts\n{fact_lines}\n\n"
            "## Retrieved evidence\nNo project evidence available\n\n"
            f"## AI suggestions\n{suggestion}\n\n"
            f"## Unknown or unsupported information\n{unknown}\n")



class DeterministicCampaignProvider:
    """Templated, offline stand-in for an AI provider (test use only)."""

    name = "deterministic-test"

    def __init__(self):
        self.calls: list[dict] = []

    def complete(self, *, system, messages, tools, opts=None):
        try:
            user = json.loads(messages[0]["content"]) if messages else {}
        except (TypeError, ValueError):
            user = {}
        if not isinstance(user, dict) or not (
                "revision_request" in user or "requested_types" in user):
            # Not a campaign call (for example conversational synthesis).
            self.calls.append({"kind": "other", "user": {}})
            return SimpleNamespace(text="Understood.", usage={
                "input_tokens": 1, "output_tokens": 1, "total_tokens": 2})
        self.calls.append({"kind": "revision" if "revision_request" in user
                           else "generation", "user": user})
        if "revision_request" in user:
            text = self._revise(user)
        else:
            text = self._generate(user)
        return SimpleNamespace(text=text, usage={
            "input_tokens": 1, "output_tokens": 1, "total_tokens": 2})

    def _generate(self, user: dict) -> str:
        request = str(user.get("user_request") or "")
        facts = [f"{label}: {value}" for label, value in _FACT_LINE.findall(request)]
        audience = next((v for k, v in _FACT_LINE.findall(request)
                         if k == "Audience"), "your customers")
        platforms = _platforms(request)
        blocks = []
        for kind in user.get("requested_types") or []:
            platform = platforms.get(kind)
            title = _TITLES.get(kind, "Deliverable")
            if platform:
                title += f" for {platform}"
            suggestion = (f"Suggestion, not a fact: write in a friendly voice "
                          f"for {audience} and end with one clear invitation.")
            blocks.append(_block(
                kind, title, platform, facts, suggestion,
                "No promotion details were supplied, so none are stated."))
        return "\n".join(blocks)

    def _revise(self, user: dict) -> str:
        current = user.get("current_deliverable") or {}
        instruction = str(user.get("revision_request") or "")
        body = str(current.get("content_md") or "")
        note = f"Revised as requested ({instruction.strip()})."
        final_match = re.search(r"(?im)^\s*#{1,6}\s*Final copy\s*$", body)
        audit_match = re.search(
            r"(?im)^\s*#{1,6}\s*(?:Persisted user facts|Retrieved evidence|"
            r"AI suggestions|Recommendations|Unknown or unsupported information|"
            r"Missing information)\b",
            body,
        )
        if final_match and audit_match and audit_match.start() > final_match.end():
            before_audit = body[:audit_match.start()].rstrip()
            after_audit = body[audit_match.start():]
            body = f"{before_audit}\n\n{note}\n\n{after_audit}"
        marker = re.search(r"(?im)^##\s*AI suggestions\s*$", body)
        if marker:
            body = body[:marker.end()] + f"\n{note}" + body[marker.end():]
        else:
            body += f"\n\n## AI suggestions\n{note}\n"
        kind = str(current.get("type") or "social_post")
        title = str(current.get("title") or _TITLES.get(kind, "Deliverable"))
        platform = current.get("platform")
        attrs = f'type="{kind}" title="{title}" platform="{platform or ""}"'
        return f"## DELIVERABLE {attrs}\n\n{body.strip()}\n"
