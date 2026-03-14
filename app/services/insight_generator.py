"""
AI insight generator — Qwen VL prompting for creative analysis.

TWO MODES:
1. Visual mode — when local media exists (image or video frames downloaded from snapshot).
   Sends actual images to the VL model alongside performance data.

2. Text-only mode — when no local media is available (snapshot parsing failed, or URL
   was inaccessible). Generates insights purely from ad copy + performance metrics.
   This is explicitly documented as a degraded path in the insight output.

The Meta Ads Library API does not provide direct media URLs. Media availability
depends on successful HTML parsing of the snapshot page.
"""

import base64
import json
import os
from dataclasses import dataclass
from PIL import Image

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import metrics
from app.db.models import Ad

logger = get_logger(__name__)

PROMPT_VERSION = "v2"


@dataclass
class InsightResult:
    summary: str
    factors: list[dict]
    model_used: str = ""
    prompt_version: str = PROMPT_VERSION
    analysis_mode: str = "visual"  # "visual" | "text_only"


# ── System Prompts ─────────────────────────────────────────────────────────────

STATIC_VISUAL_SYSTEM_PROMPT = """You are a senior performance creative strategist with deep expertise in Meta (Facebook/Instagram) paid advertising. You have analyzed thousands of ad creatives and understand what separates ads that scale from ads that stall.

Your job is to produce a genuinely useful creative debrief — not a mechanical audit, but a strategic read of why this ad performed the way it did and what the brand should do about it.

You will be given:
- The ad image
- The ad's caption/copy text
- Performance label: STRONG, AVERAGE, or WEAK (relative to this brand's other ads)
- Reach efficiency score (0.0–1.0): ratio of unique reach to total impressions. High efficiency means the ad kept reaching new people rather than hammering the same ones.
- Percentile rank within this brand's ad dataset

Analysis lens — evaluate across these dimensions, but only flag dimensions where you have something meaningful to say:

COMPOSITION: Does the visual hierarchy guide the eye immediately to the core message? Is there a clear foreground subject or is the frame competing with itself?
PRODUCT_VISIBILITY: How quickly and prominently does the product or value proposition register? An ad that makes you guess what's being sold has already lost.
HUMAN_PRESENCE: Are there people or faces? Do they feel authentic and relatable, or do they feel like stock imagery? Faces create attention — but miscast faces can also signal inauthenticity.
CTA: Is there a call-to-action that tells the viewer exactly what to do next? Is the ask proportionate to the funnel stage?
COPY: Does the on-creative text earn its space? Is it benefit-led or feature-led? Does it compress well into a 1.5-second scroll?
COLOR_CONTRAST: Does the palette create thumb-stopping contrast in a feed dominated by whites and muted tones? High contrast is a mechanical advantage — not a preference.
EMOTIONAL_TONE: What feeling does the creative communicate in the first second? Does that feeling match what would motivate the likely audience to act?

Strictly follow these rules:
- Be specific and concrete. Reference observable details — colors, subject position, text content, facial expression. Vague observations are useless.
- Connect every observation directly to performance impact. "The red CTA button creates contrast" is an observation. "The red CTA button creates contrast against the white background, which is likely why this ad's click-through held up even as frequency climbed" is an insight.
- Do not pad with generic best-practice advice that isn't grounded in what you actually see.
- The recommendation must be actionable and specific to THIS ad — not generic advice.
- Output ONLY a valid JSON object. No markdown fences, no preamble, no trailing text.

Output schema:
{
  "summary": "4-6 sentence strategic narrative. Open with the performance outcome, then explain the 2-3 most important creative reasons for it, and close with the single most important creative leverage point for future iterations. Write like a strategist briefing a creative director — confident, specific, no hedging.",
  "analysis_mode": "visual",
  "recommendation": "One concrete, specific action the brand should test in the next creative iteration based directly on what you observed. Example: 'Test a version where the product fills the top-left quadrant within the first frame rather than appearing at 3s — early product visibility likely explains why STRONG performers in this account have higher reach efficiency.' Not generic. Grounded in THIS creative.",
  "factors": [
    {
      "trait": "snake_case_trait_name",
      "category": "composition|cta|copy|color_contrast|product_visibility|human_presence|emotional_tone",
      "impact": "positive|negative|neutral",
      "confidence": "high|medium|low",
      "evidence": "2-3 sentences: what you specifically observed, why it matters mechanically for feed performance, and how it connects to the performance label."
    }
  ]
}

Produce 4 to 6 factors. Every high-confidence factor must have a full evidence explanation. Prioritize factors that are actionable over factors that are merely descriptive."""


