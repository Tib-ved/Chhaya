import React from 'react';
import { Link } from 'react-router-dom';
import { SidebarLayout } from '../../components/SidebarLayout';
import { openInDemo } from '../../lib/mode';

export const RegionalAgentTester: React.FC = () => (
  <SidebarLayout
    role="regional"
    header={<div className="head"><div><h1>Try a check-in call</h1><p>Talk to Chhaya as a resident would. Chhaya asks how you are and records your answers, as it would on a real call.</p><p className="muted call-dataset-note">Test calls run in the demo dataset.</p></div></div>}
  >
    <div className="call-start">
      <section className="card call-start-steps" aria-labelledby="call-steps">
        <header><h2 id="call-steps">How it works</h2></header>
        <ol>
          <li><b>Choose who you are</b><span>Four residents, each with a nudge for what to say, and the language to speak in.</span></li>
          <li><b>Take the call</b><span>Talk in your browser for up to five minutes, or get a call on your phone for up to three.</span></li>
          <li><b>See what Chhaya recorded</b><span>The transcript, the answers and the priority level, set by fixed rules.</span></li>
        </ol>
        <small className="muted">These are real AI calls. A phone call rings only the number you enter and adds no case to the register.</small>
      </section>

      <section className="card call-start-option" aria-labelledby="call-browser">
        <header><h2 id="call-browser">In your browser</h2></header>
        <p>Use your microphone. Headphones stop the agent hearing itself.</p>
        <ul className="call-start-facts"><li>Up to five minutes</li><li>Hindi, English, Marathi, Gujarati or Marwari</li></ul>
        <Link to="/call" className="btn big" onClick={e => { if (openInDemo('/call')) e.preventDefault(); }}>Start a microphone call</Link>
      </section>

      <section className="card call-start-option" aria-labelledby="call-phone">
        <header><h2 id="call-phone">On your phone</h2></header>
        <p>Chhaya rings the number you enter, as it would ring a resident.</p>
        <ul className="call-start-facts"><li>Up to three minutes</li><li>Only your own number</li></ul>
        <Link to="/call?via=phone" className="btn big ghost" onClick={e => { if (openInDemo('/call?via=phone')) e.preventDefault(); }}>Get a call on your phone</Link>
      </section>
    </div>
  </SidebarLayout>
);

export default RegionalAgentTester;
