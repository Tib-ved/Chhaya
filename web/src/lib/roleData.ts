// Chhaya Role Data & State Engine
// Synthetic regional model covering 4 districts, 12 wards, and 120 residents,
// with clinical safety triage rules (R1-R5, A1-A12) and real-time state mutation.

export interface WarningInfo {
  level: 'Red' | 'Orange' | 'Yellow';
  hazard: string;
  peak: number;
  peakHour: string;
  opened: string;
}

export interface District {
  id: string;
  name: string;
  lat: number;
  lng: number;
  warning: WarningInfo | null;
  workers: [string, string][]; // [en, hi]
}

export interface Ward {
  id: string;
  name: string;
  hi: string;
  district: string;
  worker: string;
  workerName: string;
  workerHi: string;
  centre: string;
  centreHi: string;
  lat: number;
  lng: number;
}

export interface CallInfo {
  status: 'reached' | 'noanswer' | 'trying';
  attempts: number;
  at: number; // minutes after 08:00
}

export interface AnswerItem {
  v: string;
  quote: string;
}

export interface VisitInfo {
  outcome: string;
  at: number;
  note: string;
  simulated?: boolean;
}

export interface EscalationInfo {
  reason: string;
  note: string;
  at: number;
  ack: boolean;
  reply: string;
}

export interface RaisedInfo {
  obs: string;
  at: number;
}

export interface Resident {
  id: string;
  name: string;
  hi: string;
  sex: 'f' | 'm' | 'unknown';
  age: number;
  alone: boolean;
  outdoor: boolean;
  conds: string[];
  ward: string;
  district: string;
  lat: number;
  lng: number;
  contact: [string, string];
  worker: string;
  risk: number;
  order?: number;
  call: CallInfo | null;
  answers: Record<string, AnswerItem> | null;
  tier: 'red' | 'amber' | 'green' | null;
  reasons: string[];
  opened?: number;
  cutShort?: boolean;
  emergency?: boolean;
  transcript?: [string, string, string | null][];
  note?: string;
  visit: VisitInfo | null;
  esc: EscalationInfo | null;
  raised: RaisedInfo | null;
  respondent: 'self' | 'family' | 'unknown';
  test?: boolean;
  // The call was a recorded conversation with an AI-played resident.
  simulated?: boolean;
}

export const NOW = 200; // demo clock: 11:20 (minutes after 08:00)

export const COND: Record<string, [string, string]> = {
  heart: ['heart condition', 'दिल की बीमारी'],
  diabetes: ['diabetes', 'मधुमेह'],
  kidney: ['kidney condition', 'गुर्दे की बीमारी'],
  respiratory: ['breathing condition', 'साँस की बीमारी']
};

export const DISTRICTS: District[] = [
  {
    id: 'churu',
    name: 'Churu',
    lat: 28.297,
    lng: 74.965,
    warning: { level: 'Red', hazard: 'Heatwave', peak: 47, peakHour: '15:00', opened: '08:02' },
    workers: [
      ['Sunita Kanwar', 'सुनीता कंवर'],
      ['Meena Bishnoi', 'मीना बिश्नोई'],
      ['Kavita Saini', 'कविता सैनी']
    ]
  },
  {
    id: 'bikaner',
    name: 'Bikaner',
    lat: 28.022,
    lng: 73.312,
    warning: null,
    workers: [
      ['Pushpa Godara', 'पुष्पा गोदारा'],
      ['Sarita Meghwal', 'सरिता मेघवाल'],
      ['Anita Vyas', 'अनीता व्यास']
    ]
  },
  {
    id: 'jhunjhunu',
    name: 'Jhunjhunu',
    lat: 28.129,
    lng: 75.398,
    warning: null,
    workers: [
      ['Manju Jangir', 'मंजू जांगिड़'],
      ['Rekha Saini', 'रेखा सैनी'],
      ['Suman Poonia', 'सुमन पूनिया']
    ]
  },
  {
    id: 'sikar',
    name: 'Sikar',
    lat: 27.609,
    lng: 75.139,
    warning: null,
    workers: [
      ['Geeta Dhaka', 'गीता ढाका'],
      ['Kiran Kumawat', 'किरण कुमावत'],
      ['Babita Jat', 'बबीता जाट']
    ]
  }
];

export const Q = ['feeling', 'fluids', 'company', 'shelter', 'exposure', 'medicine', 'help'];

export const QL: Record<string, [string, string]> = {
  feeling: ['Feeling', 'तबीयत'],
  fluids: ['Water and food', 'पानी और खाना'],
  company: ['Company', 'साथ'],
  shelter: ['Fan and electricity', 'पंखा और बिजली'],
  exposure: ['Going outside', 'बाहर जाना'],
  medicine: ['Medicines', 'दवाइयाँ'],
  help: ['Help needed', 'मदद']
};

