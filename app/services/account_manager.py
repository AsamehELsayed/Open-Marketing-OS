"""Account Manager: deterministic turn context, NOT an autonomous agent.

Pipeline: classify → direct SQLite state → focused retrieval (only when
needed) → evidence pack → bilingual synthesis → reply + separate sources.

Retrieved chunks are INTERNAL context. The reply never pastes chunk text,
filenames, or status tags unless the user explicitly asks for sources.
Handlers perform zero mutations (the chat route owns persistence).
"""
import json
import re

from app.database import repos
from app.services import state as store
from app.services.rag import context_router

# ---- flows ----
FLOW_ATTENTION = "attention"
FLOW_APPROVALS = "approvals"
FLOW_CHANGED = "changed"
FLOW_BLOCKED = "blocked"
FLOW_NEXT = "next"
FLOW_COMPANY = "company"
FLOW_EXPERIMENT = "experiment"
FLOW_SOURCES = "sources"
FLOW_ASSESSMENT = "assessment"
FLOW_EXPLAIN = "explain"
FLOW_RECOMMEND = "recommend"
FLOW_DOCUMENTARY = "documentary"

_KEYWORDS = {
    FLOW_APPROVALS: ("approval", "approve", "sign off", "sign-off", "موافقة", "موافقتي", "اعتماد"),
    FLOW_CHANGED: ("changed", "change", "new", "update", "latest", "ايه الجديد", "إيه الجديد", "اتغير"),
    FLOW_BLOCKED: ("blocked", "blocker", "stuck", "stop", "waiting on", "متعطل", "واقف"),
    FLOW_NEXT: ("next", "should i", "priority", "priorities", "do now", "الخطوة", "اعمل ايه"),
    FLOW_COMPANY: ("company", "الشركة", "company profile", "account info",
                   "business info", "بيانات الشركة"),
    FLOW_EXPERIMENT: ("experiment", "measure", "measurement", "result", "learn", "تجربة", "نتائج", "نتايج", "قياس"),
    FLOW_ATTENTION: ("attention", "needs me", "need me", "todo", "to-do", "focus", "status",
                     "محتاج", "مركزين", "مركزين", "على ايه", "علي ايه"),
}

_OPINION_CUES = ("رأيك", "رايك", "ايه رأيك", "إيه رأيك", "شايف", "تقييم", "تقييمك",
                 "what do you think", "opinion", "assess", "evaluate", "your take")
_COMPANY_CUES = ("شركة", "company", "business")
_EXPLAIN_CUES = ("why", "ليه", "ازاي", "اشمعنا", "إزاي", "how come", "for what reason", "سبب")
_RECOMMEND_CUES = ("تنصح", "تقترح", "تنصحني", "الأفضل", "الافضل", "الأحسن", "advise", "advice",
                   "recommend", "suggest")
_SOURCE_CUES = ("وريني المصدر", "وريني مصادر", "المصدر", "المصادر", "النص الأصلي", "اقتبس",
                "show me the source", "show sources", "quote", "exact document",
                "what does the file say")

_DOC_CUES = ("why", "background", "evidence", "research", "explain", "tell me about", "docs",
             "ليه", "ازاي", "دليل", "أبحاث", "ابحاث")

_ARABIC_RE = re.compile(r"[\u0600-\u06FF]")


def detect_language(text: str) -> str:
    return "ar" if _ARABIC_RE.search(text) else "en"


def classify(text: str) -> str:
    lowered = text.lower()
    # Assessment first: opinion + company cues would otherwise be swallowed
    # by the generic company flow.
    has_opinion = any(w in lowered for w in _OPINION_CUES)
    has_company = any(w in lowered for w in _COMPANY_CUES)
    if has_opinion and has_company:
        return FLOW_ASSESSMENT
    for flow, words in _KEYWORDS.items():
        # ``new`` is a standalone change cue. Substring matching made
        # ``renewal`` look like ``new`` and misrouted policy questions into
        # project-state answers, bypassing knowledge retrieval.
        if any((re.search(r"(?<![a-z0-9])new(?![a-z0-9])", lowered)
                if w == "new" else w in lowered) for w in words):
            return flow
    if any(w in lowered for w in _SOURCE_CUES):
        return FLOW_SOURCES
    if any(w in lowered for w in _EXPLAIN_CUES):
        return FLOW_EXPLAIN
    if any(w in lowered for w in _RECOMMEND_CUES):
        return FLOW_RECOMMEND
    return FLOW_DOCUMENTARY


