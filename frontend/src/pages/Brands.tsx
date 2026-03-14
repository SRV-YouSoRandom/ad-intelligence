import React, { useState } from 'react';
import { useBrands, searchBrand, useJobPoll } from '../api';
import { Button } from '../components/ui/Button';
import { Badge } from '../components/ui/Badge';
import { Search, Loader2 } from 'lucide-react';
import { motion } from 'framer-motion';
import { Link } from 'react-router-dom';

interface Brand {
  id: string;
  page_name: string | null;
  page_id: string;
  ad_count: number;
  fetched_at: string;
}

export const Brands = () => {
  const { brands, isLoading, mutate } = useBrands();
  const [pageId, setPageId] = useState('');
  const [countries, setCountries] = useState('GB,DE,FR');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  
  const { job, isPolling } = useJobPoll(activeJobId);

  // If the job finishes, refresh the brands list
  React.useEffect(() => {
    if (job?.status === 'DONE') {
      mutate();
      setActiveJobId(null);
    }
  }, [job?.status, mutate]);

  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!pageId) return;
    
    setIsSubmitting(true);
    try {
      const selectedCountries = countries.split(',').map(c => c.trim().toUpperCase());
      const res = await searchBrand({
        identifier: pageId,
        identifier_type: 'page_id',
        countries: selectedCountries,
        ad_active_status: 'ALL'
      });
      setActiveJobId(res.job_id);
      setPageId('');
    } catch (err) {
      console.error(err);
      alert('Failed to start search');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="page-wrapper">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 'var(--space-8)' }}>
        <div>
          <h1 style={{ marginBottom: 'var(--space-1)' }}>Brands Intelligence</h1>
          <p className="text-muted text-sm">Monitor Meta ad performance and generate AI creative insights.</p>
        </div>
      </div>

      <div className="card" style={{ padding: 'var(--space-6)', marginBottom: 'var(--space-8)' }}>
        <h3 style={{ marginBottom: 'var(--space-4)' }}>Fetch New Brand Ads</h3>
        <form onSubmit={handleSearch} style={{ display: 'flex', gap: 'var(--space-4)', flexWrap: 'wrap' }}>
          <div style={{ flex: 1, minWidth: '200px' }}>
            <label className="text-xs text-muted" style={{ display: 'block', marginBottom: '4px' }}>Facebook Page ID</label>
            <input 
              type="text" 
              value={pageId}
              onChange={(e) => setPageId(e.target.value)}
              placeholder="e.g. 15087023444"
              required
              style={{ width: '100%', padding: '8px 12px', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-subtle)' }}
            />
          </div>
          <div style={{ flex: 1, minWidth: '200px' }}>
            <label className="text-xs text-muted" style={{ display: 'block', marginBottom: '4px' }}>Countries (comma separated)</label>
            <input 
              type="text" 
              value={countries}
              onChange={(e) => setCountries(e.target.value)}
              placeholder="GB,DE,FR"
              required
              style={{ width: '100%', padding: '8px 12px', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-subtle)' }}
            />
          </div>
          <div style={{ display: 'flex', alignItems: 'flex-end' }}>
            <Button type="submit" isLoading={isSubmitting} icon={<Search size={16} />}>
              Search Ads
            </Button>
          </div>
        </form>

        {/* Polling Indicator */}
        {isPolling && (
          <motion.div 
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            style={{ marginTop: 'var(--space-4)', padding: 'var(--space-4)', backgroundColor: 'var(--status-info-bg)', borderRadius: 'var(--radius-md)', display: 'flex', alignItems: 'center', gap: '12px' }}
          >
            <Loader2 size={18} className="spinner" color="var(--status-info-text)" />
            <span className="text-sm" style={{ color: 'var(--status-info-text)' }}>
              Fetching ads in background. This may take a minute...
            </span>
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
          <div className="table-wrapper">
            <table className="table">
              <thead>
                <tr>
                  <th>Brand Name</th>
                  <th>Page ID</th>
                  <th>Total Ads</th>
                  <th>Last Fetched</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {brands.map((brand: Brand) => (
                  <tr key={brand.id}>
                    <td>
                      <div style={{ fontWeight: 500 }}>{brand.page_name || 'Unknown'}</div>
                    </td>
                    <td className="text-muted">{brand.page_id}</td>
                    <td>
                      <Badge variant="neutral">{brand.ad_count} ads</Badge>
                    </td>
                    <td className="text-muted text-sm">
                      {new Date(brand.fetched_at).toLocaleDateString()}
                    </td>
                    <td>
                      <Link to={`/ads?brand_id=${brand.id}`}>
                        <Button variant="ghost" size="sm">View Ads</Button>
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
};