export const VL: Record<string, Record<string, [string, string]>> = {
  feeling: {
    ok: ['No symptoms', 'कोई लक्षण नहीं'],
    mild: ['Mild symptoms', 'हल्के लक्षण'],
    severe: ['Severe symptoms', 'गंभीर लक्षण']
  },
  fluids: {
    enough: ['Enough', 'पर्याप्त'],
    low: ['Low', 'कम'],
    none: ['None', 'बिलकुल नहीं']
  },
  company: {
    with: ['With someone', 'कोई साथ है'],
    alone: ['Alone', 'अकेले']
  },
  shelter: {
    adequate: ['Adequate', 'ठीक'],
    partial: ['Partial', 'कभी-कभी'],
    none: ['None', 'नहीं']
  },
  exposure: {
    staying: ['Staying in', 'घर पर'],
    must: ['Must go out', 'बाहर जाना ज़रूरी']
  },
  medicine: {
    has: ['Has them', 'हैं'],
    low: ['Running low', 'कम बची हैं'],
    out: ['Run out', 'ख़त्म']
  },
  help: {
    none: ['None asked', 'नहीं माँगी'],
    wants: ['Wants a visit', 'मिलने को कहा'],
    urgent: ['Urgent', 'तुरंत चाहिए']
  }
};

export const WORRY: Record<string, Record<string, 'w' | 'r'>> = {
  feeling: { mild: 'w', severe: 'r' },
  fluids: { low: 'w', none: 'r' },
  company: { alone: 'w' },
  shelter: { partial: 'w', none: 'w' },
  exposure: { must: 'w' },
  medicine: { low: 'w', out: 'w' },
  help: { wants: 'w', urgent: 'r' }
};

export const ASK: Record<string, [string, Record<string, string>]> = {
  feeling: [
    'आज बहुत गर्मी है। अभी आपकी तबीयत कैसी है?',
    {
      ok: 'ठीक हूँ, कोई तकलीफ़ नहीं है।',
      mild: 'थोड़ा चक्कर आ रहा है।',
      severe: 'सीने में दर्द है, साँस लेने में तकलीफ़ हो रही है।'
    }
  ],
  fluids: [
    'आज आपने पानी पिया है? घर में पानी और खाना है?',
    {
      enough: 'हाँ, पानी भी है और खाना भी।',
      low: 'सुबह से बहुत कम पिया है।',
      none: 'घर में पानी ख़त्म हो गया है।'
    }
  ],
  company: [
    'क्या आज घर पर कोई आपके साथ है?',
    {
      with: 'हाँ, परिवार साथ है।',
      alone: 'नहीं, घर पर कोई नहीं है।'
    }
  ],
  shelter: [
    'क्या घर में पंखा चल रहा है? बिजली आ रही है?',
    {
      adequate: 'हाँ, पंखा चल रहा है।',
      partial: 'बिजली आती-जाती रहती है।',
      none: 'पंखा नहीं है।'
    }
  ],
  exposure: [
    'क्या आज आपको बाहर जाना या बाहर काम करना है?',
    {
      staying: 'नहीं, आज घर पर ही हूँ।',
      must: 'हाँ, काम पर जाना ही पड़ेगा।'
    }
  ],
  medicine: [
    'क्या आपकी रोज़ की दवाइयाँ आपके पास हैं?',
    {
      has: 'हाँ, दवाई रखी है।',
      low: 'दो दिन की ही बची है।',
      out: 'दवाई ख़त्म हो गई है।'
    }
  ],
  help: [
    'क्या आज आपको किसी मदद की ज़रूरत है?',
    {
      none: 'नहीं, अभी कुछ नहीं चाहिए।',
      wants: 'कोई आ जाए तो अच्छा रहेगा।',
      urgent: 'हाँ, जल्दी किसी को भेजिए।'
    }
  ]
};

export const RULES: Record<string, [string, string, 'red' | 'amber']> = {
  R1: ['Emergency flagged on the call', 'कॉल पर आपात स्थिति बताई', 'red'],
  R2: ['Severe symptoms', 'गंभीर लक्षण', 'red'],
  R3: ['Asked for urgent help', 'तुरंत मदद माँगी', 'red'],
  R4: ['No water or food, and alone', 'पानी-खाना नहीं, और अकेले', 'red'],
  R5: ['Raised by the health worker', 'आशा कार्यकर्ता ने बढ़ाया', 'red'],
  A1: ['Mild symptoms', 'हल्के लक्षण', 'amber'],
  A2: ['Low on water or food', 'पानी या खाना कम', 'amber'],
  A3: ['No fan or cool place', 'पंखा या ठंडी जगह नहीं', 'amber'],
  A4: ['Alone, aged 70 or over', 'अकेले, 70 से ऊपर', 'amber'],
  A5: ['Medicines low or run out', 'दवाइयाँ कम या ख़त्म', 'amber'],
  A6: ['Asked for a visit', 'मिलने को कहा', 'amber'],
  A7: ['Must go out, and at risk', 'बाहर जाना ज़रूरी, जोखिम में', 'amber'],
  A8: ['No answer after three calls', 'तीन बार फ़ोन नहीं उठा', 'amber'],
  A12: ['Call ended before every question', 'कॉल सारे सवालों से पहले ख़त्म', 'amber']
};

