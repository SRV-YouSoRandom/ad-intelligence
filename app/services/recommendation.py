"""
Brand-level recommendation engine.
Synthesizes patterns across a brand's ad portfolio to generate
creative hypotheses the brand should test next.

Political brand awareness: Political ad portfolios are analyzed
differently — reach breadth and message consistency matter more
than conversion funnel optimization.
"""

import json

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import Brand

logger = get_logger(__name__)

RECOMMENDATION_SYSTEM_PROMPT = """You are a creative strategist analyzing a brand's complete ad portfolio performance data.

You will be given a structured JSON summary of a brand's ads and their insights, grouped by performance label (STRONG / AVERAGE / WEAK) and creative type (STATIC / VIDEO).

The brand may be a commercial brand OR a political party. The ad_context field in the data will tell you which. Adjust your analysis accordingly:
- Commercial brands: focus on conversion signals, CTA clarity, product visibility, audience targeting signals
- Political brands: focus on message amplification, reach breadth, leader presence, emotional resonance, identity signaling

Your task is to identify recurring creative patterns that correlate with strong performance, and generate actionable hypotheses the brand should test next.

Rules:
- Base all observations on the data provided. Do not invent patterns.
- Be specific. Reference actual traits from the provided insights, not generic advice.
- For political brands, do not recommend commercial CTAs like "Shop Now" — recommend message, visual, or format experiments appropriate to political communication.
- Output ONLY valid JSON. No markdown. No preamble.

Output schema:
{
  "brand_context": "commercial|political",
  "static_patterns": {
    "what_works": ["specific pattern observed in STRONG static ads"],
    "what_doesnt": ["specific pattern observed in WEAK static ads"]
  },
  "video_patterns": {
    "what_works": ["specific pattern observed in STRONG video ads"],
    "what_doesnt": ["specific pattern observed in WEAK video ads"]
  },
  "hypotheses_to_test": [
    {
      "hypothesis": "Clear one-sentence hypothesis",
      "rationale": "Why this is worth testing based on the data",
      "creative_type": "STATIC|VIDEO|BOTH",
      "priority": "high|medium|low"
    }
  ],
  "portfolio_summary": "2-3 sentence summary of the brand's overall creative strategy and its effectiveness, written from the perspective of a strategist briefing a creative director."
}

Produce 2-4 patterns per category and 3-5 hypotheses. Only include patterns and hypotheses grounded in the observed data. Set priority based on how strongly the data supports the hypothesis."""


async def generate_brand_recommendations(brand: Brand, insights_summary: dict) -> dict:
    """
    Generate brand-level creative recommendations by synthesizing patterns
    across the entire ad portfolio.

    Args:
        brand: The Brand model instance
        insights_summary: Dict with:
          - total: int
          - brand_context: 'commercial' | 'political'
          - strong: list of {ad_type, ad_context, factors, summary}
          - average: list of same
          - weak: list of same

    Returns:
        Dict with static_patterns, video_patterns, hypotheses_to_test, portfolio_summary
    """
    user_content = json.dumps({
        "brand": brand.page_name,
        "total_ads_analyzed": insights_summary.get("total", 0),
        "brand_context": insights_summary.get("brand_context", "commercial"),
        "strong_ads": insights_summary.get("strong", []),
        "average_ads": insights_summary.get("average", []),
        "weak_ads": insights_summary.get("weak", []),
    }, indent=2, ensure_ascii=False)

    payload = {
        "model": settings.INSIGHT_MODEL,
        "max_tokens": 2000,
        "messages": [
            {"role": "system", "content": RECOMMENDATION_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
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

            raw_content = data["choices"][0]["message"]["content"]
            content = raw_content.strip()
            if "</think>" in content:
                content = content.split("</think>")[-1].strip()
            clean = content.lstrip("```json").rstrip("```").strip()
            result = json.loads(clean)

            logger.info("brand_recommendations_generated", brand=brand.page_name)
            return result

    except Exception as exc:
        logger.error("recommendation_generation_error", brand=brand.page_name, error=str(exc))
        raise