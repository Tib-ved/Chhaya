import React from 'react';
import { SidebarLayout } from '../../components/SidebarLayout';
import { useWorkspace } from '../../components/WorkspaceContext';
import { WeatherCard, useWeather, WeatherLine } from '../../components/WeatherCard';
import { StateDistrictsChart } from '../../components/StateDistrictsChart';

/** The Weather tab: the stored weather of the state, district or ward chosen, and at state level its districts' peaks. */
export const RegionalWeather: React.FC = () => {
  const { scope, choose } = useWorkspace();
  const { view, error, loading } = useWeather({ state: scope.state_id, district_id: scope.district_id, ward_id: scope.ward_id });

  const clock = (ms: number) => new Intl.DateTimeFormat('en-IN', { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(ms));

  return (
    <SidebarLayout
      role="regional"
      filters
      header={
        <div className="head head-overview">
          <div className="head-title-group">
            <h1>Weather</h1>
            <p>Real-time heat monitoring, scenario curves, and district alert thresholds.</p>
          </div>
          <div className="head-actions">
            <span className="overview-updated">Updated {view?.fetched_at ? clock(Date.parse(view.fetched_at)) : 'Live'}</span>
          </div>
        </div>
      }
    >
      {loading && !view && <section className="card"><p role="status">Loading the forecast…</p></section>}
      {error && !view && <p className="ops-error" role="alert">The forecast could not be read: {error}</p>}
      {view && <>
        <WeatherLine view={view} onChooseDistrict={id => choose('district', id)} />
        <div className="ov-grid">
          <div className="ov-wide"><WeatherCard view={view} /></div>
          {view.area.kind === 'state' && view.state && <div className="ov-wide"><StateDistrictsChart state={view.state} onOpen={id => choose('district', id)} /></div>}
        </div>
      </>}
    </SidebarLayout>
  );
};

export default RegionalWeather;
