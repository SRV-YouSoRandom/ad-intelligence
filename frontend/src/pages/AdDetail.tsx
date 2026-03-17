import React, { useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { useAdDetail, useInsights, useJobPoll, generateInsight, deleteInsight, getMediaUrl } from '../api';
import { Button } from '../components/ui/Button';
import { Badge } from '../components/ui/Badge';
import {
  Sparkles, Loader2, ArrowLeft, Trash2, ExternalLink,
  TrendingUp, TrendingDown, Minus, CheckCircle2, AlertCircle,
  Info, Eye, Type, Palette, Users, MousePointerClick,
  LayoutTemplate, Lightbulb, Video, Zap, Clock, Flag, Megaphone,
  Image as ImageIcon,
} from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

// ── Category metadata ─────────────────────────────────────────────────────────

const CATEGORY_META: Record<string, { label: string; icon: React.ReactNode; color: string }> = {
  composition:              { label: 'Composition',        icon: <LayoutTemplate size={13} />,    color: '#7c3aed' },
  cta:                      { label: 'Call to Action',     icon: <MousePointerClick size={13} />, color: '#0369a1' },
  copy:                     { label: 'Copy',               icon: <Type size={13} />,              color: '#0369a1' },
  color_contrast:           { label: 'Color & Contrast',  icon: <Palette size={13} />,           color: '#9333ea' },
  product_visibility:       { label: 'Product Visibility',icon: <Eye size={13} />,               color: '#0891b2' },
  message_clarity:          { label: 'Message Clarity',   icon: <Eye size={13} />,               color: '#0891b2' },
  human_presence:           { label: 'Human Presence',    icon: <Users size={13} />,             color: '#0369a1' },
  emotional_tone:           { label: 'Emotional Tone',    icon: <Zap size={13} />,              color: '#7c3aed' },
  hook_strength:            { label: 'Hook Strength',     icon: <Zap size={13} />,              color: '#dc2626' },
  pacing:                   { label: 'Pacing',            icon: <Clock size={13} />,             color: '#0369a1' },
  text_overlay_quality:     { label: 'Text Overlays',     icon: <Type size={13} />,              color: '#0369a1' },
  cta_placement:            { label: 'CTA Placement',     icon: <MousePointerClick size={13} />, color: '#0891b2' },
  scene_transition_quality: { label: 'Scene Transitions', icon: <Video size={13} />,             color: '#7c3aed' },
  offer_clarity:            { label: 'Offer Clarity',     icon: <CheckCircle2 size={13} />,      color: '#059669' },
  message_visibility:       { label: 'Message Visibility',icon: <Eye size={13} />,               color: '#0891b2' },
  audience_signal:          { label: 'Audience Signal',   icon: <Users size={13} />,             color: '#0369a1' },
  cta_specificity:          { label: 'CTA Specificity',   icon: <MousePointerClick size={13} />, color: '#0891b2' },
  tone_authenticity:        { label: 'Tone',              icon: <Zap size={13} />,              color: '#7c3aed' },
  length_fit:               { label: 'Length',            icon: <Type size={13} />,              color: '#0369a1' },
  urgency_and_proof:        { label: 'Urgency & Proof',   icon: <AlertCircle size={13} />,       color: '#d97706' },
  recommendation:           { label: 'Next Test',         icon: <Lightbulb size={13} />,         color: '#059669' },
};

const getCategoryMeta = (cat: string) =>
  CATEGORY_META[cat] ?? { label: cat.replace(/_/g, ' '), icon: <Info size={13} />, color: '#6b7280' };

// ── Impact config ─────────────────────────────────────────────────────────────

const IMPACT_CONFIG = {
  positive: { icon: <TrendingUp size={14} />, label: 'Positive', bg: '#f0fdf4', border: '#86efac', text: '#15803d', badgeBg: '#dcfce7', badgeText: '#166534' },
  negative: { icon: <TrendingDown size={14} />, label: 'Negative', bg: '#fff7ed', border: '#fed7aa', text: '#c2410c', badgeBg: '#ffedd5', badgeText: '#9a3412' },
  neutral:  { icon: <Minus size={14} />, label: 'Neutral', bg: '#f8fafc', border: '#e2e8f0', text: '#475569', badgeBg: '#f1f5f9', badgeText: '#475569' },
};

const getImpact = (impact: string) =>
  IMPACT_CONFIG[impact as keyof typeof IMPACT_CONFIG] ?? IMPACT_CONFIG.neutral;

const ConfidenceDots = ({ level }: { level: string }) => {
  const filled = level === 'high' ? 3 : level === 'medium' ? 2 : 1;
  const color = level === 'high' ? '#15803d' : level === 'medium' ? '#d97706' : '#9ca3af';
  return (
    <div style={{ display: 'flex', gap: '3px', alignItems: 'center' }}>
      {[1, 2, 3].map(i => (
        <div key={i} style={{ width: 6, height: 6, borderRadius: '50%', backgroundColor: i <= filled ? color : '#e5e7eb' }} />
      ))}
      <span style={{ fontSize: '0.7rem', color: '#9ca3af', marginLeft: 2 }}>{level}</span>
    </div>
  );
};

// ── Snapshot preview ──────────────────────────────────────────────────────────

interface SnapshotPreviewProps {
  ad: {
    snapshot_url?: string;
    media_local_path?: string;
    ad_type?: string;
    frame_paths?: string[];
  };
}

const SnapshotPreview = ({ ad }: SnapshotPreviewProps) => {
  const [mediaError, setMediaError] = useState(false);
  const { snapshot_url, media_local_path, ad_type } = ad;

  const mediaUrl = getMediaUrl(media_local_path);
  const posterUrl = ad.frame_paths?.[0] ? getMediaUrl(ad.frame_paths[0]) : undefined;

  const isVideo = ad_type === 'VIDEO';

  if (mediaUrl && !mediaError) {
    return (
      <div style={{ marginBottom: 'var(--space-4)', overflow: 'hidden', borderRadius: 8, border: '1px solid var(--border-subtle)', backgroundColor: 'var(--bg-surface-hover)' }}>
        {isVideo ? (
          <video
            src={mediaUrl}
            controls
            poster={posterUrl || undefined}
            onError={() => setMediaError(true)}
            style={{ width: '100%', display: 'block', maxHeight: 350 }}
          />
        ) : (
          <img
            src={mediaUrl}
            alt="Ad creative"
            onError={() => setMediaError(true)}
            style={{ width: '100%', display: 'block', objectFit: 'contain', maxHeight: 350 }}
          />
        )}
      </div>
    );
  }

  if (snapshot_url) {
    return (
      <div style={{ marginBottom: 'var(--space-4)' }}>
        <a
          href={snapshot_url}
          target="_blank"
          rel="noreferrer"
          style={{
            display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
            width: '100%', minHeight: 140, borderRadius: 8,
            border: '1.5px dashed var(--border-subtle)', backgroundColor: 'var(--bg-surface-hover)',
            color: 'var(--text-secondary)', gap: 8, textDecoration: 'none',
            transition: 'all var(--transition-fast)', position: 'relative'
          }}
          onMouseEnter={e => { e.currentTarget.style.backgroundColor = '#f0f5ff'; e.currentTarget.style.borderColor = 'var(--status-info-text)'; }}
          onMouseLeave={e => { e.currentTarget.style.backgroundColor = 'var(--bg-surface-hover)'; e.currentTarget.style.borderColor = 'var(--border-subtle)'; }}
        >
          {isVideo ? <Video size={24} style={{ opacity: 0.4 }} /> : <ImageIcon size={24} style={{ opacity: 0.4 }} />}
          <span style={{ fontSize: '0.8rem', fontWeight: 500 }}>View {isVideo ? 'video' : 'creative'} in Meta Library</span>
          <span style={{ fontSize: '0.72rem', opacity: 0.6 }}>Opens in new tab</span>
          <ExternalLink size={12} style={{ position: 'absolute', top: 8, right: 8, opacity: 0.4 }} />
        </a>
      </div>
    );
  }

  return (
    <div style={{ 
      marginBottom: 'var(--space-4)', width: '100%', minHeight: 100, borderRadius: 8,
      border: '1px solid var(--border-subtle)', backgroundColor: 'var(--bg-surface-hover)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-tertiary)', fontSize: '0.75rem'
    }}>
      No creative available
    </div>
  );
};

// ── Factor Card ───────────────────────────────────────────────────────────────

type Factor = { trait: string; category: string; impact: string; confidence: string; evidence: string };

const FactorCard = ({ factor }: { factor: Factor }) => {
  const impact = getImpact(factor.impact);
  const catMeta = getCategoryMeta(factor.category);
  const isRecommendation = factor.category === 'recommendation';

  if (isRecommendation) {
    return (
      <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
        style={{ padding: '14px 16px', borderRadius: 10, border: '1.5px solid #a7f3d0', backgroundColor: '#f0fdf4', marginTop: 4 }}>
        <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
          <div style={{ width: 30, height: 30, borderRadius: 8, flexShrink: 0, backgroundColor: '#dcfce7', display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#059669' }}>
            <Lightbulb size={15} />
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 700, fontSize: '0.8rem', color: '#065f46', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 6 }}>
              Recommended Next Test
            </div>
            <p style={{ fontSize: '0.875rem', lineHeight: 1.65, color: '#064e3b', margin: 0 }}>{factor.evidence}</p>
          </div>
        </div>
      </motion.div>
    );
  }

  return (
    <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
      style={{ padding: '14px 16px', borderRadius: 10, border: `1.5px solid ${impact.border}`, backgroundColor: impact.bg }}>
      <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
        <div style={{ width: 30, height: 30, borderRadius: 8, flexShrink: 0, backgroundColor: impact.badgeBg, display: 'flex', alignItems: 'center', justifyContent: 'center', color: impact.text }}>
          {impact.icon}
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6, flexWrap: 'wrap', gap: 6 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
              <span style={{ fontWeight: 600, fontSize: '0.875rem', color: '#111827', textTransform: 'capitalize' }}>
                {factor.trait.replace(/_/g, ' ')}
              </span>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 3, padding: '2px 7px', borderRadius: 999, fontSize: '0.7rem', fontWeight: 500, backgroundColor: `${catMeta.color}18`, color: catMeta.color, border: `1px solid ${catMeta.color}30` }}>
                {catMeta.icon} {catMeta.label}
              </span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, padding: '2px 8px', borderRadius: 999, fontSize: '0.7rem', fontWeight: 600, backgroundColor: impact.badgeBg, color: impact.text }}>
                {impact.icon} {impact.label}
              </span>
              <ConfidenceDots level={factor.confidence} />
            </div>
          </div>
          <p style={{ fontSize: '0.875rem', lineHeight: 1.65, color: '#374151', margin: 0 }}>{factor.evidence}</p>
        </div>
      </div>
    </motion.div>
  );
};