# ---- evidence signals (keyword presence in the pack, en+ar) ----
_SIGNAL_KEYS = {
    "demo_offer": ("free demo", "homepage concept", "ديمو", "تصميم مجاني", "مفهوم مجاني"),
    "walk_away": ("walk away", "zero commitment", "يمشي", "من غير التزام", "بدون التزام"),
    "ownership": ("source code", "client's name", "ownership", "after full payment",
                  "السورس", "الملكية", "باسم العميل", "بعد السداد"),
    "white_label": ("white-label", "white label", "وايت", "تحت اسم", "invisible"),
    "resell": ("resell", "under own brand", "يبيع", "باسمها", "باسم الوكالة"),
    "nda": ("nda", "اتفاقية سرية", "سرية"),
    "proof_projects": ("live-link", "live project", "portfolio", "مشاريع حية", "معرض"),
    "no_testimonials": ("no verified metrics", "none on record", "no testimonials",
                        "مفيش", "لا يوجد", "غير مسجلة", "unknown"),
    "markets_rank": ("primary market", "expansion market", "target market",
                     "السوق", "أسواق", "التوسع"),
    "quote_pricing": ("quote-only", "quote per brief", "custom quote", "عرض سعر"),
}

# signal -> (ar line, en line, kind). kind: fact | gap | inference
#
# DEV-008-PUBLISH-GATE — READ THIS BEFORE EDITING.
#
# These lines used to assert the commercial details of one specific real
# business: a free-demo offer, source-code ownership terms, a white-label/NDA
# arrangement, "7 live named projects", agencies as the founder-confirmed major
# segment, mid-market quote-per-brief pricing, and a hardcoded market priority
# ladder reading "Focus order: Jordan, then Egypt, Saudi Arabia, then UAE."
#
# Two problems, both shipping to every user of the frozen build:
#
#   1. PRIVACY. That is a named third party's strategy, hardcoded in an
#      Apache-2.0 product's source.
#   2. CORRECTNESS, and worse than the privacy problem. `_pack_signals` only
#      checks whether a keyword appears in the *reader's own* evidence pack,
#      then `_fact_lines` emitted the sentence above tagged "(recorded fact)".
#      So any user whose notes happened to mention an NDA was told their
#      business has a white-label NDA arrangement, and any user whose notes
#      mentioned Jordan was told their market priority is Jordan, then Egypt,
#      then Saudi Arabia, then UAE. The app was confidently lying to every
#      customer who was not that one company.
#
# Every line below is therefore phrased as a statement about *the reader's own
# evidence*, never as an assertion about their business. `_tag` labels these
# "(recorded fact)", which is now accurate: the recorded thing is what their
# files say. Do not reintroduce a specific offer, price, client count, market
# ranking or contract term here — a claim this module cannot verify is exactly
# the bug this rewrite removed.
_FACTS = {
    "demo_offer": ("ملفاتك بتذكر عرض تجريبي مجاني قبل أي التزام — اتأكد إن ده عرضك الحقيقي.",
                   "Your files mention a free trial or demo before any commitment — confirm that matches your actual offer.",
                   "fact"),
    "ownership": ("ملفاتك بتذكر ملكية الكود والدومين باسم العميل — راجع شروط التعاقد.",
                  "Your files mention client ownership of code and domain — check this against your contract terms.",
                  "fact"),
    "white_label": ("ملفاتك بتذكر العمل تحت اسم العميل أو بالوكالة — وضّح ده في العرض.",
                    "Your files mention white-label or agency work — make that explicit in your offer.",
                    "fact"),
    "proof_projects": ("عندك مشاريع حية معروضة كدليل — حدد أرقامها وروابطها.",
                       "You have live projects available as proof — pin down their numbers and links.",
                       "fact"),
    "agencies_major": ("الوكالات mentioned كقطاع رئيسي في ملفك — أكّد ده ببيانات.",
                        "Your file names agencies as a major segment — back that with data.",
                        "fact"),
    "quote_pricing": ("التسعير عندك by quote حسب كل بريف — وضّح الحد الأدنى أو الغائبه.",
                      "You price by quote per brief — state a floor, or say explicitly that there is none.",
                      "fact"),
    "no_testimonials": ("مفيش testimonials أو أرقام نتائج موثقة مسجلة لسه — وده أضعف نقطة في الـproof.",
                        "No testimonials or verified result metrics on record yet — the weakest proof point.",
                        "gap"),
}

