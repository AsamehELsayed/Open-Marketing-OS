"""DEV-007R-HOTFIX-2 — deterministic capability / conversation-meta intent.
DEV-008-SKILLS-OPS-HOTFIX §1 — compound marketing-task classification.

Why this exists (real founder reproduction):
  "scrape this instagram account @somebrand" previously routed to the
  knowledge branch and returned unrelated project/RAG facts because the
  lightweight classifier never distinguished an explicit tool request from a
  marketing question. This module normalizes imperative action language
  (including the observed "scrap" typo) into structured intents and is the
  single source of truth for route precedence shared by the compiled LangGraph
  and the legacy offline runner.

Route precedence implemented here (docs contract §14):
  A conversation_meta
  B tool_capability
  C approval_operation / job_followup
  D state_only (structured project-state questions)
  E knowledge
  F external/social/deep research

NOTE: the caller (`classify_to_route`) evaluates approval/job/campaign cues
BEFORE B so approval wording ("approve and send") always outranks a
capability phrase in the same message on both graph and legacy paths;
compiled `understand` mirrors that same order explicitly.

DEV-008-SKILLS-OPS-HOTFIX §1 adds one class *between* A and B:
  B' compound_marketing_task  — >= 2 distinct marketing domains requested
  B  tool_capability         — a single explicit tool request
so "حلّل موقعنا، حسابنا على انستجرام، الـSEO…" orchestrates instead of
running one capability, while "scrape @company" and "/audit <url>" keep
the exact deterministic fast route they have today.
"""
from __future__ import annotations

import re
from typing import Optional

# ---------------------------------------------------------------- meta intent
# Every phrase embeds a chat/conversation anchor token ("chat", الشات,
# المحادثة, recap, ...) so bare "ملخص"/"summarize" never triggers the
# current-thread route (dev-run contract §W1-2a).
_META_PHRASES = (
    "this chat",
    "this conversation",
    "what are we talking about",
    "what were we discussing",
    "what did i just ask",
    "what did you just do",
    "summarize our conversation",
    "summarize this conversation",
    "recap the chat",
    "what were we just talking about",
    "what are we discussing",
    "in this chat",
    "recap",
    "الشات ده",
    "الشات دا",
    "في الشات",
    "المحادثة دي",
    "في المحادثة",
    "كل حاجة حصلت",
    "حصلت في الشات",
    "الكلام اللي فات",
)

_AR_CONVERSATION_ANCHORS = ("الشات", "المحادثة")


def detect_conversation_meta(text: str) -> bool:
    """True when the user asks about the CURRENT thread, not project knowledge."""
    lowered = (text or "").lower().strip()
    if not lowered:
        return False
    if any(p in lowered for p in _META_PHRASES):
        return True
    # Arabic fallback: a bare recap/summary verb only counts when the SAME
    # message anchors it to the current chat/conversation.
    if any(a in lowered for a in _AR_CONVERSATION_ANCHORS):
        return any(t in lowered for t in ("ملخص", "ريكاب", "recap", "لخص"))
    return "recap" in lowered and bool(re.search(r"\b(chat|conversation)\b", lowered))


# -------------------------------------------------------- capability detection
_ACTION_VERBS = (
    "scrap", "scrape", "scraping", "scrapped", "audit", "check", "fetch",
    "pull", "research", "analyze", "analyse", "look up", "grab",
)
# Arabic equivalents observed in the product surface.
_AR_INTENT = ("انستجرام", "انستغرام", "انستا", "تيك توك")

_PLATFORM_WORDS = ("instagram", "insta", "ig", _AR_INTENT)
_URL_HANDLE_RE = re.compile(
    r"(?:https?://)?(?:www\.)?instagram\.com/([A-Za-z0-9_.]{1,40})")
_AT_HANDLE_RE = re.compile(r"@([A-Za-z0-9_.]{1,40})")
_STOP_ARGS = frozenset((
    "instagram", "insta", "ig", "account", "profile", "page", "posts", "post",
    "data", "the", "this", "our", "an", "a", "my", "their", "his/her",
))  # noqa: C406 — positional word extraction, smallest set needed


