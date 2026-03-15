import { useState } from 'react';
import { useSearchParams, Link } from 'react-router-dom';
import { useAds } from '../api';
import { Button } from '../components/ui/Button';
import { Badge } from '../components/ui/Badge';
import { Select } from '../components/ui/Select';
import { Loader2, ArrowRight, Image as ImageIcon, Video, HelpCircle } from 'lucide-react';
import { motion } from 'framer-motion';

export interface Ad {
  id: string;
  ad_archive_id: string;
  brand_id: string;
  caption: string | null;
  ad_type: string;
  is_active: boolean;
  start_date: string | null;
  end_date: string | null;
  impressions_mid: number | null;
  performance_label: string | null;
  media_local_path?: string | null;
}

const labelStyle: React.CSSProperties = {
  display: 'block',
  fontSize: '0.72rem',
  fontWeight: 600,
  textTransform: 'uppercase',
  letterSpacing: '0.05em',
  color: 'var(--text-secondary)',
  marginBottom: 5,
};

export const Ads = () => {
  const [searchParams, setSearchParams] = useSearchParams();

  const [brandId, setBrandId] = useState(searchParams.get('brand_id') || '');
  const [status, setStatus]   = useState(searchParams.get('status') || 'ALL');
  const [type, setType]       = useState(searchParams.get('type') || 'ALL');
  const [sortBy, setSortBy]   = useState(searchParams.get('sort_by') || 'created_at');

  const { ads, total, isLoading } = useAds({
    brand_id: brandId, status, type, sort_by: sortBy, order: 'desc', limit: 50,
  });

  const applyFilters = () => {
    const params: Record<string, string> = {};
    if (brandId)              params.brand_id = brandId;
    if (status !== 'ALL')     params.status   = status;
    if (type !== 'ALL')       params.type     = type;
    if (sortBy !== 'created_at') params.sort_by = sortBy;
    setSearchParams(params);
  };

  const getPerformanceBadge = (label: string | null) => {
    if (!label) return <span style={{ color: 'var(--text-tertiary)', fontSize: '0.8rem' }}>—</span>;
    if (label === 'STRONG')  return <Badge variant="success">Strong</Badge>;
    if (label === 'AVERAGE') return <Badge variant="warning">Average</Badge>;
    if (label === 'WEAK')    return <Badge variant="error">Weak</Badge>;
    return <Badge>{label}</Badge>;
  };

  const getTypeIcon = (t: string) => {
    if (t === 'STATIC') return <ImageIcon size={14} />;
    if (t === 'VIDEO')  return <Video size={14} />;
    return <HelpCircle size={14} />;
  };

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 'var(--space-6)' }}>
        <div>
          <h1 style={{ marginBottom: 4 }}>Ad Library</h1>
          <p className="text-muted text-sm">Browse and filter ads to analyze performance.</p>
        </div>
      </div>

      {/* ── Filters ── */}
      <div className="card" style={{ padding: '16px 20px', marginBottom: 'var(--space-5)', overflow: 'visible', position: 'relative', zIndex: 10 }}>
        <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'flex-end' }}>

          <div style={{ flex: '1 1 200px' }}>
            <label style={labelStyle}>Brand ID</label>
            <input
              type="text"
              value={brandId}
              onChange={e => setBrandId(e.target.value)}
              placeholder="Paste UUID here…"
            />
          </div>

          <div style={{ flex: '0 0 140px' }}>
            <label style={labelStyle}>Status</label>
            <Select 
              value={status} 
              onChange={setStatus}
              options={[
                { label: 'All', value: 'ALL' },
                { label: 'Active', value: 'ACTIVE' },
                { label: 'Inactive', value: 'INACTIVE' },
              ]}
            />
          </div>

          <div style={{ flex: '0 0 140px' }}>
            <label style={labelStyle}>Format</label>
            <Select 
              value={type} 
              onChange={setType}
              options={[
                { label: 'All formats', value: 'ALL' },
                { label: 'Static', value: 'STATIC' },
                { label: 'Video', value: 'VIDEO' },
              ]}
            />
          </div>

          <div style={{ flex: '0 0 160px' }}>
            <label style={labelStyle}>Sort by</label>
             <Select 
              value={sortBy} 
              onChange={setSortBy}
              options={[
                { label: 'Date found', value: 'created_at' },
                { label: 'Performance', value: 'performance_score' },
                { label: 'Impressions', value: 'impressions_mid' },
              ]}
            />
          </div>

          <div style={{ flex: '0 0 auto', paddingBottom: 1 }}>
            <Button onClick={applyFilters} variant="primary">Apply</Button>
          </div>
        </div>
      </div>

      {/* ── Results ── */}
      <div className="card">
        <div style={{ padding: '12px 20px', borderBottom: '1px solid var(--border-subtle)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h3 style={{ fontSize: '0.9rem', margin: 0 }}>Results</h3>
          <span className="text-sm text-muted">{total} ads</span>
        </div>

        {isLoading ? (
          <div style={{ padding: 'var(--space-12)', display: 'flex', justifyContent: 'center' }}>
            <Loader2 size={24} className="spinner text-muted" />
          </div>
        ) : ads.length === 0 ? (
          <div style={{ padding: 'var(--space-12)', textAlign: 'center' }}>
            <p className="text-muted">No ads match your filters.</p>
            {!brandId && <p className="text-sm text-muted" style={{ marginTop: 8 }}>Enter a Brand ID to get started.</p>}
          </div>
        ) : (
          <div className="table-wrapper">
            <table className="table">
              <thead>
                <tr>
                  <th>Creative</th>
                  <th>Format</th>
                  <th>Delivery</th>
                  <th>Performance</th>
                  <th></th>
                </tr>
              </thead>
              <motion.tbody initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.25 }}>
                {ads.map((ad: Ad) => {
                  const localImgUrl = ad.media_local_path
                    ? `${import.meta.env.VITE_API_URL?.replace('/api/v1', '')}/media/${ad.media_local_path.split('/media_storage/').pop()}`
                    : null;
                  
                  return (
                  <tr key={String(ad.id)}>
                    <td style={{ maxWidth: 320 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                        <div style={{
                          width: 48, height: 48, borderRadius: 8, flexShrink: 0,
                          backgroundColor: 'var(--bg-surface-hover)', border: '1px solid var(--border-subtle)',
                          display: 'flex', alignItems: 'center', justifyContent: 'center',
                          color: 'var(--text-tertiary)', overflow: 'hidden'
                        }}>
                          {localImgUrl ? (
                            <img src={localImgUrl} alt="Ad creative" style={{ width: '100%', height: '100%', objectFit: 'cover' }} onError={(e) => { e.currentTarget.style.display = 'none'; e.currentTarget.parentElement!.innerHTML = getTypeIcon(ad.ad_type) as any; }} />
                          ) : (
                            getTypeIcon(ad.ad_type)
                          )}
                        </div>
                        <div style={{ minWidth: 0 }}>
                          <p style={{ fontWeight: 500, fontSize: '0.875rem', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: 260, margin: 0 }}>
                            {ad.caption || <span style={{ color: 'var(--text-tertiary)', fontStyle: 'italic' }}>No copy text</span>}
                          </p>
                          <p style={{ fontSize: '0.72rem', color: 'var(--text-tertiary)', marginTop: 2 }}>
                            {ad.ad_archive_id}
                          </p>
                        </div>
                      </div>
                    </td>

                    <td>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                        <Badge variant={ad.ad_type === 'VIDEO' ? 'info' : 'neutral'} icon={getTypeIcon(ad.ad_type)}>
                          {ad.ad_type}
                        </Badge>
                        <Badge variant={ad.is_active ? 'success' : 'neutral'}>
                          {ad.is_active ? 'Active' : 'Inactive'}
                        </Badge>
                      </div>
                    </td>

                    <td>
                      <p style={{ fontSize: '0.875rem', margin: 0 }}>
                        {ad.impressions_mid ? `~${Intl.NumberFormat('en-US').format(ad.impressions_mid)}` : <span style={{ color: 'var(--text-tertiary)' }}>—</span>}
                      </p>
                      <p style={{ fontSize: '0.75rem', color: 'var(--text-tertiary)', marginTop: 2 }}>
                        {ad.start_date ?? '—'}
                      </p>
                    </td>

                    <td>{getPerformanceBadge(ad.performance_label)}</td>

                    <td style={{ textAlign: 'right' }}>
                      <Link to={`/ads/${ad.id}`}>
                        <Button variant="ghost" size="sm" icon={<ArrowRight size={14} />} />
                      </Link>
                    </td>
                  </tr>
                  );
                })}
              </motion.tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
};