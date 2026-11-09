// ApplyPilot — Script d'auto-complétion intelligente 1-Clic
// Injecté dans les formulaires de candidature pour remplir sans blocage Cloudflare.

(function () {
  if (window.__applypilot_injected) return;
  window.__applypilot_injected = true;

  const API_BASE = 'http://localhost:8765';

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
    const hasInputs = document.querySelectorAll('input:not([type="hidden"]), textarea, select').length >= 3;
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
      try {
        el.dispatchEvent(new InputEvent('input', { bubbles: true, cancelable: true, inputType: 'insertText', data: val }));
      } catch (_e) {
        el.dispatchEvent(new Event('input', { bubbles: true, cancelable: true }));
      }
      el.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));
      el.dispatchEvent(new Event('blur', { bubbles: true, cancelable: true }));

      // Surlignage vert discret
      highlightField(el);
      return true;
    } catch (e) {
      console.warn('[ApplyPilot] Erreur écriture champ:', e);
      return false;
    }
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
    let text = el.getAttribute('aria-label') || el.getAttribute('placeholder') || '';
    if (el.id) {
      const labelEl = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (labelEl) text += ' ' + labelEl.innerText;
    }
    const parentLabel = el.closest('label');
    if (parentLabel) text += ' ' + parentLabel.innerText;

    // Précédent frère immédiat (label ou span descriptif)
    let prev = el.previousElementSibling;
    if (prev && /label|span|p|legend/i.test(prev.tagName)) {
      text += ' ' + prev.innerText;
    }

    // Cherche le texte dans le conteneur immédiat si délimité (ex. .form-group)
    let parent = el.parentElement;
    if (parent && parent.tagName !== 'BODY' && parent.tagName !== 'FORM' && parent.children.length <= 6) {
      const labelNode = parent.querySelector('label, .label, legend, span');
      if (labelNode && labelNode !== el && !labelNode.contains(el)) {
        text += ' ' + labelNode.innerText;
      }
    }

    if (el.name) text += ' ' + el.name;
    return text.toLowerCase().replace(/\s+/g, ' ').trim();
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

  async function apiSmartAnswer(question, jobTitle) {
    if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.sendMessage) {
      try {
        const resp = await new Promise((resolve) => {
          const timer = setTimeout(() => resolve(null), 3000);
          try {
            chrome.runtime.sendMessage({ action: 'smart_answer', question, job_title: jobTitle }, (res) => {
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
      body: JSON.stringify({ question, job_title: jobTitle }),
      signal: AbortSignal.timeout(6000)
    });
    return await ansResp.json();
  }

  // -------------------------------------------------------------------------
  // Remplissage principal
  // -------------------------------------------------------------------------
  async function runAutofill(preloadData = null, preloadCvBase64 = null, preloadCvName = null) {
    setPillState('loading', '⏳ Remplissage...');

    let data = preloadData;

    // Si pas de données en direct, chercher dans chrome.storage.local
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

    // Si toujours pas de données, interroger l'API locale ApplyPilot
    if (!data) {
      try {
        data = await apiGetCandidate(location.href);
      } catch (err) {
        console.error('[ApplyPilot] Impossible de joindre ApplyPilot local:', err);
        setPillState('error', '⚠️ Lance le bot ApplyPilot (port 8765)');
        return;
      }
    }

    const pers = data.personal || {};
    const qa = data.qa || {};
    let filledCount = 0;
    let cvAttached = false;

    // 1. Text Inputs
    const inputs = document.querySelectorAll('input:not([type="hidden"]):not([type="submit"]):not([type="button"]):not([type="checkbox"]):not([type="radio"]):not([type="file"])');
    for (const input of inputs) {
      if (input.value && input.value.trim().length > 0) continue; // Ne pas écraser
      const label = getFieldLabel(input);
      const type = (input.type || '').toLowerCase();

      // Prénom
      if (/first[\s_-]*name|pr[ée]nom|given[\s_-]*name/i.test(label)) {
        if (setNativeValue(input, pers.first_name)) filledCount++;
      }
      // Nom
      else if (/last[\s_-]*name|nom(?![\s_-]*(utilisateur|user|complet|de[\s_-]*jeune))|family[\s_-]*name|surname/i.test(label)) {
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
      // Ville
      else if (/city|ville|commune|localit[ée]/i.test(label)) {
        if (setNativeValue(input, pers.city)) filledCount++;
      }
      // Adresse
      else if (/address|adresse(?![\s_-]*mail)|rue|street/i.test(label)) {
        if (setNativeValue(input, pers.address)) filledCount++;
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
      // Questions de la Banque Q&A (Salaire, Date de début, Mobilité, Diplôme, etc.)
      else if (/pr[ée]tention|salaire|salary|compensation|remun[ée]ration|gratification/i.test(label) && qa.pretentions_salariales) {
        if (setNativeValue(input, qa.pretentions_salariales)) filledCount++;
      }
      else if (/date[\s_-]*d[ée]but|start[\s_-]*date|quand.*commencer/i.test(label) && qa.date_debut_stage) {
        if (setNativeValue(input, qa.date_debut_stage)) filledCount++;
      }
      else if (/disponibilit[ée]|availability|pr[ée]avis|dur[ée]e/i.test(label) && qa.disponibilite_stage) {
        if (setNativeValue(input, qa.disponibilite_stage)) filledCount++;
      }
      else if (/mobilit[ée]|d[ée]placement/i.test(label) && qa.mobilite_geographique) {
        if (setNativeValue(input, qa.mobilite_geographique)) filledCount++;
      }
      else if (/dipl[ôo]me[\s_-]*pr[ée]par[ée]|formation|cursus/i.test(label) && qa.diplome_prepare) {
        if (setNativeValue(input, qa.diplome_prepare)) filledCount++;
      }
      else if (/ann[ée]e[\s_-]*dipl[ôo]me|graduation[\s_-]*year/i.test(label) && qa.annee_diplome) {
        if (setNativeValue(input, qa.annee_diplome)) filledCount++;
      }
      else if (/niveau[\s_-]*(d['\s]*)?[ée]tudes?|education[\s_-]*level/i.test(label) && qa.niveau_etudes) {
        if (setNativeValue(input, qa.niveau_etudes)) filledCount++;
      }
      else if (/source|comment.*connu/i.test(label) && qa.source_offre) {
        if (setNativeValue(input, qa.source_offre)) filledCount++;
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

    const selects = document.querySelectorAll('select');
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

    // 3. Boutons Radio & Checkboxes (Questions standard : Permis, Mobilité, Autorisation)
    const radios = document.querySelectorAll('input[type="radio"], input[type="checkbox"]');
    for (const r of radios) {
      if (r.checked) continue;
      const label = getFieldLabel(r);
      const isYes = /oui|yes/i.test(label) || /oui|yes/i.test(r.value || '');
      const isNo = /non|no/i.test(label) || /non|no/i.test(r.value || '');

      // Permis de conduire -> Oui
      if (/permis/i.test(label) && isYes) {
        r.click();
        filledCount++;
      }
      // Véhicule personnel -> Non
      else if (/v[ée]hicule[\s_-]*personnel/i.test(label) && isNo) {
        r.click();
        filledCount++;
      }
      // Autorisation de travail en France -> Oui
      else if (/autoris(ation|[ée])|l[ée]galement|droit[\s_-]*de[\s_-]*travailler/i.test(label) && isYes) {
        r.click();
        filledCount++;
      }
    }

    // 4. Upload automatique du CV PDF (Support natif ATS : SAP SuccessFactors, Workday, Taleo, etc.)
    if (data.has_cv) {
      async function prepareCvFile() {
        const fileName = preloadCvName || data.cv_name || 'CV_Ali_Jamai.pdf';
        if (preloadCvBase64) {
          console.log('[ApplyPilot] Conversion directe du CV préchargé en mémoire');
          try {
            const binaryStr = atob(preloadCvBase64);
            const bytes = new Uint8Array(binaryStr.length);
            for (let i = 0; i < binaryStr.length; i++) bytes[i] = binaryStr.charCodeAt(i);
            return new File([bytes], fileName, { type: 'application/pdf', lastModified: Date.now() });
          } catch (_e) {}
        }
        return await apiGetCvFile(location.href, fileName);
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

              inputEl.dispatchEvent(new Event('input', { bubbles: true, cancelable: true }));
              inputEl.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));

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

            const fileName = preloadCvName || data.cv_name || 'CV_Ali_Jamai.pdf';

            let base64 = preloadCvBase64;

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
          let fileInputs = Array.from(document.querySelectorAll('input[type="file"]'));
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
            const dropzones = Array.from(document.querySelectorAll('.attachWrapper, .attachmentField, [class*="dropzone"], [class*="upload"], [id*="attach"]'));
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

    // 5. Textareas (Banque Q&A + Génération IA des questions ouvertes)
    const textareas = document.querySelectorAll('textarea');
    let aiCount = 0;
    for (const ta of textareas) {
      if (ta.value && ta.value.trim().length > 0) continue;
      const label = getFieldLabel(ta);

      // Correspondances Banque Q&A directe dans un textarea
      if (/pr[ée]tention|salaire|salary|compensation/i.test(label) && qa.pretentions_salariales) {
        if (setNativeValue(ta, qa.pretentions_salariales)) filledCount++;
      } else if (/disponibilit[ée]|availability|date[\s_-]*d[ée]but/i.test(label) && qa.disponibilite_stage) {
        if (setNativeValue(ta, qa.disponibilite_stage)) filledCount++;
      } else if (/mobilit[ée]|d[ée]placement/i.test(label) && qa.mobilite_geographique) {
        if (setNativeValue(ta, qa.mobilite_geographique)) filledCount++;
      } else if (/anglais|langue|english/i.test(label) && qa.langues) {
        if (setNativeValue(ta, qa.langues)) filledCount++;
      }
      // Questions ouvertes -> Génération IA personnalisée en direct
      else if (/pourquoi|motivation|message|remarque|int[ée]r[êe]t|atout|d[ée]crivez|exp[ée]rience|qualit[ée]|projet|comp[ée]tence|lettre/i.test(label) || textareas.length === 1) {
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