def _platform_hit(lowered: str) -> Optional[str]:
    for w in _PLATFORM_WORDS:
        if isinstance(w, tuple):
            for w2 in w:
                if re.search(rf"\b{re.escape(w2)}\b", lowered) or w2 in lowered:
                    return w2
        elif isinstance(w, str) and len(w) <= 3:
            # short tokens must match as words: "ig"/"insta" never inside
            # untext "strategy"/"digits"
            if re.search(rf"\b{re.escape(w)}\b", lowered):
                return w
        elif w in lowered:
            return w
    return None


def _extract_handle(text: str, lowered: str) -> str:
    m = _AT_HANDLE_RE.search(text or "")
    if m:
        return m.group(1)
    m = _URL_HANDLE_RE.search(text or "")
    if m and m.group(1).lower().rstrip("/") not in ("p", "reel", "explore"):
        return m.group(1).rstrip("/")
    # "scrape somebrand instagram" / "instagram of somebrand"
    m = re.search(
        r"(?:scrap\w*|check|audit|fetch|pull|research|analyze|analyse)\s+"
        r"([a-z0-9_.]{2,30})\s+(?:on\s+)?(?:instagram|insta|ig)", lowered)
    if m and m.group(1) not in _STOP_ARGS and m.group(1) != _platform_hit(lowered):
        return m.group(1)
    m = re.search(
        r"(?:instagram|insta|ig)\s+(?:account\s+)?(?:of\s+|for\s+|from\s+)"
        r"([a-z0-9_.@]{2,30})", lowered)
    if m and m.group(1).lstrip("@") not in _STOP_ARGS:
        return m.group(1).lstrip("@")
    m = re.search(
        r"(?:instagram|insta|ig)\s+(?:account\s+|profile\s+)?"
        r"([a-z0-9_.]{2,30})(?:\s+(?:account|profile|on instagram|on ig))?$",
        lowered)
    if m and m.group(1) not in _STOP_ARGS:
        return m.group(1)
    return ""


def _action_hit(lowered: str) -> bool:
    # word-boundary match so "scrapped the plan" doesn't match... actually
    # scrapped IS scraper intent; re.match handles scrap/scrape/scraping/
    # scrapped via the "scrap" prefix family first, then plain verbs.
    if re.search(r"\bscrap(?:e|ed|ing|er|ers|es)?\b", lowered):
        return True
    return bool(re.search(
        rf"\b(?:{'|'.join(re.escape(v) for v in _ACTION_VERBS[3:])})\b", lowered))


# ------------------------------------------------------------- slash commands
# Founder-facing deterministic tool triggers ("use tools with /"). A leading
# slash is an explicit machine command: it must reach the tool registry
# regardless of phrasing. Unknown /commands fall through untouched.
_SLASH_IG_CMDS = ("scrap", "scrape", "ig", "insta", "instagram")
_SLASH_WEBSITE_CMDS = {
    "audit": "website_marketing_audit",
    "analyze": "website_marketing_audit",
    "analyse": "website_marketing_audit",
    "fetch": "website_fetch",
    "open": "website_fetch",
    "crawl": "website_crawl",
}
_SLASH_HANDLE_RE = re.compile(r"@?([A-Za-z0-9_.]{2,40})")


