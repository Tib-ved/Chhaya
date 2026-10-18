import { useSyncExternalStore } from 'react';
import { time, words } from '../operations';

export type UiLanguage = 'hi' | 'en';
const languageEvent = 'chhaya_lang_changed';
function subscribe(listener: () => void) {
  window.addEventListener(languageEvent, listener);
  window.addEventListener('storage', listener);
  return () => { window.removeEventListener(languageEvent, listener); window.removeEventListener('storage', listener); };
}
export function useUiLanguage(fallback: UiLanguage): UiLanguage {
  return useSyncExternalStore(subscribe, () => {
    const saved = localStorage.getItem('chhaya_lang');
    return saved === 'hi' || saved === 'en' ? saved : fallback;
  }, () => fallback);
}
export function setUiLanguage(language: UiLanguage) {
  localStorage.setItem('chhaya_lang', language);
  window.dispatchEvent(new Event(languageEvent));
}
export const text = (language: UiLanguage, english: string, hindi: string) => language === 'hi' ? hindi : english;

export const hindiReasons: Record<string, string> = {
  R1: 'कॉल के दौरान आपात स्थिति बताई गई',
  R2: 'गंभीर लक्षण बताए',
  R3: 'तुरंत मदद माँगी',
  R4: 'पानी या खाना नहीं है और अकेले हैं',
  A1: 'हल्के लक्षण बताए या तबीयत की जानकारी स्पष्ट नहीं है',
  A2: 'पानी या खाना कम है',
  A3: 'पंखा, ठंडी जगह या गर्म रहने का इंतज़ाम नहीं है',
  A4: 'अकेले रहते हैं और उम्र 70 साल या उससे ज़्यादा है',
  A5: 'रोज़ की दवाइयाँ कम बची हैं या खत्म हो गई हैं',
  A6: 'मिलने के लिए कहा',
  A7: 'उम्र या स्वास्थ्य संबंधी परेशानी के बावजूद बाहर जाना ज़रूरी है',
  A8: 'तीन कोशिशों के बाद भी फ़ोन नहीं उठा',
  A9: 'तबीयत का हाल जानने से पहले कॉल खत्म हो गई',
  A10: 'किसी और ने फ़ोन उठाया; तबीयत की जानकारी नहीं मिली',
  A11: 'एक जवाब समझ में नहीं आया',
  A12: 'सभी सवाल पूछने से पहले कॉल खत्म हो गई',
  G1: 'कोई परेशानी नहीं बताई गई',
};
export const reasonLabel = (id: string, english: string, language: UiLanguage) => language === 'hi' ? hindiReasons[id] || english : english;

const placeNames: Record<string, string> = { churu: 'चूरू', rajasthan: 'राजस्थान', telangana: 'तेलंगाना', 'madhya pradesh': 'मध्य प्रदेश', maharashtra: 'महाराष्ट्र' };
export function areaName(name: string, language: UiLanguage): string {
  return language === 'hi' ? placeNames[name.toLowerCase()] || name.replace(/^Ward\s+/i, 'वार्ड ') : name;
}
export function workerName(name: string, language: UiLanguage): string {
  return language === 'hi' ? name.replace(/^ASHA worker Ward ([A-Z])$/, 'आशा कार्यकर्ता · वार्ड $1') : name;
}
export function facilityName(name: string, language: UiLanguage): string {
  if (language === 'en') return name;
  return name.replace(/^Ward ([A-Z]) health centre$/, 'वार्ड $1 स्वास्थ्य केंद्र').replace(/^Ward ([A-Z]) cooling centre$/, 'वार्ड $1 शीतल केंद्र');
}
export function localTime(value: string | null | undefined, language: UiLanguage): string {
  if (language === 'en') return time(value);
  return value ? new Intl.DateTimeFormat('hi-IN', { timeZone: 'Asia/Kolkata', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value)) + ' IST' : 'दर्ज नहीं';
}
const statuses: Record<string, string> = {
  red: 'लाल', amber: 'पीला', green: 'हरा', open: 'मुलाकात बाकी', visited: 'मुलाकात दर्ज', resolved: 'मामला बंद',
  assigned: 'कार्य सौंपा गया', acknowledged: 'कार्य स्वीकार किया', arrived: 'पहुंच गए', completed: 'पूरा हुआ', escalated: 'अधिकारी की मदद चाहिए',
  queued: 'कॉल बाकी है', dialing: 'फ़ोन मिलाया जा रहा है', in_progress: 'कॉल चल रही है', no_answer: 'फ़ोन नहीं उठा', busy: 'लाइन व्यस्त', failed: 'पूरा नहीं हो पाया', dropped: 'कॉल कट गई',
  pending: 'बाकी', rejected: 'मंज़ूरी नहीं मिली', executing: 'काम चल रहा है', simulated: 'डेमो', submitted: 'भेजा गया', sent: 'भेजा गया', delivered: 'पहुंच गया', undelivered: 'नहीं पहुंचा', needs_review: 'जाँच की ज़रूरत है',
  admin: 'अधिकारी', crew: 'कार्यकर्ता', voice_agent: 'छाया', agent: 'छाया', resident: 'निवासी', system: 'सिस्टम', twilio: 'Twilio',
};
export const statusLabel = (status: string, language: UiLanguage) => language === 'hi' ? statuses[status] || words(status) : words(status);
const events: Record<string, string> = {
  call_queued: 'कॉल सूची में जोड़ी गई', call_started: 'कॉल शुरू हुई', call_ended: 'कॉल खत्म हुई',
  action_proposed: 'कार्य का प्रस्ताव तैयार', action_approved: 'कार्य की मंज़ूरी मिली', action_completed: 'कार्य पूरा हुआ', action_simulated: 'डेमो कार्य पूरा हुआ', action_submitted: 'कार्य भेजा गया', action_rejected: 'प्रस्ताव नामंज़ूर', action_needs_review: 'कार्य की जाँच ज़रूरी',
  visit_acknowledged: 'मुलाकात का कार्य स्वीकार किया', visit_arrived: 'निवासी के पास पहुंचे', visit_completed: 'मुलाकात दर्ज की गई', visit_escalated: 'अधिकारी से मदद माँगी',
};
export function eventLabel(event: string, language: UiLanguage): string {
  if (language === 'en') return words(event);
  return events[event] || (event.startsWith('provider_') ? `फ़ोन सेवा का अपडेट: ${statusLabel(event.slice(9), language)}` : words(event));
}
export const languageName = (code: string, language: UiLanguage) => (language === 'hi'
  ? ({ hi: 'हिन्दी', en: 'अंग्रेज़ी', mr: 'मराठी', gu: 'गुजराती', mwr: 'मारवाड़ी' } as Record<string, string>)
  : ({ hi: 'Hindi', en: 'English', mr: 'Marathi', gu: 'Gujarati', mwr: 'Marwari' } as Record<string, string>))[code] || code;

