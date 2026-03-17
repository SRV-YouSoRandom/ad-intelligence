"""
AI insight generator — Qwen VL prompting for creative analysis.

TWO MODES:
1. Visual mode — when local media exists (image or video frames downloaded).
   Sends actual images to the VL model alongside performance data.

2. Text-only mode — when no local media is available. Generates insights
   purely from ad copy + performance metrics.

POLITICAL AD HANDLING:
Political ads (BJP, Congress, election campaigns, etc.) have fundamentally
different success metrics than commercial ads:
- Reach breadth matters more than conversion
- Emotional resonance and identity signaling drive engagement
- Message clarity and authority cues matter more than CTA
- "Weak performer" by reach efficiency may still be strategically successful
  if it targeted a specific geography or demographic

The prompts explicitly acknowledge this context to avoid mis-framing
political ads through a commercial performance lens.
"""

import base64
import json
import os

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import metrics
from app.db.models import Ad

logger = get_logger(__name__)

PROMPT_VERSION = "v3"  # bumped for political ad awareness


def _detect_ad_context(ad) -> str:
    """
    Detect whether this ad is political/social issue or commercial.

    Ground truth sources (in priority order):
    1. disclaimer field — Meta-verified political/issue declaration
       This is the authoritative signal. If it exists, Meta has
       required the advertiser to complete identity verification
       confirming this is a political/issue/social ad.
    2. bylines field — "Paid for by" text (same meaning as disclaimer)
    3. beneficiary_payers — EU political transparency field
    4. Keyword fallback — for cases where political ads ran with
       disclaimer but it wasn't captured in the snapshot

    Returns: 'political' | 'commercial'
    """
    raw = ad.raw_meta_json or {}

    # Primary: Meta's own political ad verification fields
    disclaimer = raw.get("disclaimer") or ""
    bylines = raw.get("bylines") or ""
    beneficiary_payers = raw.get("beneficiary_payers") or []

    if disclaimer or bylines or beneficiary_payers:
        return "political"

    # Secondary: keyword heuristic for page names we know are political
    # (catches edge cases where disclaimer wasn't in the fetched snapshot)
    page_name = (ad.page_name or "").lower()
    caption = (ad.caption or "").lower()

    political_keywords = [
        "party", "election", "vote", "manifesto",
        "campaign", "political", "neta", "sarkar", "modi", "rahul",
        "gandhi", "government", "minister", "mp ", "mla ",
        "lok sabha", "rajya sabha", "phir ek baar", "viksit bharat",
        "aam aadmi", "trinamool", "shiv sena", "samajwadi",
    ]

    combined = page_name + " " + caption
    if any(kw in combined for kw in political_keywords):
        return "political"

    return "commercial"


# ── Political context block injected into all political ad prompts ─────────────

POLITICAL_CONTEXT_BLOCK = """
IMPORTANT CONTEXT — THIS IS A POLITICAL ADVERTISEMENT:
Political ads operate under fundamentally different success criteria than commercial ads:
- The primary goal is message amplification and identity reinforcement, not direct conversion
- Reach breadth (how many unique people saw it) matters more than frequency efficiency
- Emotional resonance, authority, and aspirational national identity drive engagement
- Strong performer ≠ commercially optimized; it means the creative effectively carried the political message to a broad audience
- Weak performer may still be strategically valid if it was geographically or demographically targeted
- Poster-style graphics with bold text, party colors, and leader imagery are the dominant format — evaluate them on those terms, not on whether they follow commercial CTA conventions
- Copy in Hindi, regional languages, or transliterated text should be understood in its political communication context (e.g. "Phir Ek Baar Modi Sarkar" = "Once More, Modi Government" — a campaign slogan, not a product offer)

Adjust your analysis to reflect these political communication norms. Do not penalize the absence of commercial CTAs like "Shop Now" or "Learn More". Do evaluate whether the political message is clear, emotionally resonant, and visually authoritative.
"""

# ── System Prompts ─────────────────────────────────────────────────────────────