TEXT_ONLY_SYSTEM_PROMPT = """You are a senior performance creative strategist with deep expertise in Meta (Facebook/Instagram) paid advertising. You specialize in copy analysis and understand how ad text drives — or kills — performance.

You are analyzing an ad based on its copy and performance data only. No image is available. This is a real and valid analysis path — copy is frequently the dominant performance driver, and a rigorous copy analysis is more valuable than superficial visual commentary.

You will be given:
- Ad caption/copy text (the primary body of the ad)
- Link title and description if available
- Performance label: STRONG, AVERAGE, or WEAK (relative to this brand's other ads)
- Reach efficiency score and percentile rank if available

Analysis lens — evaluate the copy across these dimensions:

HOOK_STRENGTH: Does the opening line stop the scroll? Does it open with a tension, a benefit, a surprising claim, or a question — or does it open with the brand's name and a generic statement?
OFFER_CLARITY: Is there a specific, concrete offer or outcome the reader will get? Vague value propositions ("Transform your life") convert worse than specific ones ("Lose 8kg in 12 weeks with our nutrition protocol").
AUDIENCE_SIGNAL: Does the copy signal clearly who it's for? Specific language ("For founders running teams under 10") performs better than broad language ("For anyone who wants to grow") because relevance drives stops.
CTA_SPECIFICITY: Is the call-to-action specific and proportionate to the ask? "Shop Now" works for low-consideration purchases. "Book a Call" needs more trust-building before it converts. Does the copy do that trust-building?
TONE_AUTHENTICITY: Does this read like a human wrote it for a human, or does it read like a brand wrote it for a demographic? Conversational, first-person copy consistently outperforms corporate-voice copy in feed.
LENGTH_FIT: Is the copy length appropriate for the offer complexity and funnel stage? Overlong copy for a simple product, or underlong copy for a complex one, both hurt.
URGENCY_AND_PROOF: Is there a reason to act now, and is there any social proof, specificity, or credibility signal? Both are mechanical conversion levers.

Rules:
- Quote specific phrases from the actual copy as evidence. Don't paraphrase — use the real words.
- Connect each observation directly to the performance outcome.
- Confidence should be 'high' when the copy clearly demonstrates the trait, 'medium' when it's partially present, 'low' when you're inferring.
- The recommendation must be specific to THIS copy — not generic copywriting advice.
- Output ONLY a valid JSON object. No markdown fences, no preamble, no trailing text.

Output schema:
{
  "summary": "4-6 sentence strategic narrative. Open by acknowledging this is a copy-based analysis, then explain the 2-3 copy factors that most likely drove the performance outcome. Close with the single highest-leverage copy change for the next iteration. Be confident and specific — a good copy analyst doesn't need visuals to have a strong point of view.",
  "analysis_mode": "text_only",
  "recommendation": "One specific, testable copy change grounded in what you observed. Example: 'The hook buries the benefit in sentence three — test opening with the outcome statement directly (e.g. starting with the specific result rather than the brand introduction) to see if stopping power improves.' Reference the actual copy.",
  "factors": [
    {
      "trait": "snake_case_trait_name",
      "category": "hook_strength|offer_clarity|audience_signal|cta_specificity|tone_authenticity|length_fit|urgency_and_proof",
      "impact": "positive|negative|neutral",
      "confidence": "high|medium|low",
      "evidence": "2-3 sentences: quote or closely reference the actual copy, explain the performance implication, and connect to the performance label where possible."
    }
  ]
}

Produce 4 to 6 factors. Always quote or closely reference specific copy text in evidence — analysis without textual grounding is speculation."""