const nameParts: Record<string, string> = {
  anil: 'अनिल', ashok: 'अशोक', balram: 'बलराम', dinesh: 'दिनेश', ganesh: 'गणेश', geeta: 'गीता', hari: 'हरि', indu: 'इंदु', jagdish: 'जगदीश',
  kamla: 'कमला', kishor: 'किशोर', kusum: 'कुसुम', lata: 'लता', mahesh: 'महेश', meena: 'मीना', mohan: 'मोहन', narayan: 'नारायण', nirmala: 'निर्मला',
  prakash: 'प्रकाश', pushpa: 'पुष्पा', ramesh: 'रमेश', rekha: 'रेखा', sarla: 'सरला', savitri: 'सावित्री', shanta: 'शांता', sunita: 'सुनीता',
  suresh: 'सुरेश', usha: 'उषा', vijay: 'विजय', vimal: 'विमल', gulab: 'गुलाब', devi: 'देवी', kumar: 'कुमार', singh: 'सिंह',
  khan: 'खान', rathore: 'राठौड़', prajapat: 'प्रजापत', shekhawat: 'शेखावत', soni: 'सोनी', jangir: 'जांगिड़', saini: 'सैनी',
  choudhary: 'चौधरी', sharma: 'शर्मा', meghwal: 'मेघवाल',
};
// Shows a resident's name in Devanagari when every part of it is known; otherwise keeps it as recorded.
export function personName(name: string, language: UiLanguage): string {
  if (language !== 'hi') return name;
  const parts = name.split(/\s+/).map(part => nameParts[part.toLowerCase()]);
  return parts.every(Boolean) ? parts.join(' ') : name;
}

