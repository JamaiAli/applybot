// ApplyPilot — Script d'auto-complétion intelligente 1-Clic
// Injecté dans les formulaires de candidature pour remplir sans blocage Cloudflare.

(function () {
  if (window.__applypilot_injected) return;
  window.__applypilot_injected = true;

  const API_BASE = 'http://127.0.0.1:8765';

  // -------------------------------------------------------------------------
  // Détection des pages d'offres et formulaires
  // -------------------------------------------------------------------------
  function isJobOrApplyPage() {
    const url = location.href.toLowerCase();
    const commonPatterns = [
      'indeed.', 'hellowork.', 'workday.', 'myworkdayjobs.', 'greenhouse.io',
      'lever.co', 'taleo.net', 'smartrecruiters.', 'welcometothejungle.',
      'linkedin.com/jobs', 'ashbyhq.com', 'talent-soft.', 'flatchr.',
      'breezy.hr', 'jobvite.', 'recruitee.', 'cvmkr.', 'successfactors.',
      'icims.', 'brassring.', 'cornerstoneondemand.', 'teamtailor.'
    ];
    if (commonPatterns.some(p => url.includes(p))) return true;

    // Détection par la présence de champs d'un formulaire de candidature
    const hasInputs = deepQueryAll('input:not([type="hidden"]), textarea, select').length >= 3;
    const hasApplyKeywords = /candidater|postuler|apply|submit|curriculum|téléverser|déposer mon cv/i.test(document.body.innerText || '');
    return hasInputs && hasApplyKeywords;
  }

  // -------------------------------------------------------------------------
  // Setter React / Vue / Angular compatible (Bypass de réinitialisation)
  // -------------------------------------------------------------------------
  function setNativeValue(el, val) {
    if (!el || val === undefined || val === null) return false;
    try {
      el.focus();

      // Gestion des champs contenteditable (riches éditeurs Workday / Taleo)
      if (el.isContentEditable) {
        el.innerText = val;
        el.dispatchEvent(new Event('input', { bubbles: true, cancelable: true }));
        highlightField(el);
        return true;
      }

      const proto = el instanceof HTMLTextAreaElement ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
      const desc = Object.getOwnPropertyDescriptor(proto, 'value');
      if (desc && desc.set) {
        desc.set.call(el, val);
      } else {
        el.value = val;
      }

      // Bypass React 16+ ValueTracker : React surveille les mutations de .value
      // Si _valueTracker n'est pas réinitialisé, React ignore l'événement input et vide le champ !
      const tracker = el._valueTracker;
      if (tracker) {
        tracker.setValue('');
      }

      // Simulation d'une frappe humaine pour React, Vue 2/3, Angular
      // composed: true — l'événement doit sortir du shadow root pour que le
      // composant (SmartRecruiters…) enregistre la valeur.
      try {
        el.dispatchEvent(new InputEvent('input', { bubbles: true, cancelable: true, composed: true, inputType: 'insertText', data: val }));
      } catch (_e) {
        el.dispatchEvent(new Event('input', { bubbles: true, cancelable: true, composed: true }));
      }
      el.dispatchEvent(new Event('change', { bubbles: true, cancelable: true, composed: true }));
      el.dispatchEvent(new FocusEvent('blur', { bubbles: true, cancelable: true, composed: true }));

      // Surlignage vert discret
      highlightField(el);
      return true;
    } catch (e) {
      console.warn('[ApplyPilot] Erreur écriture champ:', e);
      return false;
    }
  }

  // -------------------------------------------------------------------------
  // Formulaires en Web Components (SmartRecruiters, Workday récent…) : les champs
  // vivent dans des « shadow roots » que document.querySelectorAll ne traverse pas.
  // -------------------------------------------------------------------------
  function deepQueryAll(selector, root = document, out = []) {
    try {
      root.querySelectorAll(selector).forEach(el => out.push(el));
      root.querySelectorAll('*').forEach(el => {
        if (el.shadowRoot) deepQueryAll(selector, el.shadowRoot, out);
      });
    } catch (_e) {}
    return out;
  }

  // Parent dans l'arbre « composé » : traverse les frontières de shadow root.
  function composedParent(node) {
    if (!node) return null;
    if (node.assignedSlot) return node.assignedSlot;
    if (node.parentElement) return node.parentElement;
    const root = node.getRootNode && node.getRootNode();
    return root && root.host ? root.host : null;
  }

  function cleanQuestion(text) {
    return String(text || '').replace(/[\r\n\t]+/g, ' ').replace(/\s+/g, ' ')
      .replace(/\s*\*\s*$/, '').replace(/\b\d+\s*\/\s*\d+\s*$/, '').trim();
  }

  // Texte de la question posée au-dessus d'un champ libre, sans le bruit des
  // champs voisins. Renvoie '' si rien ne ressemble à un libellé.
  function getQuestionText(el) {
    // Le libellé LE PLUS PROCHE du champ fait foi : on s'arrête au premier niveau
    // qui en fournit un, pour ne pas attribuer à « Nom » la question d'un bloc voisin.
    // placeholder / title sont des indices faibles : on continue à chercher mieux.
    const candidates = [];
    const weak = [];
    const add = (list, t) => {
      const c = cleanQuestion(t);
      if (c.length > 2 && c.length < 400 && !list.includes(c)) list.push(c);
    };
    const push = (t) => add(candidates, t);
    let node = el;
    for (let depth = 0; depth < 10 && node && node !== document.body; depth++) {
      if (node.getAttribute) {
        ['aria-label', 'label'].forEach(a => push(node.getAttribute(a)));
        ['placeholder', 'title'].forEach(a => add(weak, node.getAttribute(a)));
        const ids = ((node.getAttribute('aria-labelledby') || '') + ' ' + (node.id ? '' : '')).trim();
        const root = node.getRootNode ? node.getRootNode() : document;
        ids.split(/\s+/).filter(Boolean).forEach(id => {
          const t = root.getElementById ? root.getElementById(id) : document.getElementById(id);
          if (t) push(t.innerText || t.textContent);
        });
        if (node.id) {
          try {
            const l = root.querySelector(`label[for="${CSS.escape(node.id)}"]`);
            if (l) push(l.innerText || l.textContent);
          } catch (_e) {}
        }
      }
      if (node.tagName === 'LABEL') push(node.innerText || node.textContent);
      // Frères précédents : le libellé est en général juste avant le champ.
      let prev = node.previousElementSibling;
      for (let i = 0; i < 3 && prev; i++, prev = prev.previousElementSibling) {
        if (!prev.querySelector || !prev.querySelector('input, textarea, select')) {
          push(prev.innerText || prev.textContent);
        }
      }
      if (candidates.length) break;
      node = composedParent(node);
    }
    return candidates.find(c => c.includes('?')) || candidates[0] || weak[0] || '';
  }

  // Vrai si le champ répond à une QUESTION (« … ? ») : il est alors traité en
  // dernière passe, par la banque de réponses puis le LLM, avec le libellé propre.
  function isQuestionField(el) {
    return getQuestionText(el).includes('?');
  }

  function isVisibleField(el) {
    if (!el || el.disabled || el.readOnly) return false;
    const r = el.getBoundingClientRect();
    return r.width > 20 && r.height > 8;
  }

  function highlightField(el) {
    const prevTransition = el.style.transition;
    const prevOutline = el.style.outline;
    const prevBg = el.style.backgroundColor;
    el.style.transition = 'all 0.3s ease';
    el.style.outline = '2px solid #10b981';
    el.style.backgroundColor = 'rgba(16, 185, 129, 0.08)';
    setTimeout(() => {
      el.style.outline = prevOutline;
      el.style.backgroundColor = prevBg;
      el.style.transition = prevTransition;
    }, 3500);
  }

  // -------------------------------------------------------------------------
  // Résolution du libellé d'un champ
  // -------------------------------------------------------------------------
  function getFieldLabel(el) {
    if (!el) return '';
    let parts = [];

    // 1. Attributs directs
    const ariaLabel = el.getAttribute('aria-label');
    if (ariaLabel) parts.push(ariaLabel);
    const placeholder = el.getAttribute('placeholder');
    if (placeholder) parts.push(placeholder);
    const title = el.getAttribute('title');
    if (title) parts.push(title);

    // 2. aria-labelledby & aria-describedby
    const ariaLabelledBy = el.getAttribute('aria-labelledby');
    if (ariaLabelledBy) {
      ariaLabelledBy.split(/\s+/).forEach(id => {
        const target = document.getElementById(id);
        if (target && target.innerText) parts.push(target.innerText);
      });
    }
    const ariaDescribedBy = el.getAttribute('aria-describedby');
    if (ariaDescribedBy) {
      ariaDescribedBy.split(/\s+/).forEach(id => {
        const target = document.getElementById(id);
        if (target && target.innerText) parts.push(target.innerText);
      });
    }

    // 3. label[for="..."]
    if (el.id) {
      try {
        const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
        if (l && l.innerText) parts.push(l.innerText);
      } catch (_e) {}
    }

    // 4. Label parent englobant
    const parentLabel = el.closest('label');
    if (parentLabel && parentLabel.innerText) {
      parts.push(parentLabel.innerText);
    }

    // 5. Frère précédent direct ou dans ses wrappers (jusqu'à 5 niveaux)
    let cur = el;
    for (let depth = 0; depth < 5 && cur && cur !== document.body; depth++) {
      let prev = cur.previousElementSibling;
      while (prev) {
        const txt = (prev.innerText || '').trim();
        if (txt && txt.length > 2 && txt.length < 350) {
          parts.push(txt);
          break;
        }
        prev = prev.previousElementSibling;
      }
      cur = cur.parentElement;
    }

    // 6. Remontée des ancêtres (jusqu'à 6 niveaux) pour capturer questions / titres
    let node = el.parentElement;
    for (let i = 0; i < 6 && node && node !== document.body && node !== document.documentElement; i++) {
      const candidates = node.querySelectorAll('label, legend, h1, h2, h3, h4, h5, h6, [class*="question" i], [class*="title" i], [class*="label" i], p, .text, span');
      for (const c of candidates) {
        if (c !== el && !c.contains(el)) {
          const t = (c.innerText || '').trim();
          if (t && t.length > 2 && t.length < 350) {
            parts.push(t);
            break;
          }
        }
      }
      if (parts.length > 2) break;
      node = node.parentElement;
    }

    // 7. Si toujours pauvre, inspecter le conteneur parent
    if (parts.length === 0 && el.parentElement) {
      const parentTxt = (el.parentElement.innerText || '').replace(el.value || '', '').trim();
      if (parentTxt && parentTxt.length < 250) parts.push(parentTxt);
    }

    ['name', 'id', 'data-test', 'data-testid', 'placeholder', 'autocomplete', 'title', 'aria-label'].forEach(attr => {
      const v = el.getAttribute && el.getAttribute(attr);
      if (v) parts.push(v);
    });

    // Champ dans un shadow root (SmartRecruiters…) : les recherches ci-dessus
    // s'arrêtent à la frontière du composant. On ajoute le libellé trouvé en la
    // traversant, placé en tête pour qu'il prime sur les textes voisins.
    const root = el.getRootNode ? el.getRootNode() : null;
    if (root && root !== document) {
      const shadowLabel = getQuestionText(el);
      if (shadowLabel) parts.unshift(shadowLabel);
      if (root.host) {
        ['label', 'name', 'id', 'data-test', 'data-testid', 'placeholder', 'aria-label'].forEach(attr => {
          const v = root.host.getAttribute && root.host.getAttribute(attr);
          if (v) parts.push(v);
        });
      }
    }

    return parts.join(' ').toLowerCase().replace(/[\r\n\t]+/g, ' ').replace(/\s+/g, ' ').trim();
  }

  // -------------------------------------------------------------------------
  // Helpers de communication API avec fallback Background Service Worker
  // (Bypasse 100% les restrictions CORS et Private Network Access de Chrome)
  // -------------------------------------------------------------------------
  async function apiGetCandidate(url) {
    if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.sendMessage) {
      try {
        const resp = await new Promise((resolve) => {
          const timer = setTimeout(() => resolve(null), 1200);
          try {
            chrome.runtime.sendMessage({ action: 'fetch_candidate', url }, (res) => {
              clearTimeout(timer);
              if (chrome.runtime.lastError) resolve(null);
              else resolve(res);
            });
          } catch (_e) {
            clearTimeout(timer);
            resolve(null);
          }
        });
        if (resp && resp.ok && resp.data) return resp.data;
      } catch (_e) {}
    }
    const res = await fetch(`${API_BASE}/api/candidate?url=${encodeURIComponent(url)}`, {
      signal: AbortSignal.timeout(3000)
    });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    return await res.json();
  }

  async function apiGetCvFile(url, defaultName) {
    if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.sendMessage) {
      try {
        const resp = await new Promise((resolve) => {
          const timer = setTimeout(() => resolve(null), 8000);
          try {
            chrome.runtime.sendMessage({ action: 'fetch_cv', url }, (res) => {
              clearTimeout(timer);
              if (chrome.runtime.lastError) resolve(null);
              else resolve(res);
            });
          } catch (_e) {
            clearTimeout(timer);
            resolve(null);
          }
        });
        if (resp && resp.ok && resp.base64) {
          const binaryStr = atob(resp.base64);
          const bytes = new Uint8Array(binaryStr.length);
          for (let i = 0; i < binaryStr.length; i++) bytes[i] = binaryStr.charCodeAt(i);
          return new File([bytes], resp.fileName || defaultName, { type: 'application/pdf', lastModified: Date.now() });
        }
      } catch (_e) {}
    }
    const cvResp = await fetch(`${API_BASE}/api/get-cv?url=${encodeURIComponent(url)}`, {
      signal: AbortSignal.timeout(10000)
    });
    if (!cvResp.ok) throw new Error('Impossible de charger le CV');
    const blob = await cvResp.blob();
    return new File([blob], defaultName, { type: 'application/pdf', lastModified: Date.now() });
  }

  async function apiSmartSelect(question, options, jobTitle) {
    if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.sendMessage) {
      try {
        const resp = await new Promise((resolve) => {
          const timer = setTimeout(() => resolve(null), 3500);
          try {
            chrome.runtime.sendMessage({
              action: 'smart_select',
              question,
              options: options.map(o => ({ value: o.value, text: o.text })),
              job_title: jobTitle
            }, (res) => {
              clearTimeout(timer);
              if (chrome.runtime.lastError) resolve(null);
              else resolve(res);
            });
          } catch (_e) {
            clearTimeout(timer);
            resolve(null);
          }
        });
        if (resp && resp.ok && resp.data) return resp.data;
      } catch (_e) {}
    }
    try {
      const selResp = await fetch(`${API_BASE}/api/smart-select`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          question,
          options: options.map(o => ({ value: o.value, text: o.text })),
          job_title: jobTitle
        }),
        signal: AbortSignal.timeout(6000)
      });
      if (selResp.ok) return await selResp.json();
    } catch (_e) {}
    return null;
  }

  async function apiSmartAnswer(question, jobTitle, maxLength) {
    // Une réponse rédigée par le LLM peut prendre une dizaine de secondes.
    if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.sendMessage) {
      try {
        const resp = await new Promise((resolve) => {
          const timer = setTimeout(() => resolve(null), 30000);
          try {
            chrome.runtime.sendMessage({ action: 'smart_answer', question, job_title: jobTitle, max_length: maxLength || 0, page_url: location.href }, (res) => {
              clearTimeout(timer);
              if (chrome.runtime.lastError) resolve(null);
              else resolve(res);
            });
          } catch (_e) {
            clearTimeout(timer);
            resolve(null);
          }
        });
        if (resp && resp.ok && resp.data) return resp.data;
      } catch (_e) {}
    }
    const ansResp = await fetch(`${API_BASE}/api/smart-answer`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, job_title: jobTitle, max_length: maxLength || 0, page_url: location.href }),
      signal: AbortSignal.timeout(30000)
    });
    return await ansResp.json();
  }

  // -------------------------------------------------------------------------
  // Remplissage principal
  // -------------------------------------------------------------------------
  async function runAutofill(preloadData = null, preloadCvBase64 = null, preloadCvName = null) {
    setPillState('loading', '⏳ Remplissage...');

    let data = preloadData;

    // 1. Toujours tenter de récupérer les données fraîches depuis le backend local pour l'URL actuelle
    // (garantit le bon choix entre CV étudiant et CV cybersécurité, même si le cache local contenait un autre profil)
    try {
      const freshData = await apiGetCandidate(location.href);
      if (freshData) {
        data = freshData;
      }
    } catch (_e) {
      // Backend temporairement inaccessible ou offline
    }

    // 2. Fallback si l'appel en direct a échoué : utiliser preloadData ou le cache chrome.storage.local
    if (!data && typeof chrome !== 'undefined' && chrome.storage && chrome.storage.local) {
      try {
        const stored = await new Promise((resolve) => {
          chrome.storage.local.get(['candidateData', 'cvBase64', 'cvFileName'], resolve);
        });
        if (stored && stored.candidateData) {
          data = stored.candidateData;
          if (!preloadCvBase64) preloadCvBase64 = stored.cvBase64;
          if (!preloadCvName) preloadCvName = stored.cvFileName;
        }
      } catch (_e) {}
    }

    if (!data) {
      console.error('[ApplyPilot] Impossible de joindre ApplyPilot local');
      setPillState('error', '⚠️ Lance le bot ApplyPilot (port 8765)');
      return;
    }

    const pers = data.personal || {};
    const qa = data.qa || {};
    let filledCount = 0;
    let cvAttached = false;

    // 1. Text Inputs (Champs personnels + Questions ouvertes en phrases complètes)
    const inputs = deepQueryAll('input:not([type="hidden"]):not([type="submit"]):not([type="button"]):not([type="checkbox"]):not([type="radio"]):not([type="file"])');
    for (const input of inputs) {
      if (input.value && input.value.trim().length > 0) continue; // Ne pas écraser
      const label = getFieldLabel(input);
      const isStandardPersonalInfo = /pr[ée]nom|first[\s_-]*name|nom(?![\s_-]*utilisateur)|last[\s_-]*name|e[\s_-]?mail|courriel|phone|t[ée]l[ée]phone|mobile|adresse|address|city|ville|postal|code[\s_-]*postal|linkedin|github|portfolio/i.test(label);
      if (!isStandardPersonalInfo && isQuestionField(input)) continue; // question libre : passe 6
      const type = (input.type || '').toLowerCase();
      const placeholder = (input.getAttribute('placeholder') || '').toLowerCase();

      // Confirmation Email
      if (/confirm.*e[\s_-]?mail|confirm.*courriel|r[ée]p[ée]t.*e[\s_-]?mail/i.test(label) || /confirm.*email/i.test(input.name || '') || /confirm.*email/i.test(input.id || '') || (input.getAttribute('data-test') || '').includes('confirm-email')) {
        if (setNativeValue(input, pers.email)) filledCount++;
      }
      // Prénom
      else if (/first[\s_-]*name|pr[ée]nom|given[\s_-]*name/i.test(label) || /first[\s_-]*name/i.test(input.name || '') || input.autocomplete === 'given-name' || (input.getAttribute('data-test') || '').includes('first-name')) {
        if (setNativeValue(input, pers.first_name)) filledCount++;
      }
      // Nom
      else if (/last[\s_-]*name|nom(?![\s_-]*(utilisateur|user|complet|de[\s_-]*jeune))|family[\s_-]*name|surname/i.test(label) || /last[\s_-]*name/i.test(input.name || '') || input.autocomplete === 'family-name' || (input.getAttribute('data-test') || '').includes('last-name')) {
        if (setNativeValue(input, pers.last_name)) filledCount++;
      }
      // Nom complet
      else if (/full[\s_-]*name|nom[\s_-]*complet|votre[\s_-]*nom/i.test(label)) {
        if (setNativeValue(input, pers.full_name)) filledCount++;
      }
      // Email
      else if (type === 'email' || /e[\s_-]?mail|courriel|adresse[\s_-]*mail/i.test(label)) {
        if (setNativeValue(input, pers.email)) filledCount++;
      }
      // Téléphone
      else if (type === 'tel' || /phone|mobile|portable|t[ée]l[ée]phone|num[ée]ro/i.test(label)) {
        if (setNativeValue(input, pers.phone)) filledCount++;
      }
      // Code postal
      else if (/code[\s_-]*postal|postal[\s_-]*code|zip/i.test(label)) {
        if (setNativeValue(input, pers.postal_code)) filledCount++;
      }
      // Ville / Lieu
      else if (/city|ville|commune|localit[ée]|lieu/i.test(label) && !/[ée]tablissement|naissance/i.test(label)) {
        if (setNativeValue(input, pers.city)) filledCount++;
      }
      // Adresse
      else if (/address|adresse(?![\s_-]*mail)|rue|street/i.test(label)) {
        if (setNativeValue(input, pers.address)) filledCount++;
      }
      // Nationalité / Citoyenneté
      else if (/nationalit[ée]|nationality|citoyennet[ée]|citizenship/i.test(label)) {
        if (setNativeValue(input, pers.nationality || 'Marocaine')) filledCount++;
      }
      // LinkedIn
      else if (/linkedin/i.test(label)) {
        if (setNativeValue(input, pers.linkedin)) filledCount++;
      }
      // GitHub
      else if (/github/i.test(label)) {
        if (setNativeValue(input, pers.github)) filledCount++;
      }
      // Portfolio / Site web
      else if (/portfolio|site[\s_-]*web|website|site[\s_-]*perso|site[\s_-]*internet/i.test(label)) {
        if (setNativeValue(input, pers.portfolio)) filledCount++;
      }
      // Établissement / École / Université
      else if (/quel[\s_-]*[ée]tablissement|[ée]tablissement.*inscrit|inscrit.*[ée]tablissement|\b[ée]tablissement\b|\b[ée]cole\b|\bschool\b|\binstitution\b|\buniversit[ée]\b/i.test(label)) {
        const schoolVal = (data.education && data.education.school) || qa.etablissement || '';
        if (setNativeValue(input, schoolVal)) filledCount++;
      }
      // Date de début / À partir de quand disponible (phrase complète si champ texte)
      else if (/[àa]\s*partir\s*de\s*quand.*disponible|disponible.*d[ée]but|date.*d[ée]but.*stage|d[ée]but.*stage|quand.*disponible.*stage|start.*date.*internship/i.test(label)) {
        let val;
        if (type === 'date') val = '2027-04-05';
        else if (/jj\/mm\/aaaa|dd\/mm\/yyyy/i.test(placeholder)) val = '05/04/2027';
        else val = qa.date_debut_stage || "Je suis disponible pour débuter mon stage à partir du 5 avril 2027 pour une durée de 4 à 6 mois.";
        if (setNativeValue(input, val)) filledCount++;
      }
      // Jusqu'à quand disponible pour le démarrage / Date limite de démarrage
      else if (/jusqu['\s]*[àa]\s*quand.*disponible|jusqu['\s]*[àa]\s*quand.*d[ée]marrage|d[ée]marrage.*stage|date.*limite.*d[ée]marrage|fin.*d[ée]marrage|latest.*start/i.test(label)) {
        let val;
        if (type === 'date') val = '2027-09-01';
        else if (/jj\/mm\/aaaa|dd\/mm\/yyyy/i.test(placeholder)) val = '01/09/2027';
        else val = qa.date_fin_demarrage_stage || "Je suis disponible pour le démarrage de mon stage jusqu'en septembre 2027 au plus tard.";
        if (setNativeValue(input, val)) filledCount++;
      }
      // Convention de stage (si champ texte)
      else if (/convention.*stage|disposerez.*convention|b[ée]n[ée]ficierez.*convention/i.test(label)) {
        const val = qa.convention_stage || (schoolVal ? `Oui, je disposerai d'une convention de stage délivrée par mon école (${schoolVal}) pour toute la durée du stage.` : "Oui, je disposerai d'une convention de stage délivrée par mon établissement pour toute la durée du stage.");
        if (setNativeValue(input, val)) filledCount++;
      }
      // Type de stage (si champ texte)
      else if (/type.*stage|nature.*stage|kind.*internship/i.test(label)) {
        const val = qa.type_stage || "Stage conventionné";
        if (setNativeValue(input, val)) filledCount++;
      }
      // Durée du stage
      else if (/dur[ée]e.*stage|duration.*internship/i.test(label)) {
        const val = qa.duree_stage || "Je recherche un stage d'une durée de 4 à 6 mois.";
        if (setNativeValue(input, val)) filledCount++;
      }
      // Prétentions salariales / Gratification
      else if (/pr[ée]tention|salaire|salary|compensation|remun[ée]ration|gratification/i.test(label)) {
        const val = qa.pretentions_salariales || "Selon la grille légale de rémunération des stagiaires en vigueur au sein de l'entreprise.";
        if (setNativeValue(input, val)) filledCount++;
      }
      // Mobilité géographique
      else if (/mobilit[ée]|d[ée]placement|relocat/i.test(label)) {
        const val = qa.mobilite_geographique || "Je suis totalement mobile sur l'ensemble de la France, avec une préférence pour l'Île-de-France et la région Centre-Val de Loire.";
        if (setNativeValue(input, val)) filledCount++;
      }
      // Diplôme préparé / Cursus
      else if (/dipl[ôo]me[\s_-]*pr[ée]par[ée]|formation|cursus|\bdipl[ôo]me\b/i.test(label)) {
        const val = (data.education && data.education.degree) || qa.diplome_prepare || "Diplôme d'ingénieur en Cybersécurité";
        if (setNativeValue(input, val)) filledCount++;
      }
      // Spécialité / Major
      else if (/sp[ée]cialit[ée]|major|field[\s_-]*of[\s_-]*study/i.test(label)) {
        const val = (data.education && data.education.specialite) || "Cybersécurité";
        if (setNativeValue(input, val)) filledCount++;
      }
      // Dates formation
      else if (/date[\s_-]*(?:de[\s_-]*)?d[ée]but.*formation|start[\s_-]*date.*education/i.test(label)) {
        if (setNativeValue(input, (data.education && data.education.start_date) || "01/09/2023")) filledCount++;
      }
      else if (/date[\s_-]*(?:de[\s_-]*)?fin.*formation|end[\s_-]*date.*education|graduation[\s_-]*date/i.test(label)) {
        if (setNativeValue(input, (data.education && data.education.end_date) || "01/05/2028")) filledCount++;
      }
      else if (/formation[\s_-]*en[\s_-]*cours|currently[\s_-]*enrolled|is[\s_-]*current/i.test(label)) {
        if (setNativeValue(input, (data.education && data.education.is_current) || "Yes")) filledCount++;
      }
      // Année d'obtention diplôme
      else if (/ann[ée]e[\s_-]*dipl[ôo]me|graduation[\s_-]*year/i.test(label)) {
        if (setNativeValue(input, qa.annee_diplome || "2028")) filledCount++;
      }
      // Niveau d'études
      else if (/niveau[\s_-]*(d['\s]*)?[ée]tudes?|education[\s_-]*level/i.test(label)) {
        if (setNativeValue(input, qa.niveau_etudes || (schoolVal ? `Étudiant à ${schoolVal}` : "Étudiant"))) filledCount++;
      }
      // Langues / Anglais
      else if (/anglais|english|langue|toeic/i.test(label)) {
        const val = qa.langues || "Français (langue maternelle) et Anglais (courant professionnel, score TOEIC 980/990).";
        if (setNativeValue(input, val)) filledCount++;
      }
      // Télétravail
      else if (/t[ée]l[ée]travail|remote|hybride/i.test(label)) {
        const val = qa.teletravail || "Je suis disponible pour travailler sur site, en mode hybride ou en télétravail.";
        if (setNativeValue(input, val)) filledCount++;
      }
      // Source offre
      else if (/source|comment.*connu/i.test(label)) {
        if (setNativeValue(input, qa.source_offre || "LinkedIn")) filledCount++;
      }
      // Questions ouvertes / Formulaire sous forme de question textuelle -> smart answer
      else if (label.includes('?') || /pourquoi|motivation|message|remarque|int[ée]r[êe]t|atout|d[ée]crivez|pr[ée]cisez/i.test(label)) {
        try {
          const ansData = await apiSmartAnswer(label, document.title);
          if (ansData && ansData.answer) {
            if (setNativeValue(input, ansData.answer)) filledCount++;
          }
        } catch (_e) {}
      }
    }

    // 2. Selects (listes déroulantes intelligentes + Support complet ATS)
    function applySelectOption(sel, opt) {
      if (!sel || !opt) return false;
      try {
        const desc = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value');
        if (desc && desc.set) desc.set.call(sel, opt.value);
        else sel.value = opt.value;
      } catch (_e) {
        sel.value = opt.value;
      }
      sel.selectedIndex = opt.index;
      sel.dispatchEvent(new Event('input', { bubbles: true, cancelable: true }));
      sel.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));
      highlightField(sel);
      return true;
    }

    const selects = deepQueryAll('select');
    for (const sel of selects) {
      if (sel.selectedIndex > 0 && sel.value && sel.value !== '0' && sel.value !== '-1') continue;
      const label = getFieldLabel(sel);
      const options = Array.from(sel.options || []).filter(o => o.value && o.value !== '' && o.value !== '-1' && o.text.trim() !== '');
      if (options.length === 0) continue;

      let matched = null;

      // A. Civilité / Titre / Salutation / Genre -> Monsieur / M.
      if (/civilit[ée]|titre|salutation|title|gender|genre/i.test(label)) {
        matched = options.find(o => /^(monsieur|m\.|mr|homme|male)$/i.test(o.text.trim()) || /^(monsieur|m\.|mr)$/i.test(o.value.trim())) ||
                  options.find(o => /monsieur|m\./i.test(o.text));
      }
      // B. Pays de résidence / Localisation -> France
      else if (/pays|country|nation/i.test(label)) {
        matched = options.find(o => /france/i.test(o.text) || /^(fr|fra)$/i.test(o.value.trim()));
      }
      // C. Permis de conduire -> Oui / Permis B
      else if (/permis|driver.*licen|driving/i.test(label)) {
        matched = options.find(o => /^oui|^yes/i.test(o.text.trim()) || /^oui|^yes/i.test(o.value.trim()) || /permis[\s_-]*b/i.test(o.text));
      }
      // D. Véhicule personnel -> Non
      else if (/v[ée]hicule|voiture|car[\s_-]*owner/i.test(label)) {
        matched = options.find(o => /^non|^no/i.test(o.text.trim()));
      }
      // E. Niveau d'études / Diplôme préparé -> Bac+4 / Bac+5 / Ingénieur / Master
      else if (/niveau.*[ée]tude|dipl[ôo]me|education.*level|degree|formation/i.test(label)) {
        matched = options.find(o => /bac\s*\+\s*4[\s/]*5|bac\s*\+\s*5|bac\s*\+\s*4|ing[ée]nieur|master/i.test(o.text)) ||
                  options.find(o => /sup[ée]rieur|grande[\s_-]*[ée]cole/i.test(o.text));
      }
      // F. Type de contrat recherché -> Stage / Internship
      else if (/contrat|contract|type.*poste|employment/i.test(label)) {
        matched = options.find(o => /^stage|internship|stagiaire|conventionn/i.test(o.text.trim()));
      }
      // G. Durée du stage -> 6 mois
      else if (/dur[ée]e|duration/i.test(label)) {
        matched = options.find(o => /6\s*mois|six\s*mois|6\s*months/i.test(o.text));
      }
      // H. Année d'obtention du diplôme -> 2028 / 2027
      else if (/ann[ée]e.*dipl[ôo]me|graduation|promo/i.test(label)) {
        matched = options.find(o => /2028/i.test(o.text)) || options.find(o => /2027/i.test(o.text));
      }
      // I. Années d'expérience professionnelle -> Débutant / Étudiant / < 1 an / 0-2 ans
      else if (/exp[ée]rience|years.*experience|anciennet[ée]/i.test(label)) {
        matched = options.find(o => /[ée]tudiant|d[ée]butant|junior|0[\s_-]*[àa-]?[\s_-]*[12]\s*an|moins\s*d['\s]*1\s*an|<[\s]*1\s*an|sans\s*exp/i.test(o.text));
      }
      // J. Mobilité géographique -> Toute la France / Oui
      else if (/mobilit[ée]|d[ée]placement|relocat/i.test(label)) {
        matched = options.find(o => /france|nationale|toute|oui|yes/i.test(o.text));
      }
      // K. Autorisation de travail en France / Éligibilité / Nationalité UE -> Oui
      else if (/autoris(ation|[ée])|l[ée]galement|droit.*travailler|work.*permit|eligible.*work|visa/i.test(label)) {
        matched = options.find(o => /^oui|^yes/i.test(o.text.trim()) || /citoyen|ue|france/i.test(o.text));
      }
      // L. Source / Comment avez-vous connu l'offre -> LinkedIn / Site carrière
      else if (/source|comment.*connu|how.*hear|canal/i.test(label)) {
        matched = options.find(o => /linkedin/i.test(o.text)) ||
                  options.find(o => /site[\s_-]*carri[èe]re|internet|site[\s_-]*web/i.test(o.text));
      }
      // M. Situation de handicap / RQTH -> Non / Non concerné
      else if (/handicap|rqth|disabilit/i.test(label)) {
        matched = options.find(o => /^non|^no|aucun|non[\s_-]*concern/i.test(o.text.trim()));
      }
      // N. Télétravail -> Indifférent / Hybride / Oui
      else if (/t[ée]l[ée]travail|remote/i.test(label)) {
        matched = options.find(o => /hybride|indiff[ée]rent|flexible|oui/i.test(o.text));
      }
      // O. Niveau d'anglais / Langues -> Courant / C1 / Professionnel
      else if (/anglais|english|langue/i.test(label)) {
        matched = options.find(o => /courant|fluent|professionnel|c1|c2|b2|avanc[ée]/i.test(o.text));
      }
      // P. Disponibilité -> Immédiate / Selon convention / 2027
      else if (/disponibilit[ée]|availability|pr[ée]avis|notice/i.test(label)) {
        matched = options.find(o => /imm[ée]diate|d[èe]s\s*que\s*possible|convention|2027/i.test(o.text));
      }

      // Si aucune correspondance stricte, tenter une correspondance sémantique avec la banque Q&A
      if (!matched && qa) {
        for (const [qaKey, qaVal] of Object.entries(qa)) {
          const cleanKey = qaKey.replace(/_/g, ' ').toLowerCase();
          if (cleanKey.split(' ').some(w => w.length > 4 && label.includes(w))) {
            const valStr = String(qaVal).toLowerCase();
            matched = options.find(o => {
              const optClean = o.text.toLowerCase();
              return optClean.includes(valStr) || valStr.includes(optClean);
            });
            if (matched) break;
          }
        }
      }

      if (matched && applySelectOption(sel, matched)) {
        filledCount++;
      }
    }

    // Helper pour activer un chip, bouton ou radio
    function activateChoiceElement(el) {
      if (!el) return false;
      try {
        el.scrollIntoView({ block: 'nearest', inline: 'nearest' });
        el.focus();
        el.click();
        el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
        el.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
        el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
        el.dispatchEvent(new Event('input', { bubbles: true, cancelable: true }));
        el.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));

        const radio = el.querySelector('input[type="radio"], input[type="checkbox"]') ||
                      (el.htmlFor ? document.getElementById(el.htmlFor) : null);
        if (radio) {
          radio.checked = true;
          radio.dispatchEvent(new Event('input', { bubbles: true, cancelable: true }));
          radio.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));
        }
        if (el.getAttribute('role') === 'radio' || el.getAttribute('role') === 'checkbox') {
          el.setAttribute('aria-checked', 'true');
        }
        highlightField(el);
        return true;
      } catch (_e) {
        return false;
      }
    }

    // 3. Traitement des choix (Radios, Checkboxes, Chips Taleo / Alta UI, Boutons de choix)
    const choiceSelectors = 'button, [role="radio"], [role="button"], label, .chip, .badge, .btn, span[class*="chip" i], div[class*="chip" i], input[type="radio"], input[type="checkbox"]';
    const choiceCandidates = deepQueryAll(choiceSelectors);
    const handledGroups = new Set();

    for (const el of choiceCandidates) {
      const isInput = el.tagName === 'INPUT';
      if (isInput && el.checked) continue;

      const txt = (el.innerText || el.value || '').trim();
      const txtLower = txt.toLowerCase();
      if (!txt && !isInput) continue;

      const label = getFieldLabel(el);
      if (!label) continue;

      // Question 5 : Disposerez-vous d'une convention pour toute la durée de votre stage ? -> OUI
      if (/convention.*stage|disposerez.*convention|b[ée]n[ée]ficierez.*convention/i.test(label)) {
        if (/^oui$|^yes$/i.test(txtLower) || (isInput && /oui|yes/i.test(el.value || ''))) {
          if (!handledGroups.has('convention')) {
            if (activateChoiceElement(el)) {
              handledGroups.add('convention');
              filledCount++;
            }
          }
        }
      }

      // Question 4 : De quel type de stage s'agit-il ? -> FIN MASTER 1 ou CÉSURE 1ÈRE PARTIE
      else if (/type.*stage|nature.*stage|kind.*internship/i.test(label)) {
        if (/fin\s*master\s*1|c[ée]sure\s*1[èe]re\s*partie|bac\s*\+\s*4|c[ée]sure/i.test(txtLower)) {
          if (!handledGroups.has('type_stage')) {
            if (activateChoiceElement(el)) {
              handledGroups.add('type_stage');
              filledCount++;
            }
          }
        }
      }

      // Permis de conduire -> OUI
      else if (/permis[\s_-]*de[\s_-]*conduire|driving[\s_-]*licen/i.test(label) && !/v[ée]hicule/i.test(label)) {
        if (/^oui$|^yes$/i.test(txtLower) || (isInput && /oui|yes/i.test(el.value || ''))) {
          if (!handledGroups.has('permis')) {
            if (activateChoiceElement(el)) {
              handledGroups.add('permis');
              filledCount++;
            }
          }
        }
      }

      // Véhicule personnel -> NON
      else if (/v[ée]hicule[\s_-]*personnel|voiture|car[\s_-]*owner/i.test(label)) {
        if (/^non$|^no$/i.test(txtLower) || (isInput && /non|no/i.test(el.value || ''))) {
          if (!handledGroups.has('vehicule')) {
            if (activateChoiceElement(el)) {
              handledGroups.add('vehicule');
              filledCount++;
            }
          }
        }
      }

      // Autorisation de travail en France -> OUI
      else if (/autoris(ation|[ée])|l[ée]galement|droit[\s_-]*de[\s_-]*travailler|work[\s_-]*permit/i.test(label)) {
        if (/^oui$|^yes$/i.test(txtLower) || (isInput && /oui|yes/i.test(el.value || ''))) {
          if (!handledGroups.has('autorisation')) {
            if (activateChoiceElement(el)) {
              handledGroups.add('autorisation');
              filledCount++;
            }
          }
        }
      }

      // Déjà salarié / Ancien employé -> NON
      else if (/d[ée]j[àa].*(salari|employ)|former.*employ/i.test(label)) {
        if (/^non$|^no$/i.test(txtLower) || (isInput && /non|no/i.test(el.value || ''))) {
          if (!handledGroups.has('ancien_salarie')) {
            if (activateChoiceElement(el)) {
              handledGroups.add('ancien_salarie');
              filledCount++;
            }
          }
        }
      }

      // Handicap / RQTH -> NON
      else if (/handicap|rqth|disabilit/i.test(label)) {
        if (/^non$|^no$|aucun|non[\s_-]*concern/i.test(txtLower) || (isInput && /non|no/i.test(el.value || ''))) {
          if (!handledGroups.has('handicap')) {
            if (activateChoiceElement(el)) {
              handledGroups.add('handicap');
              filledCount++;
            }
          }
        }
      }

      // Télétravail -> OUI / HYBRIDE
      else if (/t[ée]l[ée]travail|remote|hybride/i.test(label)) {
        if (/^oui$|^yes$|hybride|indiff[ée]rent/i.test(txtLower) || (isInput && /oui|yes/i.test(el.value || ''))) {
          if (!handledGroups.has('teletravail')) {
            if (activateChoiceElement(el)) {
              handledGroups.add('teletravail');
              filledCount++;
            }
          }
        }
      }
    }

    // 4. Upload automatique du CV PDF (Support natif ATS : SAP SuccessFactors, Workday, Taleo, etc.)
    if (data.has_cv) {
      async function prepareCvFile() {
        const targetFileName = (data && data.cv_name) || preloadCvName || 'CV.pdf';
        // N'utiliser le CV préchargé en mémoire QUE si son nom correspond au CV ciblé pour cette offre
        if (preloadCvBase64 && preloadCvName && preloadCvName === targetFileName) {
          console.log('[ApplyPilot] Conversion directe du CV préchargé en mémoire:', preloadCvName);
          try {
            const binaryStr = atob(preloadCvBase64);
            const bytes = new Uint8Array(binaryStr.length);
            for (let i = 0; i < binaryStr.length; i++) bytes[i] = binaryStr.charCodeAt(i);
            return new File([bytes], targetFileName, { type: 'application/pdf', lastModified: Date.now() });
          } catch (_e) {}
        }
        return await apiGetCvFile(location.href, targetFileName);
      }

      try {
        // A. Vérifier si le CV a déjà été chargé avec succès dans la page
        const bodyText = document.body ? document.body.innerText || '' : '';
        const alreadyUploaded = /le fichier a été chargé avec succès|uploaded successfully|télécharger le document.*\.pdf/i.test(bodyText);
        if (alreadyUploaded) {
          console.log('[ApplyPilot] CV déjà validé sur la page');
          cvAttached = true;
        } else {
          const cvFile = await prepareCvFile();
          if (!cvFile) {
            console.warn('[ApplyPilot] Impossible de charger le fichier CV');
            return;
          }
          const dt = new DataTransfer();
          dt.items.add(cvFile);

          function injectFileIntoInput(inputEl, dropTarget = null) {
            if (!inputEl) return false;
            try {
              inputEl.files = dt.files;
              try {
                const desc = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'files');
                if (desc && desc.set) desc.set.call(inputEl, dt.files);
              } catch (_e) {}

              inputEl.dispatchEvent(new Event('input', { bubbles: true, cancelable: true, composed: true }));
              inputEl.dispatchEvent(new Event('change', { bubbles: true, cancelable: true, composed: true }));
              try {
                inputEl.dispatchEvent(new Event('blur', { bubbles: true, composed: true }));
              } catch (_e) {}

              highlightField(dropTarget || inputEl);
              return true;
            } catch (err) {
              console.warn('[ApplyPilot] Erreur injectFileIntoInput:', err);
              return false;
            }
          }

          // Étape 0 : Support natif SAP SuccessFactors Quick Apply (Bouygues, Engie, etc.)

          const sfResumeField = document.getElementById('paramResumeFileName');

          const sfResumeContent = document.getElementById('paramResumeContent');

          const sfAttachWrapper = document.querySelector('[id$="_attachWrapper"], .attachWrapper');



          if ((sfResumeField && sfResumeContent) || sfAttachWrapper) {

            console.log('[ApplyPilot] Formulaire SAP SuccessFactors détecté, injection directe du CV');

            const fileName = (data && data.cv_name) || preloadCvName || 'CV.pdf';

            let base64 = (preloadCvName === fileName) ? preloadCvBase64 : null;

            if (!base64) {

              try {

                const fileObj = await apiGetCvFile(location.href, fileName);

                base64 = await new Promise((res) => {

                  const reader = new FileReader();

                  reader.onloadend = () => {

                    const s = reader.result;

                    res(typeof s === 'string' ? s.split(',')[1] : null);

                  };

                  reader.readAsDataURL(fileObj);

                });

              } catch (_e) {}

            }



            if (base64) {

              if (sfResumeField) sfResumeField.value = fileName;

              if (sfResumeContent) sfResumeContent.value = base64;



              // Notification du composant juic dans le MAIN world

              try {

                const script = document.createElement('script');

                script.textContent = `

                  (function() {

                    const fn = "${fileName}";

                    const b64 = "${base64}";

                    const now = new Date().toLocaleDateString('fr-FR');

                    if (window.juic && window.juic.$) {

                      const f = window.juic.$("paramResumeFileName");

                      const c = window.juic.$("paramResumeContent");

                      if (f) f.value = fn;

                      if (c) c.value = b64;

                    }

                    if (window.juic && window.juic.Component && window.juic.Component._registry) {

                      for (const comp of Object.values(window.juic.Component._registry)) {

                        if (comp && comp._config && (comp._config.isQuickApplyResume || comp._config.attachmentType === 'RESUME')) {

                          try {

                            comp.dispatch("bypassUploadFile", { fileName: fn, fileType: "RESUME", lastModified: now });

                            comp.dispatch("uploadSucceeded", {

                              response: {

                                result: { id: "QUICKAPPLYATTACHMENTID", name: fn, updated: now, fileType: "RESUME" }

                              }

                            });

                          } catch (_err) {}

                          break;

                        }

                      }

                    }

                    const err = document.querySelector('.qaResume');

                    if (err) err.classList.add('displayNone');

                  })();

                `;

                (document.head || document.documentElement).appendChild(script);

                script.remove();

              } catch (_e) {}



              // Mise à jour visuelle des labels SuccessFactors

              const dlLabel = document.querySelector('[id$="_attachDownloadLabel"]');

              const atLabel = document.querySelector('[id$="_attachLabel"]');

              const dlLink = document.querySelector('[id$="_attachDownloadLabelLink"]');

              const scIcon = document.querySelector('[id$="_attachSuccess"]');

              if (dlLabel) dlLabel.classList.remove('displayNone');

              if (atLabel) atLabel.classList.add('displayNone');

              if (dlLink) dlLink.textContent = fileName;

              if (scIcon) scIcon.classList.remove('displayNone');

              if (sfAttachWrapper) highlightField(sfAttachWrapper);



              cvAttached = true;

            }

          }



          // Étape 1 : Recherche d'un <input type="file"> déjà présent (Indeed, WTTJ, Lever, etc.)
          let fileInputs = deepQueryAll('input[type="file"]');
          document.querySelectorAll('*').forEach(el => {
            if (el.shadowRoot) {
              el.shadowRoot.querySelectorAll('input[type="file"]').forEach(sf => fileInputs.push(sf));
            }
          });

          if (fileInputs.length > 0) {
            let chosen = fileInputs.find(fi => {
              const txt = `${fi.id} ${fi.name} ${fi.accept} ${fi.getAttribute('aria-label') || ''}`.toLowerCase();
              return /cv|resume|curriculum|candidat/i.test(txt);
            }) || fileInputs[0];
            if (injectFileIntoInput(chosen)) {
              cvAttached = true;
            }
          }

          // Étape 2 : Portails dynamiques générant l'input file seulement au clic (SAP SuccessFactors, Workday, Taleo, etc.)
          if (!cvAttached) {
            console.log('[ApplyPilot] Recherche du déclencheur d\'upload ATS dynamique...');
            const specificTriggers = [
              // SAP SuccessFactors (framework juic / SAP UI5)
              '[id$="_attachIcon"]',
              '.addAttachments',
              '.attachActions',
              '[onclick*="juic.fire"][onclick*="action"]',
              '.attachmentBtn',
              '[id$="_attachLabel"]',
              '[id$="_attachWrapper"]',
              // Workday
              '[data-automation-id="file-upload-input"]',
              '[data-automation-id="file-upload-dropzone"]',
              '[data-automation-id="uploadFile"]',
              '.file-upload-drop-zone',
              // SAP UI5 / Fiori
              '[data-sap-ui*="upload"]',
              '.sapUiFileUploader'
            ];

            let triggerEl = null;
            for (const sel of specificTriggers) {
              const el = document.querySelector(sel);
              if (el && (el.offsetWidth > 0 || el.offsetHeight > 0)) {
                triggerEl = el;
                break;
              }
            }

            // Recherche par texte pour tout ATS non listé
            if (!triggerEl) {
              const cvKeywords = /charger.*cv|d[ée]poser.*cv|importer.*cv|ajouter.*cv|t[ée]l[ée]verser.*cv|upload.*resume|attach.*resume|select.*resume|upload.*cv|add.*cv/i;
              const candidates = Array.from(document.querySelectorAll('button, a, div[role="button"], span[role="button"], div.attachWrapper, div[tabindex="0"]'));
              for (const c of candidates) {
                if ((c.innerText || '').length < 80 && cvKeywords.test(c.innerText || '')) {
                  const subBtn = c.querySelector('button, [role="button"], .glyphicon-plus-sign, svg, span');
                  triggerEl = subBtn || c;
                  break;
                }
              }
            }

            if (triggerEl) {
              console.log('[ApplyPilot] Déclencheur CV trouvé, activation par clic:', triggerEl);
              try {
                triggerEl.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                if (typeof triggerEl.click === 'function') triggerEl.click();
              } catch (_e) {}

              // Attente de l'apparition de l'input file dans le DOM (jusqu'à 2.5 secondes)
              let newlyFound = null;
              for (let i = 0; i < 25; i++) {
                await new Promise(r => setTimeout(r, 100));
                newlyFound = document.querySelector('input[type="file"]');
                if (newlyFound) break;
              }

              if (newlyFound) {
                console.log('[ApplyPilot] Input file apparu dynamiquement après clic:', newlyFound);
                if (injectFileIntoInput(newlyFound, triggerEl)) {
                  cvAttached = true;
                  // Attente de la validation asynchrone par l'ATS
                  await new Promise(r => setTimeout(r, 2000));
                }
              }
            }
          }

          // Étape 3 : Repli HTML5 Drag & Drop sur les dropzones
          if (!cvAttached) {
            const dropzones = Array.from(document.querySelectorAll('.attachWrapper, .attachmentField, [class*="dropzone"], [class*="upload"], [id*="attach"], [data-test="resume-upload"], [data-test*="upload"], [data-test*="resume"], spl-file-upload, spl-dropzone'));
            for (const dz of dropzones) {
              try {
                dz.dispatchEvent(new DragEvent('dragenter', { bubbles: true, cancelable: true, dataTransfer: dt }));
                dz.dispatchEvent(new DragEvent('dragover', { bubbles: true, cancelable: true, dataTransfer: dt }));
                dz.dispatchEvent(new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: dt }));
                cvAttached = true;
                highlightField(dz);
                break;
              } catch (_e) {}
            }
          }
        }
      } catch (err) {
        console.warn('[ApplyPilot] Erreur attachement CV:', err);
      }
    }

    // 5. Textareas (Banque Q&A + Génération IA des questions ouvertes en phrases complètes)
    const textareas = deepQueryAll('textarea');
    let aiCount = 0;
    for (const ta of textareas) {
      if (ta.value && ta.value.trim().length > 0) continue;
      if (isQuestionField(ta)) continue; // question libre : passe 6
      const label = getFieldLabel(ta);

      // Correspondances Banque Q&A directe dans un textarea
      if (/pr[ée]tention|salaire|salary|compensation|remun[ée]ration|gratification/i.test(label)) {
        if (setNativeValue(ta, qa.pretentions_salariales || "Selon la grille légale de rémunération des stagiaires en vigueur au sein de l'entreprise.")) filledCount++;
      } else if (/[àa]\s*partir\s*de\s*quand.*disponible|disponible.*d[ée]but|date[\s_-]*d[ée]but/i.test(label)) {
        if (setNativeValue(ta, qa.date_debut_stage || "Je suis disponible pour débuter mon stage à partir du 5 avril 2027 pour une durée de 4 à 6 mois.")) filledCount++;
      } else if (/jusqu['\s]*[àa]\s*quand.*disponible|d[ée]marrage/i.test(label)) {
        if (setNativeValue(ta, qa.date_fin_demarrage_stage || "Je suis disponible pour le démarrage de mon stage jusqu'en septembre 2027 au plus tard.")) filledCount++;
      } else if (/convention.*stage|disposerez.*convention/i.test(label)) {
        if (setNativeValue(ta, qa.convention_stage || "Oui, je disposerai d'une convention de stage délivrée par mon établissement pour toute la durée du stage.")) filledCount++;
      } else if (/mobilit[ée]|d[ée]placement/i.test(label)) {
        if (setNativeValue(ta, qa.mobilite_geographique || "Je suis totalement mobile sur l'ensemble de la France, avec une préférence pour l'Île-de-France et la région Centre-Val de Loire.")) filledCount++;
      } else if (/anglais|langue|english/i.test(label)) {
        if (setNativeValue(ta, qa.langues || "Français (langue maternelle) et Anglais (courant professionnel, score TOEIC 980/990).")) filledCount++;
      }
      // Questions ouvertes -> Génération IA personnalisée en direct
      else if (/pourquoi|motivation|message|remarque|int[ée]r[êe]t|atout|d[ée]crivez|exp[ée]rience|qualit[ée]|projet|comp[ée]tence|lettre/i.test(label) || label.includes('?') || textareas.length === 1) {
        try {
          setPillState('loading', '🤖 Rédaction IA...');
          const ansData = await apiSmartAnswer(label, document.title);
          if (ansData && ansData.answer) {
            setNativeValue(ta, ansData.answer);
            filledCount++;
            if (ansData.source === 'ai') aiCount++;
          }
        } catch (e) {
          console.warn('[ApplyPilot] Échec smart-answer:', e);
        }
      }
    }

    // 6. Questions libres restées vides — champs d'une ligne comme zones de texte,
    // y compris dans les shadow roots (SmartRecruiters : « Questions préliminaires »).
    // Tout champ encore vide dont le libellé est une question part au LLM, qui
    // répond à partir de la banque de réponses puis des faits réels du candidat.
    const freeFields = deepQueryAll(
      'textarea, input[type="text"], input:not([type])'
    ).filter(el => isVisibleField(el) && !(el.value && el.value.trim().length > 0));
    for (const field of freeFields) {
      if (field.value && field.value.trim().length > 0) continue;
      const question = getQuestionText(field);
      const isTextarea = field.tagName === 'TEXTAREA';
      // Un champ d'une ligne n'est traité que si son libellé est une vraie question :
      // on ne veut pas rédiger une phrase dans un champ « Ville » resté vide.
      if (!question || (!question.includes('?') && !(isTextarea && question.length > 12))) continue;
      try {
        setPillState('loading', '🤖 Rédaction IA...');
        const maxLength = field.maxLength > 0 ? field.maxLength : (isTextarea ? 1200 : 200);
        const ansData = await apiSmartAnswer(question, document.title, maxLength);
        if (ansData && ansData.answer) {
          if (setNativeValue(field, String(ansData.answer).slice(0, maxLength))) {
            filledCount++;
            if (ansData.source === 'ai') aiCount++;
          }
        }
      } catch (e) {
        console.warn('[ApplyPilot] Échec question libre:', question, e);
      }
    }

    // Retour visuel complet
    if (cvAttached && filledCount > 0) {
      let msg = `✓ ${filledCount} champ(s) + 📄 CV joint !`;
      if (aiCount > 0) msg += ` + 🤖 ${aiCount} texte(s) IA`;
      setPillState('success', msg);
    } else if (cvAttached && filledCount === 0) {
      setPillState('success', '✓ 📄 CV joint avec succès !');
    } else if (filledCount > 0) {
      let msg = `✓ ${filledCount} champ(s) rempli(s)`;
      if (aiCount > 0) msg += ` + 🤖 ${aiCount} texte(s) IA`;
      setPillState('success', msg);
    } else {
      setPillState('idle', 'ℹ️ Formulaire déjà rempli');
    }

    return { filledCount, cvAttached, aiCount };
  }

  // -------------------------------------------------------------------------
  // Bouton flottant dans la page (Pill UI)
  // -------------------------------------------------------------------------
  let _pill = null;
  let _pillText = null;

  function createFloatingPill() {
    if (_pill || !document.body) return;

    _pill = document.createElement('div');
    _pill.id = 'applypilot-autofill-pill';
    _pill.style.cssText = `
      position: fixed;
      bottom: 24px;
      right: 24px;
      z-index: 2147483647;
      background: linear-gradient(135deg, #4338ca 0%, #312e81 100%);
      color: #ffffff;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
      font-size: 13px;
      font-weight: 600;
      padding: 9px 16px;
      border-radius: 24px;
      box-shadow: 0 4px 18px rgba(0, 0, 0, 0.35);
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 8px;
      border: 1px solid rgba(255, 255, 255, 0.25);
      transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
      user-select: none;
      backdrop-filter: blur(8px);
    `;

    _pill.innerHTML = `
      <span style="font-size: 15px;">📝</span>
      <span id="applypilot-pill-text">Remplir avec ApplyPilot</span>
      <span id="applypilot-pill-close" style="margin-left: 6px; opacity: 0.6; font-size: 16px; line-height: 1;" title="Masquer">×</span>
    `;

    _pillText = _pill.querySelector('#applypilot-pill-text');
    const closeBtn = _pill.querySelector('#applypilot-pill-close');

    _pill.addEventListener('mouseenter', () => {
      _pill.style.transform = 'translateY(-2px) scale(1.02)';
      _pill.style.boxShadow = '0 6px 22px rgba(67, 56, 202, 0.5)';
    });

    _pill.addEventListener('mouseleave', () => {
      _pill.style.transform = 'translateY(0) scale(1)';
      _pill.style.boxShadow = '0 4px 18px rgba(0, 0, 0, 0.35)';
    });

    _pill.addEventListener('click', (e) => {
      if (e.target === closeBtn) {
        e.stopPropagation();
        _pill.style.display = 'none';
        return;
      }
      runAutofill();
    });

    closeBtn.addEventListener('mouseenter', () => { closeBtn.style.opacity = '1'; });
    closeBtn.addEventListener('mouseleave', () => { closeBtn.style.opacity = '0.6'; });

    document.body.appendChild(_pill);
  }

  function setPillState(state, text) {
    if (!_pill || !_pillText) return;
    _pillText.innerText = text;
    if (state === 'loading') {
      _pill.style.background = 'linear-gradient(135deg, #1e1b4b 0%, #312e81 100%)';
      _pill.style.cursor = 'wait';
    } else if (state === 'success') {
      _pill.style.background = 'linear-gradient(135deg, #059669 0%, #064e3b 100%)';
      _pill.style.cursor = 'pointer';
      setTimeout(() => setPillState('idle', '📝 Remplir à nouveau'), 4500);
    } else if (state === 'error') {
      _pill.style.background = 'linear-gradient(135deg, #dc2626 0%, #7f1d1d 100%)';
      _pill.style.cursor = 'pointer';
      setTimeout(() => setPillState('idle', '📝 Remplir avec ApplyPilot'), 4500);
    } else {
      _pill.style.background = 'linear-gradient(135deg, #4338ca 0%, #312e81 100%)';
      _pill.style.cursor = 'pointer';
    }
  }

  // Écoute des messages venant du popup de l'extension
  if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.onMessage) {
    chrome.runtime.onMessage.addListener((req, sender, sendResponse) => {
      if (req && req.action === 'autofill') {
        runAutofill(req.data, req.cvBase64, req.cvName)
          .then(res => sendResponse(res || { status: 'done' }))
          .catch(err => sendResponse({ status: 'error', error: err ? err.message : 'Unknown' }));
        return true;
      }
    });
  }

  // Initialisation au chargement de la page
  function init() {
    if (isJobOrApplyPage()) {
      createFloatingPill();
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