STATIC_VISUAL_SYSTEM_PROMPT = """You are a senior performance creative strategist with deep expertise in Meta (Facebook/Instagram) paid advertising across both commercial brand campaigns and political party campaigns. You have analyzed thousands of ad creatives and understand what separates ads that scale from ads that stall.

Your job is to produce a genuinely useful creative debrief — not a mechanical audit, but a strategic read of why this ad performed the way it did and what should change.

You will be given:
- The ad image
- The ad's caption/copy text
- Performance label: STRONG, AVERAGE, or WEAK (relative to this brand's other ads)
- Reach efficiency score (0.0–1.0): ratio of unique reach to total impressions
- Percentile rank within this brand's ad dataset
- Ad context: COMMERCIAL or POLITICAL (adjust your analysis framework accordingly)

Analysis lens — evaluate across these dimensions, only flagging where you have something meaningful:

COMPOSITION: Does visual hierarchy guide the eye immediately to the core message?
PRODUCT_VISIBILITY (commercial) / MESSAGE_CLARITY (political): How quickly and prominently does the product/message register?
HUMAN_PRESENCE: Are there people or faces? Do they feel authentic vs stock? For political ads — does leader imagery convey authority and accessibility?
CTA: For commercial ads — is the call-to-action clear and proportionate? For political ads — is there a clear action or sentiment the viewer should take away?
COPY: Does the on-creative text earn its space? Benefit-led vs feature-led (commercial) or message-led vs slogan-led (political)?
COLOR_CONTRAST: Does the palette create thumb-stopping contrast in a feed?
EMOTIONAL_TONE: What feeling does the creative communicate in the first second? Does it match what would motivate the audience?

Rules:
- Be specific and concrete. Reference observable details.
- Connect every observation directly to performance impact.
- Do not penalize political ads for lacking commercial CTAs.
- The recommendation must be actionable and specific to THIS ad.
- Output ONLY a valid JSON object. No markdown fences, no preamble.

Output schema:
{
  "summary": "4-6 sentence strategic narrative. Open with the performance outcome, explain 2-3 creative reasons, close with the single most important leverage point.",
  "analysis_mode": "visual",
  "ad_context": "commercial|political",
  "recommendation": "One concrete, specific action to test in the next iteration. Grounded in what you observed.",
  "factors": [
    {
      "trait": "snake_case_trait_name",
      "category": "composition|cta|copy|color_contrast|product_visibility|message_clarity|human_presence|emotional_tone",
      "impact": "positive|negative|neutral",
      "confidence": "high|medium|low",
      "evidence": "2-3 sentences: what you specifically observed, why it matters mechanically, how it connects to the performance label."
    }
  ]
}

Produce 4 to 6 factors."""


TEXT_ONLY_SYSTEM_PROMPT = """You are a senior performance creative strategist with deep expertise in Meta (Facebook/Instagram) paid advertising across both commercial and political campaigns. You specialize in copy analysis.

You are analyzing an ad based on its copy and performance data only. No image is available. This is a valid analysis path — copy is frequently the dominant performance driver.

You will be given:
- Ad caption/copy text
- Link title and description if available
- Performance label: STRONG, AVERAGE, or WEAK
- Reach efficiency score and percentile rank if available
- Ad context: COMMERCIAL or POLITICAL

Analysis lens:

HOOK_STRENGTH: Does the opening line stop the scroll? For political ads — does it immediately establish identity, urgency, or aspiration?
OFFER_CLARITY (commercial) / MESSAGE_CLARITY (political): Is there a specific, concrete offer or message? For political ads — is the stance/position unmistakable?
AUDIENCE_SIGNAL: Does the copy signal clearly who it's for?
CTA_SPECIFICITY: Is the call-to-action (or call-to-belief for political) specific and proportionate?
TONE_AUTHENTICITY: Does this read like a human wrote it for a human, or a brand/party machine wrote it for a demographic?
LENGTH_FIT: Is copy length appropriate for the offer/message complexity?
URGENCY_AND_PROOF: Is there a reason to act/believe now, and any credibility signal?

Rules:
- Quote specific phrases from the actual copy as evidence.
- Connect each observation directly to the performance outcome.
- Do not penalize political ads for missing commercial CTAs.
- The recommendation must be specific to THIS copy.
- Output ONLY a valid JSON object. No markdown fences.

Output schema:
{
  "summary": "4-6 sentence strategic narrative acknowledging this is copy-based analysis. Explain 2-3 copy factors driving performance. Close with highest-leverage copy change.",
  "analysis_mode": "text_only",
  "ad_context": "commercial|political",
  "recommendation": "One specific, testable copy change. Reference actual copy text.",
  "factors": [
    {
      "trait": "snake_case_trait_name",
      "category": "hook_strength|offer_clarity|message_clarity|audience_signal|cta_specificity|tone_authenticity|length_fit|urgency_and_proof",
      "impact": "positive|negative|neutral",
      "confidence": "high|medium|low",
      "evidence": "2-3 sentences quoting or closely referencing actual copy, explaining the performance implication."
    }
  ]
}

Produce 4 to 6 factors."""


