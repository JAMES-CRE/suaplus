(function () {
    const sidebar = document.getElementById('app-sidebar');
    const toggle = document.getElementById('sidebar-toggle');
    const close = document.getElementById('sidebar-close');
    const backdrop = document.querySelector('.sidebar-backdrop');
    const workspace = document.getElementById('app-body');
    const skipLink = document.querySelector('.skip-link');
    if (!sidebar || !toggle || !close || !backdrop || !workspace) return;

    const desktop = window.matchMedia('(min-width: 1024px)');
    const preferenceKey = 'sua.sidebar.collapsed';
    let desktopCollapsed = false;
    let isOpen = false;
    try {
        desktopCollapsed = window.localStorage.getItem(preferenceKey) === 'true';
    } catch (_) {
        // The toggle also works when browser storage is unavailable.
    }
    document.body.classList.add('sidebar-enhanced');

    function applyState() {
        const visible = desktop.matches ? !desktopCollapsed : isOpen;
        document.body.classList.toggle('sidebar-open', isOpen);
        document.body.classList.toggle('sidebar-collapsed', desktopCollapsed);
        toggle.setAttribute('aria-expanded', String(visible));
        toggle.setAttribute('aria-label', visible ? 'Hide navigation' : 'Open navigation');
        sidebar.inert = !visible;
        workspace.inert = isOpen;
        if (skipLink) skipLink.inert = isOpen;
        backdrop.hidden = !isOpen;
        if (isOpen) {
            sidebar.setAttribute('role', 'dialog');
            sidebar.setAttribute('aria-modal', 'true');
        } else {
            sidebar.removeAttribute('role');
            sidebar.removeAttribute('aria-modal');
        }
    }

    function setDesktopCollapsed(collapsed) {
        desktopCollapsed = collapsed;
        try {
            window.localStorage.setItem(preferenceKey, String(collapsed));
        } catch (_) {
            // Keep the current page usable without saving a preference.
        }
        applyState();
    }

    function closeNavigation() {
        if (desktop.matches) {
            setDesktopCollapsed(true);
        } else {
            isOpen = false;
            applyState();
        }
        toggle.focus();
    }

    toggle.addEventListener('click', () => {
        if (desktop.matches) {
            setDesktopCollapsed(!desktopCollapsed);
        } else {
            isOpen = !isOpen;
            applyState();
            if (isOpen) {
                (sidebar.querySelector('a[aria-current="page"]') || sidebar.querySelector('nav a')).focus();
            }
        }
    });
    close.addEventListener('click', closeNavigation);
    backdrop.addEventListener('click', closeNavigation);
    document.addEventListener('keydown', event => {
        if (desktop.matches) {
            if (event.key === 'Escape' && !desktopCollapsed && sidebar.contains(document.activeElement)) {
                event.preventDefault();
                closeNavigation();
            }
            return;
        }
        if (!isOpen) return;
        if (event.key === 'Escape') {
            event.preventDefault();
            closeNavigation();
        } else if (event.key === 'Tab') {
            const links = [...sidebar.querySelectorAll('a[href], button:not([disabled])')]
                .filter(element => element.getClientRects().length);
            const first = links[0];
            const last = links[links.length - 1];
            if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first.focus();
            }
        }
    });
    desktop.addEventListener('change', () => {
        const restoreFocus = isOpen || sidebar.contains(document.activeElement);
        isOpen = false;
        applyState();
        if (sidebar.inert && restoreFocus) toggle.focus();
    });
    applyState();
})();
