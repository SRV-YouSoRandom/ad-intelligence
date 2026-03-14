import { useState } from 'react';
import { useSearchParams, Link } from 'react-router-dom';
import { useAds } from '../api';
import { Button } from '../components/ui/Button';
import { Badge } from '../components/ui/Badge';
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
}


export const Ads = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  
  // Parse query params to local state
  const [brandId, setBrandId] = useState(searchParams.get('brand_id') || '');
  const [status, setStatus] = useState(searchParams.get('status') || 'ALL');
  const [type, setType] = useState(searchParams.get('type') || 'ALL');
  const [sortBy, setSortBy] = useState(searchParams.get('sort_by') || 'created_at');
  
  // Data fetch
  const { ads, total, isLoading } = useAds({
    brand_id: brandId,
    status,
    type,
    sort_by: sortBy,
    order: 'desc',
    limit: 50
  });

  // Apply filters
  const applyFilters = () => {
    const params: Record<string, string> = {};
    if (brandId) params.brand_id = brandId;
    if (status !== 'ALL') params.status = status;
    if (type !== 'ALL') params.type = type;
    if (sortBy !== 'created_at') params.sort_by = sortBy;
    setSearchParams(params);
  };

  const getPerformanceBadge = (label: string | null) => {
    if (!label) return <span className="text-muted text-sm">—</span>;
    if (label === 'STRONG') return <Badge variant="success">Strong</Badge>;
    if (label === 'AVERAGE') return <Badge variant="warning">Average</Badge>;
    if (label === 'WEAK') return <Badge variant="error">Weak</Badge>;
    return <Badge>{label}</Badge>;
  };
  
  const getTypeIcon = (ad_type: string) => {
    if (ad_type === 'STATIC') return <ImageIcon size={14} color="currentColor" />;
    if (ad_type === 'VIDEO') return <Video size={14} color="currentColor" />;
    return <HelpCircle size={14} color="currentColor" />;
  };

  return (
    <div className="page-wrapper">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 'var(--space-8)' }}>
        <div>
          <h1 style={{ marginBottom: 'var(--space-1)' }}>Ad Library</h1>
          <p className="text-muted text-sm">Browse and filter ads to analyze performance.</p>
        </div>
      </div>

      {/* Filters Bar */}
      <div className="card" style={{ padding: 'var(--space-4)', marginBottom: 'var(--space-6)', display: 'flex', gap: 'var(--space-4)', flexWrap: 'wrap', alignItems: 'flex-end' }}>
        <div style={{ flex: 1, minWidth: '200px' }}>
          <label className="text-xs text-muted" style={{ display: 'block', marginBottom: '4px' }}>Brand ID</label>
          <input 
            type="text" 
            value={brandId}
            onChange={(e) => setBrandId(e.target.value)}
            placeholder="UUID (required)"
            style={{ width: '100%', padding: '8px 12px', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-subtle)' }}
          />
        </div>
        
        <div>
          <label className="text-xs text-muted" style={{ display: 'block', marginBottom: '4px' }}>Status</label>
          <select 
            value={status} 
            onChange={(e) => setStatus(e.target.value)}
            style={{ padding: '8px 12px', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-subtle)' }}
          >
            <option value="ALL">All Status</option>
            <option value="ACTIVE">Active</option>
            <option value="INACTIVE">Inactive</option>
          </select>
        </div>

        <div>
          <label className="text-xs text-muted" style={{ display: 'block', marginBottom: '4px' }}>Format</label>
          <select 
            value={type} 
            onChange={(e) => setType(e.target.value)}
            style={{ padding: '8px 12px', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-subtle)' }}
          >
            <option value="ALL">All Formats</option>
            <option value="STATIC">Static</option>
            <option value="VIDEO">Video</option>
          </select>
        </div>
        
        <div>
          <label className="text-xs text-muted" style={{ display: 'block', marginBottom: '4px' }}>Sort By</label>
          <select 
            value={sortBy} 
            onChange={(e) => setSortBy(e.target.value)}
            style={{ padding: '8px 12px', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-subtle)' }}
          >
            <option value="created_at">Found Date</option>
            <option value="performance_score">Performance</option>
            <option value="impressions_mid">Impressions</option>
          </select>
        </div>

        <Button onClick={applyFilters} variant="primary">Apply Filters</Button>
      </div>

      {/* Results Table */}
      <div className="card">
        <div style={{ padding: 'var(--space-4) var(--space-6)', borderBottom: '1px solid var(--border-subtle)', display: 'flex', justifyContent: 'space-between' }}>
          <h3 style={{ fontSize: '1rem' }}>Results</h3>
          <span className="text-sm text-muted">{total} ads found</span>
        </div>
        
        {isLoading ? (
          <div style={{ padding: 'var(--space-12)', display: 'flex', justifyContent: 'center' }}>
            <Loader2 size={24} className="spinner text-muted" />
          </div>
        ) : ads.length === 0 ? (
          <div style={{ padding: 'var(--space-12)', textAlign: 'center' }}>
            <p className="text-muted">No ads match your criteria.</p>
            {brandId ? '' : <p className="text-sm text-muted" style={{ marginTop: '8px' }}>You must specify a Brand ID to see ads.</p>}
          </div>
        ) : (
          <div className="table-wrapper">
            <table className="table">
              <thead>
                <tr>
                  <th>Creative Preview</th>
                  <th>Format / Status</th>
                  <th>Delivery</th>
                  <th>Performance</th>
                  <th></th>
                </tr>
              </thead>
              <motion.tbody
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ duration: 0.3 }}
              >
                {ads.map((ad: Ad) => (
                  <tr key={String(ad.id)}>
                    <td style={{ maxWidth: '300px' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                        <div style={{ width: '40px', height: '40px', borderRadius: 'var(--radius-sm)', backgroundColor: 'var(--bg-surface-hover)', flexShrink: 0, overflow: 'hidden' }}>
                           {/* Media preview block placeholder */}
                           <div style={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-tertiary)' }}>
                              {getTypeIcon(ad.ad_type)}
                           </div>
                        </div>
                        <div>
                          <p className="text-sm" style={{ fontWeight: 500, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: '240px' }}>
                            {ad.caption || 'No copy text'}
                          </p>
                          <p className="text-xs text-muted" style={{ marginTop: '2px' }}>
                            ID: {ad.ad_archive_id}
                          </p>
                        </div>
                      </div>
                    </td>
                    <td>
                       <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', alignItems: 'flex-start' }}>
                          <Badge variant={ad.ad_type === 'VIDEO' ? 'info' : 'neutral'} icon={getTypeIcon(ad.ad_type)}>
                            {ad.ad_type}
                          </Badge>
                          <Badge variant={ad.is_active ? 'success' : 'neutral'}>
                            {ad.is_active ? 'Active' : 'Inactive'}
                          </Badge>
                       </div>
                    </td>
                    <td>
                      <p className="text-sm">
                        {ad.impressions_mid ? `~${Intl.NumberFormat('en-US').format(ad.impressions_mid)} imp.` : '--'}
                      </p>
                      <p className="text-xs text-muted" style={{ marginTop: '2px' }}>
                        {ad.start_date || '--'}
                      </p>
                    </td>
                    <td>{getPerformanceBadge(ad.performance_label)}</td>
                    <td style={{ textAlign: 'right' }}>
                      <Link to={`/ads/${ad.id}`}>
                        <Button variant="ghost" size="sm" icon={<ArrowRight size={14} />} />
                      </Link>
                    </td>
                  </tr>
                ))}
              </motion.tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
};
