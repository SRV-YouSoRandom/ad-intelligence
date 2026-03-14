// Removed unused React import
import { NavLink, Outlet } from 'react-router-dom';
import { LayoutDashboard, Megaphone, Activity } from 'lucide-react';

export const Layout = () => {
  return (
    <div className="app-layout">
      {/* Sidebar */}
      <aside className="app-sidebar">
        <div className="sidebar-header">
          <div className="logo-wrap">
            <Activity color="var(--status-info-text)" strokeWidth={2.5} size={20} />
            <span>AdIntel</span>
          </div>
        </div>
        
        <nav className="sidebar-nav">
          <NavLink 
            to="/" 
            className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
            end
          >
            <LayoutDashboard size={18} />
            Brands
          </NavLink>
          <NavLink 
            to="/ads" 
            className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          >
            <Megaphone size={18} />
            All Ads
          </NavLink>
        </nav>
      </aside>
      
      {/* Main Content Area */}
      <main className="main-content">
        <div className="content-inner">
          <Outlet />
        </div>
      </main>
    </div>
  );
};