def detect_slash_intent(text: str) -> Optional[dict]:
    """Parse an explicit /-command into a structured capability intent.

    Supported: /scrap|/scrape|/ig|/insta|/instagram [handle] →
    instagram_public_profile; /audit|/analyze|/analyse [url] →
    website_marketing_audit; /fetch|/open [url] → website_fetch;
    /crawl [url] → website_crawl. Empty args autofill from project state in
    the executor (handle via registered social accounts, url via the project
    website). Anything not starting with "/" — or an unknown first token —
    returns None so normal detection continues.
    """
    raw = (text or "").strip()
    if not raw.startswith("/"):
        return None
    tokens = raw[1:].split()
    if not tokens:
        return None
    cmd = tokens[0].lower().strip("/").rstrip(",")
    rest = " ".join(tokens[1:])
    if cmd in _SLASH_IG_CMDS:
        m = _SLASH_HANDLE_RE.search(rest)
        handle = m.group(1) if m else ""
        return {
            "intent_type": "TOOL_CAPABILITY",
            "capability": "instagram_public_profile",
            "arguments": {"handle": handle},
            "missing": [] if handle else ["handle"],
            "confidence": 0.95 if handle else 0.8,
            "source_text": raw[:300],
        }
    capability = _SLASH_WEBSITE_CMDS.get(cmd)
    if capability is None:
        return None
    url = _extract_website_url(rest)
    return {
        "intent_type": "TOOL_CAPABILITY",
        "capability": capability,
        "arguments": {"url": url},
        "missing": [] if url else ["url"],
        "confidence": 0.95 if url else 0.8,
        "source_text": raw[:300],
    }


def detect_capability_intent(text: str) -> Optional[dict]:
    """Return a structured capability request, or None when unrelated.

    Shaped for the ToolRegistry contract: never embeds provider details —
    only the frozen capability string plus normalized arguments. Explicit
    /-commands (detect_slash_intent) take precedence here.
    """
    lowered = (text or "").lower().strip()
    if not lowered:
        return None
    slash = detect_slash_intent(text)
    if slash is not None:
        return slash
    platform = _platform_hit(lowered)
    continuation = bool(re.search(
        r"\b(?:scrap(?:e|ing|er)?|scraping)\s+(?:tool|tools)\b", lowered))
    # "استخدم ال tools واعمل scrap" / "auch" mixed-language: a scrap-family
    # verb plus an explicit tool word in the same message is an explicit
    # tool request even without a platform word.
    if not continuation:
        continuation = (re.search(r"\bscrap", lowered) is not None
                        and "tool" in lowered)
    # Continuation ("just use scraping tool") resumes the previous permission
    # to run the scraping capability even when a platform word is absent.
    if platform is None:
        if not continuation:
            return None
    handle = _extract_handle(text, lowered)
    action = _action_hit(lowered) or handle != "" or continuation
    if not action:
        return None
    missing = [] if handle else ["handle"]
    return {
        "intent_type": "TOOL_CAPABILITY",
        "capability": "instagram_public_profile",
        "arguments": {"handle": handle},
        "missing": missing,
        "confidence": 0.9 if handle else (0.75 if continuation else 0.7),
        "source_text": (text or "")[:300],
    }


# ------------------------------------------------- website capability detection
_WEBSITE_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?[a-z0-9-]+\.[a-z]{2,}", re.IGNORECASE)
_WEBSITE_VERBS_EN = ("analyze", "analyse", "audit", "crawl", "fetch",
                     "browse", "open")
_WEBSITE_VERBS_AR = ("تحلل", "حلل", "تحليل", "افحص", "فحص", "دور على", "زور",
                     "افتح")
_WEBSITE_ANCHORS = ("موقع", "website", "site", "web", "الموقع", ".com")

_CRAWL_VERBS = ("crawl",)
_FETCH_OPEN_VERBS = ("fetch", "browse", "open", "افتح")


def _extract_website_url(text: str) -> str:
    candidates = _WEBSITE_URL_RE.findall(text or "")
    for raw in candidates:
        url = raw.strip().lower().rstrip(".,!?;:،؟")
        url = re.sub(r"/+$", "", url)
        if url and "." in url and not url.endswith("."):
            return url
    return ""