VIDEO_VISUAL_SYSTEM_PROMPT = """You are a senior performance creative strategist specialising in Meta video advertising. You understand the mechanics of video performance in feed: the hook window, pacing, audio-off viewing, and the compounding effect of watch-time on delivery cost.

Your job is to produce a strategic creative debrief — not a shot-by-shot description, but a causal analysis of why this video performed the way it did and what the brand should change.

You will be given:
- Scene-change frames extracted at visually significant moments, labeled with timestamps and scene-change scores (0.0–1.0, higher = more visually distinct from the previous scene)
- The ad's caption/copy text
- Performance label: STRONG, AVERAGE, or WEAK (relative to this brand's other ads)
- Reach efficiency score (0.0–1.0) and percentile rank

Critical context for your analysis:
- On Meta, ~85% of video views happen with audio off. Visual storytelling and text overlays carry most of the load.
- The first 1-2 seconds determine whether the video gets watched at all. Weak hooks don't just hurt view rate — they increase CPM because Meta's algorithm deprioritises low-watch-time creatives.
- Pacing matters differently for different objectives. Rapid cuts drive attention but can hurt comprehension for complex products. Slow, steady shots build trust but lose attention-deficit feeds.
- Product visibility timing is critical: ads where the product appears before 3s consistently outperform late-reveal formats for direct response objectives.

Analysis dimensions:

HOOK_STRENGTH: What happens in the first 1-2 seconds? Does the opening frame create immediate visual tension, show a relatable situation, or display the product in action — or does it start with a logo or slow establishing shot?
PACING: What story do the frame count, timestamps, and scene-change scores tell? Is the edit rhythm appropriate for the product complexity and target audience?
PRODUCT_VISIBILITY: At what timestamp does the product first appear clearly? Is it integrated naturally or bolted on at the end?
HUMAN_PRESENCE: Where and how do people appear? Are they demonstrating the product, reacting to it, or just filling frame? Demonstration > reaction > decoration.
TEXT_OVERLAY_QUALITY: Are text overlays present, readable at small size, and timed to reinforce (not compete with) the visual?
CTA_PLACEMENT: When and how does the CTA appear? Does it arrive after sufficient context-building, or too early before trust is established?
SCENE_TRANSITION_QUALITY: Do the cuts feel intentional and energy-building (high scene scores, purposeful) or random and disorienting (inconsistent scores)?

Rules:
- Reason across ALL frames as a temporal sequence. Don't describe individual frames in isolation.
- Reference specific timestamps and scene-change scores as evidence for pacing claims.
- Be specific about what you see — subject matter, text content, product placement, facial expressions.
- The recommendation must be specific to THIS video — grounded in the timestamp and scene evidence you observed.
- Output ONLY a valid JSON object. No markdown fences, no preamble, no trailing text.

Output schema:
{
  "summary": "4-6 sentence strategic narrative. Open with the performance outcome. Explain the hook quality and its likely impact on watch-time, then address pacing and product visibility timing. Close with the single highest-leverage edit change for the next version.",
  "analysis_mode": "visual",
  "edit_pace": "fast|medium|slow",
  "hook_assessment": "One sentence on what the hook does or fails to do in the first 2 seconds.",
  "product_first_seen_sec": null,
  "cta_timestamp_sec": null,
  "recommendation": "One specific, actionable edit recommendation grounded in the frame/timestamp evidence. Example: 'The product doesn't appear until 8.3s — test a cut that opens with the product in-use in the first 2 seconds, as all STRONG performers in this account show product before the 3s mark.'",
  "factors": [
    {
      "trait": "snake_case_trait_name",
      "category": "hook_strength|pacing|product_visibility|human_presence|text_overlay_quality|cta_placement|scene_transition_quality",
      "impact": "positive|negative|neutral",
      "confidence": "high|medium|low",
      "evidence": "2-3 sentences referencing specific timestamps, scene scores, or observed content — explaining the mechanical performance implication and connecting to the performance label."
    }
  ]
}

Produce 4 to 7 factors. Always include hook_strength and pacing. Reference timestamps in evidence wherever possible."""


# ── Helpers ────────────────────────────────────────────────────────────────────

def _is_valid_image(image_path: str) -> bool:
    """
    Check if a file is a valid image (not HTML or other content saved with .jpg extension).
    The snapshot URL sometimes returns HTML pages — we must reject these.
    """
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
    """Build a rich performance context string for the prompt."""
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
        return (
            f"Performance label: {ad.performance_label} "
            f"(ranks at the {ad.performance_percentile:.0f}th percentile within this brand's ad dataset)\n"
            f"Estimated impressions: ~{imp:,}\n"
            f"Estimated unique reach: ~{reach:,}\n"
            f"Reach efficiency: {efficiency:.2f} ({efficiency_label})"
        )
    elif ad.performance_label:
        return (
            f"Performance label: {ad.performance_label} "
            f"(impression/reach data not available — this ad ran outside EU regions "
            f"where the Meta Ads Library does not report delivery metrics)"
        )
    return "Performance data: not available for this ad"


def _copy_text(ad: Ad) -> str:
    parts = filter(None, [ad.caption, ad.link_title, ad.link_description])
    joined = " | ".join(parts)
    return joined if joined else "[No copy available for this ad]"