_ASSESS_QUERIES = ("offers demo ownership white-label", "icp agencies markets proof",
                   "founder decisions positioning")
_EXPLAIN_QUERIES = ("agencies white-label founder decision", "agency campaign outreach")
_RECOMMEND_QUERIES = ("active campaign next step approvals", "measurement decisions pending")
_DOC_QUERIES = (None,)  # documentary uses the user's own question


def _pack_signals(pack_text: str) -> set[str]:
    lowered = pack_text.lower()
    return {name for name, keys in _SIGNAL_KEYS.items() if any(k in lowered for k in keys)}


def _fact_lines(signals: set[str], lang: str, kinds=("fact", "gap")) -> list[tuple[str, str]]:
    lines = []
    for name in _FACTS:
        if name in signals and _FACTS[name][2] in kinds:
            lines.append((_FACTS[name][0] if lang == "ar" else _FACTS[name][1], _FACTS[name][2]))
    return lines


def _declared_markets(company: dict | None) -> list[str]:
    """The markets the user actually declared, strongest section first.

    Replaces the removed hardcoded "Jordan, then Egypt, Saudi Arabia, then UAE"
    ladder. Order is the user's own `primary` before `expansion` — never ours.
    """
    if not company:
        return []
    try:
        markets = json.loads(company.get("markets_json") or "{}")
    except Exception:
        return []
    if not isinstance(markets, dict):
        return []
    out: list[str] = []
    for key in ("primary", "expansion"):
        for value in markets.get(key) or []:
            value = str(value).strip()
            if value and value not in out:
                out.append(value)
    return out


def _markets_fact(company: dict | None, lang: str) -> str | None:
    """A markets line built from the user's own record, or None if they have none."""
    markets = _declared_markets(company)
    if not markets:
        return None
    joined = "، ".join(markets) if lang == "ar" else ", ".join(markets)
    if lang == "ar":
        return f"الأسواق المسجلة عندك: {joined}."
    return f"Markets on record: {joined}."


def _tag(line: str, kind: str, lang: str) -> str:
    tag = {"fact": "(حقيقة مسجلة)" if lang == "ar" else "(recorded fact)",
           "gap": "(فجوة مرصودة)" if lang == "ar" else "(observed gap)",
           "inference": "(استنتاج)" if lang == "ar" else "(inference)"}[kind]
    return f"{line} {tag}"


def _snapshot(conn) -> dict:
    project_id = store.active_project_id(conn)
    conflicts = store.list_conflicts(conn)
    # Conflict events do not carry a project directly. Attribute them through
    # the current row and omit unresolvable events so another workspace's
    # conflict cannot leak into this project's context.
    conflicts = [c for c in conflicts
                 if ((c.get("current") or {}).get("project_id")
                     or ((c.get("current") or {}).get("id")
                         if (c.get("current") or {}).get("table") == "companies" else None))
                 == project_id]
    return {
        # DEV-008-PUBLISH-GATE: was a hardcoded `repos.Companies.get(conn, "njm")`,
        # which returned the real business's row on a private install and `None`
        # on every other install. The active project's own company row is the
        # correct subject for "who is this conversation about".
        "company": repos.Companies.get(conn, project_id),
        "campaigns": repos.Campaigns.list(conn, project_id),
        "approvals": repos.Approvals.list(conn, project_id=project_id),
        "tasks": repos.Tasks.list(conn, project_id),
        "experiments": repos.Experiments.list(conn, project_id),
        "measurements": store.all_measurements(conn, project_id),
        "learnings": repos.Learnings.list(conn, project_id),
        "conflicts": conflicts,
        "chroma": (repos.Settings.get(conn, "chroma_status") or {}).get("value", "unknown"),
    }


def _suggest(flow: str, snap: dict, lang: str) -> list[dict]:
    pending = [a for a in snap["approvals"] if a["status"] == "pending"]
    review = "Review approvals" if lang == "en" else "راجع الموافقات"
    resolve = "Resolve conflicts" if lang == "en" else "حل التعارضات"
    tasks = "Review open tasks" if lang == "en" else "راجع المهام المفتوحة"
    actions = []
    if snap["conflicts"]:
        n = len(snap["conflicts"])
        actions.append({"label": f"{resolve} ({n})", "href": "/settings", "kind": "link"})
    for a in pending[:3]:
        actions.append({"label": f"{review}: {a['id']}", "href": "/approvals", "kind": "link"})
    if flow in (FLOW_NEXT, FLOW_RECOMMEND) and not actions:
        actions.append({"label": tasks, "href": "/tasks", "kind": "link"})
    return actions


