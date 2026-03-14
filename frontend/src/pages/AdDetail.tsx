import React, { useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { useAdDetail, useInsights, useJobPoll, generateInsight, deleteInsight } from '../api';
import { Button } from '../components/ui/Button';
import { Badge } from '../components/ui/Badge';
import { Sparkles, Loader2, ArrowLeft, Trash2, ExternalLink } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

export const AdDetail = () => {
  const { id } = useParams();
  const { ad, isLoading: adLoading } = useAdDetail(id);
  const { insight, isLoading: insightLoading, mutate: mutateInsight } = useInsights(id);
  
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const { job, isPolling } = useJobPoll(activeJobId);

  // Poll for job completion
  React.useEffect(() => {
    if (job?.status === 'DONE' || job?.status === 'FAILED') {
      mutateInsight();
      setActiveJobId(null);
    }
  }, [job?.status, mutateInsight]);
  
  // Set job ID if insight is pending
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
    } catch {
      alert('Failed to start generation. Make sure the ad is scored first.');
    }
  };

  const handleDelete = async () => {
    if (!id) return;
    if (!confirm('Are you sure you want to delete this insight?')) return;
    try {
      await deleteInsight(id);
      mutateInsight();
    } catch {
      alert('Failed to delete insight.');
    }
  };

  if (adLoading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', padding: 'var(--space-12)' }}>
         <Loader2 className="spinner text-muted" size={32} />
      </div>
    );
  }

  if (!ad) {
    return (
      <div className="page-wrapper" style={{ padding: 'var(--space-8)' }}>
        <h2 className="text-muted">Ad not found</h2>
        <Link to="/ads"><Button variant="secondary" style={{ marginTop: 'var(--space-4)' }}>Back to Ads</Button></Link>
      </div>
    );
  }

  const hasInsight = insight?.summary;
  const isPending = isPolling || insight?.status === 'pending';
  const showNotGenerated = !hasInsight && !isPending && !insightLoading;

  return (
    <div className="page-wrapper" style={{ maxWidth: '900px', margin: '0 auto' }}>
      <div style={{ marginBottom: 'var(--space-6)' }}>
        <Link to="/ads" style={{ display: 'inline-flex', alignItems: 'center', gap: '8px', color: 'var(--text-secondary)', fontSize: '0.875rem' }}>
          <ArrowLeft size={16} /> Back to Library
        </Link>
      </div>

      <div style={{ display: 'flex', gap: 'var(--space-8)', flexWrap: 'wrap' }}>
        
        {/* Ad Meta Panel */}
        <div style={{ flex: '1', minWidth: '300px' }}>
          <div className="card" style={{ padding: 'var(--space-6)', height: '100%' }}>
             <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 'var(--space-4)' }}>
                <h2 style={{ fontSize: '1.25rem' }}>Creative Details</h2>
                <Badge variant={ad.is_active ? 'success' : 'neutral'}>
                  {ad.is_active ? 'Active' : 'Inactive'}
                </Badge>
             </div>
             
             <div style={{ marginBottom: 'var(--space-6)' }}>
               {ad.snapshot_url && (
                 <a href={ad.snapshot_url} target="_blank" rel="noreferrer" style={{ display: 'inline-flex', alignItems: 'center', gap: '4px', fontSize: '0.875rem', color: 'var(--status-info-text)' }}>
                   <ExternalLink size={14} /> View Ad in Meta Library
                 </a>
               )}
             </div>

             <div style={{ padding: 'var(--space-4)', backgroundColor: 'var(--bg-surface-hover)', borderRadius: 'var(--radius-md)', marginBottom: 'var(--space-6)' }}>
               <p className="text-sm" style={{ fontWeight: 500, lineHeight: 1.6 }}>{ad.caption || 'No caption text provided for this ad.'}</p>
             </div>
             
             <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--space-4)' }}>
                <div>
                   <span className="text-xs text-muted" style={{ display: 'block' }}>Format</span>
                   <span className="text-sm font-medium">{ad.ad_type}</span>
                </div>
                <div>
                   <span className="text-xs text-muted" style={{ display: 'block' }}>Delivery Schedule</span>
                   <span className="text-sm font-medium">{ad.start_date} → {ad.end_date || 'Present'}</span>
                </div>
                <div>
                   <span className="text-xs text-muted" style={{ display: 'block' }}>Est. Impressions</span>
                   <span className="text-sm font-medium">{ad.impressions_mid ? `~${Intl.NumberFormat('en-US').format(ad.impressions_mid)}` : 'N/A'}</span>
                </div>
                <div>
                   <span className="text-xs text-muted" style={{ display: 'block' }}>Performance</span>
                   {ad.performance_label ? (
                     <Badge variant={ad.performance_label === 'STRONG' ? 'success' : ad.performance_label === 'WEAK' ? 'error' : 'warning'}>
                       {ad.performance_label}
                     </Badge>
                   ) : <span className="text-sm text-muted">Unscored</span>}
                </div>
             </div>
          </div>
        </div>

        {/* AI Insight Panel */}
        <div style={{ flex: '1.5', minWidth: '400px' }}>
          <AnimatePresence mode="wait">
            
            {showNotGenerated && (
              <motion.div 
                key="not_generated"
                initial={{ opacity: 0, scale: 0.98 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.98 }}
                className="card" 
                style={{ padding: 'var(--space-12)', textAlign: 'center', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', minHeight: '340px' }}
              >
                <div style={{ width: '48px', height: '48px', borderRadius: 'var(--radius-full)', backgroundColor: 'var(--status-info-bg)', display: 'flex', alignItems: 'center', justifyContent: 'center', marginBottom: 'var(--space-6)' }}>
                  <Sparkles size={24} color="var(--status-info-text)" />
                </div>
                <h3 style={{ marginBottom: 'var(--space-2)' }}>Unlock Creative Insights</h3>
                <p className="text-muted text-sm" style={{ marginBottom: 'var(--space-8)', maxWidth: '280px' }}>
                  Use advanced vision models to analyze the creative elements that drove this ad's performance.
                </p>
                <Button variant="primary" size="lg" onClick={handleGenerate} icon={<Sparkles size={18} />}>
                  Generate Report
                </Button>
              </motion.div>
            )}

            {isPending && (
              <motion.div 
                key="pending"
                initial={{ opacity: 0, scale: 0.98 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.98 }}
                className="card" 
                style={{ padding: 'var(--space-12)', textAlign: 'center', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', minHeight: '340px' }}
              >
                <Loader2 size={36} className="spinner" color="var(--status-info-text)" style={{ marginBottom: 'var(--space-6)' }} />
                <h3>Analyzing Creative</h3>
                <p className="text-muted text-sm" style={{ marginTop: 'var(--space-2)' }}>Our AI is watching the video frames and reading the copy to build your insight report. This usually takes 15-30 seconds.</p>
              </motion.div>
            )}

            {hasInsight && (
              <motion.div 
                key="insight"
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                className="card" 
                style={{ display: 'flex', flexDirection: 'column', height: '100%' }}
              >
                <div style={{ padding: 'var(--space-6)', borderBottom: '1px solid var(--border-subtle)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                     <Sparkles size={20} color="var(--status-info-text)" />
                     <h3 style={{ fontSize: '1.125rem', margin: 0 }}>AI Strategy Report</h3>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <Badge variant={insight.analysis_mode === 'visual' ? 'info' : 'neutral'}>
                      {insight.analysis_mode === 'visual' ? 'Visual + Text' : 'Text Only'}
                    </Badge>
                    <Button variant="ghost" size="sm" onClick={handleDelete} title="Delete Insight">
                      <Trash2 size={16} />
                    </Button>
                  </div>
                </div>

                <div style={{ padding: 'var(--space-6)' }}>
                  <p className="text-sm" style={{ lineHeight: 1.7, marginBottom: 'var(--space-8)', color: 'var(--text-primary)' }}>
                    {insight.summary}
                  </p>

                  <h4 style={{ fontSize: '0.875rem', textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-secondary)', marginBottom: 'var(--space-4)' }}>
                    Key Factors
                  </h4>

                  <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
                    {insight.factors?.map((f: { trait: string, impact: string, confidence: string, evidence: string }, i: number) => (
                      <div key={i} style={{ padding: 'var(--space-4)', borderRadius: 'var(--radius-md)', border: `1px solid ${f.impact === 'positive' ? 'var(--status-success-text)' : f.impact === 'negative' ? 'var(--status-error-text)' : 'var(--border-subtle)'}`, backgroundColor: f.impact === 'positive' ? 'var(--status-success-bg)' : f.impact === 'negative' ? 'var(--status-error-bg)' : 'transparent' }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 'var(--space-2)' }}>
                          <span style={{ fontWeight: 600, fontSize: '0.875rem', textTransform: 'capitalize' }}>{f.trait.replace(/_/g, ' ')}</span>
                          <Badge variant={f.confidence === 'high' ? 'success' : 'neutral'}>{f.confidence} confidence</Badge>
                        </div>
                        <p className="text-sm" style={{ margin: 0 }}>{f.evidence}</p>
                      </div>
                    ))}
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
