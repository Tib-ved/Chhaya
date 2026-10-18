import React from 'react';
import { Link } from 'react-router-dom';
import { ChhayaBrand } from '../components/ChhayaBrand';
import { switchMode } from '../lib/mode';

export const LandingPage: React.FC = () => {
  return (
    <div className="land-wrapper">
      {/* Top Header */}
      <header className="welcome-header">
        <Link to="/" aria-label="Chhaya home"><ChhayaBrand /></Link>
        <Link to="/evidence">How we test Chhaya</Link>
      </header>

      {/* Main Split Screen */}
      <main className="land-main">
        {/* Left Column: Chhaya Brand, Sun & Shade Logo, Story & Pilot Context */}
        <div className="land-pitch">
          <div className="welcome-logo-panel"><ChhayaBrand variant="lockup" /></div>
          <h1 className="land-headline">Check on people at home during heat and cold waves.</h1>
          <p className="land-desc">Chhaya calls registered residents in their preferred language and asks how they’re feeling. Health teams can review their answers and arrange a visit when needed.</p>
          <div className="land-features">
            <div className="land-feature-card"><b>Try a check-in call</b><span>Use your microphone to hear how Chhaya asks about symptoms and daily needs.</span></div>
            <div className="land-feature-card"><b>See why a visit is needed</b><span>Read the resident’s answers and the rules behind their priority level.</span></div>
            <div className="land-feature-card"><b>Keep track of visits</b><span>Assign a health worker and record what happened at the door.</span></div>
          </div>
          <div className="land-meta"><span className="dot green" /><span>Sixteen districts in four states · Rajasthan under a heat alert</span></div>
        </div>

        {/* Right Column: Roles Entry Workspace */}
        <div className="land-roles">
          <div className="land-roles-title">Choose your view / भूमिका चुनें</div>
          {/* Regional Role Card */}
          <Link to="/regional/overview" className="role-card regional" onClick={() => switchMode('live')}>
            <b>Regional officer</b>
            <span>For district and ward officers</span>
            <span>See which residents still need help. Read their calls, assign health workers, or ask Chhaya about your area.</span>
            <em>Open Regional Officer view <span aria-hidden="true">→</span></em>
          </Link>
          {/* Ward Role Card */}
          <Link to="/ward/overview?state=RJ&district=churu" className="role-card ward">
            <b>Field crew</b>
            <span>For ASHA workers and field teams</span>
            <span>Check who to visit, read what they said on the phone, and update the officer after your visit.</span>
            <em>Open Field crew view <span aria-hidden="true">→</span></em>
          </Link>
          {/* Direct Quick Exploration Links */}
          <div className="chips"><Link to="/call" className="chip">Try a microphone call</Link><Link to="/regional/critical" className="chip">Urgent cases</Link><Link to="/regional/report" className="chip">Situation report</Link></div>
          <small>The demo uses fictional residents. Dashboard notifications are simulated. The microphone test makes a real AI call.</small>
        </div>
      </main>
    </div>
  );
};

export default LandingPage;