export const RULE_FROM: Record<string, string> = {
  R2: 'feeling',
  R3: 'help',
  R4: 'fluids',
  A1: 'feeling',
  A2: 'fluids',
  A3: 'shelter',
  A4: 'company',
  A5: 'medicine',
  A6: 'help',
  A7: 'exposure'
};

export const GUIDE: Record<string, [string, string, string][]> = {
  R1: [['danger', 'Go first. Call 108. Move the person to a cool place.', 'सबसे पहले जाएँ। 108 पर फ़ोन करें। व्यक्ति को ठंडी जगह ले जाएँ।']],
  R2: [
    ['check', 'Is the person conscious, confused, or hot and dry?', 'क्या व्यक्ति होश में है, घबराया हुआ है, या शरीर गर्म और सूखा है?'],
    ['danger', 'Go first. Call 108. Move the person to a cool place.', 'सबसे पहले जाएँ। 108 पर फ़ोन करें। व्यक्ति को ठंडी जगह ले जाएँ।']
  ],
  R3: [['ask', 'What help is needed right now?', 'अभी किस मदद की ज़रूरत है?']],
  R4: [
    ['bring', 'Water and ORS', 'पानी और ओ.आर.एस.'],
    ['ask', 'Who can stay with them today?', 'आज उनके साथ कौन रह सकता है?']
  ],
  R5: [['danger', 'You raised this case. Take them to the health centre or call 108.', 'आपने यह मामला बढ़ाया है। स्वास्थ्य केंद्र ले जाएँ या 108 पर फ़ोन करें।']],
  A1: [
    ['check', 'Are dizziness, headache or vomiting getting worse?', 'क्या चक्कर, सिरदर्द या उल्टी बढ़ रही है?'],
    ['danger', 'If worse: call 108 or take them to the health centre.', 'हालत बिगड़े तो 108 पर फ़ोन करें या स्वास्थ्य केंद्र ले जाएँ।']
  ],
  A2: [
    ['check', 'Is there drinking water for today?', 'क्या आज के लिए पीने का पानी है?'],
    ['bring', 'Water and ORS', 'पानी और ओ.आर.एस.']
  ],
  A3: [
    ['check', 'Is the electricity on?', 'क्या बिजली आ रही है?'],
    ['ask', 'Tell them the nearest cooling centre. Do not send someone unwell alone.', 'नज़दीकी शीतल केंद्र बताएँ। बीमार व्यक्ति को अकेले न भेजें।']
  ],
  A4: [['ask', 'Who can look in on them today? Note a neighbour or relative.', 'आज कौन उन्हें देखने आ सकता है? पड़ोसी या रिश्तेदार का नाम लिखें।']],
  A5: [['ask', 'Which medicine, and how many days are left? Note it and refer to the health centre. Do not suggest a medicine.', 'कौन सी दवाई, कितने दिन की बची है? लिखें और स्वास्थ्य केंद्र भेजें। कोई दवाई न सुझाएँ।']],
  A6: [['ask', 'What do they need help with?', 'उन्हें किस बात में मदद चाहिए?']],
  A7: [['ask', 'Can the work wait until evening? Cover the head, carry water, avoid midday.', 'क्या काम शाम तक रुक सकता है? सिर ढकें, पानी साथ रखें, दोपहर में न निकलें।']],
  A8: [['check', 'Is anyone at home? Knock. If nobody answers, ask a neighbour.', 'क्या घर पर कोई है? दरवाज़ा खटखटाएँ। कोई न मिले तो पड़ोसी से पूछें।']],
  A12: [['ask', 'The call ended early. Ask the remaining questions in person.', 'कॉल जल्दी ख़त्म हुई। बाक़ी सवाल सामने पूछें।']]
};

export const NEEDS: Record<string, [string, string]> = {
  ors: ['Water and ORS', 'पानी और ओ.आर.एस.'],
  med: ['Medicine check', 'दवाई की जाँच'],
  cool: ['Cooling centre details', 'शीतल केंद्र की जानकारी']
};