const demoNotes: [RegExp, string][] = [
  [/^Reported chest pain and trouble breathing; emergency flagged\.\nConfirm an ambulance was called and who is with him\.$/, 'सीने में दर्द और साँस लेने में तकलीफ़ बताई; आपात स्थिति दर्ज की गई।\nपक्का करें कि एम्बुलेंस बुलाई गई है और उनके साथ कौन है।'],
  [/^Says she feels a little dizzy and has had very little water\. Lives alone\.\nCheck fluids and whether she can keep cool today\.$/, 'हल्का चक्कर आ रहा है और बहुत कम पानी पिया है। अकेली रहती हैं।\nपानी की स्थिति देखें और पूछें कि आज ठंडी जगह रह पाएँगी या नहीं।'],
  [/^Reports vomiting again and again and cannot stand up\. Lives alone\.\nNeeds to be seen urgently; check whether anyone can stay with her\.$/, 'बार-बार उल्टी हो रही है और खड़ी नहीं हो पा रहीं। अकेली रहती हैं।\nतुरंत मिलना ज़रूरी है; देखें कि कोई उनके साथ रह सकता है या नहीं।'],
  [/^Has had no water since morning and there is none at home\. Lives alone\.\nBring drinking water; check how he is managing\.$/, 'सुबह से पानी नहीं पिया और घर में पानी नहीं है। अकेले रहते हैं।\nपीने का पानी साथ ले जाएँ; देखें कि वे कैसे हैं।'],
  [/^A family member answered: he is asleep and they could not say how he is\.\nSee him in person and ask how he has been since morning\.$/, 'परिवार के सदस्य ने फ़ोन उठाया: वे सो रहे हैं और उनकी तबीयत के बारे में नहीं बता सके।\nखुद मिलें और पूछें कि सुबह से कैसे हैं।'],
  [/^Says he is managing and has what he needs\. Lives alone, aged (\d+)\.\nLook in once today while the warning lasts\.$/, 'कहते हैं कि ठीक हैं और ज़रूरत की चीज़ें हैं। अकेले रहते हैं, उम्र $1 साल।\nचेतावनी रहने तक आज एक बार मिल लें।'],
  [/^Reports a headache and weakness\.\nCheck how she is now and whether she is drinking water\.$/, 'सिर दर्द और कमज़ोरी बताई।\nदेखें कि अब कैसी हैं और पानी पी रही हैं या नहीं।'],
  [/^No electricity and the fan is not working\. Lives alone\.\nCheck for a cooler place to stay; the cooling centre is nearby\.$/, 'बिजली नहीं है और पंखा नहीं चल रहा। अकेले रहते हैं।\nठंडी जगह का इंतज़ाम देखें; शीतल केंद्र पास में है।'],
  [/^Asked for someone to come by today\. Lives alone\.\nAsk what she needs\.$/, 'आज किसी के मिलने आने के लिए कहा। अकेली रहती हैं।\nपूछें कि उन्हें क्या चाहिए।'],
  [/^Her diabetes medicine has run out\. Lives alone\.\nCheck what she takes and help arrange a supply\.$/, 'शुगर की दवा खत्म हो गई है। अकेली रहती हैं।\nदेखें कि कौन-सी दवा लेती हैं और दवा का इंतज़ाम करवाएँ।'],
  [/^Says he feels a little weak\.\nCheck how he is now and whether he is drinking water\.$/, 'थोड़ी कमज़ोरी बताई।\nदेखें कि अब कैसे हैं और पानी पी रहे हैं या नहीं।'],
  [/^The call ended early, after two questions; she had said she had water\.\nFinish the check in person\.$/, 'दो सवालों के बाद कॉल कट गई; उन्होंने बताया था कि पानी है।\nबाकी जानकारी खुद मिलकर लें।'],
  [/^Water at home is running short\. Lives alone\.\nCheck his water supply for the next days\.$/, 'घर में पानी कम पड़ रहा है। अकेले रहते हैं।\nअगले कुछ दिनों के लिए पानी का इंतज़ाम देखें।'],
  [/^Has to go out to work today despite a breathing condition\.\nAdvise shade and water; ask about the hours she works outside\.$/, 'साँस की तकलीफ़ के बावजूद आज काम पर बाहर जाना है।\nछाँव और पानी की सलाह दें; पूछें कि कितने घंटे बाहर काम करती हैं।'],
];
// Demo call summaries have a Hindi version. A summary written during a live call is shown as recorded.
export function noteText(note: string, language: UiLanguage): { text: string; translated: boolean } {
  if (language !== 'hi' || !note) return { text: note, translated: false };
  for (const [pattern, hindi] of demoNotes) if (pattern.test(note)) return { text: note.replace(pattern, hindi), translated: true };
  return { text: note, translated: false };
}
const questionNames: Record<string, string> = { feeling: 'तबीयत', fluids_food: 'पानी और खाना', company: 'साथ में कोई है', shelter: 'मौसम से बचाव', exposure: 'बाहर जाना', medicine: 'दवाइयाँ', help: 'मदद की ज़रूरत' };
export function questionLabel(id: string, label: string, language: UiLanguage): string {
  if (language === 'en') return label;
  if (id === 'fluids_food') return label === 'Water' ? 'पानी' : label === 'Food' ? 'खाना' : questionNames[id];
  return questionNames[id] || label;
}
const answerNames: Record<string, Record<string, string>> = {
  feeling: { ok: 'कोई लक्षण नहीं बताए', mild_symptoms: 'हल्के लक्षण', severe_symptoms: 'गंभीर लक्षण', unclear: 'स्पष्ट नहीं' },
  fluids_food: { enough: 'पर्याप्त', low: 'कम', none: 'बिल्कुल नहीं' },
  company: { with_someone: 'कोई साथ है', alone: 'अकेले हैं' },
  shelter: { adequate: 'पर्याप्त इंतज़ाम', partial: 'कुछ इंतज़ाम', none: 'इंतज़ाम नहीं' },
  exposure: { staying_in: 'घर पर रहेंगे', must_go_out: 'बाहर जाना ज़रूरी है' },
  medicine: { has: 'दवाइयाँ हैं', running_low: 'कम बची हैं', out: 'खत्म हो गई हैं' },
  help: { none: 'मदद नहीं माँगी', wants_visit: 'मिलने के लिए कहा', urgent: 'तुरंत मदद चाहिए' },
};
export function answerLabel(id: string, value: string | undefined, fallback: string | null, language: UiLanguage): string {
  if (language === 'en') return fallback || 'Not recorded';
  return (value && answerNames[id]?.[value]) || fallback || 'दर्ज नहीं';
}
