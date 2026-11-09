const API_BASE = 'http://localhost:8765';

document.addEventListener('DOMContentLoaded', async () => {
  const statusBadge = document.getElementById('status-badge');
  const candName = document.getElementById('cand-name');
  const candEmail = document.getElementById('cand-email');
  const candCv = document.getElementById('cand-cv');
  const btnAutofill = document.getElementById('btn-autofill');
  const btnDownloadCv = document.getElementById('btn-download-cv');

  let candidateData = null;
  let cvBase64 = null;
  let cvFileName = 'CV_Ali_Jamai.pdf';

  // Vérification de la connexion au backend local ApplyPilot
  try {
    const res = await fetch(`${API_BASE}/api/candidate`, { signal: AbortSignal.timeout(2000) });
    if (res.ok) {
      candidateData = await res.json();
      statusBadge.textContent = '🟢 Connecté';
      statusBadge.className = 'badge connected';

      if (candidateData.personal) {
        candName.textContent = candidateData.personal.full_name || 'Ali Jamai';
        candEmail.textContent = candidateData.personal.email || '';
      }
      if (candidateData.cv_name) {
        cvFileName = candidateData.cv_name;
        candCv.textContent = `📄 ${candidateData.cv_name}`;
      }

      // Préchargement immédiat du CV en base64 pour transmission instantanée sans blocage
      try {
        const cvRes = await fetch(`${API_BASE}/api/get-cv`, { signal: AbortSignal.timeout(4000) });
        if (cvRes.ok) {
          const blob = await cvRes.blob();
          const reader = new FileReader();
          reader.onloadend = () => {
            const dataUrl = reader.result;
            cvBase64 = typeof dataUrl === 'string' ? dataUrl.split(',')[1] : null;
            if (typeof chrome !== 'undefined' && chrome.storage && chrome.storage.local) {
              chrome.storage.local.set({ candidateData, cvBase64, cvFileName }, () => {
                if (chrome.runtime.lastError) {}
              });
            }
          };
          reader.readAsDataURL(blob);
        }
      } catch (_e) {}

      if (typeof chrome !== 'undefined' && chrome.storage && chrome.storage.local) {
        chrome.storage.local.set({ candidateData, cvFileName }, () => {
          if (chrome.runtime.lastError) {}
        });
      }
    } else {
      throw new Error();
    }
  } catch (e) {
    statusBadge.textContent = '🔴 Déconnecté';
    statusBadge.className = 'badge disconnected';
    candCv.textContent = '⚠️ Démarre le bot ApplyPilot (port 8765)';
  }

  // Déclencher le remplissage sur l'onglet actif avec les données préchargées
  btnAutofill.addEventListener('click', async () => {
    btnAutofill.textContent = '⏳ Remplissage + CV...';
    try {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (tab && tab.id) {
        chrome.tabs.sendMessage(tab.id, {
          action: 'autofill',
          data: candidateData,
          cvBase64: cvBase64,
          cvName: cvFileName
        }, (response) => {
          if (chrome.runtime.lastError) {
            console.log('[ApplyPilot] Onglet non compatible ou actualisation nécessaire:', chrome.runtime.lastError.message);
            btnAutofill.textContent = '⚠️ Recharge la page (F5)';
            setTimeout(() => {
              btnAutofill.innerHTML = '<span>📝</span> Remplir le formulaire actuel';
            }, 3000);
            return;
          }
          if (response && response.cvAttached) {
            btnAutofill.textContent = '✓ Rempli + 📄 CV joint !';
          } else {
            btnAutofill.textContent = '✓ Formulaire rempli !';
          }
          setTimeout(() => {
            btnAutofill.innerHTML = '<span>📝</span> Remplir le formulaire actuel';
          }, 3500);
        });
      }
    } catch (err) {
      btnAutofill.textContent = '⚠️ Erreur';
      setTimeout(() => {
        btnAutofill.innerHTML = '<span>📝</span> Remplir le formulaire actuel';
      }, 2500);
    }
  });

  // Télécharger le dernier CV adapté
  btnDownloadCv.addEventListener('click', () => {
    window.open(`${API_BASE}/api/cv`, '_blank');
  });
});