def detect_website_intent(text: str) -> Optional[dict]:
    """Website capability extraction (URL/verb/anchor gated).

    Detect URLs via regex and action verbs (EN/AR). Requires an anchor
    (موقع/website/site/web/.com/URL present). Maps to website_crawl /
    website_fetch / website_marketing_audit.
    """
    raw_text = text or ""
    lowered = raw_text.lower().strip()
    if not lowered:
        return None
    url = _extract_website_url(raw_text)
    has_url = bool(url)
    anchor = has_url or ".com" in lowered or any(a in lowered for a in _WEBSITE_ANCHORS)
    if not anchor:
        return None
    verb_hit = any(v in lowered for v in _WEBSITE_VERBS_EN + _WEBSITE_VERBS_AR)
    if not verb_hit:
        return None
    if "crawl" in lowered:
        capability = "website_crawl"
    elif any(v in lowered for v in _FETCH_OPEN_VERBS):
        capability = "website_fetch"
    else:
        capability = "website_marketing_audit"
    missing = [] if url else ["url"]
    return {
        "intent_type": "TOOL_CAPABILITY",
        "capability": capability,
        "arguments": {"url": url},
        "missing": missing,
        "confidence": 0.9 if url else 0.75,
        "source_text": raw_text[:300],
    }


# ------------------------------------------- compound marketing task (DEV-008HF)
# §1: "two or more DISTINCT marketing domains are requested in one message" is
# an orchestration request, not a tool call. Detection is lexical and bilingual
# (the pinned founder prompt of §10 is Arabic), and deliberately conservative:
# the DEV-008-SKILLS-OPS corpus is frozen on single-target capability prompts
# whose own text contains two domain words ("an SEO audit of our SaaS
# **website**"), so raw keyword counting would rewrite pinned behavior. Three
# conditions must therefore all hold, and each exists to keep a frozen
# single-capability ask on the fast route:
#   1. no leading slash command (an explicit machine command always wins),
#   2. an explicit request/directive verb is present (an enumerated ASK, not a
#      statement of symptoms), and
#   3. two or more domain-bearing clauses inside ONE sentence (an enumeration
#      "our website, our instagram, …" — not two domain words spread across
#      separate sentences of one argument).
# Documented as a contract deviation in the W1 artifact.

INTENT_CLASSES: tuple[str, ...] = (
    "CONVERSATION_META",
    "COMPOUND_MARKETING_TASK",
    "SINGLE_CAPABILITY",
    "PROJECT_STATE",
    "KNOWLEDGE",
    "MARKETING_REASONING",
)

MARKETING_DOMAINS: tuple[str, ...] = (
    "website", "seo", "cro", "social", "instagram",
    "competitor", "positioning", "experiment",
)

# Latin markers match as whole tokens (``seo`` never inside ``strategy``);
# Arabic markers match as substrings, because Arabic is written with prefixes
# and suffixes rather than spaces ("موقعنا" / "الموقع" / "مواقع").
_DOMAIN_MARKERS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "website": (
        ("website", "web site", "webpage", "web page", "landing page",
         "homepage", "home page"),
        ("موقع", "صفح", "لاندينج"),
    ),
    "seo": (
        ("seo", "search engine optimization", "search engine",
         "organic search", "ai search", "search visibility",
         "search readiness", "serp", "search ranking", "indexation"),
        ("سيو", "محركات البحث", "ترتيب البحث", "نتائج البحث", "مفتاح"),
    ),
    "cro": (
        ("cro", "conversion", "convert", "funnel", "checkout",
         "trial conversion"),
        ("تحويل", "قمع"),
    ),
    "social": (
        ("social media", "social", "linkedin", "tiktok", "facebook",
         "twitter", "reels", "shorts"),
        ("سوشيال", "فيسبوك", "فيس بوك", "تيك توك", "مواصلات اجتماعي"),
    ),
    # instagram is its own token: §10 counts the pinned prompt as SEVEN
    # domains, so a platform mention must not also claim "social".
    "instagram": (
        ("instagram", "insta", "ig"),
        ("انستجرام", "انستغرام", "انستقرام"),
    ),
    "competitor": (
        ("competitor", "competition", "competitive", "rival",
         "market landscape"),
        ("منافس", "خصوم"),
    ),
    "positioning": (
        ("positioning", "position", "brand", "messaging",
         "value proposition", "differentiator", "brand voice", "usp"),
        ("تموضع", "تمركز", "براند", "هويه"),
    ),
    "experiment": (
        ("experiment", "a b test", "ab test", "split test", "hypothesis",
         "variant test"),
        ("تجربه", "تجارب", "اختبار", "فرضيه"),
    ),
}