VIDEO_VISUAL_SYSTEM_PROMPT = """You are a senior performance creative strategist specialising in Meta video advertising across commercial and political campaigns. You understand the mechanics of video performance: hook window, pacing, audio-off viewing, and watch-time impact on delivery cost.

You will be given:
- Scene-change frames with timestamps and scene-change scores
- The ad's caption/copy text
- Performance label: STRONG, AVERAGE, or WEAK
- Reach efficiency score and percentile rank
- Ad context: COMMERCIAL or POLITICAL

Critical context:
- ~85% of Meta video views happen with audio off
- First 1-2 seconds determine watch rate — weak hooks increase CPM
- Political video ads often open with leader visuals or national imagery — evaluate these on political communication effectiveness, not commercial hook conventions
- Product/message visibility timing: before 3s consistently outperforms late-reveal

Analysis dimensions:
HOOK_STRENGTH: What happens in first 1-2 seconds? For political — does it establish authority, aspiration, or urgency immediately?
PACING: Do frame count, timestamps, and scene scores tell a coherent story?
PRODUCT_VISIBILITY (commercial) / MESSAGE_VISIBILITY (political): When does the key message first register clearly?
HUMAN_PRESENCE: Where and how do people appear? Leader presence in political ads is a primary authority signal.
TEXT_OVERLAY_QUALITY: Are overlays present, readable at small size, timed well?
CTA_PLACEMENT: When and how does CTA appear? For political — when does the party/candidate identification appear?
SCENE_TRANSITION_QUALITY: Do cuts feel intentional and energy-building?

Rules:
- Reason across ALL frames as a temporal sequence.
- Reference specific timestamps and scene-change scores.
- Output ONLY a valid JSON object. No markdown fences.

Output schema:
{
  "summary": "4-6 sentence strategic narrative. Open with performance outcome. Explain hook quality, pacing, message visibility. Close with highest-leverage edit change.",
  "analysis_mode": "visual",
  "ad_context": "commercial|political",
  "edit_pace": "fast|medium|slow",
  "hook_assessment": "One sentence on what the hook does or fails to do in first 2 seconds.",
  "product_first_seen_sec": null,
  "cta_timestamp_sec": null,
  "recommendation": "One specific, actionable edit recommendation grounded in timestamp/frame evidence.",
  "factors": [
    {
      "trait": "snake_case_trait_name",
      "category": "hook_strength|pacing|product_visibility|message_visibility|human_presence|text_overlay_quality|cta_placement|scene_transition_quality",
      "impact": "positive|negative|neutral",
      "confidence": "high|medium|low",
      "evidence": "2-3 sentences referencing specific timestamps, scene scores, or observed content."
    }
  ]
}

Produce 4 to 7 factors. Always include hook_strength and pacing."""


# ── Helpers ────────────────────────────────────────────────────────────────────

class InsightResult:
    def __init__(self, summary, factors, model_used="", prompt_version=PROMPT_VERSION,
                 analysis_mode="visual", ad_context="commercial"):
        self.summary = summary
        self.factors = factors
        self.model_used = model_used
        self.prompt_version = prompt_version
        self.analysis_mode = analysis_mode
        self.ad_context = ad_context


def _is_valid_image(image_path: str) -> bool:
    try:
        from PIL import Image
        with Image.open(image_path) as img:
            img.verify()
        return True
    except Exception:
        return False