def _state_reply(flow: str, snap: dict, lang: str) -> str:
    ar = lang == "ar"
    pending = [a["id"] for a in snap["approvals"] if a["status"] == "pending"]
    open_tasks = [t for t in snap["tasks"] if t["status"] == "open"]
    blocked = [t["id"] for t in snap["tasks"] if t["status"] == "blocked"]
    if flow == FLOW_ATTENTION:
        if ar:
            return (f"محتاج انتباهك: {len(pending)} موافقة معلقة، {len(open_tasks)} مهمة مفتوحة، "
                    f"{len(snap['conflicts'])} تعارض.")
        return (f"Needs your attention: {len(pending)} pending approval(s), "
                f"{len(open_tasks)} open task(s), {len(snap['conflicts'])} conflict(s).")
    if flow == FLOW_APPROVALS:
        if ar:
            return "الموافقات المعلقة: " + ("، ".join(pending) if pending else "مفيش حاجة معلقة.")
        return "Waiting approvals: " + (", ".join(pending) if pending else "none.")
    if flow == FLOW_BLOCKED:
        if ar:
            return ("المهام المتعطلة: " + ("، ".join(blocked) if blocked else "مفيش.") +
                    f" التعارضات: {len(snap['conflicts'])}.")
        return ("Blocked tasks: " + (", ".join(blocked) if blocked else "none.") +
                f" Unresolved conflicts: {len(snap['conflicts'])}.")
    if flow == FLOW_COMPANY:
        company = snap["company"] or {}
        name = company.get("name") or ("غير معروف" if ar else "UNKNOWN")
        if ar:
            return f"الشركة: {name}. متسجل عليها {len(snap['campaigns'])} حملة."
        return f"Company: {name}. {len(snap['campaigns'])} campaign(s) tracked."
    if flow == FLOW_EXPERIMENT:
        if ar:
            return (f"التجارب: {len(snap['experiments'])}. القياسات: {len(snap['measurements'])}. "
                    f"الدروس: {len(snap['learnings'])}.")
        return (f"Experiments: {len(snap['experiments'])}. "
                f"Measurements: {len(snap['measurements'])}. "
                f"Learnings: {len(snap['learnings'])}.")
    if flow == FLOW_CHANGED:
        if ar:
            return f"التعارضات المتابعة: {len(snap['conflicts'])}."
        return f"Tracked conflicts: {len(snap['conflicts'])}."
    return ""


def _compose_assessment(snap: dict, signals: set[str], lang: str) -> str:
    company = snap["company"] or {}
    name = company.get("name") or ("هذا المشروع" if lang == "ar" else "this project")
    facts = _fact_lines(signals, lang, kinds=("fact",))
    gaps = _fact_lines(signals, lang, kinds=("gap",))
    # Replaces the hardcoded market ladder that used to live in _FACTS. The
    # markets are read from the user's own record or not mentioned at all.
    if "markets_rank" in signals:
        markets_line = _markets_fact(company, lang)
        if markets_line:
            facts = facts + [(markets_line, "fact")]
    if lang == "ar":
        lines = [f"{name} — ده اللي أعرفه عنها من البيانات المسجلة:"]
        if facts:
            lines.append("نقاط القوة:")
            lines += ["- " + _tag(f, k, lang) for f, k in facts]
        if gaps:
            lines.append("الفجوات اللي شايفها:")
            lines += ["- " + _tag(g, k, lang) for g, k in gaps]
        lines.append("الخلاصة (استنتاج): العرض واضح وقوي، لكن الـproof محتاج testimonials "
                     "وأرقام موثقة عشان يقنع أكتر." if ("no_testimonials" in signals or not facts)
                     else "الخلاصة (استنتاج): الصورة العامة متماسكة حسب البيانات المتاحة.")
        if not facts and not gaps:
            return "المعلومات اللي عندي مش كفاية أحكم على النقطة دي."
        return "\n".join(lines)
    lines = [f"{name} — what the recorded data says:"]
    if facts:
        lines.append("Strengths:")
        lines += ["- " + f for f, _k in facts]
    if gaps:
        lines.append("Gaps I see:")
        lines += ["- " + g for g, _k in gaps]
    if "no_testimonials" in signals or not facts:
        lines.append("(inference) The offer is clear and strong, but proof needs "
                     "testimonials and verified numbers to convince.")
    else:
        lines.append("(inference) The overall picture is coherent on available data.")
    if not facts and not gaps:
        return "I don't have enough to judge that yet."
    return "\n".join(lines)