# An explicit ASK. Without one, a turn that merely states symptoms ("we get
# 5,000 visitors but only 1.2% convert") is a conversation, not an
# enumerated set of deliverables.
_REQUEST_VERBS_LATIN = (
    "analyze", "analyse", "audit", "review", "check", "compare", "improve",
    "optimize", "optimise", "suggest", "propose", "recommend", "plan",
    "create", "build", "write", "give", "breakdown", "break down", "dig",
    "look", "fix", "grow", "ideate", "research", "examine", "assess",
    "evaluate", "inspect", "map", "benchmark", "measure", "solve", "handle",
    "address", "boost", "increase", "outline", "walk", "deep dive",
    "explain", "walk me through",
)
_REQUEST_VERBS_ARABIC = (
    "حلل", "حل", "تحليل", "افحص", "فحص", "راجع", "مراجعه", "حسن",
    "اقترح", "اكتب", "اعمل", "شوف", "دور", "قارن", "خطط", "خطه", "جيب",
    "عالج", "اشرح", "وضح", "نصيحه", "رايك", "استراتيجيه", "نمو",
)

# Arabic tashkeel (including the shadda in "حلّل") and the tatweel of "الـ" are
# stripped before matching; the tatweel is a connector, not a letter.
_ARABIC_MARKS_RE = re.compile("[\u064b-\u0655\u0670\u0640]")
_SEPARATORS_RE = re.compile(r"[\W_]+", re.UNICODE)
# A full stop only ends a sentence when it is a full stop: not the dot of
# "example.com", not the decimal point of "1.2%".
_SENTENCE_SPLIT_RE = re.compile(r"[!?؟\n\r]+|(?<![0-9A-Za-z])\.(?=\s|$)")
_CLAUSE_SPLIT_RE = re.compile(r"[,،؛;|\u2022]+")
_CONJUNCTION_TOKENS = frozenset({
    "and", "&", "plus", "then", "also",
    "ثم", "بعدين", "كمان", "و", "اضافه", "ايضا", "بالاضافه",
})
_LATIN_TOKEN_CACHE: dict[str, "re.Pattern[str]"] = {}


def _latin_token(term: str) -> "re.Pattern[str]":
    """Whole-token matcher for a Latin marker.

    ASCII-only lookarounds on purpose: the pinned prompt writes the domain as
    "الـSEO", where the preceding character is an Arabic letter, so a ``\\b``
    boundary (which treats that letter as a word character) would never match.
    The optional plural tail keeps "our competitors" / "3 growth experiments"
    on the same domain as the singular marker.
    """
    pat = _LATIN_TOKEN_CACHE.get(term)
    if pat is None:
        pat = re.compile(
            rf"(?<![a-z0-9]){re.escape(term)}(?:e?s)?(?![a-z0-9])",
            re.IGNORECASE)
        _LATIN_TOKEN_CACHE[term] = pat
    return pat


def _normalize(text: str) -> str:
    """Lowercase, fold Arabic spelling, collapse every separator to space.

    Tashkeel (the shadda in "حلّل") and the tatweel of "الـ" are dropped — they
    are not letters — and ta marbuta is folded to heh so one stem covers both
    "صفحة" and "صفحه".
    """
    cleaned = _ARABIC_MARKS_RE.sub("", (text or "").lower())
    cleaned = cleaned.replace("\u0629", "\u0647")
    return _SEPARATORS_RE.sub(" ", cleaned).strip()


def _domains_in(text: str) -> list[str]:
    """Every marketing domain named by ``text``, in MARKETING_DOMAINS order.

    A clause that carries a URL names the website domain even without the word
    "website": "/audit https://example.com and check our competitors" is a
    website ask and a competitor ask, and the URL is the website half of it.
    """
    norm = _normalize(text)
    if not norm:
        return []
    found: list[str] = []
    for domain in MARKETING_DOMAINS:
        latin, arabic = _DOMAIN_MARKERS[domain]
        if any(term in norm for term in arabic):
            found.append(domain)
            continue
        if any(_latin_token(term).search(norm) for term in latin):
            found.append(domain)
    if "website" not in found and _extract_website_url(text or ""):
        found.append("website")
    return [d for d in MARKETING_DOMAINS if d in found]


