let editingAccountId = null;

// QR 登录相关变量
let currentQRCode = '';
let currentLoginUrl = '';
let qrCheckInterval = null;

function isMobileByUA() {
    const ua = navigator.userAgent || '';
    return /Android|iPhone|iPad|iPod|Mobile/i.test(ua);
}

function syncQrOpenButtonVisibility() {
    const btn = $('btn-qr-open-url');
    if (!btn) return;
    btn.style.display = isMobileByUA() ? 'inline-flex' : 'none';
}

// ============ 扫码登录相关函数 ============
function switchTab(tabName) {
    // 仅处理添加账号弹窗内的标签页
    const root = document.getElementById('modal-add-acc');
    if (!root) return;

    // 隐藏所有标签页
    root.querySelectorAll('.tab-content').forEach(tab => {
        tab.style.display = 'none';
    });
    root.querySelectorAll('.tab-btn').forEach(btn => {
        btn.classList.remove('active');
    });

    // 显示选中的标签页
    const tab = $(`tab-${tabName}`);
    if (tab) tab.style.display = 'block';

    // 高亮选中的按钮
    const btn = root.querySelector(`.tab-btn[data-tab="${tabName}"]`);
    if (btn) btn.classList.add('active');

    // 切换底部按钮显示
    const footerManual = $('modal-footer-manual');
    const footerQR = $('modal-footer-qr');
    if (footerManual && footerQR) {
        if (tabName === 'qrcode') {
            footerManual.style.display = 'none';
            footerQR.style.display = 'flex';
        } else {
            footerManual.style.display = 'flex';
            footerQR.style.display = 'none';
        }
    }

    // 切换时仅处理轮询，不自动刷新二维码
    if (tabName !== 'qrcode') {
        stopQRCheck();
    }
}

async function generateQRCode() {
    const btn = $('btn-qr-generate');
    if (btn) btn.disabled = true;
    const status = $('qr-status');
    if (status) {
        status.textContent = '正在生成二维码...';
        status.style.color = 'var(--sub)';
    }

    try {
        const result = await fetch('/api/qr/create', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        }).then(r => r.json());

        if (result.ok && result.data) {
            currentQRCode = result.data.code;
            currentLoginUrl = result.data.url || result.data.loginUrl || '';
            const img = $('qr-code-img');
            const display = $('qr-code-display');
            
            if (img && display) {
                // 使用 qrcode 字段（QR 图片 URL）
                img.src = result.data.qrcode || result.data.url;
                img.style.display = 'block';
                display.style.display = 'grid';
            }
            startQRCheck();
        } else {
            alert('生成二维码失败: ' + (result.error || '未知错误'));
        }
    } catch (e) {
        alert('生成二维码出错: ' + e.message);
    } finally {
        if (btn) btn.disabled = false;
    }
}

async function openQRCodeLoginUrl() {
    let targetUrl = currentLoginUrl;
    if (!targetUrl) {
        const status = $('qr-status');
        if (status) {
            status.textContent = '正在获取扫码链接...';
            status.style.color = 'var(--sub)';
        }
        await generateQRCode();
        targetUrl = currentLoginUrl;
    }
    if (!targetUrl) {
        alert('未获取到扫码链接，请稍后重试');
        return;
    }

    const isMobile = isMobileByUA();
    if (!isMobile) {
        window.location.href = targetUrl;
        return;
    }

    // 手机端优先尝试通过 QQ deep link 唤起 QQ 打开目标链接
    const b64 = (typeof btoa === 'function')
        ? btoa(unescape(encodeURIComponent(targetUrl)))
        : '';
    const qqDeepLink = b64
        ? `mqqapi://forward/url?url_prefix=${encodeURIComponent(b64)}&version=1&src_type=web`
        : '';

    if (!qqDeepLink) {
        window.location.href = targetUrl;
        return;
    }

    let appSwitched = false;
    const onVisibility = () => {
        if (document.hidden) appSwitched = true;
    };
    document.addEventListener('visibilitychange', onVisibility);

    setTimeout(() => {
        document.removeEventListener('visibilitychange', onVisibility);
        if (!appSwitched) return;
    }, 1200);

    window.location.href = qqDeepLink;
}

