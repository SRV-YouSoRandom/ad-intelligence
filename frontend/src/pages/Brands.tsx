import React, { useState } from 'react';
import { useBrands, searchBrand, useJobPoll, getBrandRecommendations } from '../api';
import { Button } from '../components/ui/Button';
import { Badge } from '../components/ui/Badge';
import {
  Search, Loader2, Lightbulb, TrendingUp, TrendingDown,
  ChevronDown, ChevronUp, X, Megaphone, Flag, RefreshCw,
  CheckCircle2,
} from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { Link } from 'react-router-dom';

interface Brand {
  id: string;
  page_name: string | null;
  page_id: string;
  ad_count: number;
  fetched_at: string;
}

interface Hypothesis {
  hypothesis: string;
  rationale: string;
  creative_type: string;
  priority: string;
}

interface RecommendationResult {
  brand_context: string;
  brand_name: string;
  total_ads_analyzed: number;
  portfolio_summary: string;
  static_patterns: { what_works: string[]; what_doesnt: string[] };
  video_patterns:  { what_works: string[]; what_doesnt: string[] };
  hypotheses_to_test: Hypothesis[];
  cached: boolean;
  generated_at: string | null;
}

const PriorityBadge = ({ priority }: { priority: string }) => {
  const map: Record<string, { variant: 'success' | 'warning' | 'neutral'; label: string }> = {
    high:   { variant: 'success', label: 'High priority' },
    medium: { variant: 'warning', label: 'Medium priority' },
    low:    { variant: 'neutral', label: 'Low priority' },
  };
  const cfg = map[priority] ?? map.low;
  return <Badge variant={cfg.variant}>{cfg.label}</Badge>;
};

