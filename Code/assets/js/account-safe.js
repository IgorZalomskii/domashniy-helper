(() => {
  'use strict';
  const $ = (s, root = document) => root.querySelector(s);
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const state = { user: null, requests: [], notifications: [] };

  function notify(text, type = 'success') {
    const el = document.createElement('div'); el.className = `notification ${type}`; el.textContent = text;
    document.body.append(el); requestAnimationFrame(() => el.classList.add('show')); setTimeout(() => el.remove(), 3600);
  }
  async function api(url, options = {}) {
    const response = await fetch(url, { credentials: 'same-origin', headers: {'Content-Type':'application/json', 'X-Requested-With':'home-helper', ...(options.headers || {})}, ...options });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail || 'Не удалось выполнить запрос');
    return body;
  }
  const setVisible = (el, visible) => { if (el) el.style.display = visible ? 'block' : 'none'; };
  function gotoRole(user) {
    if (user.role === 'master') location.href = '/master';
    else if (user.role === 'admin') location.href = '/admin';
    else { const next = new URLSearchParams(location.search).get('next'); if (next && next.startsWith('/') && !next.startsWith('//')) location.href = next; }
  }
  function switchTab(name) {
    document.querySelectorAll('.auth-tab').forEach((tab) => tab.classList.toggle('active', tab.dataset.tab === name));
    document.querySelectorAll('.auth-form').forEach((form) => form.classList.toggle('active', form.id === `${name}Form`));
  }
  function setupAuth() {
    document.querySelectorAll('.auth-tab, .switch-tab').forEach((el) => el.addEventListener('click', (e) => { e.preventDefault(); switchTab(el.dataset.tab); }));
    $('#loginForm')?.addEventListener('submit', async (e) => {
      e.preventDefault(); const form = new FormData(e.currentTarget);
      try { state.user = await api('/api/auth/login', {method:'POST', body: JSON.stringify({email: form.get('email'), password: form.get('password')})}); notify('Вы вошли в аккаунт'); gotoRole(state.user); render(); }
      catch (error) { notify(error.message, 'error'); }
    });
    $('#registerForm')?.addEventListener('submit', async (e) => {
      e.preventDefault(); const form = new FormData(e.currentTarget); const password = String(form.get('password') || '');
      if (password !== form.get('confirmPassword')) return notify('Пароли не совпадают', 'error');
      if (password.length < 10) return notify('Пароль должен содержать минимум 10 символов', 'error');
      try {
        state.user = await api('/api/auth/register', {method:'POST', body: JSON.stringify({email:form.get('email'), password, name:[form.get('firstName'), form.get('lastName')].filter(Boolean).join(' '), role:form.get('role') || 'customer'})});
        notify('Аккаунт создан'); gotoRole(state.user); render();
      } catch (error) { notify(error.message, 'error'); }
    });
  }
  function renderUser() {
    const u = state.user; if (!u) return;
    const info = $('#userInfo'); if (info) info.innerHTML = `<h2>${esc(u.name)}</h2><p>${esc(u.email)}</p><p class="account-role">${u.role === 'customer' ? 'Заказчик' : u.role === 'master' ? 'Мастер' : 'Администратор'}</p>`;
    const avatar = $('#avatarContainer'); if (avatar) avatar.innerHTML = `<span class="avatar-initial">${esc((u.name || '?').trim().charAt(0).toUpperCase())}</span>`;
    const name = $('#profileName'), phone = $('#profilePhone'); if (name) name.value = u.name || ''; if (phone) phone.value = u.phone || '';
  }
  const labels = {pending:'Новая', assigned:'Назначен мастер', accepted:'Принята', in_progress:'В работе', awaiting_confirmation:'Ожидает подтверждения', completed:'Завершена', cancelled:'Отменена', rejected:'Отклонена'};
  function renderRequests() {
    const list = $('#ordersList'); if (!list) return;
    if (!state.requests.length) { list.innerHTML = '<div class="empty-state">Заявок пока нет.<br><a href="/application">Создать первую заявку</a></div>'; return; }
    list.innerHTML = state.requests.map((r) => `<article class="request-card"><div class="request-card__head"><strong>${esc(r.service_name || 'Заявка')}</strong><span class="status status-${esc(r.status)}">${esc(labels[r.status] || r.status)}</span></div><p>${esc(r.description)}</p><div class="request-meta">${esc(r.address)}${r.master_name ? ` · Мастер: ${esc(r.master_name)}` : ''}</div><div class="request-actions">${r.status === 'awaiting_confirmation' ? `<button class="balance-btn" data-request-action="confirm" data-request-id="${r.id}">Подтвердить</button>` : ''}${['pending','assigned','accepted'].includes(r.status) ? `<button class="balance-btn withdraw-btn" data-request-action="cancel" data-request-id="${r.id}">Отменить</button>` : ''}${['assigned','accepted','in_progress','awaiting_confirmation','completed'].includes(r.status) ? `<button class="balance-btn" data-chat-id="${r.id}">Чат</button>` : ''}${r.status === 'completed' && !r.has_review ? `<button class="balance-btn" data-review-id="${r.id}">Оценить</button>` : ''}</div><div class="request-extra"></div></article>`).join('');
    list.querySelectorAll('[data-request-action]').forEach((button) => button.addEventListener('click', () => requestAction(button.dataset.requestId, button.dataset.requestAction)));
    list.querySelectorAll('[data-chat-id]').forEach((b) => b.addEventListener('click', () => showChat(b.dataset.chatId, b.closest('.request-card').querySelector('.request-extra'))));
    list.querySelectorAll('[data-review-id]').forEach((b) => b.addEventListener('click', () => showReview(b.dataset.reviewId, b.closest('.request-card').querySelector('.request-extra'))));
  }
  async function showChat(id, target) { target.textContent = 'Загрузка сообщений…'; try { const messages = await api(`/api/service-requests/${id}/messages`); target.replaceChildren(...(messages.length ? messages.map(m => { const p=document.createElement('p'); p.textContent=`${m.name || 'Пользователь'}: ${m.body}`; return p; }) : [document.createTextNode('Сообщений пока нет.')])); const status=state.requests.find(r=>String(r.id)===String(id))?.status; if (!['completed','cancelled','rejected'].includes(status)) { const form=document.createElement('form'); form.innerHTML='<label>Сообщение <input name="body" required maxlength="3000"></label><button type="submit">Отправить</button>'; form.onsubmit=async e=>{e.preventDefault(); try{await api(`/api/service-requests/${id}/messages`,{method:'POST',body:JSON.stringify({body:form.elements.body.value})}); await showChat(id,target);}catch(err){notify(err.message,'error')}}; target.append(form); } } catch (e) { target.textContent = e.message; } }
  function showReview(id, target) { target.innerHTML = '<form><label>Оценка <select name="rating" required><option value="5">5</option><option value="4">4</option><option value="3">3</option><option value="2">2</option><option value="1">1</option></select></label><label>Комментарий <textarea name="body" required maxlength="2000"></textarea></label><button type="submit">Отправить отзыв</button></form>'; target.firstElementChild.addEventListener('submit', async e => { e.preventDefault(); const f=e.currentTarget; try { await api(`/api/service-requests/${id}/review`, {method:'POST', body:JSON.stringify({rating:Number(f.elements.rating.value), body:f.elements.body.value})}); notify('Отзыв отправлен на проверку'); await loadRequests(); } catch (err) { notify(err.message,'error'); } }); }
  async function requestAction(id, action) {
    let reason = ''; if (action === 'cancel') { reason = prompt('Причина отмены (не менее 5 символов):') || ''; if (reason.length < 5) return notify('Укажите причину отмены', 'error'); }
    try { await api(`/api/service-requests/${id}/action`, {method:'POST', body:JSON.stringify({action, reason})}); notify('Статус заявки обновлён'); await loadRequests(); } catch (error) { notify(error.message, 'error'); }
  }
  async function loadRequests() { state.requests = await api('/api/service-requests'); renderRequests(); }
  async function loadNotifications() { const items = await api('/api/notifications'); const list=$('#notificationsList'); if (!list) return; list.replaceChildren(...(items.length ? items.map(n => { const p=document.createElement('p'); p.textContent=`${n.is_read ? '' : '• '}${n.text || ''}`; return p; }) : [document.createTextNode('Уведомлений нет.')])); }
  async function loadFavorites() { const list=$('#favoritesList'); if (!list) return; const items=await api('/api/favorites'); list.replaceChildren(...(items.length ? items.map(m => { const row=document.createElement('p'); const name=document.createElement('span'); name.textContent=`${m.name || 'Мастер'}${m.title ? ` · ${m.title}` : ''}${m.district ? ` · ${m.district}` : ''}`; const remove=document.createElement('button'); remove.type='button'; remove.textContent='Убрать'; remove.addEventListener('click',async()=>{ try { await api(`/api/favorites/${m.user_id}`,{method:'DELETE'}); await loadFavorites(); } catch(e) { notify(e.message,'error'); } }); row.append(name,' ',remove); return row; }) : [document.createTextNode('В избранном пока никого нет.')])); }
  function setupCabinet() {
    $('#logoutBtn')?.addEventListener('click', async () => { await api('/api/auth/logout', {method:'POST'}); location.reload(); });
    document.querySelectorAll('[data-popup-target]').forEach((el) => { const open = () => { const p = $(`#${el.dataset.popupTarget}`); if (p) { p.__opener = el; p.classList.add('active'); p.querySelector('button')?.focus(); if(p.id==='favoritesPopup') loadFavorites().catch(e=>notify(e.message,'error')); if(p.id==='notificationsPopup') loadNotifications().catch(e=>notify(e.message,'error')); } }; el.addEventListener('click', open); el.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } }); });
    document.querySelectorAll('[data-close-popup]').forEach((el) => el.addEventListener('click', () => { const p = $(`#${el.dataset.closePopup}`); p?.classList.remove('active'); p?.__opener?.focus(); }));
    document.querySelectorAll('.popup-overlay').forEach((p) => p.addEventListener('click', (e) => { if (e.target === p) { p.classList.remove('active'); p.__opener?.focus(); } }));
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') document.querySelectorAll('.popup-overlay.active').forEach((p) => { p.classList.remove('active'); p.__opener?.focus(); }); });
    $('#profileForm')?.addEventListener('submit', async (e) => { e.preventDefault(); const form = new FormData(e.currentTarget); try { state.user = await api('/api/profile', {method:'PATCH', body:JSON.stringify({name:form.get('name'), phone:form.get('phone') || ''})}); renderUser(); notify('Профиль сохранён'); } catch (error) { notify(error.message, 'error'); } });
    $('#markNotificationsRead')?.addEventListener('click', async () => { try { await api('/api/notifications/read', {method:'POST'}); await loadNotifications(); } catch (e) { notify(e.message,'error'); } });
  }
  function render() { setVisible($('#authContainer'), !state.user); setVisible($('#cabinetContainer'), !!state.user); if (state.user) { renderUser(); loadRequests().catch((e) => notify(e.message, 'error')); loadNotifications().catch((e) => notify(e.message, 'error')); loadFavorites().catch((e) => notify(e.message, 'error')); } }
  async function init() { setupAuth(); setupCabinet(); try { state.user = await api('/api/auth/me'); } catch (_) { state.user = null; } if (state.user) { gotoRole(state.user); if (state.user.role !== 'customer') return; } render(); }
  init();
})();