export const OUTCOMES: [string, string, string][] = [
  ['advised_ok', 'Advised, managing fine', 'समझाया, ठीक हैं'],
  ['supplies', 'Water, ORS or supplies given', 'पानी, ओ.आर.एस. या सामान दिया'],
  ['referred', 'Referred to the health centre', 'स्वास्थ्य केंद्र भेजा'],
  ['hospital', 'Taken to hospital', 'अस्पताल ले गए'],
  ['not_home', 'Not at home', 'घर पर नहीं मिले']
];

export const TELL: [string, string, string][] = [
  ['transport', 'Needs transport to hospital', 'अस्पताल ले जाने के लिए गाड़ी चाहिए'],
  ['supplies', 'Needs supplies I do not have', 'ज़रूरी सामान मेरे पास नहीं है'],
  ['missing', 'Cannot find the person', 'व्यक्ति नहीं मिल रहा'],
  ['other', 'Something else', 'कुछ और']
];

export const SAW: [string, string, string][] = [
  ['confused', 'Confused or not making sense', 'घबराहट, बात समझ नहीं आ रही'],
  ['fainted', 'Fainted or collapsed', 'बेहोश हुए या गिर पड़े'],
  ['dry', 'Hot, dry skin, not sweating', 'शरीर गर्म और सूखा, पसीना नहीं'],
  ['vomit', 'Vomiting again and again', 'बार-बार उल्टी'],
  ['chest', 'Chest pain or trouble breathing', 'सीने में दर्द या साँस की तकलीफ़'],
  ['other', 'Something else that worries me', 'कुछ और जो चिंता की बात है']
];

export const FORECAST = {
  hours: [6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20],
  temp: [31, 33, 36, 39, 41, 43, 45, 46, 47, 47, 46, 44, 41, 38, 36]
};

export const BANDS = ['Under 55', '55 to 64', '65 to 74', '75 and over'];

// Deterministic PRNG
function rng(seed: number) {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 4294967296;
  };
}

export function riskOf(r: { age: number; alone: boolean; outdoor: boolean; conds: string[] }): number {
  return (
    (r.age >= 75 ? 3 : r.age >= 65 ? 2 : 0) +
    (r.alone ? 2 : 0) +
    (r.outdoor ? 2 : 0) +
    r.conds.length
  );
}

export function riskWhy(r: { age: number; alone: boolean; outdoor: boolean; conds: string[] }): string {
  const w: string[] = [];
  if (r.age >= 75) w.push('75+');
  else if (r.age >= 65) w.push('65 to 74');
  if (r.alone) w.push('lives alone');
  if (r.outdoor) w.push('outdoor worker');
  r.conds.forEach(c => {
    if (COND[c]) w.push(COND[c][0]);
  });
  return w.join(', ') || 'none';
}

export function triage(r: {
  emergency?: boolean;
  raised?: RaisedInfo | null;
  call?: CallInfo | null;
  cutShort?: boolean;
  answers?: Record<string, AnswerItem> | null;
  age: number;
  conds: string[];
}): { reasons: string[]; tier: 'red' | 'amber' | 'green' } {
  const a = r.answers || {};
  const v = (k: string) => a[k] && a[k].v;
  const R: string[] = [];
  if (r.emergency) R.push('R1');
  if (v('feeling') === 'severe') R.push('R2');
  if (v('help') === 'urgent') R.push('R3');
  if (v('fluids') === 'none' && v('company') === 'alone') R.push('R4');
  if (r.raised) R.push('R5');
  if (v('feeling') === 'mild') R.push('A1');
  if (v('fluids') === 'low' || v('fluids') === 'none') R.push('A2');
  if (v('shelter') === 'none') R.push('A3');
  if (v('company') === 'alone' && r.age >= 70) R.push('A4');
  if (v('medicine') === 'low' || v('medicine') === 'out') R.push('A5');
  if (v('help') === 'wants') R.push('A6');
  if (v('exposure') === 'must' && (r.age >= 65 || r.conds.length > 0)) R.push('A7');
  if (r.call && r.call.status === 'noanswer') R.push('A8');
  if (r.call && r.call.status === 'reached' && r.cutShort) R.push('A12');
  return {
    reasons: R,
    tier: R.some(x => x[0] === 'R') ? 'red' : R.length ? 'amber' : 'green'
  };
}

export function retriage(r: Resident) {
  const t = triage(r);
  r.reasons = t.reasons;
  r.tier = t.tier;
}