def _compose_explain(snap: dict, signals: set[str], lang: str) -> str:
    active = [c for c in snap["campaigns"] if c["status"] in ("approved", "executing", "measuring")]
    if lang == "ar":
        lines = ["اخترنا الوكالات لأن:"]
        if "agencies_major" in signals:
            lines.append("- الوكالات قطاع رئيسي مؤكد من المؤسس، والطلب على الـwhite-label حقيقي. (حقيقة مسجلة)")
        if "white_label" in signals or "resell" in signals:
            lines.append("- الوكالة تقدر تبيع الشغل باسمها وتحافظ على هامشها، واحنا نشتغل من ورا الستار. (حقيقة مسجلة)")
        if active:
            n = len(active)
            lines.append(f"- عندنا {n} حملة شغالة على الاتجاه ده دلوقتي. (حقيقة مسجلة)")
        lines.append("(استنتاج) بدل ما ننافس على عميل واحد واحد، الوكالة الواحدة ممكن تجيب شغل متكرر.")
        return "\n".join(lines)
    lines = ["Why agencies:"]
    if "agencies_major" in signals:
        lines.append("- Agencies are a founder-confirmed major segment with real white-label demand. (recorded fact)")
    if "white_label" in signals or "resell" in signals:
        lines.append("- The agency resells under its brand and keeps margin; we deliver invisibly. (recorded fact)")
    if active:
        lines.append(f"- {len(active)} campaign(s) already run in this direction. (recorded fact)")
    lines.append("(inference) One agency can bring repeat work instead of one-off clients.")
    return "\n".join(lines)


def _compose_recommend(snap: dict, signals: set[str], lang: str, actions: list[dict]) -> str:
    if lang == "ar":
        if actions:
            return ("أنصحك تبدأ بـ: " + actions[0]["label"] +
                    ". (استنتاج مبني على حالة الشغل الحالية)")
        return "مفيش حاجة ضاغطة — تابع صفحة Today. (استنتاج)"
    if actions:
        return (f"I recommend starting with: {actions[0]['label']}. "
                "(inference from current state)")
    return "Nothing pressing — check the Today page. (inference)"


def _compose_documentary(signals: set[str], lang: str) -> str:
    facts = _fact_lines(signals, lang, kinds=("fact", "gap"))[:4]
    if not facts:
        return ("المعلومات اللي عندي مش كفاية أحكم على النقطة دي."
                if lang == "ar" else "I don't have enough to judge that yet.")
    if lang == "ar":
        lines = ["اللي أعرفه عن الموضوع ده:"]
        lines += ["- " + f for f, _k in facts]
        return "\n".join(lines)
    lines = ["What I know on this:"]
    lines += ["- " + f for f, _k in facts]
    return "\n".join(lines)


def _compose_sources(provenance: list[dict], lang: str) -> str:
    paths = sorted({p["path"] for p in provenance if p.get("path")})
    if not paths:
        return ("الإجابة اللي فاتت كانت من البيانات المباشرة المسجلة (الحملات والموافقات)، "
                "مش من مستندات — فمفيش ملفات أعرضها."
                if lang == "ar"
                else "The last answer came from live recorded state (campaigns/approvals), "
                     "not documents — so there are no files to show.")
    head = "المصادر:" if lang == "ar" else "Sources:"
    return head + "\n" + "\n".join(f"- {p}" for p in paths)


def _merge_hits(results: list[dict]) -> list[dict]:
    merged: dict[tuple[str, str], dict] = {}
    for res in results:
        for h in res.get("hits", []):
            key = (h.get("path", ""), h.get("chunk_id", ""))
            if key not in merged or h.get("score", 0) > merged[key].get("score", 0):
                merged[key] = h
    return sorted(merged.values(), key=lambda h: -h.get("score", 0))