const PatternList = ({ items, positive }: { items: string[]; positive: boolean }) => (
  <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
    {items.map((item, i) => (
      <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
        <div style={{
          width: 18, height: 18, borderRadius: '50%', flexShrink: 0, marginTop: 1,
          backgroundColor: positive ? '#dcfce7' : '#ffedd5',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          {positive
            ? <TrendingUp size={10} color="#15803d" />
            : <TrendingDown size={10} color="#c2410c" />}
        </div>
        <span style={{ fontSize: '0.875rem', lineHeight: 1.55, color: 'var(--text-primary)' }}>{item}</span>
      </div>
    ))}
  </div>
);

const RecommendationsPanel = ({
  brandId, brandName, onClose,
}: { brandId: string; brandName: string; onClose: () => void }) => {
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<RecommendationResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fetchReport = React.useCallback((forceRefresh = false) => {
    setLoading(true);
    setError(null);
    getBrandRecommendations(brandId, forceRefresh)
      .then(data => setResult(data))
      .catch(err => setError(err.message))
      .finally(() => setLoading(false));
  }, [brandId]);

  // Fetch on mount — will hit cache if available
  React.useEffect(() => { fetchReport(false); }, [fetchReport]);

  return (
    <motion.div
      initial={{ opacity: 0, y: -8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -8 }}
      style={{
        border: '1px solid var(--border-subtle)', borderRadius: 12,
        backgroundColor: 'var(--bg-surface)', overflow: 'hidden',
        boxShadow: '0 4px 24px rgba(0,0,0,0.07)', marginTop: 12,
      }}
    >
      {/* Panel header */}
      <div style={{
        padding: '14px 20px', borderBottom: '1px solid var(--border-subtle)',
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        backgroundColor: '#fafafa',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{ width: 30, height: 30, borderRadius: 8, backgroundColor: '#fef9c3', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <Lightbulb size={15} color="#a16207" />
          </div>
          <div>
            <div style={{ fontWeight: 600, fontSize: '0.875rem' }}>Creative Strategy Report</div>
            <div style={{ fontSize: '0.72rem', color: 'var(--text-secondary)' }}>{brandName}</div>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {/* Cache status indicator */}
          {result && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              {result.cached ? (
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: '0.72rem', color: '#15803d', backgroundColor: '#dcfce7', padding: '2px 8px', borderRadius: 999 }}>
                  <CheckCircle2 size={11} /> Cached
                  {result.generated_at && (
                    <span style={{ color: '#166534', opacity: 0.7 }}>
                      · {new Date(result.generated_at).toLocaleDateString()}
                    </span>
                  )}
                </span>
              ) : (
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: '0.72rem', color: '#1d4ed8', backgroundColor: '#dbeafe', padding: '2px 8px', borderRadius: 999 }}>
                  <Sparkle size={11} /> Fresh
                </span>
              )}
              {/* Refresh button — visible only when cached */}
              {result.cached && (
                <button
                  onClick={() => fetchReport(true)}
                  disabled={loading}
                  title="Regenerate report with latest insights (uses AI credits)"
                  style={{
                    display: 'inline-flex', alignItems: 'center', gap: 4,
                    fontSize: '0.72rem', color: 'var(--text-secondary)',
                    background: 'none', border: '1px solid var(--border-subtle)',
                    borderRadius: 6, padding: '3px 8px', cursor: loading ? 'not-allowed' : 'pointer',
                    opacity: loading ? 0.5 : 1,
                  }}
                >
                  <RefreshCw size={11} className={loading ? 'spinner' : ''} />
                  Regenerate
                </button>
              )}
            </div>
          )}
          <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-secondary)', padding: 4 }}>
            <X size={16} />
          </button>
        </div>
      </div>

      <div style={{ padding: '20px' }}>
        {loading && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '24px 0' }}>
            <Loader2 size={20} className="spinner" color="var(--status-info-text)" />
            <div>
              <div className="text-sm" style={{ color: 'var(--text-primary)', fontWeight: 500 }}>
                {result ? 'Regenerating report…' : 'Synthesizing patterns across ad portfolio…'}
              </div>
              <div className="text-xs text-muted" style={{ marginTop: 2 }}>
                This calls the AI model — may take 20–40 seconds
              </div>
            </div>
          </div>
        )}

        {error && (
          <div style={{ padding: '12px 16px', backgroundColor: 'var(--status-error-bg)', borderRadius: 8, color: 'var(--status-error-text)', fontSize: '0.875rem' }}>
            {error}
          </div>
        )}

        {result && !loading && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
            {/* Context badge + portfolio summary */}
            <div>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 10 }}>
                {result.brand_context === 'political'
                  ? <Badge variant="warning" icon={<Flag size={11} />}>Political Campaign</Badge>
                  : <Badge variant="info" icon={<Megaphone size={11} />}>Commercial Brand</Badge>}
                <span className="text-xs text-muted">{result.total_ads_analyzed} ads analyzed</span>
              </div>
              <p style={{ fontSize: '0.875rem', lineHeight: 1.7, color: 'var(--text-primary)', margin: 0 }}>
                {result.portfolio_summary}
              </p>
            </div>

            {/* Patterns grid */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
              {(result.static_patterns?.what_works?.length > 0 || result.static_patterns?.what_doesnt?.length > 0) && (
                <div style={{ border: '1px solid var(--border-subtle)', borderRadius: 10, padding: '14px 16px' }}>
                  <div style={{ fontWeight: 600, fontSize: '0.8rem', marginBottom: 12 }}>Static Ads</div>
                  {result.static_patterns.what_works?.length > 0 && (
                    <div style={{ marginBottom: 12 }}>
                      <div style={{ fontSize: '0.72rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.05em', color: '#15803d', marginBottom: 8 }}>What Works</div>
                      <PatternList items={result.static_patterns.what_works} positive={true} />
                    </div>
                  )}
                  {result.static_patterns.what_doesnt?.length > 0 && (
                    <div>
                      <div style={{ fontSize: '0.72rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.05em', color: '#c2410c', marginBottom: 8 }}>What Doesn't</div>
                      <PatternList items={result.static_patterns.what_doesnt} positive={false} />
                    </div>
                  )}
                </div>
              )}
              {(result.video_patterns?.what_works?.length > 0 || result.video_patterns?.what_doesnt?.length > 0) && (
                <div style={{ border: '1px solid var(--border-subtle)', borderRadius: 10, padding: '14px 16px' }}>
                  <div style={{ fontWeight: 600, fontSize: '0.8rem', marginBottom: 12 }}>Video Ads</div>
                  {result.video_patterns.what_works?.length > 0 && (
                    <div style={{ marginBottom: 12 }}>
                      <div style={{ fontSize: '0.72rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.05em', color: '#15803d', marginBottom: 8 }}>What Works</div>
                      <PatternList items={result.video_patterns.what_works} positive={true} />
                    </div>
                  )}
                  {result.video_patterns.what_doesnt?.length > 0 && (
                    <div>
                      <div style={{ fontSize: '0.72rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.05em', color: '#c2410c', marginBottom: 8 }}>What Doesn't</div>
                      <PatternList items={result.video_patterns.what_doesnt} positive={false} />
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* Hypotheses */}
            {result.hypotheses_to_test?.length > 0 && (
              <div>
                <div style={{ fontSize: '0.72rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.07em', color: 'var(--text-secondary)', marginBottom: 10 }}>
                  Hypotheses to Test
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {result.hypotheses_to_test.map((h, i) => (
                    <div key={i} style={{ padding: '12px 16px', border: '1px solid var(--border-subtle)', borderRadius: 10, backgroundColor: 'var(--bg-page)' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 6, gap: 8 }}>
                        <span style={{ fontWeight: 600, fontSize: '0.875rem', lineHeight: 1.4 }}>{h.hypothesis}</span>
                        <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
                          <Badge variant="neutral">{h.creative_type}</Badge>
                          <PriorityBadge priority={h.priority} />
                        </div>
                      </div>
                      <p style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', lineHeight: 1.55, margin: 0 }}>{h.rationale}</p>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </motion.div>
  );
};

// tiny inline component — avoids importing another icon lib
const Sparkle = ({ size }: { size: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor">
    <path d="M12 2l2.4 7.4H22l-6.2 4.5 2.4 7.4L12 17l-6.2 4.3 2.4-7.4L2 9.4h7.6z"/>
  </svg>
);

export const Brands = () => {
  const { brands, isLoading, mutate } = useBrands();
  const [pageId, setPageId] = useState('');
  const [countries, setCountries] = useState('GB,DE,FR');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [openRecommendationId, setOpenRecommendationId] = useState<string | null>(null);

  const { job, isPolling } = useJobPoll(activeJobId);

  React.useEffect(() => {
    if (job?.status === 'DONE') { mutate(); setActiveJobId(null); }
  }, [job?.status, mutate]);

  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!pageId) return;
    setIsSubmitting(true);
    try {
      const res = await searchBrand({
        identifier: pageId,
        identifier_type: 'page_id',
        countries: countries.split(',').map(c => c.trim().toUpperCase()),
        ad_active_status: 'ALL',
      });
      setActiveJobId(res.job_id);
      setPageId('');
    } catch (err) {
      alert('Failed to start search: ' + (err instanceof Error ? err.message : 'Unknown error'));
    } finally {
      setIsSubmitting(false);
    }
  };

  const toggleRecommendations = (brandId: string) => {
    setOpenRecommendationId(prev => prev === brandId ? null : brandId);
  };

  return (
    <div className="page-wrapper">
      <div style={{ marginBottom: 'var(--space-8)' }}>
        <h1 style={{ marginBottom: 'var(--space-1)' }}>Brands Intelligence</h1>
        <p className="text-muted text-sm">Monitor Meta ad performance and generate AI creative insights.</p>
      </div>

      <div className="card" style={{ padding: 'var(--space-6)', marginBottom: 'var(--space-8)' }}>
        <h3 style={{ marginBottom: 'var(--space-4)' }}>Fetch New Brand Ads</h3>
        <form onSubmit={handleSearch} style={{ display: 'flex', gap: 'var(--space-4)', flexWrap: 'wrap' }}>
          <div style={{ flex: 1, minWidth: '200px' }}>
            <label className="text-xs text-muted" style={{ display: 'block', marginBottom: '4px' }}>Facebook Page ID</label>
            <input type="text" value={pageId} onChange={e => setPageId(e.target.value)} placeholder="e.g. 15087023444" required />
          </div>
          <div style={{ flex: 1, minWidth: '200px' }}>
            <label className="text-xs text-muted" style={{ display: 'block', marginBottom: '4px' }}>Countries (comma separated)</label>
            <input type="text" value={countries} onChange={e => setCountries(e.target.value)} placeholder="GB,DE,FR" required />
          </div>
          <div style={{ display: 'flex', alignItems: 'flex-end' }}>
            <Button type="submit" isLoading={isSubmitting} icon={<Search size={16} />}>Search Ads</Button>
          </div>
        </form>

        {isPolling && (
          <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }}
            style={{ marginTop: 'var(--space-4)', padding: 'var(--space-4)', backgroundColor: 'var(--status-info-bg)', borderRadius: 'var(--radius-md)', display: 'flex', alignItems: 'center', gap: '12px' }}>
            <Loader2 size={18} className="spinner" color="var(--status-info-text)" />
            <span className="text-sm" style={{ color: 'var(--status-info-text)' }}>Fetching ads in background…</span>
          </motion.div>
        )}
      </div>

      <div className="card">
        <div style={{ padding: 'var(--space-4) var(--space-6)', borderBottom: '1px solid var(--border-subtle)' }}>
          <h3 style={{ fontSize: '1rem' }}>Tracked Brands</h3>
        </div>

        {isLoading ? (
          <div style={{ padding: 'var(--space-8)', textAlign: 'center' }}>
            <Loader2 size={24} className="spinner text-muted" style={{ margin: '0 auto' }} />
          </div>
        ) : brands.length === 0 ? (
          <div style={{ padding: 'var(--space-8)', textAlign: 'center', color: 'var(--text-secondary)' }}>
            <p>No brands tracked yet. Search a Page ID above to begin.</p>
          </div>
        ) : (
          <div>
            {brands.map((brand: Brand) => (
              <div key={brand.id} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr 1fr 1fr auto auto', alignItems: 'center', padding: '12px 20px', gap: 16 }}>
                  <div style={{ fontWeight: 500 }}>{brand.page_name || 'Unknown'}</div>
                  <div className="text-muted text-sm">{brand.page_id}</div>
                  <div><Badge variant="neutral">{brand.ad_count} ads</Badge></div>
                  <div className="text-muted text-sm">{new Date(brand.fetched_at).toLocaleDateString()}</div>
                  <Link to={`/ads?brand_id=${brand.id}`}>
                    <Button variant="ghost" size="sm">View Ads</Button>
                  </Link>
                  <Button
                    variant={openRecommendationId === brand.id ? 'secondary' : 'ghost'}
                    size="sm"
                    onClick={() => toggleRecommendations(brand.id)}
                    icon={openRecommendationId === brand.id ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                  >
                    Strategy
                  </Button>
                </div>

                <AnimatePresence>
                  {openRecommendationId === brand.id && (
                    <div style={{ padding: '0 20px 20px' }}>
                      <RecommendationsPanel
                        brandId={brand.id}
                        brandName={brand.page_name || brand.page_id}
                        onClose={() => setOpenRecommendationId(null)}
                      />
                    </div>
                  )}
                </AnimatePresence>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};