export function transcriptFor(r: Resident): [string, string, string | null][] {
  const t: [string, string, string | null][] = [
    [
      'agent',
      'नमस्ते। मैं ज़िला स्वास्थ्य कार्यालय की ओर से एक स्वचालित सहायक बोल रही हूँ। क्या मैं ' +
        r.hi +
        ' जी से बात कर रही हूँ?',
      null
    ],
    [
      'res',
      r.respondent === 'family'
        ? 'नहीं, मैं उनकी बहू बोल रही हूँ। वे सो रही हैं।'
        : ('हाँ, बोल रहा हूँ।'.replace('रहा', r.sex === 'f' ? 'रही' : 'रहा')),
      null
    ]
  ];
  for (const q of Q) {
    if (!r.answers || !r.answers[q]) continue;
    t.push(['agent', ASK[q][0], null]);
    t.push(['res', r.answers[q].quote, q]);
    if (q === 'feeling' && r.emergency) {
      t.push([
        'agent',
        'यह गंभीर हो सकता है। अभी 108 पर फ़ोन करें, या पास में किसी से कहें। मैं स्वास्थ्य कार्यकर्ता को अभी सूचना दे रही हूँ।',
        null
      ]);
      return t;
    }
  }
  t.push([
    'agent',
    'धन्यवाद। एक स्वास्थ्य कार्यकर्ता आपसे मिलने आ सकती हैं या फ़ोन कर सकती हैं। अपना ध्यान रखिए।',
    null
  ]);
  return t;
}

export function noteFor(r: Resident): string {
  const bits = r.reasons.filter(x => RULE_FROM[x]).map(x => RULES[x][0].toLowerCase());
  if (!bits.length) return 'No concerns raised on the call.';
  return (
    'Reported: ' +
    bits.join('; ') +
    '.' +
    (r.alone ? ' Lives alone.' : '') +
    (r.conds.length ? ' Has a ' + r.conds.map(c => COND[c][0]).join(' and a ') + '.' : '')
  );
}

export function needsOf(r: Resident): string[] {
  const a = r.answers || {};
  const v = (k: string) => a[k] && a[k].v;
  const n: string[] = [];
  if (v('fluids') === 'low' || v('fluids') === 'none') n.push('ors');
  if (v('medicine') === 'low' || v('medicine') === 'out') n.push('med');
  if (v('shelter') === 'none') n.push('cool');
  return n;
}

// Global data stores
export const WARDS: Ward[] = [];
export const RESIDENTS: Resident[] = [];

// Name datasets
const F: [string, string][] = [
  ['Bhanwari', 'भंवरी'],
  ['Sharda', 'शारदा'],
  ['Rukma', 'रुकमा'],
  ['Santosh', 'संतोष'],
  ['Laxmi', 'लक्ष्मी'],
  ['Jamna', 'जमना'],
  ['Kesar', 'केसर'],
  ['Gulab', 'गुलाब'],
  ['Sita', 'सीता'],
  ['Parvati', 'पार्वती'],
  ['Dhapu', 'धापू'],
  ['Mohini', 'मोहिनी'],
  ['Chanda', 'चंदा'],
  ['Suraj', 'सूरज']
];
const FS: [string, string][] = [
  ['Devi', 'देवी'],
  ['Kanwar', 'कंवर'],
  ['Bai', 'बाई']
];
const M: [string, string][] = [
  ['Hanuman', 'हनुमान'],
  ['Mohan', 'मोहन'],
  ['Gopal', 'गोपाल'],
  ['Prem', 'प्रेम'],
  ['Dhanna', 'धन्ना'],
  ['Birbal', 'बीरबल'],
  ['Teja', 'तेजा'],
  ['Ramesh', 'रमेश'],
  ['Bhagirath', 'भागीरथ'],
  ['Om', 'ओम'],
  ['Sukha', 'सुखा'],
  ['Kishan', 'किशन'],
  ['Mangi', 'मांगी'],
  ['Rupa', 'रूपा']
];
const MS: [string, string][] = [
  ['Ram', 'राम'],
  ['Lal', 'लाल'],
  ['Singh', 'सिंह']
];

