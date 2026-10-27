"""DEV-044: the request extractor must never invent a fact; when unsure, nothing."""
import pytest

from app.services import campaign_production as production
from app.services import campaign_request as request


@pytest.mark.parametrize("text", [
    "Don't offer 20% off, just promote the new menu on instagram",
    "No free shipping this time. Instagram campaign please",
])
def test_negated_offer_is_absent(text):
    assert "offer" not in request.extract(text)


def test_negated_constraint_may_remain():
    facts = request.extract("No free shipping this time. Instagram campaign please")
    assert facts["channels"] == ["instagram"]
    assert "No free shipping this time" in facts.get("constraints", [])


@pytest.mark.parametrize("text,expected", [
    ("Skip email. Do an Instagram campaign", ["instagram"]),
    ("Not on TikTok, only Instagram campaign", ["instagram"]),
    ("Instagram and email campaign", ["instagram", "email"]),
])
def test_negated_channels(text, expected):
    assert request.extract(text)["channels"] == expected


def test_negated_audience_never_teens():
    facts = request.extract("I'm not targeting teens. Campaign for adults on Facebook")
    assert facts.get("target_audience", "adults") == "adults"
    assert "teen" not in facts.get("target_audience", "").lower()


def test_negated_geography_and_product():
    assert "geography" not in request.extract("Not in Dubai, campaign for my gym")
    assert "product" not in request.extract("Don't promote the smoothies, just post")


@pytest.mark.parametrize("text", [
    "30 day money back guarantee on everything",
    "Offer a 14 day trial",
    "Our customers joined 2 days ago",
    "30-day return policy applies",
    "free delivery within 30 days",
])
def test_duration_rejects_non_campaign_lengths(text):
    assert "duration" not in request.extract(text)


@pytest.mark.parametrize("text,expected", [
    ("30-day Instagram and email campaign", "30 days"),
    ("campaign for 6 weeks", "6 weeks"),
    ("a two-week Facebook campaign", "2 weeks"),
    ("over 3 months", "3 months"),
    ("run it for a month", "1 month"),
])
def test_duration_kept(text, expected):
    assert request.extract(text)["duration"] == expected


def test_duration_is_not_a_deliverable_count():
    assert "email" not in request.extract_content_requirements(
        "Create a 2 week email campaign")["counts"]
    assert request.extract_content_requirements("5 social posts")["counts"] == {
        "social_post": 5}
    assert request.extract_content_requirements("3 emails")["counts"] == {"email": 3}


@pytest.mark.parametrize("text,expected", [
    ("for my gym in Dubai offering free shipping", "Dubai"),
    ("in Austin lemonade", "Austin"),
    ("launch in Cairo. No emojis", "Cairo"),
    ("Promote in Q4 to our audience", ""),
])
def test_geography_boundaries(text, expected):
    assert request.extract_geography(text) == expected


def test_geography_two_places_never_wrong():
    assert request.extract_geography("in Mumbai and Delhi") in {
        "Mumbai", "Mumbai and Delhi"}


@pytest.mark.parametrize("text", [
    "launch on Instagram",
    "our launch for approval",
    "launch in Cairo",
    "app launch targeting gamers aged 18-25",
    "how to market for small teams",
])
def test_product_rejects_prepositions(text):
    assert "product" not in request.extract(text)


def test_product_still_extracted():
    assert request.extract("promote our new menu on Instagram")["product"] == "new menu"


def test_ig_alias_removed():
    assert request.extract_channels("ig campaign") == []
    assert request.extract_channels("an insta campaign") == ["instagram"]


@pytest.mark.parametrize("text,expected", [
    ("ok do it", False), ("sure, do that", False), ("please do it", False),
    ("do this", False), ("make it", False),
    ("Yes, go ahead and write the content", True), ("go ahead", True),
    ("Looks good, generate it", True), ("write them", True),
    ("generate everything", True),
])
def test_go_ahead(text, expected):
    assert production.is_generation_go_ahead(text) is expected
