# Design Decisions & Product Thinking

## What This Platform Is — And What It Isn't

This platform was built to analyze ad creatives from the Meta Ads Library and generate AI-powered performance insights. Before explaining the decisions made, it is important to be precise about the fundamental constraint that shapes everything: **the Meta Ads Library API is a transparency tool, not a marketing analytics tool.**

Understanding this distinction is the most important thing to know about this project.

---

## The Meta Ads Library: What It Actually Is

The Meta Ads Library was created for regulatory compliance and public transparency — specifically to let journalists, researchers, and regulators see what political and social issue ads are being run, by whom, and where. It was not designed for marketing teams.

What this means in practice:

- **Impression and reach data is only available for EU-delivered ads**, under GDPR transparency requirements. For US commercial ads, these fields return null. This is by design.
- **The API returns no direct media URLs**. The `ad_snapshot_url` field points to a rendered HTML preview page, not a downloadable image or video file. Extracting actual creative media requires parsing that HTML page — a fragile process that depends on Meta's page structure remaining consistent.
- **There is no engagement data**. No clicks, no conversions, no CTR, no frequency. The API was built for accountability, not optimization.
- **The archive depth is limited**. For non-political commercial ads outside the EU, the archive is shallow and coverage is inconsistent.

This platform is built honestly around these constraints. Every limitation is documented. No data is fabricated or estimated beyond what is explicitly labeled as an estimate.

---

## Why This Cannot Be a Competitor Analysis Tool

A common instinct when seeing a platform that fetches public ad data is to ask: "Can I use this to spy on competitors?"

The answer, for this platform and for any tool built on the Meta Ads Library API, is **no** — and the reasons are worth stating clearly.

**The data is incomplete by design.** You can see that a competitor ran an ad. You cannot see how it performed unless that ad was delivered to EU audiences and the numbers happened to be included in the API response. For most commercial brands running primarily US campaigns, impression and reach data simply does not exist in the API response. Any tool claiming to show you competitor performance numbers from this data source is either fabricating them, using a different (likely scraped, likely against ToS) data source, or showing you EU-only data while implying it represents global performance.

**Creative context is missing.** Even when performance data is available, there is no targeting data, no A/B test context, no frequency information, and no conversion data. A "strong performer" by reach efficiency could be a retargeting ad shown to a warm audience of 500 people, or a broad awareness ad. Without targeting context, performance comparisons across brands are not meaningful.

**The legal and ethical position of scraping is unclear.** Any approach that goes beyond the official API — scraping the Ads Library UI, using browser automation to extract data Meta has not made available via API — enters a legally grey area and violates Meta's Terms of Service. This platform explicitly does not do this.

---

## Where This Platform Has Real Merit: Brand Self-Analysis

The platform becomes genuinely valuable in one specific scenario: **a brand analyzing its own ads**.

When a brand uses this platform to look at their own page's ads, several things change:

**Access to the real API.** Meta's Marketing API — which brands access through their own Business Manager — returns complete data: exact impression counts, reach, clicks, CPM, frequency, conversions, placement breakdowns, and audience data. This is the data that actually enables performance analysis. A brand can feed this data into the same AI insight pipeline and get analyses grounded in real numbers rather than range estimates.

**Creative context exists.** The brand knows which ads were retargeting vs prospecting, which were A/B tested, which ran at what spend level. This context makes the AI-generated insights meaningfully actionable rather than speculative.

**The insight quality improves dramatically.** The AI analysis in this platform was built to work even with limited data — analyzing copy when no image is available, being explicit about confidence levels. With full data, the same system produces analyses that are genuinely useful for creative strategy decisions.

**The recommendation layer becomes powerful.** The optional brand recommendation feature — which identifies what creative patterns correlate with strong performance across a brand's portfolio — is only meaningful when applied to a brand's own complete dataset. Applied to competitor data from the Ads Library API, it would be drawing patterns from a partial, biased sample.

---

## Key Technical Decisions

### Why EU Countries as the Primary Data Source

The fetch pipeline defaults to `["GB", "DE", "FR"]` rather than `["US"]`. This is deliberate. EU-delivered ads are archived under GDPR transparency rules, which means they are more likely to carry impression and reach data. US commercial ads almost never return this data via the Ads Library API. Targeting EU countries is not a limitation — it is the correct approach for getting any meaningful performance data out of this API.

### Why Insights Are User-Triggered, Not Automatic

The original design auto-generated insights for all fetched ads at the end of the fetch pipeline. This was changed to on-demand triggering. The reasons: the AI model calls are expensive (even on free tiers, they consume quota), the value of analyzing an ad is zero if the ad has no caption text and no media, and the user should choose which ads are worth analyzing rather than having the system burn resources on every ad indiscriminately.

### Why Text-Only Analysis Exists as a Mode

When the snapshot HTML parsing fails to extract a real image — which happens frequently because Meta's snapshot pages require session authentication — the insight generator falls back to analyzing the ad copy and performance data alone. This is not a workaround or a hack. It is an honest acknowledgment of what data is available. The `analysis_mode` field in every insight explicitly marks whether visual analysis was used, so the frontend can communicate this to users.

### Why Scoring Is Relative Within a Brand

Performance labels (STRONG / AVERAGE / WEAK) are calculated as percentile rankings within a single brand's dataset, not across all brands. This is the only defensible approach given that impression ranges vary enormously by brand size, industry, and campaign objective. A "strong" ad for a small brand and a "strong" ad for Nike mean completely different things in absolute terms. Relative ranking within the brand's own dataset is meaningful. Cross-brand absolute comparison is not.

### Why Valkey Over Redis for the Job Queue

Valkey is a Redis-compatible open source fork that is not subject to the licensing changes Redis made in 2024. It is functionally identical for this use case — BLPOP, RPUSH, HMSET, Lua scripting all work identically. The choice is forward-looking: as Redis's open source licensing becomes increasingly restricted, Valkey is the production-safe choice for new projects.

### Why the Media Extraction Architecture Is What It Is

The snapshot HTML parsing approach (BeautifulSoup + CDN URL extraction) is explicitly fragile and explicitly documented as such. It works when Meta's snapshot pages load and contain parseable image/video tags. It fails when pages require authentication, when Meta changes their HTML structure, or when CDN URLs have expired. The fallback to text-only analysis means the system never hard-fails on missing media — it degrades gracefully and documents the degradation. This is the correct engineering response to an unreliable external dependency.