// Initialize the 4 districts and 120 residents
function buildDatabase() {
  if (WARDS.length > 0) return;

  DISTRICTS.forEach((d, di) => {
    const rnd = rng(7919 * (di + 3));
    (['A', 'B', 'C'] as const).forEach((letter, wi) => {
      const w: Ward = {
        id: `${d.id}-${letter.toLowerCase()}`,
        name: `Ward ${letter}`,
        hi: `वार्ड ${letter}`,
        district: d.id,
        worker: `${d.id}-w${wi}`,
        workerName: d.workers[wi][0],
        workerHi: d.workers[wi][1],
        centre: `Ward ${letter} Primary Health Centre`,
        centreHi: `वार्ड ${letter} प्राथमिक स्वास्थ्य केंद्र`,
        lat: +(d.lat + (wi - 1) * 0.012).toFixed(5),
        lng: +(d.lng + (wi - 1) * 0.015).toFixed(5)
      };
      WARDS.push(w);

      for (let i = 0; i < 10; i++) {
        const isF = rnd() < 0.5;
        const firstArr = isF ? F : M;
        const surArr = isF ? FS : MS;
        const n = firstArr[Math.floor(rnd() * firstArr.length)];
        const sn = surArr[Math.floor(rnd() * surArr.length)];
        const age = 42 + Math.floor(Math.pow(rnd(), 0.7) * 46);
        const conds = Object.keys(COND).filter(() => rnd() < 0.17);

        const r: Resident = {
          id: `${w.id}-${i + 1}`,
          name: `${n[0]} ${sn[0]}`,
          hi: `${n[1]} ${sn[1]}`,
          sex: isF ? 'f' : 'm',
          age,
          alone: rnd() < 0.36,
          outdoor: age < 68 && rnd() < 0.25,
          conds,
          ward: w.id,
          district: d.id,
          lat: +(w.lat + (rnd() - 0.5) * 0.01).toFixed(5),
          lng: +(w.lng + (rnd() - 0.5) * 0.01).toFixed(5),
          contact: rnd() < 0.5 ? ['son', 'बेटे'] : rnd() < 0.5 ? ['daughter', 'बेटी'] : ['neighbour', 'पड़ोसी'],
          worker: w.worker,
          risk: 0,
          call: null,
          answers: null,
          tier: null,
          reasons: [],
          visit: null,
          esc: null,
          raised: null,
          respondent: 'self'
        };
        r.risk = riskOf(r);
        RESIDENTS.push(r);
      }
    });
  });

  // Churu round simulation (order of risk, realistic heatwave triage)
  const churuRnd = rng(399);
  const churu = RESIDENTS.filter(r => r.district === 'churu').sort(
    (a, b) => b.risk - a.risk || b.age - a.age
  );

  churu.forEach((r, i) => {
    r.order = i + 1;
    const at = 2 + i * 4 + Math.floor(churuRnd() * 3);
    const roll = churuRnd();

    if (roll < 0.14) {
      r.call = { status: 'noanswer', attempts: 3, at };
      r.opened = at + 80;
      retriage(r);
      return;
    }
    if (roll < 0.22) {
      r.call = { status: 'trying', attempts: 2, at };
      return;
    }

    r.call = { status: 'reached', attempts: 1, at };
    r.opened = at + 3;

    const pick = (opts: [string, number][]): string => {
      let x = churuRnd();
      for (const [v, p] of opts) {
        if ((x -= p) < 0) return v;
      }
      return opts[opts.length - 1][0];
    };

    const ans: Record<string, string> = {
      feeling: pick([['severe', 0.02 + r.risk * 0.008], ['mild', 0.24], ['ok', 1]]),
      fluids: pick([['none', 0.05], ['low', 0.24], ['enough', 1]]),
      company: r.alone ? 'alone' : 'with',
      shelter: pick([['none', 0.08], ['partial', 0.16], ['adequate', 1]]),
      exposure: r.outdoor
        ? pick([['must', 0.8], ['staying', 1]])
        : pick([['must', 0.08], ['staying', 1]]),
      help: pick([['urgent', 0.02], ['wants', 0.15], ['none', 1]])
    };

    if (r.conds.length > 0) {
      ans.medicine = pick([['out', 0.08], ['low', 0.22], ['has', 1]]);
    }

    r.emergency = ans.feeling === 'severe' && churuRnd() < 0.5;
    r.answers = {};

    for (const q of Q) {
      if (ans[q] === undefined) continue;
      r.answers[q] = { v: ans[q], quote: ASK[q][1][ans[q]] };
      if (q === 'feeling' && r.emergency) {
        r.cutShort = true;
        break;
      }
    }

    retriage(r);
    r.transcript = transcriptFor(r);
    r.note = noteFor(r);

    if (r.tier !== 'green' && churuRnd() < (r.tier === 'red' ? 0.5 : 0.4)) {
      const vt = r.opened + 30 + Math.floor(churuRnd() * 70);
      if (vt <= NOW - 5) {
        r.visit = {
          outcome: r.tier === 'red' ? 'hospital' : OUTCOMES[Math.floor(churuRnd() * 3)][0],
          at: vt,
          note: ''
        };
      }
    }
  });

  const openCases = churu.filter(r => r.tier === 'amber' && !r.visit && r.call?.status === 'reached');
  if (openCases[1]) {
    openCases[1].esc = {
      reason: 'missing',
      note: 'Door locked, neighbor said went towards market early morning',
      at: NOW - 25,
      ack: false,
      reply: ''
    };
  }
}

// Build once
buildDatabase();

// Queries & Scope
export interface Scope {
  district?: string;
  ward?: string;
  worker?: string;
}

export function getDistrict(id: string): District | undefined {
  return DISTRICTS.find(d => d.id === id);
}

export function getWard(id: string): Ward | undefined {
  return WARDS.find(w => w.id === id);
}

export function getResident(id: string): Resident | undefined {
  return RESIDENTS.find(r => r.id === id);
}