def _clauses(sentence: str) -> list[str]:
    """Coordinated request items of one sentence, order preserved.

    Punctuation and coordination words both end an item, so "our website, our
    instagram and our SEO" is three items rather than one clause.
    """
    clauses: list[str] = []
    current: list[str] = []
    for raw in _CLAUSE_SPLIT_RE.split(sentence or ""):
        for word in raw.split():
            if word in _CONJUNCTION_TOKENS:
                if current:
                    clauses.append(" ".join(current))
                    current = []
                continue
            current.append(word)
        if current:  # a comma ends an item
            clauses.append(" ".join(current))
            current = []
    if current:
        clauses.append(" ".join(current))
    return clauses


def _compound_enumeration(text: str) -> list[str]:
    """Domains requested as >= 2 coordinated items of a single sentence.

    "Items" means clauses that each contribute a domain the others do not: two
    domain words inside one clause ("an SEO audit of our website") are one ask
    and must stay a single capability, while "our website, our instagram, …"
    is a list and is an orchestration request. Returns the union of the domains
    of the best such sentence, else ``[]``.
    """
    best: list[str] = []
    for sentence in _SENTENCE_SPLIT_RE.split((text or "")):
        hits: list[str] = []
        items = 0
        for clause in _clauses(sentence):
            fresh = [d for d in _domains_in(clause) if d not in hits]
            if not fresh:
                continue
            items += 1
            hits.extend(fresh)
        if items >= 2 and len(hits) > len(best):
            best = hits
    return best


def _has_request_verb(text: str) -> bool:
    norm = _normalize(text)
    if not norm:
        return False
    if any(term in norm for term in _REQUEST_VERBS_ARABIC):
        return True
    return any(_latin_token(term).search(norm) for term in _REQUEST_VERBS_LATIN)


def detect_compound_task(text: str) -> Optional[dict]:
    """A turn that requests >= 2 distinct marketing domains at once.

    Returns ``{"intent_class": "COMPOUND_MARKETING_TASK", "domains": [...],
    "confidence": float, "reason": str}``, or ``None`` for a single capability,
    a slash command, a current-thread question, or anything below the
    enumeration threshold. Never raises: any failure degrades to ``None``,
    which keeps the single-capability fast route.
    """
    try:
        raw = text or ""
        if not raw.strip():
            return None
        # (1) an explicit /command is a machine instruction, never a fan-out.
        if detect_slash_intent(raw) is not None:
            return None
        # (2) an enumerated ASK needs an explicit directive verb.
        if not _has_request_verb(raw):
            return None
        # (3) two or more domain-bearing clauses of one sentence.
        domains = _compound_enumeration(raw)
        if len(domains) < 2:
            return None
        ordered = [d for d in MARKETING_DOMAINS if d in domains]
        confidence = min(0.95, 0.78 + 0.03 * len(ordered))
        return {
            "intent_class": "COMPOUND_MARKETING_TASK",
            "domains": ordered,
            "confidence": round(confidence, 3),
            "reason": (f"{len(ordered)} distinct marketing domains requested "
                       f"in one turn: {', '.join(ordered)}"),
        }
    except Exception:
        return None


