(() => {
    const form = document.getElementById('sas-form');
    const completed = document.getElementById('assessment-complete');
    if (completed) {
        try { window.sessionStorage.removeItem('sua.assessment.' + completed.dataset.draftKey); } catch (_) {}
    }
    if (!form) return;
    const radios = Array.from(form.querySelectorAll('input[type="radio"]'));
    const total = new Set(radios.map(input => input.name)).size;
    const progress = document.getElementById('assessment-progress');
    const text = document.getElementById('assessment-progress-text');
    const hint = document.getElementById('assessment-submit-hint');
    const consent = form.querySelector ? form.querySelector('input[name="consent"]') : null;
    const notice = document.getElementById('assessment-draft-notice');
    const discard = document.getElementById('assessment-discard');
    const key = form.dataset && form.dataset.draftKey ? 'sua.assessment.' + form.dataset.draftKey : null;
    const maxAge = 24 * 60 * 60 * 1000;
    let storageAvailable = false;
    if (key) {
        try {
            window.sessionStorage.setItem('sua.draft.check', '1');
            window.sessionStorage.removeItem('sua.draft.check');
            storageAvailable = true;
            const saved = JSON.parse(window.sessionStorage.getItem(key) || 'null');
            if (saved && saved.expires > Date.now()) {
                const answered = new Set(radios.filter(r => r.checked).map(r => r.name));
                radios.forEach(r => { if (!answered.has(r.name) && saved.answers[r.name] === r.value) r.checked = true; });
                if (notice) notice.textContent = 'Your draft answers were restored from this browser tab.';
            } else window.sessionStorage.removeItem(key);
        } catch (_) {
            if (notice) notice.textContent = 'Draft storage is unavailable. Keep this page open until you submit.';
        }
    }
    function saveDraft() {
        if (!key || !storageAvailable) return;
        const answers = Object.fromEntries(radios.filter(r => r.checked).map(r => [r.name, r.value]));
        try { window.sessionStorage.setItem(key, JSON.stringify({ answers, expires: Date.now() + maxAge })); }
        catch (_) { if (notice) notice.textContent = 'Your draft could not be saved. Keep this page open.'; }
    }
    function updateProgress() {
        const answered = new Set(radios.filter(input => input.checked).map(input => input.name)).size;
        progress.max = total || 1;
        progress.value = answered;
        text.textContent = `${answered} of ${total} skills assessed`;
        if (hint) hint.textContent = answered === total
            ? (consent && !consent.checked ? 'All questions answered. Confirm consent before saving.' : 'All questions answered. Your assessment is ready to save.')
            : `${total - answered} skill${total - answered === 1 ? '' : 's'} left to assess.`;
    }
    form.addEventListener('change', () => { updateProgress(); saveDraft(); });
    form.addEventListener('submit', saveDraft);
    window.addEventListener('pageshow', updateProgress);
    if (discard) discard.addEventListener('click', () => {
        radios.forEach(r => { r.checked = false; });
        try { if (key) window.sessionStorage.removeItem(key); } catch (_) {}
        if (notice) notice.textContent = 'Draft discarded. Answers have not been submitted.';
        updateProgress();
    });
    updateProgress();
})();
