import React from 'react';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { Layout } from './components/Layout';
import { Brands } from './pages/Brands';
import { Ads } from './pages/Ads';
import { AdDetail } from './pages/AdDetail';
import { SWRConfig } from 'swr';
import './App.css';

function App() {
  return (
    <SWRConfig 
      value={{
        revalidateOnFocus: false,
        shouldRetryOnError: false
      }}
    >
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Layout />}>
            <Route index element={<Brands />} />
            <Route path="ads" element={<Ads />} />
            <Route path="ads/:id" element={<AdDetail />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </SWRConfig>
  );
}

export default App;