# Project-state vocabulary, mirroring the legacy flow names so the class and
# the legacy `state_only` route never disagree. Read from the legacy
# classifier when it imports, with this list as the fail-closed fallback.
_PROJECT_STATE_FLOWS = frozenset({
    "attention", "approvals", "blocked", "next", "company", "experiment",
    "changed",
})
_PROJECT_STATE_CUES = (
    "needs my attention", "what needs attention", "pending approvals",
    "show approvals", "what is blocked", "what's blocked", "next step",
    "what's next", "what is next", "current state", "project state",
    "our experiment", "what changed", "company profile", "inWaiting",
    "اللي محتاج انتباهي", "الموافقات المعلقة", "اللي متوقف", "الخطوه الجايه",
    "حاله المشروع", "ايه اللي اتغير",
)
_KNOWLEDGE_CUES = (
    "why do we", "why did we", "why are we", "why is", "why was",
    "what do we know", "what did we decide", "what did you", "what is our",
    "how do we use", "how did we", "what are our", "مناذا", "لماذا",
    "ليه", "ليه بنستخدم", "ايه اللي عرفناه", "إيه اللي عرفناه",
)
_MARKETING_REASONING_CUES = (
    "what should we", "what do you think", "your opinion", "ideas",
    "strategy", "strategic", "positioning", "campaign", "growth", "grow",
    "audience", "offer", "pricing", "messaging", "content calendar",
    "go to market", "gtm", "نصيحة", "رايك", "استراتيجيه", "تسويق",
    "نمو", "جمهور", "عروض", "تسعير", "محتوى",
)


def _legacy_state_flow(text: str) -> str:
    try:
        from app.services import account_manager as legacy

        flow = str(legacy.classify(text or "") or "")
    except Exception:
        flow = ""
    return flow if flow in _PROJECT_STATE_FLOWS else ""


def classify_task_class(text: str) -> str:
    """The single intent class of a turn: one of INTENT_CLASSES, in order.

    ``CONVERSATION_META`` > ``COMPOUND_MARKETING_TASK`` > ``SINGLE_CAPABILITY``
    > ``PROJECT_STATE`` > ``KNOWLEDGE`` > ``MARKETING_REASONING``. Never
    raises and never returns an empty string: any internal failure degrades to
    the catch-all class.
    """
    try:
        raw = text or ""
        if not raw.strip():
            return "MARKETING_REASONING"
        if detect_conversation_meta(raw):
            return "CONVERSATION_META"
        if detect_compound_task(raw) is not None:
            return "COMPOUND_MARKETING_TASK"
        if (detect_capability_intent(raw) is not None
                or detect_website_intent(raw) is not None):
            return "SINGLE_CAPABILITY"
        norm = _normalize(raw)
        if _legacy_state_flow(raw) or any(c in norm for c in _PROJECT_STATE_CUES):
            return "PROJECT_STATE"
        if any(c in norm for c in _MARKETING_REASONING_CUES):
            return "MARKETING_REASONING"
        if any(c in norm for c in _KNOWLEDGE_CUES) or "?" in raw:
            return "KNOWLEDGE"
        return "MARKETING_REASONING"
    except Exception:
        return "MARKETING_REASONING"


# -------------------------------------------------------------- integrated api
def classify_intent(text: str) -> dict:
    """Route precedence (see module doc). Carries the matched intent forward —
    route must never lose the capability extraction it was based on.

    DEV-008-SKILLS-OPS-HOTFIX §1: every return additionally carries
    ``intent_class``. The compound check sits between conversation_meta and
    the single-capability checks, so a multi-domain turn orchestrates instead
    of collapsing onto whichever one capability happened to match.
    """
    if detect_conversation_meta(text):
        return {"route": "conversation_meta", "intent_class": "CONVERSATION_META",
                "confidence": 0.95,
                "reason": "current-thread meta question"}
    compound = detect_compound_task(text)
    if compound is not None:
        return {"route": "compound_marketing_task",
                "intent_class": compound["intent_class"],
                "domains": compound["domains"],
                "confidence": compound["confidence"],
                "reason": compound["reason"]}
    cap = detect_capability_intent(text)
    if cap is not None:
        return {"route": "tool_capability",
                "intent_class": "SINGLE_CAPABILITY", "capability": cap,
                "confidence": cap["confidence"],
                "reason": "explicit capability command"}
    web = detect_website_intent(text)
    if web is not None:
        return {"route": "tool_capability",
                "intent_class": "SINGLE_CAPABILITY", "capability": web,
                "confidence": web["confidence"],
                "reason": "website capability command"}
    return {"route": "", "intent_class": classify_task_class(text),
            "confidence": 0.0,
            "reason": "not a meta/capability intent; classify_to_route continues"}
