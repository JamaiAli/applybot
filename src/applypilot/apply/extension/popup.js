const API_BASE = 'http://127.0.0.1:8765';

document.addEventListener('DOMContentLoaded', async () => {
  const statusBadge = document.getElementById('status-badge');
  const candName = document.getElementById('cand-name');
  const candEmail = document.getElementById('cand-email');
  const candCv = document.getElementById('cand-cv');
  const btnAutofill = document.getElementById('btn-autofill');
  const btnDownloadCv = document.getElementById('btn-download-cv');

  let candidateData = null;
  let cvBase64 = null;
  let cvFileName = 'CV.pdf';

  // 1. Initialisation immédiate depuis le stockage local (zéro attente visuelle)
  if (typeof chrome !== 'undefined' && chrome.storage && chrome.storage.local) {
    try {
      const cached = await new Promise(res => chrome.storage.local.get(['candidateData', 'cvBase64', 'cvFileName'], res));
      if (cached && cached.candidateData) {
        candidateData = cached.candidateData;
        cvBase64 = cached.cvBase64 || null;
        cvFileName = cached.cvFileName || cvFileName;
        if (candidateData.personal) {
          candName.textContent = candidateData.personal.full_name || 'Candidat';
          candEmail.textContent = candidateData.personal.email || '';
        }
        candCv.textContent = `📄 ${cvFileName}`;
        statusBadge.textContent = '🟢 Prêt';
        statusBadge.className = 'badge connected';
      }
    } catch (_e) {}
  }

  // 2. Récupération de l'URL de l'onglet actif
  let activeTabUrl = '';
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tab && tab.url) {
      activeTabUrl = tab.url;
    }
  } catch (_e) {}

  const urlParam = activeTabUrl ? `?url=${encodeURIComponent(activeTabUrl)}` : '';

  // 3. Rafraîchissement avec le backend local ApplyPilot (127.0.0.1 ultra-rapide)
  try {
    const res = await fetch(`${API_BASE}/api/candidate${urlParam}`, { signal: AbortSignal.timeout(5000) });
    if (res.ok) {
      candidateData = await res.json();
      statusBadge.textContent = '🟢 Connecté';
      statusBadge.className = 'badge connected';

      if (candidateData.personal) {
        candName.textContent = candidateData.personal.full_name || 'Candidat';
        candEmail.textContent = candidateData.personal.email || '';
      }
      if (candidateData.cv_name) {
        cvFileName = candidateData.cv_name;
        candCv.textContent = `📄 ${candidateData.cv_name}`;
      }

      // Préchargement du CV en base64
      try {
        const cvRes = await fetch(`${API_BASE}/api/get-cv${urlParam}`, { signal: AbortSignal.timeout(6000) });
        if (cvRes.ok) {
          const blob = await cvRes.blob();
          const reader = new FileReader();
          reader.onloadend = () => {
            const dataUrl = reader.result;
            cvBase64 = typeof dataUrl === 'string' ? dataUrl.split(',')[1] : null;
            if (typeof chrome !== 'undefined' && chrome.storage && chrome.storage.local) {
              chrome.storage.local.set({ candidateData, cvBase64, cvFileName });
            }
          };
          reader.readAsDataURL(blob);
        }
      } catch (_e) {}

      if (typeof chrome !== 'undefined' && chrome.storage && chrome.storage.local) {
        chrome.storage.local.set({ candidateData, cvFileName });
      }
    } else {
      throw new Error('HTTP ' + res.status);
    }
  } catch (e) {
    if (!candidateData) {
      statusBadge.textContent = '🔴 Déconnecté';
      statusBadge.className = 'badge disconnected';
      candCv.textContent = '⚠️ Démarre le bot ApplyPilot (port 8765)';
    }
  }

  // 4. Déclencher le remplissage sur l'onglet actif avec injection automatique
  btnAutofill.addEventListener('click', async () => {
    btnAutofill.textContent = '⏳ Remplissage + CV...';
    try {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (!tab || !tab.id) {
        btnAutofill.textContent = '⚠️ Aucun onglet actif';
        return;
      }

      // S'assurer d'avoir les données du candidat
      if (!candidateData) {
        try {
          const fresh = await fetch(`${API_BASE}/api/candidate${urlParam}`, { signal: AbortSignal.timeout(4000) });
          if (fresh.ok) candidateData = await fresh.json();
        } catch (_e) {}
      }

      // Fonction d'envoi du message à l'onglet
      const sendMsg = () => new Promise((resolve) => {
        chrome.tabs.sendMessage(tab.id, {
          action: 'autofill',
          data: candidateData,
          cvBase64: cvBase64,
          cvName: cvFileName
        }, (response) => {
          if (chrome.runtime.lastError) {
            resolve({ error: chrome.runtime.lastError.message });
          } else {
            resolve(response || { ok: true });
          }
        });
      });

      let reply = await sendMsg();

      // Si le script n'était pas injecté (onglet ouvert avant l'extension ou F5 non fait) : injection dynamique !
      if (reply && reply.error && (reply.error.includes('Receiving end does not exist') || reply.error.includes('Could not establish connection'))) {
        console.log('[ApplyPilot] Script autofill non présent, injection dynamique via chrome.scripting...');
        try {
          await chrome.scripting.executeScript({
            target: { tabId: tab.id, allFrames: true },
            files: ['autofill.js']
          });
          await new Promise(r => setTimeout(r, 200));
          reply = await sendMsg();
        } catch (injectErr) {
          console.warn('[ApplyPilot] Échec injection dynamique:', injectErr);
        }
      }

      if (reply && reply.cvAttached) {
        btnAutofill.textContent = '✓ Rempli + 📄 CV joint !';
      } else if (reply && reply.filledCount > 0) {
        btnAutofill.textContent = `✓ ${reply.filledCount} champ(s) rempli(s) !`;
      } else if (reply && !reply.error) {
        btnAutofill.textContent = '✓ Formulaire complété !';
      } else {
        btnAutofill.textContent = '⚠️ Recharge la page (F5)';
      }

      setTimeout(() => {
        btnAutofill.innerHTML = '<span>📍 </span> Remplir le formulaire actuel';
      }, 3500);
    } catch (err) {
      console.error('[ApplyPilot] Erreur autofill:', err);
      btnAutofill.textContent = '⚠️ Erreur';
      setTimeout(() => {
        btnAutofill.innerHTML = '<span>📍 </span> Remplir le formulaire actuel';
      }, 2500);
    }
  });

  // 5. Télécharger le CV adapté à l'offre courante
  btnDownloadCv.addEventListener('click', () => {
    window.open(`${API_BASE}/api/cv${urlParam}`, '_blank');
  });
});