def _social_accounts_for_project(conn, project_id: str) -> list:
    """W1 contract C2, defensive: [] when SocialAccounts repo absent (pre-INT)."""
    try:
        from app.database import repos as _repos
        sa = getattr(_repos, "SocialAccounts", None)
        if sa is None:
            return []
        rows = sa.for_project(conn, project_id) or []
        out = [{"platform": str(r.get("platform", "")), "handle": str(r.get("handle", "")),
                "url": str(r.get("url", "")), "status": str(r.get("status", "LIKELY")),
                "source": str(r.get("source", "manual"))}
               for r in rows if isinstance(r, dict)]
        order = {"VERIFIED": 0, "LIKELY": 1, "UNVERIFIED": 2}
        out.sort(key=lambda a: order.get(str(a.get("status", "")).upper(), 3))
        return out[:12]
    except Exception:
        return []


def _instagram_fallback_reply(conn, project_id: str, text: str, lang: str) -> dict | None:
    """Deterministic provider-first, screenshots-last ordering mirroring the
    executor path: social_accounts (identity) → provider chain (access) → web
    alternative → screenshots only for named unreachable details."""
    try:
        from app.services.tools import instagram_tools as ig_tools
    except Exception:
        return None
    try:
        if ig_tools.detect_instagram_intent(text) is None:
            return None
    except Exception:
        return None
    ar = lang == "ar"
    accounts = _social_accounts_for_project(conn, project_id) if project_id else []

    def _ig_result(flow: str, reply: str) -> dict:
        return {"flow": flow, "reply_md": reply, "state": {},
                "evidence": [], "provenance": [], "conflicts": [],
                "unknowns": [], "stale": [], "suggested_actions": [],
                "retrieval_used": False, "retrieval_mode": "SKIPPED",
                "social_accounts": accounts}
    ig_handle, ig_owned = "", False
    for a in accounts:
        if a.get("platform") == "instagram" and a.get("handle"):
            ig_handle, ig_owned = str(a["handle"]).lstrip("@"), str(a.get("status", "")).upper() == "VERIFIED"
            break
    if not ig_handle:
        try:
            ig_handle, ig_owned = ig_tools._handle_from_project(conn, project_id)
        except Exception:
            pass
    # Handle/identity question path: answer from social_accounts first.
    lowered = (text or "").lower()
    is_handle_q = any(k in lowered for k in ("handle", "هاندل", "الهاندل", "الحساب", "ايه حساب", "إيه حساب"))
    if is_handle_q and not ig_tools.detect_instagram_intent(text).get("username"):
        if ig_handle:
            reply = (f"هاندل الإنستجرام الخاص بهذا المشروع هو @{ig_handle}."
                     if ar else
                     f"This project's Instagram handle is @{ig_handle}.")
        else:
            reply = ("لسه معنديش حساب إنستجرام لهذا المشروع — ما هو اسم الحساب أو الـ@handle؟"
                     if ar else
                     "I don't have this project's Instagram yet — what is the @handle?")
        return {"flow": "instagram_identity", "reply_md": reply, "social_accounts": accounts,
                "retrieval_used": False, "retrieval_mode": "SKIPPED"}
    # Audit path: providers first, screenshots last.
    intent = ig_tools.detect_instagram_intent(text)
    explicit = (intent or {}).get("username", "") if intent else ""
    if not explicit:
        import re as _re
        m = _re.search(r"@([\w.]{1,40})", text or "")
        explicit = m.group(1) if m else ""

    if not explicit and not ig_handle:
        reply = ("لسه معنديش حساب إنستجرام لهذا المشروع — ما هو اسم الحساب أو الـ@handle؟"
                 if ar else
                 "I don't have this project's Instagram yet — what is the @handle?")
        return {"flow": "instagram_identity", "reply_md": reply, "social_accounts": accounts,
                "retrieval_used": False, "retrieval_mode": "SKIPPED"}
    try:
        obs = ig_tools.t_instagram_audit(
            conn, project_id=project_id, root=None,
            args={"username": explicit} if explicit else {})
    except Exception as e:
        obs = {"ok": False, "error": str(e)[:200]}
    if obs.get("status") == "NEEDS_HANDLE":
        return {"flow": "instagram_identity",
                "reply_md": (obs.get("note") or "I don't have this project's Instagram yet — what's the @handle?"),
                "social_accounts": accounts, "retrieval_used": False, "retrieval_mode": "SKIPPED"}
    audit = obs.get("audit") or {}
    if obs.get("status") in ("VERIFIED", "PARTIAL"):
        acct = audit.get("account") or {}
        uname = (acct.get("username") or explicit or ig_handle or "").lstrip("@")
        posts_n = len(audit.get("posts", []) or [])
        followers = acct.get("followers")
        f_str = f" ({followers:,} متابع)" if (followers is not None and ar) else (f" ({followers:,} followers)" if followers is not None else "")
        p_str = f" و{posts_n} منشورات حديثة" if (posts_n and ar) else (f" and {posts_n} recent posts" if posts_n else "")
        reply = (f"تم جلب بيانات @{uname} بنجاح من مزود إنستجرام{f_str}{p_str}."
                 if ar else
                 f"Retrieved Instagram public research for @{uname}{f_str}{p_str}.")
        unknowns = audit.get("unknowns", []) or []
        if unknowns:
            named = "; ".join(str(u)[:120] for u in unknowns[:2])
            reply += (f"\n\nتفاصيل إضافية غير متوفرة: {named}" if ar else f"\n\nAdditional details unavailable: {named}")
        return {"flow": "instagram_audit", "reply_md": reply, "social_accounts": accounts,
                "retrieval_used": False, "retrieval_mode": "SKIPPED"}

    has_cfg = audit.get("has_configured_provider", False)
    prim = audit.get("primary_failure") or {}
    prov_name = (prim.get("provider") or "Apify").capitalize()
    err_code = prim.get("error_code") or "integration error"

    from app.services.social.instagram.apify_client import sanitize_secrets
    diag_lines = []
    if prim.get("provider"):
        diag_lines.append(f"Provider: {prim.get('provider')}")
    if prim.get("error_code"):
        diag_lines.append(f"Error Category: {prim.get('error_code')}")
    for u in (audit.get("unknowns", []) or [])[:3]:
        diag_lines.append(f"Detail: {sanitize_secrets(str(u))}")

    details_block = ""
    if diag_lines:
        inner = "\n".join(f"- {line}" for line in diag_lines)
        details_block = (f"\n\n<details><summary>Show details</summary>\n\n{inner}\n\n</details>"
                         if not ar else
                         f"\n\n<details><summary>عرض التفاصيل</summary>\n\n{inner}\n\n</details>")

    if ar:
        if has_cfg:
            reply = (f"تعذر تشغيل بحث إنستجرام.\n\n"
                     f"اتصال {prov_name} مضبوط، لكن مزود السحب أرجع خطأ في التكامل ({err_code}).\n\n"
                     f"[إعادة المحاولة] [فحص اتصال إنستجرام] [استخدام مزود آخر]\n\n"
                     f"يمكنني أيضاً تحليل المعلومات المحفوظة مسبقاً في هذا المشروع إذا كنت تفضل ذلك."
                     f"{details_block}")
        else:
            reply = ("تعذر تشغيل بحث إنستجرام لعدم توصيل أي مزود.\n\n"
                     "[توصيل المزود في الإعدادات]\n\n"
                     "يمكنني أيضاً تحليل المعلومات المحفوظة مسبقاً في هذا المشروع إذا كنت تفضل ذلك."
                     f"{details_block}")
    else:
        if has_cfg:
            reply = (f"Instagram research couldn't run.\n\n"
                     f"{prov_name} connection is configured, but the scraper provider returned an {err_code.lower() if isinstance(err_code, str) else 'integration error'}.\n\n"
                     f"[Retry] [Test Instagram Connection] [Use another provider]\n\n"
                     f"I can still analyze the information already saved in this project if you prefer."
                     f"{details_block}")
        else:
            reply = ("Instagram research couldn't run because no Instagram provider is connected.\n\n"
                     "[Connect in Settings]\n\n"
                     "I can still analyze the information already saved in this project if you prefer."
                     f"{details_block}")

    return {"flow": "instagram_access", "reply_md": reply, "social_accounts": accounts,
            "retrieval_used": False, "retrieval_mode": "SKIPPED"}