function startQRCheck() {
    stopQRCheck();
    let checkCount = 0;
    qrCheckInterval = setInterval(async () => {
        checkCount++;
        if (checkCount > 120) {
            // 2分钟超时
            stopQRCheck();
            alert('二维码已过期，请重新生成');
            return;
        }

        try {
            const result = await fetch('/api/qr/check', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ code: currentQRCode })
            }).then(r => r.json());

            if (result.ok && result.data) {
                const status = $('qr-status');
                if (status) {
                    if (result.data.status === 'Wait') {
                        status.textContent = '等待扫码...';
                        status.style.color = 'var(--text-sub)';
                    } else if (result.data.status === 'OK') {
                        status.textContent = '✓ 登录成功，正在保存...';
                        status.style.color = 'var(--primary)';
                        stopQRCheck();
                        
                        // 自动填入 Code
                        const loginCode = result.data.code || '';
                        $('acc-code').value = loginCode;
                        
                        // 获取备注名，如果没输入就用默认名
                        let accName = $('acc-name-qr').value.trim();
                        if (!accName) {
                            // 默认备注名使用 QQ 号（uin）
                            accName = result.data.uin ? String(result.data.uin) : '扫码账号';
                        }
                        
                        // 直接保存账号
                        try {
                            const qq = result.data.uin ? String(result.data.uin) : '';
                            const payload = { 
                                name: accName, 
                                code: loginCode, 
                                platform: 'qq',
                                uin: qq,
                                qq,
                                avatar: result.data.avatar || (qq ? `https://q1.qlogo.cn/g?b=qq&nk=${qq}&s=640` : '')
                            };
                            
                            const saveResult = await fetch('/api/accounts', {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json', 'x-admin-token': adminToken },
                                body: JSON.stringify(payload)
                            }).then(r => r.json());
                            
                            if (saveResult.ok) {
                                status.textContent = '✓ 保存成功';
                                setTimeout(() => {
                                    modal.classList.remove('show');
                                    loadAccounts();
                                }, 1000);
                            } else {
                                status.textContent = '✗ 保存失败: ' + (saveResult.error || '未知错误');
                                status.style.color = '#F44336';
                            }
                        } catch (e) {
                            status.textContent = '✗ 保存出错: ' + e.message;
                            status.style.color = '#F44336';
                        }
                    } else if (result.data.status === 'Used') {
                        status.textContent = '二维码已失效';
                        status.style.color = '#F44336';
                        stopQRCheck();
                    }
                }
            }
        } catch (e) {
            console.error('QR Check Error:', e);
        }
    }, 1000);
}

function stopQRCheck() {
    if (qrCheckInterval) {
        clearInterval(qrCheckInterval);
        qrCheckInterval = null;
    }
}

$('btn-add-acc-modal').addEventListener('click', () => {
    editingAccountId = null;
    $('acc-name').value = '';
    $('acc-code').value = '';
    $('acc-name-qr').value = '';
    $('acc-platform').value = 'qq';
    currentQRCode = '';
    currentLoginUrl = '';
    syncQrOpenButtonVisibility();
    switchTab('qrcode');
    stopQRCheck();
    generateQRCode();
    modal.querySelector('h3').textContent = '添加账号';
    modal.classList.add('show');
});

// 标签页切换
document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        switchTab(btn.dataset.tab);
    });
});

const btnQrGenerate = $('btn-qr-generate');
if (btnQrGenerate) {
    btnQrGenerate.addEventListener('click', () => {
        generateQRCode();
    });
}

const btnQrOpenUrl = $('btn-qr-open-url');
if (btnQrOpenUrl) {
    btnQrOpenUrl.addEventListener('click', () => {
        openQRCodeLoginUrl();
    });
}

syncQrOpenButtonVisibility();

window.editAccount = (id) => {
    const acc = accounts.find(a => a.id === id);
    if (!acc) return;
    editingAccountId = id;
    $('acc-name').value = acc.name;
    $('acc-code').value = acc.code;
    $('acc-platform').value = acc.platform;
    currentQRCode = '';
    currentLoginUrl = '';
    switchTab('manual');
    stopQRCheck();
    modal.querySelector('h3').textContent = '编辑账号';
    modal.classList.add('show');
};

document.querySelectorAll('.close-modal').forEach(btn => {
    btn.addEventListener('click', () => {
        stopQRCheck();
        modal.classList.remove('show');
    });
});
$('btn-cancel-acc').addEventListener('click', () => {
    stopQRCheck();
    modal.classList.remove('show');
});

const btnCancelQR = $('btn-cancel-acc-qr');
if (btnCancelQR) {
    btnCancelQR.addEventListener('click', () => {
        stopQRCheck();
        modal.classList.remove('show');
    });
}

$('btn-save-acc').addEventListener('click', async () => {
    // 判断当前使用的标签页
    const isQRMode = document.getElementById('tab-qrcode').style.display !== 'none';
    
    let name, code, platform;
    
    if (isQRMode) {
        name = $('acc-name-qr').value.trim();
        code = $('acc-code').value.trim(); // 扫码结果会自动填入
        platform = 'qq'; // 扫码登录固定是 QQ
    } else {
        name = $('acc-name').value.trim();
        code = $('acc-code').value.trim();
        platform = $('acc-platform').value;

        // 仅用正则提取 code=xxx（兼容完整URL/片段）
        const match = code.match(/[?&]code=([^&]+)/i);
        if (match && match[1]) {
            code = decodeURIComponent(match[1]);
            $('acc-code').value = code;
        }
    }
    
    // 手动新增账号时，备注可留空，后端会自动使用“账号X”
    if (!name && editingAccountId) return alert('请输入名称');
    if (!code) return alert('请输入Code 或 先扫码');
    
    const payload = { name, code, platform };
    if (editingAccountId) payload.id = editingAccountId;
    
    await api('/api/accounts', 'POST', payload);
    stopQRCheck();
    modal.classList.remove('show');
    loadAccounts();
    pollAccountLogs();
});

window.deleteAccount = async (id) => {
    if (confirm('确定删除该账号?')) {
        const ret = await api('/api/accounts/' + id, 'DELETE');
        if (!ret) return;
        const sid = String(id);
        accounts = accounts.filter(a => String(a.id) !== sid);
        if (String(currentAccountId || '') === sid) {
            currentAccountId = null;
        }
        renderAccountSelector();
        renderAccountManager();
        renderLogFilterOptions();
        if (accounts.length > 0) {
            switchAccount(accounts[0].id);
        } else {
            $('current-account-name').textContent = '无账号';
            updateTopbarAccount({ name: '无账号' });
            resetDashboardStats();
        }
        await loadAccounts();
        await pollAccountLogs();
    }
};