export function inScope(sc: Scope = {}): Resident[] {
  return RESIDENTS.filter(
    r =>
      (!sc.district || r.district === sc.district) &&
      (!sc.ward || r.ward === sc.ward) &&
      (!sc.worker || r.worker === sc.worker)
  );
}

export const needsVisit = (r: Resident) => r.tier === 'red' || r.tier === 'amber';
export const isOpen = (r: Resident) => needsVisit(r) && !r.visit;

export const callLabel = (r: Resident) =>
  !r.call
    ? 'Not called'
    : r.call.status === 'reached'
    ? 'Reached'
    : r.call.status === 'noanswer'
    ? 'No answer ×3'
    : 'Still trying';

export const ageBand = (r: Resident) =>
  r.age >= 75
    ? '75 and over'
    : r.age >= 65
    ? '65 to 74'
    : r.age >= 55
    ? '55 to 64'
    : 'Under 55';

export interface ResidentFilter {
  tier?: string;
  call?: string;
  reason?: string;
  open?: boolean | number;
  age?: string;
  cond?: string;
  alone?: boolean | number;
  risk?: boolean | number;
  called?: boolean | number;
  visited?: boolean | number;
}

export function applyFilter(list: Resident[], f: ResidentFilter = {}): Resident[] {
  return list.filter(
    r =>
      (!f.tier || r.tier === f.tier) &&
      (!f.call || callLabel(r) === f.call) &&
      (!f.reason || r.reasons.includes(f.reason)) &&
      (!f.open || isOpen(r)) &&
      (!f.age || ageBand(r) === f.age) &&
      (!f.cond || r.conds.includes(f.cond)) &&
      (!f.alone || r.alone) &&
      (!f.risk || r.risk >= 5) &&
      (!f.called || !!r.call) &&
      (!f.visited || !!r.visit)
  );
}

export function median(a: number[]): number | null {
  if (!a.length) return null;
  const s = a.slice().sort((x, y) => x - y);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : Math.round((s[m - 1] + s[m]) / 2);
}

export function clock(m: number): string {
  const t = 480 + m;
  return `${String(Math.floor(t / 60)).padStart(2, '0')}:${String(t % 60).padStart(2, '0')}`;
}

export function dur(m: number, hi?: boolean): string {
  m = Math.max(0, Math.round(m));
  const h = Math.floor(m / 60);
  const r = m % 60;
  return hi
    ? (h ? `${h} ${h === 1 ? 'घंटा' : 'घंटे'} ` : '') + `${r} मिनट`
    : (h ? `${h} h ` : '') + `${r} min`;
}

export const waiting = (r: Resident) => NOW - (r.opened || 0);

export function stats(sc: Scope = {}) {
  const all = inScope(sc);
  const called = all.filter(r => r.call);
  const cases = all.filter(r => r.tier);
  const tiers = {
    red: cases.filter(r => r.tier === 'red').length,
    amber: cases.filter(r => r.tier === 'amber').length,
    green: cases.filter(r => r.tier === 'green').length
  };
  return {
    all,
    called,
    cases,
    reached: called.filter(r => r.call?.status === 'reached').length,
    noanswer: called.filter(r => r.call?.status === 'noanswer').length,
    trying: called.filter(r => r.call?.status === 'trying').length,
    tiers,
    need: cases.filter(needsVisit).length,
    visited: cases.filter(r => needsVisit(r) && r.visit).length,
    open: cases.filter(isOpen).length,
    redOpen: cases.filter(r => r.tier === 'red' && !r.visit).length,
    high: all.filter(r => r.risk >= 5).length,
    alone: all.filter(r => r.alone).length,
    old: all.filter(r => r.age >= 75).length,
    medianVisit: median(
      cases.filter(r => r.visit && needsVisit(r)).map(r => r.visit!.at - (r.opened || 0))
    )
  };
}

export function progress(sc: Scope = {}) {
  const all = inScope(sc);
  const xs: number[] = [];
  const reached: number[] = [];
  const opened: number[] = [];
  const visited: number[] = [];
  for (let t = 0; t <= NOW; t += 10) {
    xs.push(t);
    reached.push(
      all.filter(r => r.call && r.call.status === 'reached' && r.call.at + 3 <= t).length
    );
    opened.push(all.filter(r => r.tier && (r.opened || 0) <= t).length);
    visited.push(all.filter(r => r.visit && r.visit.at <= t).length);
  }
  return { xs, reached, opened, visited };
}

// Interactive Mutators (updating memory state and returning updated resident)
export function recordVisit(id: string, outcome: string, note = ''): Resident | undefined {
  const r = getResident(id);
  if (!r) return undefined;
  r.visit = { outcome, at: NOW, note };
  return r;
}