def _encode_image(image_path: str) -> str:
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _performance_context(ad: Ad) -> str:
    if ad.performance_label and ad.impressions_mid and ad.reach_mid:
        imp = int(ad.impressions_mid)
        reach = int(ad.reach_mid)
        efficiency = reach / imp if imp > 0 else 0.0
        efficiency_label = (
            "high — the ad kept reaching new people efficiently"
            if efficiency >= 0.7
            else "moderate — some audience overlap is occurring"
            if efficiency >= 0.4
            else "low — the ad was shown heavily to the same people"
        )
        # Include impression range uncertainty
        imp_lower = int(ad.impressions_lower or imp)
        imp_upper = int(ad.impressions_upper or imp)
        range_note = f" (range: {imp_lower:,}–{imp_upper:,})" if imp_upper > imp_lower else ""

        return (
            f"Performance label: {ad.performance_label} "
            f"(ranks at the {ad.performance_percentile:.0f}th percentile within this brand's ad dataset)\n"
            f"Estimated impressions: ~{imp:,}{range_note}\n"
            f"Estimated unique reach: ~{reach:,}\n"
            f"Reach efficiency: {efficiency:.2f} ({efficiency_label})"
        )
    elif ad.performance_label:
        return (
            f"Performance label: {ad.performance_label} "
            f"(impression/reach data not available — ad ran outside EU regions "
            f"where the Meta Ads Library does not report delivery metrics)"
        )
    return "Performance data: not available for this ad"


def _copy_text(ad: Ad) -> str:
    parts = filter(None, [ad.caption, ad.link_title, ad.link_description])
    joined = " | ".join(parts)
    return joined if joined else "[No copy available for this ad]"


def _build_system_prompt(base_prompt: str, ad_context: str) -> str:
    """Inject political context block if this is a political ad."""
    if ad_context == "political":
        return base_prompt + "\n\n" + POLITICAL_CONTEXT_BLOCK
    return base_prompt


def _build_static_visual_messages(ad: Ad, ad_context: str) -> list[dict]:
    return [{
        "role": "user",
        "content": [
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{_encode_image(ad.media_local_path)}"},
            },
            {
                "type": "text",
                "text": (
                    f"--- AD CONTEXT ---\n"
                    f"Ad type: {ad_context.upper()}\n\n"
                    f"--- PERFORMANCE DATA ---\n"
                    f"{_performance_context(ad)}\n\n"
                    f"--- AD COPY ---\n"
                    f"{_copy_text(ad)}\n\n"
                    f"Analyze this ad creative and produce your strategic debrief. "
                    f"Respond only with the JSON object."
                ),
            },
        ],
    }]


def _build_text_only_messages(ad: Ad, ad_context: str) -> list[dict]:
    return [{
        "role": "user",
        "content": (
            f"--- AD CONTEXT ---\n"
            f"Ad type: {ad_context.upper()}\n\n"
            f"--- PERFORMANCE DATA ---\n"
            f"{_performance_context(ad)}\n\n"
            f"--- AD COPY ---\n"
            f"{_copy_text(ad)}\n\n"
            f"Produce your copy analysis and strategic debrief. "
            f"Respond only with the JSON object."
        ),
    }]


def _build_video_visual_messages(ad: Ad, ad_context: str) -> list[dict]:
    frame_metas = ad.frame_metadata or []
    content: list[dict] = [{
        "type": "text",
        "text": (
            f"--- AD CONTEXT ---\n"
            f"Ad type: {ad_context.upper()}\n\n"
            f"--- PERFORMANCE DATA ---\n"
            f"{_performance_context(ad)}\n\n"
            f"--- AD COPY ---\n"
            f"{ad.caption or '[No copy available]'}\n\n"
            f"--- VIDEO FRAMES ---\n"
            f"Analyzing {len(frame_metas)} scene-change frames. "
            f"Frames are ordered chronologically. Scene-change score indicates visual "
            f"distinctiveness from previous scene (higher = bigger visual shift):\n"
        ),
    }]
    for i, meta in enumerate(frame_metas):
        hook_tag = " ← HOOK WINDOW (first 2s)" if meta.get("is_hook") else ""
        label = (
            f"Frame {i + 1} | {meta['timestamp_sec']:.1f}s{hook_tag} | "
            f"scene-change score: {meta['scene_score']:.2f}"
        )
        content.append({"type": "text", "text": f"\n{label}:"})
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{_encode_image(meta['path'])}"},
        })
    content.append({
        "type": "text",
        "text": (
            "\nAnalyze these frames as a temporal narrative. "
            "Produce your strategic debrief. Respond only with the JSON object."
        ),
    })
    return [{"role": "user", "content": content}]


