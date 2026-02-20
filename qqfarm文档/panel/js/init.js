function updateUptimeDisplay() {
    if (lastSyncTimestamp > 0) {
        const elapsed = (Date.now() - lastSyncTimestamp) / 1000;
        const currentUptime = lastServerUptime + elapsed;
        const el = $('stat-uptime');
        if (el) el.textContent = fmtTime(currentUptime);
    }
}

function updateTime() {
    const now = new Date();
    const el = document.getElementById('sys-time');
    if (el) el.textContent = now.toLocaleTimeString();
}
setInterval(() => {
    updateTime();
    updateUptimeDisplay();
}, 1000);
updateTime();
lockHorizontalSwipeOnMobile();
applyFontScale();
window.addEventListener('resize', applyFontScale);
window.addEventListener('resize', syncOpsRowsMode);
updateTopbarAccount(null);
initTheme();

// 初始化
$('btn-refresh').addEventListener('click', () => { window.location.reload(); });

$('btn-theme').addEventListener('click', () => {
    const isLight = !document.body.classList.contains('light-theme');
    const mode = isLight ? 'light' : 'dark';
    applyTheme(mode);
    localStorage.setItem(THEME_STORAGE_KEY, mode);
    if (isLoggedIn) {
        api('/api/settings/theme', 'POST', { theme: mode });
    }
});

const loginBtn = $('btn-login');
if (loginBtn) loginBtn.addEventListener('click', doLogin);
const loginInput = $('login-password');
if (loginInput) {
    loginInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') doLogin();
    });
}

const logsFilterSel = $('logs-account-filter');
if (logsFilterSel) {
    logsFilterSel.value = logFilterAccountId;
    const onAccountFilterChange = () => {
        logFilterAccountId = logsFilterSel.value || 'all';
        localStorage.setItem('logFilterAccountId', logFilterAccountId);
        pollLogs();
    };
    logsFilterSel.addEventListener('change', onAccountFilterChange);
    logsFilterSel.addEventListener('input', onAccountFilterChange);
    logsFilterSel.addEventListener('blur', onAccountFilterChange);
}

const logsModuleSel = $('logs-module-filter');
if (logsModuleSel) {
    logsModuleSel.value = logFilters.module;
    const onModuleFilterChange = () => {
        logFilters.module = logsModuleSel.value || '';
        localStorage.setItem('logFilterModule', logFilters.module);
        pollLogs();
    };
    logsModuleSel.addEventListener('change', onModuleFilterChange);
    logsModuleSel.addEventListener('input', onModuleFilterChange);
    logsModuleSel.addEventListener('blur', onModuleFilterChange);
}

const logsWarnSel = $('logs-warn-filter');
if (logsWarnSel) {
    logsWarnSel.value = logFilters.isWarn;
    const onWarnFilterChange = () => {
        logFilters.isWarn = logsWarnSel.value || '';
        localStorage.setItem('logFilterIsWarn', logFilters.isWarn);
        pollLogs();
    };
    logsWarnSel.addEventListener('change', onWarnFilterChange);
    logsWarnSel.addEventListener('input', onWarnFilterChange);
    logsWarnSel.addEventListener('blur', onWarnFilterChange);
}

const logsEventFilter = $('logs-event-filter');
if (logsEventFilter) {
    logsEventFilter.value = logFilters.event;
    const onEventFilterChange = () => {
        logFilters.event = String(logsEventFilter.value || '').trim();
        localStorage.setItem('logFilterEvent', logFilters.event);
        pollLogs();
    };
    logsEventFilter.addEventListener('change', onEventFilterChange);
    logsEventFilter.addEventListener('input', onEventFilterChange);
    logsEventFilter.addEventListener('blur', onEventFilterChange);
}

const logsKeywordInput = $('logs-keyword-filter');
if (logsKeywordInput) {
    logsKeywordInput.value = logFilters.keyword;
    let keywordTimer = null;
    const onKeywordChange = () => {
        const next = logsKeywordInput.value.trim();
        if (!next) {
            if (keywordTimer) clearTimeout(keywordTimer);
            logFilters.keyword = '';
            localStorage.setItem('logFilterKeyword', logFilters.keyword);
            pollLogs();
            return;
        }
        if (keywordTimer) clearTimeout(keywordTimer);
        keywordTimer = setTimeout(() => {
            logFilters.keyword = next;
            localStorage.setItem('logFilterKeyword', logFilters.keyword);
            pollLogs();
        }, 250);
    };
    logsKeywordInput.addEventListener('input', onKeywordChange);
    logsKeywordInput.addEventListener('search', onKeywordChange);
    logsKeywordInput.addEventListener('change', onKeywordChange);
}

const logsTimeFromInput = $('logs-time-from-filter');
if (logsTimeFromInput) {
    logsTimeFromInput.value = logFilters.timeFrom;
    const onTimeFromChange = () => {
        logFilters.timeFrom = logsTimeFromInput.value || '';
        localStorage.setItem('logFilterTimeFrom', logFilters.timeFrom);
        pollLogs();
    };
    logsTimeFromInput.addEventListener('change', onTimeFromChange);
    logsTimeFromInput.addEventListener('input', onTimeFromChange);
}

const logsTimeToInput = $('logs-time-to-filter');
if (logsTimeToInput) {
    logsTimeToInput.value = logFilters.timeTo;
    const onTimeToChange = () => {
        logFilters.timeTo = logsTimeToInput.value || '';
        localStorage.setItem('logFilterTimeTo', logFilters.timeTo);
        pollLogs();
    };
    logsTimeToInput.addEventListener('change', onTimeToChange);
    logsTimeToInput.addEventListener('input', onTimeToChange);
}

initLogFiltersUI();

checkLogin();