export function raiseToRed(id: string, obs: string): Resident | undefined {
  const r = getResident(id);
  if (!r) return undefined;
  r.raised = { obs, at: NOW };
  retriage(r);
  return r;
}

export function tellOfficer(id: string, reason: string, note = ''): Resident | undefined {
  const r = getResident(id);
  if (!r) return undefined;
  r.esc = { reason, note, at: NOW, ack: false, reply: '' };
  return r;
}

export function ackEscalation(id: string, reply = ''): Resident | undefined {
  const r = getResident(id);
  if (!r || !r.esc) return undefined;
  r.esc.ack = true;
  r.esc.reply = reply;
  return r;
}

export function reassignWorker(id: string, workerId: string): Resident | undefined {
  const r = getResident(id);
  if (!r) return undefined;
  r.worker = workerId;
  return r;
}

// Test agent persona cards
export interface PersonaCard {
  id: string;
  name: string;
  hi: string;
  sex: 'f' | 'm';
  age: number;
  alone: boolean;
  outdoor: boolean;
  conds: string[];
  ward: string;
  family?: boolean;
  emergency?: boolean;
  profile: string;
  nudge: string;
  a: Record<string, string>;
}

export const TEST_PERSONAS: PersonaCard[] = [
  {
    id: 'alone_dizzy',
    name: 'Kamla Devi',
    hi: 'कमला देवी',
    sex: 'f',
    age: 72,
    alone: true,
    outdoor: false,
    conds: ['diabetes'],
    ward: 'churu-a',
    profile: 'Lives alone. Has diabetes.',
    nudge: 'You feel a little dizzy and have had very little water today.',
    a: {
      feeling: 'mild',
      fluids: 'low',
      company: 'alone',
      shelter: 'adequate',
      exposure: 'staying',
      medicine: 'has',
      help: 'none'
    }
  },
  {
    id: 'must_work',
    name: 'Ramesh Kumar',
    hi: 'रमेश कुमार',
    sex: 'm',
    age: 58,
    alone: false,
    outdoor: true,
    conds: ['heart'],
    ward: 'churu-b',
    profile: 'Construction labourer. Heart condition.',
    nudge: 'You feel fine, but you have to work outside today.',
    a: {
      feeling: 'ok',
      fluids: 'enough',
      company: 'with',
      shelter: 'adequate',
      exposure: 'must',
      medicine: 'has',
      help: 'none'
    }
  },
  {
    id: 'family',
    name: 'Gulab Devi',
    hi: 'गुलाब देवी',
    sex: 'f',
    age: 80,
    alone: false,
    outdoor: false,
    conds: [],
    ward: 'churu-c',
    family: true,
    profile: 'You are her daughter-in-law and picked up the phone.',
    nudge: 'She is asleep. You are not sure how she is.',
    a: {
      feeling: 'mild',
      company: 'with',
      help: 'wants'
    }
  },
  {
    id: 'emergency',
    name: 'Mohan Singh',
    hi: 'मोहन सिंह',
    sex: 'm',
    age: 68,
    alone: true,
    outdoor: false,
    conds: [],
    ward: 'churu-a',
    emergency: true,
    profile: 'Lives alone.',
    nudge: 'You have chest pain and feel confused.',
    a: {
      feeling: 'severe'
    }
  }
];

export function makeTestResident(card: PersonaCard): Resident {
  const w = getWard(card.ward)!;
  const id = `test-${Date.now()}`;
  const r: Resident = {
    id,
    name: card.name,
    hi: card.hi,
    sex: card.sex,
    age: card.age,
    alone: card.alone,
    outdoor: card.outdoor,
    conds: card.conds.slice(),
    ward: w.id,
    district: w.district,
    lat: w.lat,
    lng: w.lng,
    contact: ['son', 'बेटे'],
    worker: w.worker,
    risk: 0,
    call: { status: 'reached', attempts: 1, at: NOW },
    answers: {},
    tier: null,
    reasons: [],
    visit: null,
    esc: null,
    raised: null,
    respondent: card.family ? 'family' : 'self',
    emergency: !!card.emergency,
    cutShort: !!card.emergency || !!card.family,
    test: true,
    opened: NOW
  };
  r.risk = riskOf(r);

  const fullAnswers: Record<string, AnswerItem> = {};
  for (const q of Q) {
    if (card.a[q]) {
      fullAnswers[q] = {
        v: card.a[q],
        quote:
          card.family && q === 'feeling'
            ? 'पता नहीं, सुबह से सो रही हैं।'
            : ASK[q][1][card.a[q]]
      };
    }
  }

  // Pre-seed full answers and script
  r.answers = fullAnswers;
  retriage(r);
  r.transcript = transcriptFor(r);
  r.note = noteFor(r);
  RESIDENTS.push(r);
  return r;
}