def _parse_insight_response(raw: str, expected_mode: str, ad_context: str) -> InsightResult:
    content = raw.strip()
    if "</think>" in content:
        content = content.split("</think>")[-1].strip()
    if content.startswith("```"):
        content = content.split("```")[1]
        if content.startswith("json"):
            content = content[4:]
    content = content.strip()
    data = json.loads(content)

    factors = data.get("factors", [])
    recommendation = data.get("recommendation")
    if recommendation:
        factors.append({
            "trait": "recommended_next_test",
            "category": "recommendation",
            "impact": "positive",
            "confidence": "high",
            "evidence": recommendation,
        })

    return InsightResult(
        summary=data["summary"],
        factors=factors,
        analysis_mode=data.get("analysis_mode", expected_mode),
        ad_context=data.get("ad_context", ad_context),
    )


# ── Main Interface ─────────────────────────────────────────────────────────────

async def generate_insight(ad: Ad) -> InsightResult:
    """
    Generate creative insights for an ad.

    Auto-detects:
    - Whether this is a political or commercial ad
    - Whether visual (static image), visual (video frames), or text-only mode

    Political ads receive additional context in the system prompt to
    prevent mis-framing through a purely commercial performance lens.
    """
    ad_context = _detect_ad_context(ad)

    has_image = (
        ad.media_local_path
        and os.path.exists(ad.media_local_path)
        and _is_valid_image(ad.media_local_path)
    )
    has_frames = ad.frame_metadata and len(ad.frame_metadata) > 0

    if ad.ad_type == "VIDEO" and has_frames:
        system_prompt = _build_system_prompt(VIDEO_VISUAL_SYSTEM_PROMPT, ad_context)
        messages = _build_video_visual_messages(ad, ad_context)
        mode = "visual"
    elif ad.ad_type == "STATIC" and has_image:
        system_prompt = _build_system_prompt(STATIC_VISUAL_SYSTEM_PROMPT, ad_context)
        messages = _build_static_visual_messages(ad, ad_context)
        mode = "visual"
    else:
        logger.info(
            "insight_text_only_mode",
            ad_id=str(ad.id),
            ad_type=ad.ad_type,
            ad_context=ad_context,
            has_image=bool(has_image),
        )
        system_prompt = _build_system_prompt(TEXT_ONLY_SYSTEM_PROMPT, ad_context)
        messages = _build_text_only_messages(ad, ad_context)
        mode = "text_only"

    logger.info(
        "insight_generation_starting",
        ad_id=str(ad.id),
        mode=mode,
        media_path=ad.media_local_path or "text-only",
        frames_count=len(ad.frame_metadata) if ad.frame_metadata else 0
    )

    payload = {
        "model": settings.INSIGHT_MODEL,
        "max_tokens": 2000,
        "messages": [
            {"role": "system", "content": system_prompt},
            *messages,
        ],
        "response_format": {"type": "json_object"},
    }

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                f"{settings.OPENROUTER_BASE_URL}/chat/completions",
                json=payload,
                headers={
                    "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                },
            )
            response.raise_for_status()
            data = response.json()

            if "choices" not in data:
                logger.error("openrouter_no_choices", ad_id=str(ad.id), response=data)
                raise ValueError(f"OpenRouter returned no choices. Response: {data}")

            raw_content = data["choices"][0]["message"]["content"]
            result = _parse_insight_response(raw_content, mode, ad_context)
            result.model_used = settings.INSIGHT_MODEL
            result.prompt_version = PROMPT_VERSION
            metrics.increment(f"insights_generated_{mode}_{ad_context}")
            logger.info("insight_generated", ad_id=str(ad.id), mode=mode, ad_context=ad_context)
            return result

    except json.JSONDecodeError as exc:
        logger.error("insight_parse_error", ad_id=str(ad.id), error=str(exc))
        raise
    except Exception as exc:
        logger.error("insight_generation_error", ad_id=str(ad.id), error=str(exc))
        raise