def handle_turn(conn, root, text: str, retriever=None, store_override=None,
                prior_provenance: list[dict] | None = None) -> dict:
    """Pure turn handler. retriever(query) -> {hits, mode}; called only when needed."""
    lang = detect_language(text)
    # Instagram identity/access path first (provider-first, screenshots-last);
    # falls through to generic flows when no Instagram intent.
    try:
        _pid = store.active_project_id(conn)
    except Exception:
        _pid = ""
    try:
        ig_early = _instagram_fallback_reply(conn, _pid, text, lang)
    except Exception:
        ig_early = None
    if ig_early is not None:
        return ig_early
    flow = classify(text)
    snap = _snapshot(conn)

    pack_queries: tuple = ()
    if flow == FLOW_ASSESSMENT:
        pack_queries = _ASSESS_QUERIES
    elif flow == FLOW_EXPLAIN:
        pack_queries = _EXPLAIN_QUERIES
    elif flow == FLOW_RECOMMEND:
        pack_queries = _RECOMMEND_QUERIES
    elif flow == FLOW_DOCUMENTARY or any(c in text.lower() for c in _DOC_CUES):
        pack_queries = (text,)

    hits, retrieval_used, mode = [], False, "SKIPPED"
    if pack_queries:
        if retriever is None:
            from app.services.rag import rag_service
            retriever = lambda q: rag_service.retrieve(conn, q, store_override)
        results = [retriever(q) for q in pack_queries if q]
        modes = {r.get("mode", "?") for r in results}
        mode = modes.pop() if len(modes) == 1 else "MIXED"
        hits, retrieval_used = _merge_hits(results), True

    state_block = {k: snap.get(k) for k in ("company", "campaigns", "approvals", "measurements")}
    ctx = context_router.build_context(conn, root, text, state_block, hits)
    pack_text = " ".join(h.get("text", "") for h in ctx["enrichment"] + ctx["unknown"])
    signals = _pack_signals(pack_text)
    actions = _suggest(flow, snap, lang)

    if flow in (FLOW_ATTENTION, FLOW_APPROVALS, FLOW_BLOCKED, FLOW_COMPANY,
                FLOW_EXPERIMENT, FLOW_CHANGED):
        reply = _state_reply(flow, snap, lang)
        if flow == FLOW_CHANGED and ctx["stale"]:
            extra = (f" وفيه {len(ctx['stale'])} مستند محتاج تحديث فهرسة."
                     if lang == "ar" else f" Plus {len(ctx['stale'])} doc(s) need reindexing.")
            reply += extra
    elif flow == FLOW_NEXT:
        first = actions[0]["label"] if actions else ("راجع صفحة Today" if lang == "ar"
                                                     else "review the Today page")
        done = "متنفذش حاجة — القرار في التطبيق." if lang == "ar" else "Nothing executed — decide in the app."
        reply = (f"الخطوة المقترحة: {first}. {done}" if lang == "ar"
                 else f"Suggested next step: {first}. {done}")
    elif flow == FLOW_SOURCES:
        reply = _compose_sources(prior_provenance or ctx["provenance"], lang)
    elif flow == FLOW_ASSESSMENT:
        reply = _compose_assessment(snap, signals, lang)
    elif flow == FLOW_EXPLAIN:
        reply = _compose_explain(snap, signals, lang)
    elif flow == FLOW_RECOMMEND:
        reply = _compose_recommend(snap, signals, lang, actions)
    else:
        reply = _compose_documentary(signals, lang)

    unknowns = ctx["unknown"]
    certain_enough = ("not stated as fact",)
    certain_enough_ar = ("مش محسوبة",)
    if unknowns and flow != FLOW_SOURCES and not any(
            p in reply for p in certain_enough + certain_enough_ar):
        note = (f" (فيه {len(unknowns)} نقطة مش متأكدة — مش محسوبة كحقيقة.)"
                if lang == "ar" else f" ({len(unknowns)} uncertain item(s) — not stated as fact.)")
        reply += note
    if ctx["stale"] and flow not in (FLOW_CHANGED, FLOW_SOURCES):
        note = (f" (فيه {len(ctx['stale'])} مستند محتاج تحديث.)"
                if lang == "ar" else f" ({len(ctx['stale'])} stale doc(s).)")
        reply += note

    return {
        "flow": flow,
        "reply_md": reply,
        "state": state_block,
        "evidence": ctx["enrichment"],
        "provenance": ctx["provenance"],
        "conflicts": snap["conflicts"],
        "unknowns": unknowns,
        "stale": ctx["stale"],
        "suggested_actions": actions,
        "retrieval_used": retrieval_used,
        "retrieval_mode": mode,
    }



# v0.2: deterministic fallback alias. Agentic path is manager_loop.run_manager_turn.
handle_turn_fallback = handle_turn