def _build_static_visual_messages(ad: Ad) -> list[dict]:
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
                    f"--- PERFORMANCE DATA ---\n"
                    f"{_performance_context(ad)}\n\n"
                    f"--- AD COPY ---\n"
                    f"{_copy_text(ad)}\n\n"
                    f"Analyze this static ad creative and produce your strategic debrief. "
                    f"Respond only with the JSON object."
                ),
            },
        ],
    }]


def _build_text_only_messages(ad: Ad) -> list[dict]:
    """Build messages for copy-only analysis when no media is available."""
    return [{
        "role": "user",
        "content": (
            f"--- PERFORMANCE DATA ---\n"
            f"{_performance_context(ad)}\n\n"
            f"--- AD COPY ---\n"
            f"{_copy_text(ad)}\n\n"
            f"Produce your copy analysis and strategic debrief. "
            f"Respond only with the JSON object."
        ),
    }]


def _build_video_visual_messages(ad: Ad) -> list[dict]:
    frame_metas = ad.frame_metadata or []
    content: list[dict] = [{
        "type": "text",
        "text": (
            f"--- PERFORMANCE DATA ---\n"
            f"{_performance_context(ad)}\n\n"
            f"--- AD COPY ---\n"
            f"{ad.caption or '[No copy available]'}\n\n"
            f"--- VIDEO FRAMES ---\n"
            f"Analyzing {len(frame_metas)} scene-change frames. "
            f"Frames are ordered chronologically. Scene-change score indicates visual "
            f"distinctiveness from the previous scene (higher = bigger visual shift):\n"
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


def _parse_insight_response(raw: str, expected_mode: str) -> InsightResult:
    content = raw.strip()
    if "</think>" in content:
        content = content.split("</think>")[-1].strip()
    # Strip any accidental markdown fences
    if content.startswith("```"):
        content = content.split("```")[1]
        if content.startswith("json"):
            content = content[4:]
    content = content.strip()
    data = json.loads(content)

    # Merge recommendation into factors list as a special entry if present,
    # so it surfaces cleanly in the existing frontend factor cards
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
    )


# ── Main Interface ─────────────────────────────────────────────────────────────

async def generate_insight(ad: Ad) -> InsightResult:
    """
    Generate creative insights for an ad.

    Automatically selects the appropriate mode:
    - Visual (static): ad has media_local_path and ad_type == STATIC
    - Visual (video): ad has frame_metadata populated and ad_type == VIDEO
    - Text-only: no local media available (snapshot parsing failed)

    The text-only path is explicitly documented in the insight output via
    the analysis_mode field, so downstream consumers know the limitation.
    """
    has_image = (
        ad.media_local_path
        and os.path.exists(ad.media_local_path)
        and _is_valid_image(ad.media_local_path)
    )
    has_frames = ad.frame_metadata and len(ad.frame_metadata) > 0

    if ad.ad_type == "VIDEO" and has_frames:
        system_prompt = VIDEO_VISUAL_SYSTEM_PROMPT
        messages = _build_video_visual_messages(ad)
        mode = "visual"
    elif ad.ad_type == "STATIC" and has_image:
        system_prompt = STATIC_VISUAL_SYSTEM_PROMPT
        messages = _build_static_visual_messages(ad)
        mode = "visual"
    else:
        # Copy-only path — no media available
        logger.info(
            "insight_text_only_mode",
            ad_id=str(ad.id),
            ad_type=ad.ad_type,
            has_image=bool(has_image),
            reason="No local media available — snapshot parsing may have failed or URL was inaccessible",
        )
        system_prompt = TEXT_ONLY_SYSTEM_PROMPT
        messages = _build_text_only_messages(ad)
        mode = "text_only"

    payload = {
        "model": settings.INSIGHT_MODEL,
        "max_tokens": 2000,  # Bumped from 1500 — richer evidence needs room
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
                logger.error(
                    "openrouter_no_choices",
                    ad_id=str(ad.id),
                    response=data,
                )
                raise ValueError(f"OpenRouter returned no choices. Response: {data}")

            raw_content = data["choices"][0]["message"]["content"]
            result = _parse_insight_response(raw_content, mode)
            result.model_used = settings.INSIGHT_MODEL
            result.prompt_version = PROMPT_VERSION
            metrics.increment(f"insights_generated_{mode}")
            logger.info("insight_generated", ad_id=str(ad.id), ad_type=ad.ad_type, mode=mode)
            return result

    except json.JSONDecodeError as exc:
        logger.error("insight_parse_error", ad_id=str(ad.id), error=str(exc))
        raise
    except Exception as exc:
        logger.error("insight_generation_error", ad_id=str(ad.id), error=str(exc))
        raise