// ── Main Component ────────────────────────────────────────────────────────────

export const AdDetail = () => {
  const { id } = useParams();
  const { ad, isLoading: adLoading } = useAdDetail(id);
  const { insight, isLoading: insightLoading, mutate: mutateInsight } = useInsights(id);

  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const { job, isPolling } = useJobPoll(activeJobId);

  React.useEffect(() => {
    if (job?.status === 'DONE' || job?.status === 'FAILED') {
      mutateInsight();
      setActiveJobId(null);
    }
  }, [job?.status, mutateInsight]);

  React.useEffect(() => {
    if (insight?.status === 'pending' && insight?.job_id && !activeJobId) {
      setActiveJobId(insight.job_id);
    }
  }, [insight?.status, insight?.job_id, activeJobId]);

  const handleGenerate = async () => {
    if (!id) return;
    try {
      const res = await generateInsight(id);
      setActiveJobId(res.job_id);
    } catch (err) {
      alert('Failed to start generation: ' + (err instanceof Error ? err.message : 'Unknown error'));
    }
  };

  const handleDelete = async () => {
    if (!id || !confirm('Delete this insight?')) return;
    try {
      await deleteInsight(id);
      mutateInsight();
    } catch {
      alert('Failed to delete insight.');
    }
  };

  if (adLoading) {
    return <div style={{ display: 'flex', justifyContent: 'center', padding: 'var(--space-12)' }}><Loader2 className="spinner text-muted" size={32} /></div>;
  }
  if (!ad) {
    return (
      <div style={{ padding: 'var(--space-8)' }}>
        <h2 className="text-muted">Ad not found</h2>
        <Link to="/ads"><Button variant="secondary" style={{ marginTop: 'var(--space-4)' }}>Back to Ads</Button></Link>
      </div>
    );
  }

  const hasInsight = insight?.summary;
  const isPending = isPolling || insight?.status === 'pending';
  const showNotGenerated = !hasInsight && !isPending && !insightLoading;

  const regularFactors = (insight?.factors ?? []).filter((f: Factor) => f.category !== 'recommendation');
  const recommendationFactor = (insight?.factors ?? []).find((f: Factor) => f.category === 'recommendation');
  const positiveCount = regularFactors.filter((f: Factor) => f.impact === 'positive').length;
  const negativeCount = regularFactors.filter((f: Factor) => f.impact === 'negative').length;

  const isPolitic = insight?.ad_context === 'political';

  return (
    <div style={{ maxWidth: 960, margin: '0 auto' }}>
      <div style={{ marginBottom: 'var(--space-6)' }}>
        <Link to="/ads" style={{ display: 'inline-flex', alignItems: 'center', gap: 8, color: 'var(--text-secondary)', fontSize: '0.875rem' }}>
          <ArrowLeft size={16} /> Back to Library
        </Link>
      </div>

      <div style={{ display: 'flex', gap: 'var(--space-6)', flexWrap: 'wrap', alignItems: 'flex-start' }}>

        {/* ── Left: Ad meta ── */}
        <div style={{ flex: '0 0 300px', minWidth: 280 }}>
          <div className="card" style={{ padding: 'var(--space-6)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 'var(--space-4)' }}>
              <h2 style={{ fontSize: '1rem', margin: 0 }}>Creative Details</h2>
              <Badge variant={ad.is_active ? 'success' : 'neutral'}>{ad.is_active ? 'Active' : 'Inactive'}</Badge>
            </div>

            {/* Snapshot preview — new */}
            <SnapshotPreview ad={ad} />

            {ad.snapshot_url && (
              <a href={ad.snapshot_url} target="_blank" rel="noreferrer"
                style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: '0.8rem', color: 'var(--status-info-text)', marginBottom: 'var(--space-4)' }}>
                <ExternalLink size={13} /> View in Meta Library
              </a>
            )}

            <div style={{ padding: 'var(--space-4)', backgroundColor: 'var(--bg-surface-hover)', borderRadius: 8, marginBottom: 'var(--space-5)', border: '1px solid var(--border-subtle)' }}>
              <p style={{ fontSize: '0.875rem', fontWeight: 500, lineHeight: 1.6, color: 'var(--text-primary)', margin: 0 }}>
                {ad.caption || <span style={{ color: 'var(--text-tertiary)', fontStyle: 'italic' }}>No caption text</span>}
              </p>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              {[
                { label: 'Format', value: ad.ad_type },
                { label: 'Delivery', value: `${ad.start_date ?? '—'} → ${ad.end_date ?? 'Present'}` },
                {
                  label: 'Est. Impressions',
                  value: ad.impressions_mid
                    ? `~${Intl.NumberFormat('en-US').format(ad.impressions_mid)}`
                    : <span style={{ color: 'var(--text-tertiary)' }}>No data</span>,
                },
                {
                  label: 'Imp. Range',
                  value: (ad.impressions_lower && ad.impressions_upper)
                    ? `${Intl.NumberFormat('en-US').format(ad.impressions_lower)} – ${Intl.NumberFormat('en-US').format(ad.impressions_upper)}`
                    : <span style={{ color: 'var(--text-tertiary)' }}>—</span>,
                },
              ].map(({ label, value }) => (
                <div key={label} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <span style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>{label}</span>
                  <span style={{ fontSize: '0.8rem', fontWeight: 500 }}>{value}</span>
                </div>
              ))}
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>Performance</span>
                {ad.performance_label ? (
                  <Badge variant={ad.performance_label === 'STRONG' ? 'success' : ad.performance_label === 'WEAK' ? 'error' : 'warning'}>
                    {ad.performance_label}
                  </Badge>
                ) : (
                  <span style={{ fontSize: '0.8rem', color: 'var(--text-tertiary)' }}>Unscored</span>
                )}
              </div>
            </div>
          </div>
        </div>

        {/* ── Right: Insights ── */}
        <div style={{ flex: 1, minWidth: 340 }}>
          <AnimatePresence mode="wait">

            {showNotGenerated && (
              <motion.div key="not_generated" initial={{ opacity: 0, scale: 0.98 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0 }}
                className="card" style={{ padding: 'var(--space-12)', textAlign: 'center', display: 'flex', flexDirection: 'column', alignItems: 'center', minHeight: 320 }}>
                <div style={{ width: 52, height: 52, borderRadius: 14, backgroundColor: 'var(--status-info-bg)', display: 'flex', alignItems: 'center', justifyContent: 'center', marginBottom: 'var(--space-5)' }}>
                  <Sparkles size={24} color="var(--status-info-text)" />
                </div>
                <h3 style={{ marginBottom: 8 }}>Generate Creative Insights</h3>
                <p className="text-muted text-sm" style={{ marginBottom: 'var(--space-6)', maxWidth: 280, lineHeight: 1.6 }}>
                  AI analysis of the creative elements that drove this ad's performance. Supports both commercial and political ad formats.
                </p>
                <Button variant="primary" size="lg" onClick={handleGenerate} icon={<Sparkles size={16} />}>
                  Generate Report
                </Button>
              </motion.div>
            )}

            {isPending && (
              <motion.div key="pending" initial={{ opacity: 0, scale: 0.98 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0 }}
                className="card" style={{ padding: 'var(--space-12)', textAlign: 'center', display: 'flex', flexDirection: 'column', alignItems: 'center', minHeight: 320 }}>
                <Loader2 size={36} className="spinner" color="var(--status-info-text)" style={{ marginBottom: 'var(--space-5)' }} />
                <h3 style={{ marginBottom: 8 }}>Analyzing Creative</h3>
                <p className="text-muted text-sm" style={{ lineHeight: 1.6 }}>
                  The AI is analyzing visual composition, copy, and performance signals. Usually takes 20–40 seconds.
                </p>
              </motion.div>
            )}

            {hasInsight && (
              <motion.div key="insight" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="card">
                {/* Header */}
                <div style={{ padding: '16px 20px', borderBottom: '1px solid var(--border-subtle)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <div style={{ width: 32, height: 32, borderRadius: 9, backgroundColor: 'var(--status-info-bg)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                      <Sparkles size={16} color="var(--status-info-text)" />
                    </div>
                    <div>
                      <div style={{ fontWeight: 600, fontSize: '0.9rem' }}>AI Strategy Report</div>
                      <div style={{ fontSize: '0.72rem', color: 'var(--text-secondary)', marginTop: 1, display: 'flex', alignItems: 'center', gap: 6 }}>
                        {insight.analysis_mode === 'visual' ? '📸 Visual + Copy' : '📝 Copy-only'}
                        {isPolitic
                          ? <><Flag size={10} color="#a16207" /><span style={{ color: '#a16207' }}>Political ad</span></>
                          : <><Megaphone size={10} /><span>Commercial ad</span></>}
                      </div>
                    </div>
                  </div>
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                    {(positiveCount > 0 || negativeCount > 0) && (
                      <div style={{ display: 'flex', gap: 6 }}>
                        {positiveCount > 0 && (
                          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 3, padding: '2px 8px', borderRadius: 999, fontSize: '0.72rem', fontWeight: 600, backgroundColor: '#dcfce7', color: '#15803d' }}>
                            <TrendingUp size={11} /> {positiveCount}
                          </span>
                        )}
                        {negativeCount > 0 && (
                          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 3, padding: '2px 8px', borderRadius: 999, fontSize: '0.72rem', fontWeight: 600, backgroundColor: '#ffedd5', color: '#c2410c' }}>
                            <TrendingDown size={11} /> {negativeCount}
                          </span>
                        )}
                      </div>
                    )}
                    <Button variant="ghost" size="sm" onClick={handleDelete} title="Delete insight">
                      <Trash2 size={15} />
                    </Button>
                  </div>
                </div>

                {/* Political disclaimer */}
                {isPolitic && (
                  <div style={{ margin: '0 20px', marginTop: 16, padding: '10px 14px', backgroundColor: '#fefce8', border: '1px solid #fde68a', borderRadius: 8, fontSize: '0.8rem', color: '#92400e', lineHeight: 1.5 }}>
                    <strong>Political ad context:</strong> This analysis accounts for political communication norms — reach breadth, message resonance, and authority signaling — rather than applying commercial conversion metrics.
                  </div>
                )}

                <div style={{ padding: '20px' }}>
                  {/* Summary */}
                  <div style={{ marginBottom: 20, padding: '14px 16px', backgroundColor: 'var(--bg-surface-hover)', borderRadius: 10, borderLeft: '3px solid var(--status-info-text)' }}>
                    <p style={{ fontSize: '0.875rem', lineHeight: 1.75, color: 'var(--text-primary)', margin: 0 }}>{insight.summary}</p>
                  </div>

                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
                    <span style={{ fontSize: '0.7rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.07em', color: 'var(--text-secondary)' }}>Analysis Factors</span>
                    <div style={{ flex: 1, height: 1, backgroundColor: 'var(--border-subtle)' }} />
                    <span style={{ fontSize: '0.7rem', color: 'var(--text-tertiary)' }}>{regularFactors.length} factors</span>
                  </div>

                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {regularFactors.map((f: Factor, i: number) => <FactorCard key={i} factor={f} />)}
                  </div>

                  {recommendationFactor && (
                    <>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '18px 0 10px' }}>
                        <span style={{ fontSize: '0.7rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.07em', color: 'var(--text-secondary)' }}>What to Test Next</span>
                        <div style={{ flex: 1, height: 1, backgroundColor: 'var(--border-subtle)' }} />
                      </div>
                      <FactorCard factor={recommendationFactor} />
                    </>
                  )}

                  <div style={{ marginTop: 16, paddingTop: 14, borderTop: '1px solid var(--border-subtle)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span style={{ fontSize: '0.7rem', color: 'var(--text-tertiary)' }}>
                      {insight.model_used?.split('/').pop()} · v{insight.prompt_version}
                    </span>
                    <span style={{ fontSize: '0.7rem', color: 'var(--text-tertiary)' }}>
                      {new Date(insight.generated_at).toLocaleDateString()}
                    </span>
                  </div>
                </div>
              </motion.div>
            )}

          </AnimatePresence>
        </div>
      </div>
    </div>
  );
};